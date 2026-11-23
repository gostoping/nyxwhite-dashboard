-- 작가실: 대표 원고를 흐름은 그대로 두고 감정선·감정 연결 장치만 다듬어 제작용 정리본으로 올린다.
-- 흐름을 바꾸는 것은 '흐름 변경 제안'으로만 올리고 대표가 승인해야 반영된다.

-- 1) 앱 설정 (작가실 정리본 자동 확정 등)
create table if not exists public.app_settings (
  key        text primary key,
  value      jsonb not null,
  updated_at timestamptz not null default now()
);
insert into public.app_settings (key, value) values ('writers_room_auto_confirm', 'true'::jsonb) on conflict (key) do nothing;

-- 2) 흐름 변경 제안
create table if not exists public.story_proposals (
  id               uuid primary key default gen_random_uuid(),
  episode_id       uuid not null references public.episodes(id) on delete cascade,
  script_version_id uuid,
  scene_no         int,
  kind             text not null default 'other' check (kind in ('add_scene','remove_scene','reorder','event_timing','reveal','relationship','setting','ending','other')),
  current_text     text,                   -- 지금 원고
  proposed_text    text not null,          -- 제안
  reason           text not null,          -- 왜 (감정선·흐름 근거)
  impact           text,                   -- 영향 받는 화·장면
  status           text not null default 'proposed' check (status in ('proposed','approved','rejected','applied')),
  owner_note       text,                   -- 대표 의견
  decided_at       timestamptz,
  created_at       timestamptz not null default now()
);
create index if not exists story_proposals_episode on public.story_proposals(episode_id, status);

do $$
declare t text;
begin
  foreach t in array array['app_settings','story_proposals'] loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists nyx_read on public.%I', t);
    execute format('drop policy if exists nyx_owner on public.%I', t);
    execute format('create policy nyx_read on public.%I for select to authenticated using (public.nyx_is_member())', t);
    execute format('create policy nyx_owner on public.%I for all to authenticated using (public.nyx_is_owner()) with check (public.nyx_is_owner())', t);
    execute format('grant select, insert, update, delete on public.%I to authenticated', t);
  end loop;
end $$;

-- 3) 에이전트(작가실)용: 정리본 저장, 흐름 제안 올리기, 승인된 제안 읽기, 설정 읽기
create or replace function public.agent_save_script_draft(p_token text, p_episode uuid, p_content text, p_note text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  insert into public.script_versions (episode_id, content, note) values (p_episode, p_content, left(p_note, 500)) returning id into v_id;
  perform public.nyx_log('작가실', '정리본 초안 저장: ' || left(coalesce(p_note, ''), 80), v_id, v_dev);
  return v_id;
end $$;

create or replace function public.agent_add_story_proposal(p_token text, p jsonb) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_id uuid;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  insert into public.story_proposals (episode_id, script_version_id, scene_no, kind, current_text, proposed_text, reason, impact)
  values ((p->>'episode_id')::uuid, (p->>'script_version_id')::uuid, (p->>'scene_no')::int, coalesce(p->>'kind', 'other'),
          p->>'current_text', p->>'proposed_text', p->>'reason', p->>'impact')
  returning id into v_id;
  begin
    insert into public.notifications (title, body, level, link_tab)
    values ('작가실 흐름 변경 제안', left(p->>'proposed_text', 120), 'warning', 'scripts');
  exception when others then null;
  end;
  perform public.nyx_log('작가실', '흐름 변경 제안: ' || left(p->>'proposed_text', 80), v_id, v_dev);
  return v_id;
end $$;

create or replace function public.agent_writers_room_context(p_token text, p_episode uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return jsonb_build_object(
    'auto_confirm', coalesce((select value from public.app_settings where key = 'writers_room_auto_confirm'), 'false'::jsonb),
    'approved', coalesce((select jsonb_agg(to_jsonb(s)) from public.story_proposals s where s.episode_id = p_episode and s.status = 'approved'), '[]'::jsonb),
    'pending',  coalesce((select count(*) from public.story_proposals s where s.episode_id = p_episode and s.status = 'proposed'), 0));
end $$;

create or replace function public.agent_mark_proposal_applied(p_token text, p_ids uuid[]) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.story_proposals set status = 'applied' where id = any(p_ids) and status = 'approved';
end $$;

revoke all on function public.agent_save_script_draft(text, uuid, text, text) from public;
revoke all on function public.agent_add_story_proposal(text, jsonb) from public;
revoke all on function public.agent_writers_room_context(text, uuid) from public;
revoke all on function public.agent_mark_proposal_applied(text, uuid[]) from public;
grant execute on function public.agent_save_script_draft(text, uuid, text, text) to anon, authenticated;
grant execute on function public.agent_add_story_proposal(text, jsonb) to anon, authenticated;
grant execute on function public.agent_writers_room_context(text, uuid) to anon, authenticated;
grant execute on function public.agent_mark_proposal_applied(text, uuid[]) to anon, authenticated;

-- 4) 일곱 번째 직원: 작가실
do $staff$
declare cols text[]; c text := 'name'; v text := quote_literal('작가실');
begin
  if exists (select 1 from public.staff where name = '작가실') then return; end if;
  select array_agg(column_name::text) into cols from information_schema.columns where table_schema = 'public' and table_name = 'staff';
  if exists (select 1 from pg_constraint where conname = 'staff_code_check' and conrelid = 'public.staff'::regclass) then
    execute 'alter table public.staff drop constraint staff_code_check';
    execute (select format('alter table public.staff add constraint staff_code_check check (code in (%s))', string_agg(quote_literal(x), ', '))
               from (select distinct code as x from public.staff where code is not null union select 'writers_room') t);
  end if;
  if 'code' = any(cols) then c := c || ', code'; v := v || ', ' || quote_literal('writers_room'); end if;
  if 'role_summary' = any(cols) then c := c || ', role_summary';
    v := v || ', ' || quote_literal('대표 원고를 흐름은 그대로 두고 감정선과 감정 연결 장치를 다듬어 제작용 정리본으로 올립니다. 흐름 변경은 제안만 합니다.'); end if;
  if 'sort_order' = any(cols) then c := c || ', sort_order'; v := v || ', 0'; end if;
  execute format('insert into public.staff (%s) values (%s)', c, v);
end
$staff$;

-- 5) 작가실 지침 v1 (수정안)
do $g$
declare v_staff uuid;
begin
  select id into v_staff from public.staff where name = '작가실' limit 1;
  if v_staff is null or exists (select 1 from public.staff_guidelines where staff_id = v_staff) then return; end if;
  insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (v_staff, 1, $w$[작가실 지침 v1: 감정선 정리와 제작용 정리본]

■ 맡은 일
대표가 올린 원고를, 이야기 흐름은 그대로 둔 채 감정선이 약한 곳을 보강하고 감정이 이어지게 하는 장치를 다듬어 "제작용 정리본"으로 올린다.
제작 직원 중 대본을 고칠 수 있는 것은 작가실뿐이다. 정리본이 확정된 뒤에는 작가실도 고치지 않는다.

■ 직접 할 수 있는 것 (흐름 유지 범위)
1) 감정·톤 괄호: 대사마다 감정과 톤(피치 높낮이·속도·세기)을 적는다. 예) 린: (떨리지만 또박또박, 점점 높아지며) …
2) 감정 연결 장치: 감정이 이유 없이 튀거나 끊기는 곳에만 넣는다.
   - 리액션 한 줄(짧은 대답, 숨, 말끝 흐림)
   - 시선·호흡·정적 지문 (예: [2초 정적. 린이 시선을 떨군다.])
   - 표정 변화 지문 (예: [입꼬리가 올라가다 멈춘다.])
   - 감정이 담긴 소품 동작 (예: 팔찌를 만진다)
   - 감정만 전하는 짧은 내레이션 한 줄 (이미 보여 준 정보를 되풀이하지 않는다)
3) 대사 다듬기: 감정선에 못 미치는 대사의 표현만 다듬는다. 전달하는 정보, 화자, 장면 안의 위치는 그대로, 길이는 원문의 ±20% 이내.
4) 제작용 형식 정리
   - 씬 머리: S#번호 | 시간 구간 | 장소(바이블 이름 그대로) | 낮·밤 / 등장: 인물(외형 상태) 예) 린(과거), 린(미래)
   - 한 괄호에 한 장면만. 여러 화면이 묶여 있으면 줄을 나눈다.
   - 화면 속 글자는 [화면 글자: …]로 통일한다.
   - 인물·장소 이름은 바이블과 같게 쓴다.

■ 직접 하면 안 되는 것 → "흐름 변경 제안"으로만 올린다 (대표 승인 후 반영)
장면 추가·삭제·순서 변경 / 사건·반전·정보가 드러나는 시점 변경 / 인물 관계·설정 변경 / 대사가 전하는 정보의 변경 / 결말·클리프행어 변경 / 새 인물 / 분량이 크게 늘어나는 변경
판단 기준: "이걸 바꾸면 이 화의 줄거리 요약이 달라지는가?" 달라지면 흐름 변경이다. 애매하면 제안으로 올린다.

■ 흐름 변경 제안 양식
화·씬 / 종류(장면 추가·삭제·순서·사건 시점·반전·관계·설정·결말·기타) / 지금 원고 / 제안 / 이유(감정선·흐름 근거) / 영향 받는 화·장면

■ 작업 순서
1) 원고 전체와 앞뒤 화, 시리즈 바이블, 대표 연출 메모를 읽고 이 화의 감정 곡선(시작 감정 → 전환점 → 끝 감정)을 한 줄로 적는다.
2) 감정선이 약하거나 끊기는 곳을 찾는다.
3) "직접 할 수 있는 것"만으로 정리본을 만든다.
4) 변경표를 만든다: 씬 / 원문 / 바꾼 것 / 이유(감정선 근거).
5) 흐름을 바꿔야 더 좋아지는 곳은 정리본에 반영하지 말고 제안으로 올린다.
6) 정리본은 새 초안으로 저장한다(원본은 그대로 보관). 설정의 "작가실 정리본 자동 확정"이 켜져 있으면 확정까지 하고, 대표에게 변경 요약을 알린다.
7) 대표가 제안을 승인하면 그 내용을 반영한 새 정리본을 만들고, 제안을 "반영 완료"로 표시한다. 반려된 제안은 다시 올리지 않는다.

■ 내놓는 것
제작용 정리본, 감정 곡선 한 줄, 변경표, 흐름 변경 제안(있을 때), 대표용 변경 요약(3~5줄).$w$, '새 직원: 작가실', 'proposed');
end
$g$;
