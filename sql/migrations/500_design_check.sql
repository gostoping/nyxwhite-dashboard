-- 코디네이터가 만들어진 사진 후보(사진 디자인·기준 사진 세트)를 프롬프트와 비교해 장마다 확인 결과를 남긴다(글자·이름표·다른 사람·묘사 불일치)
alter table public.photo_designs add column if not exists review jsonb;
create or replace function public.agent_update_photo_design(p_token text, p_id uuid, p jsonb) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if p ? 'review' and (select count(*) from jsonb_object_keys(p)) = 1 then   -- 확인 결과만 저장(상태·오류는 그대로)
    update public.photo_designs set review = p->'review' where id = p_id;
    return;
  end if;
  update public.photo_designs set
    status = coalesce(p->>'status', status), previews = coalesce(p->'previews', previews),
    soul_id = coalesce(p->>'soul_id', soul_id), ref_set = coalesce(p->'ref_set', ref_set), error = p->>'error',
    review = case when p ? 'previews' or p ? 'ref_set' then null else review end   -- 새 사진이 오면 이전 확인 결과는 지운다
  where id = p_id;
end $$;
revoke all on function public.agent_update_photo_design(text, uuid, jsonb) from public;
grant execute on function public.agent_update_photo_design(text, uuid, jsonb) to anon, authenticated;

-- 항목 감수 대기열을 새 버전으로 옮길 때, 사진 후보 확인(design_id) 작업은 건드리지 않는다
create or replace function public.nyx_coord_queue() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_t uuid;
begin
  if new.status = 'active' and (TG_OP = 'INSERT' or old.status is distinct from 'active') then
    select t.id into v_t from public.pipeline_tasks t where t.kind = 'coord_review' and t.status = 'queued'
       and t.payload->>'entry_id' = new.entry_id::text and not (t.payload ? 'design_id') limit 1;
    if v_t is not null then
      update public.pipeline_tasks set payload = payload || jsonb_build_object('version_id', new.id) where id = v_t;
    else
      insert into public.pipeline_tasks (kind, series_id, payload, not_before)
      select 'coord_review', be.series_id, jsonb_build_object('version_id', new.id, 'entry_id', be.id), now() + interval '20 seconds'
        from public.bible_entries be where be.id = new.entry_id
         and be.category in ('character', 'location', 'background', 'prop', 'style', 'music');
    end if;
  end if;
  return null;
end $$;

-- 사진 디자인에 시리즈도 넘긴다(현황판 표시)
create or replace function public.agent_get_photo_design(p_token text, p_id uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select to_jsonb(d) || jsonb_build_object('entry_name', e.name, 'series_id', e.series_id, 'category', e.category, 'summary', e.summary, 'series_title', s.title,
                   'description', bv.description, 'details', bv.details, 'images', coalesce(bv.image_paths, '{}'), 'cur_soul_id', bv.soul_id, 'prompt_en', bv.prompt_en,
                   'prompt_pending', exists (select 1 from public.pipeline_tasks t where t.kind in ('bible_prompt', 'coord_review') and t.status in ('queued', 'running')
                                              and t.payload->>'entry_id' = e.id::text),
                   'generation', (select value from public.app_settings where key = 'generation'))
          from public.photo_designs d join public.bible_entries e on e.id = d.entry_id join public.series s on s.id = e.series_id
          left join public.bible_versions bv on bv.entry_id = e.id and bv.status = 'active'
          where d.id = p_id);
end $$;
