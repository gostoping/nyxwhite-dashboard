-- API 키 보관: 대표만 넣을 수 있고, 화면에서는 다시 볼 수 없으며, 에이전트(토큰)만 꺼내 쓴다.
-- Supabase Vault(암호화 보관)를 쓰고, 쓸 수 없는 환경이면 접근이 완전히 막힌 표에 보관한다.

create table if not exists public.app_secrets (
  name       text primary key,
  value      text not null,
  updated_at timestamptz not null default now()
);
alter table public.app_secrets enable row level security;           -- 정책 없음 = 아무도 직접 못 읽음
revoke all on public.app_secrets from anon, authenticated;

create table if not exists public.app_secret_meta (                 -- 화면 표시용: 마지막 4자리와 날짜만
  name       text primary key,
  last4      text,
  storage    text,
  updated_at timestamptz not null default now()
);
alter table public.app_secret_meta enable row level security;
drop policy if exists nyx_read on public.app_secret_meta;
create policy nyx_read on public.app_secret_meta for select to authenticated using (public.nyx_is_owner());
grant select on public.app_secret_meta to authenticated;

create or replace function public.nyx_allowed_secret(p_name text) returns boolean language sql immutable as $$
  select p_name in ('anthropic_api_key', 'higgsfield_key_id', 'higgsfield_key_secret', 'elevenlabs_api_key');
$$;

create or replace function public.set_secret(p_name text, p_value text) returns void
language plpgsql security definer set search_path = public as $$
declare v_id uuid; v_store text := 'vault';
begin
  if not public.nyx_is_owner() then raise exception 'row-level security: 대표만 키를 등록할 수 있습니다'; end if;
  if not public.nyx_allowed_secret(p_name) then raise exception '허용되지 않은 키 이름입니다'; end if;
  if coalesce(trim(p_value), '') = '' then raise exception '키가 비어 있습니다'; end if;
  begin
    execute 'select id from vault.secrets where name = $1' into v_id using 'nyx_' || p_name;
    if v_id is null then
      execute 'select vault.create_secret($1, $2, $3)' using trim(p_value), 'nyx_' || p_name, 'NYX ' || p_name;
    else
      execute 'select vault.update_secret($1, $2)' using v_id, trim(p_value);
    end if;
    delete from public.app_secrets where name = p_name;
  exception when others then
    v_store := 'table';
    insert into public.app_secrets (name, value) values (p_name, trim(p_value))
    on conflict (name) do update set value = excluded.value, updated_at = now();
  end;
  insert into public.app_secret_meta (name, last4, storage) values (p_name, right(trim(p_value), 4), v_store)
  on conflict (name) do update set last4 = excluded.last4, storage = excluded.storage, updated_at = now();
  perform public.nyx_log('대표', 'API 키 등록: ' || p_name || ' (…' || right(trim(p_value), 4) || ')', null, null);
end $$;

create or replace function public.nyx_read_secret(p_name text) returns text
language plpgsql security definer set search_path = public as $$
declare v text;
begin
  begin
    execute 'select decrypted_secret from vault.decrypted_secrets where name = $1' into v using 'nyx_' || p_name;
  exception when others then v := null;
  end;
  if v is null then select value into v from public.app_secrets where name = p_name; end if;
  return v;
end $$;
revoke all on function public.nyx_read_secret(text) from public, anon, authenticated;

create or replace function public.agent_get_secrets(p_token text, p_names text[]) returns jsonb
language plpgsql security definer set search_path = public as $$
declare v_dev uuid := public.nyx_agent_device(p_token); r jsonb := '{}'::jsonb; n text;
begin
  if v_dev is null then raise exception 'invalid agent token'; end if;
  foreach n in array p_names loop
    if public.nyx_allowed_secret(n) then r := r || jsonb_build_object(n, public.nyx_read_secret(n)); end if;
  end loop;
  return r;
end $$;

revoke all on function public.set_secret(text, text) from public, anon;
grant execute on function public.set_secret(text, text) to authenticated;
revoke all on function public.agent_get_secrets(text, text[]) from public;
grant execute on function public.agent_get_secrets(text, text[]) to anon, authenticated;

-- 에이전트 명령에 '키 연결 시험' 추가
alter table public.agent_commands drop constraint if exists agent_commands_command_check;
alter table public.agent_commands add constraint agent_commands_command_check check (command in ('ping','restart','update','test_keys'));

-- 단가표: Claude 모델별 단가 (2026-11 확인)
delete from public.price_book where provider = 'anthropic' and model = 'claude' and usd is null;
insert into public.price_book (provider, model, unit, usd, label, source) values
  ('anthropic', 'claude-opus-5-5',   'per_1m_input_tokens',  4,    'Claude Opus 5.5 입력',   'Claude 가격표 2026-11'),
  ('anthropic', 'claude-opus-5-5',   'per_1m_output_tokens', 20,   'Claude Opus 5.5 출력',   'Claude 가격표 2026-11'),
  ('anthropic', 'claude-sonnet-5-5', 'per_1m_input_tokens',  2,    'Claude Sonnet 5.5 입력', 'Claude 가격표 2026-11'),
  ('anthropic', 'claude-sonnet-5-5', 'per_1m_output_tokens', 10,   'Claude Sonnet 5.5 출력', 'Claude 가격표 2026-11'),
  ('anthropic', 'claude-haiku-5-5',  'per_1m_input_tokens',  0.10, 'Claude Haiku 5.5 입력',  'Claude 가격표 2026-11 (10만 토큰 이하)'),
  ('anthropic', 'claude-haiku-5-5',  'per_1m_output_tokens', 0.50, 'Claude Haiku 5.5 출력',  'Claude 가격표 2026-11 (10만 토큰 이하)')
on conflict (provider, model, unit) do update set usd = excluded.usd, label = excluded.label, source = excluded.source, updated_at = now();
