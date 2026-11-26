-- 870에서 못 찾은 경우: 세부 사항·영어 프롬프트·아직 적용 전 수정안까지 보고 "8화부터 / 8회부터 / from episode 8 / ep 8 onward"를 찾는다.
do $$
declare r record; ep_from int; txt text;
begin
  for r in select be.id, be.name from public.bible_entries be
            where be.category = 'character' and be.same_as is not null and (be.apply_eps is null or cardinality(be.apply_eps) = 0) loop
    select string_agg(concat_ws(' ', bv.description, bv.details, bv.prompt_en, bv.change_reason), ' ') into txt
      from public.bible_versions bv where bv.entry_id = r.id;
    txt := concat_ws(' ', txt, (select concat_ws(' ', summary, change_note) from public.bible_entries where id = r.id));
    ep_from := coalesce(
      substring(txt from '(\d{1,3})\s*(?:화|회|부)\s*(?:부터|이후|~|째부터)'),
      substring(txt from '(?i)from\s+(?:episode|ep\.?)\s*(\d{1,3})'),
      substring(txt from '(?i)(?:episode|ep\.?)\s*(\d{1,3})\s*(?:onward|onwards|and after|\+|~)'))::int;
    if ep_from is not null then
      update public.bible_entries set apply_eps = array(select generate_series(ep_from, 200)) where id = r.id;
      raise notice '동일인 % : 바이블 글에서 찾아 %화부터 나오게 함', r.name, ep_from;
    else
      raise notice '동일인 % : 화 번호를 찾지 못함 · 글 앞부분: %', r.name, left(regexp_replace(coalesce(txt, ''), '\s+', ' ', 'g'), 160);
    end if;
  end loop;
end $$;
