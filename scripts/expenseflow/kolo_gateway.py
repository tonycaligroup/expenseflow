from copy import deepcopy
import re
from uuid import uuid4

from .errors import ExpenseFlowError


class FakeKoloGateway:
    """Governed-record and messaging test double for Kolo workflows."""

    def __init__(self, peers=None, spreadsheets=None):
        self.records = {}
        self.messages = []
        self.tasks = []
        self.uploads = []
        self.audit_events = {}
        self.peers = deepcopy(peers or [])
        self.spreadsheets = deepcopy(spreadsheets or {})
        self.sheet_operations = []
        self.sheet_failures = {}

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

    def complete_task(self, task_id):
        for task in self.tasks:
            if task["task_id"] == task_id:
                task["status"] = "completed"
                return deepcopy(task)
        raise ExpenseFlowError("task_not_found", f"No Kolo task found for {task_id}.")

    def upload_file(self, file_path):
        object_id = f"obj_{uuid4().hex[:12]}"
        upload = {
            "object_store_object_id": object_id,
            "reference": f"kolo://obj/{object_id}",
            "file_path": file_path,
        }
        self.uploads.append(deepcopy(upload))
        return deepcopy(upload)

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

    def add_spreadsheet(self, spreadsheet_id, sheet_name="ExpenseFlow", rows=None, sheet_id=0):
        self.spreadsheets[str(spreadsheet_id)] = {
            "spreadsheet_id": str(spreadsheet_id),
            "title": f"Spreadsheet {spreadsheet_id}",
            "sheets": {
                sheet_name: {
                    "sheet_id": sheet_id,
                    "rows": deepcopy(rows or []),
                }
            },
        }

    def queue_sheet_failure(self, operation, error):
        self.sheet_failures.setdefault(operation, []).append(error)

    def sheets_get_metadata(self, spreadsheet_id):
        self._raise_sheet_failure("metadata")
        spreadsheet = self._spreadsheet(spreadsheet_id)
        self.sheet_operations.append({"operation": "metadata", "spreadsheet_id": str(spreadsheet_id)})
        return {
            "spreadsheetId": str(spreadsheet_id),
            "properties": {"title": spreadsheet["title"]},
            "sheets": [
                {"properties": {"sheetId": sheet["sheet_id"], "title": title}}
                for title, sheet in spreadsheet["sheets"].items()
            ],
        }

    def sheets_read_values(self, spreadsheet_id, a1_range):
        self._raise_sheet_failure("read")
        sheet_name, start_row, end_row, start_column, end_column = _parse_a1(a1_range)
        sheet = self._sheet(spreadsheet_id, sheet_name)
        rows = sheet["rows"]
        last_row = len(rows) if end_row is None else min(end_row, len(rows))
        values = []
        for row_number in range(start_row, last_row + 1):
            row = rows[row_number - 1] if row_number <= len(rows) else []
            sliced = list(row[start_column - 1 : end_column])
            while sliced and sliced[-1] == "":
                sliced.pop()
            values.append(sliced)
        self.sheet_operations.append(
            {"operation": "read", "spreadsheet_id": str(spreadsheet_id), "range": a1_range}
        )
        return {"range": a1_range, "majorDimension": "ROWS", "values": deepcopy(values)}

    def sheets_update_values(self, spreadsheet_id, a1_range, values):
        self._raise_sheet_failure("update")
        sheet_name, start_row, _, start_column, end_column = _parse_a1(a1_range)
        sheet = self._sheet(spreadsheet_id, sheet_name)
        width = end_column - start_column + 1
        for offset, incoming in enumerate(values):
            row_number = start_row + offset
            while len(sheet["rows"]) < row_number:
                sheet["rows"].append([])
            row = sheet["rows"][row_number - 1]
            while len(row) < end_column:
                row.append("")
            row[start_column - 1 : end_column] = list(incoming[:width]) + [""] * max(0, width - len(incoming))
        self.sheet_operations.append(
            {
                "operation": "update",
                "spreadsheet_id": str(spreadsheet_id),
                "range": a1_range,
                "values": deepcopy(values),
            }
        )
        return {
            "spreadsheetId": str(spreadsheet_id),
            "updatedRange": a1_range,
            "updatedRows": len(values),
            "updatedColumns": width,
        }

    def sheets_append_values(self, spreadsheet_id, a1_range, values):
        self._raise_sheet_failure("append")
        sheet_name, _, _, start_column, end_column = _parse_a1(a1_range)
        sheet = self._sheet(spreadsheet_id, sheet_name)
        first_row = len(sheet["rows"]) + 1
        width = end_column - start_column + 1
        for incoming in values:
            row = [""] * (start_column - 1)
            row.extend(list(incoming[:width]) + [""] * max(0, width - len(incoming)))
            sheet["rows"].append(row)
        last_row = first_row + len(values) - 1
        updated_range = f"'{sheet_name.replace(chr(39), chr(39) * 2)}'!{_column_label(start_column)}{first_row}:{_column_label(end_column)}{last_row}"
        response = {
            "spreadsheetId": str(spreadsheet_id),
            "tableRange": a1_range,
            "updates": {
                "updatedRange": updated_range,
                "updatedRows": len(values),
                "updatedColumns": width,
                "updatedCells": len(values) * width,
            },
        }
        self.sheet_operations.append(
            {
                "operation": "append",
                "spreadsheet_id": str(spreadsheet_id),
                "range": a1_range,
                "values": deepcopy(values),
                "response": deepcopy(response),
            }
        )
        return response

    def _spreadsheet(self, spreadsheet_id):
        try:
            return self.spreadsheets[str(spreadsheet_id)]
        except KeyError:
            raise ExpenseFlowError("sheets_not_found", "The configured Google spreadsheet was not found.")

    def _sheet(self, spreadsheet_id, sheet_name):
        spreadsheet = self._spreadsheet(spreadsheet_id)
        try:
            return spreadsheet["sheets"][sheet_name]
        except KeyError:
            raise ExpenseFlowError(
                "sheet_tab_not_found",
                "The configured Google Sheets tab was not found.",
                details={"sheet_name": sheet_name},
            )

    def _raise_sheet_failure(self, operation):
        failures = self.sheet_failures.get(operation) or []
        if failures:
            error = failures.pop(0)
            if isinstance(error, Exception):
                raise error
            raise AssertionError("Queued sheet failure must be an exception.")


_A1_RE = re.compile(
    r"^(?P<sheet>'(?:[^']|'')+'|[^!]+)!(?P<start_col>[A-Z]+)(?P<start_row>\d+):(?P<end_col>[A-Z]+)(?P<end_row>\d*)$"
)


def _parse_a1(value):
    match = _A1_RE.match(str(value))
    if not match:
        raise ExpenseFlowError("sheets_invalid_request", "Fake Sheets gateway received an invalid A1 range.")
    sheet_name = match.group("sheet")
    if sheet_name.startswith("'"):
        sheet_name = sheet_name[1:-1].replace("''", "'")
    return (
        sheet_name,
        int(match.group("start_row")),
        int(match.group("end_row")) if match.group("end_row") else None,
        _column_number(match.group("start_col")),
        _column_number(match.group("end_col")),
    )


def _column_number(label):
    number = 0
    for character in label:
        number = number * 26 + ord(character) - 64
    return number


def _column_label(number):
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label
