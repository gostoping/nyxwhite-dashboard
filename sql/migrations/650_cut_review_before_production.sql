-- 작가실(장소별 씬 정리 → 확정 → 컷 설계)이 가져온 컷을, 제작(영상 생성) 전에 감정감수자·영상감수자가 감수한다.
-- 결과는 컷마다 reviews.emotion / reviews.video 에 남고, 고칠 수 있는 것은 감수자가 프롬프트를 직접 고친다.
alter table public.production_cuts add column if not exists reviews jsonb not null default '{}';

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
    reviews = case when p ? 'reviews' then coalesce(reviews, '{}'::jsonb) || (p->'reviews') else reviews end
  where id = p_id;
end $$;

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe','bible_place','bible_prompt','api_check','bible_suggest','upscale',
                  'coord_review','cut_review'));

