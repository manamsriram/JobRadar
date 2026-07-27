"""Pure FSM/gate logic for the application pipeline. No I/O — see pipeline_db.py
for the DB-backed transaction that wraps validate_transition()."""

APPLIED = "APPLIED"
SCREENING = "SCREENING"
INTERVIEWING = "INTERVIEWING"
OFFER_PENDING = "OFFER_PENDING"
HIRED = "HIRED"
REJECTED = "REJECTED"
WITHDRAWN = "WITHDRAWN"

ALL_STATES = {APPLIED, SCREENING, INTERVIEWING, OFFER_PENDING, HIRED, REJECTED, WITHDRAWN}
TERMINAL_STATES = {HIRED, REJECTED, WITHDRAWN}

# Forward progression only — self-loops and terminal exits are handled
# separately below rather than duplicated into every state's entry.
_FORWARD_TRANSITIONS = {
    APPLIED: {SCREENING, INTERVIEWING},
    SCREENING: {INTERVIEWING},
    INTERVIEWING: {OFFER_PENDING},
    OFFER_PENDING: {HIRED},
}


class PipelineError(Exception):
    """Base class; callers should catch the specific subclass to pick a status code."""


class TerminalStateError(PipelineError):
    """from_state is terminal — maps to HTTP 409, not 422."""


class InvalidTransitionError(PipelineError):
    """to_state isn't reachable from from_state per the transition matrix — HTTP 422."""


class GateNotSatisfiedError(PipelineError):
    """Transition is structurally valid but a gate rule blocks it — HTTP 422."""


def validate_transition(from_state: str, to_state: str, prior_events: list[dict]) -> None:
    """Raise if the from_state -> to_state move isn't permitted. Returns None if OK.

    prior_events: this application's ledger rows so far, each a dict with at
    least `to_state` and `scorecard` keys, ordered oldest-first (order doesn't
    matter for the gate check below, but callers already have them ordered).
    """
    if from_state in TERMINAL_STATES:
        raise TerminalStateError(f"{from_state} is terminal, no further transitions allowed")

    if to_state == from_state:
        return  # annotate-only self-loop, permitted for every non-terminal state

    if to_state in (REJECTED, WITHDRAWN):
        return  # any non-terminal state can be abandoned

    if to_state not in _FORWARD_TRANSITIONS.get(from_state, set()):
        raise InvalidTransitionError(f"cannot transition {from_state} -> {to_state}")

    if from_state == INTERVIEWING and to_state == OFFER_PENDING:
        has_scorecard = any(
            e.get("to_state") == INTERVIEWING and e.get("scorecard") is not None
            for e in prior_events
        )
        if not has_scorecard:
            raise GateNotSatisfiedError(
                "INTERVIEWING -> OFFER_PENDING requires a prior INTERVIEWING event with a scorecard"
            )
