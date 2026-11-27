-- 대표 요청(2026-11-27): 모습 단계가 바뀌어도 같은 사람으로 알아볼 수 있게, 인물 사진 후보를 만들 때 같은 사람의 적용된 사진을 참고로 준다.
create or replace function public.agent_get_photo_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'series_id', e.series_id, 'cur_version_id', bv.id,
                   'last_review', (select pd.review from public.photo_designs pd where pd.entry_id = e.id and pd.id <> d.id and pd.review is not null order by pd.created_at desc limit 1), 'category', e.category, 'summary', e.summary, 'series_title', s.title,
                   'description', bv.description, 'details', bv.details, 'images', coalesce(bv.image_paths, '{}'), 'cur_soul_id', bv.soul_id, 'prompt_en', bv.prompt_en,
                   'prompt_pending', exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running')
                                              and t.payload->>'entry_id' = e.id::text),
                   'generation', (select value from public.app_settings where key = 'generation'),
                   -- 같은 사람(원래 인물 + 모습 단계·다른 모습)의 적용된 사진: 단계가 바뀌어도 같은 사람으로 보이게 참고 사진으로 쓴다
                   'stage', e.stage, 'change_note', e.change_note,
                   'kin', (select coalesce(jsonb_agg(jsonb_build_object('name', k.name, 'stage', k.stage, 'root', k.id = coalesce(e.same_as, e.id),
                                   'images', to_jsonb(kv.image_paths)) order by (k.id = coalesce(e.same_as, e.id)) desc, k.stage nulls first, k.created_at), '[]')
                             from public.bible_entries k join public.bible_versions kv on kv.entry_id = k.id and kv.status = 'active'
                            where e.category = 'character' and k.id <> e.id and coalesce(cardinality(kv.image_paths), 0) > 0
                              and (k.id = coalesce(e.same_as, e.id) or k.same_as = coalesce(e.same_as, e.id))))
          from public.photo_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          left join public.bible_versions bv on bv.entry_id = e.id and bv.status = 'active'
          where d.id = p_id);
end $$;
