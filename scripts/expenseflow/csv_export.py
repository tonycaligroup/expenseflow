import csv
from io import StringIO

from .errors import ExpenseFlowError
from .money import money_to_str, normalize_currency, parse_money


CSV_COLUMNS = [
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
]

FORMULA_PREFIXES = ("=", "+", "-", "@")


def escape_csv_cell(value):
    text = "" if value is None else str(value)
    if text.startswith(FORMULA_PREFIXES):
        return "'" + text
    return text


def expense_to_csv_row(report, expense):
    amount = money_to_str(parse_money(expense.get("amount"), "amount"))
    tax = money_to_str(parse_money(expense.get("tax", "0.00"), "tax", allow_zero=True))
    return {
        "report_id": report.get("report_id"),
        "expense_id": expense.get("expense_id"),
        "submitter_user_id": expense.get("submitter_user_id") or report.get("submitter_user_id"),
        "submitter_name": expense.get("submitter_name") or report.get("submitter_name"),
        "date": expense.get("date"),
        "vendor": expense.get("vendor"),
        "category": expense.get("category"),
        "amount": amount,
        "currency": normalize_currency(expense.get("currency")),
        "tax": tax,
        "payment_method": expense.get("payment_method"),
        "receipt_ref": expense.get("receipt_ref"),
        "note": expense.get("note"),
    }


def generate_report_csv(report, expenses):
    if report.get("status") != "approved":
        raise ExpenseFlowError(
            "report_not_approved",
            "Only approved reports can be exported.",
            details={"report_id": report.get("report_id"), "status": report.get("status")},
        )
    if not expenses:
        raise ExpenseFlowError("empty_export", "At least one expense is required for CSV export.")

    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for expense in expenses:
        if expense.get("status") != "approved":
            raise ExpenseFlowError(
                "expense_not_approved",
                "Only approved expenses can be exported.",
                details={"expense_id": expense.get("expense_id"), "status": expense.get("status")},
            )
        row = expense_to_csv_row(report, expense)
        writer.writerow({key: escape_csv_cell(row.get(key)) for key in CSV_COLUMNS})
    return output.getvalue()
