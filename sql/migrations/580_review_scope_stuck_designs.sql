-- 1) 코디네이터 감수는 인물·협찬만(대표 결정: 시간이 오래 걸려서)
create or replace function public.nyx_coord_queue() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_t uuid;
begin
  if new.status = 'active' and (TG_OP = 'INSERT' or old.status is distinct from 'active') then
    select t.id into v_t from public.pipeline_tasks t where t.kind = 'coord_review' and t.status = 'queued'
       and t.payload->>'entry_id' = new.entry_id::text and not (t.payload ? 'design_id') limit 1;
    if v_t is not null then
      update public.pipeline_tasks set payload = payload || jsonb_build_object('version_id', new.id) where id = v_t;
    else
      insert into public.pipeline_tasks (kind, series_id, payload, not_before)
      select 'coord_review', be.series_id, jsonb_build_object('version_id', new.id, 'entry_id', be.id), now() + interval '20 seconds'
        from public.bible_entries be where be.id = new.entry_id
         and be.category in ('character', 'location', 'background', 'prop', 'style', 'music')
         and (be.category = 'character' or coalesce(be.origin, '') = 'sponsor');   -- 감수는 인물·협찬만(대표 결정)
    end if;
  end if;
  return null;
end $$;

-- 2) 사진 칸이 '만드는 중'으로 영원히 도는 것 방지: 사진 작업이 실패·취소되면 사진 디자인도 실패로 바꿔 원인을 보여 준다
create or replace function public.nyx_photo_task_sync() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.kind in ('photo_design', 'photo_set') and new.status in ('failed', 'cancelled') and old.status is distinct from new.status then
    update public.photo_designs set status = 'failed',
           error = coalesce(nullif(new.error, ''), case when new.status = 'cancelled' then '작업이 취소됐습니다(중복 정리·보류 등)' else '작업 실패' end)
     where id::text = new.payload->>'design_id' and status in ('queued', 'training');
  end if;
  return null;
end $$;
drop trigger if exists nyx_photo_task_sync on public.pipeline_tasks;
create trigger nyx_photo_task_sync after update on public.pipeline_tasks for each row execute function public.nyx_photo_task_sync();

-- 지금 이미 멈춰 있는 사진 디자인(맡은 작업이 없거나 끝났는데 '만드는 중'): 실패로 바꾸고 이유를 남긴다 → 카드에서 "새로 만들기"
update public.photo_designs d set status = 'failed',
       error = '맡은 작업이 끊겨 멈춰 있었습니다(에이전트 재시작·옛 버전·중복 정리). "새로 만들기"를 눌러 주세요'
 where d.status in ('queued', 'training')
   and not exists (select 1 from public.pipeline_tasks t where t.kind in ('photo_design', 'photo_set') and t.status in ('queued', 'running')
                    and t.payload->>'design_id' = d.id::text);

-- 3) 얼굴 학습(photo_set)·업스케일은 원래 오래 걸리므로 45분이 아니라 120분이 지나야 '멈춤'으로 본다(정상 작업을 중복으로 다시 돌리던 문제)
create or replace function public.nyx_release_orphans() returns int
language plpgsql security definer set search_path = public as $$
declare n int := 0; r record; v_why text;
begin
  for r in select t.id, t.kind, t.attempts, t.started_at, d.name dname, s.state, s.last_seen_at
             from public.pipeline_tasks t left join public.agent_status s on s.device_id = t.device_id left join public.devices d on d.id = t.device_id
            where t.status = 'running' and (
                  (s.state in ('idle', 'paused', 'error') and s.last_seen_at > t.started_at + interval '30 seconds')
               or coalesce(s.last_seen_at, t.started_at) < now() - interval '5 minutes'
               or t.started_at < now() - case when t.kind in ('photo_set', 'upscale') then interval '120 minutes' else interval '45 minutes' end)
            for update of t skip locked loop
    v_why := case when r.state in ('idle', 'paused', 'error') and r.last_seen_at > r.started_at + interval '30 seconds'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 작업 중에 재시작돼 이 일을 놓쳤습니다'
                  when coalesce(r.last_seen_at, r.started_at) < now() - interval '5 minutes'
                    then '맡은 컴퓨터(' || coalesce(r.dname, '?') || ')가 5분 넘게 응답이 없습니다(꺼짐·잠자기·인터넷 끊김)'
                  else (case when r.kind in ('photo_set', 'upscale') then '120분' else '45분' end) || ' 넘게 끝나지 않았습니다' end;
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
