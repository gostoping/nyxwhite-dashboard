-- 대표 결정(2026-11-24): 영상은 480p로 만든다(싸게 확인). 편집에서 OK할 때 "1080p로 업그레이드?"를 묻고,
-- 예면 집 PC가 완성본을 1080p로 키운 배포용 파일을 만든 뒤 배포 탭에 올린다.
update public.app_settings set value = value || jsonb_build_object('resolution', '480p') where key = 'generation';

alter table public.releases add column if not exists resolution text not null default '480p';
alter table public.releases add column if not exists upscale_status text not null default 'none';   -- none | queued | running | done | failed
alter table public.releases add column if not exists upscale_files jsonb not null default '{}';
alter table public.releases drop constraint if exists releases_upscale_status_check;
alter table public.releases add constraint releases_upscale_status_check check (upscale_status in ('none','queued','running','done','failed'));

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe','bible_place','bible_prompt','api_check','bible_suggest','upscale'));

create or replace function public.agent_get_release(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(r) || jsonb_build_object('render', (select to_jsonb(x) from public.edit_renders x where x.id = r.render_id),
                 'episode', (select jsonb_build_object('number', e.number, 'title', e.title) from public.episodes e where e.id = r.episode_id))
            from public.releases r where r.id = p_id);
end $$;

create or replace function public.agent_update_release(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.releases set
    upscale_status = coalesce(p->>'upscale_status', upscale_status),
    upscale_files = coalesce(p->'upscale_files', upscale_files),
    resolution = coalesce(p->>'resolution', resolution)
  where id = p_id;
end $$;
revoke all on function public.agent_get_release(text, uuid) from public;
revoke all on function public.agent_update_release(text, uuid, jsonb) from public;
grant execute on function public.agent_get_release(text, uuid) to anon, authenticated;
grant execute on function public.agent_update_release(text, uuid, jsonb) to anon, authenticated;
