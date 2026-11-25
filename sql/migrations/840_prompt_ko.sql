-- 한글 프롬프트: 대표가 한글로 읽고 고치면 감독이 영어 프롬프트에 그대로 반영한다
alter table public.production_cuts add column if not exists prompt_ko text;

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
      voice_lines, frame_first, frame_last, use_end_frame, continue_prev, staging, prompt_ko)
    values (p_episode, p_version, p_scene, (c->>'cut_no')::int, c->>'code', c->>'scene_heading',
      least(15, greatest(4, coalesce((c->>'seconds')::int, 10))), c->>'summary', c->>'dialogue', c->>'emotion',
      coalesce(c->'expression_beats', '[]'), c->>'prompt',
      coalesce(array(select jsonb_array_elements_text(c->'reference_names')), '{}'), c->>'camera', c->>'director_note',
      c->'review', coalesce(c->>'resolution', '480p'), (c->>'est_usd')::numeric,
      coalesce(c->'voice_lines', '[]'), coalesce(c->'frame_first', '{}'), coalesce(c->'frame_last', '{}'),
      coalesce((c->>'use_end_frame')::boolean, false), coalesce((c->>'continue_prev')::boolean, false),
      case when jsonb_typeof(c->'staging') = 'object' then c->'staging' else '{}'::jsonb end, c->>'prompt_ko');
    n := n + 1;
  end loop;
  insert into public.scene_packages (episode_id, scene_no, heading, plan)
  values (p_episode, p_scene, (select c2->>'scene_heading' from jsonb_array_elements(p_cuts) c2 limit 1),
          coalesce((select c2->'scene_plan' from jsonb_array_elements(p_cuts) c2 where jsonb_typeof(c2->'scene_plan') = 'object' limit 1), '{}'::jsonb))
  on conflict (episode_id, scene_no) do update set heading = coalesce(excluded.heading, scene_packages.heading),
    plan = case when excluded.plan <> '{}'::jsonb then excluded.plan else scene_packages.plan end, updated_at = now();
  perform public.nyx_log('감독', p_scene || '씬 컷 ' || n || '개 설계', p_episode, v_dev);
  return n;
end $$;

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
    prep_status = coalesce(p->>'prep_status', prep_status),
    camera = coalesce(p->>'camera', camera), prompt_ko = case when p ? 'prompt_ko' then p->>'prompt_ko' else prompt_ko end, dialogue = case when p ? 'dialogue' then p->>'dialogue' else dialogue end,
    seconds = case when p ? 'seconds' then least(15, greatest(4, (p->>'seconds')::int)) else seconds end, summary = coalesce(p->>'summary', summary), emotion = coalesce(p->>'emotion', emotion),
    staging = case when jsonb_typeof(p->'staging') = 'object' then p->'staging' else staging end,
    reviews = case when p ? 'reviews' then coalesce(reviews, '{}'::jsonb) || (p->'reviews') else reviews end
  where id = p_id;
end $$;
