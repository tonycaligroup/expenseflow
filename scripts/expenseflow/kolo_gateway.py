from copy import deepcopy
from uuid import uuid4

from .errors import ExpenseFlowError


class FakeKoloGateway:
    """Governed-record and messaging test double for Kolo workflows."""

    def __init__(self, peers=None):
        self.records = {}
        self.messages = []
        self.tasks = []
        self.audit_events = {}
        self.peers = deepcopy(peers or [])

    def upsert_record(self, record_type, external_id, payload, status="active", schema_version=1):
        key = (record_type, str(external_id))
        created = key not in self.records
        record = {
            "record_type": record_type,
            "external_id": str(external_id),
            "payload": deepcopy(payload),
            "status": status,
            "schema_version": schema_version,
        }
        self.records[key] = record
        return {"created": created, "record": deepcopy(record)}

    def get_record(self, record_type, external_id):
        key = (record_type, str(external_id))
        if key not in self.records:
            raise ExpenseFlowError(
                "record_not_found",
                f"No {record_type} record found for {external_id}.",
                details={"record_type": record_type, "external_id": str(external_id)},
            )
        return deepcopy(self.records[key])

    def list_records(self, record_type, status=None):
        rows = [
            deepcopy(record)
            for (rtype, _), record in self.records.items()
            if rtype == record_type and (status is None or record.get("status") == status)
        ]
        rows.sort(key=lambda record: record["external_id"])
        return rows

    def set_record_status(self, record_type, external_id, status):
        record = self.get_record(record_type, external_id)
        record["status"] = status
        record["payload"]["status"] = status
        self.records[(record_type, str(external_id))] = deepcopy(record)
        return deepcopy(record)

    def list_peers(self):
        return deepcopy(self.peers)

    def contact_agent(self, target_user_id, message):
        queue_id = f"queue_{uuid4().hex[:12]}"
        delivery = {
            "status": "ok",
            "queueId": queue_id,
            "deliveryStatus": "queued",
            "target_user_id": target_user_id,
            "message": message,
        }
        self.messages.append(deepcopy(delivery))
        return deepcopy(delivery)

    def create_task(self, title, user_id, metadata=None):
        task = {
            "task_id": f"task_{uuid4().hex[:12]}",
            "title": title,
            "user_id": user_id,
            "metadata": deepcopy(metadata or {}),
            "status": "open",
        }
        self.tasks.append(deepcopy(task))
        return deepcopy(task)

    def log_action(self, category, title, idempotency_key, metadata=None):
        if idempotency_key in self.audit_events:
            return deepcopy(self.audit_events[idempotency_key])
        event = {
            "auditEventId": f"audit_{uuid4().hex[:12]}",
            "category": category,
            "title": title,
            "idempotency_key": idempotency_key,
            "metadata": deepcopy(metadata or {}),
        }
        self.audit_events[idempotency_key] = event
        return deepcopy(event)
