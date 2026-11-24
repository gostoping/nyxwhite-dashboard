-- ① '잠시 뒤 다시'(앞 컷·사진 기다림)는 시도 횟수에 세지 않는다.
--    기다릴 때마다 시도 횟수가 올라, 기다린 일이 그 뒤 한 번 인터넷이 끊기거나 컴퓨터가 재시작되면 바로 '실패'로 끝나던 문제.
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
  where id = p_id;
  if p_status in ('done','failed') or p_retry_in is not null then
    update public.agent_status set current_job = null where device_id = v_dev;
  end if;
end $$;
revoke all on function public.agent_task_update(text, uuid, text, text, text, text, int, boolean) from public;
grant execute on function public.agent_task_update(text, uuid, text, text, text, text, int, boolean) to anon, authenticated;

-- ② 가편집 자동 만들기: 두 영상 줄이 동시에 마지막 컷을 통과시키면 가편집·조립 일이 두 개 생기던 문제 → 화마다 잠금
create or replace function public.agent_rough_cut(p_token text, p_episode uuid, p_timeline jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_proj uuid; v_clips int; v_rev int; v_render uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  perform pg_advisory_xact_lock(hashtext('rough_cut:' || p_episode::text));   -- 마지막 두 컷이 동시에 통과해도 가편집은 하나만
  select id, jsonb_array_length(coalesce(timeline->'clips', '[]')), rev into v_proj, v_clips, v_rev from public.edit_projects where episode_id = p_episode;
  if v_proj is not null and v_clips > 0 then return null; end if;
  if v_proj is null then
    insert into public.edit_projects (episode_id, timeline) values (p_episode, p_timeline) returning id, rev into v_proj, v_rev;
  else
    update public.edit_projects set timeline = p_timeline, rev = rev + 1, updated_at = now() where id = v_proj returning rev into v_rev;
  end if;
  insert into public.edit_renders (project_id, episode_id, kind, rev, timeline) values (v_proj, p_episode, 'preview', v_rev, p_timeline) returning id into v_render;
  insert into public.pipeline_tasks (kind, episode_id, payload) values ('assemble', p_episode, jsonb_build_object('render_id', v_render, 'auto', true));
  return v_render;
end $$;
revoke all on function public.agent_rough_cut(text, uuid, jsonb) from public;
grant execute on function public.agent_rough_cut(text, uuid, jsonb) to anon, authenticated;
