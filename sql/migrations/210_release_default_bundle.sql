-- 대표 결정: 기본 공개 방식 = 3편 묶음 공개 + 알림은 첫 편 하나 (배포 시점에 다시 정리)
update public.app_settings set value = value || jsonb_build_object('mode', 'bundle', 'bundle_size', 3, 'notify_per_bundle', 1) where key = 'release';
do $r$
declare v_staff uuid; v_g record; v_add text := $t$

■ 기본 방식 (대표 결정, 2026-11)
- 3편 묶음 공개 + 알림은 묶음의 첫 편 하나만. 반응을 보고 바꿀 수는 있지만, 바꿀 때는 이유를 대표에게 먼저 알린다.
- 첫 배포 전에 대표와 공개 계획을 다시 한번 정리한다.$t$;
begin
  select id into v_staff from public.staff where name = '배포 담당' limit 1;
  if v_staff is null then return; end if;
  select * into v_g from public.staff_guidelines where staff_id = v_staff order by version_no desc limit 1;
  if v_g.id is null or position('■ 기본 방식' in v_g.content) > 0 then return; end if;
  if v_g.status = 'proposed' then
    update public.staff_guidelines set content = v_g.content || v_add where id = v_g.id;
  else
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status)
    values (v_staff, v_g.version_no + 1, v_g.content || v_add, '대표 결정: 3편 묶음 + 알림 1개', 'proposed');
  end if;
end
$r$;
