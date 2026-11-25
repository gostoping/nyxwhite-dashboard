-- 제작이 최우선, 대표 지시가 최우선: 제작 중 대표가 '보완 요청'으로 인물·장소·소품을 바꾸라고 하면 그 컷에 반영하고 바이블에도 저장한다.
-- 바이블 변경은 대표 본인의 지시이므로 바로 적용한다(고쳐쓰기 '바꾸는 이유'와 같은 길: 코디네이터가 묘사·영어 프롬프트에 반영).
create or replace function public.agent_bible_owner_change(p_token text, p_episode uuid, p_name text, p_reason text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_series uuid; v_entry uuid; cur public.bible_versions; v_no int; v_new uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select series_id into v_series from public.episodes where id = p_episode;
  select id into v_entry from public.bible_entries where series_id = v_series and name = p_name limit 1;
  if v_entry is null then
    select id into v_entry from public.bible_entries
     where series_id = v_series and regexp_replace(name, '\s*\(.*?\)\s*', '', 'g') = regexp_replace(p_name, '\s*\(.*?\)\s*', '', 'g') limit 1;
  end if;
  if v_entry is null then return null; end if;
  if coalesce(trim(p_reason), '') = '' then return v_entry; end if;   -- 이유 없이 부르면 항목 번호만(목소리 바꾸기용)
  select * into cur from public.bible_versions where entry_id = v_entry and status = 'active' order by version_no desc limit 1;
  if cur.id is null then return v_entry; end if;
  select coalesce(max(version_no), 0) + 1 into v_no from public.bible_versions where entry_id = v_entry;
  insert into public.bible_versions (entry_id, version_no, description, details, image_paths, voice_path, voice_id, soul_id, prompt_en, change_reason, status, proposed_by)
  values (v_entry, v_no, cur.description, cur.details, cur.image_paths, cur.voice_path, cur.voice_id, cur.soul_id, cur.prompt_en,
          left('제작 중 대표 지시: ' || p_reason, 500), 'proposed', '대표')
  returning id into v_new;
  update public.bible_versions set status = 'retired' where entry_id = v_entry and status = 'active' and id <> v_new;
  update public.bible_versions set status = 'active', decided_at = now(), proposed_by = '대표' where id = v_new;
  return v_entry;
end $$;
revoke all on function public.agent_bible_owner_change(text, uuid, text, text) from public;
grant execute on function public.agent_bible_owner_change(text, uuid, text, text) to anon, authenticated;
