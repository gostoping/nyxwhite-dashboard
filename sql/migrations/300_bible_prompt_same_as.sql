-- ① 영어 프롬프트: 바이블 묘사·세부 사항을 한국어로 적어도 된다. 적용되는 순간 작가실이 영어 고정 프롬프트(prompt_en)로 한 번 옮겨 두고,
--    감독·사진 모델은 매번 번역하지 않고 이 문장을 그대로 쓴다(컷마다 번역이 달라져 얼굴이 흔들리는 것을 막음).
-- ② 동일인: '미래의 남자'처럼 같은 사람의 다른 시점·호칭을 같은 사람(예: 탄)에 연결한다.
alter table public.bible_versions add column if not exists prompt_en text;
alter table public.bible_entries add column if not exists same_as uuid references public.bible_entries(id) on delete set null;
alter table public.bible_entries drop constraint if exists bible_entries_same_as_self;
alter table public.bible_entries add constraint bible_entries_same_as_self check (same_as is null or same_as <> id);

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe','bible_place','bible_prompt'));

-- 묘사·세부 사항이 바뀌면 예전 영어 프롬프트는 버린다
create or replace function public.nyx_bible_prompt_reset() returns trigger
language plpgsql as $$
begin
  if (new.description is distinct from old.description or new.details is distinct from old.details)
     and new.prompt_en is not distinct from old.prompt_en then
    new.prompt_en := null;
  end if;
  return new;
end $$;
drop trigger if exists nyx_bible_prompt_reset on public.bible_versions;
create trigger nyx_bible_prompt_reset before update on public.bible_versions for each row execute function public.nyx_bible_prompt_reset();

-- 적용본에 한글이 있고 영어 프롬프트가 없으면 작가실에 맡긴다(같은 버전에 대기 중인 일이 있으면 또 넣지 않음)
create or replace function public.nyx_bible_prompt_queue() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'active' and new.prompt_en is null
     and (coalesce(new.description, '') || coalesce(new.details, '')) ~ '[가-힣]'
     and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running')
                     and t.payload->>'version_id' = new.id::text) then
    insert into public.pipeline_tasks (kind, series_id, payload)
    select 'bible_prompt', be.series_id, jsonb_build_object('version_id', new.id, 'entry_id', be.id)
      from public.bible_entries be where be.id = new.entry_id and be.category in ('character', 'location', 'background', 'prop', 'style');
  end if;
  return null;
end $$;
drop trigger if exists nyx_bible_prompt_queue on public.bible_versions;
create trigger nyx_bible_prompt_queue after insert or update on public.bible_versions for each row execute function public.nyx_bible_prompt_queue();

create or replace function public.agent_bible_set_prompt(p_token text, p_version uuid, p_prompt text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if coalesce(trim(p_prompt), '') = '' then raise exception '프롬프트가 비어 있습니다'; end if;
  update public.bible_versions set prompt_en = left(p_prompt, 4000) where id = p_version;
end $$;
revoke all on function public.agent_bible_set_prompt(text, uuid, text) from public;
grant execute on function public.agent_bible_set_prompt(text, uuid, text) to anon, authenticated;

-- 이미 적용된 한글 묘사도 한 번씩 옮겨 둔다
insert into public.pipeline_tasks (kind, series_id, payload)
select 'bible_prompt', be.series_id, jsonb_build_object('version_id', bv.id, 'entry_id', be.id)
  from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id
 where bv.status = 'active' and bv.prompt_en is null and (coalesce(bv.description, '') || coalesce(bv.details, '')) ~ '[가-힣]'
   and be.category in ('character', 'location', 'background', 'prop', 'style')
   and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running') and t.payload->>'version_id' = bv.id::text);

-- 감독·컷 설계가 받는 바이블에 영어 프롬프트와 동일인을 함께 넘긴다
create or replace function public.agent_context(p_token text, p_episode uuid, p_version uuid default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_ser uuid; v_ver uuid := p_version;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select series_id into v_ser from public.episodes where id = p_episode;
  if v_ver is null then
    select id into v_ver from public.script_versions where episode_id = p_episode and status = 'confirmed'
     order by version_no desc nulls last limit 1;
  end if;
  return jsonb_build_object(
    'episode', (select to_jsonb(e) from public.episodes e where e.id = p_episode),
    'series',  (select jsonb_build_object('id', s.id, 'title', s.title) from public.series s where s.id = v_ser),
    'script',  (select jsonb_build_object('id', v.id, 'version_no', v.version_no, 'status', v.status, 'content', v.content)
                  from public.script_versions v where v.id = v_ver),
    'latest_draft', (select jsonb_build_object('id', v.id, 'content', v.content, 'note', v.note)
                  from public.script_versions v where v.episode_id = p_episode and v.status = 'draft' order by v.created_at desc limit 1),
    'neighbors', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                    'excerpt', left((select v.content from public.script_versions v where v.episode_id = e.id
                                       order by (v.status = 'confirmed') desc, v.created_at desc limit 1), 1500)) order by e.number), '[]')
                  from public.episodes e, public.episodes me
                  where me.id = p_episode and e.series_id = me.series_id and e.number in (me.number - 1, me.number + 1)),
    'guidelines', (select coalesce(jsonb_object_agg(st.name, g.content), '{}')
                  from public.staff st join public.staff_guidelines g on g.staff_id = st.id and g.status = 'active'),
    'bible', (select coalesce(jsonb_agg(jsonb_build_object('name', be.name, 'category', be.category, 'summary', be.summary,
                      'description', bv.description, 'details', bv.details, 'images', bv.image_paths, 'voice_id', bv.voice_id, 'soul_id', bv.soul_id,
                      'prompt_en', bv.prompt_en, 'same_as', (select o.name from public.bible_entries o where o.id = be.same_as)) order by be.category, be.sort_order), '[]')
              from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
              where be.series_id = v_ser),
    'notes', (select coalesce(jsonb_agg(jsonb_build_object('scene_no', n.scene_no, 'priority', n.priority, 'important', n.important,
                      'content', n.content) order by n.scene_no nulls first), '[]')
              from public.episode_notes n where n.episode_id = p_episode and n.status <> 'closed'),
    'lessons', public.agent_get_lessons(p_token, v_ser, false),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings));
end $$;
