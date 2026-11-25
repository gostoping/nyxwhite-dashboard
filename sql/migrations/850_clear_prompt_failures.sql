-- 대표 요청(2026-11-25): 지금 남아 있는 컷 프롬프트 수정 실패(감독 답 잘림 등)를 지우고 컷을 다시 쓸 수 있게 한다.
do $$
declare n int; g int; k int;
begin
  -- 실패한 컷 단위 감독 작업(프롬프트 수정·한글판·범위 조정) → 취소(목록에서 빠짐)
  update public.pipeline_tasks set status = 'cancelled', error = coalesce(error, '') || ' (대표 요청으로 지움)', finished_at = coalesce(finished_at, now())
   where kind = 'director' and status = 'failed' and coalesce(payload->>'cut_id', '') <> '';
  get diagnostics n = row_count;
  -- 그 수정을 기다리던 생성(감독 반영 뒤 생성)도 취소
  update public.pipeline_tasks t set status = 'cancelled', error = '감독 수정이 실패해 취소(대표 요청으로 정리)', finished_at = now()
   where t.kind = 'generate' and t.status = 'queued' and (t.payload->>'after_director')::boolean is true
     and not exists (select 1 from public.pipeline_tasks d where d.kind = 'director' and d.cut_id = t.cut_id and d.status in ('queued', 'running'));
  get diagnostics g = row_count;
  -- 생성 승인 상태로 멈춘 컷은 설계됨으로(다시 반영·생성할 수 있게). 남은 수정 요청은 지워 새 지시만 반영되게
  update public.production_cuts c set status = 'designed', fix_request = null, updated_at = now()
   where c.status = 'approved'
     and not exists (select 1 from public.pipeline_tasks t where t.cut_id = c.id and t.status in ('queued', 'running'));
  get diagnostics k = row_count;
  update public.production_cuts c set fix_request = null, updated_at = now()
   where c.fix_request is not null and c.status in ('designed', 'failed', 'passed', 'hold')
     and not exists (select 1 from public.pipeline_tasks t where t.cut_id = c.id and t.kind = 'director' and t.status in ('queued', 'running'))
     and exists (select 1 from public.pipeline_tasks t where t.cut_id = c.id and t.kind = 'director' and t.status = 'cancelled' and t.error like '%대표 요청으로 지움%');
  raise notice '프롬프트 수정 실패 정리: 감독 작업 %개, 기다리던 생성 %개, 승인 대기에서 되돌린 컷 %개', n, g, k;
end $$;
