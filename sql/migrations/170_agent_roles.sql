-- 에이전트 역할: 회사(office) / 서버(server, 집 윈도우: 영상 보관·완성본) / 이동용(mobile, 노트북: 가벼운 일만)
alter table public.agent_status add column if not exists role text not null default 'office';
alter table public.agent_status drop constraint if exists agent_status_role_check;
alter table public.agent_status add constraint agent_status_role_check check (role in ('office','server','mobile'));

-- 지금 있는 집 윈도우가 있으면 서버로(이동용으로 이름 붙은 것은 제외)
update public.agent_status s set role = 'server'
 where s.platform = 'windows' and s.role = 'office'
   and not exists (select 1 from public.devices d where d.id = s.device_id and d.name ~ '이동');
update public.agent_status s set role = 'mobile'
 where exists (select 1 from public.devices d where d.id = s.device_id and d.name ~ '이동');

-- 일 나누기: 보관(store)은 서버만, 이동용은 오래 걸리는 일(영상 생성·보관·조립)을 받지 않는다
create or replace function public.agent_claim_task(p_token text, p_kinds text[], p_platform text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid; v jsonb; v_role text; v_kinds text[] := p_kinds;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  select role into v_role from public.agent_status where device_id = v_dev;
  if (select paused from public.agent_status where device_id = v_dev) then return null; end if;
  if v_role = 'mobile' then
    v_kinds := array(select k from unnest(p_kinds) k where k not in ('generate','store','assemble','bgm'));
  elsif v_role <> 'server' then
    v_kinds := array(select k from unnest(p_kinds) k where k <> 'store');
  end if;
  update public.pipeline_tasks set status = 'queued', device_id = null, progress = '이전 실행이 끊겨 다시 시도'
   where status = 'running' and started_at < now() - interval '45 minutes' and attempts < 3;
  select id into v_id from public.pipeline_tasks
   where status = 'queued' and kind = any(v_kinds) and not_before <= now()
     and (platform is null or platform = p_platform or (platform = 'server' and v_role = 'server'))
   order by case kind when 'store' then 0 when 'video_review' then 1 else 2 end, created_at
   for update skip locked limit 1;
  if v_id is null then return null; end if;
  update public.pipeline_tasks set status = 'running', device_id = v_dev, started_at = now(), attempts = attempts + 1
   where id = v_id returning to_jsonb(pipeline_tasks.*) into v;
  update public.agent_status set current_job = v->>'kind' where device_id = v_dev;
  return v;
end $$;

-- '윈도우가 있나?' = 보관 서버가 있나 (에이전트가 보관 작업을 만들 때 씀)
create or replace function public.agent_has_platform(p_token text, p_platform text) returns boolean
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if p_platform = 'windows' then
    return exists (select 1 from public.agent_status where role = 'server' and coalesce(state, '') not in ('removed', 'not_installed'));
  end if;
  return exists (select 1 from public.agent_status where platform = p_platform and coalesce(state, '') not in ('removed', 'not_installed'));
end $$;

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_platform_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_platform_check check (platform in ('mac','windows','server'));

-- 보관 작업은 서버 역할로 (이동용 윈도우가 가져가지 않게)
update public.pipeline_tasks set platform = 'server' where kind = 'store' and status = 'queued';
create or replace function public.nyx_store_to_server() returns trigger language plpgsql as $$
begin
  if new.kind = 'store' and coalesce(new.platform, 'windows') = 'windows' then new.platform := 'server'; end if;
  return new;
end $$;
drop trigger if exists nyx_store_to_server on public.pipeline_tasks;
create trigger nyx_store_to_server before insert on public.pipeline_tasks for each row execute function public.nyx_store_to_server();
