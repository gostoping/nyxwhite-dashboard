-- 작업 진행 기록: 에이전트가 진행 상황(progress)을 바꿀 때마다 시간과 함께 쌓아 둔다(최근 300줄).
-- 화면에서 "어디서(어느 컴퓨터) 무엇을 어떻게 하고 있는지"를 기록으로 보여 주기 위함.
alter table public.pipeline_tasks add column if not exists progress_log jsonb not null default '[]'::jsonb;

create or replace function public.nyx_task_progress_log() returns trigger
language plpgsql as $$
declare v_line jsonb;
begin
  if new.progress is not null and new.progress is distinct from old.progress then
    v_line := jsonb_build_object('at', clock_timestamp(), 'text', left(new.progress, 400), 'device', new.device_id);
  elsif new.status is distinct from old.status and new.status in ('done', 'failed', 'cancelled') then
    v_line := jsonb_build_object('at', clock_timestamp(), 'text', case new.status when 'done' then '완료' when 'failed' then '실패: ' || left(coalesce(new.error, ''), 300) else '취소됨' end,
                                 'device', new.device_id);
  end if;
  if v_line is not null then
    new.progress_log := coalesce(old.progress_log, '[]'::jsonb) || jsonb_build_array(v_line);
    if jsonb_array_length(new.progress_log) > 300 then
      new.progress_log := (select jsonb_agg(x order by n) from jsonb_array_elements(new.progress_log) with ordinality t(x, n)
                           where n > jsonb_array_length(new.progress_log) - 300);
    end if;
  end if;
  return new;
end $$;

drop trigger if exists nyx_task_progress_log on public.pipeline_tasks;
create trigger nyx_task_progress_log before update on public.pipeline_tasks
  for each row execute function public.nyx_task_progress_log();
