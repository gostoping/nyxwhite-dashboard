-- =====================================================================
-- NYX WHITE DIRECTOR · 시리즈 바이블 추가 스크립트
-- Supabase 대시보드 → SQL Editor → New query → 이 파일 전체를 붙여 넣고 Run
-- 여러 번 실행해도 안전합니다(이미 있는 것은 건너뜀).
-- =====================================================================

-- 1) 도우미: 대표인지 / 등록된 구성원인지 / 요청한 기기
create or replace function public.nyx_is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.app_members where user_id = auth.uid() and role = 'owner');
$$;

create or replace function public.nyx_is_member() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.app_members where user_id = auth.uid());
$$;

create or replace function public.nyx_actor() returns text
language sql stable security definer set search_path = public as $$
  select case when public.nyx_is_owner() then '대표'
              else coalesce((select label from public.app_members where user_id = auth.uid()), 'AI') end;
$$;

create or replace function public.nyx_header_device() returns uuid
language plpgsql stable security definer set search_path = public as $$
declare v uuid;
begin
  begin
    v := nullif(current_setting('request.headers', true)::json ->> 'x-device-id', '')::uuid;
  exception when others then
    return null;
  end;
  if v is null or not exists (select 1 from public.devices where id = v) then return null; end if;
  return v;
end $$;

-- 2) 표
create table if not exists public.bible_entries (
  id          uuid primary key default gen_random_uuid(),
  series_id   uuid not null references public.series(id) on delete cascade,
  category    text not null check (category in ('character','location','prop','style','voice')),
  name        text not null,
  summary     text,
  sort_order  int  not null default 0,
  created_at  timestamptz not null default now(),
  unique (series_id, category, name)
);

create table if not exists public.bible_versions (
  id            uuid primary key default gen_random_uuid(),
  entry_id      uuid not null references public.bible_entries(id) on delete cascade,
  version_no    int,                        -- 비워 두면 트리거가 다음 번호를 채움
  description   text not null,              -- 고정 묘사 문장 (감독이 그대로 사용)
  details       text,                       -- 화별 의상, 바뀌면 안 되는 특징, 금지 사항
  voice_id      text,                       -- 음성 도구의 고정 목소리 ID
  image_paths   text[] not null default '{}',  -- 저장소(bible) 안 기준 사진 경로
  voice_path    text,                       -- 저장소(bible) 안 목소리 샘플 경로
  change_reason text,
  status        text not null default 'proposed' check (status in ('proposed','active','retired','rejected')),
  proposed_by   text,
  decided_at    timestamptz,
  device_id     uuid references public.devices(id) on delete set null,
  created_at    timestamptz not null default now(),
  unique (entry_id, version_no)
);
create unique index if not exists bible_versions_one_active on public.bible_versions(entry_id) where status = 'active';

create table if not exists public.continuity_logs (
  id          uuid primary key default gen_random_uuid(),
  episode_id  uuid not null references public.episodes(id) on delete cascade,
  entry_id    uuid not null references public.bible_entries(id) on delete cascade,
  scene_no    int,
  state       text not null,               -- 예: 연습복, 머리 묶음, 왼손에 붕대
  source      text,
  device_id   uuid references public.devices(id) on delete set null,
  created_at  timestamptz not null default now()
);
create index if not exists continuity_logs_episode on public.continuity_logs(episode_id, created_at);

-- 3) 자동 채움: 버전 번호, 누가, 어느 기기
create or replace function public.nyx_bible_stamp() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if TG_TABLE_NAME = 'bible_versions' then
    if new.version_no is null then
      new.version_no := (select coalesce(max(version_no), 0) + 1 from public.bible_versions where entry_id = new.entry_id);
    end if;
    new.proposed_by := public.nyx_actor();
    if not public.nyx_is_owner() then new.status := 'proposed'; end if;   -- AI는 수정안만
  elsif TG_TABLE_NAME = 'continuity_logs' then
    new.source := public.nyx_actor();
  end if;
  new.device_id := coalesce(new.device_id, public.nyx_header_device());
  return new;
end $$;

drop trigger if exists nyx_bible_versions_stamp on public.bible_versions;
create trigger nyx_bible_versions_stamp before insert on public.bible_versions
  for each row execute function public.nyx_bible_stamp();
drop trigger if exists nyx_continuity_stamp on public.continuity_logs;
create trigger nyx_continuity_stamp before insert on public.continuity_logs
  for each row execute function public.nyx_bible_stamp();

-- 4) 활동 기록 + 수정안 알림 (기록 표 구조가 달라도 본 작업은 막지 않음)
create or replace function public.nyx_bible_log() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_sum text; v_name text; v_id uuid;
begin
  if TG_TABLE_NAME = 'bible_entries' then
    v_id := new.id; v_sum := '바이블 항목 추가: ' || new.name;
  elsif TG_TABLE_NAME = 'bible_versions' then
    select name into v_name from public.bible_entries where id = new.entry_id;
    v_id := new.id;
    if TG_OP = 'INSERT' then
      v_sum := '바이블 ' || v_name || ' v' || new.version_no || ' 작성 (' || new.status || ')';
    elsif new.status is distinct from old.status then
      v_sum := '바이블 ' || v_name || ' v' || new.version_no || ' ' || new.status;
    else
      return new;
    end if;
  elsif TG_TABLE_NAME = 'continuity_logs' then
    select name into v_name from public.bible_entries where id = new.entry_id;
    v_id := new.id; v_sum := '연속성 기록: ' || v_name || ' — ' || left(new.state, 60);
  end if;

  begin
    insert into public.activity_log (actor, summary, entity_id, device_id)
    values (public.nyx_actor(), v_sum, v_id, public.nyx_header_device());
  exception when others then null;
  end;

  -- AI가 수정안을 올리면 대표에게 알림
  if TG_TABLE_NAME = 'bible_versions' and TG_OP = 'INSERT' and not public.nyx_is_owner() then
    begin
      insert into public.notifications (title, body, level, link_tab)
      values ('바이블 수정안', v_name || ' v' || new.version_no || ' 검토가 필요합니다', 'warning', 'bible');
    exception when others then null;
    end;
  end if;
  return new;
end $$;

drop trigger if exists nyx_bible_entries_log on public.bible_entries;
create trigger nyx_bible_entries_log after insert on public.bible_entries
  for each row execute function public.nyx_bible_log();
drop trigger if exists nyx_bible_versions_log on public.bible_versions;
create trigger nyx_bible_versions_log after insert or update on public.bible_versions
  for each row execute function public.nyx_bible_log();
drop trigger if exists nyx_continuity_log on public.continuity_logs;
create trigger nyx_continuity_log after insert on public.continuity_logs
  for each row execute function public.nyx_bible_log();

-- 5) 적용은 대표만
create or replace function public.activate_bible_version(p_id uuid) returns void
language plpgsql security definer set search_path = public as $$
declare v_entry uuid;
begin
  if not public.nyx_is_owner() then
    raise exception 'row-level security: 바이블 적용은 대표만 할 수 있습니다';
  end if;
  select entry_id into v_entry from public.bible_versions where id = p_id;
  if v_entry is null then raise exception '버전을 찾을 수 없습니다'; end if;
  update public.bible_versions set status = 'retired' where entry_id = v_entry and status = 'active' and id <> p_id;
  update public.bible_versions set status = 'active', decided_at = now() where id = p_id;
end $$;
revoke all on function public.activate_bible_version(uuid) from public, anon;
grant execute on function public.activate_bible_version(uuid) to authenticated;

-- 6) 권한 규칙: 구성원은 읽기, AI는 수정안·연속성 기록만 쓰기, 나머지는 대표
alter table public.bible_entries   enable row level security;
alter table public.bible_versions  enable row level security;
alter table public.continuity_logs enable row level security;

drop policy if exists nyx_be_read  on public.bible_entries;
drop policy if exists nyx_be_owner on public.bible_entries;
create policy nyx_be_read  on public.bible_entries for select to authenticated using (public.nyx_is_member());
create policy nyx_be_owner on public.bible_entries for all    to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());

drop policy if exists nyx_bv_read    on public.bible_versions;
drop policy if exists nyx_bv_propose on public.bible_versions;
drop policy if exists nyx_bv_owner   on public.bible_versions;
create policy nyx_bv_read    on public.bible_versions for select to authenticated using (public.nyx_is_member());
create policy nyx_bv_propose on public.bible_versions for insert to authenticated with check (public.nyx_is_member() and status = 'proposed');
create policy nyx_bv_owner   on public.bible_versions for all    to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());

drop policy if exists nyx_cl_read   on public.continuity_logs;
drop policy if exists nyx_cl_insert on public.continuity_logs;
drop policy if exists nyx_cl_owner  on public.continuity_logs;
create policy nyx_cl_read   on public.continuity_logs for select to authenticated using (public.nyx_is_member());
create policy nyx_cl_insert on public.continuity_logs for insert to authenticated with check (public.nyx_is_member());
create policy nyx_cl_owner  on public.continuity_logs for all    to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());

-- 7) 기준 사진·목소리 저장소 (비공개)
insert into storage.buckets (id, name, public) values ('bible', 'bible', false) on conflict (id) do nothing;

drop policy if exists "nyx bible read"   on storage.objects;
drop policy if exists "nyx bible upload" on storage.objects;
drop policy if exists "nyx bible delete" on storage.objects;
create policy "nyx bible read"   on storage.objects for select to authenticated using (bucket_id = 'bible' and public.nyx_is_member());
create policy "nyx bible upload" on storage.objects for insert to authenticated with check (bucket_id = 'bible' and public.nyx_is_member());
create policy "nyx bible delete" on storage.objects for delete to authenticated using (bucket_id = 'bible' and public.nyx_is_owner());

-- 로그인 사용자 접근 허용 (RLS가 다시 걸러냄)
grant select, insert, update, delete on public.bible_entries, public.bible_versions, public.continuity_logs to authenticated;

-- 끝. 대시보드를 새로고침하면 "바이블" 탭을 쓸 수 있습니다.
