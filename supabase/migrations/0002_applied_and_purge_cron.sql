-- JobRadar migration 0002 — applied tracking + stale-job purge cron.
-- Run once in the Supabase SQL editor (Dashboard > SQL Editor > New query).

-- 1. Track which jobs you've applied to. Preserved by the purge below.
alter table jobs add column if not exists applied boolean not null default false;

-- 2. pg_cron: hourly delete of un-applied jobs older than 3 calendar days.
--    Keeps today + the previous 3 dates (e.g. on the 13th: 13,12,11,10).
create extension if not exists pg_cron;

-- Drop any prior schedule with this name (no-op if absent).
select cron.unschedule(jobid) from cron.job where jobname = 'purge-stale-jobs';

select cron.schedule(
  'purge-stale-jobs',
  '0 * * * *',  -- top of every hour
  $$delete from jobs
      where applied = false
        and posted_at < ((now() at time zone 'utc')::date - interval '3 days')$$
);
