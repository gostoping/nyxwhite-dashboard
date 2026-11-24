-- 바이블에 '배경' 분류 추가: 장소(인물이 연기하는 공간)와 별도로, 하늘·도시 전경·창밖 풍경·무대 배경 같은 화면 뒤쪽 그림.
-- 사진 만들기·사진 보고 묘사 쓰기·대본에서 찾기·넣을 곳 추천에서 장소·소품과 똑같이 다룬다.
alter table public.bible_entries drop constraint if exists bible_entries_category_check;
alter table public.bible_entries add constraint bible_entries_category_check
  check (category in ('character','location','background','prop','style','voice','glossary'));

create or replace function public.agent_bible_upsert(p_token text, p_series uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare it jsonb; v_id uuid; n_new int := 0; n_prop int := 0; v_cat text;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  for it in select * from jsonb_array_elements(coalesce(p_items, '[]')) loop
    v_cat := it->>'category';
    if v_cat not in ('character', 'location', 'background', 'prop') or coalesce(trim(it->>'name'), '') = '' then continue; end if;
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


create or replace function public.agent_bible_place_info(p_token text, p_entry uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
declare v_ser uuid;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select series_id into v_ser from public.bible_entries where id = p_entry;
  if v_ser is null then return null; end if;
  return jsonb_build_object(
    'entry', (select jsonb_build_object('id', be.id, 'name', be.name, 'category', be.category, 'summary', be.summary, 'appears', be.appears,
                     'description', bv.description, 'details', bv.details, 'version_id', bv.id)
                from public.bible_entries be left join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
               where be.id = p_entry),
    'series', (select jsonb_build_object('id', id, 'title', title) from public.series where id = v_ser),
    'others', (select coalesce(jsonb_agg(jsonb_build_object('category', be.category, 'name', be.name, 'summary', be.summary) order by be.category, be.sort_order), '[]')
                 from public.bible_entries be where be.series_id = v_ser and be.id <> p_entry and be.category in ('character', 'location', 'background', 'prop')),
    'episodes', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                   'text', (select v.content from public.script_versions v where v.episode_id = e.id
                            order by (v.status = 'confirmed') desc, v.created_at desc limit 1)) order by e.number), '[]')
                 from public.episodes e where e.series_id = v_ser));
end $$;

