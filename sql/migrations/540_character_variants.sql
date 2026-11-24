-- 같은 인물의 다른 모습(예: 성형 후 예뻐진 린)을 바이블 항목으로 두고, 동일인 + 적용 화로 연결한다.
-- apply_eps: 이 항목이 나오는 화(비우면 모든 화). change_note: 원래 모습과 무엇이 달라졌는지(작가실·감독·코디네이터에게 전달).
alter table public.bible_entries add column if not exists apply_eps int[];
alter table public.bible_entries add column if not exists change_note text;

create or replace function public.agent_context(p_token text, p_episode uuid, p_version uuid default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_ser uuid; v_ver uuid := p_version; v_no int;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select series_id, number into v_ser, v_no from public.episodes where id = p_episode;
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
    -- 바이블: 이 화에 적용되는 항목만(적용 화가 정해진 항목은 그 화에만).
    -- '동일인'의 다른 모습(예: 성형 후 예뻐진 린, 적용 화 5~)이 이 화에 적용되면, 원래 인물(린) 이름으로 그 모습의 묘사·사진·얼굴을 쓴다.
    'bible', (select coalesce(jsonb_agg(jsonb_build_object('name', a.name, 'category', a.category, 'summary', a.summary,
                      'description', coalesce(vx.description, a.description), 'details', coalesce(vx.details, a.details),
                      'images', coalesce(vx.image_paths, a.image_paths), 'voice_id', coalesce(vx.voice_id, a.voice_id), 'soul_id', coalesce(vx.soul_id, a.soul_id),
                      'prompt_en', case when vx.entry_id is not null then vx.prompt_en else a.prompt_en end,
                      'same_as', (select o.name from public.bible_entries o where o.id = a.same_as),
                      'entry_id', a.entry_id, 'temp_image', coalesce(vx.temp_image, a.temp_image),
                      'variant', vx.name, 'change_note', coalesce(vx.change_note, a.change_note), 'apply_eps', to_jsonb(a.apply_eps))
                      order by a.category, a.sort_order), '[]')
              from (select be.id entry_id, be.name, be.category, be.summary, be.same_as, be.sort_order, be.temp_image, be.apply_eps, be.change_note,
                           bv.description, bv.details, bv.image_paths, bv.voice_id, bv.soul_id, bv.prompt_en
                      from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
                     where be.series_id = v_ser
                       and (be.apply_eps is null or cardinality(be.apply_eps) = 0 or v_no = any(be.apply_eps))) a
              left join lateral (
                select x.id entry_id, x.name, x.change_note, x.temp_image, xv.description, xv.details, xv.image_paths, xv.voice_id, xv.soul_id, xv.prompt_en
                  from public.bible_entries x join public.bible_versions xv on xv.entry_id = x.id and xv.status = 'active'
                 where x.same_as = a.entry_id and x.id <> a.entry_id and x.apply_eps is not null and v_no = any(x.apply_eps)
                 order by x.created_at desc limit 1) vx on a.apply_eps is null or cardinality(a.apply_eps) = 0),
    -- 직전 화가 끝났을 때 상태(항목마다 가장 마지막 기록): 감독이 다음 화를 이어서 설계할 때 먼저 읽는다
    'prev_continuity', (select coalesce(jsonb_agg(jsonb_build_object('name', x.name, 'scene_no', x.scene_no, 'state', x.state) order by x.name), '[]')
                  from (select distinct on (l.entry_id) be.name, l.scene_no, l.state
                          from public.continuity_logs l join public.bible_entries be on be.id = l.entry_id
                          join public.episodes pe on pe.id = l.episode_id, public.episodes me
                         where me.id = p_episode and pe.series_id = me.series_id and pe.number = me.number - 1
                         order by l.entry_id, l.scene_no desc nulls last, l.created_at desc) x),
    'notes', (select coalesce(jsonb_agg(jsonb_build_object('scene_no', n.scene_no, 'priority', n.priority, 'important', n.important,
                      'content', n.content) order by n.scene_no nulls first), '[]')
              from public.episode_notes n where n.episode_id = p_episode and n.status <> 'closed'),
    'lessons', public.agent_get_lessons(p_token, v_ser, false),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings));
end $$;
