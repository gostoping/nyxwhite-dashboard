-- 대표 결정: 지금부터 인물 사진(후보·기준 사진)은 나노바나나 프로로 통일. 얼굴 학습(Soul ID) 대신 고른 사진을 참고 사진으로 같은 얼굴을 유지.
update public.app_settings set value = coalesce(value, '{}'::jsonb) || jsonb_build_object('char_photo_model', 'nano') where key = 'generation';
insert into public.app_settings (key, value) select 'generation', jsonb_build_object('char_photo_model', 'nano')
 where not exists (select 1 from public.app_settings where key = 'generation');
-- 아직 시작 안 한 인물 사진 요청도 나노바나나 프로로
update public.photo_designs pd set model = 'nano'
  from public.bible_entries be
 where be.id = pd.entry_id and be.category = 'character' and pd.status in ('queued') and coalesce(pd.model, 'soul2') = 'soul2';
