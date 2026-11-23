-- 화별 대표 연출 메모: 대표가 화·씬마다 남기는 연출 지시. 감독이 컷 카드에 옮기고, 감수자가 반영 여부를 확인한다.
create table if not exists public.episode_notes (
  id           uuid primary key default gen_random_uuid(),
  episode_id   uuid not null references public.episodes(id) on delete cascade,
  scene_no     int,                                   -- 비어 있으면 화 전체
  priority     text not null default 'must' check (priority in ('must','nice')),   -- 필수 반영 / 참고
  important    boolean not null default false,        -- 중요 장면(주요 등급으로 다룸)
  content      text not null,
  status       text not null default 'open' check (status in ('open','applied','closed')),  -- 반영 대기 / 반영됨 / 대표 확인 완료
  applied_note text,                                  -- 감독이 어떻게 반영했는지
  device_id    uuid references public.devices(id) on delete set null,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);
create index if not exists episode_notes_episode on public.episode_notes(episode_id);

create or replace function public.nyx_notes_stamp() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  new.updated_at := now();
  if TG_OP = 'INSERT' then new.device_id := coalesce(new.device_id, public.nyx_header_device()); end if;
  return new;
end $$;
drop trigger if exists nyx_notes_stamp on public.episode_notes;
create trigger nyx_notes_stamp before insert or update on public.episode_notes
  for each row execute function public.nyx_notes_stamp();

create or replace function public.nyx_notes_log() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_ep text;
begin
  select e.number || '화' into v_ep from public.episodes e where e.id = new.episode_id;
  perform public.nyx_log('대표', '연출 메모 추가 (' || coalesce(v_ep, '') || coalesce(' S#' || new.scene_no, ' 전체') || '): ' || left(new.content, 60),
                         new.id, new.device_id);
  return new;
end $$;
drop trigger if exists nyx_notes_log on public.episode_notes;
create trigger nyx_notes_log after insert on public.episode_notes
  for each row execute function public.nyx_notes_log();

alter table public.episode_notes enable row level security;
drop policy if exists nyx_en_read  on public.episode_notes;
drop policy if exists nyx_en_owner on public.episode_notes;
create policy nyx_en_read  on public.episode_notes for select to authenticated using (public.nyx_is_member());
create policy nyx_en_owner on public.episode_notes for all    to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());
grant select, insert, update, delete on public.episode_notes to authenticated;
