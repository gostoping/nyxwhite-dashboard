-- 인물 사진 후보를 만들 모델을 요청마다 고를 수 있게(soul2 기본 · nano(나노바나나 프로) · grok). 얼굴 학습(Soul ID)은 어떤 모델 사진으로도 된다.
alter table public.photo_designs add column if not exists model text;
