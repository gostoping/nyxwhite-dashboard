-- 시리즈 목소리 한꺼번에: 작가실(Claude)이 대본·바이블을 읽고 인물별 목소리 설명과 시험 대사를 쓰고,
-- 인물마다 ElevenLabs 후보 3개를 만든다. 대표는 바이블 탭에서 듣고 고르기만 한다.
alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','generate','store','video_review','bgm','voice_design','voice_save','voice_cast'));
alter table public.pipeline_tasks add column if not exists series_id uuid references public.series(id) on delete cascade;

create or replace function public.agent_series_cast(p_token text, p_series uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return jsonb_build_object(
    'series', (select jsonb_build_object('id', id, 'title', title) from public.series where id = p_series),
    'characters', (select coalesce(jsonb_agg(jsonb_build_object('entry_id', be.id, 'name', be.name, 'summary', be.summary,
                      'description', bv.description, 'details', bv.details, 'voice_id', bv.voice_id,
                      'open_design', exists (select 1 from public.voice_designs d where d.entry_id = be.id and d.status in ('queued','ready','saving','saved')))
                    order by be.sort_order, be.created_at), '[]')
                   from public.bible_entries be
                   left join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
                   where be.series_id = p_series and be.category = 'character'),
    'scripts', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                   'text', left((select v.content from public.script_versions v where v.episode_id = e.id
                                 order by (v.status = 'confirmed') desc, v.created_at desc limit 1), 12000)) order by e.number), '[]')
                from (select * from public.episodes where series_id = p_series order by number limit 3) e));
end $$;

create or replace function public.agent_add_voice_design(p_token text, p_entry uuid, p_description text, p_sample text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_id uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  insert into public.voice_designs (entry_id, description, sample_text) values (p_entry, left(p_description, 1000), left(p_sample, 1000))
  returning id into v_id;
  return v_id;
end $$;

revoke all on function public.agent_series_cast(text, uuid) from public;
revoke all on function public.agent_add_voice_design(text, uuid, text, text) from public;
grant execute on function public.agent_series_cast(text, uuid) to anon, authenticated;
grant execute on function public.agent_add_voice_design(text, uuid, text, text) to anon, authenticated;
