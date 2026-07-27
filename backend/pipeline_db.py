"""asyncpg pool + query functions for the pipeline-tracking feature (applications
+ event ledger, Postgres via Supabase — scoped to this feature only, see
i-am-building-a-reactive-pearl.md). Connects as the least-privilege jobradar_app
role so the ledger's append-only RLS grants are actually enforced.
"""
import json
import os

import asyncpg

from pipeline_state import validate_transition

_pool: asyncpg.Pool | None = None


async def _init_connection(conn: asyncpg.Connection) -> None:
    # Auto (de)serialize jsonb <-> dict so callers never touch json.dumps/loads.
    await conn.set_type_codec(
        "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
    )


async def init_pool() -> None:
    global _pool
    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        return  # pipeline feature is optional — absence disables its routes, not the app
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5, init=_init_connection)


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("pipeline_db pool not initialized — DATABASE_URL not set?")
    return _pool


class ApplicationNotFoundError(Exception):
    pass


def _row_to_application(row: asyncpg.Record) -> dict:
    return {
        "id": str(row["id"]),
        "job_id": row["job_id"],
        "job_title": row["job_title"],
        "company": row["company"],
        "job_url": row["job_url"],
        "current_state": row["current_state"],
        "applied_at": row["applied_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "next_action_due": row["next_action_due"].isoformat() if row["next_action_due"] else None,
    }


def _row_to_event(row: asyncpg.Record) -> dict:
    return {
        "id": row["id"],
        "application_id": str(row["application_id"]),
        "from_state": row["from_state"],
        "to_state": row["to_state"],
        "occurred_at": row["occurred_at"].isoformat(),
        "note": row["note"],
        "scorecard": row["scorecard"],
        "metadata": row["metadata"],
    }


async def create_or_get_application(job_id: str, job_title: str, company: str, job_url: str | None) -> tuple[dict, bool]:
    """Returns (application, created). Idempotent: an existing non-terminal
    application for job_id is returned instead of creating a duplicate."""
    pool = get_pool()
    async with pool.acquire() as conn:
        existing = await conn.fetchrow(
            """
            select * from public.applications
            where job_id = $1 and current_state not in ('REJECTED', 'WITHDRAWN')
            """,
            job_id,
        )
        if existing:
            return _row_to_application(existing), False

        async with conn.transaction():
            row = await conn.fetchrow(
                """
                insert into public.applications (job_id, job_title, company, job_url, current_state)
                values ($1, $2, $3, $4, 'APPLIED')
                returning *
                """,
                job_id, job_title, company, job_url,
            )
            await conn.execute(
                """
                insert into public.pipeline_events (application_id, from_state, to_state)
                values ($1, null, 'APPLIED')
                """,
                row["id"],
            )
        return _row_to_application(row), True


async def get_pipeline() -> dict[str, list[dict]]:
    """Kanban view: applications grouped by current_state."""
    pool = get_pool()
    rows = await pool.fetch(
        "select * from public.applications order by current_state, updated_at desc"
    )
    grouped: dict[str, list[dict]] = {}
    for row in rows:
        grouped.setdefault(row["current_state"], []).append(_row_to_application(row))
    return grouped


async def get_events(application_id: str) -> list[dict]:
    pool = get_pool()
    rows = await pool.fetch(
        "select * from public.pipeline_events where application_id = $1 order by occurred_at",
        application_id,
    )
    return [_row_to_event(r) for r in rows]


async def transition(
    application_id: str,
    to_state: str,
    note: str | None = None,
    scorecard: dict | None = None,
    metadata: dict | None = None,
) -> tuple[dict, dict]:
    """Validates then atomically applies a state transition + ledger insert.
    Returns (application, event). Raises ApplicationNotFoundError, or
    pipeline_state's TerminalStateError / InvalidTransitionError /
    GateNotSatisfiedError — callers map these to 404 / 409 / 422 respectively."""
    pool = get_pool()
    async with pool.acquire() as conn:
        async with conn.transaction():
            app_row = await conn.fetchrow(
                "select * from public.applications where id = $1 for update", application_id
            )
            if app_row is None:
                raise ApplicationNotFoundError(f"no application with id {application_id}")

            event_rows = await conn.fetch(
                "select to_state, scorecard from public.pipeline_events where application_id = $1",
                application_id,
            )
            prior_events = [{"to_state": r["to_state"], "scorecard": r["scorecard"]} for r in event_rows]

            validate_transition(app_row["current_state"], to_state, prior_events)

            updated = await conn.fetchrow(
                """
                update public.applications
                set current_state = $1, updated_at = now()
                where id = $2
                returning *
                """,
                to_state, application_id,
            )
            event_row = await conn.fetchrow(
                """
                insert into public.pipeline_events (application_id, from_state, to_state, note, scorecard, metadata)
                values ($1, $2, $3, $4, $5, $6)
                returning *
                """,
                application_id, app_row["current_state"], to_state, note,
                scorecard, metadata or {},
            )
        return _row_to_application(updated), _row_to_event(event_row)
