-- 바이블 영어 프롬프트가 실패한 같은 내용(묘사+세부 사항)은 다시 자동으로 넣지 않는다(알림 반복 방지).
-- 실패는 지시 탭 적용 대기함 '실패'에 원인과 함께 남고, 대표가 '다시 시도'를 누르거나 묘사를 고치면 다시 돈다.
create or replace function public.nyx_bible_prompt_queue() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_src text := md5(coalesce(new.description, '') || '|' || coalesce(new.details, ''));
begin
  if new.status = 'active' and new.prompt_en is null
     and (coalesce(new.description, '') || coalesce(new.details, '')) ~ '[가-힣]'
     and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.payload->>'version_id' = new.id::text
                     and (t.status in ('queued', 'running') or (t.status = 'failed' and t.payload->>'src' = v_src))) then
    insert into public.pipeline_tasks (kind, series_id, payload)
    select 'bible_prompt', be.series_id, jsonb_build_object('version_id', new.id, 'entry_id', be.id, 'src', v_src)
      from public.bible_entries be where be.id = new.entry_id and be.category in ('character', 'location', 'background', 'prop', 'style', 'music');
  end if;
  return null;
end $$;
