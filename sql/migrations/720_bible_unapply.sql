-- 바이블 적용 해제(대표): 목소리 또는 사진(기준 사진·얼굴 고정)을 뺀 새 버전을 적용한다. 이전 버전은 이력에 남아 되돌릴 수 있다.
-- 목소리는 동일인 전체에서 함께 뺀다(한 사람 = 목소리 하나).
create or replace function public.nyx_same_person_voice() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_root uuid;
begin
  if new.status <> 'active' or pg_trigger_depth() > 3 or current_setting('nyx.skip_voice_sync', true) = '1' then return null; end if;
  select coalesce(same_as, id) into v_root from public.bible_entries where id = new.entry_id;
  if new.voice_id is null then
    update public.bible_versions set voice_id = (
      select v.voice_id from public.bible_versions v join public.bible_entries e on e.id = v.entry_id
      where v.status = 'active' and v.voice_id is not null and v.entry_id <> new.entry_id and (e.id = v_root or e.same_as = v_root)
      order by (e.id = v_root) desc limit 1)
    where id = new.id and exists (select 1 from public.bible_versions v join public.bible_entries e on e.id = v.entry_id
      where v.status = 'active' and v.voice_id is not null and v.entry_id <> new.entry_id and (e.id = v_root or e.same_as = v_root));
    return null;
  end if;
  update public.bible_versions v set voice_id = new.voice_id
  where v.status = 'active' and v.voice_id is distinct from new.voice_id and v.entry_id <> new.entry_id
    and v.entry_id in (select id from public.bible_entries where id = v_root or same_as = v_root);
  return null;
end $$;

create or replace function public.owner_bible_unapply(p_entry uuid, p_what text) returns int
language plpgsql security definer set search_path = public as $$
declare v_root uuid; e record; cur public.bible_versions; v_no int; v_new uuid; n int := 0;
begin
  if not public.nyx_is_owner() then raise exception 'row-level security: 바이블 적용 해제는 대표만 할 수 있습니다'; end if;
  if p_what not in ('voice', 'photo') then raise exception '해제할 것은 voice 또는 photo'; end if;
  perform set_config('nyx.skip_voice_sync', '1', true);
  select coalesce(same_as, id) into v_root from public.bible_entries where id = p_entry;
  for e in select id from public.bible_entries
           where (p_what = 'voice' and (id = v_root or same_as = v_root)) or (p_what = 'photo' and id = p_entry) loop
    select * into cur from public.bible_versions where entry_id = e.id and status = 'active' order by version_no desc limit 1;
    if cur.id is null then continue; end if;
    if p_what = 'voice' and cur.voice_id is null then continue; end if;
    if p_what = 'photo' and coalesce(array_length(cur.image_paths, 1), 0) = 0 and cur.soul_id is null then continue; end if;
    select coalesce(max(version_no), 0) + 1 into v_no from public.bible_versions where entry_id = e.id;
    insert into public.bible_versions (entry_id, version_no, description, details, image_paths, voice_path, voice_id, soul_id, prompt_en, change_reason, status, proposed_by)
    values (e.id, v_no, cur.description, cur.details,
            case when p_what = 'photo' then '{}'::text[] else cur.image_paths end, cur.voice_path,
            case when p_what = 'voice' then null else cur.voice_id end,
            case when p_what = 'photo' then null else cur.soul_id end, cur.prompt_en,
            case when p_what = 'voice' then '대표: 목소리 적용 해제' else '대표: 사진·얼굴 고정 적용 해제' end, 'proposed', 'owner')
    returning id into v_new;
    update public.bible_versions set status = 'retired' where entry_id = e.id and status = 'active' and id <> v_new;
    update public.bible_versions set status = 'active', decided_at = now() where id = v_new;
    n := n + 1;
  end loop;
  if p_what = 'voice' then   -- 적용됐던 목소리 디자인은 '저장됨'으로 돌려 둔다(다시 '바이블에 적용' 가능, 자동 추천이 다시 덮지 않음)
    update public.voice_designs set status = 'saved'
    where status = 'linked' and entry_id in (select id from public.bible_entries where id = v_root or same_as = v_root);
  end if;
  -- 목소리·사진만 뺀 버전이다: 코디네이터 재감수·프롬프트 재작성은 돌리지 않는다
  delete from public.pipeline_tasks where status = 'queued' and kind in ('coord_review', 'bible_prompt') and created_at > now() - interval '5 seconds'
    and (payload->>'entry_id')::uuid in (select id from public.bible_entries where id = v_root or same_as = v_root or id = p_entry) and p_what = 'voice';
  return n;
end $$;
revoke all on function public.owner_bible_unapply(uuid, text) from public, anon;
grant execute on function public.owner_bible_unapply(uuid, text) to authenticated;

-- 대표가 목소리를 뺐으면 자동 추천이 곧바로 다시 넣지 않게
create or replace function public.nyx_voice_queue() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_series uuid; v_cat text; v_same uuid;
begin
  if new.status <> 'active' or new.voice_id is not null or current_setting('nyx.skip_voice_sync', true) = '1' then return new; end if;
  select series_id, category, same_as into v_series, v_cat, v_same from public.bible_entries where id = new.entry_id;
  if v_cat <> 'character' or v_same is not null then return new; end if;
  if exists (select 1 from public.voice_designs d where d.entry_id = new.entry_id and d.status in ('queued','ready','saving','saved','linked')) then return new; end if;
  if exists (select 1 from public.pipeline_tasks t where t.kind = 'voice_cast' and t.status in ('queued','running')
             and coalesce(t.series_id, (t.payload->>'series_id')::uuid) = v_series) then return new; end if;
  insert into public.pipeline_tasks (kind, series_id, payload) values ('voice_cast', v_series, jsonb_build_object('series_id', v_series, 'auto', true));
  return new;
end $$;
