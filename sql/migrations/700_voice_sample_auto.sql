-- 목소리 디자인: 들어 볼 대사는 코디네이터가 대본에서 뽑아 쓴다(대표가 100자를 쓰지 않음). '추천하기'는 대표가 누른 것이므로 기존 목소리도 바꿔 적용한다.
alter table public.voice_designs add column if not exists force_apply boolean not null default false;

create or replace function public.agent_add_voice_design3(p_token text, p_entry uuid, p_description text, p_sample text, p_auto boolean, p_force boolean) returns uuid
language plpgsql security definer set search_path = public as $$
declare v uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  insert into public.voice_designs (entry_id, description, sample_text, auto, force_apply)
  values (p_entry, p_description, p_sample, coalesce(p_auto, false), coalesce(p_force, false)) returning id into v;
  return v;
end $$;
revoke all on function public.agent_add_voice_design3(text, uuid, text, text, boolean, boolean) from public;
grant execute on function public.agent_add_voice_design3(text, uuid, text, text, boolean, boolean) to anon, authenticated;

create or replace function public.agent_get_voice_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'series_title', s.title, 'series_id', s.id, 'entry_summary', e.summary,
                  'entry_description', (select v.description from public.bible_versions v where v.entry_id = e.id and v.status = 'active' limit 1))
          from public.voice_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          where d.id = p_id);
end $$;
