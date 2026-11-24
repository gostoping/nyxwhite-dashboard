-- 사진 후보 확인 결과(코디네이터가 고친 프롬프트·금지 사항)를 다음 사진 만들기에 넘긴다
create or replace function public.agent_get_photo_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'series_id', e.series_id, 'cur_version_id', bv.id,
                   'last_review', (select pd.review from public.photo_designs pd where pd.entry_id = e.id and pd.id <> d.id and pd.review is not null order by pd.created_at desc limit 1), 'category', e.category, 'summary', e.summary, 'series_title', s.title,
                   'description', bv.description, 'details', bv.details, 'images', coalesce(bv.image_paths, '{}'), 'cur_soul_id', bv.soul_id, 'prompt_en', bv.prompt_en,
                   'prompt_pending', exists (select 1 from public.pipeline_tasks t where t.kind in ('bible_prompt', 'coord_review') and t.status in ('queued', 'running')
                                              and t.payload->>'entry_id' = e.id::text),
                   'generation', (select value from public.app_settings where key = 'generation'))
          from public.photo_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          left join public.bible_versions bv on bv.entry_id = e.id and bv.status = 'active'
          where d.id = p_id);
end $$;

-- 일이 계속 있어 옛 버전에 머물러 있던 에이전트를 한 번 업데이트시킨다(코디네이터·사진 줄이 새 버전에 있음)
insert into public.agent_commands (device_id, command)
select s.device_id, 'update' from public.agent_status s
 where s.last_seen_at > now() - interval '1 day' and coalesce(s.state, '') <> 'removed'
   and not exists (select 1 from public.agent_commands c where c.device_id = s.device_id and c.command = 'update' and c.status in ('queued', 'sent'));
