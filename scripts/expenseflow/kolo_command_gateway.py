import json
import subprocess

from .errors import ExpenseFlowError


class KoloCommandGateway:
    """Shell-backed gateway for Kolo platform commands."""

    def __init__(self, runner=None):
        self.runner = runner or _run_command

    def upsert_record(self, record_type, external_id, payload, status="active", schema_version=1):
        result = self.runner(
            [
                "kolo",
                "record-upsert",
                "--record-type",
                record_type,
                "--external-id",
                str(external_id),
                "--payload",
                json.dumps(payload, sort_keys=True),
                "--schema-version",
                str(schema_version),
            ]
        )
        record = _normalize_record(result, record_type, external_id, payload, status, schema_version)
        if record.get("status") != status:
            self.set_record_status(record_type, external_id, status)
            record["status"] = status
            record["payload"]["status"] = status
        return {"created": bool(result.get("created", False)), "record": record, "raw": result}

    def get_record(self, record_type, external_id):
        result = self.runner(
            [
                "kolo",
                "record-get",
                "--record-type",
                record_type,
                "--external-id",
                str(external_id),
            ]
        )
        return _normalize_record(result, record_type, external_id)

    def list_records(self, record_type, status=None):
        command = ["kolo", "record-list", "--record-type", record_type]
        if status is not None:
            command.extend(["--status", status])
        result = self.runner(command)
        rows = result if isinstance(result, list) else result.get("records", [])
        if not isinstance(rows, list):
            raise ExpenseFlowError(
                "invalid_kolo_response",
                "Kolo record-list returned an unexpected response shape.",
                details={"record_type": record_type},
            )
        records = []
        for row in rows:
            external_id = _record_external_id(row)
            if external_id is None:
                raise ExpenseFlowError(
                    "invalid_kolo_response",
                    "Kolo record-list returned a record without an external ID.",
                    details={"record_type": record_type},
                )
            if "payload" not in row:
                records.append(self.get_record(record_type, external_id))
            else:
                records.append(_normalize_record(row, record_type, external_id))
        return records

    def set_record_status(self, record_type, external_id, status):
        result = self.runner(
            [
                "kolo",
                "record-status",
                "--record-type",
                record_type,
                "--external-id",
                str(external_id),
                "--status",
                status,
            ]
        )
        return _normalize_record(result, record_type, external_id, status=status)

    def contact_agent(self, target_user_id, message):
        result = self.runner(["kolo", "contact-agent", "-t", str(target_user_id), "-m", message])
        queue_id = result.get("queueId") or result.get("queue_id")
        if not queue_id:
            raise ExpenseFlowError(
                "missing_queue_id",
                "Kolo contact-agent did not return a queueId.",
                details={"target_user_id": target_user_id},
            )
        return {
            "status": result.get("status", "ok"),
            "queueId": queue_id,
            "deliveryStatus": result.get("deliveryStatus", result.get("delivery_status")),
            "raw": result,
        }

    def create_task(self, title, user_id, metadata=None):
        command = ["kolo", "task-create", "--title", title, "--user", str(user_id)]
        result = self.runner(command)
        task = result.get("task", {}) if isinstance(result.get("task"), dict) else {}
        task_id = task.get("task_id") or task.get("taskId") or task.get("id")
        task_id = task_id or result.get("task_id") or result.get("taskId") or result.get("id")
        if not task_id:
            raise ExpenseFlowError(
                "missing_task_id",
                "Kolo task-create did not return a task ID.",
                details={"title": title, "user_id": user_id},
            )
        return {
            "task_id": task_id,
            "status": task.get("status", result.get("status", "open")),
            "metadata": metadata or {},
            "raw": result,
        }

    def log_action(self, category, title, idempotency_key, metadata=None):
        command = [
            "kolo",
            "log-action",
            "--category",
            category,
            "--title",
            title,
            "--idempotency-key",
            idempotency_key,
        ]
        if metadata:
            command.extend(["--details", json.dumps(metadata, sort_keys=True)])
        result = self.runner(command)
        audit_event_id = result.get("auditEventId") or result.get("audit_event_id") or result.get("id")
        if not audit_event_id:
            raise ExpenseFlowError(
                "missing_audit_event_id",
                "Kolo log-action did not return an audit event ID.",
                details={"category": category, "idempotency_key": idempotency_key},
            )
        return {"auditEventId": audit_event_id, "raw": result}


def _run_command(command):
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        raise ExpenseFlowError(
            "kolo_command_failed",
            f"Kolo command failed: {' '.join(command[:2])}",
            retryable=True,
            details={
                "returncode": completed.returncode,
                "stderr": completed.stderr.strip(),
                "stdout": completed.stdout.strip(),
            },
        )
    output = completed.stdout.strip()
    if not output:
        return {}
    try:
        return json.loads(output)
    except json.JSONDecodeError as exc:
        raise ExpenseFlowError(
            "invalid_kolo_json",
            "Kolo command did not return valid JSON.",
            details={"command": command[:2], "error": exc.msg, "output": output[:500]},
        )


def _normalize_record(result, record_type, external_id, payload=None, status=None, schema_version=1):
    record = result.get("record", result)
    normalized_payload = record.get("payload", payload or {})
    normalized = {
        "record_type": record.get("record_type", record.get("recordType", record_type)),
        "external_id": str(record.get("external_id", record.get("externalId", external_id))),
        "payload": normalized_payload,
        "status": record.get("status", status or normalized_payload.get("status", "active")),
        "schema_version": record.get("schema_version", record.get("schemaVersion", schema_version)),
    }
    return normalized


def _record_external_id(record):
    return record.get("external_id", record.get("externalId"))
