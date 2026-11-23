-- 광고 탭: 시리즈별 광고용 콘텐츠와 집행 이력·성과. 지금은 대표가 직접 기록할 수 있고,
-- 유튜브·Google Ads 연결 후에는 광고 담당·배포 담당(에이전트)이 자동으로 기록한다.
create table if not exists public.ad_contents (
  id          uuid primary key default gen_random_uuid(),
  series_id   uuid not null references public.series(id) on delete cascade,
  episode_id  uuid references public.episodes(id) on delete set null,   -- 어느 화(스토리)에서 나왔나. 비면 시리즈 전체
  kind        text not null default 'shorts' check (kind in ('shorts','hook','trailer','compilation','episode','thumbnail','other')),
  title       text not null,
  description text,
  source      jsonb not null default '{}',   -- {"render_id":…, "clips":[…], "from_sec":…, "to_sec":…}
  media       jsonb not null default '{}',   -- {"preview":"takes/…mp4","poster":"…jpg","youtube_id":"…"}
  status      text not null default 'idea' check (status in ('idea','making','ready','running','paused','ended')),
  created_by  text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists ad_contents_series on public.ad_contents(series_id, episode_id);

create table if not exists public.ad_runs (
  id          uuid primary key default gen_random_uuid(),
  content_id  uuid not null references public.ad_contents(id) on delete cascade,
  platform    text not null default 'youtube_ads' check (platform in ('youtube_ads','youtube_organic','youtube_monetization','meta','tiktok','other')),
  objective   text not null default 'subscribers' check (objective in ('views','subscribers','traffic','revenue','awareness')),
  placement   text,                 -- 인스트림·쇼츠·디스커버리 / 수익: pre·mid·post
  position_sec numeric,             -- 수익 광고(중간 광고) 위치
  targeting   text,                 -- 대상(나라·나이·관심사)
  started_at  date,
  ended_at    date,
  budget_krw  numeric,
  spend_krw   numeric,
  metrics     jsonb not null default '{}',   -- impressions, views, clicks, subs, watch_hours, avg_view_pct, next_ep_rate, revenue_krw
  note        text,
  source      text not null default 'manual' check (source in ('manual','agent')),
  created_at  timestamptz not null default now()
);
create index if not exists ad_runs_content on public.ad_runs(content_id, started_at);

do $$
declare t text;
begin
  foreach t in array array['ad_contents','ad_runs'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists nyx_read on public.%I', t);
    execute format('drop policy if exists nyx_owner on public.%I', t);
    execute format('create policy nyx_read on public.%I for select to authenticated using (public.nyx_is_member())', t);
    execute format('create policy nyx_owner on public.%I for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner())', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;
drop trigger if exists nyx_touch on public.ad_contents;
create trigger nyx_touch before update on public.ad_contents for each row execute function public.nyx_touch();

-- 에이전트(광고 담당·배포 담당)용
create or replace function public.agent_upsert_ad_content(p_token text, p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_id uuid := (p->>'id')::uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if v_id is null then
    insert into public.ad_contents (series_id, episode_id, kind, title, description, source, media, status, created_by)
    values ((p->>'series_id')::uuid, (p->>'episode_id')::uuid, coalesce(p->>'kind', 'shorts'), p->>'title', p->>'description',
            coalesce(p->'source', '{}'), coalesce(p->'media', '{}'), coalesce(p->>'status', 'idea'), coalesce(p->>'created_by', '광고 담당'))
    returning id into v_id;
  else
    update public.ad_contents set title = coalesce(p->>'title', title), description = coalesce(p->>'description', description),
      status = coalesce(p->>'status', status), media = media || coalesce(p->'media', '{}'), source = source || coalesce(p->'source', '{}')
    where id = v_id;
  end if;
  return v_id;
end $$;

create or replace function public.agent_add_ad_run(p_token text, p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_id uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  insert into public.ad_runs (content_id, platform, objective, placement, position_sec, targeting, started_at, ended_at, budget_krw, spend_krw, metrics, note, source)
  values ((p->>'content_id')::uuid, coalesce(p->>'platform', 'youtube_ads'), coalesce(p->>'objective', 'subscribers'), p->>'placement',
          (p->>'position_sec')::numeric, p->>'targeting', (p->>'started_at')::date, (p->>'ended_at')::date,
          (p->>'budget_krw')::numeric, (p->>'spend_krw')::numeric, coalesce(p->'metrics', '{}'), p->>'note', 'agent')
  returning id into v_id;
  return v_id;
end $$;

revoke all on function public.agent_upsert_ad_content(text, jsonb) from public;
revoke all on function public.agent_add_ad_run(text, jsonb) from public;
grant execute on function public.agent_upsert_ad_content(text, jsonb) to anon, authenticated;
grant execute on function public.agent_add_ad_run(text, jsonb) to anon, authenticated;
