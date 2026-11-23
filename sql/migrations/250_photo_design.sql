-- 사진 디자인: 바이블 인물 사진은 Higgsfield Soul 2(실사 인물)로 만들고 Soul ID로 얼굴을 고정한다.
-- 흐름: 설명 → Soul 2 후보 4장 → 대표가 고름 → 그 사진으로 Soul ID 학습 → 같은 얼굴로 기준 사진 세트(정면·3/4·전신) → 대표가 바이블에 적용.
-- 컷 설계의 장면 사진은 바이블 기준 사진들을 참고로 Nano Banana Pro가 그린다(에이전트가 경로를 스스로 찾음, 없으면 Grok Imagine).
-- 바이블 저장소에 쓰는 것은 여전히 대표 화면만 한다(후보·세트는 takes 저장소에 두고, 적용할 때 대표 화면이 바이블로 복사).
alter table public.bible_versions add column if not exists soul_id text;

create table if not exists public.photo_designs (
  id          uuid primary key default gen_random_uuid(),
  entry_id    uuid not null references public.bible_entries(id) on delete cascade,
  prompt      text not null,
  status      text not null default 'queued' check (status in ('queued','ready','training','set_ready','linked','failed')),
  previews    jsonb not null default '[]',     -- 후보 [{"path":"photos/…/1.png"}]
  chosen      jsonb not null default '[]',     -- 대표가 고른 후보 경로들
  soul_id     text,
  ref_set     jsonb not null default '[]',     -- 기준 사진 세트 [{"path":…, "label":"정면"}]
  error       text,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);
create index if not exists photo_designs_entry on public.photo_designs(entry_id, created_at desc);
alter table public.photo_designs enable row level security;
drop policy if exists nyx_read on public.photo_designs;
drop policy if exists nyx_owner on public.photo_designs;
create policy nyx_read on public.photo_designs for select to authenticated using (public.nyx_is_member());
create policy nyx_owner on public.photo_designs for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());
grant select, insert, update, delete on public.photo_designs to authenticated;
drop trigger if exists nyx_touch on public.photo_designs;
create trigger nyx_touch before update on public.photo_designs for each row execute function public.nyx_touch();

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set'));

create or replace function public.agent_get_photo_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'category', e.category, 'summary', e.summary, 'series_title', s.title,
                   'description', bv.description, 'details', bv.details, 'images', coalesce(bv.image_paths, '{}'), 'cur_soul_id', bv.soul_id,
                   'generation', (select value from public.app_settings where key = 'generation'))
          from public.photo_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          left join public.bible_versions bv on bv.entry_id = e.id and bv.status = 'active'
          where d.id = p_id);
end $$;

create or replace function public.agent_update_photo_design(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.photo_designs set
    status = coalesce(p->>'status', status), previews = coalesce(p->'previews', previews),
    soul_id = coalesce(p->>'soul_id', soul_id), ref_set = coalesce(p->'ref_set', ref_set), error = p->>'error'
  where id = p_id;
end $$;

revoke all on function public.agent_get_photo_design(text, uuid) from public;
revoke all on function public.agent_update_photo_design(text, uuid, jsonb) from public;
grant execute on function public.agent_get_photo_design(text, uuid) to anon, authenticated;
grant execute on function public.agent_update_photo_design(text, uuid, jsonb) to anon, authenticated;

-- 바이블에 Soul ID도 넘긴다
create or replace function public.agent_context(p_token text, p_episode uuid, p_version uuid default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_ser uuid; v_ver uuid := p_version;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select series_id into v_ser from public.episodes where id = p_episode;
  if v_ver is null then
    select id into v_ver from public.script_versions where episode_id = p_episode and status = 'confirmed'
     order by version_no desc nulls last limit 1;
  end if;
  return jsonb_build_object(
    'episode', (select to_jsonb(e) from public.episodes e where e.id = p_episode),
    'series',  (select jsonb_build_object('id', s.id, 'title', s.title) from public.series s where s.id = v_ser),
    'script',  (select jsonb_build_object('id', v.id, 'version_no', v.version_no, 'status', v.status, 'content', v.content)
                  from public.script_versions v where v.id = v_ver),
    'latest_draft', (select jsonb_build_object('id', v.id, 'content', v.content, 'note', v.note)
                  from public.script_versions v where v.episode_id = p_episode and v.status = 'draft' order by v.created_at desc limit 1),
    'neighbors', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                    'excerpt', left((select v.content from public.script_versions v where v.episode_id = e.id
                                       order by (v.status = 'confirmed') desc, v.created_at desc limit 1), 1500)) order by e.number), '[]')
                  from public.episodes e, public.episodes me
                  where me.id = p_episode and e.series_id = me.series_id and e.number in (me.number - 1, me.number + 1)),
    'guidelines', (select coalesce(jsonb_object_agg(st.name, g.content), '{}')
                  from public.staff st join public.staff_guidelines g on g.staff_id = st.id and g.status = 'active'),
    'bible', (select coalesce(jsonb_agg(jsonb_build_object('name', be.name, 'category', be.category, 'summary', be.summary,
                      'description', bv.description, 'details', bv.details, 'images', bv.image_paths, 'voice_id', bv.voice_id, 'soul_id', bv.soul_id) order by be.category, be.sort_order), '[]')
              from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
              where be.series_id = v_ser),
    'notes', (select coalesce(jsonb_agg(jsonb_build_object('scene_no', n.scene_no, 'priority', n.priority, 'important', n.important,
                      'content', n.content) order by n.scene_no nulls first), '[]')
              from public.episode_notes n where n.episode_id = p_episode and n.status <> 'closed'),
    'lessons', public.agent_get_lessons(p_token, v_ser, false),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings));
end $$;


-- 장면 사진 기본: Nano Banana Pro(경로 자동 탐색) → 안 되면 Grok Imagine 2.0
update public.app_settings set value = value || jsonb_build_object('image_model', 'auto', 'character_model', 'higgsfield-ai/soul/v2/standard')
 where key = 'generation' and coalesce(value->>'image_model', '') in ('', 'xai/grok-imagine-image-2.0');
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('higgsfield', 'higgsfield-ai/soul/v2/standard', 'per_image', 0.003, 'Soul 2 인물 사진(최저가 기준)', 'Higgsfield Explore 표시가 2026-11 (from $0.003, 해상도별 상이)'),
  ('higgsfield', 'soul-id-training', 'per_call', null, 'Soul ID 학습', '확인 후 입력'),
  ('higgsfield', 'nano-banana-pro', 'per_image', 0.234, 'Nano Banana Pro 장면 사진', 'Higgsfield 공식가 참고(apiyi 2026 비교표), 확인 후 수정')
on conflict (provider, model, unit) do nothing;
