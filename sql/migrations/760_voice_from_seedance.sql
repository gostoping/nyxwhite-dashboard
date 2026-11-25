-- 대표 결정(11/25): 목소리는 바이블에서 만들지 않는다. Seedance가 영상과 함께 만든 목소리를 그대로 쓰고,
-- 제작에서 만든 목소리를 바이블(인물 카드)에 저장해 둔다. 지금까지 만든 목소리(ElevenLabs)는 바이블·제작에서 모두 지운다.
update public.app_settings set value = coalesce(value, '{}'::jsonb) || '{"dialogue_tool": "higgsfield", "voice_source": "seedance"}'::jsonb where key = 'generation';
insert into public.app_settings (key, value) select 'generation', '{"dialogue_tool": "higgsfield", "voice_source": "seedance"}'::jsonb
 where not exists (select 1 from public.app_settings where key = 'generation');

drop trigger if exists nyx_voice_queue on public.bible_versions;   -- 목소리 자동 추천 끔
update public.pipeline_tasks set status = 'cancelled', error = '대표 결정: 목소리는 Seedance가 영상과 함께 만든 것을 씁니다'
 where kind in ('voice_cast', 'voice_design', 'voice_save') and status in ('queued', 'running');

select set_config('nyx.skip_voice_sync', '1', true);
update public.bible_versions set voice_id = null, voice_path = null where voice_id is not null or voice_path is not null;
delete from public.voice_designs;

-- 제작: 컷의 대사 미리 듣기(ElevenLabs 소리) 지우기. 대사 글·감정 지시는 남긴다(Seedance 프롬프트에 쓰임)
update public.production_cuts c set voice_lines = (
  select coalesce(jsonb_agg(jsonb_strip_nulls(jsonb_build_object('speaker', l->'speaker', 'text', l->'text', 'direction', l->'direction', 'tags', l->'tags'))), '[]'::jsonb)
  from jsonb_array_elements(coalesce(c.voice_lines, '[]'::jsonb)) l)
 where jsonb_array_length(coalesce(c.voice_lines, '[]'::jsonb)) > 0;

-- 제작에서 만든 목소리를 바이블에 저장: 'takes:경로'(영상의 대사·현장음 소리). 동일인 카드도 같이.
create or replace function public.nyx_set_voice_sample(p_entry uuid, p_path text, p_force boolean) returns boolean
language plpgsql security definer set search_path = public as $$
declare v_root uuid; n int;
begin
  select coalesce(same_as, id) into v_root from public.bible_entries where id = p_entry;
  update public.bible_versions v set voice_path = p_path
   where v.status = 'active' and v.entry_id in (select id from public.bible_entries where id = v_root or same_as = v_root)
     and (p_force or v.voice_path is null);
  get diagnostics n = row_count;
  return n > 0;
end $$;
revoke all on function public.nyx_set_voice_sample(uuid, text, boolean) from public, anon, authenticated;

create or replace function public.agent_bible_voice_sample(p_token text, p_episode uuid, p_name text, p_path text) returns boolean
language plpgsql security definer set search_path = public as $$
declare v_entry uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select be.id into v_entry from public.bible_entries be join public.episodes e on e.series_id = be.series_id
   where e.id = p_episode and be.category = 'character'
     and (be.name = p_name or regexp_replace(be.name, '\s*\(.*?\)\s*', '', 'g') = regexp_replace(p_name, '\s*\(.*?\)\s*', '', 'g'))
   order by (be.name = p_name) desc limit 1;
  if v_entry is null then return false; end if;
  return public.nyx_set_voice_sample(v_entry, p_path, false);   -- 처음 생긴 목소리만 자동 저장(대표가 고른 것은 덮지 않음)
end $$;
revoke all on function public.agent_bible_voice_sample(text, uuid, text, text) from public;
grant execute on function public.agent_bible_voice_sample(text, uuid, text, text) to anon, authenticated;

create or replace function public.owner_bible_voice_sample(p_entry uuid, p_path text) returns boolean
language plpgsql security definer set search_path = public as $$
begin
  if not public.nyx_is_owner() then raise exception 'row-level security: 대표만 바꿀 수 있습니다'; end if;
  return public.nyx_set_voice_sample(p_entry, p_path, true);
end $$;
revoke all on function public.owner_bible_voice_sample(uuid, text) from public, anon;
grant execute on function public.owner_bible_voice_sample(uuid, text) to authenticated;
