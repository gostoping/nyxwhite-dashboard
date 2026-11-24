-- 사진 만들기는 그 항목의 영어 프롬프트 반영(작가실·코디네이터 bible_prompt)만 기다린다.
-- 코디네이터 감수·사진 확인까지 기다리게 하니 감수가 밀리면 Higgsfield 사진이 몇 분씩 멈춰 있었다.
create or replace function public.agent_get_photo_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'series_id', e.series_id, 'cur_version_id', bv.id,
                   'last_review', (select pd.review from public.photo_designs pd where pd.entry_id = e.id and pd.id <> d.id and pd.review is not null order by pd.created_at desc limit 1), 'category', e.category, 'summary', e.summary, 'series_title', s.title,
                   'description', bv.description, 'details', bv.details, 'images', coalesce(bv.image_paths, '{}'), 'cur_soul_id', bv.soul_id, 'prompt_en', bv.prompt_en,
                   'prompt_pending', exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running')
                                              and t.payload->>'entry_id' = e.id::text),
                   'generation', (select value from public.app_settings where key = 'generation'))
          from public.photo_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          left join public.bible_versions bv on bv.entry_id = e.id and bv.status = 'active'
          where d.id = p_id);
end $$;
