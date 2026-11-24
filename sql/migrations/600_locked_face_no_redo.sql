-- 얼굴 고정(Soul ID)된 인물은 고쳐쓰기로 새로 요청하지 않는 한 사진 후보를 다시 만들지 않는다.
-- 고정보다 먼저 만든 예전 사진 요청(590에서 다시 맡긴 것 포함)은 '접음'(dismissed)으로 치우고 작업도 취소한다.
alter table public.photo_designs drop constraint if exists photo_designs_status_check;
alter table public.photo_designs add constraint photo_designs_status_check
  check (status in ('queued', 'ready', 'training', 'set_ready', 'linked', 'failed', 'dismissed'));

with old as (
  select pd.id from public.photo_designs pd
    join public.bible_versions bv on bv.entry_id = pd.entry_id and bv.status = 'active'
   where bv.soul_id is not null and pd.status in ('queued', 'ready', 'training', 'set_ready', 'failed')
     and pd.created_at < coalesce(bv.decided_at, bv.created_at)
), d as (
  update public.photo_designs set status = 'dismissed' where id in (select id from old) returning id
)
update public.pipeline_tasks set status = 'cancelled', error = '얼굴이 이미 고정돼 예전 사진 요청을 치움'
 where kind in ('photo_design', 'photo_set') and status in ('queued') and payload->>'design_id' in (select id::text from d);
