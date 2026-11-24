-- 대본 갈아끼우기: 대표만. 한 화의 기존 대본(모든 버전)과 그 대본으로 만든 제작 자료(컷·테이크·작업·편집·메모·작가실 제안)를 지운다.
-- 비용 기록과 정산은 남긴다. p_drop = true면 화 자체도 지운다(새 파일에 없는 화).
create or replace function public.owner_reset_episode(p_episode uuid, p_drop boolean default false) returns void
language plpgsql security definer set search_path = public as $$
declare r record;
begin
  if not public.nyx_is_owner() then raise exception '대표만 할 수 있습니다'; end if;
  delete from public.pipeline_tasks where episode_id = p_episode;
  delete from public.production_cuts where episode_id = p_episode;
  delete from public.scene_packages where episode_id = p_episode;
  delete from public.edit_renders where episode_id = p_episode;
  delete from public.edit_projects where episode_id = p_episode;
  delete from public.story_proposals where episode_id = p_episode;
  delete from public.episode_notes where episode_id = p_episode;
  delete from public.continuity_logs where episode_id = p_episode;
  -- 대본 버전을 가리키는 다른 표(초기 설정에서 만든 것 포함)를 찾아 함께 정리
  for r in select c.conrelid::regclass as tbl, a.attname as col
             from pg_constraint c join pg_attribute a on a.attrelid = c.conrelid and a.attnum = c.conkey[1]
            where c.contype = 'f' and c.confrelid = 'public.script_versions'::regclass loop
    execute format('delete from %s where %I in (select id from public.script_versions where episode_id = $1)', r.tbl, r.col) using p_episode;
  end loop;
  delete from public.script_versions where episode_id = p_episode;
  if p_drop then
    delete from public.episodes where id = p_episode;
  else
    update public.episodes set status = 'writing' where id = p_episode;
  end if;
end $$;
revoke all on function public.owner_reset_episode(uuid, boolean) from public, anon;
grant execute on function public.owner_reset_episode(uuid, boolean) to authenticated;
