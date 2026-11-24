-- 코디네이터가 사진을 보고 묘사(한국어)와 영어 프롬프트를 함께 쓴다: 이미 영어 프롬프트가 있으면 작가실 번역은 건너뛰게 프롬프트도 넘긴다
create or replace function public.agent_bible_photo_info(p_token text, p_version uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select jsonb_build_object('version_id', bv.id, 'status', bv.status, 'description', bv.description, 'details', bv.details,
                 'image_paths', to_jsonb(coalesce(bv.image_paths, '{}'::text[])),
                 'proposed_by', bv.proposed_by, 'prompt_en', bv.prompt_en, 'change_reason', bv.change_reason,
                 'entry_id', be.id, 'name', be.name, 'category', be.category, 'summary', be.summary, 'appears', be.appears,
                 'origin', be.origin, 'series_id', be.series_id, 'series', s.title)
            from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id join public.series s on s.id = be.series_id
           where bv.id = p_version);
end $$;
revoke all on function public.agent_bible_photo_info(text, uuid) from public;
grant execute on function public.agent_bible_photo_info(text, uuid) to anon, authenticated;
