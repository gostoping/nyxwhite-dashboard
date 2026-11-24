-- 영상 생성(generate)은 Higgsfield 대기 최대 40분 + 받기·대사 교체·감수까지 한 작업이라 45분을 넘길 수 있다.
-- 그런데 45분에 '멈춘 일'로 보고 다시 맡기면, 아직 돌고 있는 생성이 하나 더 시작돼 같은 컷을 두 번 결제했다.
-- 영상 생성만 90분까지 기다린다(그 사이 컴퓨터가 꺼지면 5분 무응답 규칙으로 바로 다시 맡김 — 에이전트 1.21부터는 이미 맡긴 요청을 이어서 기다림).
create or replace function public.nyx_release_orphans() returns int
language plpgsql security definer set search_path = public as $$
declare n int := 0; r record; v_why text;
begin
  for r in select t.id, t.kind, t.attempts, t.started_at, d.name dname, s.state, s.last_seen_at
             from public.pipeline_tasks t left join public.agent_status s on s.device_id = t.device_id left join public.devices d on d.id = t.device_id
            where t.status = 'running' and (
                  (s.state in ('idle', 'paused', 'error') and s.last_seen_at > t.started_at + interval '30 seconds')
               or coalesce(s.last_seen_at, t.started_at) < now() - interval '5 minutes'
               or t.started_at < now() - case when t.kind in ('photo_set', 'upscale') then interval '120 minutes' when t.kind = 'generate' then interval '90 minutes' else interval '45 minutes' end)
            for update of t skip locked loop
    v_why := case when r.state in ('idle', 'paused', 'error') and r.last_seen_at > r.started_at + interval '30 seconds'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 작업 중에 재시작돼 이 일을 놓쳤습니다'
                  when coalesce(r.last_seen_at, r.started_at) < now() - interval '5 minutes'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 5분 넘게 응답이 없습니다(꺼짐·잠자기·인터넷 끊김)'
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
revoke all on function public.nyx_release_orphans() from public, anon;
grant execute on function public.nyx_release_orphans() to authenticated;
