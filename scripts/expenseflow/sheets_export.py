import hashlib
import json
import re

from .errors import ExpenseFlowError
from .money import money_to_str, normalize_currency, parse_money


SHEET_COLUMNS = [
    "report_id",
    "expense_id",
    "submitter_user_id",
    "submitter_name",
    "date",
    "vendor",
    "category",
    "amount",
    "currency",
    "tax",
    "payment_method",
    "receipt_ref",
    "note",
    "approved_at",
    "expenseflow_row_id",
]

_UPDATED_RANGE_RE = re.compile(r"^(?P<sheet>.+)!(?:\$?[A-Z]+)(?P<row>\d+):(?:\$?[A-Z]+)(?P=row)$")
_UNSAFE_RECEIPT_MARKERS = ("file://", "/media/inbound/", "/home/", "/tmp/")


def quote_sheet_title(title):
    normalized = str(title or "").strip()
    if not normalized:
        raise ExpenseFlowError("missing_sheet_name", "Google Sheets destination requires sheet_name.")
    return "'" + normalized.replace("'", "''") + "'"


def column_label(number):
    if not isinstance(number, int) or number < 1:
        raise ValueError("Column number must be a positive integer.")
    label = ""
    while number:
        number, remainder = divmod(number - 1, 26)
        label = chr(65 + remainder) + label
    return label


def header_range(sheet_name):
    return f"{quote_sheet_title(sheet_name)}!A1:{column_label(len(SHEET_COLUMNS))}1"


def data_range(sheet_name):
    return f"{quote_sheet_title(sheet_name)}!A2:{column_label(len(SHEET_COLUMNS))}"


def row_range(sheet_name, row_number):
    if not isinstance(row_number, int) or row_number < 2:
        raise ExpenseFlowError("invalid_sheet_row", "ExpenseFlow data rows must be on row 2 or later.")
    return f"{quote_sheet_title(sheet_name)}!A{row_number}:{column_label(len(SHEET_COLUMNS))}{row_number}"


def make_row_id(org_id, spreadsheet_id, expense_id):
    material = "\0".join(str(value) for value in (org_id, spreadsheet_id, expense_id))
    return "expenseflow_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def build_sheet_row(report, expense, org_id, spreadsheet_id):
    if report.get("status") not in {"approved", "exported"}:
        raise ExpenseFlowError(
            "report_not_approved",
            "Only approved reports can be exported.",
            details={"report_id": report.get("report_id"), "status": report.get("status")},
        )
    if expense.get("status") not in {"approved", "exported"}:
        raise ExpenseFlowError(
            "expense_not_approved",
            "Only approved expenses can be exported.",
            details={"expense_id": expense.get("expense_id"), "status": expense.get("status")},
        )
    row = {
        "report_id": report.get("report_id"),
        "expense_id": expense.get("expense_id"),
        "submitter_user_id": expense.get("submitter_user_id") or report.get("submitter_user_id"),
        "submitter_name": expense.get("submitter_name") or report.get("submitter_name"),
        "date": expense.get("date"),
        "vendor": expense.get("vendor"),
        "category": expense.get("category"),
        "amount": money_to_str(parse_money(expense.get("amount"), "amount")),
        "currency": normalize_currency(expense.get("currency")),
        "tax": money_to_str(parse_money(expense.get("tax", "0.00"), "tax", allow_zero=True)),
        "payment_method": expense.get("payment_method"),
        "receipt_ref": safe_receipt_reference(expense),
        "note": expense.get("note"),
        "approved_at": report.get("approved_at"),
        "expenseflow_row_id": make_row_id(org_id, spreadsheet_id, expense.get("expense_id")),
    }
    return ["" if row.get(column) is None else str(row.get(column)) for column in SHEET_COLUMNS]


def content_hash(values):
    encoded = json.dumps(list(values), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_updated_range(updated_range, expected_sheet_name=None):
    match = _UPDATED_RANGE_RE.match(str(updated_range or ""))
    if not match:
        raise ExpenseFlowError(
            "invalid_sheets_write_response",
            "Google Sheets did not return a usable updatedRange.",
            details={"updated_range": updated_range},
        )
    returned_title = _unquote_sheet_title(match.group("sheet"))
    if expected_sheet_name is not None and returned_title != expected_sheet_name:
        raise ExpenseFlowError(
            "unexpected_sheet_write",
            "Google Sheets reported a write to a different sheet.",
            details={"expected_sheet": expected_sheet_name, "returned_sheet": returned_title},
        )
    return int(match.group("row"))


def normalized_values(response, width=None):
    rows = response.get("values", []) if isinstance(response, dict) else []
    if not isinstance(rows, list):
        raise ExpenseFlowError("invalid_sheets_response", "Google Sheets returned invalid row values.")
    normalized = []
    for row in rows:
        if not isinstance(row, list):
            raise ExpenseFlowError("invalid_sheets_response", "Google Sheets returned an invalid row.")
        values = ["" if value is None else str(value) for value in row]
        if width is not None:
            values = (values + [""] * width)[:width]
        normalized.append(values)
    return normalized


def index_rows_by_id(rows):
    id_index = len(SHEET_COLUMNS) - 1
    indexed = {}
    for offset, row in enumerate(rows, start=2):
        values = (list(row) + [""] * len(SHEET_COLUMNS))[: len(SHEET_COLUMNS)]
        row_id = str(values[id_index] or "")
        if not row_id:
            continue
        indexed.setdefault(row_id, []).append({"row_number": offset, "values": values})
    return indexed


def safe_receipt_reference(expense):
    attachments = expense.get("receipt_attachments") or []
    for attachment in attachments:
        reference = attachment.get("reference") or attachment.get("object_store_object_id")
        if reference and not _is_unsafe_receipt_reference(reference):
            return str(reference)
    reference = str(expense.get("receipt_ref") or "")
    if _is_unsafe_receipt_reference(reference):
        return ""
    return reference


def _unquote_sheet_title(value):
    value = str(value)
    if len(value) >= 2 and value[0] == "'" and value[-1] == "'":
        return value[1:-1].replace("''", "'")
    return value


def _is_unsafe_receipt_reference(reference):
    normalized = str(reference).replace("\\", "/").lower()
    return any(marker in normalized for marker in _UNSAFE_RECEIPT_MARKERS)
