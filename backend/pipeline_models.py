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


class SendOutreachIn(BaseModel):
    """One outreach email, composed elsewhere (the drafting skill) and sent
    through the gates in main.py. `override` waives only the confidence gate —
    the kill switch, the daily cap and dedup are not overridable."""
    email: str
    subject: str
    body: str
    source_url: str | None = None
    override: bool = False

    @field_validator("email")
    @classmethod
    def _looks_like_an_address(cls, v: str) -> str:
        local, sep, domain = v.partition("@")
        if not (local and sep and "." in domain) or any(c.isspace() for c in v):
            raise ValueError("email must be a single well-formed address")
        return v

    @field_validator("subject", "body")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("subject and body must not be blank")
        return v
