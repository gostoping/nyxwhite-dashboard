-- 대표 요청(2026-11-27): 대본 탭에서 화 하나를 통째로 지우는 기능.
-- 그 화의 대본(모든 버전)·제작·편집·메모·작업·화 자체를 지운다. 비용 기록은 남는다(화 연결만 비워짐). 활동 기록은 지우지 않는다.
-- 지우기 막는 참조(초기 설정에서 만든 표 포함)를 찾아 함께 정리하는 도우미(내부 전용).
create or replace function public.nyx_purge(p_tbl regclass, p_cond text, p_depth int default 0, p_path regclass[] default '{}') returns void
language plpgsql security definer set search_path = public as $f$
declare r record;
begin
  if p_depth > 10 then raise exception '참조가 너무 깊음: %', p_tbl; end if;
  for r in
    select c.conrelid::regclass as child,
           (select string_agg(quote_ident(a.attname), ',' order by k.ord) from unnest(c.conkey) with ordinality k(att, ord)
              join pg_attribute a on a.attrelid = c.conrelid and a.attnum = k.att) as ccols,
           (select string_agg(quote_ident(a.attname), ',' order by k.ord) from unnest(c.confkey) with ordinality k(att, ord)
              join pg_attribute a on a.attrelid = c.confrelid and a.attnum = k.att) as pcols,
           c.confdeltype as t, c.conname as cname
      from pg_constraint c
     where c.contype = 'f' and c.confrelid = p_tbl and c.conrelid <> p_tbl
  loop
    if r.t in ('a', 'r', 'c') and r.child::text in ('activity_log', 'public.activity_log') then
      -- 활동 기록은 고치거나 지울 수 없는 기록이라 그대로 둔다: 지워지는 작품·화를 가리키는 연결(외래키)만 풀고, 기록 내용과 id는 남긴다
      execute format('alter table %s drop constraint %I', r.child, r.cname);
    elsif r.t in ('a', 'r', 'c') then
      if r.child = any(p_path) then
        -- 서로 참조하는 고리(예: 화 ↔ 현재 대본): 고리를 끊으려고 그 참조만 비운다
        execute format('update %s set (%s) = row(%s) where (%s) in (select %s from %s where %s)', r.child, r.ccols,
                       (select string_agg('null', ',') from regexp_split_to_table(r.ccols, ',')), r.ccols, r.pcols, p_tbl, p_cond);
      else
        perform public.nyx_purge(r.child, format('(%s) in (select %s from %s where %s)', r.ccols, r.pcols, p_tbl, p_cond), p_depth + 1, p_path || p_tbl);
      end if;
    end if;
  end loop;
  execute format('delete from %s where %s', p_tbl, p_cond);
end $f$;
revoke all on function public.nyx_purge(regclass, text, int, regclass[]) from public, anon, authenticated;

create or replace function public.owner_delete_episode(p_episode uuid) returns void
language plpgsql security definer set search_path = public as $$
begin
  if not public.nyx_is_owner() then raise exception '대표만 할 수 있습니다'; end if;
  if not exists (select 1 from public.episodes where id = p_episode) then raise exception '화를 찾지 못했습니다'; end if;
  perform public.owner_reset_episode(p_episode, false);
  perform public.nyx_purge('public.episodes'::regclass, format('id = %L', p_episode));
end $$;
revoke all on function public.owner_delete_episode(uuid) from public, anon;
grant execute on function public.owner_delete_episode(uuid) to authenticated;

-- 활동 기록이 작품·화·대본을 외래키로 가리키면 화를 지울 수 없으므로(활동 기록은 고칠 수 없음) 그 연결만 미리 푼다. 기록 내용은 그대로.
do $$
declare r record;
begin
  for r in select c.conname from pg_constraint c
            where c.contype = 'f' and to_regclass('public.activity_log') is not null and c.conrelid = 'public.activity_log'::regclass
              and c.confrelid in ('public.series'::regclass, 'public.episodes'::regclass, 'public.script_versions'::regclass) loop
    execute format('alter table public.activity_log drop constraint %I', r.conname);
  end loop;
end $$;
