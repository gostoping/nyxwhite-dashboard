-- 대표 지시(2026-11-26): '예뻐진 린'은 8화부터(성형수술 이후). 그 전 화는 원래 린.
do $$
declare n int;
begin
  update public.bible_entries set apply_eps = array(select generate_series(8, 200)),
         change_note = case when coalesce(change_note, '') ~ '8\s*화' then change_note
                            else concat_ws(E'\n', nullif(change_note, ''), '8화부터 성형수술 이후의 린(대표 지시)') end
   where category = 'character' and name = '예뻐진 린';
  get diagnostics n = row_count;
  raise notice '예뻐진 린: 8화부터 나오게 % 항목 저장', n;
end $$;
