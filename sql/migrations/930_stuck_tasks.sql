-- 대표 요청(2026-11-27): 일이 '하는 중'·'기다리는 중'에서 멈춘 것처럼 보이는 일이 자꾸 생기지 않게.
-- 1) 진행 표시가 마지막으로 바뀐 시각(progress_at)을 남긴다.
-- 2) 짧은 일(영어 프롬프트·사진 확인 등)이 10분 넘게 진행이 없으면, 맡은 컴퓨터가 살아 있어도 걸린 것으로 보고 다른 컴퓨터가 다시 맡게 한다(3번까지).
-- 3) 다시 맡긴 뒤 예전 컴퓨터가 뒤늦게 보내는 진행·결과는 덮어쓰지 않는다.
alter table public.pipeline_tasks add column if not exists progress_at timestamptz;

create or replace function public.nyx_task_progress_at() returns trigger
language plpgsql as $$
begin
  if new.progress is distinct from old.progress or (new.status = 'running' and old.status is distinct from 'running') then
    new.progress_at := now();
  end if;
  return new;
end $$;
drop trigger if exists nyx_task_progress_at on public.pipeline_tasks;
create trigger nyx_task_progress_at before update on public.pipeline_tasks
  for each row execute function public.nyx_task_progress_at();

create or replace function public.nyx_release_orphans() returns int
language plpgsql security definer set search_path = public as $$
declare n int := 0; r record; v_why text;
  v_short text[] := array['bible_prompt', 'coord_review', 'photo_describe', 'bible_place', 'bible_suggest', 'api_check', 'balance_check', 'edit_command', 'prep_cut'];
begin
  for r in select t.id, t.kind, t.attempts, t.started_at, t.progress_at, d.name dname, s.state, s.last_seen_at
             from public.pipeline_tasks t left join public.agent_status s on s.device_id = t.device_id left join public.devices d on d.id = t.device_id
            where t.status = 'running' and (
                  (s.state in ('idle', 'paused', 'error') and s.last_seen_at > t.started_at + interval '30 seconds')
               or coalesce(s.last_seen_at, t.started_at) < now() - interval '5 minutes'
               -- 짧은 일(보통 1분 안팎)이 진행 표시 없이 오래 멈춰 있으면: 컴퓨터는 살아 있어도 그 일이 걸린 것 → 다른 컴퓨터가 다시 맡게
               or (t.kind = any(v_short) and coalesce(t.progress_at, t.started_at) < now() - interval '10 minutes')
               or t.started_at < now() - case when t.kind in ('photo_set', 'upscale') then interval '120 minutes' when t.kind = 'generate' then interval '90 minutes' else interval '45 minutes' end)
            for update of t skip locked loop
    v_why := case when r.state in ('idle', 'paused', 'error') and r.last_seen_at > r.started_at + interval '30 seconds'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 작업 중에 재시작돼 이 일을 놓쳤습니다'
                  when coalesce(r.last_seen_at, r.started_at) < now() - interval '5 minutes'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 5분 넘게 응답이 없습니다(꺼짐·잠자기·인터넷 끊김)'
                  when r.kind = any(v_short) and coalesce(r.progress_at, r.started_at) < now() - interval '10 minutes'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')에서 10분 넘게 진행이 없어 멈춘 것으로 보입니다'
                  else (case when r.kind in ('photo_set', 'upscale') then '120분' when r.kind = 'generate' then '90분' else '45분' end) || ' 넘게 끝나지 않았습니다' end;
    if r.attempts < 3 then
      update public.pipeline_tasks set status = 'queued', device_id = null, progress = v_why || ' → 다시 맡김' where id = r.id;
    else
      update public.pipeline_tasks set status = 'failed', finished_at = now(),
             error = v_why || ' (3번 시도). 이렇게 하세요: 에이전트 탭에서 그 컴퓨터가 켜져 있고 "쉬는 중/일하는 중"인지 확인한 뒤 다시 시도. 같은 일에서 계속 멈추면 원인 문구를 개발에 전달'
       where id = r.id;
    end if;
    n := n + 1;
  end loop;
  return n;
end $$;

drop function if exists public.agent_task_update(text, uuid, text, text, text, text, int);
create or replace function public.agent_task_update(p_token text, p_id uuid, p_status text, p_progress text default null,
                                                    p_result text default null, p_error text default null, p_retry_in int default null,
                                                    p_wait boolean default false)
returns void language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token);
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  update public.pipeline_tasks set
    status = case when p_retry_in is not null then 'queued' else coalesce(p_status, status) end,
    progress = coalesce(p_progress, progress), result = coalesce(left(p_result, 4000), result), error = left(p_error, 2000),
    not_before = case when p_retry_in is not null then now() + make_interval(secs => p_retry_in) else not_before end,
    device_id = case when p_retry_in is not null then null else device_id end,
    attempts = case when p_wait and p_retry_in is not null then greatest(attempts - 1, 0) else attempts end,
    finished_at = case when p_status in ('done','failed') and p_retry_in is null then now() else finished_at end
  where id = p_id
    -- 멈춘 것으로 보고 다른 컴퓨터에 다시 맡긴 일: 예전 컴퓨터가 뒤늦게 진행을 보내도 덮어쓰지 않는다(끝냈다는 보고는 아직 아무도 안 맡았을 때만)
    and (device_id = v_dev or (device_id is null and p_status in ('done', 'failed') and p_retry_in is null));
  if p_status in ('done','failed') or p_retry_in is not null then
    update public.agent_status set current_job = null where device_id = v_dev;
  end if;
end $$;

revoke all on function public.agent_task_update(text, uuid, text, text, text, text, int, boolean) from public;
grant execute on function public.agent_task_update(text, uuid, text, text, text, text, int, boolean) to anon, authenticated;
