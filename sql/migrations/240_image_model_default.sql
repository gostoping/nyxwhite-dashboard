-- 장면 사진 기본 모델: Higgsfield Grok Imagine 2.0 (참고 사진 image_urls로 편집·일관성 유지, 공개 문서 확인 2026-11-23)
update public.app_settings
   set value = value || jsonb_build_object('image_model', 'xai/grok-imagine-image-2.0')
 where key = 'generation' and coalesce(value->>'image_model', '') = '';
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('higgsfield', 'xai/grok-imagine-image-2.0', 'per_image', 0.04, 'Grok Imagine 2.0 장면 사진(1k)', 'Higgsfield Explore 표시가 2026-11 (from $0.040)')
on conflict (provider, model, unit) do nothing;
