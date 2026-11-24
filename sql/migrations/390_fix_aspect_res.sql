-- 대표 결정(2026-11-24): 세로 9:16 · 처음 생성 720p 고정. 설정 → 제작 '모델·도구' 표에서 바꿀 수 있다(새로 설계하는 컷부터).
update public.app_settings set value = value || jsonb_build_object('aspect_ratio', '9:16', 'resolution', '720p') where key = 'generation';
