-- 사진이 빈 바이블 항목: 제작(컷 설계)이 시작될 때 에이전트가 작가실 묘사로 사진 1장을 만들어 '임시 기준 사진'으로 고정한다.
-- 임의로 정해진 모습이라도 모든 컷에서 같게 나온다. 대표가 사진을 올리면(image_paths) 그쪽이 우선이다.
-- 임시 사진은 takes 저장소에 있고, 바이블 저장소로 옮기는 '확정'은 대표 화면에서만 한다.
alter table public.bible_entries add column if not exists temp_image jsonb;   -- {"path": "bible_temp/…jpg", "model": "", "at": ""}

create or replace function public.agent_bible_set_temp_photo(p_token text, p_entry uuid, p_path text, p_model text default null) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v jsonb;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  -- 먼저 만든 쪽이 이긴다(여러 에이전트가 동시에 만들어도 하나로 고정)
  update public.bible_entries set temp_image = jsonb_build_object('path', p_path, 'model', p_model, 'at', now())
   where id = p_entry and temp_image is null;
  select temp_image into v from public.bible_entries where id = p_entry;
  return v;
end $$;
revoke all on function public.agent_bible_set_temp_photo(text, uuid, text, text) from public;
grant execute on function public.agent_bible_set_temp_photo(text, uuid, text, text) to anon, authenticated;

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
                      'prompt_en', bv.prompt_en, 'same_as', (select o.name from public.bible_entries o where o.id = be.same_as),
                      'entry_id', be.id, 'temp_image', be.temp_image) order by be.category, be.sort_order), '[]')
              from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
              where be.series_id = v_ser),
    'notes', (select coalesce(jsonb_agg(jsonb_build_object('scene_no', n.scene_no, 'priority', n.priority, 'important', n.important,
                      'content', n.content) order by n.scene_no nulls first), '[]')
              from public.episode_notes n where n.episode_id = p_episode and n.status <> 'closed'),
    'lessons', public.agent_get_lessons(p_token, v_ser, false),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings));
end $$;
