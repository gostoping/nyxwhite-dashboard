-- ① 연속성: 감독이 컷 설계를 마치면 이 화가 끝났을 때 인물·소품 상태를 기록하고, 다음 화 감독은 그걸 먼저 읽는다
create or replace function public.agent_add_continuity(p_token text, p_episode uuid, p_items jsonb, p_source text default '감독(컷 설계)') returns int
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_ser uuid; it jsonb; v_entry uuid; n int := 0;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  select series_id into v_ser from public.episodes where id = p_episode;
  -- 같은 출처의 예전 기록은 새 설계로 바꾼다(대표가 직접 남긴 기록은 그대로)
  -- (기록 출처 칸은 자동 채움 규칙이 'AI'로 바꾸므로, 에이전트 기기가 남긴 기록으로 구분한다)
  delete from public.continuity_logs where episode_id = p_episode and device_id is not null;
  for it in select * from jsonb_array_elements(coalesce(p_items, '[]')) loop
    select id into v_entry from public.bible_entries
     where series_id = v_ser and (name = it->>'name' or regexp_replace(name, '\s*[\(\[].*?[\)\]]\s*', '', 'g') = regexp_replace(it->>'name', '\s*[\(\[].*?[\)\]]\s*', '', 'g'))
     order by (name = it->>'name') desc limit 1;
    if v_entry is null or coalesce(trim(it->>'state'), '') = '' then continue; end if;
    insert into public.continuity_logs (episode_id, entry_id, scene_no, state, source, device_id)
    values (p_episode, v_entry, nullif(it->>'scene_no', '')::int, left(it->>'state', 1000), p_source, v_dev);
    n := n + 1;
  end loop;
  return n;
end $$;
revoke all on function public.agent_add_continuity(text, uuid, jsonb, text) from public;
grant execute on function public.agent_add_continuity(text, uuid, jsonb, text) to anon, authenticated;

-- ② 실패 컷 자동 재시도: 설정 '자동 재시도 횟수'(generation.max_auto_retries)만큼은 대표가 미리 승인한 것으로 보고
--    에이전트가 감독 수정 → 다시 생성까지 이어 간다. 그 횟수를 넘으면 지금처럼 대표 승인만.
create or replace function public.agent_add_task(p_token text, p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid; v_max int; v_takes int;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  if p->>'kind' = 'generate' then
    select coalesce((value->>'max_auto_retries')::int, 0) into v_max from public.app_settings where key = 'generation';
    select count(*) into v_takes from public.production_takes where cut_id = (p->>'cut_id')::uuid;
    if coalesce(p->'payload'->>'auto_retry', '') <> 'true' or v_takes = 0 or v_takes > coalesce(v_max, 0) then
      raise exception '영상 생성은 대표 승인으로만 만들 수 있습니다(자동 재시도 한도 초과)';
    end if;
  end if;
  insert into public.pipeline_tasks (kind, episode_id, scene_no, cut_id, take_id, payload, platform, not_before)
  values (p->>'kind', (p->>'episode_id')::uuid, (p->>'scene_no')::int, (p->>'cut_id')::uuid, (p->>'take_id')::uuid,
          coalesce(p->'payload', '{}'::jsonb), p->>'platform', now() + make_interval(secs => coalesce((p->>'delay')::int, 0)))
  returning id into v_id;
  return v_id;
end $$;

-- 자동 재시도 때 감수 의견을 감독 수정 요청으로 남길 수 있게
create or replace function public.agent_update_cut(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.production_cuts set
    status = coalesce(p->>'status', status), prompt = coalesce(p->>'prompt', prompt), review = coalesce(p->'review', review),
    director_note = coalesce(p->>'director_note', director_note), best_take_id = coalesce((p->>'best_take_id')::uuid, best_take_id),
    expression_beats = coalesce(p->'expression_beats', expression_beats), est_usd = coalesce((p->>'est_usd')::numeric, est_usd),
    reference_names = case when p ? 'reference_names' then array(select jsonb_array_elements_text(p->'reference_names')) else reference_names end,
    fix_request = case when p ? 'clear_fix' then null else coalesce(p->>'fix_request', fix_request) end,
    voice_lines = coalesce(p->'voice_lines', voice_lines), frame_first = coalesce(p->'frame_first', frame_first),
    frame_last = coalesce(p->'frame_last', frame_last), use_end_frame = coalesce((p->>'use_end_frame')::boolean, use_end_frame),
    continue_prev = coalesce((p->>'continue_prev')::boolean, continue_prev), prep_review = coalesce(p->'prep_review', prep_review),
    prep_status = coalesce(p->>'prep_status', prep_status)
  where id = p_id;
end $$;

-- ④ 가편집 자동: 한 화의 모든 컷이 통과하면 에이전트가 기본 타임라인(통과 영상 순서대로 + 대사 자막)으로 정확한 미리보기를 만든다.
--    대표가 이미 편집을 시작했으면(타임라인에 컷이 있으면) 건드리지 않는다.
create or replace function public.agent_rough_cut(p_token text, p_episode uuid, p_timeline jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_proj uuid; v_clips int; v_rev int; v_render uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select id, jsonb_array_length(coalesce(timeline->'clips', '[]')), rev into v_proj, v_clips, v_rev from public.edit_projects where episode_id = p_episode;
  if v_proj is not null and v_clips > 0 then return null; end if;
  if v_proj is null then
    insert into public.edit_projects (episode_id, timeline) values (p_episode, p_timeline) returning id, rev into v_proj, v_rev;
  else
    update public.edit_projects set timeline = p_timeline, rev = rev + 1, updated_at = now() where id = v_proj returning rev into v_rev;
  end if;
  insert into public.edit_renders (project_id, episode_id, kind, rev, timeline) values (v_proj, p_episode, 'preview', v_rev, p_timeline) returning id into v_render;
  insert into public.pipeline_tasks (kind, episode_id, payload) values ('assemble', p_episode, jsonb_build_object('render_id', v_render, 'auto', true));
  return v_render;
end $$;
revoke all on function public.agent_rough_cut(text, uuid, jsonb) from public;
grant execute on function public.agent_rough_cut(text, uuid, jsonb) to anon, authenticated;

-- ⑤ 배포 대기: 편집에서 대표가 'OK(배포로 넘기기)'한 완성본. 시리즈·화별로 배포 탭에 보인다
create table if not exists public.releases (
  id           uuid primary key default gen_random_uuid(),
  episode_id   uuid not null unique references public.episodes(id) on delete cascade,
  render_id    uuid references public.edit_renders(id) on delete set null,
  status       text not null default 'ready' check (status in ('ready', 'scheduled', 'published', 'hold')),
  title        text,
  description  text,
  planned_at   timestamptz,
  published_url text,
  note         text,
  approved_at  timestamptz not null default now(),
  published_at timestamptz,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
alter table public.releases enable row level security;
drop policy if exists nyx_read on public.releases;
drop policy if exists nyx_owner on public.releases;
create policy nyx_read on public.releases for select to authenticated using (public.nyx_is_member());
create policy nyx_owner on public.releases for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());
grant select, insert, update, delete on public.releases to authenticated;
drop trigger if exists nyx_touch on public.releases;
create trigger nyx_touch before update on public.releases for each row execute function public.nyx_touch();

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe','bible_place','bible_prompt','api_check'));

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
