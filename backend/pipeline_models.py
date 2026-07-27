"""Pydantic schemas for the pipeline-tracking routes only. The rest of main.py
validates job dicts with isinstance (flat 3-field dicts) — these payloads have
real structural shape (enum-constrained to_state, nested scorecard), so
pydantic is justified here without touching that convention elsewhere.
"""
from typing import Any

from pydantic import BaseModel, field_validator

from pipeline_state import ALL_STATES


class CreateApplicationIn(BaseModel):
    job_id: str
    job_title: str
    company: str
    job_url: str | None = None


class TransitionIn(BaseModel):
    to_state: str
    note: str | None = None
    scorecard: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    @field_validator("to_state")
    @classmethod
    def _to_state_is_known(cls, v: str) -> str:
        if v not in ALL_STATES:
            raise ValueError(f"to_state must be one of {sorted(ALL_STATES)}")
        return v


class ApplicationOut(BaseModel):
    id: str
    job_id: str
    job_title: str
    company: str
    job_url: str | None
    current_state: str
    applied_at: str
    updated_at: str
    next_action_due: str | None


class PipelineEventOut(BaseModel):
    id: int
    application_id: str
    from_state: str | None
    to_state: str
    occurred_at: str
    note: str | None
    scorecard: dict[str, Any] | None
    metadata: dict[str, Any]
