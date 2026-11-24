-- 바이블 '설정' 줄: 스타일·톤과 배경음악은 작가실이 대본을 읽고 먼저 추천안을 올리고, 대표가 고쳐서 적용한다.
-- 배경음악은 ElevenLabs Music으로 바로 만들 수 있다(편집실 '이 화 음악 톤' → ElevenLabs로 만들기).
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
    if not exists (select 1 from public.bible_versions where entry_id = v_id and status in ('active', 'proposed')) then
      insert into public.bible_versions (entry_id, description, details, change_reason, status, proposed_by)
      values (v_id, left(coalesce(nullif(it->>'description', ''), nullif(it->>'summary', ''), it->>'name'), 2000), left(it->>'details', 2000),
              coalesce(nullif(it->>'reason', ''), '대본에서 필요 항목으로 추출'), 'proposed', '작가실(대본 추출)');
      n_prop := n_prop + 1;
    end if;
  end loop;
  return jsonb_build_object('new_entries', n_new, 'proposals', n_prop);
end $$;

insert into public.price_book (provider, model, unit, usd, label, source) values
  ('elevenlabs', 'music', 'per_second', 0.0025, 'ElevenLabs Music 배경음악 (분당 $0.15 기준)', 'ElevenLabs 가격 참고 2026-11, 요금제에 따라 다름 — 확인 후 수정')
on conflict (provider, model, unit) do nothing;

alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe','bible_place','bible_prompt','api_check','bible_suggest'));

-- 소리 도구 선택(설정 → 제작): 대사 = elevenlabs | none, 배경음악 = elevenlabs | upload
update public.app_settings set value = value || jsonb_build_object('dialogue_tool', coalesce(value->>'dialogue_tool', 'elevenlabs'),
                                                                   'music_tool', coalesce(value->>'music_tool', 'elevenlabs'), 'music_model', coalesce(value->>'music_model', 'music_v1'))
 where key = 'generation';
