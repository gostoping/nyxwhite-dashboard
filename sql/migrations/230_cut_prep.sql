-- 컷 설계 단계 확장: ① 컷 나누기(감독) → ② 대사 미리 듣기(ElevenLabs) → ③ 장면 사진(첫·끝) → 감수 → 대표 생성 승인
alter table public.production_cuts add column if not exists voice_lines  jsonb not null default '[]';  -- [{speaker,text,tags,direction,path,seconds,analysis}]
alter table public.production_cuts add column if not exists frame_first  jsonb not null default '{}';  -- {prompt, path, status, error}
alter table public.production_cuts add column if not exists frame_last   jsonb not null default '{}';
alter table public.production_cuts add column if not exists use_end_frame boolean not null default false;
alter table public.production_cuts add column if not exists continue_prev boolean not null default false;  -- 앞 컷 마지막 장면에서 이어 시작
alter table public.production_cuts add column if not exists prep_review  jsonb;                         -- 사진·목소리 감수 결과
alter table public.production_cuts add column if not exists prep_status  text not null default 'none'
  check (prep_status in ('none','preparing','ready','fix','failed'));

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast','assemble','edit_command'));

create or replace function public.agent_update_cut(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.production_cuts set
    status = coalesce(p->>'status', status), prompt = coalesce(p->>'prompt', prompt), review = coalesce(p->'review', review),
    director_note = coalesce(p->>'director_note', director_note), best_take_id = coalesce((p->>'best_take_id')::uuid, best_take_id),
    expression_beats = coalesce(p->'expression_beats', expression_beats), est_usd = coalesce((p->>'est_usd')::numeric, est_usd),
    reference_names = case when p ? 'reference_names' then array(select jsonb_array_elements_text(p->'reference_names')) else reference_names end,
    fix_request = case when p ? 'clear_fix' then null else fix_request end,
    voice_lines = coalesce(p->'voice_lines', voice_lines), frame_first = coalesce(p->'frame_first', frame_first),
    frame_last = coalesce(p->'frame_last', frame_last), use_end_frame = coalesce((p->>'use_end_frame')::boolean, use_end_frame),
    continue_prev = coalesce((p->>'continue_prev')::boolean, continue_prev), prep_review = coalesce(p->'prep_review', prep_review),
    prep_status = coalesce(p->>'prep_status', prep_status)
  where id = p_id;
end $$;

-- 감독이 컷을 저장할 때 새 칸도 함께
create or replace function public.agent_save_cuts(p_token text, p_episode uuid, p_version uuid, p_scene int, p_cuts jsonb) returns int
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); c jsonb; n int := 0;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  delete from public.production_cuts where episode_id = p_episode and scene_no = p_scene and status in ('designed','cancelled')
     and not exists (select 1 from public.production_takes t where t.cut_id = production_cuts.id);
  for c in select * from jsonb_array_elements(p_cuts) loop
    if exists (select 1 from public.production_cuts where episode_id = p_episode and scene_no = p_scene and cut_no = (c->>'cut_no')::int) then
      continue;
    end if;
    insert into public.production_cuts (episode_id, script_version_id, scene_no, cut_no, code, scene_heading, seconds, summary, dialogue,
      emotion, expression_beats, prompt, reference_names, camera, director_note, review, resolution, est_usd,
      voice_lines, frame_first, frame_last, use_end_frame, continue_prev)
    values (p_episode, p_version, p_scene, (c->>'cut_no')::int, c->>'code', c->>'scene_heading',
      least(15, greatest(4, coalesce((c->>'seconds')::int, 10))), c->>'summary', c->>'dialogue', c->>'emotion',
      coalesce(c->'expression_beats', '[]'), c->>'prompt',
      coalesce(array(select jsonb_array_elements_text(c->'reference_names')), '{}'), c->>'camera', c->>'director_note',
      c->'review', coalesce(c->>'resolution', '480p'), (c->>'est_usd')::numeric,
      coalesce(c->'voice_lines', '[]'), coalesce(c->'frame_first', '{}'), coalesce(c->'frame_last', '{}'),
      coalesce((c->>'use_end_frame')::boolean, false), coalesce((c->>'continue_prev')::boolean, false));
    n := n + 1;
  end loop;
  insert into public.scene_packages (episode_id, scene_no, heading)
  values (p_episode, p_scene, (select c2->>'scene_heading' from jsonb_array_elements(p_cuts) c2 limit 1))
  on conflict (episode_id, scene_no) do update set heading = coalesce(excluded.heading, scene_packages.heading), updated_at = now();
  perform public.nyx_log('감독', p_scene || '씬 컷 ' || n || '개 설계', p_episode, v_dev);
  return n;
end $$;

-- 바이블에 목소리 ID도 넘긴다
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
                      'description', bv.description, 'details', bv.details, 'images', bv.image_paths, 'voice_id', bv.voice_id) order by be.category, be.sort_order), '[]')
              from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
              where be.series_id = v_ser),
    'notes', (select coalesce(jsonb_agg(jsonb_build_object('scene_no', n.scene_no, 'priority', n.priority, 'important', n.important,
                      'content', n.content) order by n.scene_no nulls first), '[]')
              from public.episode_notes n where n.episode_id = p_episode and n.status <> 'closed'),
    'lessons', public.agent_get_lessons(p_token, v_ser, false),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings));
end $$;


-- 컷 준비 설정: 사진 모델(Higgsfield 경로)·목소리 모델·자동 준비
update public.app_settings set value = value || jsonb_build_object(
  'auto_prep', true, 'image_model', coalesce(value->>'image_model', ''), 'tts_model', 'eleven_v3', 'stt_model', 'scribe_v1')
 where key = 'generation';
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('higgsfield', 'seedance-2.5-i2v-480p', 'per_second', 0.1177, 'Seedance 2.5 사진→영상 480p', 'Higgsfield 공개 가격 2026-11'),
  ('higgsfield', 'seedance-2.5-i2v-720p', 'per_second', 0.2647, 'Seedance 2.5 사진→영상 720p', 'Higgsfield 공개 가격 2026-11(글→영상과 같은 단가 가정)'),
  ('higgsfield', 'seedance-2.5-i2v-1080p', 'per_second', 0.6518, 'Seedance 2.5 사진→영상 1080p', 'Higgsfield 공개 가격 2026-11')
on conflict (provider, model, unit) do nothing;

create or replace function public.agent_list_cuts(p_token text, p_episode uuid, p_scene int default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return coalesce((select jsonb_agg(to_jsonb(c) order by c.scene_no, c.cut_no) from public.production_cuts c
                   where c.episode_id = p_episode and (p_scene is null or c.scene_no = p_scene)), '[]');
end $$;
revoke all on function public.agent_list_cuts(text, uuid, int) from public;
grant execute on function public.agent_list_cuts(text, uuid, int) to anon, authenticated;
