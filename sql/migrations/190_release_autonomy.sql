-- 배포 담당이 시청자 반응을 보고 공개 시점·편수를 스스로 정한다(대표 지시). 무엇을 공개할지는 대표가 확정한 완성본만.
do $r$
declare v_staff uuid; v_g record; v_add text := $t$

■ 공개 결정 권한 (대표 위임)
- 공개 날짜·시간·하루 편수·알림 여부·쇼츠 연결은 배포 담당이 시청자 반응을 보고 스스로 정한다. 대표 승인을 기다리지 않는다.
- 단, 공개할 수 있는 것은 대표가 편집실에서 확정한 완성본뿐이다. 제목·설명·썸네일은 이미 승인된 안 중에서 고르거나, 바꾸면 결정 기록에 남긴다.
- 지켜야 할 선: 알림 있는 공개는 하루 최대 3편, 간격 3시간 이상 / 재고 2일분 미만이면 속도를 줄인다 / 광고비가 드는 일은 여전히 대표 승인.
- 결정할 때마다 대표에게 한 줄 알림: 무엇을 언제 왜(근거 지표) 공개하는지. 대표가 설정에서 '자동 결정'을 끄면 다시 승인을 받는다.
- 판단 근거: 직전 화의 공개 후 1시간·24시간 조회, 알림 도달률, 다음 화 이동률, 평균 시청 비율, 댓글 반응(기대·불만), 구독 증감. 반응이 뜨거우면 다음 화를 앞당기고, 식으면 간격을 두고 쇼츠로 다시 불을 붙인다.$t$;
begin
  select id into v_staff from public.staff where name = '배포 담당' limit 1;
  if v_staff is null then return; end if;
  select * into v_g from public.staff_guidelines where staff_id = v_staff order by version_no desc limit 1;
  if v_g.id is null or position('공개 결정 권한' in v_g.content) > 0 then return; end if;
  if v_g.status = 'proposed' then
    update public.staff_guidelines set content = replace(v_g.content, '업로드·공개는 대표 승인 후에만 한다.', '공개할 완성본은 대표가 확정하고, 공개 시점·편수는 아래 "공개 결정 권한"에 따라 배포 담당이 정한다.') || v_add where id = v_g.id;
  else
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status)
    values (v_staff, v_g.version_no + 1, replace(v_g.content, '업로드·공개는 대표 승인 후에만 한다.', '공개할 완성본은 대표가 확정하고, 공개 시점·편수는 아래 "공개 결정 권한"에 따라 배포 담당이 정한다.') || v_add,
            '대표 지시: 공개 시점·편수 결정을 배포 담당에게 위임', 'proposed');
  end if;
end
$r$;

update public.app_settings set value = value || jsonb_build_object('auto_decide', true) where key = 'release';
insert into public.app_settings (key, value) values ('release', jsonb_build_object('per_day', 3, 'times', jsonb_build_array('12:00','18:00','21:00'), 'buffer_days', 3, 'extra_as_shorts', true, 'auto_decide', true))
on conflict (key) do nothing;
