-- 바이블 = 제작 자료실. 작가실이 대본에서 씬마다 필요한 인물·장소·소품을 뽑아 바이블에 "필요" 항목으로 올린다.
-- 대표가 사진을 올리면 그걸 쓰고, 안 올리면 사진 디자인(인물은 Soul 2)으로 만들어 바이블에 저장한다. 제작자(감독·컷 설계)는 바이블만 가져다 쓴다.
alter table public.bible_entries add column if not exists appears jsonb not null default '[]';   -- [{"ep":1,"scene":3}]
alter table public.bible_entries add column if not exists origin text not null default 'owner';  -- owner | script

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract'));

create or replace function public.agent_bible_overview(p_token text, p_series uuid, p_episode uuid default null) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return jsonb_build_object(
    'series', (select jsonb_build_object('id', id, 'title', title) from public.series where id = p_series),
    'entries', (select coalesce(jsonb_agg(jsonb_build_object('category', be.category, 'name', be.name, 'summary', be.summary,
                   'description', bv.description) order by be.category, be.sort_order), '[]')
                from public.bible_entries be left join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
                where be.series_id = p_series),
    'episodes', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                   'text', (select v.content from public.script_versions v where v.episode_id = e.id
                            order by (v.status = 'confirmed') desc, v.created_at desc limit 1)) order by e.number), '[]')
                 from public.episodes e where e.series_id = p_series and (p_episode is null or e.id = p_episode)));
end $$;

-- 항목을 올린다: 없으면 새 항목 + 작가실 수정안(대표 적용 대기), 있으면 등장 씬만 보탠다.
create or replace function public.agent_bible_upsert(p_token text, p_series uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare it jsonb; v_id uuid; n_new int := 0; n_prop int := 0; v_cat text;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  for it in select * from jsonb_array_elements(coalesce(p_items, '[]')) loop
    v_cat := it->>'category';
    if v_cat not in ('character', 'location', 'prop') or coalesce(trim(it->>'name'), '') = '' then continue; end if;
    select id into v_id from public.bible_entries where series_id = p_series and category = v_cat and lower(name) = lower(trim(it->>'name'));
    if v_id is null then
      insert into public.bible_entries (series_id, category, name, summary, origin, appears, sort_order)
      values (p_series, v_cat, left(trim(it->>'name'), 80), left(it->>'summary', 300), 'script', coalesce(it->'appears', '[]'),
              (select coalesce(max(sort_order), 0) + 1 from public.bible_entries where series_id = p_series))
      returning id into v_id;
      n_new := n_new + 1;
    else
      update public.bible_entries set appears = (select coalesce(jsonb_agg(distinct x), '[]') from jsonb_array_elements(appears || coalesce(it->'appears', '[]')) x)
       where id = v_id;
    end if;
    if not exists (select 1 from public.bible_versions where entry_id = v_id and status in ('active', 'proposed'))
       and coalesce(it->>'description', '') <> '' then
      insert into public.bible_versions (entry_id, description, details, change_reason, status, proposed_by)
      values (v_id, left(it->>'description', 2000), left(it->>'details', 2000), '대본에서 필요 항목으로 추출', 'proposed', '작가실(대본 추출)');
      n_prop := n_prop + 1;
    end if;
  end loop;
  return jsonb_build_object('new_entries', n_new, 'proposals', n_prop);
end $$;

revoke all on function public.agent_bible_overview(text, uuid, uuid) from public;
revoke all on function public.agent_bible_upsert(text, uuid, jsonb) from public;
grant execute on function public.agent_bible_overview(text, uuid, uuid) to anon, authenticated;
grant execute on function public.agent_bible_upsert(text, uuid, jsonb) to anon, authenticated;
