-- '진행 중'인데 실제로는 아무도 하고 있지 않은 일(맡은 컴퓨터가 재시작·꺼짐·잠자기)을 빨리 찾아 다시 맡기거나 실패로 남긴다.
-- 판단: 맡은 컴퓨터가 그 일을 시작한 뒤 '쉬는 중'이라고 신호를 보냈거나(일을 놓침), 5분 넘게 신호가 없음(꺼짐·잠자기·인터넷 끊김),
--       또는 45분 넘게 끝나지 않음. 3번째면 실패로 남기고 원인·할 일을 적는다.
create or replace function public.nyx_release_orphans() returns int
language plpgsql security definer set search_path = public as $$
declare n int := 0; r record; v_why text;
begin
  for r in select t.id, t.attempts, t.started_at, d.name dname, s.state, s.last_seen_at
             from public.pipeline_tasks t left join public.agent_status s on s.device_id = t.device_id left join public.devices d on d.id = t.device_id
            where t.status = 'running' and (
                  (s.state in ('idle', 'paused', 'error') and s.last_seen_at > t.started_at + interval '30 seconds')
               or coalesce(s.last_seen_at, t.started_at) < now() - interval '5 minutes'
               or t.started_at < now() - interval '45 minutes')
            for update of t skip locked loop
    v_why := case when r.state in ('idle', 'paused', 'error') and r.last_seen_at > r.started_at + interval '30 seconds'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 작업 중에 재시작돼 이 일을 놓쳤습니다'
                  when coalesce(r.last_seen_at, r.started_at) < now() - interval '5 minutes'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 5분 넘게 응답이 없습니다(꺼짐·잠자기·인터넷 끊김)'
                  else '45분 넘게 끝나지 않았습니다' end;
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
revoke all on function public.nyx_release_orphans() from public, anon;
grant execute on function public.nyx_release_orphans() to authenticated;

create or replace function public.agent_claim_task(p_token text, p_kinds text[], p_platform text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid; v jsonb; v_role text; v_kinds text[] := p_kinds;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  select role into v_role from public.agent_status where device_id = v_dev;
  if (select paused from public.agent_status where device_id = v_dev) then return null; end if;
  if v_role = 'mobile' then
    v_kinds := array(select k from unnest(p_kinds) k where k not in ('generate','store','assemble','bgm'));
  elsif v_role <> 'server' then
    v_kinds := array(select k from unnest(p_kinds) k where k <> 'store');
  end if;
  perform public.nyx_release_orphans();
  select id into v_id from public.pipeline_tasks
   where status = 'queued' and kind = any(v_kinds) and not_before <= now()
     and (platform is null or platform = p_platform or (platform = 'server' and v_role = 'server'))
   order by case kind when 'store' then 0 when 'video_review' then 1 else 2 end, created_at
   for update skip locked limit 1;
  if v_id is null then return null; end if;
  update public.pipeline_tasks set status = 'running', device_id = v_dev, started_at = now(), attempts = attempts + 1
   where id = v_id returning to_jsonb(pipeline_tasks.*) into v;
  update public.agent_status set current_job = v->>'kind' where device_id = v_dev;
  return v;
end $$;
