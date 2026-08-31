# Outreach automation design

Adapts `jobrader_outreach_design_doc.md` to what JobRadar actually is. The
source doc was written against a generic web-app stack — ORM models, a job
queue, migrations, multi-tenant admin, per-user settings. JobRadar has none
of those. This spec keeps the source doc's *ideas* (search-first discovery,
Hunter for verification only, learned per-domain email patterns, human review
before send) and drops its *machinery*.

## Problem

`enricher.py` already finds contacts, but it is Hunter-only: one contact per
credit, 50 credits/month, and Hunter returns whoever it has — often not a
recruiter, sometimes nobody. After finding a contact there is nothing to do
with it: no draft, no send, no record of having reached out.

Goal: from an applied job, get to a reviewed, personalized outreach email to a
plausible recruiter, spending as few Hunter credits as possible, without ever
sending anything Sriram has not read.

## What the source doc assumes vs. what exists

| Source doc assumes | JobRadar reality | Decision |
|---|---|---|
| Postgres + ORM + migrations | JSON files under `DATA_DIR` via `state.py` (atomic writes, backups) | Keep JSON files. No DB. |
| Background job queue, dead-letter, retries | `asyncio` + GitHub Actions cron | Reuse. Enrichment runs inline on request; batch work is a workflow. |
| Multi-tenant users, per-user settings, `approved_by_user_id` | Single user (Sriram). Auth is one shared `INGEST_TOKEN` / `INTERNAL_KEY` | Drop all per-user fields and admin/user setting split. Env vars are the config. |
| Separate search provider abstraction | `board_scraper.py` now has a Playwright Google search with CAPTCHA backoff | Reuse that, don't build a second search client. |
| Gmail OAuth + refresh-token encryption | `notifier.py` sends via Gmail SMTP app password | Reuse SMTP. No OAuth, no token storage. |
| Gemini for drafting | `ai_match.py` — ordered free OpenAI-compatible providers with per-provider daily budgets, degrades to `None` | Reuse that provider ladder. |
| 10 new tables | — | 3 JSON files. |

## Non-goals

Everything the source doc lists as out of scope, plus: no admin dashboard, no
per-user config, no cost-estimate accounting beyond the existing Hunter credit
ledger, no HTML email, no bulk sending of any kind.

## Architecture

One new module, `backend/outreach.py`, plus additions to `enricher.py`. The
flow is a straight line, each step degrading to "nothing found" rather than
raising — same convention as every scraper here:

```
job (already applied)
  └─ resolve_domain()                      [enricher.py, exists]
      └─ discover_candidates()             [outreach.py — Google via Playwright]
          └─ generate_guesses()            [outreach.py — pattern engine, pure]
              └─ rank_guesses()            [outreach.py — pure, uses pattern memory]
                  └─ verify_top()          [enricher.py — Hunter /email-verifier]
                      └─ learn_pattern()   [outreach.py — updates pattern memory]
                          └─ draft()       [outreach.py — via ai_match's provider ladder]
                              └─ review UI → send_outreach()  [notifier.py SMTP]
```

Pure functions (pattern generation, ranking, pattern learning, name parsing)
are separated from I/O so they are testable without network — the existing
convention in `test_scraper.py` and `test_board_scraper.py`.

### Why discovery reuses the Google search

Hunter is the expensive path (1 credit per returned email, 50/month). Google
is already wired up here with a real Chromium fetch and CAPTCHA backoff, and
recruiter discovery is the same shape as job discovery: a boolean query, a
SERP, a parse. Reuse `_search_url` / the Playwright fetch and add a recruiter
query builder. Same 32-word cap applies.

Recruiter discovery shares the job search's CAPTCHA backoff key
(`google-search`) rather than getting its own. The block is per-IP, not
per-query: Google walls the runner, not the search string. A separate key
would only mean discovery walks into a wall the job search has already found,
spends a request confirming it, and gets itself blocked too — two keys
learning the same fact twice. The cost is that both go quiet together, which
they would anyway.

One consequence to handle in the UI: discovery is user-triggered, so when it
is backing off it must say so ("Google search is rate-limited until 18:00")
rather than returning zero candidates as though the company simply had no
recruiters. A scheduled job search can fail quietly; an interactive click
cannot.

Recruiter query templates (each must fit the cap — one query per run, not one
per template; templates are tried in order until candidates are found):

```
site:linkedin.com/in "<company>" (recruiter OR "talent acquisition" OR sourcer)
site:<domain> (recruiting OR "talent acquisition" OR careers)
"<company>" (recruiter OR "university recruiter" OR "campus recruiter")
```

Discovery is cached per domain for 7 days in the contacts file, so a second
job at the same company costs zero searches and zero credits.

## Data

Three new files under `DATA_DIR`, all through `state.py`'s atomic-write
helpers. No schema migrations — a missing key reads as its default.

### `outreach_patterns.json` — the asset worth keeping

Keyed by root domain. This is the part of the source doc most worth having:
one verified pattern turns every future contact at that company into a free,
high-confidence guess.

```json
{
  "acme.com": {
    "patterns": {
      "first.last": {"verified": 2, "failed": 0, "last_verified_at": "..."},
      "flast":      {"verified": 0, "failed": 1, "last_verified_at": null}
    },
    "accept_all": false,
    "updated_at": "..."
  }
}
```

Rules, simplified from the source doc's score formula to something with no
tunable coefficients to get wrong:

- A pattern is `verified` at ≥2 successful verifications with no conflicting
  evidence, `probable` at 1, else `unknown`.
- `accept_all: true` caps every pattern at `probable` — an accept-all domain
  says yes to anything, so a "valid" result proves nothing.
- Failures decrement standing but never delete a pattern; a pattern with more
  failures than successes ranks below unknown ones.
- Entries older than 180 days drop to `probable` on read (staleness decay),
  rather than being recomputed on a schedule.

### Auto-promotion (decided)

A `verified` domain pattern makes new contacts at that domain send-eligible
**without** spending a credit to verify each person. This is what makes the
50-credit monthly budget go far: a company costs 2 verifications once, then
every future contact there is free.

The guard rails that make this safe:

- Only a `verified` pattern auto-promotes. `probable` (one sample) does not —
  a single success can be coincidence.
- An `accept_all` domain never auto-promotes, since it caps at `probable` by
  the rule above. This is the main false-positive risk and it is closed.
- A pattern that has any recorded failure at that domain does not auto-promote
  until it has 2 clean successes *after* the most recent failure.
- Auto-promotion requires a clean name parse. A name that does not split into
  a confident first/last falls back to manual verification — the pattern may
  be right while the name plugged into it is wrong, which is the other real
  failure mode.
- Staleness decay applies first: a 180-day-old pattern is `probable`, so it
  stops auto-promoting until re-verified. Companies change email providers.
- Auto-promoted guesses are labelled as such in the UI and in the outreach
  log (`send_eligibility: auto_promoted_pattern`), never displayed as if the
  specific address had been verified. Review before send is still required —
  auto-promotion removes a credit cost, not the human gate.

A bounced auto-promoted send, when observed, records a pattern failure, which
demotes the pattern per the rule above and stops further auto-promotion at
that domain until it re-earns two clean successes.

### `outreach_log.json` — dedup and audit in one file

One record per draft, whether or not it was sent: job id, domain, recipient
email, contact name/title, source URL, guessed pattern, verification status
and confidence, subject, body, draft model, status
(`draft` / `approved` / `sent` / `rejected`), timestamps.

Dedup keys, checked before drafting: (job id + recipient email) never repeats;
(domain + recipient email) not within a 30-day cooldown. A blocked duplicate
surfaces the prior record in the UI rather than silently skipping.

### `company_contacts.json` — extended, not replaced

Already exists. Adds discovered (not just Hunter-returned) candidates with
`source_url`, `source_type`, `title`, `discovered_at`, and `origin`
(`hunter` | `search`), so the UI can say where a contact came from and the
existing cache-read endpoint keeps working unchanged.

## Ranking

Two ranks, both pure functions returning `(score, reasons: list[str])` so the
UI can show *why* — the source doc's explainability requirement, kept.

**Contacts** — title relevance first (recruiter/TA/sourcer > people ops/HR >
hiring manager/EM, and the last group only when the first two are empty),
then source quality (company domain > LinkedIn profile > anything else), then
freshness, then whether the name parses into a clean first/last.

**Guesses** — a verified domain pattern outranks everything. Then probable
patterns, then the global frequency order (`first.last` > `first` > `flast` >
`firstlast` > `f.last` > `first_last` > `last.first`), demoted by any recorded
failure for that pattern at that domain.

Reason strings are fixed identifiers (`matched_verified_domain_pattern`,
`high_recruiter_title_relevance`, `accept_all_domain_penalty`, …), not prose.

## Hunter verification

New function in `enricher.py`, next to the existing domain-search client so
they share the credit ledger (`hunter_budget.json`, `MONTHLY_CALL_CAP`):

- Endpoint: `/v2/email-verifier`. Costs a credit per verification, so it is
  gated by the same `budget_remaining()` check the domain search uses.
- Verify at most 3 guesses per contact; stop early on confidence ≥ 90.
- If the domain has a `verified` pattern, spend nothing: the guess is
  auto-promoted (see Auto-promotion above). Verification there is available
  on demand but never automatic.
- An `accept_all` response sets the domain's `accept_all` flag and does not
  promote the pattern.
- Provider failure ≠ invalid email: the former is retryable and leaves the
  guess intact, the latter records a pattern failure. These must not collapse
  into one status.

Budget note: 50 credits/month total, shared with the existing per-application
lookup. Pattern memory is what makes this affordable — a company verified once
costs nothing thereafter.

## Drafting

Reuses `ai_match.py`'s provider ladder (ordered free providers, per-provider
daily budget, falls through on cap/error). Returns `None` when every provider
is exhausted — and per the source doc's reliability requirement, a failed
draft must not erase the verified contact. The UI offers manual composition in
that case.

Prompt inputs: contact name/title, company, job title and URL, and the resume
text `ai_match.py` already extracts for the match gate. Output is the source
doc's JSON schema (`subject`, `email_body`, `anchor_topics`, `tone_label`,
`confidence_notes`), validated before storage; a malformed response is one
retry, then `None`.

Plain text only. Every draft must carry the job URL and must not claim prior
acquaintance.

## Sending

`notifier.py` already sends through Gmail SMTP. Add `send_outreach()` beside
`send_digest_alert()`.

Hard rules, enforced in code and not merely in the UI:

- Sending is off unless `OUTREACH_SEND_ENABLED=true`. Default off.
- Nothing sends without an explicit approve action. There is no code path from
  a scheduled job to an outbound outreach email — the batch workflow may
  create drafts, never send them.
- Daily send cap (`OUTREACH_DAILY_SEND_CAP`, default 10).
- Blocked when verification status is below threshold, unless the request
  carries an explicit override flag that is recorded in the log.
- Dedup keys above are re-checked at send time, not just at draft time.

## API

Follows existing naming (`/api/jobs/{job_id}/...`, token via the same header
convention as the other write endpoints):

| Route | Purpose |
|---|---|
| `POST /api/jobs/{job_id}/outreach/discover` | Resolve domain, search, extract, rank. No Hunter spend. |
| `POST /api/jobs/{job_id}/outreach/verify` | Verify top guesses for one chosen contact. Spends credits. |
| `POST /api/jobs/{job_id}/outreach/draft` | Generate a draft for a chosen contact. |
| `GET /api/jobs/{job_id}/outreach` | Cache-only read: candidates, guesses, drafts, prior sends. Free. |
| `PATCH /api/outreach/{record_id}` | Edit subject/body/recipient, approve, or reject. |
| `POST /api/outreach/{record_id}/send` | Send. Refuses per the rules above. |
| `GET /api/outreach` | The review queue. |

Discover and verify are separate calls on purpose: discovery is free, so it
can run eagerly; verification spends credits, so it needs a click.

## UI

`ContactCard.tsx` exists and is small. Extend rather than replace: contact
name/title, an origin badge (searched vs. Hunter), source link, guessed email
with its verification chip, the pattern-confidence badge ("first.last —
verified, 2 samples"), and the rank reasons.

One new `OutreachPanel.tsx`: the draft in an editable textarea, the dedup
warning when a prior record exists, the accept-all / low-confidence warnings,
and approve / reject / send. Send is disabled with a visible reason when the
rules block it, never silently inert.

Existing conventions: functional components, arrow syntax, Tailwind, default
export per file.

## Scheduling

No new cron initially. Discovery runs when asked. If a batch pass is wanted
later, it is a workflow that only *creates* drafts for recently applied jobs —
the send path stays manual by construction.

## Phases

1. **Discovery + patterns, no spend.** ✅ shipped (`backend/outreach.py`,
   `test_outreach.py`, `GET /api/jobs/{id}/outreach`,
   `POST /api/jobs/{id}/outreach/discover`, extended `ContactCard`). Domain resolution, recruiter search,
   candidate extraction, pattern generation, ranking, pattern memory file,
   `GET`/`discover` endpoints, extended `ContactCard`. Nothing calls Hunter.
   Independently useful: guessed emails plus evidence.
2. **Verification.** ✅ shipped — Hunter email-verifier (`enricher.verify_email`),
   pattern learning, accept-all handling, verified-address store,
   `POST /api/jobs/{id}/outreach/verify`, eligibility chips on `ContactCard`.
3. **Drafting + send.** ✅ shipped — outreach log with dedup and daily cap
   (`outreach.check_send_allowed`), SMTP send plus Gmail label filing and
   reply detection (`outreach_mail.py`), `POST /api/jobs/{id}/outreach/send`,
   `GET /api/outreach/log`, `POST /api/outreach/sync-replies`,
   `OutreachPanel` (compose, verify, override, gated send) and `OutreachLog`
   (audit trail, reply sync) behind a new Outreach tab.
   Drafting itself is not a backend endpoint: the `.claude/skills/outreach`
   skill writes the email in the user's voice and the panel keeps it editable,
   so no AI provider budget is spent on prose and the human gate is the same
   review either way.

## Tests

Following the existing convention — pure functions tested directly, no live
network:

- Name parsing: hyphenated, single-token, three-token, non-Latin, initials.
- Pattern generation: exact permutation set per name shape.
- Guess ranking: verified pattern wins; failed pattern demoted; reasons emitted.
- Pattern learning: 1 success → `probable`, 2 → `verified`, accept-all caps at
  `probable`, failures demote, 180-day decay.
- Auto-promotion: `verified` promotes without a credit; `probable` does not;
  accept-all does not; a post-failure pattern needs 2 clean successes first;
  an unparseable name falls back to manual verification; a stale pattern
  stops promoting; the log records `auto_promoted_pattern`.
- Dedup: same job+email blocked; same domain+email inside cooldown blocked;
  outside cooldown allowed.
- Verification mapping: provider error vs. invalid-email are distinct statuses.
- Draft schema validation rejects malformed model output.
- Send gating: refuses when disabled, over cap, under confidence, or duplicate;
  override is recorded.
- Recruiter query builder respects the 32-word cap (same check the job search
  now has).

## Environment

```
OUTREACH_SEND_ENABLED=false     # nothing sends unless this is true
OUTREACH_DAILY_SEND_CAP=10
OUTREACH_MIN_CONFIDENCE=80      # below this, send needs an explicit override
OUTREACH_DEDUP_COOLDOWN_DAYS=30
OUTREACH_MAX_VERIFY_PER_CONTACT=3
```

Reuses `HUNTER_API_KEY`, `HUNTER_MONTHLY_CALL_CAP`, the `ai_match.py` provider
keys, and the existing Gmail SMTP credentials. No new secrets.

## Open questions

- **Decided (phase 1):** discovery stays on SERP titles/snippets only — no
  LinkedIn profile fetch, so nothing touches the login wall or its terms. A
  SERP anchor gives a name, a title and a profile URL, which is all ranking
  needs; the profile page itself would only add confidence.
- **Decided (phase 1):** recruiter discovery shares the job search's
  `google-search` backoff key, per the reasoning above. The interactive path
  surfaces `blocked_until` as a 503 so a rate-limited search never reads as
  "this company has no recruiters".

Superseded, kept for context:

- Is LinkedIn profile scraping wanted at all, or should discovery stay on
  SERP titles/snippets only? The latter is slower to build confidence but
  stays clear of a login wall and its terms.
- Recruiter search shares the Google CAPTCHA backoff with the job search. If
  discovery gets blocked at the same time as the 3-hourly job search, both go
  quiet together — acceptable, or should outreach discovery get its own
  backoff key?
