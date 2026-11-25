-- 제작 탭에서 대표가 컷이 맡을 대본 범위를 바꾸면 감독이 대사·길이까지 다시 쓴다
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
    camera = coalesce(p->>'camera', camera), dialogue = case when p ? 'dialogue' then p->>'dialogue' else dialogue end,
    seconds = case when p ? 'seconds' then least(15, greatest(4, (p->>'seconds')::int)) else seconds end, summary = coalesce(p->>'summary', summary), emotion = coalesce(p->>'emotion', emotion),
    staging = case when jsonb_typeof(p->'staging') = 'object' then p->'staging' else staging end,
    reviews = case when p ? 'reviews' then coalesce(reviews, '{}'::jsonb) || (p->'reviews') else reviews end
  where id = p_id;
end $$;
