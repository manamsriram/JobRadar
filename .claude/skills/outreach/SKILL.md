---
name: outreach
description: Draft and send a recruiter outreach email for a job you applied to in JobRadar. Use when the user says "reach out about <job/company>", "draft an outreach email", "email the recruiter at X", or asks to contact someone about an application. Handles contact discovery, address verification, drafting in the user's voice, and sending through the backend's send gates.
---

# Outreach

Draft and send one recruiter email per job. The backend owns every safety gate
(kill switch, daily cap, dedup, verification); this skill owns the writing and
the human check before anything goes out.

`BASE` below is the JobRadar API root — `http://localhost:8000` in dev, or
whatever `INGEST_URL` points at.

## Steps

### 1. Find the job

```bash
curl -s "$BASE/api/jobs/applied" | jq '.[] | {id, title, company}'
```

Match on what the user said. Ambiguous between two jobs — ask, don't guess.

### 2. Get contacts and their addresses

```bash
curl -s "$BASE/api/jobs/<job_id>/outreach"
```

Each candidate carries `guesses` (ranked addresses) and `eligibility`:

- `eligibility.eligible: true`, basis `verified_address` or
  `stored_verified_address` — a confirmed address. Send freely.
- basis `auto_promoted_pattern` — the company's email pattern is verified from
  two prior checks, so this address is a high-confidence construction. Say so
  to the user; never present it as confirmed.
- `eligible: false` — needs verification. Go to step 3.

Empty candidate list: run discovery first.

```bash
curl -s -X POST "$BASE/api/jobs/<job_id>/outreach/discover"
```

A `503` here means the search backend is rate-limited until the time in the
message — report that, don't retry in a loop.

### 3. Verify, only when needed

```bash
curl -s -X POST "$BASE/api/jobs/<job_id>/outreach/verify?source_url=<contact source_url>"
```

**This can spend a Hunter credit (50/month).** Only call it when eligibility is
false and the user wants to email that specific person. The response says
`spent_credit`. `503` means the monthly quota is gone — tell the user and stop;
do not fall back to sending an unverified address on your own initiative.

### 4. Draft

Read the job description and the contact's title first. Then write a plain-text
email, no HTML, no attachments:

- Subject: specific and short. `<Role> application — <their name>` beats
  anything clever.
- 120 words or fewer. Three short paragraphs at most.
- Open with the concrete thing: which role, applied when, from where.
- One sentence of genuine relevance — a project, a shipped thing, a stack
  overlap with the posting. Pull from the user's resume, never invent one.
- Close with a specific small ask ("worth a short chat?"), not "let me know if
  you have any questions".
- No "I hope this finds you well", no "I'm reaching out because", no flattery
  about the company's mission, no em-dash-heavy AI cadence.
- Sign with the user's real name.

Show the draft to the user in full and wait for approval. Never send an
unreviewed draft, even when they said "just send it" earlier in the session —
show it, then send.

### 5. Send

```bash
curl -s -X POST "$BASE/api/jobs/<job_id>/outreach/send" \
  -H "Content-Type: application/json" \
  -d '{"email": "...", "subject": "...", "body": "...", "source_url": "..."}'
```

The message gets the `reach out` Gmail label; a detected reply moves it to
`hiring` (`POST /api/outreach/sync-replies`).

`409` means a gate stopped it — read `detail.reason`:

| reason | what it means | what to do |
|---|---|---|
| `sending_disabled` | `OUTREACH_SEND_ENABLED` is not `true` | Tell the user to set it. Do not work around it. |
| `daily_cap_reached` | Hit `OUTREACH_DAILY_SEND_CAP` | Stop for today. |
| `duplicate` | Already mailed this person (`detail.prior`) | Show the prior record and its date. |
| `unverified_address` | Confidence gate | Offer step 3, or `"override": true` **only** if the user explicitly accepts a possible bounce. |

## Rules

- One email per person per job. The backend enforces it; don't try to route around it.
- Never send to an address the user hasn't seen in the draft you showed them.
- Never invent an employment history, a mutual connection, or a referral.
- `override` is the user's decision to make, never yours.
