-- 바이블 '배경음악' 분류: 화별로 원하는 배경음악 톤(적용 화는 appears의 ep). 편집실에서 이 화 톤을 보고 Suno용 프롬프트를 복사한다.
-- '협찬 씬'은 분류가 아니라 출처(origin = 'sponsor')로 구분한다: 협찬 장소·소품 추가로 넣은 항목.
alter table public.bible_entries drop constraint if exists bible_entries_category_check;
alter table public.bible_entries add constraint bible_entries_category_check
  check (category in ('character','location','background','prop','style','voice','glossary','music'));

-- 배경음악 톤도 한글로 적으면 영어 프롬프트(Suno용)로 옮겨 둔다
create or replace function public.nyx_bible_prompt_queue() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'active' and new.prompt_en is null
     and (coalesce(new.description, '') || coalesce(new.details, '')) ~ '[가-힣]'
     and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running')
                     and t.payload->>'version_id' = new.id::text) then
    insert into public.pipeline_tasks (kind, series_id, payload)
    select 'bible_prompt', be.series_id, jsonb_build_object('version_id', new.id, 'entry_id', be.id)
      from public.bible_entries be where be.id = new.entry_id and be.category in ('character', 'location', 'background', 'prop', 'style', 'music');
  end if;
  return null;
end $$;
