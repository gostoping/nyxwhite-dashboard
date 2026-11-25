-- 상호 피드백: 감수자(편집실 흐름 감수 등)가 남긴 교훈을 누구에게 줄지(감독·작가실·대본감수자·코디네이터) 기록한다.
-- 비어 있으면 예전처럼 감독용. 같은 교훈이 다시 나오면 받는 사람을 합친다.
alter table public.director_lessons add column if not exists targets text[] not null default '{}';

create or replace function public.agent_add_lesson(p_token text, p_lesson jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid; v_hits int; v_status text; v_series uuid := (p_lesson->>'series_id')::uuid;
  v_targets text[] := coalesce(array(select jsonb_array_elements_text(coalesce(p_lesson->'targets', '[]'::jsonb))), '{}');
  v_cat text := case when p_lesson->>'category' in ('face','costume','location','prop','continuity','expression','emotion_tone','dialogue','camera','motion','prompt','localization','other')
                     then p_lesson->>'category' else 'other' end;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  select id into v_id from public.director_lessons
   where fingerprint = p_lesson->>'fingerprint' and series_id is not distinct from v_series;
  if v_id is null then
    insert into public.director_lessons (series_id, fingerprint, category, problem, rule, bad_example, good_example, related, sources, cut_refs, status, targets)
    values (v_series, p_lesson->>'fingerprint', v_cat, p_lesson->>'problem', p_lesson->>'rule',
            p_lesson->>'bad_example', p_lesson->>'good_example',
            coalesce(array(select jsonb_array_elements_text(p_lesson->'related')), '{}'),
            coalesce(array(select jsonb_array_elements_text(p_lesson->'sources')), '{}'),
            coalesce(array(select jsonb_array_elements_text(p_lesson->'cut_refs')), '{}'),
            case when p_lesson->>'severity' = '치명' then 'active' else 'candidate' end, v_targets)
    returning id, hit_count, status into v_id, v_hits, v_status;
  else
    update public.director_lessons set
      hit_count = hit_count + 1, last_seen_at = now(), updated_at = now(),
      rule = coalesce(nullif(p_lesson->>'rule', ''), rule),
      good_example = coalesce(nullif(p_lesson->>'good_example', ''), good_example),
      sources = (select array(select distinct unnest(sources || coalesce(array(select jsonb_array_elements_text(p_lesson->'sources')), '{}')))),
      cut_refs = (select array(select distinct unnest(cut_refs || coalesce(array(select jsonb_array_elements_text(p_lesson->'cut_refs')), '{}')))),
      targets = (select array(select distinct unnest(targets || v_targets))),
      status = case when status = 'candidate' and hit_count + 1 >= 2 then 'active' else status end
    where id = v_id returning hit_count, status into v_hits, v_status;
  end if;
  perform public.nyx_log('평가자', '교훈 ' || case when v_hits = 1 then '추가' else '반복(' || v_hits || '회)' end
    || case when cardinality(v_targets) > 0 then ' → ' || array_to_string(v_targets, '·') else '' end || ': ' || left(p_lesson->>'rule', 60), v_id, v_dev);
  return jsonb_build_object('id', v_id, 'hit_count', v_hits, 'status', v_status);
end $$;
revoke all on function public.agent_add_lesson(text, jsonb) from public;
grant execute on function public.agent_add_lesson(text, jsonb) to anon, authenticated;
