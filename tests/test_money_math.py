import unittest

from scripts.expenseflow.money import parse_money, totals_by_currency


class MoneyMathTests(unittest.TestCase):
    def test_rounds_half_up(self):
        self.assertEqual(str(parse_money("10.005")), "10.01")

    def test_totals_are_grouped_by_currency(self):
        totals = totals_by_currency(
            [
                {"amount": "100.00", "currency": "EUR"},
                {"amount": "50.00", "currency": "USD"},
                {"amount": "25.50", "currency": "USD"},
            ]
        )

        self.assertEqual(totals, {"EUR": "100.00", "USD": "75.50"})


if __name__ == "__main__":
    unittest.main()
