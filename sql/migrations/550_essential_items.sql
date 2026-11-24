-- 바이블 필수 항목: 인물·스타일·음악·협찬·필수 소품/장소만 미리 사진을 만들고 감수한다.
-- 필수가 아닌 소품·장소·배경은 묘사만 두고, 처음 나오는 컷의 장면 사진을 자동으로 그 항목의 참고 사진(임시 기준 사진)으로 쓴다.
alter table public.bible_entries add column if not exists essential boolean;
update public.bible_entries be set essential = (
       be.category in ('character', 'style', 'music') or be.origin = 'sponsor'
    or (be.placement is not null and be.placement::text not in ('null', '[]', '{}'))
    or exists (select 1 from public.bible_versions bv where bv.entry_id = be.id and bv.status = 'active' and (cardinality(coalesce(bv.image_paths, '{}')) > 0 or bv.soul_id is not null)))
 where be.essential is null;
alter table public.bible_entries alter column essential set default true;

-- 대본 추출: 작가실이 필수 여부를 같이 준다(인물·스타일·음악은 늘 필수)
create or replace function public.agent_bible_upsert(p_token text, p_series uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare it jsonb; v_id uuid; n_new int := 0; n_prop int := 0; v_cat text;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  for it in select * from jsonb_array_elements(coalesce(p_items, '[]')) loop
    v_cat := it->>'category';
    if v_cat not in ('character', 'location', 'background', 'prop', 'style', 'music') or coalesce(trim(it->>'name'), '') = '' then continue; end if;
    select id into v_id from public.bible_entries where series_id = p_series and category = v_cat and lower(name) = lower(trim(it->>'name'));
    if v_id is null then
      insert into public.bible_entries (series_id, category, name, summary, origin, appears, sort_order, essential)
      values (p_series, v_cat, left(trim(it->>'name'), 80), left(it->>'summary', 300), 'script', coalesce(it->'appears', '[]'),
              (select coalesce(max(sort_order), 0) + 1 from public.bible_entries where series_id = p_series),
              case when v_cat in ('character', 'style', 'music') then true
                   when it ? 'essential' then coalesce((it->>'essential')::boolean, false) else false end)
      returning id into v_id;
      n_new := n_new + 1;
    else
      update public.bible_entries set appears = (select coalesce(jsonb_agg(distinct x), '[]') from jsonb_array_elements(appears || coalesce(it->'appears', '[]')) x)
       where id = v_id;
    end if;
    -- 묘사가 비어 와도 수정안을 만든다(없으면 감독이 그 항목을 못 봄): 한 줄 설명 → 이름 순으로 채운다
    -- 다시 추천(force_proposal)이면 적용본이 있어도 새 수정안을 올린다(같은 내용 대기 중이면 생략)
    if (coalesce(it->>'force_proposal', '') = 'true'
        and not exists (select 1 from public.bible_versions where entry_id = v_id and status = 'proposed' and description = it->>'description'))
       or not exists (select 1 from public.bible_versions where entry_id = v_id and status in ('active', 'proposed')) then
      insert into public.bible_versions (entry_id, description, details, change_reason, status, proposed_by)
      values (v_id, left(coalesce(nullif(it->>'description', ''), nullif(it->>'summary', ''), it->>'name'), 2000), left(it->>'details', 2000),
              coalesce(nullif(it->>'reason', ''), '대본에서 필요 항목으로 추출'), 'proposed', '작가실(대본 추출)');
      n_prop := n_prop + 1;
    end if;
  end loop;
  return jsonb_build_object('new_entries', n_new, 'proposals', n_prop);
end $$;

-- 에이전트가 읽는 바이블에 필수 여부를 넣는다
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
                      'variant', vx.name, 'essential', a.essential, 'change_note', coalesce(vx.change_note, a.change_note), 'apply_eps', to_jsonb(a.apply_eps))
                      order by a.category, a.sort_order), '[]')
              from (select be.id entry_id, be.essential, be.name, be.category, be.summary, be.same_as, be.sort_order, be.temp_image, be.apply_eps, be.change_note,
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

-- 필수가 아닌 항목은 코디네이터 자동 감수를 하지 않는다
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
         and coalesce(be.essential, true);   -- 필수가 아닌 항목은 감수하지 않는다(처음 나온 장면 사진을 참고로 씀)
    end if;
  end if;
  return null;
end $$;
