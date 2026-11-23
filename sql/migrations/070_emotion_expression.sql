-- 감정선·발화 톤(대본감수자), 2~3초 표정 변화(감독 설계, 영상감수자 검사), 발화 톤 확인(최종감수자)
-- 검토 전 수정안이 있으면 그 내용을 고치고, 없으면 적용 중 지침을 고쳐 새 수정안을 만든다.
do $outer$
declare
  r record; v_staff uuid; v_latest record; v_no int; v_new text; e record;
  v_reason constant text := '감정선·발화 톤 검사, 2~3초 표정 변화 설계·검사';
begin
  for r in select * from (values
    ('감독',
     array[
       '   - 행동 타임라인: 초 단위(예: 0~3초 …, 3~8초 …). 행동은 3개 이하',
       '   - 대사: 화자 / 원문 그대로 / 감정 / 언어'],
     array[
       E'   - 행동 타임라인: 초 단위(예: 0~3초 …, 3~8초 …). 행동은 3개 이하\n   - 표정 타임라인: 2~3초마다 표정 변화를 지시한다(예: 0~2초 눈이 커지며 놀람 → 2~5초 입꼬리가 올라가며 안도 → 5~8초 시선을 떨구며 쑥스러움). 대본의 감정 흐름을 따른다',
       '   - 대사: 화자 / 원문 그대로 / 감정 / 톤(피치 높낮이·속도·세기, 예: 높고 빠르게, 낮고 떨리게) / 언어'],
     $g$

[표정·감정 연기]
- 표정은 2~3초마다 바뀌게 설계한다. 같은 표정이 3초 넘게 이어지지 않게 한다. 일부러 멈춘 정적인 장면이면 그 이유를 컷 카드 '메모'에 적는다.
- 감정이 한 가지로 이어지는 구간(슬픔이 깊어지는 장면 등)은 같은 감정 안에서 세기와 세부를 바꾼다(눈빛, 눈썹, 입꼬리, 시선, 숨).
- 표정은 눈에 보이는 움직임으로 쓴다. "기쁘다"가 아니라 "눈가가 접히며 입꼬리가 올라간다".
- 표정 변화는 바이블 얼굴이 유지되는 범위 안에서 한다. 얼굴이 일그러질 만큼 과장된 표정은 피한다.
- 대사마다 톤(피치 높낮이·속도·세기)을 적는다. 앞 컷의 감정에서 자연스럽게 이어지게 하고, 갑자기 감정이 튀면 이유를 적는다.
- 대본의 지문, 앞뒤 장면, 대표 연출 메모에서 감정의 근거를 찾아 컷 카드에 함께 적는다.$g$),
    ('대본감수자',
     array[
       '대본 충실 35 / 바이블 일치 30 / 연속성 15 / 컷 규칙·명확성 10 / 실현 가능성 10',
       '6) 대표 연출 메모: 이 화의 ''필수'' 메모가 해당 씬의 컷 카드와 프롬프트에 모두 반영됐는가. ''중요 장면''이 주요 등급으로 다뤄졌는가.'],
     array[
       '대본 충실 30 / 감정선·발화 톤 15 / 바이블 일치 25 / 연속성 10 / 컷 규칙·명확성 10 / 실현 가능성 10',
       E'6) 대표 연출 메모: 이 화의 ''필수'' 메모가 해당 씬의 컷 카드와 프롬프트에 모두 반영됐는가. ''중요 장면''이 주요 등급으로 다뤄졌는가.\n7) 감정선과 발화 톤: 대본의 감정이 대사의 톤과 표정 타임라인에 녹아 있는가. 아래 [감정선·발화 톤 검사]를 따른다.'],
     $g$

[감정선·발화 톤 검사]
- 대본의 지문, 대사 내용, 앞뒤 장면에서 읽히는 감정이 컷 카드의 대사 감정·톤과 표정 타임라인에 녹아 있는지 본다.
- 이야기 흐름상 감정 곡선이 자연스러운가: 앞 컷에서 이유 없이 감정이 튀거나 꺾이지 않는가. 감정이 바뀌는 지점에 대본상 계기가 있는가.
- 발화의 높낮이(피치)·속도·세기가 대사 내용과 감정에 맞는가. 예) 놀라는 대사인데 "낮고 느리게"면 지적한다.
- 표정 타임라인이 2~3초 간격으로 짜여 있고, 각 표정이 그 순간의 대사·감정과 맞는가.
- 지적할 때는 감정의 근거가 된 대본 원문(지문·대사)을 함께 적고, 바꿔 넣을 감정·톤·표정 문장을 그대로 제시한다.$g$),
    ('영상감수자',
     array['얼굴·헤어 일관성 35 / 의상·소품 20 / 장소·조명 15 / 움직임 자연스러움 15 / 화질 15'],
     array['얼굴·헤어 일관성 30 / 표정 연기 15 / 의상·소품 20 / 장소·조명 10 / 움직임 자연스러움 10 / 화질 15'],
     $g$

[표정 검사]
- 2~3초 간격으로 장면을 보고, 컷 카드의 표정 타임라인과 대본의 감정에 맞게 표정이 나왔는지 확인한다.
- 보고는 표로 한다: 시간(초) / 기대한 표정(컷 카드) / 실제 표정 / 일치(맞음·약함·다름) / 수정 제안.
- 지적 대상: 같은 표정이 3초 넘게 이어짐(컷 카드에 정적 장면 이유가 없을 때), 감정과 반대되는 표정, 표정 변화가 너무 약해 읽히지 않음, 대사와 표정의 시점이 어긋남.
- 표정이 바뀌면서 얼굴이 다른 사람처럼 변하면 얼굴 일관성 위반으로 즉시 불합격 처리한다.
- 미세한 표정이 애매하면 점수를 깎지 말고 "대표 확인 필요"와 시간을 적는다.$g$),
    ('최종감수자',
     array['대사·행동 이행 50 / 감정·의도 전달 20 / 순서·타이밍 15 / 끝 상태(다음 컷 연결) 15'],
     array['대사·행동 이행 45 / 감정·의도 전달(표정·발화 톤 포함) 25 / 순서·타이밍 15 / 끝 상태(다음 컷 연결) 15'],
     $g$

[발화 톤 확인]
- 실제 목소리의 높낮이·속도·세기가 컷 카드의 톤 지시와 대본의 감정에 맞는지 확인하고, 다르면 시간과 함께 적는다.$g$)
  ) as t(name, finds, repls, addon)
  loop
    select id into v_staff from public.staff where name = r.name limit 1;
    if v_staff is null then raise notice '직원 없음: %', r.name; continue; end if;
    select id, status, content into v_latest from public.staff_guidelines
     where staff_id = v_staff and status in ('proposed','active') order by (status = 'proposed') desc, version_no desc limit 1;
    if v_latest.id is null then raise notice '기존 지침 없음: %', r.name; continue; end if;
    if position(split_part(r.addon, E'\n', 3) in v_latest.content) > 0 then raise notice '이미 반영: %', r.name; continue; end if;
    v_new := v_latest.content;
    for i in 1 .. array_length(r.finds, 1) loop
      if position(r.finds[i] in v_new) = 0 then raise notice '바꿀 문장을 못 찾음(%): %', r.name, left(r.finds[i], 40);
      else v_new := replace(v_new, r.finds[i], r.repls[i]); end if;
    end loop;
    v_new := v_new || r.addon;
    if v_latest.status = 'proposed' then
      update public.staff_guidelines set content = v_new, change_reason = coalesce(change_reason, '') || ' + ' || v_reason where id = v_latest.id;
      raise notice '수정안에 반영: %', r.name;
    else
      select coalesce(max(version_no), 0) + 1 into v_no from public.staff_guidelines where staff_id = v_staff;
      insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (v_staff, v_no, v_new, v_reason, 'proposed');
      raise notice '새 수정안: % v%', r.name, v_no;
    end if;
  end loop;
end
$outer$;
