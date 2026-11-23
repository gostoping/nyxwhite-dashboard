-- 감독 노하우(교훈) 기록: 감수자·평가자·대표에게 받은 지적을 쌓아 다음 작업에 먼저 적용한다.
-- 지침·바이블이 항상 우선이고, 교훈은 그 아래의 "경험 메모"다. 대표는 언제든 고치거나 끌 수 있다.
create table if not exists public.director_lessons (
  id           uuid primary key default gen_random_uuid(),
  series_id    uuid references public.series(id) on delete cascade,   -- 비어 있으면 모든 시리즈 공통
  fingerprint  text not null,              -- 같은 교훈을 합치는 열쇠(예: "costume:white-shirt-logo")
  category     text not null default 'other' check (category in
                 ('face','costume','location','prop','continuity','expression','emotion_tone','dialogue','camera','motion','prompt','localization','other')),
  problem      text not null,              -- 무엇이 잘못됐었나
  rule         text not null,              -- 앞으로 이렇게 한다 (감독이 그대로 따를 문장)
  bad_example  text,                       -- 실패한 프롬프트·표현
  good_example text,                       -- 통과한 프롬프트·표현
  related      text[] not null default '{}',   -- 관련 바이블 항목·인물·장소 이름
  sources      text[] not null default '{}',   -- 대본감수자 / 영상감수자 / 최종감수자 / 현지화감수자 / 평가자 / 대표
  cut_refs     text[] not null default '{}',   -- 근거 컷 ID
  hit_count    int not null default 1,     -- 같은 문제가 나온 횟수
  applied_ok   int not null default 0,     -- 이 교훈을 적용해 통과한 횟수
  status       text not null default 'candidate' check (status in ('candidate','active','retired')),
  promoted     boolean not null default false,  -- 지침 수정안으로 올라갔는지
  created_at   timestamptz not null default now(),
  last_seen_at timestamptz not null default now(),
  updated_at   timestamptz not null default now(),
  unique (series_id, fingerprint)
);
create index if not exists director_lessons_status on public.director_lessons(status, series_id);

-- 에이전트용: 교훈 기록(같은 열쇠면 합치고 횟수를 올림), 통과 기록, 읽기
create or replace function public.agent_add_lesson(p_token text, p_lesson jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid; v_hits int; v_status text; v_series uuid := (p_lesson->>'series_id')::uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  select id into v_id from public.director_lessons
   where fingerprint = p_lesson->>'fingerprint' and series_id is not distinct from v_series;
  if v_id is null then
    insert into public.director_lessons (series_id, fingerprint, category, problem, rule, bad_example, good_example, related, sources, cut_refs, status)
    values (v_series, p_lesson->>'fingerprint', coalesce(p_lesson->>'category', 'other'), p_lesson->>'problem', p_lesson->>'rule',
            p_lesson->>'bad_example', p_lesson->>'good_example',
            coalesce(array(select jsonb_array_elements_text(p_lesson->'related')), '{}'),
            coalesce(array(select jsonb_array_elements_text(p_lesson->'sources')), '{}'),
            coalesce(array(select jsonb_array_elements_text(p_lesson->'cut_refs')), '{}'),
            case when p_lesson->>'severity' = '치명' then 'active' else 'candidate' end)
    returning id, hit_count, status into v_id, v_hits, v_status;
  else
    update public.director_lessons set
      hit_count = hit_count + 1, last_seen_at = now(), updated_at = now(),
      rule = coalesce(nullif(p_lesson->>'rule', ''), rule),
      good_example = coalesce(nullif(p_lesson->>'good_example', ''), good_example),
      sources = (select array(select distinct unnest(sources || coalesce(array(select jsonb_array_elements_text(p_lesson->'sources')), '{}')))),
      cut_refs = (select array(select distinct unnest(cut_refs || coalesce(array(select jsonb_array_elements_text(p_lesson->'cut_refs')), '{}')))),
      -- 같은 문제가 두 번 나오면 자동으로 '적용 중'이 된다(대표가 꺼 둔 것은 그대로 둔다)
      status = case when status = 'candidate' and hit_count + 1 >= 2 then 'active' else status end
    where id = v_id returning hit_count, status into v_hits, v_status;
  end if;
  perform public.nyx_log('평가자', '감독 교훈 ' || case when v_hits = 1 then '추가' else '반복(' || v_hits || '회)' end || ': ' || left(p_lesson->>'rule', 60), v_id, v_dev);
  return jsonb_build_object('id', v_id, 'hit_count', v_hits, 'status', v_status);
end $$;

create or replace function public.agent_lesson_worked(p_token text, p_ids uuid[]) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.director_lessons set applied_ok = applied_ok + 1, updated_at = now() where id = any(p_ids);
end $$;

create or replace function public.agent_get_lessons(p_token text, p_series uuid, p_include_candidates boolean default false) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return coalesce((select jsonb_agg(to_jsonb(l) order by l.hit_count desc, l.last_seen_at desc)
    from public.director_lessons l
    where (l.series_id is null or l.series_id = p_series)
      and (l.status = 'active' or (p_include_candidates and l.status = 'candidate'))), '[]'::jsonb);
end $$;

revoke all on function public.agent_add_lesson(text, jsonb) from public;
revoke all on function public.agent_lesson_worked(text, uuid[]) from public;
revoke all on function public.agent_get_lessons(text, uuid, boolean) from public;
grant execute on function public.agent_add_lesson(text, jsonb) to anon, authenticated;
grant execute on function public.agent_lesson_worked(text, uuid[]) to anon, authenticated;
grant execute on function public.agent_get_lessons(text, uuid, boolean) to anon, authenticated;

alter table public.director_lessons enable row level security;
drop policy if exists nyx_read on public.director_lessons;
drop policy if exists nyx_owner on public.director_lessons;
create policy nyx_read on public.director_lessons for select to authenticated using (public.nyx_is_member());
create policy nyx_owner on public.director_lessons for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner());
grant select, insert, update, delete on public.director_lessons to authenticated;

-- 지침: 감독(교훈 읽고 적용), 평가자(교훈 정리), 감수자들(교훈 위반 확인)
do $outer$
declare r record; v_staff uuid; v_latest record; v_no int;
  v_reason constant text := '감독 노하우(교훈) 기록: 피드백을 쌓아 다음 작업에 먼저 적용';
begin
  for r in select * from (values
('감독', $g$

[감독 노하우(교훈) 기록]
- 작업을 시작하기 전에, 이 시리즈와 공통의 '적용 중' 교훈을 모두 읽는다. 이번 컷의 인물·장소·장면과 관련된 교훈은 반드시 적용한다.
- 컷 카드에 '적용한 교훈' 칸을 두고 교훈 ID와 어떻게 적용했는지 적는다.
- 교훈은 지침과 바이블 아래다. 부딪히면 지침·바이블을 따르고 "교훈 충돌"로 평가자에게 알린다.
- 감수자의 지적을 반영해 통과하면, 무엇을 바꿔서 통과했는지(실패한 표현 → 통과한 표현)를 응답표에 남긴다. 평가자가 이것을 교훈으로 정리한다.
- 같은 실수를 다시 하지 않는 것이 감독의 핵심 평가 기준이다. '적용 중' 교훈과 같은 문제로 다시 지적받으면 그 사실을 응답표에 먼저 적는다.$g$),
('평가자', $g$

[감독 교훈 정리]
- 컷 판정이 끝날 때마다 대본감수자·영상감수자·최종감수자·현지화감수자의 지적 중 감독이 고쳐서 해결된 것을 교훈으로 정리한다.
- 교훈 한 건: 분류(얼굴·의상·장소·소품·연속성·표정·감정톤·대사·카메라·움직임·프롬프트·현지화) / 문제 / 앞으로의 규칙(감독이 그대로 따를 한 문장) / 실패한 표현 / 통과한 표현 / 관련 인물·장소 / 근거 컷 ID / 출처 감수자.
- 같은 문제는 같은 열쇠(예: costume:흰셔츠-로고)로 기록해 합친다. 같은 문제가 두 번 나오면 자동으로 '적용 중'이 된다. 치명 문제는 처음부터 '적용 중'.
- 특정 시리즈에만 해당하면 그 시리즈 교훈으로, 어느 작품에나 해당하면 공통 교훈으로 기록한다.
- 교훈을 적용한 컷이 통과하면 "효과 있음"을 기록한다.
- 한 교훈이 3번 넘게 반복되면 지침 수정안으로 올린다(대표 결정).
- 화 단위 요약에 새로 생긴 교훈, 반복된 교훈, 감독의 첫 시도 합격률 변화를 적는다.$g$),
('대본감수자', $g$

[교훈 확인]
- 컷 카드의 '적용한 교훈' 칸을 확인한다. 이 컷과 관련된 '적용 중' 교훈이 빠졌으면 '중요'로 지적한다.$g$),
('영상감수자', $g$

[교훈 확인]
- 지적한 문제가 '적용 중' 교훈과 같은 문제면 교훈 ID를 함께 적는다. 반복 실수는 평가자가 따로 관리한다.$g$)
  ) as t(name, addon)
  loop
    select id into v_staff from public.staff where name = r.name limit 1;
    if v_staff is null then raise notice '직원 없음: %', r.name; continue; end if;
    select id, status, content into v_latest from public.staff_guidelines
     where staff_id = v_staff and status in ('proposed','active') order by (status = 'proposed') desc, version_no desc limit 1;
    if v_latest.id is null then raise notice '기존 지침 없음: %', r.name; continue; end if;
    if position(split_part(r.addon, E'\n', 3) in v_latest.content) > 0 then raise notice '이미 반영: %', r.name; continue; end if;
    if v_latest.status = 'proposed' then
      update public.staff_guidelines set content = content || r.addon, change_reason = coalesce(change_reason, '') || ' + ' || v_reason where id = v_latest.id;
      raise notice '수정안에 반영: %', r.name;
    else
      select coalesce(max(version_no), 0) + 1 into v_no from public.staff_guidelines where staff_id = v_staff;
      insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (v_staff, v_no, v_latest.content || r.addon, v_reason, 'proposed');
      raise notice '새 수정안: % v%', r.name, v_no;
    end if;
  end loop;
end
$outer$;
