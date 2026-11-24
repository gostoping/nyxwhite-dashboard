-- 영상 모델 선택(Higgsfield 안: Seedance 2.5 / Kling 4.0)과 대사 도구(Higgsfield = 영상 모델이 함께 만든 소리 / ElevenLabs = 바이블 인물 목소리로 바꿔 입힘)
update public.app_settings
   set value = value || jsonb_build_object('video_model', coalesce(value->>'video_model', 'seedance-2.5'),
                                           'dialogue_tool', case when value->>'dialogue_tool' = 'elevenlabs_confirmed' then 'elevenlabs' else 'higgsfield' end)
 where key = 'generation';
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('higgsfield', 'kling-4.0-i2v-720p', 'per_second', 0.110, 'Kling 4.0 사진→영상 720p', 'Higgsfield Explore 표시가 2026-11 (from $0.110) — 확인 후 수정'),
  ('higgsfield', 'kling-4.0-i2v-1080p', 'per_second', 0.157, 'Kling 4.0 사진→영상 1080p', 'Higgsfield Explore 정가 2026-11 ($0.157) — 확인 후 수정'),
  ('higgsfield', 'kling-4.0-t2v-720p', 'per_second', 0.110, 'Kling 4.0 글→영상 720p', 'Higgsfield Explore 표시가 2026-11 — 확인 후 수정'),
  ('higgsfield', 'kling-4.0-t2v-1080p', 'per_second', 0.157, 'Kling 4.0 글→영상 1080p', 'Higgsfield Explore 정가 2026-11 — 확인 후 수정')
on conflict (provider, model, unit) do nothing;
