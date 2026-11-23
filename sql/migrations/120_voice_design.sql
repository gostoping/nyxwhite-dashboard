-- 목소리 디자인: 대시보드에서 설명을 적으면 ElevenLabs가 후보 3개를 만들고, 대표가 들어 보고 고른다.
-- 고른 목소리 ID는 대표 화면에서 바이블 새 버전으로 적용된다(바이블 적용은 여전히 대표만).
create table if not exists public.voice_designs (
  id                  uuid primary key default gen_random_uuid(),
  entry_id            uuid not null references public.bible_entries(id) on delete cascade,
  description         text not null,
  sample_text         text,
  status              text not null default 'queued' check (status in ('queued','ready','saving','saved','linked','failed')),
  previews            jsonb not null default '[]',   -- [{"generated_voice_id":"…","path":"voices/…/1.mp3","duration":6.1}]
  chosen_generated_id text,
  voice_id            text,
  error               text,
  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now()
);
create index if not exists voice_designs_entry on public.voice_designs(entry_id, created_at desc);
alter table public.voice_designs enable row level security;
drop policy if exists nyx_read on public.voice_designs;
drop policy if exists nyx_owner on public.voice_designs;
create policy nyx_read on public.voice_designs for select to authenticated using (public.nyx_is_member());
create policy nyx_owner on public.voice_designs for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());
grant select, insert, update, delete on public.voice_designs to authenticated;
drop trigger if exists nyx_touch on public.voice_designs;
create trigger nyx_touch before update on public.voice_designs for each row execute function public.nyx_touch();

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','generate','store','video_review','bgm','voice_design','voice_save'));

create or replace function public.agent_get_voice_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'series_title', s.title)
          from public.voice_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          where d.id = p_id);
end $$;

create or replace function public.agent_update_voice_design(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.voice_designs set
    status = coalesce(p->>'status', status), previews = coalesce(p->'previews', previews),
    voice_id = coalesce(p->>'voice_id', voice_id), error = p->>'error'
  where id = p_id;
end $$;

revoke all on function public.agent_get_voice_design(text, uuid) from public;
revoke all on function public.agent_update_voice_design(text, uuid, jsonb) from public;
grant execute on function public.agent_get_voice_design(text, uuid) to anon, authenticated;
grant execute on function public.agent_update_voice_design(text, uuid, jsonb) to anon, authenticated;
