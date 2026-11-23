-- 새 표에 로그인 사용자의 접근을 허용 (실제 권한은 표마다 걸린 권한 규칙(RLS)이 다시 걸러냄)
grant select, insert, update, delete on public.bible_entries, public.bible_versions, public.continuity_logs to authenticated;
grant select, update on public.agent_status to authenticated;
grant select, insert on public.agent_commands to authenticated;
