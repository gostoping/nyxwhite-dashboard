-- 얼굴을 고정(Soul ID)한 인물은 대표가 감수 결과와 상관없이 그 사진으로 정한 것 → '사진 다시 만들기 추천'을 지우고, 앞으로도 올리지 않는다
create or replace function public.nyx_review_locked_guard() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.photo_redo and exists (select 1 from public.bible_versions v where v.entry_id = new.entry_id and v.status = 'active' and v.soul_id is not null) then
    new.photo_redo := false;
    new.photo_issues := '[]'::jsonb;
  end if;
  return new;
end $$;
drop trigger if exists nyx_review_locked_guard on public.bible_reviews;
create trigger nyx_review_locked_guard before insert or update on public.bible_reviews for each row execute function public.nyx_review_locked_guard();

create or replace function public.nyx_lock_clears_redo() returns trigger
language plpgsql security definer set search_path = public as $$
begin
  if new.status = 'active' and new.soul_id is not null then
    update public.bible_reviews set photo_redo = false, photo_issues = '[]'::jsonb where entry_id = new.entry_id and photo_redo;
  end if;
  return new;
end $$;
drop trigger if exists nyx_lock_clears_redo on public.bible_versions;
create trigger nyx_lock_clears_redo after insert or update of status, soul_id on public.bible_versions for each row execute function public.nyx_lock_clears_redo();

-- 지금 고정된 인물의 추천은 바로 지운다
update public.bible_reviews r set photo_redo = false, photo_issues = '[]'::jsonb
where r.photo_redo and exists (select 1 from public.bible_versions v where v.entry_id = r.entry_id and v.status = 'active' and v.soul_id is not null);
