-- 대표 결정(2026-11-24): 영상 Seedance 2.5 고정, 대사 목소리·배경음악은 ElevenLabs. 설정 → 제작 '모델·도구' 표에서 언제든 바꿀 수 있다.
update public.app_settings
   set value = value || jsonb_build_object('video_model', 'seedance-2.5', 'dialogue_tool', 'elevenlabs', 'music_tool', 'elevenlabs')
 where key = 'generation';
