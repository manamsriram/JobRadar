-- Adds the description column that scrapers now populate (body-level filtering + UI).
-- PostgREST rejects upserts with unknown columns, so apply this before deploying
-- the description-aware backend.
alter table public.jobs add column if not exists description text default '';
