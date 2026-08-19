from copy import deepcopy

from .errors import ExpenseFlowError


class FakeStore:
    """In-memory governed-record style store for unit tests and dry runs."""

    def __init__(self):
        self.records = {}

    def upsert(self, record_type, external_id, payload, status="active", schema_version=1):
        key = (record_type, external_id)
        created = key not in self.records
        record = {
            "record_type": record_type,
            "external_id": external_id,
            "payload": deepcopy(payload),
            "status": status,
            "schema_version": schema_version,
        }
        self.records[key] = record
        return {"created": created, "record": deepcopy(record)}

    def get(self, record_type, external_id):
        key = (record_type, external_id)
        if key not in self.records:
            raise ExpenseFlowError(
                "record_not_found",
                f"No {record_type} record found for {external_id}.",
                details={"record_type": record_type, "external_id": external_id},
            )
        return deepcopy(self.records[key])

    def list(self, record_type, status=None):
        rows = [
            deepcopy(record)
            for (rtype, _), record in self.records.items()
            if rtype == record_type and (status is None or record.get("status") == status)
        ]
        rows.sort(key=lambda row: row["external_id"])
        return rows

    def set_status(self, record_type, external_id, status):
        record = self.get(record_type, external_id)
        record["status"] = status
        record["payload"]["status"] = status
        self.records[(record_type, external_id)] = deepcopy(record)
        return deepcopy(record)
