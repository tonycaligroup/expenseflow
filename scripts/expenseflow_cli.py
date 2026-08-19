#!/usr/bin/env python3
import argparse
import json
import sys

from expenseflow.errors import ExpenseFlowError
from expenseflow.expense_core import create_expense, detect_duplicates, validate_expense
from expenseflow.report_engine import create_report


def _load_json(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExpenseFlowError("invalid_json", f"Invalid JSON: {exc.msg}")


def _ok(**payload):
    return {"status": "ok", **payload}


def cmd_validate_expense(args):
    return _ok(expense=validate_expense(_load_json(args.expense), _load_json(args.settings or "{}")))


def cmd_create_expense(args):
    return _ok(
        expense=create_expense(
            _load_json(args.expense),
            _load_json(args.submitter),
            _load_json(args.settings or "{}"),
            args.expense_id,
        )
    )


def cmd_detect_duplicates(args):
    return _ok(
        duplicates=detect_duplicates(
            _load_json(args.expense),
            _load_json(args.existing),
        )
    )


def cmd_create_report(args):
    return _ok(
        report=create_report(
            _load_json(args.expenses),
            _load_json(args.submitter),
            args.title,
            _load_json(args.period) if args.period else None,
            args.report_id,
        )
    )


def main(argv=None):
    parser = argparse.ArgumentParser(description="ExpenseFlow deterministic core CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    cmd = sub.add_parser("validate-expense")
    cmd.add_argument("--expense", required=True)
    cmd.add_argument("--settings")
    cmd.set_defaults(func=cmd_validate_expense)

    cmd = sub.add_parser("create-expense")
    cmd.add_argument("--expense", required=True)
    cmd.add_argument("--submitter", required=True)
    cmd.add_argument("--settings")
    cmd.add_argument("--expense-id")
    cmd.set_defaults(func=cmd_create_expense)

    cmd = sub.add_parser("detect-duplicates")
    cmd.add_argument("--expense", required=True)
    cmd.add_argument("--existing", required=True)
    cmd.set_defaults(func=cmd_detect_duplicates)

    cmd = sub.add_parser("create-report")
    cmd.add_argument("--expenses", required=True)
    cmd.add_argument("--submitter", required=True)
    cmd.add_argument("--title")
    cmd.add_argument("--period")
    cmd.add_argument("--report-id")
    cmd.set_defaults(func=cmd_create_report)

    args = parser.parse_args(argv)
    try:
        result = args.func(args)
    except ExpenseFlowError as exc:
        print(json.dumps(exc.to_dict(), indent=2))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
