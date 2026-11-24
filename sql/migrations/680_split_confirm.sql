-- 컷 설계 전 제작용 씬 나누기(2026-11-24): 작가실이 긴 씬을 대사·사건 그대로(에이전트가 대사 95% 이상 보존을 확인) 나눈 정리본은
-- '정리본 자동 확정' 설정과 상관없이 바로 확정한다(내용이 바뀌지 않는 제작용 정리라 대표 확정을 기다리지 않음).
-- 확정되면 nyx_script_on_confirm 트리거가 감독 컷 설계를 이어 붙인다.
create or replace function public.agent_confirm_split(p_token text, p_id uuid, p_summary text) returns int
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); v_ep uuid; v_no int;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  select episode_id into v_ep from public.script_versions where id = p_id and status = 'draft';
  if v_ep is null then raise exception '초안이 아닙니다'; end if;
  select coalesce(max(version_no), 0) + 1 into v_no from public.script_versions where episode_id = v_ep;
  update public.script_versions set status = 'superseded' where episode_id = v_ep and status = 'confirmed';
  update public.script_versions set status = 'confirmed', version_no = v_no, confirmed_at = now(),
         change_summary = left(p_summary, 2000) where id = p_id;
  begin
    insert into public.notifications (title, body, level, link_tab)
    values ('작가실 씬 나누기 확정: v' || v_no, left(p_summary, 300), 'info', 'production');
  exception when others then null;
  end;
  perform public.nyx_log('작가실', '제작용 씬 나누기 자동 확정 v' || v_no, p_id, v_dev);
  return v_no;
end $$;
revoke all on function public.agent_confirm_split(text, uuid, text) from public;
grant execute on function public.agent_confirm_split(text, uuid, text) to anon, authenticated;
