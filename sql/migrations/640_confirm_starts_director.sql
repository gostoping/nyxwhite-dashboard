-- 대본(작가실 정리본)이 확정되면 감독 컷 설계를 바로 맡긴다 — 대표가 확정하든 자동 확정이든.
-- 긴 씬 나누기 수정본처럼 이어 할 일이 정해져 있으면(on_confirm) 그것만. 같은 화에 감독 일이 이미 대기·진행 중이면 더 넣지 않는다.
create or replace function public.nyx_script_on_confirm() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'confirmed' and old.status is distinct from 'confirmed' then
    if new.on_confirm is not null then
      insert into public.pipeline_tasks (kind, episode_id, payload)
      values ('director', new.episode_id, coalesce(new.on_confirm->'payload', '{}'::jsonb) || jsonb_build_object('script_version_id', new.id));
    elsif not exists (select 1 from public.pipeline_tasks t where t.kind = 'director' and t.episode_id = new.episode_id and t.status in ('queued', 'running')) then
      insert into public.pipeline_tasks (kind, episode_id, payload)
      values ('director', new.episode_id, jsonb_build_object('script_version_id', new.id));
    end if;
  end if;
  return null;
end $$;
-- 확정 함수가 같은 거래 안에서 직접 감독 일을 넣는 경우와 겹치지 않게, 거래가 끝날 때(커밋 직전) 확인한다
drop trigger if exists nyx_script_on_confirm on public.script_versions;
create constraint trigger nyx_script_on_confirm after update on public.script_versions
  deferrable initially deferred for each row execute function public.nyx_script_on_confirm();
