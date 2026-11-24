-- 대표 결정(2026-11-24): 나노바나나 프로는 Higgsfield API에서 "model_disabled"(꺼져 있음)라 쓸 수 없다.
-- 사진 기본 모델을 Seedream 5.0 Pro로: 인물 사진(후보·기준 사진)과 장면·장소·소품 사진 모두.
update public.app_settings
   set value = coalesce(value, '{}'::jsonb) || jsonb_build_object('char_photo_model', 'seedream', 'image_model', 'seedream')
 where key = 'generation';
insert into public.app_settings (key, value) select 'generation', jsonb_build_object('char_photo_model', 'seedream', 'image_model', 'seedream')
 where not exists (select 1 from public.app_settings where key = 'generation');
-- 아직 시작 안 한 사진 요청 중 나노바나나 프로·Grok으로 잡힌 것은 Seedream 5.0 Pro로
update public.photo_designs set model = 'seedream'
 where status = 'queued' and coalesce(model, '') in ('nano', 'grok', '');
