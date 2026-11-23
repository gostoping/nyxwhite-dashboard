-- 바이블 항목별 사진 방식: auto = 사진 디자인으로 만든다, upload = 대표가 외부 사진을 직접 올린다(한꺼번에 만들기에서 빠진다)
alter table public.bible_entries add column if not exists photo_source text not null default 'auto';
alter table public.bible_entries drop constraint if exists bible_entries_photo_source_check;
alter table public.bible_entries add constraint bible_entries_photo_source_check check (photo_source in ('auto', 'upload'));

-- 사진을 올리면 작가실이 사진을 보고 고정 묘사(프롬프트)를 쓴다
alter table public.pipeline_tasks drop constraint if exists pipeline_tasks_kind_check;
alter table public.pipeline_tasks add constraint pipeline_tasks_kind_check
  check (kind in ('writers_room','director','prep_cut','generate','store','video_review','bgm','voice_design','voice_save','voice_cast',
                  'assemble','edit_command','photo_design','photo_set','bible_extract','photo_describe'));

-- 묘사를 쓸 재료: 항목 정보 + 대상 버전(사진 경로·기존 묘사)
create or replace function public.agent_bible_photo_info(p_token text, p_version uuid) returns jsonb
language plpgsql stable security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  return (select jsonb_build_object('version_id', bv.id, 'status', bv.status, 'description', bv.description, 'details', bv.details,
                 'image_paths', to_jsonb(coalesce(bv.image_paths, '{}'::text[])),
                 'entry_id', be.id, 'name', be.name, 'category', be.category, 'summary', be.summary, 'appears', be.appears,
                 'series', s.title)
            from public.bible_versions bv join public.bible_entries be on be.id = bv.entry_id join public.series s on s.id = be.series_id
           where bv.id = p_version);
end $$;

-- 사진을 보고 쓴 묘사를 그 버전에 채운다(대표가 사진을 올리며 적용한 버전)
create or replace function public.agent_bible_set_description(p_token text, p_version uuid, p_description text, p_details text) returns void
language plpgsql security definer set search_path = public as $$
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  if coalesce(trim(p_description), '') = '' then raise exception '묘사가 비어 있습니다'; end if;
  update public.bible_versions
     set description = left(p_description, 4000),
         details = coalesce(nullif(left(p_details, 4000), ''), details),
         change_reason = '대표가 사진 직접 올림 · 사진을 보고 묘사 자동 작성'
   where id = p_version and status in ('active', 'proposed');
end $$;

revoke all on function public.agent_bible_photo_info(text, uuid) from public;
revoke all on function public.agent_bible_set_description(text, uuid, text, text) from public;
grant execute on function public.agent_bible_photo_info(text, uuid) to anon, authenticated;
grant execute on function public.agent_bible_set_description(text, uuid, text, text) to anon, authenticated;
