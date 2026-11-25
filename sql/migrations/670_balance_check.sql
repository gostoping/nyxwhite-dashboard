-- 비용 탭: 서비스별 남은 잔액을 에이전트가 API로 받아 온다(무료 조회만)
alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe','bible_place','bible_prompt','api_check','bible_suggest','upscale',
                  'coord_review','cut_review','balance_check'));
