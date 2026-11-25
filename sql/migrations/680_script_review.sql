-- 대본 감수: 원고 → 작가실 정리본(→ 컷 설계)이 씬 순서대로 빠짐없이 옮겨졌는지 보려고 한 화의 대본 버전을 모두 읽는다
create or replace function public.agent_script_versions(p_token text, p_episode uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return coalesce((select jsonb_agg(jsonb_build_object('id', v.id, 'version_no', v.version_no, 'status', v.status, 'note', v.note,
                     'created_at', v.created_at, 'content', v.content) order by v.created_at)
                   from public.script_versions v where v.episode_id = p_episode), '[]');
end $$;
revoke all on function public.agent_script_versions(text, uuid) from public;
grant execute on function public.agent_script_versions(text, uuid) to anon, authenticated;

-- 직원: 최종감수자 → 대본감수자(대표 지시). 할 일도 새로 정한다. 감정·영상 감수자 할 일도 '생성된 영상' 기준으로 명시.
update public.staff set name = '대본감수자',
  role_summary = '작가실 정리본(과 컷 설계)이 원고의 씬 순서대로 빠짐없이 옮겨졌는지 감수합니다: 씬 번호·순서, 빠진 씬·대사·사건, 장소별 씬 구분, 컷이 대사를 모두 담았는지. 제작 탭 "대본 감수" 버튼.'
  where code = 'final_reviewer';
update public.staff set role_summary = '생성된 영상의 감정 전달을 감수합니다: 표정·목소리·동작이 대사의 감정선을 따라가는지, 앞뒤 컷의 감정 온도가 이어지는지. 제작 탭 컷마다 "감정 감수"(영상이 생긴 뒤 활성).'
  where code = 'script_reviewer' and coalesce(role_summary, '') = '';
update public.staff set role_summary = '생성된 영상의 화면을 감수합니다: 얼굴·의상·배경 일관성, 표정 변화, 손짓·동작, 속도, 왜곡·글자. 제작 탭 컷마다 "영상 감수"(영상이 생긴 뒤 활성). 음성 감수(대사가 대본과 같은지·목소리 일관성)도 함께 맡습니다.'
  where code = 'video_reviewer' and coalesce(role_summary, '') = '';

do $r$
declare s uuid; g record; v_new text := $t$[대본감수자 지침 v3: 원고 → 정리본 → 컷 설계 대조 (대표 지시로 최종감수자에서 이름·역할 변경)]

■ 맡은 일
작가실이 가져온 정리본이 대표 원고의 내용을 씬 순서대로 빠짐없이 옮겼는지, 그리고 컷 설계가 정리본을 빠짐없이 담았는지 감수한다. 영상을 만들기 전에 일한다.

■ 확인할 것
1) 씬 번호·순서: S#1부터 빠지거나 겹치는 번호 없이 이어지는가, 원고의 사건 순서와 같은가.
2) 장소별 씬 구분: 장소·시간이 바뀌는 곳마다 새 씬인가, 씬 머리의 장소 이름이 바이블 장소 이름과 같은가.
3) 빠진 것: 원고에 있던 씬·사건·대사·인물 등장이 정리본에서 빠지지 않았는가(감정선 보강으로 추가된 것은 허용, 흐름 변경은 지적).
4) 대사: 원고 대사가 글자 그대로 남았는가.
5) 컷 설계가 있으면: 씬마다 컷이 있는가, 정리본의 대사가 모두 컷에 순서대로 들어갔는가.

■ 보고 양식
문제마다: 씬 / 종류(순서·누락·대사 변경·장소 구분·컷 누락) / 원고(또는 정리본) 원문 / 지금 상태 / 고칠 곳(작가실·감독).

■ 하지 않는 일
영상 화면·소리 판단(영상·감정 감수자 담당), 대본 직접 수정.$t$;
begin
  select id into s from public.staff where code = 'final_reviewer';
  if s is null then return; end if;
  select * into g from public.staff_guidelines where staff_id = s order by version_no desc limit 1;
  if g.id is null or position('대본감수자 지침 v3' in g.content) = 0 then
    update public.staff_guidelines set status = 'retired' where staff_id = s and status = 'active';
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status, proposed_by, decided_by, decided_at)
    values (s, coalesce(g.version_no, 0) + 1, v_new, '대표 지시: 최종감수자 → 대본감수자(원고·정리본·컷 설계 대조)', 'active', 'owner', 'owner', now());
  end if;
end
$r$;
