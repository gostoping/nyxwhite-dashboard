-- 다국어: 언어별 등급(마스터/립싱크/더빙/자막), 바이블 용어집, 현지화 비용, 현지화감수자

-- 1) 언어 목록과 등급
create table if not exists public.languages (
  code       text primary key,            -- ko, en, ja, zh-Hans
  name       text not null,
  tier       text not null check (tier in ('master','lipsync','dub','subtitle','off')),
  sort_order int not null default 0,
  note       text,
  updated_at timestamptz not null default now()
);
insert into public.languages (code, name, tier, sort_order, note) values
  ('ko', '한국어', 'master', 1, '원본. 모든 언어는 한국어 확정 대사에서 옮긴다'),
  ('en', '영어', 'lipsync', 2, '매출 최우선 시장'),
  ('ja', '일본어', 'dub', 3, '얼굴이 크게 나오는 대사 컷은 립싱크 검토'),
  ('zh-Hans', '중국어(간체)', 'dub', 4, '번체(대만·홍콩) 필요 여부 확인')
on conflict (code) do nothing;

-- 2) 바이블에 '용어집' 분류 추가 (이름 표기, 호칭, 고유명사를 언어별로)
alter table public.bible_entries drop constraint if exists bible_entries_category_check;
alter table public.bible_entries add constraint bible_entries_category_check
  check (category in ('character','location','prop','style','voice','glossary'));

-- 3) 비용 장부에 '현지화' 단계 추가
alter table public.cost_events drop constraint if exists cost_events_stage_check;
alter table public.cost_events add constraint cost_events_stage_check check (stage in (
  'script_revision','cut_card','prompt_review','keyframe','keyframe_review','video','video_review',
  'final_review','evaluate','upscale','subtitle','audio','edit','directive','localization','fixed','other'));

alter table public.languages enable row level security;
drop policy if exists nyx_read on public.languages;
drop policy if exists nyx_owner on public.languages;
create policy nyx_read on public.languages for select to authenticated using (public.nyx_is_member());
create policy nyx_owner on public.languages for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());
grant select, insert, update, delete on public.languages to authenticated;

-- 4) 여섯 번째 직원: 현지화감수자 (staff 표의 칸 구성에 맞춰 넣는다)
do $staff$
declare cols text[]; c text := 'name'; v text := quote_literal('현지화감수자');
begin
  if exists (select 1 from public.staff where name = '현지화감수자') then return; end if;
  select array_agg(column_name::text) into cols from information_schema.columns where table_schema = 'public' and table_name = 'staff';
  -- 직원 코드에 허용 목록 규칙이 있으면 새 코드를 목록에 더한다
  if exists (select 1 from pg_constraint where conname = 'staff_code_check' and conrelid = 'public.staff'::regclass) then
    execute 'alter table public.staff drop constraint staff_code_check';
    execute (select format('alter table public.staff add constraint staff_code_check check (code in (%s))', string_agg(quote_literal(x), ', '))
               from (select distinct code as x from public.staff where code is not null union select 'localization_reviewer') t);
  end if;
  if 'code' = any(cols) then c := c || ', code'; v := v || ', ' || quote_literal('localization_reviewer'); end if;
  if 'role_summary' = any(cols) then c := c || ', role_summary';
    v := v || ', ' || quote_literal('한국어 확정 대사를 영어·일본어·중국어로 옮기고, 자막·더빙·립싱크 결과를 검사합니다.'); end if;
  if 'sort_order' = any(cols) then c := c || ', sort_order';
    v := v || ', (select coalesce(max(sort_order), 0) + 1 from public.staff)'; end if;
  execute format('insert into public.staff (%s) values (%s)', c, v);
end
$staff$;
