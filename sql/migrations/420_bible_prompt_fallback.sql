-- 바이블 영어 프롬프트: 누가 쓴 묘사인지(대표/AI)와 시리즈를 함께 넘긴다.
-- AI(대본 추출)가 쓴 묘사로 실패하면 에이전트가 대본에서 그 항목이 나오는 부분을 찾아 채우고,
-- 대표가 쓴 묘사면 무엇이 빠졌는지 한글로 알려 준다.
create or replace function public.agent_bible_photo_info(p_token text, p_version uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select jsonb_build_object('version_id', bv.id, 'status', bv.status, 'description', bv.description, 'details', bv.details,
                 'image_paths', to_jsonb(coalesce(bv.image_paths, '{}'::text[])),
                 'proposed_by', bv.proposed_by, 'change_reason', bv.change_reason,
                 'entry_id', be.id, 'name', be.name, 'category', be.category, 'summary', be.summary, 'appears', be.appears,
                 'origin', be.origin, 'series_id', be.series_id, 'series', s.title)
            from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id join public.series s on s.id = be.series_id
           where bv.id = p_version);
end $$;
revoke all on function public.agent_bible_photo_info(text, uuid) from public;
grant execute on function public.agent_bible_photo_info(text, uuid) to anon, authenticated;

-- 지금까지 실패해 쌓인 영어 프롬프트 작업은 새 방식(재시도 → 대본에서 채우기)으로 한 번 다시 돌린다.
-- 에이전트가 새 버전으로 바뀔 시간을 두고 20분 뒤부터.
with f as (
  select distinct on (t.payload->>'version_id') t.id, t.series_id, t.payload
    from public.pipeline_tasks t join public.bible_versions bv on bv.id::text = t.payload->>'version_id'
   where t.kind = 'bible_prompt' and t.status = 'failed' and bv.status = 'active' and bv.prompt_en is null
   order by t.payload->>'version_id', t.created_at desc
), ins as (
  insert into public.pipeline_tasks (kind, series_id, payload, not_before)
  select 'bible_prompt', f.series_id, f.payload || '{"rerun": true}'::jsonb, now() + interval '20 minutes' from f
  returning 1
)
update public.pipeline_tasks set status = 'cancelled'
 where kind = 'bible_prompt' and status = 'failed';
