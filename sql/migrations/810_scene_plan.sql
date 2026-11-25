-- 씬 연출 계획: 감독이 씬마다 화면 구성·방향(등장·좌우)·흐름(컷 연결)·분위기·앞뒤 씬 연결을 적는다. 제작 탭 씬 카드에 보인다.
alter table public.scene_packages add column if not exists plan jsonb not null default '{}'::jsonb;

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
      voice_lines, frame_first, frame_last, use_end_frame, continue_prev, staging)
    values (p_episode, p_version, p_scene, (c->>'cut_no')::int, c->>'code', c->>'scene_heading',
      least(15, greatest(4, coalesce((c->>'seconds')::int, 10))), c->>'summary', c->>'dialogue', c->>'emotion',
      coalesce(c->'expression_beats', '[]'), c->>'prompt',
      coalesce(array(select jsonb_array_elements_text(c->'reference_names')), '{}'), c->>'camera', c->>'director_note',
      c->'review', coalesce(c->>'resolution', '480p'), (c->>'est_usd')::numeric,
      coalesce(c->'voice_lines', '[]'), coalesce(c->'frame_first', '{}'), coalesce(c->'frame_last', '{}'),
      coalesce((c->>'use_end_frame')::boolean, false), coalesce((c->>'continue_prev')::boolean, false),
      case when jsonb_typeof(c->'staging') = 'object' then c->'staging' else '{}'::jsonb end);
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

-- 에이전트가 씬 연출 계획을 읽는다(대본감수자가 연결·구도를 함께 감수)
create or replace function public.agent_scene_plans(p_token text, p_episode uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('scene_no', s.scene_no, 'heading', s.heading, 'plan', s.plan) order by s.scene_no)
                   from public.scene_packages s where s.episode_id = p_episode), '[]');
end $$;
revoke all on function public.agent_scene_plans(text, uuid) from public;
grant execute on function public.agent_scene_plans(text, uuid) to anon, authenticated;
