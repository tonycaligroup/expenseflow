import hashlib
import json
from decimal import Decimal

from .errors import ExpenseFlowError
from .money import money_to_str, normalize_currency, parse_money
from .sheets_export import safe_receipt_reference


QBO_TRANSACTION_TYPES = {"purchase", "bill", "journalentry"}
QBO_PAYMENT_TYPES = {"Cash", "Check", "CreditCard"}


def build_qbo_transaction(report, expenses, config):
    _validate_approved(report, expenses)
    transaction_type = str(config.get("transaction_type") or "").strip().lower()
    if transaction_type not in QBO_TRANSACTION_TYPES:
        raise ExpenseFlowError(
            "invalid_qbo_transaction_type",
            "QuickBooks transaction_type must be purchase, bill, or journalentry.",
        )
    if not expenses:
        raise ExpenseFlowError("empty_export", "At least one expense is required for QuickBooks sync.")

    currencies = {normalize_currency(expense.get("currency")) for expense in expenses}
    if len(currencies) != 1:
        raise ExpenseFlowError(
            "qbo_multi_currency_report",
            "A QuickBooks sync run must contain expenses in one currency.",
            details={"currencies": sorted(currencies)},
        )
    currency = next(iter(currencies))
    line_specs = [_line_spec(expense, config) for expense in expenses]
    document_number = _document_number(report.get("report_id"), report.get("org_id") or "default")
    common = {
        "DocNumber": document_number,
        "TxnDate": max(str(expense.get("date") or "") for expense in expenses),
        "CurrencyRef": {"value": currency},
        "PrivateNote": _private_note(report, expenses),
    }
    if config.get("department_id"):
        common["DepartmentRef"] = {"value": str(config["department_id"])}

    if transaction_type == "purchase":
        body = _purchase_body(common, line_specs, config)
        entity_type = "Purchase"
        path = "purchase"
    elif transaction_type == "bill":
        body = _bill_body(common, line_specs, report, config)
        entity_type = "Bill"
        path = "bill"
    else:
        body = _journal_entry_body(common, line_specs, config)
        entity_type = "JournalEntry"
        path = "journalentry"

    payload_hash = canonical_hash({"path": path, "body": body})
    return {
        "entity_type": entity_type,
        "path": path,
        "body": body,
        "request_id": "expenseflow-" + payload_hash[:24],
        "payload_hash": payload_hash,
        "line_items": [
            {
                "expense_id": spec["expense_id"],
                "line_index": index,
                "content_hash": canonical_hash(spec),
            }
            for index, spec in enumerate(line_specs, start=1)
        ],
    }


def normalize_qbo_reference_cache(response_by_entity):
    fields = {
        "Account": ("Id", "Name", "AccountType", "AccountSubType", "Active"),
        "Vendor": ("Id", "DisplayName", "Active"),
        "Customer": ("Id", "DisplayName", "Active"),
        "TaxCode": ("Id", "Name", "Description", "Active", "Taxable"),
        "Class": ("Id", "Name", "FullyQualifiedName", "Active"),
        "Department": ("Id", "Name", "FullyQualifiedName", "Active"),
        "Currency": ("Id", "Name", "Code", "Active"),
    }
    collection_names = {
        "Account": "accounts",
        "Vendor": "vendors",
        "Customer": "customers",
        "TaxCode": "tax_codes",
        "Class": "classes",
        "Department": "departments",
        "Currency": "currencies",
    }
    cache = {}
    for entity, allowed_fields in fields.items():
        response = response_by_entity.get(entity) or {}
        query_response = response.get("QueryResponse", response.get("queryResponse", {}))
        rows = query_response.get(entity, query_response.get(entity.lower(), []))
        if isinstance(rows, dict):
            rows = [rows]
        if not isinstance(rows, list):
            raise ExpenseFlowError(
                "invalid_qbo_query_response",
                "QuickBooks returned an invalid reference query response.",
                details={"entity": entity},
            )
        cache[collection_names[entity]] = [
            {field: row[field] for field in allowed_fields if field in row}
            for row in rows
            if isinstance(row, dict) and row.get("Id") is not None
        ]
    return cache


def extract_qbo_entity(execution_result, entity_type):
    if not isinstance(execution_result, dict):
        raise ExpenseFlowError(
            "qbo_execution_result_invalid",
            "QuickBooks execution completed without a usable result object.",
        )
    entity = _find_entity(execution_result, entity_type.lower())
    if not isinstance(entity, dict) or entity.get("Id") is None:
        raise ExpenseFlowError(
            "qbo_execution_result_invalid",
            "QuickBooks execution did not return the created entity ID.",
            details={"entity_type": entity_type},
        )
    return {
        "entity_type": entity_type,
        "entity_id": str(entity["Id"]),
        "sync_token": None if entity.get("SyncToken") is None else str(entity.get("SyncToken")),
    }


def canonical_hash(value):
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _validate_approved(report, expenses):
    if report.get("status") not in {"approved", "exported", "synced"}:
        raise ExpenseFlowError(
            "report_not_approved",
            "Only approved reports can be synced to QuickBooks.",
            details={"report_id": report.get("report_id"), "status": report.get("status")},
        )
    invalid = [
        expense.get("expense_id")
        for expense in expenses
        if expense.get("status") not in {"approved", "exported", "synced"}
    ]
    if invalid:
        raise ExpenseFlowError(
            "expense_not_approved",
            "Only approved expenses can be synced to QuickBooks.",
            details={"expense_ids": invalid},
        )


def _line_spec(expense, config):
    category = str(expense.get("category") or "").strip()
    mappings = config.get("category_account_ids") or {}
    account_id = mappings.get(category) or mappings.get("*")
    if not account_id:
        raise ExpenseFlowError(
            "missing_qbo_category_mapping",
            "No QuickBooks account mapping exists for an expense category.",
            details={"expense_id": expense.get("expense_id"), "category": category},
        )
    amount = parse_money(expense.get("amount"), "amount")
    detail = {"AccountRef": {"value": str(account_id)}}
    if config.get("default_class_id"):
        detail["ClassRef"] = {"value": str(config["default_class_id"])}
    if config.get("default_tax_code_id"):
        detail["TaxCodeRef"] = {"value": str(config["default_tax_code_id"])}
    return {
        "expense_id": str(expense.get("expense_id")),
        "amount": money_to_str(amount),
        "description": _line_description(expense),
        "detail": detail,
    }


def _purchase_body(common, line_specs, config):
    balancing_account_id = _required_config(config, "balancing_account_id")
    payment_type = str(config.get("payment_type") or "Cash")
    if payment_type not in QBO_PAYMENT_TYPES:
        raise ExpenseFlowError(
            "invalid_qbo_payment_type",
            "QuickBooks purchase payment_type must be Cash, Check, or CreditCard.",
        )
    return {
        **common,
        "PaymentType": payment_type,
        "AccountRef": {"value": balancing_account_id},
        "Line": [_account_expense_line(spec) for spec in line_specs],
    }


def _bill_body(common, line_specs, report, config):
    vendor_ids = config.get("employee_vendor_ids") or {}
    submitter_id = str(report.get("submitter_user_id"))
    vendor_id = vendor_ids.get(submitter_id) or config.get("default_employee_vendor_id")
    if not vendor_id:
        raise ExpenseFlowError(
            "missing_qbo_employee_vendor",
            "QuickBooks Bill sync requires a vendor mapping for the report submitter.",
            details={"submitter_user_id": report.get("submitter_user_id")},
        )
    body = {
        **common,
        "VendorRef": {"value": str(vendor_id)},
        "Line": [_account_expense_line(spec) for spec in line_specs],
    }
    if config.get("accounts_payable_account_id"):
        body["APAccountRef"] = {"value": str(config["accounts_payable_account_id"])}
    return body


def _journal_entry_body(common, line_specs, config):
    balancing_account_id = _required_config(config, "balancing_account_id")
    lines = []
    total = Decimal("0.00")
    for spec in line_specs:
        amount = Decimal(spec["amount"])
        total += amount
        detail = {
            "PostingType": "Debit",
            "AccountRef": spec["detail"]["AccountRef"],
        }
        if spec["detail"].get("ClassRef"):
            detail["ClassRef"] = spec["detail"]["ClassRef"]
        lines.append(
            {
                "Amount": _json_amount(amount),
                "Description": spec["description"],
                "DetailType": "JournalEntryLineDetail",
                "JournalEntryLineDetail": detail,
            }
        )
    lines.append(
        {
            "Amount": _json_amount(total),
            "Description": "ExpenseFlow employee expense clearing",
            "DetailType": "JournalEntryLineDetail",
            "JournalEntryLineDetail": {
                "PostingType": "Credit",
                "AccountRef": {"value": balancing_account_id},
            },
        }
    )
    return {**common, "Line": lines}


def _account_expense_line(spec):
    return {
        "Amount": _json_amount(Decimal(spec["amount"])),
        "Description": spec["description"],
        "DetailType": "AccountBasedExpenseLineDetail",
        "AccountBasedExpenseLineDetail": spec["detail"],
    }


def _json_amount(amount):
    return float(money_to_str(amount))


def _required_config(config, key):
    value = str(config.get(key) or "").strip()
    if not value:
        raise ExpenseFlowError(
            "missing_qbo_account_mapping",
            f"QuickBooks destination requires {key}.",
            details={"field": key},
        )
    return value


def _document_number(report_id, org_id="default"):
    material = f"{org_id}:{report_id}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:16].upper()
    return "EF-" + digest


def _line_description(expense):
    parts = [
        str(expense.get("vendor") or "Expense"),
        str(expense.get("category") or "Uncategorized"),
        f"ExpenseFlow {expense.get('expense_id')}",
    ]
    note = str(expense.get("note") or "").strip()
    if note:
        parts.append(note)
    return " | ".join(parts)[:1000]


def _private_note(report, expenses):
    references = [safe_receipt_reference(expense) for expense in expenses]
    references = [reference for reference in references if reference]
    text = f"ExpenseFlow report {report.get('report_id')}"
    if references:
        text += "; receipt refs: " + ", ".join(references)
    return text[:4000]


def _find_entity(value, expected_key):
    current = value
    for _ in range(4):
        if not isinstance(current, dict):
            return None
        for key, item in current.items():
            if str(key).lower() == expected_key and isinstance(item, dict):
                return item
        envelope = next(
            (
                current[key]
                for key in ("execution_result", "executionResult", "result", "response", "body", "data")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if envelope is None:
            return None
        current = envelope
    return None
