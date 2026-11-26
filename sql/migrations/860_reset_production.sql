-- 대표 결정(2026-11-26): 두 번째 제작을 처음부터 다시 한다.
-- 지우는 것: 모든 화의 제작(컷·테이크·씬 계획·제작/편집 작업)·편집(타임라인·렌더)·연출 메모 전부,
--            바이블에서 인물과 협찬을 뺀 항목(장소·배경·소품·스타일·목소리·용어·배경음악)과 그 버전·사진 후보·감수 기록.
-- 남기는 것: 대본(원고·정리본·확정본), 바이블 인물(사진·목소리 포함), 협찬 항목, 감수 교훈, 비용 기록, 저장소 파일(영상·사진 파일은 지우지 않음).
-- 지우기 전에 모두 nyx_backup 스키마에 복사해 둔다(필요하면 되살릴 수 있게).
create schema if not exists nyx_backup;
revoke all on schema nyx_backup from public;
do $$
declare
  kinds text[] := array['director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast','assemble','edit_command','upscale','cut_review'];
  del_entries uuid[];
  n_cut int; n_take int; n_task int; n_edit int; n_render int; n_note int; n_ent int; n_sp int; n_cont int; n_ep int;
  v text;
begin
  select coalesce(array_agg(id), '{}') into del_entries from public.bible_entries
   where category <> 'character' and coalesce(origin, '') <> 'sponsor';

  -- 백업(같은 날 두 번 돌아도 덮어쓰지 않게 if not exists)
  create table if not exists nyx_backup.production_cuts_20261126 as select * from public.production_cuts;
  create table if not exists nyx_backup.production_takes_20261126 as select * from public.production_takes;
  create table if not exists nyx_backup.scene_packages_20261126 as select * from public.scene_packages;
  create table if not exists nyx_backup.edit_projects_20261126 as select * from public.edit_projects;
  create table if not exists nyx_backup.edit_renders_20261126 as select * from public.edit_renders;
  create table if not exists nyx_backup.episode_notes_20261126 as select * from public.episode_notes;
  create table if not exists nyx_backup.continuity_logs_20261126 as select * from public.continuity_logs;
  execute format('create table if not exists nyx_backup.pipeline_tasks_20261126 as select * from public.pipeline_tasks where kind = any(%L::text[]) or cut_id is not null or coalesce(payload->>''entry_id'', '''') = any(%L::text[])', kinds, del_entries::text[]);
  execute format('create table if not exists nyx_backup.bible_entries_20261126 as select * from public.bible_entries where id = any(%L::uuid[])', del_entries);
  execute format('create table if not exists nyx_backup.bible_versions_20261126 as select * from public.bible_versions where entry_id = any(%L::uuid[])', del_entries);
  execute format('create table if not exists nyx_backup.photo_designs_20261126 as select * from public.photo_designs where entry_id = any(%L::uuid[])', del_entries);
  execute format('create table if not exists nyx_backup.bible_reviews_20261126 as select * from public.bible_reviews where entry_id = any(%L::uuid[])', del_entries);

  -- 제작·편집 작업
  delete from public.pipeline_tasks where kind = any(kinds) or cut_id is not null or take_id is not null
     or coalesce(payload->>'entry_id', '') = any(del_entries::text[]);
  get diagnostics n_task = row_count;
  delete from public.edit_renders; get diagnostics n_render = row_count;
  delete from public.edit_projects; get diagnostics n_edit = row_count;
  select count(*) into n_take from public.production_takes;
  delete from public.production_cuts; get diagnostics n_cut = row_count;   -- 테이크는 함께 지워짐
  delete from public.scene_packages; get diagnostics n_sp = row_count;
  delete from public.continuity_logs; get diagnostics n_cont = row_count;
  delete from public.episode_notes; get diagnostics n_note = row_count;

  -- 바이블: 인물·협찬 말고 전부
  update public.bible_entries set same_as = null where same_as = any(del_entries) and not (id = any(del_entries));
  delete from public.bible_reviews where entry_id = any(del_entries) or version_id in (select id from public.bible_versions where entry_id = any(del_entries));
  delete from public.bible_entries where id = any(del_entries); get diagnostics n_ent = row_count;

  -- 화 상태: 제작·편집 단계였던 화는 '준비'(확정 대본 있음) 또는 '집필'로
  update public.episodes e set status = case when exists (select 1 from public.script_versions s where s.episode_id = e.id and s.status = 'confirmed') then 'ready' else 'writing' end
   where e.status in ('in_production', 'review', 'approved', 'editing', 'done');
  get diagnostics n_ep = row_count;

  raise notice '처음부터 다시: 컷 %개(테이크 %개) · 씬 계획 %개 · 작업 %개 · 편집 %개(렌더 %개) · 연출 메모 %개 · 연속성 기록 %개 · 바이블 항목 %개 지움 · 화 %개를 준비 상태로', n_cut, n_take, n_sp, n_task, n_edit, n_render, n_note, n_cont, n_ent, n_ep;
  for v in select format('%s %s v%s · 마지막 %s', coalesce(d.name, a.device_id::text, '?'), coalesce(a.platform, ''), coalesce(a.version, '?'),
                         coalesce(to_char(a.last_seen_at at time zone 'Asia/Seoul', 'MM-DD HH24:MI'), '-'))
           from public.agent_status a left join public.devices d on d.id = a.device_id order by a.last_seen_at desc nulls last loop
    raise notice '에이전트: %', v;
  end loop;
end $$;
