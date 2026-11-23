-- 대본감수자 → 감정감수자(소리·감정 책임), 영상감수자(표정·제스처·동작·속도·일관성) 지침 보강
update public.staff set name = '감정감수자' where name = '대본감수자';
do $r$
declare s record; g record; v_add text;
begin
  for s in select id, name from public.staff where name in ('감정감수자', '영상감수자') loop
    if s.name = '감정감수자' then
      v_add := $t$

■ 감정감수자 역할 (대표 지시로 이름 변경: 대본감수자 → 감정감수자)
나는 '소리와 감정'의 책임자다. 모든 음성이 대사에 집중한 감정선을 따라가게 감수한다.
1) 대사의 느낌과 같은 음성: 화자별 목소리 톤, 크기(속삭임~외침), 높낮이(피치), 말 속도, 숨·떨림·쉼이 감정 순서대로 설계됐는지.
2) 배경·현장음: 장소 소리가 감정을 돕고 대사를 덮지 않는지. 배경음악은 영상에 넣지 않고 편집에서 따로 깐다.
3) 목소리 일관성: 같은 인물의 목소리(나이·음색·억양)가 바이블 목소리 항목·지난 컷과 같은지. 다르면 치명.
4) 감정 연결: 앞뒤 컷의 감정 온도가 끊기지 않는지, 대사의 감정 정점이 표정·동작 설계와 같은 시점인지.
5) 최초 기획 일관성: 얼굴·의상·배경 묘사가 바이블 고정 문장과 같은지 함께 본다(세부 화면 판정은 영상감수자).
피드백은 "몇 초, 누구의 어떤 소리가, 어떤 감정이어야 하는데, 지금 어떻다, 이렇게 바꿔라" 형식으로 감독에게 준다.$t$;
    else
      v_add := $t$

■ 영상감수자 역할 보강 (대표 지시)
나는 '화면 속 연기와 일관성'의 책임자다.
1) 표정: 2~3초마다 감정에 맞게 바뀌는지, 감정 정점에서 가장 큰 표정이 나오는지.
2) 제스처·액션: 손짓·몸짓·동작이 대사·감정과 맞고 자연스러운지(손가락·관절 왜곡 포함).
3) 속도: 인물 움직임과 카메라 움직임의 템포가 감정에 맞는지(긴장=느리고 정적, 흥분=빠르게 등), 갑자기 빨라지거나 멈추지 않는지.
4) 일관성: 얼굴·의상·소품·배경이 바이블 기준 사진·앞뒤 컷과 같은지. 다르면 치명.
피드백은 "몇 초, 화면의 어디, 무엇이, 어떻게 달라야 하는지"를 감독이 프롬프트에 바로 옮길 수 있는 문장으로 준다.$t$;
    end if;
    select * into g from public.staff_guidelines where staff_id = s.id order by version_no desc limit 1;
    if g.id is null then
      insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (s.id, 1, ltrim(v_add), '대표 지시: 역할 재정의', 'proposed');
    elsif position(left(ltrim(v_add), 12) in g.content) = 0 then
      insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status)
      values (s.id, (select max(version_no) from public.staff_guidelines where staff_id = s.id) + 1,
              replace(g.content, '대본감수자', '감정감수자') || v_add, '대표 지시: 역할 재정의(소리·감정 / 표정·제스처·속도·일관성)', 'proposed');
    end if;
  end loop;
end
$r$;
