-- 배포 담당: 연재 공개 규칙(하루 여러 편, 알림 한도 지키기) 추가
do $r$
declare v_staff uuid; v_g record; v_add text := $t$

■ 연재 공개 규칙 (대표 지시: 다음이 기다려지게, 매일 알림이 가게)
- 목표: 매일 같은 시간대에 새 화가 올라와 구독·알림 습관을 만든다. 각 화 끝은 클리프행어 + "다음 화 ○시 공개" 문구(끝 화면·고정 댓글·설명 첫 줄).
- 유튜브 알림 한도: 시청자는 한 채널에서 24시간에 최대 3개의 새 영상 알림만 받는다. 짧은 시간에 3개를 넘게 공개하면 24시간 동안 알림이 멈출 수 있다.
  → 하루 공개는 알림 있는 영상 최대 3개, 서로 최소 3~4시간 간격(예: 12:00 · 18:00 · 21:00). 4번째가 필요하면 쇼츠·예고편으로 하고 "구독자에게 알림" 끄기.
  → 한꺼번에 공개 금지: 미리 올려 두고 비공개 + 예약 공개로 하나씩 연다.
- 재고(버퍼) 관리: 공개 예정보다 완성본이 최소 3일분 앞서 있어야 한다. 재고가 2일분 아래로 떨어지면 대표에게 알리고, 하루 공개 수를 줄이는 안을 함께 제안한다(끊기는 것보다 속도를 줄이는 쪽이 낫다).
- 공개 계획표: 화별 공개 날짜·시간·알림 여부·쇼츠 연결을 표로 만들어 대표 승인을 받는다.
- 효과 확인: 알림 도달률(스튜디오 '알림 보낸 비율'), 공개 후 1시간 조회, 다음 화 이동률, 구독 증감을 일별로 비교해 하루 편수·시간을 조정 제안한다.$t$;
begin
  select id into v_staff from public.staff where name = '배포 담당' limit 1;
  if v_staff is null then return; end if;
  select * into v_g from public.staff_guidelines where staff_id = v_staff order by version_no desc limit 1;
  if v_g.id is null or position('연재 공개 규칙' in v_g.content) > 0 then return; end if;
  if v_g.status = 'proposed' then
    update public.staff_guidelines set content = v_g.content || v_add where id = v_g.id;
  else
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status)
    values (v_staff, v_g.version_no + 1, v_g.content || v_add, '대표 지시: 하루 여러 편 연재 공개 + 알림 한도 규칙', 'proposed');
  end if;
end
$r$;

insert into public.app_settings (key, value) values
  ('release', jsonb_build_object('per_day', 3, 'times', jsonb_build_array('12:00', '18:00', '21:00'), 'buffer_days', 3, 'extra_as_shorts', true))
on conflict (key) do nothing;
