-- 대표 결정: 작가실 정리 → 확정 → 컷 설계는 자동으로 제작실로 넘어온다. 예전에 남은 '감독 컷 설계 실패' 기록은 지운다(취소로 정리).
-- 이후 확인은 제작 탭 '대본 감수' → '승인 대기 컷 전체 생성'.
update public.pipeline_tasks t set status = 'cancelled',
       error = coalesce(error, '') || E'\n[정리] 대표 결정으로 지난 감독 컷 설계 실패 기록을 지웠습니다(제작 탭 대본 감수로 확인)'
 where t.kind = 'director' and t.status = 'failed';
