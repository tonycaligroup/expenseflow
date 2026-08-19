from datetime import datetime, timezone


DEFAULT_CATEGORIES = [
    "Travel",
    "Lodging",
    "Meals & Entertainment",
    "Ground Transport",
    "Software & Subscriptions",
    "Office Supplies",
    "Hardware/Equipment",
    "Professional Services",
    "Other",
]

EXPENSE_STATUSES = {
    "draft",
    "held_pending_onboarding",
    "held_pending_manager",
    "submitted",
    "approved",
    "rejected",
    "exported",
    "synced",
}

REPORT_STATUSES = {
    "draft",
    "held_pending_manager",
    "pending_approval",
    "partially_approved",
    "approved",
    "rejected",
    "exported",
    "synced",
}

EXPENSE_TRANSITIONS = {
    "draft": {"held_pending_onboarding", "held_pending_manager", "submitted", "rejected"},
    "held_pending_onboarding": {"draft", "submitted", "rejected"},
    "held_pending_manager": {"submitted", "rejected"},
    "submitted": {"approved", "rejected"},
    "approved": {"exported", "synced"},
    "rejected": set(),
    "exported": {"synced"},
    "synced": set(),
}

REPORT_TRANSITIONS = {
    "draft": {"held_pending_manager", "pending_approval", "rejected"},
    "held_pending_manager": {"pending_approval", "rejected"},
    "pending_approval": {"partially_approved", "approved", "rejected"},
    "partially_approved": {"approved", "rejected", "exported"},
    "approved": {"exported", "synced"},
    "rejected": set(),
    "exported": {"synced"},
    "synced": set(),
}


def utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
