-- 제작 파이프라인: 작가실 → 감독(컷 설계) → 대본감수자 → [대표 생성 승인] → Seedance 생성 → 영상감수자·평가
-- 씬 단위 보관: 영상(무음) / 대사·현장음 / 배경음악 / 원본 을 따로 저장하고, 집 윈도우 PC(OneDrive 폴더)를 보관 서버로 쓴다.

-- 1) 컷: 감독이 설계한 15초 이하 단위
create table if not exists public.production_cuts (
  id                uuid primary key default gen_random_uuid(),
  episode_id        uuid not null references public.episodes(id) on delete cascade,
  script_version_id uuid,
  scene_no          int not null,
  cut_no            int not null,
  code              text,                        -- 예: E01-S03-C02
  scene_heading     text,                        -- 씬 머리(장소·시간)
  seconds           int not null default 10 check (seconds between 4 and 15),
  summary           text,                        -- 이 컷에서 일어나는 일
  dialogue          text,                        -- 대사(화자: 대사)
  emotion           text,                        -- 이 컷의 감정선
  expression_beats  jsonb not null default '[]', -- [{"t":"0-3","who":"린","face":"놀람→굳음"}, …] 2~3초마다
  prompt            text,                        -- Seedance에 보낼 프롬프트
  reference_names   text[] not null default '{}',-- 바이블 항목 이름(사진을 참고 이미지로 붙임)
  camera            text,
  director_note     text,                        -- 감독이 남긴 설명·반영한 교훈
  review            jsonb,                       -- 대본감수자 결과
  status            text not null default 'designed' check (status in
                      ('designed','approved','generating','reviewing','passed','failed','hold','cancelled')),
  resolution        text not null default '480p' check (resolution in ('480p','720p','1080p')),
  est_usd           numeric,
  best_take_id      uuid,
  fix_request       text,                        -- 대표가 감독에게 남긴 수정 요청
  created_at        timestamptz not null default now(),
  updated_at        timestamptz not null default now()
);
create index if not exists production_cuts_ep on public.production_cuts(episode_id, scene_no, cut_no);

-- 2) 테이크: 한 컷을 생성한 한 번의 결과
create table if not exists public.production_takes (
  id            uuid primary key default gen_random_uuid(),
  cut_id        uuid not null references public.production_cuts(id) on delete cascade,
  take_no       int not null default 1,
  model         text,
  resolution    text,
  seconds       int,
  request_id    text,
  status        text not null default 'submitted' check (status in
                  ('submitted','in_progress','completed','failed','nsfw','canceled','stored')),
  remote_url    text,                            -- Higgsfield 결과 주소(며칠 뒤 사라짐)
  files         jsonb not null default '{}',     -- {"preview":"takes/..mp4","poster":"..jpg","voice_preview":"..m4a","mac":{...},"windows":{...}}
  review        jsonb,                           -- 영상감수자 결과
  score         numeric,
  verdict       text check (verdict in ('pass','fail')),
  usd           numeric,
  cost_event_id uuid,
  error         text,
  created_at    timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);
create index if not exists production_takes_cut on public.production_takes(cut_id, take_no);

-- 3) 씬 묶음: 씬별 보관 상태(배경음악 포함)
create table if not exists public.scene_packages (
  id          uuid primary key default gen_random_uuid(),
  episode_id  uuid not null references public.episodes(id) on delete cascade,
  scene_no    int not null,
  heading     text,
  folder      jsonb not null default '{}',   -- {"windows":"C:\\...\\S03_..", "mac":"/Users/..."}
  bgm         jsonb not null default '{}',   -- {"status":"none|requested|ready","prompt":"…","files":{…}}
  note        text,
  updated_at  timestamptz not null default now(),
  unique (episode_id, scene_no)
);

-- 4) 작업 대기열(제작 전용)
create table if not exists public.pipeline_tasks (
  id          uuid primary key default gen_random_uuid(),
  kind        text not null check (kind in ('writers_room','director','generate','store','video_review','bgm')),
  episode_id  uuid references public.episodes(id) on delete cascade,
  scene_no    int,
  cut_id      uuid references public.production_cuts(id) on delete cascade,
  take_id     uuid references public.production_takes(id) on delete cascade,
  payload     jsonb not null default '{}',
  platform    text check (platform in ('mac','windows')),   -- 비어 있으면 아무 에이전트나
  status      text not null default 'queued' check (status in ('queued','running','done','failed','cancelled')),
  device_id   uuid references public.devices(id) on delete set null,
  progress    text,
  result      text,
  error       text,
  attempts    int not null default 0,
  not_before  timestamptz not null default now(),
  created_at  timestamptz not null default now(),
  started_at  timestamptz,
  finished_at timestamptz
);
create index if not exists pipeline_tasks_open on public.pipeline_tasks(status, kind, not_before);
create index if not exists pipeline_tasks_ep on public.pipeline_tasks(episode_id, created_at desc);

-- 5) 저장소 출입증: 에이전트가 정해진 파일 하나만 잠시 올리고 받을 수 있게
create table if not exists public.storage_grants (
  bucket     text not null,
  path       text not null,
  mode       text not null check (mode in ('write','read')),
  expires_at timestamptz not null,
  primary key (bucket, path, mode)
);
alter table public.storage_grants enable row level security;   -- 정책 없음: 직접 접근 불가, 아래 함수만

create or replace function public.nyx_storage_granted(p_bucket text, p_path text, p_mode text) returns boolean
language sql stable security definer set search_path = public as $$
  select exists (select 1 from public.storage_grants
                 where bucket = p_bucket and path = p_path and mode = p_mode and expires_at > now());
$$;
grant execute on function public.nyx_storage_granted(text, text, text) to anon, authenticated;

insert into storage.buckets (id, name, public) values ('takes', 'takes', false) on conflict (id) do nothing;
drop policy if exists "nyx takes read"        on storage.objects;
drop policy if exists "nyx takes owner"       on storage.objects;
drop policy if exists "nyx grant write"       on storage.objects;
drop policy if exists "nyx grant read"        on storage.objects;
create policy "nyx takes read"  on storage.objects for select to authenticated using (bucket_id = 'takes' and public.nyx_is_member());
create policy "nyx takes owner" on storage.objects for delete to authenticated using (bucket_id = 'takes' and public.nyx_is_owner());
create policy "nyx grant write" on storage.objects for insert to anon
  with check (bucket_id in ('takes') and public.nyx_storage_granted(bucket_id, name, 'write'));
create policy "nyx grant read"  on storage.objects for select to anon
  using (bucket_id in ('takes', 'bible') and public.nyx_storage_granted(bucket_id, name, 'read'));

-- 6) 화면 권한
do $$
declare t text;
begin
  foreach t in array array['production_cuts','production_takes','scene_packages','pipeline_tasks'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists nyx_read on public.%I', t);
    execute format('drop policy if exists nyx_owner on public.%I', t);
    execute format('create policy nyx_read on public.%I for select to authenticated using (public.nyx_is_member())', t);
    execute format('create policy nyx_owner on public.%I for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner())', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;

create or replace function public.nyx_touch() returns trigger language plpgsql as $$
begin new.updated_at := now(); return new; end $$;
drop trigger if exists nyx_touch on public.production_cuts;
create trigger nyx_touch before update on public.production_cuts for each row execute function public.nyx_touch();
drop trigger if exists nyx_touch on public.production_takes;
create trigger nyx_touch before update on public.production_takes for each row execute function public.nyx_touch();

-- 7) 저장 폴더·생성 기본값 설정
insert into public.app_settings (key, value) values
  ('storage', jsonb_build_object('windows_folder', '%OneDrive%\NYX', 'mac_folder', '~/Movies/NYX', 'server', 'windows')),
  ('generation', jsonb_build_object('model', 'seedance-2.5', 'resolution', '480p', 'aspect_ratio', '9:16', 'audio', true,
                                    'max_auto_retries', 0, 'claude_writer', 'claude-opus-5-5', 'claude_director', 'claude-opus-5-5',
                                    'claude_reviewer', 'claude-sonnet-5-5'))
on conflict (key) do nothing;

-- 8) 단가(Higgsfield 공개 가격, 초당): 해상도별. 참고 이미지(reference)를 쓰면 더 비싸다.
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('higgsfield', 'seedance-2.5-t2v-480p',  'per_second', 0.1177, 'Seedance 2.5 글→영상 480p', 'Higgsfield 공개 가격 2026-11'),
  ('higgsfield', 'seedance-2.5-t2v-720p',  'per_second', 0.2647, 'Seedance 2.5 글→영상 720p', 'Higgsfield 공개 가격 2026-11'),
  ('higgsfield', 'seedance-2.5-t2v-1080p', 'per_second', 0.6518, 'Seedance 2.5 글→영상 1080p', 'Higgsfield 공개 가격 2026-11'),
  ('higgsfield', 'seedance-2.5-ref-480p',  'per_second', 0.2056, 'Seedance 2.5 참고이미지→영상 480p', 'Higgsfield 공개 가격 2026-11'),
  ('higgsfield', 'seedance-2.5-ref-720p',  'per_second', 0.4622, 'Seedance 2.5 참고이미지→영상 720p', 'Higgsfield 공개 가격 2026-11'),
  ('higgsfield', 'seedance-2.5-ref-1080p', 'per_second', 1.1372, 'Seedance 2.5 참고이미지→영상 1080p', 'Higgsfield 공개 가격 2026-11')
on conflict (provider, model, unit) do update set usd = excluded.usd, label = excluded.label, source = excluded.source;
update public.price_book set usd = 0.2647, label = 'Seedance 2.5 영상(720p 기준, 예전 항목)', source = 'Higgsfield 공개 가격 2026-11'
 where provider = 'higgsfield' and model = 'seedance-2.5' and unit = 'per_second';

-- 9) 에이전트용 함수
create or replace function public.agent_claim_task(p_token text, p_kinds text[], p_platform text) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid; v jsonb;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  if (select paused from public.agent_status where device_id = v_dev) then return null; end if;
  -- 오래 멈춘 작업(에이전트가 꺼짐)은 다시 대기열로
  update public.pipeline_tasks set status = 'queued', device_id = null, progress = '이전 실행이 끊겨 다시 시도'
   where status = 'running' and started_at < now() - interval '45 minutes' and attempts < 3;
  select id into v_id from public.pipeline_tasks
   where status = 'queued' and kind = any(p_kinds) and not_before <= now()
     and (platform is null or platform = p_platform)
   order by case kind when 'store' then 0 when 'video_review' then 1 else 2 end, created_at
   for update skip locked limit 1;
  if v_id is null then return null; end if;
  update public.pipeline_tasks set status = 'running', device_id = v_dev, started_at = now(), attempts = attempts + 1
   where id = v_id returning to_jsonb(pipeline_tasks.*) into v;
  update public.agent_status set current_job = v->>'kind' where device_id = v_dev;
  return v;
end $$;

create or replace function public.agent_task_update(p_token text, p_id uuid, p_status text, p_progress text default null,
                                                    p_result text default null, p_error text default null, p_retry_in int default null)
returns void language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token);
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  update public.pipeline_tasks set
    status = case when p_retry_in is not null then 'queued' else coalesce(p_status, status) end,
    progress = coalesce(p_progress, progress), result = coalesce(left(p_result, 4000), result), error = left(p_error, 2000),
    not_before = case when p_retry_in is not null then now() + make_interval(secs => p_retry_in) else not_before end,
    device_id = case when p_retry_in is not null then null else device_id end,
    finished_at = case when p_status in ('done','failed') and p_retry_in is null then now() else finished_at end
  where id = p_id;
  if p_status in ('done','failed') or p_retry_in is not null then
    update public.agent_status set current_job = null where device_id = v_dev;
  end if;
end $$;

-- 에이전트가 이어서 만드는 작업. 돈이 드는 'generate'는 대표만 만들 수 있다.
create or replace function public.agent_add_task(p_token text, p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  if p->>'kind' = 'generate' then raise exception '영상 생성은 대표 승인으로만 만들 수 있습니다'; end if;
  insert into public.pipeline_tasks (kind, episode_id, scene_no, cut_id, take_id, payload, platform, not_before)
  values (p->>'kind', (p->>'episode_id')::uuid, (p->>'scene_no')::int, (p->>'cut_id')::uuid, (p->>'take_id')::uuid,
          coalesce(p->'payload', '{}'::jsonb), p->>'platform', now() + make_interval(secs => coalesce((p->>'delay')::int, 0)))
  returning id into v_id;
  return v_id;
end $$;

-- 일에 필요한 자료를 한 번에: 화·시리즈·대본·지침·바이블·연출 메모·교훈·설정
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
                      'description', bv.description, 'details', bv.details, 'images', bv.image_paths) order by be.category, be.sort_order), '[]')
              from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
              where be.series_id = v_ser),
    'notes', (select coalesce(jsonb_agg(jsonb_build_object('scene_no', n.scene_no, 'priority', n.priority, 'important', n.important,
                      'content', n.content) order by n.scene_no nulls first), '[]')
              from public.episode_notes n where n.episode_id = p_episode and n.status <> 'closed'),
    'lessons', public.agent_get_lessons(p_token, v_ser, false),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings));
end $$;

-- 작가실 정리본 자동 확정(설정이 켜져 있고 흐름 제안이 없을 때만 에이전트가 부른다)
create or replace function public.agent_confirm_script(p_token text, p_id uuid, p_summary text) returns int
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_ep uuid; v_no int;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  if coalesce((select value from public.app_settings where key = 'writers_room_auto_confirm'), 'false'::jsonb) <> 'true'::jsonb then
    raise exception '정리본 자동 확정이 꺼져 있습니다';
  end if;
  select episode_id into v_ep from public.script_versions where id = p_id and status = 'draft';
  if v_ep is null then raise exception '초안이 아닙니다'; end if;
  select coalesce(max(version_no), 0) + 1 into v_no from public.script_versions where episode_id = v_ep;
  update public.script_versions set status = 'superseded' where episode_id = v_ep and status = 'confirmed';
  update public.script_versions set status = 'confirmed', version_no = v_no, confirmed_at = now(),
         change_summary = left(p_summary, 2000) where id = p_id;
  begin
    insert into public.notifications (title, body, level, link_tab)
    values ('작가실 정리본 확정: v' || v_no, left(p_summary, 300), 'info', 'production');
  exception when others then null;
  end;
  perform public.nyx_log('작가실', '정리본 자동 확정 v' || v_no, p_id, v_dev);
  return v_no;
end $$;

-- 감독: 한 씬의 컷 설계 저장(아직 승인 전인 컷만 바꾼다)
create or replace function public.agent_save_cuts(p_token text, p_episode uuid, p_version uuid, p_scene int, p_cuts jsonb) returns int
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); c jsonb; n int := 0;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  delete from public.production_cuts where episode_id = p_episode and scene_no = p_scene and status in ('designed','cancelled')
     and not exists (select 1 from public.production_takes t where t.cut_id = production_cuts.id);
  for c in select * from jsonb_array_elements(p_cuts) loop
    if exists (select 1 from public.production_cuts where episode_id = p_episode and scene_no = p_scene and cut_no = (c->>'cut_no')::int) then
      continue;   -- 이미 승인·생성된 컷은 그대로 둔다
    end if;
    insert into public.production_cuts (episode_id, script_version_id, scene_no, cut_no, code, scene_heading, seconds, summary, dialogue,
      emotion, expression_beats, prompt, reference_names, camera, director_note, review, resolution, est_usd)
    values (p_episode, p_version, p_scene, (c->>'cut_no')::int, c->>'code', c->>'scene_heading',
      least(15, greatest(4, coalesce((c->>'seconds')::int, 10))), c->>'summary', c->>'dialogue', c->>'emotion',
      coalesce(c->'expression_beats', '[]'), c->>'prompt',
      coalesce(array(select jsonb_array_elements_text(c->'reference_names')), '{}'), c->>'camera', c->>'director_note',
      c->'review', coalesce(c->>'resolution', '480p'), (c->>'est_usd')::numeric);
    n := n + 1;
  end loop;
  insert into public.scene_packages (episode_id, scene_no, heading)
  values (p_episode, p_scene, (select c2->>'scene_heading' from jsonb_array_elements(p_cuts) c2 limit 1))
  on conflict (episode_id, scene_no) do update set heading = coalesce(excluded.heading, scene_packages.heading), updated_at = now();
  perform public.nyx_log('감독', p_scene || '씬 컷 ' || n || '개 설계', p_episode, v_dev);
  return n;
end $$;

-- 컷·테이크·씬 묶음 고치기(정해진 칸만)
create or replace function public.agent_update_cut(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.production_cuts set
    status = coalesce(p->>'status', status), prompt = coalesce(p->>'prompt', prompt), review = coalesce(p->'review', review),
    director_note = coalesce(p->>'director_note', director_note), best_take_id = coalesce((p->>'best_take_id')::uuid, best_take_id),
    expression_beats = coalesce(p->'expression_beats', expression_beats), est_usd = coalesce((p->>'est_usd')::numeric, est_usd),
    reference_names = case when p ? 'reference_names' then array(select jsonb_array_elements_text(p->'reference_names')) else reference_names end,
    fix_request = case when p ? 'clear_fix' then null else fix_request end
  where id = p_id;
end $$;

create or replace function public.agent_save_take(p_token text, p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid := (p->>'id')::uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  if v_id is null then
    insert into public.production_takes (cut_id, take_no, model, resolution, seconds, request_id, status, usd, cost_event_id)
    values ((p->>'cut_id')::uuid,
            coalesce((select max(take_no) from public.production_takes where cut_id = (p->>'cut_id')::uuid), 0) + 1,
            p->>'model', p->>'resolution', (p->>'seconds')::int, p->>'request_id', coalesce(p->>'status', 'submitted'),
            (p->>'usd')::numeric, (p->>'cost_event_id')::uuid)
    returning id into v_id;
  else
    update public.production_takes set
      status = coalesce(p->>'status', status), request_id = coalesce(p->>'request_id', request_id),
      remote_url = coalesce(p->>'remote_url', remote_url),
      files = case when p ? 'files' then files || (p->'files') else files end,
      review = coalesce(p->'review', review), score = coalesce((p->>'score')::numeric, score), verdict = coalesce(p->>'verdict', verdict),
      usd = coalesce((p->>'usd')::numeric, usd), cost_event_id = coalesce((p->>'cost_event_id')::uuid, cost_event_id), error = p->>'error'
    where id = v_id;
  end if;
  return v_id;
end $$;

create or replace function public.agent_get_take(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(t) || jsonb_build_object('cut', to_jsonb(c), 'episode', jsonb_build_object('number', e.number, 'title', e.title, 'series_id', e.series_id),
                   'series_title', (select title from public.series where id = e.series_id),
                   'package', (select to_jsonb(sp) from public.scene_packages sp where sp.episode_id = c.episode_id and sp.scene_no = c.scene_no))
          from public.production_takes t join public.production_cuts c on c.id = t.cut_id join public.episodes e on e.id = c.episode_id
          where t.id = p_id);
end $$;

create or replace function public.agent_get_cut(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(c) || jsonb_build_object('episode', jsonb_build_object('number', e.number, 'title', e.title, 'series_id', e.series_id),
                   'takes', coalesce((select jsonb_agg(to_jsonb(t) order by t.take_no) from public.production_takes t where t.cut_id = c.id), '[]'),
                   'scene_cuts', coalesce((select jsonb_agg(jsonb_build_object('cut_no', x.cut_no, 'summary', x.summary, 'dialogue', x.dialogue, 'seconds', x.seconds) order by x.cut_no)
                                           from public.production_cuts x where x.episode_id = c.episode_id and x.scene_no = c.scene_no), '[]'))
          from public.production_cuts c join public.episodes e on e.id = c.episode_id where c.id = p_id);
end $$;

create or replace function public.agent_save_package(p_token text, p_episode uuid, p_scene int, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  insert into public.scene_packages (episode_id, scene_no, folder, bgm)
  values (p_episode, p_scene, coalesce(p->'folder', '{}'), coalesce(p->'bgm', '{}'))
  on conflict (episode_id, scene_no) do update set
    folder = scene_packages.folder || coalesce(p->'folder', '{}'), bgm = scene_packages.bgm || coalesce(p->'bgm', '{}'), updated_at = now();
end $$;

-- 저장소 출입증(15분)
create or replace function public.agent_storage_grant(p_token text, p_bucket text, p_path text, p_mode text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if p_bucket not in ('takes', 'bible') or p_mode not in ('write', 'read') or (p_bucket = 'bible' and p_mode = 'write') then
    raise exception '허용되지 않은 저장소 요청';
  end if;
  delete from public.storage_grants where expires_at < now() - interval '1 day';
  insert into public.storage_grants (bucket, path, mode, expires_at) values (p_bucket, p_path, p_mode, now() + interval '15 minutes')
  on conflict (bucket, path, mode) do update set expires_at = excluded.expires_at;
end $$;

create or replace function public.agent_has_platform(p_token text, p_platform text) returns boolean
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return exists (select 1 from public.agent_status where platform = p_platform and coalesce(state, '') not in ('removed', 'not_installed'));
end $$;

create or replace function public.agent_notify(p_token text, p_title text, p_body text, p_level text default 'info') returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  begin
    insert into public.notifications (title, body, level, link_tab) values (left(p_title, 120), left(p_body, 500), p_level, 'production');
  exception when others then null;
  end;
end $$;

do $$
declare f text;
begin
  foreach f in array array[
    'agent_claim_task(text, text[], text)', 'agent_task_update(text, uuid, text, text, text, text, int)', 'agent_add_task(text, jsonb)',
    'agent_context(text, uuid, uuid)', 'agent_confirm_script(text, uuid, text)', 'agent_save_cuts(text, uuid, uuid, int, jsonb)',
    'agent_update_cut(text, uuid, jsonb)', 'agent_save_take(text, jsonb)', 'agent_get_take(text, uuid)', 'agent_get_cut(text, uuid)',
    'agent_save_package(text, uuid, int, jsonb)', 'agent_storage_grant(text, text, text, text)', 'agent_has_platform(text, text)',
    'agent_notify(text, text, text, text)'] loop
    execute format('revoke all on function public.%s from public', f);
    execute format('grant execute on function public.%s to anon, authenticated', f);
  end loop;
end $$;
