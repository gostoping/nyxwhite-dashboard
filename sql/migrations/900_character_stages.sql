-- 대표 요청(2026-11-27): 인물 모습 단계(A1·A2·…·H, 성형 전·후 등).
-- 같은 사람의 겉모습 단계를 '원래 인물(same_as) + 단계 코드(stage)' 항목으로 둔다. 목소리·정체성은 원래 인물, 얼굴·체형·머리·의상은 단계 항목.
-- 감독은 대본 컷의 '린(A1)' 표기대로 그 단계 항목을 쓴다. 단계 항목은 화 단위 '다른 모습'(예뻐진 린) 대체 규칙에 끼지 않는다.
alter table public.bible_entries add column if not exists stage text;

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
                      'variant', vx.name, 'stage', a.stage, 'essential', a.essential, 'refs', coalesce(a.refs, '[]'::jsonb), 'change_note', coalesce(vx.change_note, a.change_note), 'apply_eps', to_jsonb(a.apply_eps))
                      order by a.category, a.sort_order), '[]')
              from (select be.id entry_id, be.stage, be.essential, be.refs, be.name, be.category, be.summary, be.same_as, be.sort_order, be.temp_image, be.apply_eps, be.change_note,
                           bv.description, bv.details, bv.image_paths, bv.voice_id, bv.soul_id, bv.prompt_en
                      from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
                     where be.series_id = v_ser
                       and (be.apply_eps is null or cardinality(be.apply_eps) = 0 or v_no = any(be.apply_eps))) a
              left join lateral (
                select x.id entry_id, x.name, x.change_note, x.temp_image, xv.description, xv.details, xv.image_paths, xv.voice_id, xv.soul_id, xv.prompt_en
                  from public.bible_entries x join public.bible_versions xv on xv.entry_id = x.id and xv.status = 'active'
                 where x.same_as = a.entry_id and x.id <> a.entry_id and x.stage is null and x.apply_eps is not null and v_no = any(x.apply_eps)
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


create or replace function public.agent_bible_overview(p_token text, p_series uuid, p_episode uuid default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return jsonb_build_object(
    'series', (select jsonb_build_object('id', id, 'title', title) from public.series where id = p_series),
    'entries', (select coalesce(jsonb_agg(jsonb_build_object('category', be.category, 'name', be.name, 'summary', be.summary,
                   'description', bv.description, 'stage', be.stage, 'same_as', (select o.name from public.bible_entries o where o.id = be.same_as)) order by be.category, be.sort_order), '[]')
                from public.bible_entries be left join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
                where be.series_id = p_series),
    'episodes', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                   'text', (select v.content from public.script_versions v where v.episode_id = e.id
                            order by (v.status = 'confirmed') desc, v.created_at desc limit 1)) order by e.number), '[]')
                 from public.episodes e where e.series_id = p_series and (p_episode is null or e.id = p_episode)));
end $$;

create or replace function public.agent_bible_upsert(p_token text, p_series uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare it jsonb; v_id uuid; v_ver uuid; n_new int := 0; n_prop int := 0; n_auto int := 0; v_cat text;
  v_root uuid; v_stage text; v_rootname text; v_eps int[];
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  for it in select * from jsonb_array_elements(coalesce(p_items, '[]')) loop
    v_cat := it->>'category';
    if v_cat not in ('character', 'location', 'background', 'prop', 'style', 'music') or coalesce(trim(it->>'name'), '') = '' then continue; end if;
    -- 인물 모습 단계(같은 사람의 겉모습 단계): same_as(원래 인물 이름) + stage(단계 코드)
    v_stage := nullif(trim(coalesce(it->>'stage', '')), ''); v_rootname := nullif(trim(coalesce(it->>'same_as', '')), ''); v_root := null;
    if v_cat <> 'character' or v_rootname is null or lower(v_rootname) = lower(trim(it->>'name')) then v_stage := null; v_rootname := null; end if;
    if v_rootname is not null then
      select id into v_root from public.bible_entries where series_id = p_series and category = 'character' and lower(name) = lower(v_rootname);
      if v_root is null then   -- 원래 인물이 아직 없으면 먼저 만든다(묘사는 다음 추출이나 대표가 채움)
        insert into public.bible_entries (series_id, category, name, summary, origin, appears, sort_order, essential)
        values (p_series, 'character', left(v_rootname, 80), '모습 단계가 있는 인물', 'script', '[]',
                (select coalesce(max(sort_order), 0) + 1 from public.bible_entries where series_id = p_series), true)
        returning id into v_root;
        n_new := n_new + 1;
      end if;
    end if;
    v_eps := (select array_agg(distinct (x->>'ep')::int order by (x->>'ep')::int) from jsonb_array_elements(coalesce(it->'appears', '[]')) x where (x->>'ep') ~ '^\d+$');
    select id into v_id from public.bible_entries where series_id = p_series and category = v_cat and lower(name) = lower(trim(it->>'name'));
    if v_id is null and v_stage is not null then   -- 예전 추출이 '린(A1)'·'린A1'처럼 만든 같은 단계가 있으면 그것을 쓴다
      select id into v_id from public.bible_entries where series_id = p_series and category = 'character'
        and (stage = v_stage and same_as = v_root or lower(replace(replace(replace(name, '(', ''), ')', ''), ' ', '')) = lower(v_rootname || v_stage)) limit 1;
    end if;
    if v_id is null then
      insert into public.bible_entries (series_id, category, name, summary, origin, appears, sort_order, essential)
      values (p_series, v_cat, left(trim(it->>'name'), 80), left(it->>'summary', 300), 'script', coalesce(it->'appears', '[]'),
              (select coalesce(max(sort_order), 0) + 1 from public.bible_entries where series_id = p_series),
              v_cat in ('character', 'style', 'music'))
      returning id into v_id;
      n_new := n_new + 1;
    else
      update public.bible_entries set appears = (select coalesce(jsonb_agg(distinct x), '[]') from jsonb_array_elements(appears || coalesce(it->'appears', '[]')) x)
       where id = v_id;
    end if;
    if v_stage is not null then   -- 단계 항목: 원래 인물과 잇고, 나오는 화를 모아 둔다(그 화의 감독에게만 보이게)
      update public.bible_entries set same_as = v_root, stage = v_stage,
             name = case when lower(name) = lower(trim(it->>'name')) then name else left(trim(it->>'name'), 80) end,
             apply_eps = (select array_agg(distinct x order by x) from unnest(coalesce(apply_eps, '{}') || coalesce(v_eps, '{}')) x)
       where id = v_id and v_id <> v_root;
    end if;
    -- 인물: 아직 적용 전이면 예전 추출이 올린 수정안을 이번 결과로 바꾼다(단계 구조로 다시 찾을 때 묵은 수정안이 남지 않게)
    if v_cat = 'character' and not exists (select 1 from public.bible_versions where entry_id = v_id and status = 'active')
       and coalesce(it->>'description', '') <> '' then
      delete from public.bible_versions where entry_id = v_id and status = 'proposed'
         and (change_reason = '대본에서 필요 항목으로 추출' or change_reason like '[대본 추출]%')
         and description is distinct from left(it->>'description', 2000);
    end if;
    -- 묘사가 비어 와도 수정안을 만든다(없으면 감독이 그 항목을 못 봄): 한 줄 설명 → 이름 순으로 채운다
    -- 다시 추천(force_proposal)이면 적용본이 있어도 새 수정안을 올린다(같은 내용 대기 중이면 생략)
    if (coalesce(it->>'force_proposal', '') = 'true'
        and not exists (select 1 from public.bible_versions where entry_id = v_id and status = 'proposed' and description = it->>'description'))
       or not exists (select 1 from public.bible_versions where entry_id = v_id and status in ('active', 'proposed')) then
      insert into public.bible_versions (entry_id, description, details, change_reason, status, proposed_by)
      values (v_id, left(coalesce(nullif(it->>'description', ''), nullif(it->>'summary', ''), it->>'name'), 2000), left(it->>'details', 2000),
              coalesce('[대본 추출] ' || nullif(it->>'reason', ''), '대본에서 필요 항목으로 추출'), 'proposed', '작가실(대본 추출)')
      returning id into v_ver;
      n_prop := n_prop + 1;
      -- 필수가 아닌 장소·소품·배경: 사진 없이 묘사(프롬프트)로 바로 바이블에 적용(대표 결정: 인물·협찬만 필수)
      if v_cat in ('location', 'prop', 'background') and not exists (select 1 from public.bible_entries where id = v_id and (essential or origin = 'sponsor'))
         and not exists (select 1 from public.bible_versions where entry_id = v_id and status = 'active') then
        update public.bible_versions set status = 'active', decided_at = now() where id = v_ver;
        n_auto := n_auto + 1;
      end if;
    end if;
  end loop;
  return jsonb_build_object('new_entries', n_new, 'proposals', n_prop, 'auto_applied', n_auto);
end $$;
