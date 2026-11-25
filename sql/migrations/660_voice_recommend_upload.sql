drop function if exists public.agent_voice_link(text, uuid);
-- 목소리도 사진처럼: ① 인물마다 추천 목소리를 자동으로 만들어 바이블에 먼저 넣고(대표가 언제든 다른 후보로 바꿈)
-- ② 대표가 목소리 파일을 올리면 ElevenLabs가 그 목소리로 만들어 바이블에 반영한다.
alter table public.voice_designs add column if not exists auto boolean not null default false;      -- 자동 추천으로 만든 디자인
alter table public.voice_designs add column if not exists sample_path text;                         -- 대표가 올린 목소리 파일(takes 버킷)
alter table public.voice_designs add column if not exists preview_path text;                        -- 저장된 목소리로 읽은 시험 대사

-- 동일인(same_as) 변형 인물은 원래 인물의 목소리를 물려받으므로 따로 만들지 않는다
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
                   where be.series_id = p_series and be.category = 'character' and be.same_as is null),
    'scripts', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                   'text', left((select v.content from public.script_versions v where v.episode_id = e.id
                                 order by (v.status = 'confirmed') desc, v.created_at desc limit 1), 12000)) order by e.number), '[]')
                from (select * from public.episodes where series_id = p_series order by number limit 3) e));
end $$;

create or replace function public.agent_add_voice_design2(p_token text, p_entry uuid, p_description text, p_sample text, p_auto boolean) returns uuid
language plpgsql security definer set search_path = public as $$
declare v uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  insert into public.voice_designs (entry_id, description, sample_text, auto) values (p_entry, p_description, p_sample, coalesce(p_auto, false)) returning id into v;
  return v;
end $$;
revoke all on function public.agent_add_voice_design2(text, uuid, text, text, boolean) from public;
grant execute on function public.agent_add_voice_design2(text, uuid, text, text, boolean) to anon, authenticated;

create or replace function public.agent_update_voice_design(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.voice_designs set
    status = coalesce(p->>'status', status), previews = coalesce(p->'previews', previews),
    voice_id = coalesce(p->>'voice_id', voice_id), error = p->>'error',
    chosen_generated_id = coalesce(p->>'chosen_generated_id', chosen_generated_id),
    preview_path = coalesce(p->>'preview_path', preview_path)
  where id = p_id;
end $$;

-- 에이전트가 저장한 목소리를 바이블에 넣는다: 자동 추천(대표가 아직 목소리를 안 정한 인물만) 또는 대표가 올린 파일.
-- 현재 버전을 그대로 복사하고 voice_id만 바꾼 새 버전을 적용한다(사진·묘사는 그대로).
create or replace function public.agent_voice_link(p_token text, p_design uuid, p_force boolean default false) returns text
language plpgsql security definer set search_path = public as $$
declare d public.voice_designs; cur public.bible_versions; v_no int; v_new uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select * into d from public.voice_designs where id = p_design;
  if d.id is null or d.voice_id is null then return 'no_voice'; end if;
  select * into cur from public.bible_versions where entry_id = d.entry_id and status = 'active' order by version_no desc limit 1;
  -- 자동 추천은 대표가 이미 정한 목소리를 덮지 않는다
  if not p_force and d.auto and d.sample_path is null and cur.voice_id is not null
     and not exists (select 1 from public.voice_designs x where x.entry_id = d.entry_id and x.auto and x.voice_id = cur.voice_id) then
    update public.voice_designs set status = 'saved' where id = d.id;
    return 'kept_owner_voice';
  end if;
  select coalesce(max(version_no), 0) + 1 into v_no from public.bible_versions where entry_id = d.entry_id;
  insert into public.bible_versions (entry_id, version_no, description, details, image_paths, voice_path, voice_id, soul_id, prompt_en,
                                     change_reason, status, proposed_by, decided_at)
  values (d.entry_id, v_no, coalesce(cur.description, (select coalesce(summary, name) from public.bible_entries where id = d.entry_id)), cur.details,
          coalesce(cur.image_paths, '{}'), cur.voice_path, d.voice_id, cur.soul_id, cur.prompt_en,
          case when d.sample_path is not null then '대표가 올린 목소리 파일로 목소리 고정' else '추천 목소리(자동) — 바이블 목소리에서 다른 후보로 바꿀 수 있습니다' end,
          'proposed', 'ai', now())
  returning id into v_new;
  update public.bible_versions set status = 'retired' where entry_id = d.entry_id and status = 'active' and id <> v_new;
  update public.bible_versions set status = 'active' where id = v_new;
  -- 목소리만 바뀐 버전이다: 사진·프롬프트 감수를 다시 돌리지 않는다(비용·대기 줄이기)
  delete from public.pipeline_tasks where status = 'queued' and kind in ('coord_review', 'bible_prompt')
    and (payload->>'version_id' = v_new::text or payload->>'entry_id' = d.entry_id::text and created_at > now() - interval '5 seconds');
  update public.voice_designs set status = 'linked' where id = d.id;
  -- 이 인물 대사가 있는 생성 전 컷 → 새 바이블 목소리로 대사 미리 듣기를 다시 만든다
  with hit as (
    update public.production_cuts c set prep_status = 'none'
    where c.status in ('designed', 'failed', 'hold') and c.prep_status <> 'preparing'
      and exists (select 1 from jsonb_array_elements(coalesce(c.voice_lines, '[]')) l
                  where regexp_replace(l->>'speaker', '\s*\(.*?\)\s*', '', 'g')
                        = regexp_replace((select name from public.bible_entries where id = d.entry_id), '\s*\(.*?\)\s*', '', 'g'))
    returning c.episode_id, c.scene_no)
  insert into public.pipeline_tasks (kind, episode_id, scene_no, payload)
  select distinct 'prep_cut', h.episode_id, h.scene_no, '{}'::jsonb from hit h
  where not exists (select 1 from public.pipeline_tasks t where t.kind = 'prep_cut' and t.status in ('queued', 'running')
                    and t.episode_id = h.episode_id and t.scene_no = h.scene_no and t.cut_id is null);
  return 'linked';
end $$;
revoke all on function public.agent_voice_link(text, uuid, boolean) from public;
grant execute on function public.agent_voice_link(text, uuid, boolean) to anon, authenticated;

-- 인물이 생기거나 바뀌었는데 목소리가 없으면 → 시리즈 목소리 설계를 자동으로 맡긴다(같은 시리즈 대기 중이면 하나만)
create or replace function public.nyx_voice_queue() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_series uuid; v_cat text; v_same uuid;
begin
  if new.status <> 'active' or new.voice_id is not null then return new; end if;
  select series_id, category, same_as into v_series, v_cat, v_same from public.bible_entries where id = new.entry_id;
  if v_cat <> 'character' or v_same is not null then return new; end if;
  if exists (select 1 from public.voice_designs d where d.entry_id = new.entry_id and d.status in ('queued','ready','saving','saved','linked')) then return new; end if;
  if exists (select 1 from public.pipeline_tasks t where t.kind = 'voice_cast' and t.status in ('queued','running')
             and coalesce(t.series_id, (t.payload->>'series_id')::uuid) = v_series) then return new; end if;
  insert into public.pipeline_tasks (kind, series_id, payload) values ('voice_cast', v_series, jsonb_build_object('series_id', v_series, 'auto', true));
  return new;
end $$;
drop trigger if exists nyx_voice_queue on public.bible_versions;
create trigger nyx_voice_queue after insert or update of status on public.bible_versions for each row execute function public.nyx_voice_queue();

-- 지금 목소리가 없는 인물이 있는 시리즈마다 한 번 추천을 시작한다
insert into public.pipeline_tasks (kind, series_id, payload)
select 'voice_cast', s.id, jsonb_build_object('series_id', s.id, 'auto', true)
from public.series s
where exists (select 1 from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
              where be.series_id = s.id and be.category = 'character' and be.same_as is null and bv.voice_id is null
                and not exists (select 1 from public.voice_designs d where d.entry_id = be.id and d.status in ('queued','ready','saving','saved','linked')))
  and not exists (select 1 from public.pipeline_tasks t where t.kind = 'voice_cast' and t.status in ('queued','running') and t.series_id = s.id)
  and coalesce(s.on_hold, false) = false;
