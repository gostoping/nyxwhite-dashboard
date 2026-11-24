-- 여덟 번째 직원: 코디네이터
-- 대본에서 나온 바이블 항목(인물·장소·배경·소품·스타일·배경음악)의 묘사·영어 프롬프트를 준비하고 감수한다.
-- 고쳐쓰기(대표 요청)가 묘사·영어 프롬프트에 정확히 반영됐는지, 묘사와 프롬프트가 서로 맞는지, 대본과 어긋나지 않는지 확인하고
-- 결과를 bible_reviews에 남긴다(바이블 카드에 표시). 영어 프롬프트가 빠뜨린 것은 직접 보정하고, 묘사 자체를 바꿔야 하면 대표에게 넘긴다.

-- 1) 직원 등록
do $staff$
declare cols text[]; c text := 'name'; v text := quote_literal('코디네이터');
begin
  if exists (select 1 from public.staff where name = '코디네이터') then return; end if;
  select array_agg(column_name::text) into cols from information_schema.columns where table_schema = 'public' and table_name = 'staff';
  if exists (select 1 from pg_constraint where conname = 'staff_code_check' and conrelid = 'public.staff'::regclass) then
    execute 'alter table public.staff drop constraint staff_code_check';
    execute (select format('alter table public.staff add constraint staff_code_check check (code in (%s))', string_agg(quote_literal(x), ', '))
               from (select distinct code as x from public.staff where code is not null union select 'coordinator') t);
  end if;
  if 'code' = any(cols) then c := c || ', code'; v := v || ', ' || quote_literal('coordinator'); end if;
  if 'role_summary' = any(cols) then c := c || ', role_summary';
    v := v || ', ' || quote_literal('바이블 항목의 묘사·영어 프롬프트를 준비·감수합니다. 고쳐쓰기 요청이 정확히 반영됐는지 확인해 카드에 기록하고, 프롬프트가 빠뜨린 것은 보정합니다.'); end if;
  if 'sort_order' = any(cols) then c := c || ', sort_order'; v := v || ', 0'; end if;
  execute format('insert into public.staff (%s) values (%s)', c, v);
end
$staff$;

do $g$
declare v_staff uuid;
begin
  select id into v_staff from public.staff where name = '코디네이터' limit 1;
  if v_staff is null or exists (select 1 from public.staff_guidelines where staff_id = v_staff) then return; end if;
  insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (v_staff, 1, $w$[코디네이터 지침 v1: 바이블 프롬프트 준비·감수]

■ 맡은 일
- 시리즈 바이블 항목(인물·장소·배경·소품·스타일·배경음악)이 적용될 때마다 감수한다.
- 대표가 고쳐쓰기에서 '바꾸는 이유·바꿀 내용'에 적은 요청이 한글 묘사와 영어 프롬프트에 정확히 들어갔는지 항목별로 확인한다.
- 영어 프롬프트가 한글 묘사·세부 사항의 고정 특징을 빠짐없이, 서로 어긋나지 않게 담았는지 확인한다.
- 대본에서 그 항목이 나오는 부분과 어긋나는 것(나이·성별·머리색·장소 구조 등)이 없는지 확인한다.

■ 판단과 처리
- 모두 맞으면 '반영 확인'.
- 영어 프롬프트만 빠뜨렸거나 틀렸으면 직접 고친다(묘사는 손대지 않음) → '프롬프트 보정'.
- 한글 묘사 자체가 요청과 다르거나 대본과 부딪히면 고치지 않고 '대표 확인 필요'로 넘기고, 무엇을 어떻게 고치면 되는지 한국어 한 줄로 적는다.
- 항목마다 근거를 단다: 요청 문구, 묘사 문구, 프롬프트 문구, 대본 화·씬.$w$, '코디네이터 신설', 'proposed');
end
$g$;

-- 2) 감수 기록
create table if not exists public.bible_reviews (
  id            uuid primary key default gen_random_uuid(),
  series_id     uuid references public.series(id) on delete cascade,
  entry_id      uuid not null references public.bible_entries(id) on delete cascade,
  version_id    uuid references public.bible_versions(id) on delete set null,
  version_no    int,
  verdict       text not null check (verdict in ('pass', 'fixed', 'owner')),   -- 반영 확인 | 프롬프트 보정 | 대표 확인 필요
  request       text,              -- 대표가 고쳐쓰기에 적은 요청(있으면)
  checks        jsonb not null default '[]',   -- [{item, status: ok|missing|conflict|fixed, where, note}]
  prompt_before text,
  prompt_after  text,
  owner_note    text,
  summary       text,
  photo_redo    boolean not null default false,   -- 기준 사진을 다시 만들기 추천
  photo_issues  jsonb not null default '[]',
  device_id     uuid references public.devices(id) on delete set null,
  created_at    timestamptz not null default now()
);
alter table public.bible_reviews add column if not exists photo_redo boolean not null default false;
alter table public.bible_reviews add column if not exists photo_issues jsonb not null default '[]';
create index if not exists bible_reviews_entry on public.bible_reviews(entry_id, created_at desc);
alter table public.bible_reviews enable row level security;
drop policy if exists nyx_read on public.bible_reviews;
drop policy if exists nyx_owner on public.bible_reviews;
create policy nyx_read on public.bible_reviews for select to authenticated using (public.nyx_is_member());
create policy nyx_owner on public.bible_reviews for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());

-- 3) 작업 종류
alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe','bible_place','bible_prompt','api_check','bible_suggest','upscale',
                  'coord_review'));

-- 4) 에이전트용: 감수 자료(지금 버전 + 바로 앞 버전) / 기록 저장
create or replace function public.agent_coord_info(p_token text, p_version uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select jsonb_build_object('version_id', bv.id, 'version_no', bv.version_no, 'status', bv.status, 'description', bv.description, 'details', bv.details,
                 'prompt_en', bv.prompt_en, 'change_reason', bv.change_reason, 'proposed_by', bv.proposed_by,
                 'image_paths', to_jsonb(coalesce(bv.image_paths, '{}'::text[])),
                 'entry_id', be.id, 'name', be.name, 'category', be.category, 'summary', be.summary, 'series_id', be.series_id, 'series', s.title,
                 'prev', (select jsonb_build_object('version_no', pv.version_no, 'description', pv.description, 'details', pv.details, 'prompt_en', pv.prompt_en)
                            from public.bible_versions pv where pv.entry_id = bv.entry_id and pv.version_no < bv.version_no and pv.status <> 'proposed'
                           order by pv.version_no desc limit 1),
                 'prompt_pending', exists (select 1 from public.pipeline_tasks t where t.kind = 'bible_prompt' and t.status in ('queued', 'running')
                                            and t.payload->>'version_id' = bv.id::text))
            from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id join public.series s on s.id = be.series_id
           where bv.id = p_version);
end $$;
revoke all on function public.agent_coord_info(text, uuid) from public;
grant execute on function public.agent_coord_info(text, uuid) to anon, authenticated;

create or replace function public.agent_bible_review_save(p_token text, p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  insert into public.bible_reviews (series_id, entry_id, version_id, version_no, verdict, request, checks, prompt_before, prompt_after, owner_note, summary,
                                    photo_redo, photo_issues, device_id)
  values ((p->>'series_id')::uuid, (p->>'entry_id')::uuid, (p->>'version_id')::uuid, (p->>'version_no')::int, p->>'verdict', p->>'request',
          coalesce(p->'checks', '[]'), p->>'prompt_before', p->>'prompt_after', p->>'owner_note', left(p->>'summary', 500),
          coalesce((p->>'photo_redo')::boolean, false), coalesce(p->'photo_issues', '[]'), v_dev)
  returning id into v_id;
  return v_id;
end $$;
revoke all on function public.agent_bible_review_save(text, jsonb) from public;
grant execute on function public.agent_bible_review_save(text, jsonb) to anon, authenticated;

-- 5) 바이블 항목이 적용되면 코디네이터가 감수(같은 항목의 대기 중 감수가 있으면 그 감수를 새 버전으로 옮김)
create or replace function public.nyx_coord_queue() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_t uuid;
begin
  if new.status = 'active' and (TG_OP = 'INSERT' or old.status is distinct from 'active') then
    select t.id into v_t from public.pipeline_tasks t where t.kind = 'coord_review' and t.status = 'queued' and t.payload->>'entry_id' = new.entry_id::text limit 1;
    if v_t is not null then
      update public.pipeline_tasks set payload = payload || jsonb_build_object('version_id', new.id) where id = v_t;
    else
      insert into public.pipeline_tasks (kind, series_id, payload, not_before)
      select 'coord_review', be.series_id, jsonb_build_object('version_id', new.id, 'entry_id', be.id), now() + interval '20 seconds'
        from public.bible_entries be where be.id = new.entry_id
         and be.category in ('character', 'location', 'background', 'prop', 'style', 'music');
    end if;
  end if;
  return null;
end $$;
drop trigger if exists nyx_coord_queue on public.bible_versions;
create trigger nyx_coord_queue after insert or update on public.bible_versions for each row execute function public.nyx_coord_queue();

-- 6) 지금 적용 중인 항목은 한 번씩 감수(처음 감수 기록 만들기). 대표 요청이 있었던 항목부터.
insert into public.pipeline_tasks (kind, series_id, payload, not_before)
select 'coord_review', be.series_id, jsonb_build_object('version_id', bv.id, 'entry_id', be.id), now() + interval '5 minutes'
  from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id
 where bv.status = 'active' and be.category in ('character', 'location', 'background', 'prop', 'style', 'music')
   and not exists (select 1 from public.pipeline_tasks t where t.kind = 'coord_review' and t.status in ('queued', 'running') and t.payload->>'entry_id' = be.id::text)
 order by (coalesce(bv.change_reason, '') <> '') desc;
grant select, insert, update, delete on public.bible_reviews to authenticated;

-- 7) 사진 '새로 만들기' 순서: 그 항목의 영어 프롬프트 반영(작가실)·감수(코디네이터)가 진행 중이면 그게 끝난 뒤에 만든다.
--    대표가 묘사 칸을 손대지 않고 누르면(use_bible) 만들 때의 최신 영어 프롬프트를 쓴다.
alter table public.photo_designs add column if not exists use_bible boolean not null default false;
create or replace function public.agent_get_photo_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'category', e.category, 'summary', e.summary, 'series_title', s.title,
                   'description', bv.description, 'details', bv.details, 'images', coalesce(bv.image_paths, '{}'), 'cur_soul_id', bv.soul_id, 'prompt_en', bv.prompt_en,
                   'prompt_pending', exists (select 1 from public.pipeline_tasks t where t.kind in ('bible_prompt', 'coord_review') and t.status in ('queued', 'running')
                                              and t.payload->>'entry_id' = e.id::text),
                   'generation', (select value from public.app_settings where key = 'generation'))
          from public.photo_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          left join public.bible_versions bv on bv.entry_id = e.id and bv.status = 'active'
          where d.id = p_id);
end $$;
