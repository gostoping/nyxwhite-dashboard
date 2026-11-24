-- 바이블 '바꾸는 이유'에만 적고 묘사는 그대로 둔 대표 수정본: 작가실이 그 요청을 묘사·영어 프롬프트에 반영하도록 한 번 맡긴다
-- (그동안은 이유가 기록으로만 남고 이전 영어 프롬프트를 그대로 이어받았음). 에이전트 업데이트를 기다려 15분 뒤부터.
insert into public.pipeline_tasks (kind, series_id, payload, not_before)
select 'bible_prompt', be.series_id, jsonb_build_object('version_id', bv.id, 'entry_id', be.id, 'apply_reason', true), now() + interval '15 minutes'
  from public.bible_versions bv
  join public.bible_entries be on be.id = bv.entry_id
 where bv.status = 'active' and bv.proposed_by = '대표'
   and coalesce(trim(bv.change_reason), '') <> ''
   and bv.change_reason !~ '(자동|사진을 보고|작가실|가져옴|목소리|얼굴 고정|Soul)'
   and bv.created_at > now() - interval '30 days'
   and be.category in ('character', 'location', 'background', 'prop', 'style', 'music')
   and exists (select 1 from public.bible_versions pv where pv.entry_id = bv.entry_id and pv.version_no < bv.version_no
                and pv.description is not distinct from bv.description and pv.details is not distinct from bv.details)
   and not exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running')
                    and t.payload->>'version_id' = bv.id::text);
