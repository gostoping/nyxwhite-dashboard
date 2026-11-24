-- 고쳐쓰기 요청이 예전 에이전트에서 '반영 완료'로만 끝나고 묘사에 안 들어간 것: 새 방식(코디네이터가 요청대로 묘사·영어 프롬프트 고침)으로 다시 맡긴다.
-- 에이전트가 새 버전으로 바뀔 시간을 두고 8분 뒤부터.
insert into public.pipeline_tasks (kind, series_id, payload, not_before)
select 'bible_prompt', be.series_id, jsonb_build_object('version_id', bv.id, 'entry_id', be.id, 'apply_reason', true, 'rerun', true), now() + interval '8 minutes'
  from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id
 where bv.status = 'active' and bv.proposed_by = '대표' and coalesce(trim(bv.change_reason), '') <> ''
   and bv.change_reason !~ '(자동|사진을 보고|가져옴|목소리|얼굴 고정|Soul|코디네이터가 묘사·프롬프트에 반영)'
   and bv.created_at > now() - interval '30 days'
   and be.category in ('character', 'location', 'background', 'prop', 'style', 'music')
   and exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.payload->>'version_id' = bv.id::text and t.payload ? 'apply_reason')
   and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running') and t.payload->>'version_id' = bv.id::text);
