-- 바이블 '바꾸는 이유'에 요청만 적고 묘사는 그대로 둔 대표 수정본: 적용되는 순간 DB가 바로 작가실에 반영을 맡긴다
-- (브라우저가 옛 화면이어도 빠지지 않게). 짧은 바이블 일은 긴 일(컷 설계·영상)보다 먼저 집는다.
create or replace function public.nyx_bible_reason_queue() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'active' and (TG_OP = 'INSERT' or old.status is distinct from 'active')
     and new.proposed_by = '대표' and coalesce(trim(new.change_reason), '') <> ''
     and new.change_reason !~ '(자동|사진을 보고|작가실|가져옴|목소리|얼굴 고정|Soul)'
     and exists (select 1 from public.bible_versions pv where pv.entry_id = new.entry_id and pv.id <> new.id and pv.version_no < new.version_no
                  and pv.description is not distinct from new.description and pv.details is not distinct from new.details)
     and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running')
                      and t.payload->>'version_id' = new.id::text and t.payload ? 'apply_reason') then
    insert into public.pipeline_tasks (kind, series_id, payload)
    select 'bible_prompt', be.series_id, jsonb_build_object('version_id', new.id, 'entry_id', be.id, 'apply_reason', true)
      from public.bible_entries be where be.id = new.entry_id
       and be.category in ('character', 'location', 'background', 'prop', 'style', 'music');
  end if;
  return null;
end $$;
drop trigger if exists nyx_bible_reason_queue on public.bible_versions;
create trigger nyx_bible_reason_queue after insert or update on public.bible_versions for each row execute function public.nyx_bible_reason_queue();

-- 그사이(옛 화면으로) 저장돼 빠진 것도 한 번 맡긴다
insert into public.pipeline_tasks (kind, series_id, payload)
select 'bible_prompt', be.series_id, jsonb_build_object('version_id', bv.id, 'entry_id', be.id, 'apply_reason', true)
  from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id
 where bv.status = 'active' and bv.proposed_by = '대표' and coalesce(trim(bv.change_reason), '') <> ''
   and bv.change_reason !~ '(자동|사진을 보고|작가실|가져옴|목소리|얼굴 고정|Soul)'
   and bv.created_at > now() - interval '30 days'
   and be.category in ('character', 'location', 'background', 'prop', 'style', 'music')
   and exists (select 1 from public.bible_versions pv where pv.entry_id = bv.entry_id and pv.version_no < bv.version_no
                and pv.description is not distinct from bv.description and pv.details is not distinct from bv.details)
   and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running', 'done')
                    and t.payload->>'version_id' = bv.id::text and t.payload ? 'apply_reason');
-- 450에서 15분 뒤로 미뤄 둔 것은 바로 시작
update public.pipeline_tasks set not_before = now() where kind = 'bible_prompt' and status = 'queued' and payload ? 'apply_reason' and not_before > now();

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
   order by case when kind = 'store' then 0 when kind = 'video_review' then 1 when kind in ('bible_prompt', 'photo_describe', 'api_check', 'bible_suggest') then 2 else 3 end, created_at
   for update skip locked limit 1;
  if v_id is null then return null; end if;
  update public.pipeline_tasks set status = 'running', device_id = v_dev, started_at = now(), attempts = attempts + 1
   where id = v_id returning to_jsonb(pipeline_tasks.*) into v;
  update public.agent_status set current_job = v->>'kind' where device_id = v_dev;
  return v;
end $$;
