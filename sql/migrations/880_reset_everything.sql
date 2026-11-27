-- 대표 결정(2026-11-27): 대본부터 다시 넣어 처음부터 한다 → 작품 데이터 전부 지움.
-- 지우는 것: 모든 작품(시리즈)·화·대본(원고·정리본·확정본)·씬·바이블 전부(인물·협찬 포함)·사진 후보·목소리 설계·
--            제작(컷·테이크·씬 계획)·편집·연출 메모·연속성 기록·작업(pipeline_tasks)·알림·작품별 감수 교훈·광고 소재.
-- 남기는 것: 설정·가격표·비용 기록(작품 연결만 비워짐)·에이전트/기기·직원·공통 감수 교훈(작품 지정 없는 것)·활동 기록(지울 수 없는 기록이라 그대로, 작품 연결 외래키만 풂).
-- 저장소 파일(영상·사진)은 SQL로 지울 수 없어 대시보드 설정 탭 '저장소 파일 비우기'로 지운다.
-- 지우기 전에 nyx_backup 스키마에 표를 통째로 복사해 둔다(비밀값·토큰 표는 복사하지 않음).
create schema if not exists nyx_backup;
revoke all on schema nyx_backup from public;

-- 어떤 표의 행을 지우기 전에, 그 행을 '지우기 막음(no action/restrict)'으로 참조하는 자식 행을 먼저 지운다(재귀).
create or replace function pg_temp.nyx_purge(p_tbl regclass, p_cond text, p_depth int default 0, p_path regclass[] default '{}') returns void
language plpgsql as $f$
declare r record;
begin
  if p_depth > 10 then raise exception '참조가 너무 깊음: %', p_tbl; end if;
  for r in
    select c.conrelid::regclass as child,
           (select string_agg(quote_ident(a.attname), ',' order by k.ord) from unnest(c.conkey) with ordinality k(att, ord)
              join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.att) as ccols,
           (select string_agg(quote_ident(a.attname), ',' order by k.ord) from unnest(c.confkey) with ordinality k(att, ord)
              join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k.att) as pcols,
           c.confdeltype as t, c.conname as cname
      from pg_constraint c
     where c.contype = 'f' and c.confrelid = p_tbl and c.conrelid <> p_tbl
  loop
    if r.t in ('a', 'r', 'c') and r.child::text in ('activity_log', 'public.activity_log') then
      -- 활동 기록은 고치거나 지울 수 없는 기록이라 그대로 둔다: 지워지는 작품·화를 가리키는 연결(외래키)만 풀고, 기록 내용과 id는 남긴다
      execute format('alter table %s drop constraint %I', r.child, r.cname);
    elsif r.t in ('a', 'r', 'c') then
      if r.child = any(p_path) then
        -- 서로 참조하는 고리(예: 화 ↔ 현재 대본): 고리를 끊으려고 그 참조만 비운다
        execute format('update %s set (%s) = row(%s) where (%s) in (select %s from %s where %s)', r.child, r.ccols,
                       (select string_agg('null', ',') from regexp_split_to_table(r.ccols, ',')), r.ccols, r.pcols, p_tbl, p_cond);
      else
        perform pg_temp.nyx_purge(r.child, format('(%s) in (select %s from %s where %s)', r.ccols, r.pcols, p_tbl, p_cond), p_depth + 1, p_path || p_tbl);
      end if;
    end if;
  end loop;
  execute format('delete from %s where %s', p_tbl, p_cond);
end $f$;

do $$
declare
  t text; n bigint; msg text := '';
  skip text[] := array['app_secrets', 'app_secret_meta', 'agent_tokens', 'nyx_migrations'];
  wipe text[] := array['pipeline_tasks', 'notifications', 'storage_grants', 'edit_renders', 'edit_projects', 'production_takes', 'production_cuts',
                       'scene_packages', 'continuity_logs', 'episode_notes', 'story_proposals', 'photo_designs', 'voice_designs',
                       'bible_reviews', 'bible_versions', 'bible_entries', 'ad_runs', 'ad_contents'];
  before jsonb := '{}';
begin
  -- 백업: 공개 스키마의 모든 표(비밀값·토큰 제외)를 통째로
  for t in select c.relname from pg_class c where c.relnamespace = 'public'::regnamespace and c.relkind = 'r' and not (c.relname = any(skip)) loop
    execute format('create table if not exists nyx_backup.%I as select * from public.%I', t || '_20261127', t);
    execute format('select count(*) from public.%I', t) into n;
    before := before || jsonb_build_object(t, n);
  end loop;

  -- 작품 내용 표는 전부 비움(존재하는 표만)
  foreach t in array wipe loop
    if to_regclass('public.' || t) is not null then
      perform pg_temp.nyx_purge(('public.' || t)::regclass, 'true');
    end if;
  end loop;
  -- 작품(시리즈) 전부 → 화·대본·씬 등 연결된 것도 함께
  perform pg_temp.nyx_purge('public.series'::regclass, 'true');
  if to_regclass('public.episodes') is not null then perform pg_temp.nyx_purge('public.episodes'::regclass, 'true'); end if;

  -- 결과는 표별 줄어든 행 수만(내용은 남기지 않음 — 공개 기록)
  for t in select key from jsonb_each(before) order by key loop
    execute format('select count(*) from public.%I', t) into n;
    if (before->>t)::bigint <> n then msg := msg || format('%s %s→%s · ', t, before->>t, n); end if;
  end loop;
  raise notice '처음부터 다시(전부): %', coalesce(nullif(msg, ''), '지운 것 없음');
end $$;
