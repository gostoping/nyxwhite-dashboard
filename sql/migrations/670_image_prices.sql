-- 사진 모델 단가 보강(2026-11-24): Seedream 5.0 Pro·Kling Image 4.0이 단가표에 없어 비용이 0으로 기록되던 문제.
-- Seedream 5.0 Pro: Higgsfield API 목록 $0.054/장. Kling Image 4.0: 공개가를 못 찾아 Seedream과 같은 값으로 두고 '확인 후 수정' 표시.
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('higgsfield', 'bytedance/seedream/v5.0/pro', 'per_image', 0.054, 'Seedream 5.0 Pro 사진', 'Higgsfield API 목록 2026-11 ($0.054/장)'),
  ('higgsfield', 'kling-image/v4.0', 'per_image', 0.054, 'Kling Image 4.0 사진', '공개가 미확인 — Seedream과 같은 값으로 임시, 콘솔에서 확인 후 수정')
on conflict (provider, model, unit) do update set usd = coalesce(public.price_book.usd, excluded.usd), source = coalesce(public.price_book.source, excluded.source);
