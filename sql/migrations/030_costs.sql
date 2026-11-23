-- 비용 장부와 견적서
-- 모든 지출(AI 호출, 영상 생성, 수동 입력, 고정비)을 한 줄씩 기록하고,
-- 결과(성공·실패·진행 중·보류)로 나눠 "순수 제작비"와 "누수 비용"을 구분한다.

-- 1) 환율 등 설정 (한 줄짜리 표)
create table if not exists public.cost_settings (
  id          int primary key default 1 check (id = 1),
  krw_per_usd numeric not null default 1340,
  updated_at  timestamptz not null default now()
);
insert into public.cost_settings (id) values (1) on conflict (id) do nothing;

-- 2) 단가표 (견적과 자동 계산에 씀, 대표가 대시보드에서 수정)
create table if not exists public.price_book (
  id         uuid primary key default gen_random_uuid(),
  provider   text not null,               -- higgsfield / anthropic / local / other
  model      text not null,               -- 예: seedance-2.5
  unit       text not null check (unit in ('per_second','per_1m_input_tokens','per_1m_output_tokens','per_image','per_call','per_month')),
  usd        numeric,                     -- 비어 있으면 "단가 미입력"
  label      text,                        -- 화면 표시 이름
  source     text,                        -- 단가 출처
  updated_at timestamptz not null default now(),
  unique (provider, model, unit)
);
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('higgsfield', 'seedance-2.5', 'per_second', 0.0738, 'Seedance 2.5 영상', 'Higgsfield API 안내(2026-11)'),
  ('higgsfield', 'kling-3.0-720p', 'per_second', 0.084, 'Kling 3.0 720p 영상', 'Higgsfield 비교표: 10초 $0.84'),
  ('anthropic', 'claude', 'per_1m_input_tokens', null, 'Claude 입력 토큰(100만 개당)', '확인 후 입력'),
  ('anthropic', 'claude', 'per_1m_output_tokens', null, 'Claude 출력 토큰(100만 개당)', '확인 후 입력'),
  ('local', 'ssamcut', 'per_call', 0, '쌤컷 편집(무료)', '무료 프로그램'),
  ('local', 'ffmpeg-whisper', 'per_call', 0, '맥 로컬 처리(자막·음량·업스케일)', '맥에서 무료 처리')
on conflict (provider, model, unit) do nothing;

-- 3) 비용 장부
create table if not exists public.cost_events (
  id                uuid primary key default gen_random_uuid(),
  occurred_at       timestamptz not null default now(),
  series_id         uuid references public.series(id) on delete set null,
  episode_id        uuid references public.episodes(id) on delete set null,
  scene_no          int,
  cut_id            text,                 -- 예: E01-S03-C02
  stage             text not null default 'other' check (stage in (
                      'script_revision','cut_card','prompt_review','keyframe','keyframe_review','video','video_review',
                      'final_review','evaluate','upscale','subtitle','audio','edit','directive','fixed','other')),
  provider          text,
  model             text,
  attempt_no        int,                  -- 같은 컷·단계의 몇 번째 시도인지
  qty               jsonb,                -- {"seconds":10} / {"input_tokens":1200,"output_tokens":800} / {"images":1}
  usd               numeric,
  krw               numeric,              -- 비어 있으면 기록 시 환율로 계산
  outcome           text not null default 'pending' check (outcome in ('success','failed','pending','hold')),
  failure_reason    text,                 -- 왜 실패했나 (감수 의견 요약)
  failure_cause     text check (failure_cause in ('director','tool','script','standard','other')),
  job_id            uuid,
  script_version_id uuid,
  source            text not null default 'agent' check (source in ('agent','manual')),
  note              text,
  device_id         uuid references public.devices(id) on delete set null,
  updated_at        timestamptz not null default now()
);
create index if not exists cost_events_episode on public.cost_events(episode_id, occurred_at desc);
create index if not exists cost_events_outcome on public.cost_events(outcome);

create or replace function public.nyx_cost_stamp() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.series_id is null and new.episode_id is not null then
    select series_id into new.series_id from public.episodes where id = new.episode_id;
  end if;
  if new.krw is null and new.usd is not null then
    new.krw := round(new.usd * (select krw_per_usd from public.cost_settings where id = 1));
  end if;
  if TG_OP = 'INSERT' then new.device_id := coalesce(new.device_id, public.nyx_header_device()); end if;
  new.updated_at := now();
  return new;
end $$;
drop trigger if exists nyx_cost_stamp on public.cost_events;
create trigger nyx_cost_stamp before insert or update on public.cost_events
  for each row execute function public.nyx_cost_stamp();

-- 4) 화별 매출·배포 상태, 배포 시점 최종 견적서 보관
create table if not exists public.episode_finance (
  episode_id  uuid primary key references public.episodes(id) on delete cascade,
  revenue_krw numeric,
  note        text,
  updated_at  timestamptz not null default now()
);
create table if not exists public.final_invoices (
  id          uuid primary key default gen_random_uuid(),
  episode_id  uuid not null references public.episodes(id) on delete cascade,
  label       text not null,             -- 예: 1화 배포본
  snapshot    jsonb not null,            -- 확정 당시 견적서 내용 그대로
  created_at  timestamptz not null default now()
);

-- 5) 에이전트용: 비용 기록, 결과 정리 (토큰으로만)
create or replace function public.agent_log_cost(p_token text, p_cost jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  insert into public.cost_events (episode_id, scene_no, cut_id, stage, provider, model, attempt_no, qty, usd, krw,
                                  outcome, failure_reason, failure_cause, job_id, script_version_id, note, device_id, source)
  values ((p_cost->>'episode_id')::uuid, (p_cost->>'scene_no')::int, p_cost->>'cut_id', coalesce(p_cost->>'stage', 'other'),
          p_cost->>'provider', p_cost->>'model', (p_cost->>'attempt_no')::int, p_cost->'qty', (p_cost->>'usd')::numeric,
          (p_cost->>'krw')::numeric, coalesce(p_cost->>'outcome', 'pending'), p_cost->>'failure_reason', p_cost->>'failure_cause',
          (p_cost->>'job_id')::uuid, (p_cost->>'script_version_id')::uuid, p_cost->>'note', v_dev, 'agent')
  returning id into v_id;
  return v_id;
end $$;

create or replace function public.agent_set_cost_outcome(p_token text, p_ids uuid[], p_outcome text,
                                                         p_reason text default null, p_cause text default null) returns int
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); n int;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  update public.cost_events set outcome = p_outcome,
         failure_reason = coalesce(p_reason, failure_reason), failure_cause = coalesce(p_cause, failure_cause)
  where id = any(p_ids);
  get diagnostics n = row_count;
  return n;
end $$;

create or replace function public.agent_price_book(p_token text) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return jsonb_build_object(
    'krw_per_usd', (select krw_per_usd from public.cost_settings where id = 1),
    'prices', coalesce((select jsonb_agg(to_jsonb(p) - 'id') from public.price_book p), '[]'::jsonb));
end $$;

revoke all on function public.agent_log_cost(text, jsonb) from public;
revoke all on function public.agent_set_cost_outcome(text, uuid[], text, text, text) from public;
revoke all on function public.agent_price_book(text) from public;
grant execute on function public.agent_log_cost(text, jsonb) to anon, authenticated;
grant execute on function public.agent_set_cost_outcome(text, uuid[], text, text, text) to anon, authenticated;
grant execute on function public.agent_price_book(text) to anon, authenticated;

-- 6) 권한 규칙: 구성원은 보기, 대표는 수정
do $$
declare t text;
begin
  foreach t in array array['cost_settings','price_book','cost_events','episode_finance','final_invoices'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists nyx_read on public.%I', t);
    execute format('drop policy if exists nyx_owner on public.%I', t);
    execute format('create policy nyx_read on public.%I for select to authenticated using (public.nyx_is_member())', t);
    execute format('create policy nyx_owner on public.%I for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner())', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;
