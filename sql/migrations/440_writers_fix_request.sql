-- 감독 → 작가실 피드백: 감독이 컷으로 설계하지 못한 긴 씬은 작가실이 대사·흐름을 그대로 두고 나눈 수정본을 낸다.
-- 그 수정본이 확정되면(대표 확정 또는 자동 확정) 감독이 그 씬만 다시 설계하도록 이어 붙여 둔다.
alter table public.script_versions add column if not exists on_confirm jsonb;

create or replace function public.agent_set_script_followup(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if p->>'kind' <> 'director' then raise exception '확정 뒤 이어 할 일은 감독 컷 설계만'; end if;
  update public.script_versions set on_confirm = p where id = p_id and status = 'draft';
end $$;
revoke all on function public.agent_set_script_followup(text, uuid, jsonb) from public;
grant execute on function public.agent_set_script_followup(text, uuid, jsonb) to anon, authenticated;

create or replace function public.nyx_script_on_confirm() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'confirmed' and old.status is distinct from 'confirmed' and new.on_confirm is not null then
    insert into public.pipeline_tasks (kind, episode_id, payload)
    values ('director', new.episode_id, coalesce(new.on_confirm->'payload', '{}'::jsonb) || jsonb_build_object('script_version_id', new.id));
  end if;
  return null;
end $$;
drop trigger if exists nyx_script_on_confirm on public.script_versions;
create trigger nyx_script_on_confirm after update on public.script_versions for each row execute function public.nyx_script_on_confirm();
