#!/usr/bin/env python3
import argparse
import json
import sys

from expenseflow.errors import ExpenseFlowError
from expenseflow.kolo_command_gateway import KoloCommandGateway
from expenseflow.kolo_workflows import (
    capture_expense,
    decide_report_approval,
    export_approved_report_csv,
    submit_report_for_approval,
    upsert_approval_policy,
    upsert_department_policy,
    upsert_expense_settings,
    upsert_user_profile,
)


def _load_json(value):
    try:
        return json.loads(value)
    except json.JSONDecodeError as exc:
        raise ExpenseFlowError("invalid_json", f"Invalid JSON: {exc.msg}")


def _ok(**payload):
    return {"status": "ok", **payload}


def cmd_upsert_user(args, gateway):
    return _ok(result=upsert_user_profile(gateway, _load_json(args.profile)))


def cmd_upsert_settings(args, gateway):
    return _ok(result=upsert_expense_settings(gateway, args.org_id, _load_json(args.settings)))


def cmd_upsert_approval_policy(args, gateway):
    return _ok(result=upsert_approval_policy(gateway, args.org_id, _load_json(args.policy)))


def cmd_upsert_department_policy(args, gateway):
    return _ok(result=upsert_department_policy(gateway, args.department, _load_json(args.policy)))


def cmd_capture_expense(args, gateway):
    return _ok(
        expense=capture_expense(
            gateway,
            _load_json(args.expense),
            args.submitter_user_id,
            org_id=args.org_id,
            expense_id=args.expense_id,
        )
    )


def cmd_submit_report(args, gateway):
    return _ok(
        result=submit_report_for_approval(
            gateway,
            args.submitter_user_id,
            args.expense_id,
            org_id=args.org_id,
            title=args.title,
            period=_load_json(args.period) if args.period else None,
            report_id=args.report_id,
            approval_request_id=args.approval_request_id,
        )
    )


def cmd_decide_report(args, gateway):
    return _ok(
        result=decide_report_approval(
            gateway,
            args.approval_request_id,
            int(args.approver_user_id),
            args.decision,
            note=args.note,
            decision_id=args.decision_id,
        )
    )


def cmd_export_csv(args, gateway):
    return _ok(result=export_approved_report_csv(gateway, args.report_id))


def main(argv=None):
    parser = argparse.ArgumentParser(description="ExpenseFlow Kolo runtime CLI")
    parser.add_argument("--org-id", default="default")
    sub = parser.add_subparsers(dest="command", required=True)

    cmd = sub.add_parser("upsert-user")
    cmd.add_argument("--profile", required=True)
    cmd.set_defaults(func=cmd_upsert_user)

    cmd = sub.add_parser("upsert-settings")
    cmd.add_argument("--settings", required=True)
    cmd.set_defaults(func=cmd_upsert_settings)

    cmd = sub.add_parser("upsert-approval-policy")
    cmd.add_argument("--policy", required=True)
    cmd.set_defaults(func=cmd_upsert_approval_policy)

    cmd = sub.add_parser("upsert-department-policy")
    cmd.add_argument("--department", required=True)
    cmd.add_argument("--policy", required=True)
    cmd.set_defaults(func=cmd_upsert_department_policy)

    cmd = sub.add_parser("capture-expense")
    cmd.add_argument("--submitter-user-id", required=True)
    cmd.add_argument("--expense", required=True)
    cmd.add_argument("--expense-id")
    cmd.set_defaults(func=cmd_capture_expense)

    cmd = sub.add_parser("submit-report")
    cmd.add_argument("--submitter-user-id", required=True)
    cmd.add_argument("--expense-id", action="append", required=True)
    cmd.add_argument("--title")
    cmd.add_argument("--period")
    cmd.add_argument("--report-id")
    cmd.add_argument("--approval-request-id")
    cmd.set_defaults(func=cmd_submit_report)

    cmd = sub.add_parser("decide-report")
    cmd.add_argument("--approval-request-id", required=True)
    cmd.add_argument("--approver-user-id", required=True)
    cmd.add_argument("--decision", choices=["approved", "rejected"], required=True)
    cmd.add_argument("--note")
    cmd.add_argument("--decision-id")
    cmd.set_defaults(func=cmd_decide_report)

    cmd = sub.add_parser("export-csv")
    cmd.add_argument("--report-id", required=True)
    cmd.set_defaults(func=cmd_export_csv)

    args = parser.parse_args(argv)
    gateway = KoloCommandGateway()
    try:
        result = args.func(args, gateway)
    except ExpenseFlowError as exc:
        print(json.dumps(exc.to_dict(), indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
