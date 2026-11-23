-- 편집실: 화마다 타임라인(영상·대사·음악·자막)을 대시보드에서 다듬고, 에이전트가 ffmpeg로 조립한다.
create table if not exists public.edit_projects (
  id          uuid primary key default gen_random_uuid(),
  episode_id  uuid not null unique references public.episodes(id) on delete cascade,
  timeline    jsonb not null default '{"clips":[],"music":[],"subs":[],"settings":{}}',
  rev         int not null default 1,
  updated_at  timestamptz not null default now()
);

create table if not exists public.edit_renders (
  id          uuid primary key default gen_random_uuid(),
  project_id  uuid not null references public.edit_projects(id) on delete cascade,
  episode_id  uuid not null references public.episodes(id) on delete cascade,
  kind        text not null check (kind in ('preview','final')),
  rev         int,
  timeline    jsonb not null,
  status      text not null default 'queued' check (status in ('queued','running','done','failed')),
  progress    text,
  files       jsonb not null default '{}',
  error       text,
  created_at  timestamptz not null default now(),
  finished_at timestamptz
);
create index if not exists edit_renders_project on public.edit_renders(project_id, created_at desc);

do $$
declare t text;
begin
  foreach t in array array['edit_projects','edit_renders'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists nyx_read on public.%I', t);
    execute format('drop policy if exists nyx_owner on public.%I', t);
    execute format('create policy nyx_read on public.%I for select to authenticated using (public.nyx_is_member())', t);
    execute format('create policy nyx_owner on public.%I for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner())', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','generate','store','video_review','bgm','voice_design','voice_save','voice_cast','assemble','edit_command'));

-- 대표가 음악 파일을 편집실에 올릴 수 있게(takes 저장소, music/ 아래)
drop policy if exists "nyx takes owner write" on storage.objects;
create policy "nyx takes owner write" on storage.objects for insert to authenticated
  with check (bucket_id = 'takes' and public.nyx_is_owner());

-- 에이전트: 편집 자료(타임라인 + 쓰인 테이크의 파일 위치 + 화·시리즈)
create or replace function public.agent_get_edit(p_token text, p_project uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_ep uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select episode_id into v_ep from public.edit_projects where id = p_project;
  return (select jsonb_build_object(
    'project', to_jsonb(p),
    'episode', jsonb_build_object('id', e.id, 'number', e.number, 'title', e.title, 'series_id', e.series_id),
    'series_title', (select title from public.series where id = e.series_id),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings),
    'takes', coalesce((select jsonb_object_agg(t.id, jsonb_build_object('files', t.files, 'remote_url', t.remote_url, 'seconds', t.seconds,
                         'code', c.code, 'scene_no', c.scene_no, 'cut_no', c.cut_no, 'dialogue', c.dialogue, 'summary', c.summary))
                       from public.production_takes t join public.production_cuts c on c.id = t.cut_id where c.episode_id = v_ep), '{}'))
    from public.edit_projects p join public.episodes e on e.id = p.episode_id where p.id = p_project);
end $$;

create or replace function public.agent_save_edit(p_token text, p_project uuid, p_timeline jsonb) returns int
language plpgsql security definer set search_path = public as $$
declare v int;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.edit_projects set timeline = p_timeline, rev = rev + 1, updated_at = now() where id = p_project returning rev into v;
  return v;
end $$;

create or replace function public.agent_get_render(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(r) from public.edit_renders r where r.id = p_id);
end $$;

create or replace function public.agent_update_render(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.edit_renders set status = coalesce(p->>'status', status), progress = coalesce(p->>'progress', progress),
    files = case when p ? 'files' then files || (p->'files') else files end, error = p->>'error',
    finished_at = case when p->>'status' in ('done','failed') then now() else finished_at end
  where id = p_id;
end $$;

do $$
declare f text;
begin
  foreach f in array array['agent_get_edit(text, uuid)', 'agent_save_edit(text, uuid, jsonb)', 'agent_get_render(text, uuid)', 'agent_update_render(text, uuid, jsonb)'] loop
    execute format('revoke all on function public.%s from public', f);
    execute format('grant execute on function public.%s to anon, authenticated', f);
  end loop;
end $$;
