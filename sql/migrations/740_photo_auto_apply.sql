-- 인물·협찬 사진 만들기도 목소리처럼 "추천만 받을까요 / 추천하고 자동 반영할까요?"
-- 자동 반영을 고르면: 코디네이터가 후보를 확인해 추천 사진을 고르고 → (인물) 얼굴 고정·기준 사진 → 바이블 적용까지 에이전트가 이어서 한다.
alter table public.photo_designs add column if not exists auto_apply boolean not null default false;

create or replace function public.agent_update_photo_design(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if p ? 'review' and (select count(*) from jsonb_object_keys(p)) = 1 then
    update public.photo_designs set review = p->'review' where id = p_id;
    return;
  end if;
  update public.photo_designs set
    status = coalesce(p->>'status', status), previews = coalesce(p->'previews', previews),
    soul_id = coalesce(p->>'soul_id', soul_id), ref_set = coalesce(p->'ref_set', ref_set), error = p->>'error',
    chosen = case when p ? 'chosen' then p->'chosen' else chosen end,
    review = case when p ? 'previews' or p ? 'ref_set' then null else review end
  where id = p_id;
end $$;

create or replace function public.agent_photo_redesign(p_token text, p_from uuid, p_prompt text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_id uuid; v_entry uuid; v_model text; v_auto boolean;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select entry_id, model, auto_apply into v_entry, v_model, v_auto from public.photo_designs where id = p_from;
  if v_entry is null then return null; end if;
  if exists (select 1 from public.photo_designs where id = p_from and auto_from is not null) then return null; end if;
  insert into public.photo_designs (entry_id, prompt, use_bible, model, auto_from, auto_apply)
  values (v_entry, left(p_prompt, 1800), false, v_model, p_from, coalesce(v_auto, false)) returning id into v_id;
  insert into public.pipeline_tasks (kind, payload) values ('photo_design', jsonb_build_object('design_id', v_id));
  return v_id;
end $$;

-- 자동 반영으로 맡긴 디자인만: 바이블 저장소에 그 항목 폴더로 사진을 올릴 수 있게(15분짜리 허가)
create or replace function public.agent_bible_photo_grant(p_token text, p_design uuid, p_path text) returns void
language plpgsql security definer set search_path = public as $$
declare v_entry uuid; v_series uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select d.entry_id, e.series_id into v_entry, v_series from public.photo_designs d join public.bible_entries e on e.id = d.entry_id
   where d.id = p_design and d.auto_apply;
  if v_entry is null then raise exception '자동 반영으로 맡긴 사진이 아닙니다'; end if;
  if p_path not like v_series::text || '/' || v_entry::text || '/%' or p_path like '%..%' then raise exception '허용되지 않은 경로'; end if;
  insert into public.storage_grants (bucket, path, mode, expires_at) values ('bible', p_path, 'write', now() + interval '15 minutes')
  on conflict (bucket, path, mode) do update set expires_at = excluded.expires_at;
end $$;
revoke all on function public.agent_bible_photo_grant(text, uuid, text) from public;
grant execute on function public.agent_bible_photo_grant(text, uuid, text) to anon, authenticated;

drop policy if exists "nyx grant write" on storage.objects;
create policy "nyx grant write" on storage.objects for insert to anon
  with check (bucket_id in ('takes', 'bible') and public.nyx_storage_granted(bucket_id, name, 'write'));

create or replace function public.agent_photo_auto_apply(p_token text, p_design uuid, p_paths text[], p_soul text) returns text
language plpgsql security definer set search_path = public as $$
declare d public.photo_designs; cur public.bible_versions; v_no int; v_new uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select * into d from public.photo_designs where id = p_design;
  if d.id is null or not d.auto_apply then return 'not_auto'; end if;
  if coalesce(array_length(p_paths, 1), 0) = 0 then return 'no_photos'; end if;
  select * into cur from public.bible_versions where entry_id = d.entry_id and status = 'active' order by version_no desc limit 1;
  select coalesce(max(version_no), 0) + 1 into v_no from public.bible_versions where entry_id = d.entry_id;
  insert into public.bible_versions (entry_id, version_no, description, details, image_paths, voice_path, voice_id, soul_id, prompt_en, change_reason, status, proposed_by)
  values (d.entry_id, v_no, coalesce(cur.description, d.prompt), cur.details, p_paths, cur.voice_path, cur.voice_id,
          coalesce(nullif(p_soul, ''), cur.soul_id), cur.prompt_en, '코디네이터 추천 사진 자동 반영(대표가 자동 반영을 고름)', 'proposed', 'AI')
  returning id into v_new;
  update public.bible_versions set status = 'retired' where entry_id = d.entry_id and status = 'active' and id <> v_new;
  update public.bible_versions set status = 'active', decided_at = now() where id = v_new;
  update public.photo_designs set status = 'linked' where id = d.id;
  return 'linked';
end $$;
revoke all on function public.agent_photo_auto_apply(text, uuid, text[], text) from public;
grant execute on function public.agent_photo_auto_apply(text, uuid, text[], text) to anon, authenticated;
