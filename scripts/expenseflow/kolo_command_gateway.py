import json
import subprocess

from .errors import ExpenseFlowError
from .maton_sheets_gateway import MatonSheetsGateway


class KoloCommandGateway:
    """Shell-backed gateway for Kolo platform commands."""

    def __init__(self, runner=None, sheets_gateway=None):
        self.runner = runner or _run_command
        self.sheets_gateway = sheets_gateway or MatonSheetsGateway()

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

    def list_peers(self):
        result = self.runner(["kolo", "list-peers"])
        rows = result if isinstance(result, list) else result.get("peers", result.get("users", []))
        if not isinstance(rows, list):
            raise ExpenseFlowError(
                "invalid_kolo_response",
                "Kolo list-peers returned an unexpected response shape.",
            )
        peers = []
        for row in rows:
            user_id = row.get("user_id", row.get("userId"))
            if user_id is None:
                raise ExpenseFlowError("invalid_kolo_response", "Kolo peer is missing a user ID.")
            peers.append(
                {
                    "user_id": _normalize_user_id(user_id),
                    "display_name": row.get("display_name", row.get("displayName")),
                    "org_id": row.get("org_id", row.get("orgId")),
                }
            )
        return peers

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

    def complete_task(self, task_id):
        result = self.runner(["kolo", "task-complete", "--task-id", str(task_id)])
        task = result.get("task", {}) if isinstance(result.get("task"), dict) else {}
        return {
            "task_id": task.get("task_id") or task.get("taskId") or task.get("id") or task_id,
            "status": task.get("status", result.get("status", "completed")),
            "raw": result,
        }

    def upload_file(self, file_path):
        result = self.runner(["kolo", "file-upload", str(file_path)])
        object_id = result.get("objectStoreObjectId") or result.get("object_store_object_id")
        reference = result.get("reference") or result.get("url")
        if not object_id:
            raise ExpenseFlowError(
                "missing_receipt_object_id",
                "Kolo file-upload did not return an object-store ID.",
            )
        return {
            "object_store_object_id": object_id,
            "reference": reference or f"kolo://obj/{object_id}",
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

    def sheets_get_metadata(self, spreadsheet_id):
        return self.sheets_gateway.get_metadata(spreadsheet_id)

    def sheets_read_values(self, spreadsheet_id, a1_range):
        return self.sheets_gateway.read_values(spreadsheet_id, a1_range)

    def sheets_update_values(self, spreadsheet_id, a1_range, values):
        return self.sheets_gateway.update_values(spreadsheet_id, a1_range, values)

    def sheets_append_values(self, spreadsheet_id, a1_range, values):
        return self.sheets_gateway.append_values(spreadsheet_id, a1_range, values)

    def quickbooks_status(self):
        result = self.runner(["kolo", "quickbooks", "status"])
        if not isinstance(result, dict) or not isinstance(result.get("realms", []), list):
            raise ExpenseFlowError(
                "invalid_qbo_status_response",
                "Kolo returned an invalid QuickBooks connection status.",
            )
        return result

    def quickbooks_call(self, path, realm_id=None, query=None, api="accounting"):
        command = ["kolo", "quickbooks", "call", str(path), "--api", api, "-q"]
        if realm_id is not None:
            command.extend(["--realm", str(realm_id)])
        for key, value in sorted((query or {}).items()):
            command.extend(["--query", f"{key}={value}"])
        result = self.runner(command)
        if not isinstance(result, dict):
            raise ExpenseFlowError(
                "invalid_qbo_response",
                "Kolo returned an invalid QuickBooks read response.",
                details={"path": str(path)},
            )
        return result

    def quickbooks_write(
        self,
        path,
        body,
        realm_id=None,
        request_id=None,
        reason=None,
        session_key=None,
        chat_id=None,
        api="accounting",
        http_method="post",
        query=None,
    ):
        encoded_body = json.dumps(body, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        if len(encoded_body.encode("utf-8")) > 100_000:
            raise ExpenseFlowError(
                "qbo_payload_too_large",
                "QuickBooks payload exceeds the 100 KB ExpenseFlow safety limit.",
                details={"path": str(path)},
            )
        command = [
            "kolo",
            "quickbooks",
            "write",
            str(path),
            "--api",
            api,
            "--http-method",
            http_method,
            "--body",
            encoded_body,
        ]
        if realm_id is not None:
            command.extend(["--realm", str(realm_id)])
        if request_id:
            command.extend(["--request-id", str(request_id)])
        if reason:
            command.extend(["--reason", str(reason)])
        if session_key:
            command.extend(["--session-key", str(session_key)])
        if chat_id:
            command.extend(["--chat-id", str(chat_id)])
        for key, value in sorted((query or {}).items()):
            command.extend(["--query", f"{key}={value}"])
        result = self.runner(command)
        brief_number = (
            result.get("brief_number")
            or result.get("briefNumber")
            or result.get("brief_id")
            or result.get("briefId")
        )
        if not brief_number:
            raise ExpenseFlowError(
                "missing_qbo_brief_number",
                "Kolo did not return a QuickBooks approval brief number.",
                details={"path": str(path)},
            )
        return {"brief_number": str(brief_number), "raw": result}

    def quickbooks_write_status(self, brief_number):
        result = self.runner(
            ["kolo", "quickbooks", "write-status", "--brief-id", str(brief_number)]
        )
        if not isinstance(result, dict) or not result.get("status"):
            raise ExpenseFlowError(
                "invalid_qbo_write_status",
                "Kolo returned an invalid QuickBooks approval status.",
                details={"brief_number": str(brief_number)},
            )
        normalized = dict(result)
        normalized["status"] = str(result["status"]).lower()
        if "execution_result" not in normalized and "executionResult" in normalized:
            normalized["execution_result"] = normalized["executionResult"]
        return normalized


def _run_command(command):
    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        if _is_record_not_found(command, completed.stdout, completed.stderr):
            raise ExpenseFlowError(
                "record_not_found",
                "Kolo governed record was not found.",
                details={
                    "record_type": _command_option(command, "--record-type"),
                    "external_id": _command_option(command, "--external-id"),
                },
            )
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


def _normalize_user_id(user_id):
    try:
        return int(user_id)
    except (TypeError, ValueError):
        return user_id


def _is_record_not_found(command, stdout, stderr):
    if command[:2] != ["kolo", "record-get"]:
        return False
    output = str(stdout or "").strip()
    if output:
        try:
            if _contains_not_found(json.loads(output)):
                return True
        except json.JSONDecodeError:
            pass
    combined = f"{output}\n{stderr or ''}".lower()
    return "not_found" in combined or "not found" in combined or "404" in combined


def _contains_not_found(value):
    if isinstance(value, dict):
        if value.get("status") == 404 or value.get("statusCode") == 404:
            return True
        return any(_contains_not_found(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_not_found(item) for item in value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"not_found", "not found"} or "record not found" in normalized
    return False


def _command_option(command, option):
    try:
        return command[command.index(option) + 1]
    except (ValueError, IndexError):
        return None
