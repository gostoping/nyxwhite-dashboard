-- 동일인(원래 인물 + same_as로 이어진 다른 모습)은 목소리를 하나로 관리한다.
-- 어느 쪽에서든 목소리가 정해지면(적용 중인 버전의 voice_id) 같은 사람의 다른 카드에도 그대로 들어간다(새 버전 없이 제자리 반영).
create or replace function public.nyx_same_person_voice() returns trigger
language plpgsql security definer set search_path = public as $$
declare v_root uuid;
begin
  if new.status <> 'active' or pg_trigger_depth() > 3 then return null; end if;
  select coalesce(same_as, id) into v_root from public.bible_entries where id = new.entry_id;
  if new.voice_id is null then   -- 새로 적용된 카드에 목소리가 없으면 → 같은 사람의 목소리를 물려받는다
    update public.bible_versions set voice_id = (
      select v.voice_id from public.bible_versions v join public.bible_entries e on e.id = v.entry_id
      where v.status = 'active' and v.voice_id is not null and v.entry_id <> new.entry_id and (e.id = v_root or e.same_as = v_root)
      order by (e.id = v_root) desc limit 1)
    where id = new.id and exists (select 1 from public.bible_versions v join public.bible_entries e on e.id = v.entry_id
      where v.status = 'active' and v.voice_id is not null and v.entry_id <> new.entry_id and (e.id = v_root or e.same_as = v_root));
    return null;
  end if;
  update public.bible_versions v set voice_id = new.voice_id
  where v.status = 'active' and v.voice_id is distinct from new.voice_id and v.entry_id <> new.entry_id
    and v.entry_id in (select id from public.bible_entries where id = v_root or same_as = v_root);
  return null;
end $$;
drop trigger if exists nyx_same_person_voice on public.bible_versions;
create trigger nyx_same_person_voice after insert or update of status, voice_id on public.bible_versions
  for each row execute function public.nyx_same_person_voice();

-- 지금 어긋나 있는 동일인 목소리 맞추기: 원래 인물 목소리가 있으면 그것으로, 없으면 다른 모습에 정해진 목소리로
update public.bible_versions v set voice_id = b.voice_id
from public.bible_entries e join public.bible_versions b on b.entry_id = e.same_as and b.status = 'active' and b.voice_id is not null
where v.entry_id = e.id and v.status = 'active' and v.voice_id is distinct from b.voice_id;
update public.bible_versions b set voice_id = x.voice_id
from (select distinct on (e.same_as) e.same_as root, v.voice_id from public.bible_entries e
      join public.bible_versions v on v.entry_id = e.id and v.status = 'active' and v.voice_id is not null
      where e.same_as is not null order by e.same_as, v.created_at desc) x
where b.entry_id = x.root and b.status = 'active' and b.voice_id is null;

-- 동일인의 다른 모습 카드에서 '추천하기'를 눌러도 되게: 시리즈 인물 목록에 same_as를 함께 준다(자동 추천은 원래 인물만)
create or replace function public.agent_series_cast(p_token text, p_series uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return jsonb_build_object(
    'series', (select jsonb_build_object('id', id, 'title', title) from public.series where id = p_series),
    'characters', (select coalesce(jsonb_agg(jsonb_build_object('entry_id', be.id, 'name', be.name, 'summary', be.summary, 'same_as', be.same_as,
                      'description', bv.description, 'details', bv.details, 'voice_id', bv.voice_id,
                      'open_design', exists (select 1 from public.voice_designs d where d.entry_id = be.id and d.status in ('queued','ready','saving','saved')))
                    order by be.sort_order, be.created_at), '[]')
                   from public.bible_entries be
                   left join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
                   where be.series_id = p_series and be.category = 'character'),
    'scripts', (select coalesce(jsonb_agg(jsonb_build_object('number', e.number, 'title', e.title,
                   'text', left((select v.content from public.script_versions v where v.episode_id = e.id
                                 order by (v.status = 'confirmed') desc, v.created_at desc limit 1), 12000)) order by e.number), '[]')
                from (select * from public.episodes where series_id = p_series order by number limit 3) e));
end $$;
