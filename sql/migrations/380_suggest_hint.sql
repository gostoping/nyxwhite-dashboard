-- 스타일·톤/배경음악 추천: 대표 제안(hint) 반영, '다시 받기'는 적용본이 있어도 새 수정안으로 올린다
create or replace function public.agent_bible_upsert(p_token text, p_series uuid, p_items jsonb) returns jsonb
language plpgsql security definer set search_path = public as $$
declare it jsonb; v_id uuid; n_new int := 0; n_prop int := 0; v_cat text;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  for it in select * from jsonb_array_elements(coalesce(p_items, '[]')) loop
    v_cat := it->>'category';
    if v_cat not in ('character', 'location', 'background', 'prop', 'style', 'music') or coalesce(trim(it->>'name'), '') = '' then continue; end if;
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
    -- 묘사가 비어 와도 수정안을 만든다(없으면 감독이 그 항목을 못 봄): 한 줄 설명 → 이름 순으로 채운다
    -- 다시 추천(force_proposal)이면 적용본이 있어도 새 수정안을 올린다(같은 내용 대기 중이면 생략)
    if (coalesce(it->>'force_proposal', '') = 'true'
        and not exists (select 1 from public.bible_versions where entry_id = v_id and status = 'proposed' and description = it->>'description'))
       or not exists (select 1 from public.bible_versions where entry_id = v_id and status in ('active', 'proposed')) then
      insert into public.bible_versions (entry_id, description, details, change_reason, status, proposed_by)
      values (v_id, left(coalesce(nullif(it->>'description', ''), nullif(it->>'summary', ''), it->>'name'), 2000), left(it->>'details', 2000),
              coalesce(nullif(it->>'reason', ''), '대본에서 필요 항목으로 추출'), 'proposed', '작가실(대본 추출)');
      n_prop := n_prop + 1;
    end if;
  end loop;
  return jsonb_build_object('new_entries', n_new, 'proposals', n_prop);
end $$;
