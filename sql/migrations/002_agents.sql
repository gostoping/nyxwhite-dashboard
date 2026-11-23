-- =====================================================================
-- NYX WHITE DIRECTOR · 에이전트 원격 통제 스크립트
-- Supabase 대시보드 → SQL Editor → New query → 이 파일 전체를 붙여 넣고 Run
-- 여러 번 실행해도 안전합니다.
--
-- 에이전트는 로그인 계정 없이 "에이전트 토큰"으로만 들어옵니다.
-- 토큰은 대시보드에서 대표가 발급하고, 창고에는 암호화된 값만 남습니다.
-- 토큰으로 할 수 있는 일은 아래 agent_* 함수 다섯 개뿐입니다.
-- =====================================================================

-- 공통 도우미 (바이블 스크립트와 같은 것, 다시 만들어도 안전)
create or replace function public.nyx_is_owner() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.app_members where user_id = auth.uid() and role = 'owner');
$$;
create or replace function public.nyx_is_member() returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.app_members where user_id = auth.uid());
$$;

-- 1) 표
create table if not exists public.agent_tokens (
  device_id   uuid primary key references public.devices(id) on delete cascade,
  token_hash  text not null,
  created_at  timestamptz not null default now()
);

create table if not exists public.agent_status (
  device_id    uuid primary key references public.devices(id) on delete cascade,
  platform     text,                 -- mac / windows
  version      text,                 -- 에이전트 프로그램 버전
  state        text,                 -- idle / busy / paused / error
  paused       boolean not null default false,   -- 대표가 일시정지
  current_job  text,
  last_error   text,
  info         jsonb,
  last_seen_at timestamptz
);

create table if not exists public.agent_commands (
  id          uuid primary key default gen_random_uuid(),
  device_id   uuid not null references public.devices(id) on delete cascade,
  command     text not null check (command in ('ping','restart','update')),
  status      text not null default 'queued' check (status in ('queued','sent','done','failed')),
  result      text,
  created_at  timestamptz not null default now(),
  done_at     timestamptz
);
create index if not exists agent_commands_open on public.agent_commands(device_id) where status in ('queued','sent');

-- 2) 내부 도우미 (밖에서 직접 부를 수 없음)
create or replace function public.nyx_agent_device(p_token text) returns uuid
language sql stable security definer set search_path = public as $$
  select device_id from public.agent_tokens
  where token_hash = encode(sha256(convert_to(coalesce(p_token, ''), 'UTF8')), 'hex');
$$;
revoke all on function public.nyx_agent_device(text) from public, anon, authenticated;

create or replace function public.nyx_new_token() returns text
language sql volatile as $$
  select 'nyx_' || replace(gen_random_uuid()::text, '-', '') || replace(gen_random_uuid()::text, '-', '');
$$;
revoke all on function public.nyx_new_token() from public, anon, authenticated;

create or replace function public.nyx_log(p_actor text, p_summary text, p_entity uuid, p_device uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  begin
    insert into public.activity_log (actor, summary, entity_id, device_id) values (p_actor, p_summary, p_entity, p_device);
  exception when others then null;   -- 기록 표 구조가 달라도 본 작업은 계속
  end;
end $$;
revoke all on function public.nyx_log(text, text, uuid, uuid) from public, anon, authenticated;

-- 3) 대표용: 에이전트 추가 / 토큰 다시 발급
create or replace function public.create_agent(p_name text, p_platform text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid; v_token text := public.nyx_new_token();
begin
  if not public.nyx_is_owner() then raise exception 'row-level security: 에이전트 추가는 대표만 할 수 있습니다'; end if;
  if coalesce(trim(p_name), '') = '' then raise exception '이름을 적어 주세요'; end if;
  begin
    insert into public.devices (name, kind, is_agent)
    values (trim(p_name), case when p_platform = 'mac' then 'mac_agent' else 'windows_agent' end, true)
    returning id into v_dev;
  exception when check_violation then
    insert into public.devices (name, kind, is_agent) values (trim(p_name), 'other', true) returning id into v_dev;
  end;
  insert into public.agent_tokens (device_id, token_hash) values (v_dev, encode(sha256(convert_to(v_token, 'UTF8')), 'hex'));
  insert into public.agent_status (device_id, platform, state) values (v_dev, p_platform, 'not_installed');
  perform public.nyx_log('대표', '에이전트 추가: ' || trim(p_name), v_dev, null);
  return jsonb_build_object('device_id', v_dev, 'token', v_token);
end $$;

create or replace function public.reissue_agent_token(p_device uuid) returns text
language plpgsql security definer set search_path = public as $$
declare v_token text := public.nyx_new_token();
begin
  if not public.nyx_is_owner() then raise exception 'row-level security: 대표만 할 수 있습니다'; end if;
  insert into public.agent_tokens (device_id, token_hash) values (p_device, encode(sha256(convert_to(v_token, 'UTF8')), 'hex'))
  on conflict (device_id) do update set token_hash = excluded.token_hash, created_at = now();
  perform public.nyx_log('대표', '에이전트 토큰 다시 발급 (이전 설치는 연결이 끊김)', p_device, null);
  return v_token;
end $$;

create or replace function public.remove_agent(p_device uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.nyx_is_owner() then raise exception 'row-level security: 대표만 할 수 있습니다'; end if;
  delete from public.agent_tokens where device_id = p_device;
  update public.agent_status set state = 'removed', paused = true where device_id = p_device;
  perform public.nyx_log('대표', '에이전트 연결 해제', p_device, null);
end $$;

revoke all on function public.create_agent(text, text) from public, anon;
revoke all on function public.reissue_agent_token(uuid) from public, anon;
revoke all on function public.remove_agent(uuid) from public, anon;
grant execute on function public.create_agent(text, text) to authenticated;
grant execute on function public.reissue_agent_token(uuid) to authenticated;
grant execute on function public.remove_agent(uuid) to authenticated;

-- 4) 에이전트용 (토큰으로만 동작)
-- 살아 있음 신호: 상태를 남기고, 일시정지 여부와 밀린 명령을 받아 감
create or replace function public.agent_heartbeat(
  p_token text, p_platform text default null, p_version text default null, p_state text default null,
  p_current_job text default null, p_last_error text default null, p_info jsonb default null
) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_paused boolean; v_name text; v_cmds jsonb; v_prev timestamptz;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  select last_seen_at into v_prev from public.agent_status where device_id = v_dev;
  insert into public.agent_status (device_id, platform, version, state, current_job, last_error, info, last_seen_at)
  values (v_dev, p_platform, p_version, p_state, p_current_job, p_last_error, p_info, now())
  on conflict (device_id) do update set
    platform = coalesce(excluded.platform, agent_status.platform), version = coalesce(excluded.version, agent_status.version),
    state = excluded.state, current_job = excluded.current_job, last_error = excluded.last_error,
    info = coalesce(excluded.info, agent_status.info), last_seen_at = now()
  returning paused into v_paused;
  select name into v_name from public.devices where id = v_dev;
  if v_prev is null or v_prev < now() - interval '10 minutes' then
    perform public.nyx_log(coalesce(v_name, '에이전트'), '에이전트 연결됨 (v' || coalesce(p_version, '?') || ')', v_dev, v_dev);
  end if;
  with c as (
    update public.agent_commands set status = 'sent'
    where device_id = v_dev and status = 'queued'
    returning id, command, created_at
  ) select coalesce(jsonb_agg(jsonb_build_object('id', id, 'command', command) order by created_at), '[]'::jsonb) into v_cmds from c;
  return jsonb_build_object('device_id', v_dev, 'name', v_name, 'paused', v_paused, 'commands', v_cmds);
end $$;

create or replace function public.agent_command_done(p_token text, p_id uuid, p_ok boolean, p_result text default null) returns void
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token);
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  update public.agent_commands set status = case when p_ok then 'done' else 'failed' end, result = left(p_result, 2000), done_at = now()
  where id = p_id and device_id = v_dev;
end $$;

-- 작업 대기열에서 한 건 가져가기 (같은 일을 두 에이전트가 가져가지 않음)
create or replace function public.agent_claim_job(p_token text, p_types text[]) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_job jsonb; v_id uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  if (select paused from public.agent_status where device_id = v_dev) then return null; end if;
  select id into v_id from public.jobs
  where status = 'queued' and type = any(p_types)
  order by created_at for update skip locked limit 1;
  if v_id is null then return null; end if;
  update public.jobs set status = 'running', attempts = coalesce(attempts, 0) + 1 where id = v_id;
  select to_jsonb(j) into v_job from public.jobs j where id = v_id;
  update public.agent_status set current_job = (v_job ->> 'type') where device_id = v_dev;
  perform public.nyx_log((select name from public.devices where id = v_dev), (v_job ->> 'type') || ' 작업 시작', v_id, v_dev);
  return v_job;
end $$;

create or replace function public.agent_finish_job(p_token text, p_job uuid, p_ok boolean, p_error text default null) returns void
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_type text;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  update public.jobs set status = case when p_ok then 'done' else 'failed' end, error = left(p_error, 2000), finished_at = now()
  where id = p_job and status = 'running' returning type into v_type;
  update public.agent_status set current_job = null where device_id = v_dev;
  perform public.nyx_log((select name from public.devices where id = v_dev),
    coalesce(v_type, '작업') || case when p_ok then ' done' else ' failed: ' || coalesce(left(p_error, 200), '') end, p_job, v_dev);
end $$;

revoke all on function public.agent_heartbeat(text, text, text, text, text, text, jsonb) from public;
revoke all on function public.agent_command_done(text, uuid, boolean, text) from public;
revoke all on function public.agent_claim_job(text, text[]) from public;
revoke all on function public.agent_finish_job(text, uuid, boolean, text) from public;
grant execute on function public.agent_heartbeat(text, text, text, text, text, text, jsonb) to anon, authenticated;
grant execute on function public.agent_command_done(text, uuid, boolean, text) to anon, authenticated;
grant execute on function public.agent_claim_job(text, text[]) to anon, authenticated;
grant execute on function public.agent_finish_job(text, uuid, boolean, text) to anon, authenticated;

-- 5) 권한 규칙: 토큰 표는 아무도 직접 못 봄 / 상태·명령은 구성원이 보고 대표가 조작
alter table public.agent_tokens   enable row level security;
alter table public.agent_status   enable row level security;
alter table public.agent_commands enable row level security;

drop policy if exists nyx_as_read  on public.agent_status;
drop policy if exists nyx_as_owner on public.agent_status;
create policy nyx_as_read  on public.agent_status for select to authenticated using (public.nyx_is_member());
create policy nyx_as_owner on public.agent_status for update to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());

drop policy if exists nyx_ac_read  on public.agent_commands;
drop policy if exists nyx_ac_owner on public.agent_commands;
create policy nyx_ac_read  on public.agent_commands for select to authenticated using (public.nyx_is_member());
create policy nyx_ac_owner on public.agent_commands for insert to authenticated with check (public.nyx_is_owner() and status = 'queued');

-- 6) 에이전트 프로그램 배포 저장소 (공개 읽기: 프로그램 파일만 들어가고 토큰·비밀은 없음)
insert into storage.buckets (id, name, public) values ('agent', 'agent', true) on conflict (id) do update set public = true;
drop policy if exists "nyx agent read"   on storage.objects;
create policy "nyx agent read" on storage.objects for select to authenticated using (bucket_id = 'agent');
drop policy if exists "nyx agent upload" on storage.objects;
drop policy if exists "nyx agent update" on storage.objects;
drop policy if exists "nyx agent delete" on storage.objects;
create policy "nyx agent upload" on storage.objects for insert to authenticated with check (bucket_id = 'agent' and public.nyx_is_owner());
create policy "nyx agent update" on storage.objects for update to authenticated using (bucket_id = 'agent' and public.nyx_is_owner());
create policy "nyx agent delete" on storage.objects for delete to authenticated using (bucket_id = 'agent' and public.nyx_is_owner());

-- 로그인 사용자 접근 허용 (RLS가 다시 걸러냄)
grant select, update on public.agent_status to authenticated;
grant select, insert on public.agent_commands to authenticated;

-- 끝. 대시보드 → 에이전트 탭에서 "프로그램 배포" → "에이전트 추가" 순서로 진행하세요.
