#!/usr/bin/env python3
import argparse
import json
import sys

from expenseflow.errors import ExpenseFlowError
from expenseflow.kolo_command_gateway import KoloCommandGateway
from expenseflow.kolo_workflows import (
    acknowledge_expense_policy,
    attach_receipt_reference,
    approve_user_onboarding,
    capture_expense,
    capture_expense_with_discovery,
    configure_organization,
    decide_report_approval,
    export_approved_report_csv,
    export_approved_report_sheets,
    map_sender_identity,
    organization_setup_readiness,
    refresh_qbo_reference_cache,
    reconcile_user_directory,
    send_due_approval_reminders,
    submit_report_for_approval,
    sync_approved_report_qbo,
    upsert_accounting_destination,
    upsert_approval_delegation,
    upsert_approval_policy,
    upsert_department_policy,
    upsert_expense_settings,
    upsert_user_profile,
    upload_and_attach_receipt,
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
    return _ok(result=upsert_department_policy(gateway, args.department, _load_json(args.policy), args.org_id))


def cmd_upsert_destination(args, gateway):
    return _ok(result=upsert_accounting_destination(gateway, args.org_id, _load_json(args.destination)))


def cmd_upsert_delegation(args, gateway):
    return _ok(
        result=upsert_approval_delegation(
            gateway,
            _load_json(args.delegation),
            args.delegation_id,
            args.org_id,
        )
    )


def cmd_configure_org(args, gateway):
    return _ok(
        result=configure_organization(
            gateway,
            args.org_id,
            _load_json(args.settings),
            _load_json(args.approval_policy),
            _load_json(args.destination),
        )
    )


def cmd_reconcile_users(args, gateway):
    return _ok(result=reconcile_user_directory(gateway, args.org_id, args.deactivate_missing))


def cmd_setup_readiness(args, gateway):
    return _ok(
        result=organization_setup_readiness(
            gateway,
            args.org_id,
            verify_destination=not args.skip_integration_check,
        )
    )


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


def cmd_capture_with_discovery(args, gateway):
    return _ok(
        result=capture_expense_with_discovery(
            gateway,
            _load_json(args.expense),
            args.submitter_user_id,
            org_id=args.org_id,
            sender_id=args.sender_id,
            expense_id=args.expense_id,
        )
    )


def cmd_map_sender(args, gateway):
    return _ok(
        result=map_sender_identity(
            gateway,
            args.sender_id,
            args.user_id,
            args.admin_user_id,
            args.org_id,
        )
    )


def cmd_approve_onboarding(args, gateway):
    return _ok(
        result=approve_user_onboarding(
            gateway,
            args.user_id,
            args.admin_user_id,
            args.approver_user_id,
            org_id=args.org_id,
        )
    )


def cmd_acknowledge_policy(args, gateway):
    return _ok(
        result=acknowledge_expense_policy(
            gateway,
            args.user_id,
            args.acknowledging_user_id,
            args.policy_version,
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


def cmd_export_sheets(args, gateway):
    return _ok(result=export_approved_report_sheets(gateway, args.report_id))


def cmd_refresh_qbo(args, gateway):
    return _ok(result=refresh_qbo_reference_cache(gateway, args.org_id))


def cmd_sync_qbo(args, gateway):
    return _ok(
        result=sync_approved_report_qbo(
            gateway,
            args.report_id,
            session_key=args.session_key,
            chat_id=args.chat_id,
            retry_terminal=args.retry_terminal,
        )
    )


def cmd_attach_receipt(args, gateway):
    return _ok(
        result=attach_receipt_reference(
            gateway,
            args.expense_id,
            _load_json(args.attachment),
            args.acting_user_id,
            args.org_id,
        )
    )


def cmd_upload_receipt(args, gateway):
    return _ok(
        result=upload_and_attach_receipt(
            gateway,
            args.expense_id,
            args.file,
            args.acting_user_id,
            args.org_id,
            metadata=_load_json(args.metadata) if args.metadata else None,
        )
    )


def cmd_send_reminders(args, gateway):
    return _ok(result=send_due_approval_reminders(gateway, args.org_id, args.as_of))


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

    cmd = sub.add_parser("upsert-destination")
    cmd.add_argument("--destination", required=True)
    cmd.set_defaults(func=cmd_upsert_destination)

    cmd = sub.add_parser("upsert-delegation")
    cmd.add_argument("--delegation", required=True)
    cmd.add_argument("--delegation-id")
    cmd.set_defaults(func=cmd_upsert_delegation)

    cmd = sub.add_parser("configure-org")
    cmd.add_argument("--settings", required=True)
    cmd.add_argument("--approval-policy", required=True)
    cmd.add_argument("--destination", required=True)
    cmd.set_defaults(func=cmd_configure_org)

    cmd = sub.add_parser("reconcile-users")
    cmd.add_argument("--deactivate-missing", action="store_true")
    cmd.set_defaults(func=cmd_reconcile_users)

    cmd = sub.add_parser("setup-readiness")
    cmd.add_argument("--skip-integration-check", action="store_true")
    cmd.set_defaults(func=cmd_setup_readiness)

    cmd = sub.add_parser("capture-expense")
    cmd.add_argument("--submitter-user-id", required=True)
    cmd.add_argument("--expense", required=True)
    cmd.add_argument("--expense-id")
    cmd.set_defaults(func=cmd_capture_expense)

    cmd = sub.add_parser("capture-with-discovery")
    cmd.add_argument("--submitter-user-id", type=int)
    cmd.add_argument("--sender-id")
    cmd.add_argument("--expense", required=True)
    cmd.add_argument("--expense-id")
    cmd.set_defaults(func=cmd_capture_with_discovery)

    cmd = sub.add_parser("map-sender")
    cmd.add_argument("--sender-id", required=True)
    cmd.add_argument("--user-id", type=int, required=True)
    cmd.add_argument("--admin-user-id", type=int, required=True)
    cmd.set_defaults(func=cmd_map_sender)

    cmd = sub.add_parser("approve-onboarding")
    cmd.add_argument("--user-id", type=int, required=True)
    cmd.add_argument("--admin-user-id", type=int, required=True)
    cmd.add_argument("--approver-user-id", type=int, required=True)
    cmd.set_defaults(func=cmd_approve_onboarding)

    cmd = sub.add_parser("acknowledge-policy")
    cmd.add_argument("--user-id", type=int, required=True)
    cmd.add_argument("--acknowledging-user-id", type=int, required=True)
    cmd.add_argument("--policy-version", type=int, required=True)
    cmd.set_defaults(func=cmd_acknowledge_policy)

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

    cmd = sub.add_parser("export-sheets")
    cmd.add_argument("--report-id", required=True)
    cmd.set_defaults(func=cmd_export_sheets)

    cmd = sub.add_parser("qbo-refresh-cache")
    cmd.set_defaults(func=cmd_refresh_qbo)

    cmd = sub.add_parser("sync-qbo")
    cmd.add_argument("--report-id", required=True)
    cmd.add_argument("--session-key")
    cmd.add_argument("--chat-id")
    cmd.add_argument("--retry-terminal", action="store_true")
    cmd.set_defaults(func=cmd_sync_qbo)

    cmd = sub.add_parser("attach-receipt")
    cmd.add_argument("--expense-id", required=True)
    cmd.add_argument("--acting-user-id", type=int, required=True)
    cmd.add_argument("--attachment", required=True)
    cmd.set_defaults(func=cmd_attach_receipt)

    cmd = sub.add_parser("upload-receipt")
    cmd.add_argument("--expense-id", required=True)
    cmd.add_argument("--acting-user-id", type=int, required=True)
    cmd.add_argument("--file", required=True)
    cmd.add_argument("--metadata")
    cmd.set_defaults(func=cmd_upload_receipt)

    cmd = sub.add_parser("send-reminders")
    cmd.add_argument("--as-of")
    cmd.set_defaults(func=cmd_send_reminders)

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
