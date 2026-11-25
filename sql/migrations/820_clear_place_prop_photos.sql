-- 대표 결정(2026-11-25): 장소·소품·배경 사진은 너무 제한적이라 기존에 만든 것을 뺀다(협찬은 그대로).
-- 사진 파일은 저장소에 그대로 두고, 바이블에서 쓰던 경로는 image_paths_removed에 옮겨 둔다(필요하면 되살릴 수 있게).
-- 이제 이 항목들은 사진 없이 글(묘사·영어 프롬프트)로만 영상에 들어간다.
alter table public.bible_versions add column if not exists image_paths_removed text[];

do $$
declare n int; d int; k int;
begin
  update public.bible_versions v set image_paths_removed = v.image_paths, image_paths = '{}'
    from public.bible_entries e
   where e.id = v.entry_id and e.category in ('location', 'prop', 'background') and coalesce(e.origin, '') <> 'sponsor'
     and cardinality(coalesce(v.image_paths, '{}')) > 0 and v.image_paths_removed is null;
  get diagnostics n = row_count;
  update public.photo_designs p set status = 'dismissed', updated_at = now()
    from public.bible_entries e
   where e.id = p.entry_id and e.category in ('location', 'prop', 'background') and coalesce(e.origin, '') <> 'sponsor'
     and p.status in ('queued', 'ready', 'training', 'set_ready', 'failed');
  get diagnostics d = row_count;
  update public.pipeline_tasks t set status = 'cancelled', error = '대표 결정: 장소·소품·배경 사진은 만들지 않음', finished_at = now()
   where t.status in ('queued', 'running') and t.kind in ('photo_design', 'photo_set', 'photo_describe')
     and exists (select 1 from public.bible_entries e where e.id::text = coalesce(t.payload->>'entry_id', '')
                 and e.category in ('location', 'prop', 'background') and coalesce(e.origin, '') <> 'sponsor');
  get diagnostics k = row_count;
  raise notice '장소·소품·배경 사진 뺌: 버전 %개, 사진 후보 %개, 대기 작업 %개', n, d, k;
end $$;
