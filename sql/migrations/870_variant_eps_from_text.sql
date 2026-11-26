-- 동일인의 다른 모습(예: 예뻐진 린)의 '나오는 화'를 바로잡는다.
-- ① 나오는 화가 비어 있는데 달라진 점·요약·묘사에 "8화부터/8화 이후/8화~"가 적혀 있으면 그 화부터(최대 200화)로 채운다.
-- ② "8~"로 저장했는데 그때 마지막 화까지만 들어간 경우(끝이 그 시리즈의 마지막 화) → 앞으로 생길 화까지(200화) 늘린다.
do $$
declare r record; n int; ep_from int; maxep int; v text;
begin
  for r in select be.id, be.name, be.series_id, be.apply_eps, be.change_note, be.summary, bv.description
             from public.bible_entries be left join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
            where be.category = 'character' and be.same_as is not null loop
    select coalesce(max(number), 0) into maxep from public.episodes where series_id = r.series_id;
    if r.apply_eps is null or cardinality(r.apply_eps) = 0 then
      ep_from := coalesce(substring(coalesce(r.change_note, '') from '(\d{1,3})\s*화\s*(?:부터|이후|~)'),
                          substring(coalesce(r.summary, '') from '(\d{1,3})\s*화\s*(?:부터|이후|~)'),
                          substring(coalesce(r.description, '') from '(\d{1,3})\s*화\s*(?:부터|이후|~)'))::int;
      if ep_from is not null then
        update public.bible_entries set apply_eps = array(select generate_series(ep_from, 200)) where id = r.id;
        raise notice '동일인 % : 글에 적힌 대로 %화부터 나오게 함', r.name, ep_from;
      else
        raise notice '동일인 % : 나오는 화가 비어 있고 글에도 "N화부터"가 없음 → 모든 화에 나옴(바이블에서 나오는 화를 적어 주세요)', r.name;
      end if;
    elsif (select max(x) from unnest(r.apply_eps) x) = maxep and (select max(x) - min(x) + 1 from unnest(r.apply_eps) x) = cardinality(r.apply_eps) and maxep < 200 then
      update public.bible_entries set apply_eps = array(select generate_series((select min(x) from unnest(r.apply_eps) x), 200)) where id = r.id;
      raise notice '동일인 % : %화~%화 → %화부터 앞으로 생길 화까지로 늘림', r.name, (select min(x) from unnest(r.apply_eps) x), maxep, (select min(x) from unnest(r.apply_eps) x);
    else
      raise notice '동일인 % : 나오는 화 % (그대로)', r.name, r.apply_eps;
    end if;
  end loop;
end $$;
