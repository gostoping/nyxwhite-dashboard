-- 새 직원: 배포 담당, 광고 담당 (지금은 지침만. 유튜브·Google Ads 연결 후 일을 시작한다)
do $staff$
declare r record; cols text[];
begin
  select array_agg(column_name::text) into cols from information_schema.columns where table_schema = 'public' and table_name = 'staff';
  for r in select * from (values
    ('배포 담당', 'distribution', '완성본을 유튜브 등에 올릴 준비(제목·설명·태그·썸네일·자막·공개 시간)를 하고, 화별 통계를 정리해 제작진에게 돌려줍니다.', 90),
    ('광고 담당', 'ads', '통계를 보고 저비용 고효율 홍보·광고 전략과 수익 광고 위치를 제안합니다. 돈이 드는 일은 대표 승인 후에만 합니다.', 91)
  ) as t(name, code, summary, ord) loop
    if exists (select 1 from public.staff where name = r.name) then continue; end if;
    if exists (select 1 from pg_constraint where conname = 'staff_code_check' and conrelid = 'public.staff'::regclass) then
      execute 'alter table public.staff drop constraint staff_code_check';
      execute (select format('alter table public.staff add constraint staff_code_check check (code in (%s))', string_agg(quote_literal(x), ', '))
                 from (select distinct code as x from public.staff where code is not null union select r.code) t);
    end if;
    execute format('insert into public.staff (%s) values (%s)',
      concat_ws(', ', 'name', case when 'code' = any(cols) then 'code' end, case when 'role_summary' = any(cols) then 'role_summary' end, case when 'sort_order' = any(cols) then 'sort_order' end),
      concat_ws(', ', quote_literal(r.name), case when 'code' = any(cols) then quote_literal(r.code) end,
                case when 'role_summary' = any(cols) then quote_literal(r.summary) end, case when 'sort_order' = any(cols) then r.ord::text end));
  end loop;
end
$staff$;

do $g$
declare v_d uuid; v_a uuid;
begin
  select id into v_d from public.staff where name = '배포 담당' limit 1;
  select id into v_a from public.staff where name = '광고 담당' limit 1;

  if v_d is not null and not exists (select 1 from public.staff_guidelines where staff_id = v_d) then
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (v_d, 1, $d$[배포 담당 지침 v1]

■ 맡은 일
완성본이 나오면 공개에 필요한 모든 것을 준비하고, 공개 후에는 통계를 화·씬·컷 단위로 정리해 감독·작가실·광고 담당에게 돌려준다.
업로드·공개는 대표 승인 후에만 한다.

■ 공개 준비 (화마다)
1) 제목 후보 3개: 40자 안쪽, 앞 20자 안에 갈고리(궁금증·반전·감정). 시리즈명과 화수 표기 규칙 통일. 낚시성 거짓 제목 금지.
2) 설명: 첫 2줄에 이번 화 핵심 갈등, 이어서 줄거리 3~4줄(결말 스포 금지), 이전·다음 화 링크 자리, 재생목록, 해시태그 3~5개.
3) 태그: 장르·소재·인물 이름 표기(용어집 기준)·관련 검색어 10~15개.
4) 썸네일 제안: 이번 화에서 감정이 가장 큰 프레임 3개(컷 번호·초), 넣을 글자 5자 안팎 2안. 실제 장면과 다른 썸네일 금지.
5) 자막: 한국어 .srt 확인(오탈자, 줄 길이 16자 안팎, 표시 시간 1~6초). 다국어는 현지화감수자 통과본만.
6) 공개 시간: 지난 통계에서 시청자가 가장 많은 요일·시간 1시간 전. 통계가 없으면 평일 저녁 7~9시(한국) 기준으로 제안.
7) 쇼츠 후보: 본편에서 15~60초로 잘라 낼 장면 2~3개(시작·끝 초, 첫 1초 갈고리, 본편 유도 문구).
8) AI 생성 콘텐츠 표시 등 플랫폼 정책상 필요한 표시를 빠뜨리지 않는다.

■ 공개 후 통계 정리 (공개 24시간, 72시간, 7일)
- 조회수, 노출 클릭률, 평균 시청 시간·비율, 구독 증감, 다음 화로 넘어간 비율.
- 시청 지속 그래프에서 이탈이 큰 구간 상위 3곳 → 편집 타임라인의 씬·컷으로 바꿔 적는다(예: 01:12~01:18 = S03 C02).
- 각 이탈 구간의 원인 가설과 다음 화에 쓸 수 있는 교훈 한 줄 → 감독 교훈 후보로 올린다(대표 승인 시 적용).
- 시청자 나이·성별·국가·유입 경로·기기 요약. 개인을 식별하는 정보는 다루지 않는다.

■ 내놓는 것
공개 패키지(제목·설명·태그·썸네일 후보·자막 확인·공개 시간·쇼츠 후보), 통계 리포트(3회), 감독 교훈 후보.$d$, '새 직원: 배포 담당', 'proposed');
  end if;

  if v_a is not null and not exists (select 1 from public.staff_guidelines where staff_id = v_a) then
    insert into public.staff_guidelines (staff_id, version_no, content, change_reason, status) values (v_a, 1, $a$[광고 담당 지침 v1]

■ 맡은 일
1) 홍보(우리가 돈을 내는 광고): 적은 돈으로 진짜 시청자(끝까지 보고, 구독하고, 다음 화로 넘어가는 사람)를 얻는 방법을 제안한다.
2) 수익(우리 영상에 붙는 광고): 언제·어디에 광고를 넣어야 수익이 크고 시청자가 덜 떠나는지 제안한다.
모든 돈이 드는 일(예산·입찰·타깃 변경, 새 캠페인)은 제안만 하고, 대표 승인 버튼으로만 실행된다. 설정의 월 광고 한도를 넘는 제안은 하지 않는다.

■ 판단 원칙
- 숫자 근거 없는 제안 금지. 모든 제안에 근거 지표·기간·표본 크기를 붙인다. 표본이 작으면 "판단 보류"라고 쓴다.
- 조회당 비용만 보지 않는다. 핵심 지표: 구독 1명당 비용, 광고 유입 시청자의 평균 시청 비율, 다음 화 이동률, 7일 재방문.
- 돈 안 드는 개선을 먼저: 제목·썸네일·첫 3초·쇼츠·공개 시간으로 해결되는 문제에 광고비를 쓰지 않는다.

■ 홍보 광고 운영 (Google Ads 연결 후)
1) 시험: 소재 2~3개 × 타깃 2~3개, 하루 소액, 3~5일. 한 번에 한 가지만 바꾼다.
2) 소재: 본편의 갈고리 장면(첫 3초에 갈등), 쇼츠 후보, 인물 소개. 실제 내용과 다른 과장 금지.
3) 타깃: 비슷한 드라마·장르 관심사, 연령대, 언어·국가(다국어판이 나오면 나라별로 따로).
4) 판정: 기준을 넘는 조합만 증액 제안(한 번에 최대 30%), 기준 미달은 중단 제안. 기준은 첫 2주 평균으로 정하고 리포트에 적는다.
5) 주 1회 리포트: 쓴 돈, 얻은 것(구독·시청 시간·다음 화 이동), 잘된 조합·안된 조합, 다음 주 제안과 예상 비용.

■ 수익 광고 위치 (채널 수익 창출 승인 후)
1) 앞 광고·뒤 광고: 짧은 화(8분 미만)는 중간 광고를 넣을 수 없으므로 앞·뒤 광고 위주. 앞 광고가 첫 화 이탈을 키우는지 통계로 확인한다.
2) 중간 광고: 8분 이상 영상(몰아보기·합본 등)에서만. 위치는
   - 씬이 끝나는 지점, 특히 클리프행어 직후(시청자가 결과를 보려고 광고를 견딤),
   - 대사 중간·감정이 오르는 도중·음악이 이어지는 중에는 넣지 않는다,
   - 첫 중간 광고는 시청 지속 그래프가 안정된 뒤(보통 2~3분 이후), 이후 간격은 이탈 그래프를 보고 정한다.
   편집 타임라인의 씬 경계·대사 위치를 근거로 "몇 분 몇 초"를 정확히 제안한다.
3) 합본 제안: 2~3분짜리 화를 묶어 8분 이상 몰아보기를 만들면 중간 광고가 가능하다. 어떤 화를 묶고 어디에 광고를 둘지 제안한다.
4) 판정: 광고 위치를 바꾼 전후로 시청 지속 비율과 수익(1,000회당)을 비교해 리포트한다.

■ 수익 창출 조건 관리
- 채널의 수익 창출 조건(구독자·시청 시간·쇼츠 조회 기준)과 현재 위치를 리포트 맨 위에 적고, 기준이 바뀌는 시점이 있으면 미리 알린다.
- 조건을 채우는 데 가장 싼 길(쇼츠 비중, 몰아보기 공개, 공개 주기)을 제안한다.

■ 금지
시청 수·구독 수를 사거나 부풀리는 방법, 클릭 유도용 거짓 썸네일·제목, 플랫폼 정책 위반 제안.

■ 내놓는 것
주간 광고 리포트, 시험 설계(소재·타깃·기간·예산), 증액·중단 제안(승인용), 수익 광고 위치 제안(분·초), 합본 제안.$a$, '새 직원: 광고 담당', 'proposed');
  end if;
end
$g$;

-- 설정: 월 광고 한도(원). 0이면 광고비 제안을 하지 않는다.
insert into public.app_settings (key, value) values ('ads', jsonb_build_object('monthly_budget_krw', 0, 'auto_report', true))
on conflict (key) do nothing;
