from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .errors import ExpenseFlowError


SUPPORTED_CURRENCIES = {
    "USD",
    "EUR",
    "GBP",
    "CAD",
    "AUD",
    "JPY",
    "MXN",
}


def normalize_currency(value):
    currency = (value or "USD").strip().upper()
    if currency not in SUPPORTED_CURRENCIES:
        raise ExpenseFlowError(
            "invalid_currency",
            f"Currency '{value}' is not supported.",
            details={"supported": sorted(SUPPORTED_CURRENCIES)},
        )
    return currency


def parse_money(value, field="amount", allow_zero=False):
    try:
        amount = Decimal(str(value).strip())
    except (AttributeError, InvalidOperation):
        raise ExpenseFlowError("invalid_money", f"{field} must be a number.")
    if amount < 0 or (amount == 0 and not allow_zero):
        requirement = "zero or greater" if allow_zero else "greater than zero"
        raise ExpenseFlowError(
            "invalid_money",
            f"{field} must be {requirement}.",
            details={"field": field},
        )
    return amount.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def money_to_str(value):
    return format(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), "f")


def totals_by_currency(expenses):
    totals = {}
    for expense in expenses:
        currency = normalize_currency(expense.get("currency"))
        amount = parse_money(expense.get("amount"), "amount")
        totals[currency] = totals.get(currency, Decimal("0.00")) + amount
    return {currency: money_to_str(total) for currency, total in sorted(totals.items())}
