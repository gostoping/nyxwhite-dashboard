-- 인물은 무조건 필수(사진·얼굴 고정·감수 대상). 스타일·배경음악도 늘 필수.
create or replace function public.nyx_bible_essential_guard() returns trigger
language plpgsql as $$
begin
  if new.category in ('character', 'style', 'music') then new.essential := true; end if;
  if new.essential is null then new.essential := true; end if;
  return new;
end $$;
drop trigger if exists nyx_bible_essential_guard on public.bible_entries;
create trigger nyx_bible_essential_guard before insert or update on public.bible_entries
  for each row execute function public.nyx_bible_essential_guard();
update public.bible_entries set essential = true where category in ('character', 'style', 'music') and essential is distinct from true;
