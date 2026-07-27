import pytest

from pipeline_state import (
    APPLIED,
    HIRED,
    INTERVIEWING,
    OFFER_PENDING,
    REJECTED,
    SCREENING,
    WITHDRAWN,
    GateNotSatisfiedError,
    InvalidTransitionError,
    TerminalStateError,
    validate_transition,
)


def test_forward_transitions_allowed():
    validate_transition(APPLIED, SCREENING, [])
    validate_transition(APPLIED, INTERVIEWING, [])
    validate_transition(SCREENING, INTERVIEWING, [])


def test_skip_ahead_rejected():
    with pytest.raises(InvalidTransitionError):
        validate_transition(APPLIED, OFFER_PENDING, [])
    with pytest.raises(InvalidTransitionError):
        validate_transition(SCREENING, HIRED, [])


def test_backward_transition_rejected():
    with pytest.raises(InvalidTransitionError):
        validate_transition(INTERVIEWING, APPLIED, [])


def test_terminal_state_locked():
    for terminal in (HIRED, REJECTED, WITHDRAWN):
        with pytest.raises(TerminalStateError):
            validate_transition(terminal, SCREENING, [])


def test_any_non_terminal_can_reject_or_withdraw():
    for state in (APPLIED, SCREENING, INTERVIEWING, OFFER_PENDING):
        validate_transition(state, REJECTED, [])
        validate_transition(state, WITHDRAWN, [])


def test_self_loop_annotate_allowed_for_every_non_terminal_state():
    for state in (APPLIED, SCREENING, INTERVIEWING, OFFER_PENDING):
        validate_transition(state, state, [])


def test_offer_pending_gated_without_scorecard():
    with pytest.raises(GateNotSatisfiedError):
        validate_transition(INTERVIEWING, OFFER_PENDING, [])


def test_offer_pending_gated_with_note_only_no_scorecard():
    events = [{"to_state": INTERVIEWING, "scorecard": None, "note": "went fine"}]
    with pytest.raises(GateNotSatisfiedError):
        validate_transition(INTERVIEWING, OFFER_PENDING, events)


def test_offer_pending_allowed_with_prior_scorecard():
    events = [{"to_state": INTERVIEWING, "scorecard": {"score": 4}}]
    validate_transition(INTERVIEWING, OFFER_PENDING, events)


def test_offer_pending_scorecard_can_come_from_self_loop_event():
    # e.g. the self-loop annotate that attaches the scorecard before the real transition
    events = [
        {"to_state": INTERVIEWING, "scorecard": None},
        {"to_state": INTERVIEWING, "scorecard": {"score": 5}},
    ]
    validate_transition(INTERVIEWING, OFFER_PENDING, events)


def test_hired_reachable_from_offer_pending():
    validate_transition(OFFER_PENDING, HIRED, [])
