-- 하루기록(dailylog) 앱 — Supabase 마이그레이션
-- 기존 하루 프로젝트(mfgiesampazjzgfliuje)의 SQL Editor에서 그대로 실행하세요.
-- 하루 앱과 같은 계정(auth.users)을 그대로 씁니다. 새 테이블/버킷만 추가됩니다.

create table if not exists public.daily_logs (
  owner uuid not null references auth.users(id) on delete cascade,
  date text not null,
  content text not null default '',
  updated timestamptz not null default now(),
  primary key (owner, date)
);

alter table public.daily_logs enable row level security;

create policy "daily_logs own select" on public.daily_logs
  for select using (owner = auth.uid());
create policy "daily_logs own insert" on public.daily_logs
  for insert with check (owner = auth.uid());
create policy "daily_logs own update" on public.daily_logs
  for update using (owner = auth.uid()) with check (owner = auth.uid());
create policy "daily_logs own delete" on public.daily_logs
  for delete using (owner = auth.uid());

-- 사진 저장용 스토리지 버킷 (본인 폴더에만 업로드, 읽기는 공개)
insert into storage.buckets (id, name, public)
values ('dailylog', 'dailylog', true)
on conflict (id) do nothing;

create policy "dailylog storage own insert" on storage.objects
  for insert with check (bucket_id = 'dailylog' and (storage.foldername(name))[1] = auth.uid()::text);
create policy "dailylog storage public read" on storage.objects
  for select using (bucket_id = 'dailylog');
create policy "dailylog storage own delete" on storage.objects
  for delete using (bucket_id = 'dailylog' and (storage.foldername(name))[1] = auth.uid()::text);
