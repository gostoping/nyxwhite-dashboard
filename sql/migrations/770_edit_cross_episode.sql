-- 편집실: 다른 화(또는 모든 화)의 영상을 한 타임라인으로 가져와 이어 붙일 수 있게, 조립 때 타임라인에 있는 테이크는 화와 상관없이 넘겨준다
create or replace function public.agent_get_edit(p_token text, p_project uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_ep uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select episode_id into v_ep from public.edit_projects where id = p_project;
  return (select jsonb_build_object(
    'project', to_jsonb(p),
    'episode', jsonb_build_object('id', e.id, 'number', e.number, 'title', e.title, 'series_id', e.series_id),
    'series_title', (select title from public.series where id = e.series_id),
    'settings', (select coalesce(jsonb_object_agg(key, value), '{}') from public.app_settings),
    'takes', coalesce((select jsonb_object_agg(t.id, jsonb_build_object('files', t.files, 'remote_url', t.remote_url, 'seconds', t.seconds,
                         'code', c.code, 'scene_no', c.scene_no, 'cut_no', c.cut_no, 'dialogue', c.dialogue, 'summary', c.summary))
                       from public.production_takes t join public.production_cuts c on c.id = t.cut_id
                       where c.episode_id = v_ep
                          or t.id::text in (select x->>'take_id' from jsonb_array_elements(coalesce(p.timeline->'clips', '[]')) x)
                          or t.id::text in (select x->>'take_id' from public.edit_renders r, jsonb_array_elements(coalesce(r.timeline->'clips', '[]')) x
                                            where r.project_id = p.id and r.status in ('queued', 'running'))), '{}'))
    from public.edit_projects p join public.episodes e on e.id = p.episode_id where p.id = p_project);
end $$;
