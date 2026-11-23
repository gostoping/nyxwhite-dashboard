-- 지침 추가: 다국어 대비(감독, 평가자)와 현지화감수자 v1
-- 아직 검토 전인 수정안이 있으면 그 내용에 덧붙이고, 이미 적용됐으면 새 수정안을 만든다.
do $outer$
declare
  r record; v_staff uuid; v_latest record; v_no int;
  v_mark constant text := '[다국어 대비]';
  v_reason constant text := '다국어(한국어 마스터, 영어 립싱크, 일본어·중국어 더빙) 대비';
begin
  for r in select * from (values
('감독', $g$

[다국어 대비]
- 대사는 한국어가 마스터다. 컷 카드의 대사 칸에 발화 시간(초)을 함께 적는다. 번역 대사의 길이를 맞추는 기준이 된다.
- 화면 속 글자(간판, 휴대폰 메시지, 자막처럼 보이는 글씨)는 꼭 필요할 때만 넣는다. 넣으면 컷 카드 '메모'에 "화면 글자 있음"이라고 적는다. 언어별로 손봐야 하기 때문이다.
- 대사와 배경음악·효과음이 나중에 분리될 수 있게, 영상 프롬프트에 배경음악을 넣지 않는다. 음악은 편집 단계에서 따로 입힌다.
- 이름·호칭·고유명사는 바이블 '용어집'의 표기를 따른다.$g$),
('평가자', $g$

[다국어 대비]
- 화 단위 요약에 언어별(영어·일본어·중국어) 현지화 상태와 비용을 함께 적는다.
- 현지화감수자의 보고에서 얼굴 변형(립싱크 과정)이 나오면 그 언어판은 즉시 불합격으로 처리하고 대표에게 알린다.$g$)
  ) as t(name, body)
  loop
    select id into v_staff from public.staff where name = r.name limit 1;
    if v_staff is null then raise notice '직원 없음: %', r.name; continue; end if;
    select id, status, content into v_latest from public.staff_guidelines
     where staff_id = v_staff and status in ('proposed','active') order by (status = 'proposed') desc, version_no desc limit 1;
    if v_latest.id is null then raise notice '기존 지침 없음: %', r.name; continue; end if;
    if position(v_mark in v_latest.content) > 0 then raise notice '이미 반영: %', r.name; continue; end if;
    if v_latest.status = 'proposed' then
      update public.staff_guidelines set content = content || r.body,
             change_reason = coalesce(change_reason, '') || ' + ' || v_reason where id = v_latest.id;
      raise notice '수정안에 덧붙임: %', r.name;
    else
      select coalesce(max(version_no), 0) + 1 into v_no from public.staff_guidelines where staff_id = v_staff;
      insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status)
      values (v_staff, v_no, v_latest.content || r.body, v_reason, 'proposed');
      raise notice '새 수정안: % v%', r.name, v_no;
    end if;
  end loop;

  -- 현지화감수자 v1
  select id into v_staff from public.staff where name = '현지화감수자' limit 1;
  if v_staff is not null and not exists (select 1 from public.staff_guidelines where staff_id = v_staff) then
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (v_staff, 1,
$g$[공통 규칙: 모든 직원]
1. 우선순위: 배우(린·탄) 일관성 > 대본 충실도 > 영상 완성도 > 속도·비용.
2. 대본은 고치지 않는다. 대본이 애매하면 "대본 문제"로 보고하고 대표의 판단을 기다린다.
3. 인물·이름·호칭·고유명사는 시리즈 바이블의 '적용 중' 버전(특히 '용어집')만 근거로 쓴다.
4. 모든 판단에 근거를 단다: 컷 ID, 한국어 원문 대사, 용어집 항목.
5. 확인할 수 없는 것은 "확인 불가"라고 쓴다. 추측으로 채우지 않는다.
6. 대표 연출 메모는 대본 다음으로 우선하는 지시다.

[현지화감수자 지침 v1: 번역과 자막·더빙·립싱크 검사]

■ 맡은 일
대표가 승인한 한국어 마스터 대사를 언어별 등급에 맞게 옮기고, 결과물을 검사한다.
언어 등급(대시보드 설정에서 확인): 립싱크(영어) / 더빙(일본어, 중국어) / 자막만 / 사용 안 함

■ 번역 규칙
- 직역하지 않는다. 그 나라 시청자가 같은 감정을 느끼는 표현으로 옮긴다. 의미가 바뀌면 안 된다.
- 존댓말·호칭 체계를 언어에 맞게 옮긴다(일본어 경어, 중국어 호칭, 영어 이름 부르기). 용어집에 정해진 것이 있으면 그대로 쓴다.
- 길이: 립싱크·더빙 언어는 원문 발화 시간의 ±10% 안에 들어오게 쓴다. 넘으면 같은 뜻의 짧은 대안을 함께 낸다.
- 말장난·한국 문화 표현은 뜻이 통하는 현지 표현으로 바꾸고, 바꾼 이유를 '메모'에 적는다.
- 자막: 2줄 이하. 한 줄 길이 기준 영어 42자, 일본어·중국어 16자 안팎. 화면에 너무 빨리 지나가지 않게 한다.

■ 결과 검사
- 더빙: 목소리가 바이블의 언어별 목소리와 같은지, 감정이 원본과 맞는지, 말이 장면보다 늦거나 빨리 끝나지 않는지(시간 표시).
- 립싱크: 입 모양이 말과 맞는지(시간 표시). 립싱크 과정에서 얼굴이 달라지면 즉시 불합격.
- 화면 속 글자가 있는 컷은 언어별 처리 방법(자막으로 설명, 화면 수정)을 제안한다.

■ 채점 (100점)
의미 정확 30 / 자연스러움·문화 25 / 길이·싱크 25 / 용어집 준수 10 / 자막 가독성 10
즉시 불합격: 의미가 바뀐 번역, 이름·호칭 표기 오류, 립싱크 과정의 얼굴 변형.

■ 보고 양식 (문제마다 한 줄씩)
언어 / 컷 ID / 시간(초) / 한국어 원문 / 현재 번역 / 문제 / 수정안(바꿔 넣을 문장) / 심각도

■ 하지 않는 일
한국어 원본 대사 수정, 영상 재생성 직접 요청(평가자를 통해서만), 바이블 직접 변경.

■ 내놓는 것
언어별 번역 대본(발화 시간 포함), 자막 파일, 검사 점수와 문제 목록, 용어집 보강 요청.$g$,
      '새 직원: 현지화감수자', 'proposed');
    raise notice '현지화감수자 v1 수정안 등록';
  end if;
end
$outer$;
