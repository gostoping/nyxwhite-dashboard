-- 배포 담당: 묶음 공개(여러 편을 함께 열고 알림은 한 편만)
do $r$
declare v_staff uuid; v_g record; v_add text := $t$

■ 묶음 공개 (여러 편 함께, 알림은 하나)
- 2~3편을 같은 때 공개하되 "구독자에게 알림"은 첫 편 하나만 켠다(업로드 때 화별로 끄고 켤 수 있음). 나머지 편은 알림·구독 피드에 뜨지 않으므로
  첫 편의 끝 화면·고정 댓글·설명에서 다음 편으로 바로 이어지게 하고, 시리즈 재생목록에 순서대로 넣어 자동 재생으로 넘어가게 한다.
- 알림이 하나뿐이라 시청자가 지치지 않고, 들어온 사람은 몰아서 본다. 언제 묶음으로 열지(정주행 유도) / 하나씩 열지(매일 기다리게)는 반응을 보고 정한다.
- 묶음도 한 번에 3편을 넘기지 않는다(3편 초과 공개 시 24시간 알림 정지 위험).$t$;
begin
  select id into v_staff from public.staff where name = '배포 담당' limit 1;
  if v_staff is null then return; end if;
  select * into v_g from public.staff_guidelines where staff_id = v_staff order by version_no desc limit 1;
  if v_g.id is null or position('묶음 공개' in v_g.content) > 0 then return; end if;
  if v_g.status = 'proposed' then
    update public.staff_guidelines set content = v_g.content || v_add where id = v_g.id;
  else
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status)
    values (v_staff, v_g.version_no + 1, v_g.content || v_add, '대표 지시: 여러 편 공개 + 알림 한 편만', 'proposed');
  end if;
end
$r$;
