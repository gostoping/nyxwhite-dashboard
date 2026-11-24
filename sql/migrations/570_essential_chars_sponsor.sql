-- 대표 결정: 필수는 인물과 협찬(소품·장소)뿐. 대본에서 나온 장소·소품·배경은 사진을 만들지 않고 묘사(영어 프롬프트)로 바로 바이블에 적용,
-- 제작하며 나온 장면 사진이 그 항목의 참고 사진으로 쌓여(최근 6장) 바이블에 저장된다.
alter table public.bible_entries add column if not exists refs jsonb not null default '[]';

create or replace function public.nyx_bible_essential_guard() returns trigger
language plpgsql as $$
begin
  new.essential := new.category in ('character', 'style', 'music') or coalesce(new.origin, '') = 'sponsor';
  return new;
end $$;
drop trigger if exists nyx_bible_essential_guard on public.bible_entries;
create trigger nyx_bible_essential_guard before insert or update on public.bible_entries
  for each row execute function public.nyx_bible_essential_guard();
update public.bible_entries set essential = essential;   -- 위 규칙으로 다시 계산

-- 이미 수정안만 있고 적용본이 없는 필수 아닌 장소·소품·배경은 지금 바로 적용(묘사가 곧 프롬프트)
update public.bible_versions bv set status = 'active', decided_at = now()
  from public.bible_entries be
 where be.id = bv.entry_id and be.category in ('location', 'prop', 'background') and not be.essential
   and bv.status = 'proposed'
   and bv.id = (select x.id from public.bible_versions x where x.entry_id = be.id and x.status = 'proposed' order by x.version_no desc limit 1)
   and not exists (select 1 from public.bible_versions y where y.entry_id = be.id and y.status = 'active');

-- 참고 사진 쌓기: 장면 사진이 나올 때마다 그 컷에 나온 필수 아닌 항목에 붙인다(같은 사진은 한 번, 최근 6장)
create or replace function public.agent_bible_add_ref(p_token text, p_entry uuid, p_path text, p_cut text default null, p_ep int default null) returns int
language plpgsql security definer set search_path = public as $$
declare n int;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.bible_entries
     set refs = (select coalesce(jsonb_agg(t.x order by t.x->>'at' desc), '[]') from (
                   select s.x from (
                     select x from jsonb_array_elements(refs) x where x->>'path' <> p_path
                     union all select jsonb_build_object('path', p_path, 'cut', p_cut, 'ep', p_ep, 'at', now())) s(x)
                   order by s.x->>'at' desc limit 6) t(x)),
         temp_image = coalesce(temp_image, jsonb_build_object('path', p_path, 'model', coalesce(p_cut, '') || ' 장면 사진(자동 참고)', 'at', now()))
   where id = p_entry;
  select jsonb_array_length(refs) into n from public.bible_entries where id = p_entry;
  return n;
end $$;
revoke all on function public.agent_bible_add_ref(text, uuid, text, text, int) from public;
grant execute on function public.agent_bible_add_ref(text, uuid, text, text, int) to anon, authenticated;

-- 대본 추출: 필수 아닌 장소·소품·배경은 바로 적용
create or replace function public.agent_bible_upsert(p_token text, p_series uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare it jsonb; v_id uuid; v_ver uuid; n_new int := 0; n_prop int := 0; n_auto int := 0; v_cat text;
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
              v_cat in ('character', 'style', 'music'))
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
              coalesce(nullif(it->>'reason', ''), '대본에서 필요 항목으로 추출'), 'proposed', '작가실(대본 추출)')
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

-- 에이전트가 읽는 바이블에 쌓인 참고 사진(refs)을 넣는다
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
                      'variant', vx.name, 'essential', a.essential, 'refs', coalesce(a.refs, '[]'::jsonb), 'change_note', coalesce(vx.change_note, a.change_note), 'apply_eps', to_jsonb(a.apply_eps))
                      order by a.category, a.sort_order), '[]')
              from (select be.id entry_id, be.essential, be.refs, be.name, be.category, be.summary, be.same_as, be.sort_order, be.temp_image, be.apply_eps, be.change_note,
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
