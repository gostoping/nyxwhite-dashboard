-- 지침에서 특정 작품의 인물 이름을 뺀다(어느 시리즈에서든 같은 지침을 쓰도록)
-- 검토 전 수정안은 그대로 고치고, 이미 적용된 지침은 고친 내용으로 새 수정안을 만든다.
do $$
declare r record; v_new text; v_no int;
begin
  for r in
    select g.id, g.staff_id, g.status, g.content from public.staff_guidelines g
    where g.status in ('proposed','active') and (g.content like '%린·탄%' or g.content like '%예: 린 v3%')
  loop
    v_new := replace(replace(r.content, '배우(린·탄) 일관성', '배우(바이블에 등록된 인물) 일관성'), '(예: 린 v3)', '(예: 주인공 v3)');
    v_new := replace(v_new, '린·탄', '등장인물');
    if r.status = 'proposed' then
      update public.staff_guidelines set content = v_new where id = r.id;
    elsif not exists (select 1 from public.staff_guidelines where staff_id = r.staff_id and status = 'proposed') then
      select coalesce(max(version_no), 0) + 1 into v_no from public.staff_guidelines where staff_id = r.staff_id;
      insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status)
      values (r.staff_id, v_no, v_new, '특정 작품 인물 이름 제거(모든 시리즈 공통 지침)', 'proposed');
    end if;
  end loop;
end $$;
