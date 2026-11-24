-- 보류함: 만들다 멈춘 시리즈·화를 영구 보류해 두고(자료는 그대로), 나중에 꺼내 쓴다.
alter table public.series add column if not exists on_hold boolean not null default false;
alter table public.series add column if not exists held_at timestamptz;
alter table public.series add column if not exists hold_note text;
alter table public.episodes add column if not exists held_at timestamptz;
alter table public.episodes add column if not exists hold_note text;
alter table public.episodes add column if not exists status_before_hold text;

-- 보류/꺼내기: 보류하면 대기 중인 일은 취소(진행 중인 일은 끝까지), 꺼내면 원래 상태로
create or replace function public.owner_hold(p_kind text, p_id uuid, p_on boolean, p_note text default null) returns int
language plpgsql security definer set search_path = public as $$
declare n int := 0;
begin
  if not public.nyx_is_owner() then raise exception '대표만 할 수 있습니다'; end if;
  if p_kind = 'series' then
    update public.series set on_hold = p_on, held_at = case when p_on then now() end, hold_note = case when p_on then p_note end where id = p_id;
    if p_on then
      update public.pipeline_tasks set status = 'cancelled', error = '시리즈 보류로 취소', finished_at = now()
       where status = 'queued' and (series_id = p_id or episode_id in (select id from public.episodes where series_id = p_id));
      get diagnostics n = row_count;
    end if;
  elsif p_kind = 'episode' then
    if p_on then
      update public.episodes set status_before_hold = case when status = 'on_hold' then status_before_hold else status end,
             status = 'on_hold', held_at = now(), hold_note = p_note where id = p_id;
      update public.pipeline_tasks set status = 'cancelled', error = '화 보류로 취소', finished_at = now()
       where status = 'queued' and episode_id = p_id;
      get diagnostics n = row_count;
    else
      update public.episodes set status = coalesce(status_before_hold, 'ready'), held_at = null, hold_note = null, status_before_hold = null
       where id = p_id and status = 'on_hold';
    end if;
  else
    raise exception 'p_kind는 series 또는 episode';
  end if;
  return n;
end $$;
revoke all on function public.owner_hold(text, uuid, boolean, text) from public, anon;
grant execute on function public.owner_hold(text, uuid, boolean, text) to authenticated;

-- 보류 중인 시리즈·화에는 새 일이 시작되지 않는다(자동 재시도·자동 이어 하기 포함)
create or replace function public.nyx_task_hold_guard() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'queued' and (
       exists (select 1 from public.episodes e where e.id = new.episode_id and e.status = 'on_hold')
    or exists (select 1 from public.episodes e join public.series s on s.id = e.series_id where e.id = new.episode_id and s.on_hold)
    or exists (select 1 from public.series s where s.id = coalesce(new.series_id, (new.payload->>'series_id')::uuid) and s.on_hold)) then
    new.status := 'cancelled';
    new.error := '보류 중인 시리즈·화라 시작하지 않았습니다(기록 탭 보류함에서 꺼내면 다시 할 수 있음)';
  end if;
  return new;
end $$;
drop trigger if exists nyx_task_hold_guard on public.pipeline_tasks;
create trigger nyx_task_hold_guard before insert on public.pipeline_tasks for each row execute function public.nyx_task_hold_guard();
