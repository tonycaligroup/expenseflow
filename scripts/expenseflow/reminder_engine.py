from datetime import datetime, timedelta, timezone

from .errors import ExpenseFlowError


def normalize_reminder_settings(settings):
    raw = settings.get("approval_reminders") or {}
    if not isinstance(raw, dict):
        raise ExpenseFlowError("invalid_reminder_settings", "approval_reminders must be an object.")

    enabled = raw.get("enabled", False)
    if not isinstance(enabled, bool):
        raise ExpenseFlowError("invalid_reminder_settings", "approval_reminders.enabled must be true or false.")
    initial_delay_hours = _positive_int(raw.get("initial_delay_hours", 24), "initial_delay_hours")
    interval_hours = _positive_int(raw.get("interval_hours", 24), "interval_hours")
    max_attempts = _positive_int(raw.get("max_attempts", 3), "max_attempts")
    escalation_user_ids = raw.get("escalation_user_ids") or []
    if not isinstance(escalation_user_ids, list):
        escalation_user_ids = [escalation_user_ids]
    if any(not isinstance(user_id, (int, str)) or str(user_id).strip() == "" for user_id in escalation_user_ids):
        raise ExpenseFlowError(
            "invalid_reminder_settings",
            "approval_reminders.escalation_user_ids must contain only user IDs.",
        )
    return {
        "enabled": enabled,
        "initial_delay_hours": initial_delay_hours,
        "interval_hours": interval_hours,
        "max_attempts": max_attempts,
        "escalation_user_ids": list(dict.fromkeys(escalation_user_ids)),
    }


def initialize_reminder_schedule(approval_request, settings):
    config = normalize_reminder_settings(settings)
    updated = dict(approval_request)
    updated["reminder_count"] = 0
    updated["last_reminder_at"] = None
    updated["reminder_status"] = "scheduled" if config["enabled"] else "disabled"
    updated["next_reminder_at"] = None
    if config["enabled"]:
        created_at = parse_utc(updated.get("created_at"), "created_at")
        updated["next_reminder_at"] = format_utc(created_at + timedelta(hours=config["initial_delay_hours"]))
    return updated


def is_reminder_due(approval_request, as_of):
    if approval_request.get("status") != "pending":
        return False
    if approval_request.get("reminder_status") != "scheduled":
        return False
    next_reminder_at = approval_request.get("next_reminder_at")
    if not next_reminder_at:
        return False
    return parse_utc(next_reminder_at, "next_reminder_at") <= parse_utc(as_of, "as_of")


def advance_reminder_schedule(approval_request, settings, sent_at):
    config = normalize_reminder_settings(settings)
    updated = dict(approval_request)
    count = int(updated.get("reminder_count") or 0) + 1
    updated["reminder_count"] = count
    updated["last_reminder_at"] = format_utc(parse_utc(sent_at, "sent_at"))
    if count >= config["max_attempts"]:
        updated["reminder_status"] = "exhausted"
        updated["next_reminder_at"] = None
    else:
        updated["reminder_status"] = "scheduled"
        updated["next_reminder_at"] = format_utc(
            parse_utc(sent_at, "sent_at") + timedelta(hours=config["interval_hours"])
        )
    return updated


def parse_utc(value, field):
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value or "").strip()
        if text.endswith("Z"):
            text = f"{text[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            raise ExpenseFlowError("invalid_timestamp", f"{field} must be an ISO-8601 timestamp.")
    if parsed.tzinfo is None:
        raise ExpenseFlowError("invalid_timestamp", f"{field} must include a timezone.")
    return parsed.astimezone(timezone.utc)


def format_utc(value):
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _positive_int(value, field):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        raise ExpenseFlowError("invalid_reminder_settings", f"{field} must be a positive integer.")
    if parsed <= 0:
        raise ExpenseFlowError("invalid_reminder_settings", f"{field} must be a positive integer.")
    return parsed
