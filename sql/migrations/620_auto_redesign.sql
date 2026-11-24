-- 코디네이터가 고친 프롬프트로 사진 후보를 스스로 한 번 다시 만든다(후보가 전부 못 쓸 때).
alter table public.photo_designs add column if not exists auto_from uuid;
create or replace function public.agent_photo_redesign(p_token text, p_from uuid, p_prompt text) returns uuid
language plpgsql security definer set search_path = public as $$
declare v_id uuid; v_entry uuid; v_model text;
begin
  if public.nyx_agent_device(p_token) is null then raise exception 'invalid agent token'; end if;
  select entry_id, model into v_entry, v_model from public.photo_designs where id = p_from;
  if v_entry is null then return null; end if;
  -- 같은 사슬에서 자동 다시 만들기는 1번만(무한 반복·비용 방지)
  if exists (select 1 from public.photo_designs where id = p_from and auto_from is not null) then return null; end if;
  insert into public.photo_designs (entry_id, prompt, use_bible, model, auto_from)
  values (v_entry, left(p_prompt, 1800), false, v_model, p_from) returning id into v_id;
  insert into public.pipeline_tasks (kind, payload) values ('photo_design', jsonb_build_object('design_id', v_id));
  return v_id;
end $$;
revoke all on function public.agent_photo_redesign(text, uuid, text) from public;
grant execute on function public.agent_photo_redesign(text, uuid, text) to anon, authenticated;

-- 인물 영어 프롬프트를 코디네이터가 한 번씩 다시 감수(안전 규정에 걸릴 표현·실존 인물 이름을 완곡하게 고침)
insert into public.pipeline_tasks (kind, series_id, payload, not_before)
select 'coord_review', be.series_id, jsonb_build_object('version_id', bv.id, 'entry_id', be.id), now() + interval '3 minutes'
  from public.bible_entries be join public.bible_versions bv on bv.entry_id = be.id and bv.status = 'active'
 where be.category = 'character'
   and not exists (select 1 from public.pipeline_tasks t where t.kind = 'coord_review' and t.status in ('queued', 'running') and t.payload->>'entry_id' = be.id::text);

-- 내용 거절(403·nsfw 등)로 실패한 최근 사진 요청은 다시 맡긴다: 이제 코디네이터가 표현을 고쳐 다시 만든다
with d as (
  update public.photo_designs pd set status = 'queued', error = null
   where pd.status = 'failed' and pd.created_at > now() - interval '1 day'
     and pd.error ~* '(nsfw|safety|moderation|content|inappropriate|prohibited|403|forbidden)'
     and not exists (select 1 from public.pipeline_tasks t where t.kind = 'photo_design' and t.status in ('queued', 'running') and t.payload->>'design_id' = pd.id::text)
  returning pd.id
)
insert into public.pipeline_tasks (kind, payload, not_before)
select 'photo_design', jsonb_build_object('design_id', d.id), now() + interval '4 minutes' from d;
