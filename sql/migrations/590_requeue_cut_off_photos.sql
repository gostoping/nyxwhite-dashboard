-- 580에서 '맡은 작업이 끊겨 멈춤'으로 정리한 사진 요청(대표가 이미 요청한 것)을 새 에이전트로 다시 맡긴다.
-- 고른 사진이 있으면 얼굴 학습(photo_set)부터, 없으면 후보 만들기(photo_design)부터. 에이전트 업데이트를 기다려 2분 뒤부터.
with d as (
  update public.photo_designs set status = case when coalesce(jsonb_array_length(chosen), 0) > 0 then 'training' else 'queued' end, error = null
   where status = 'failed' and error like '맡은 작업이 끊겨 멈춰 있었습니다%'
  returning id, status
)
insert into public.pipeline_tasks (kind, payload, not_before)
select case when d.status = 'training' then 'photo_set' else 'photo_design' end, jsonb_build_object('design_id', d.id), now() + interval '2 minutes' from d;

-- 취소·실패로 끝났던 사진 작업 때문에 '실패'로 바뀐 것 중 최근 3시간 것도 한 번 다시(같은 요청 중복 없이)
with d as (
  update public.photo_designs pd set status = case when coalesce(jsonb_array_length(pd.chosen), 0) > 0 then 'training' else 'queued' end, error = null
   where pd.status = 'failed' and pd.created_at > now() - interval '3 hours'
     and (pd.error like '작업이 취소됐습니다%' or pd.error like '%시간 초과%' or pd.error like '%지났습니다%' or pd.error like '%놓쳤습니다%' or pd.error like '%응답이 없습니다%')
     and not exists (select 1 from public.pipeline_tasks t where t.kind in ('photo_design', 'photo_set') and t.status in ('queued', 'running') and t.payload->>'design_id' = pd.id::text)
  returning pd.id, pd.status
)
insert into public.pipeline_tasks (kind, payload, not_before)
select case when d.status = 'training' then 'photo_set' else 'photo_design' end, jsonb_build_object('design_id', d.id), now() + interval '2 minutes' from d;
