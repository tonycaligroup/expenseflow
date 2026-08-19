from .errors import ExpenseFlowError
from .models import EXPENSE_TRANSITIONS, REPORT_TRANSITIONS


def validate_transition(kind, current, new):
    transitions = {
        "expense": EXPENSE_TRANSITIONS,
        "report": REPORT_TRANSITIONS,
    }.get(kind)
    if transitions is None:
        raise ExpenseFlowError("invalid_transition_kind", f"Unknown transition kind '{kind}'.")
    if current not in transitions:
        raise ExpenseFlowError("invalid_status", f"Unknown {kind} status '{current}'.")
    if new not in transitions:
        raise ExpenseFlowError("invalid_status", f"Unknown {kind} status '{new}'.")
    if new not in transitions[current]:
        raise ExpenseFlowError(
            "invalid_transition",
            f"Cannot move {kind} from {current} to {new}.",
            details={"kind": kind, "from": current, "to": new},
        )
    return True
