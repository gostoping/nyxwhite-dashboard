-- 대표 요청(2026-11-27): 바이블 카드에 영어 프롬프트의 한국어판을 보여 주고, 한국어로 고치면 영어 프롬프트에 반영한다.
alter table public.bible_versions add column if not exists prompt_ko text;

create or replace function public.agent_bible_set_prompt_ko(p_token text, p_version uuid, p_ko text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  update public.bible_versions set prompt_ko = nullif(left(coalesce(p_ko, ''), 6000), '') where id = p_version;
end $$;
revoke all on function public.agent_bible_set_prompt_ko(text, uuid, text) from public;
grant execute on function public.agent_bible_set_prompt_ko(text, uuid, text) to anon, authenticated;

-- 영어 프롬프트가 바뀌었는데 한국어판은 그대로면(묘사 수정 등) 묵은 한국어판을 지운다(카드에서 '한글로 보기'로 다시 만듦)
create or replace function public.nyx_bible_promptko_clear() returns trigger
language plpgsql as $$
begin
  if new.prompt_en is distinct from old.prompt_en and new.prompt_ko is not distinct from old.prompt_ko then
    new.prompt_ko := null;
  end if;
  return new;
end $$;
drop trigger if exists nyx_bible_promptko_clear on public.bible_versions;
create trigger nyx_bible_promptko_clear before update on public.bible_versions
  for each row execute function public.nyx_bible_promptko_clear();
