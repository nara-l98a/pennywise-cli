import csv
import tempfile
import unittest
from pathlib import Path

from pennywise.app import (add_transaction, budget_status, connect, delete_transaction,
                           export_csv, get_transactions, import_csv, money,
                           search_transactions, set_budget, summary, to_cents, _validate_date)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.conn = connect(self.root / "ledger.db")

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_exact_money_and_validation(self):
        self.assertEqual(to_cents("12.30"), 1230)
        self.assertEqual(money(9_007_199_254_740_993), "90,071,992,547,409.93")
        for invalid in ("0", "-1", "1.001", "nan", "oops"):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                to_cents(invalid)
        with self.assertRaisesRegex(ValueError, "too large"):
            to_cents("92233720368547758.08")
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            _validate_date("20260401")

    def test_transactions_summary_and_budget(self):
        add_transaction(self.conn, "income", "2500.00", "salary", "2026-04-01")
        add_transaction(self.conn, "expense", "42.75", "food", "2026-04-03", "groceries")
        add_transaction(self.conn, "expense", "60", "transport", "2026-03-30")
        set_budget(self.conn, "2026-04", "food", "50")
        data = summary(self.conn, "2026-04")
        self.assertEqual(data["income_cents"], 250000)
        self.assertEqual(data["expense_cents"], 4275)
        self.assertEqual(data["net_cents"], 245725)
        self.assertEqual(data["budgets"][0]["spent_cents"], 4275)
        self.assertEqual(len(get_transactions(self.conn, "2026-04", "expense")), 1)
        self.assertEqual(budget_status(self.conn, "2026-04")[0]["budget_cents"], 5000)

    def test_csv_round_trip(self):
        add_transaction(self.conn, "expense", "8.99", "books", "2026-04-10", "reference")
        exported = self.root / "transactions.csv"
        self.assertEqual(export_csv(self.conn, exported, "2026-04"), 1)
        target = connect(self.root / "second.db")
        try:
            self.assertEqual(import_csv(target, exported), 1)
            row = get_transactions(target)[0]
            self.assertEqual((row["category"], row["amount_cents"], row["note"]), ("books", 899, "reference"))
        finally:
            target.close()

    def test_csv_preserves_large_amount_exactly(self):
        add_transaction(self.conn, "income", "90071992547409.93", "sale", "2026-04-10")
        exported = self.root / "large.csv"
        export_csv(self.conn, exported)
        with exported.open(newline="", encoding="utf-8") as f:
            self.assertEqual(next(csv.DictReader(f))["amount"], "90071992547409.93")

    def test_bad_csv_import_is_atomic(self):
        path = self.root / "bad.csv"
        path.write_text("date,type,amount,category,note\n2026-04-01,expense,3,food,ok\n2026-04-02,wat,4,food,bad\n")
        with self.assertRaisesRegex(ValueError, "line 3"):
            import_csv(self.conn, path)
        self.assertEqual(len(get_transactions(self.conn)), 0)

    def test_search_filters_and_delete(self):
        first = add_transaction(self.conn, "expense", "8.99", "Books", "2026-04-10", "Python guide")
        add_transaction(self.conn, "expense", "12", "food", "2026-04-11", "Lunch")
        self.assertEqual([r["id"] for r in search_transactions(
            self.conn, text="python", start="2026-04-01", end="2026-04-30", kind="expense")], [first])
        self.assertEqual([r["category"] for r in search_transactions(self.conn, category="FOOD")], ["food"])
        with self.assertRaisesRegex(ValueError, "after"):
            search_transactions(self.conn, start="2026-05-01", end="2026-04-01")
        self.assertTrue(delete_transaction(self.conn, first))
        self.assertFalse(delete_transaction(self.conn, first))
        self.assertEqual(len(get_transactions(self.conn)), 1)


if __name__ == "__main__":
    unittest.main()
