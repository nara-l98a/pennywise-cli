"""A dependency-free personal finance ledger backed by SQLite."""
from __future__ import annotations

import argparse
import csv
from contextlib import closing
import os
import sqlite3
import sys
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterable


def to_cents(value: str | Decimal | int) -> int:
    """Convert a human-readable amount to exact cents; reject fractions of a cent."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"invalid amount: {value!r}") from exc
    if not amount.is_finite() or amount <= 0:
        raise ValueError("amount must be a positive finite number")
    scaled = amount * 100
    if scaled != scaled.to_integral_value():
        raise ValueError("amount may have at most two decimal places")
    cents = int(scaled)
    if cents > 9_223_372_036_854_775_807:
        raise ValueError("amount is too large for the SQLite integer range")
    return cents


def money(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, fraction = divmod(abs(cents), 100)
    return f"{sign}{whole:,}.{fraction:02d}"


def _csv_amount(cents: int) -> str:
    whole, fraction = divmod(cents, 100)
    return f"{whole}.{fraction:02d}"


def validate_month(month: str) -> str:
    try:
        datetime.strptime(month, "%Y-%m")
    except ValueError as exc:
        raise ValueError("month must use YYYY-MM format") from exc
    if len(month) != 7:
        raise ValueError("month must use YYYY-MM format")
    return month


def connect(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY,
            kind TEXT NOT NULL CHECK (kind IN ('income', 'expense')),
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            category TEXT NOT NULL CHECK (length(trim(category)) > 0),
            occurred_on TEXT NOT NULL,
            note TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_transactions_date ON transactions(occurred_on);
        CREATE INDEX IF NOT EXISTS idx_transactions_category ON transactions(category);
        CREATE TABLE IF NOT EXISTS budgets (
            month TEXT NOT NULL,
            category TEXT NOT NULL,
            amount_cents INTEGER NOT NULL CHECK (amount_cents > 0),
            PRIMARY KEY (month, category)
        );
    """)
    return conn


def _validate_date(value: str) -> str:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("date must use YYYY-MM-DD format") from exc
    if parsed.isoformat() != value:
        raise ValueError("date must use YYYY-MM-DD format")
    return value


def add_transaction(conn: sqlite3.Connection, kind: str, amount: str, category: str,
                    occurred_on: str | None = None, note: str = "") -> int:
    if kind not in {"income", "expense"}:
        raise ValueError("type must be income or expense")
    category = category.strip()
    if not category:
        raise ValueError("category cannot be empty")
    day = _validate_date(occurred_on or date.today().isoformat())
    cur = conn.execute(
        "INSERT INTO transactions(kind, amount_cents, category, occurred_on, note) VALUES(?,?,?,?,?)",
        (kind, to_cents(amount), category, day, note.strip()),
    )
    conn.commit()
    return int(cur.lastrowid)


def get_transactions(conn: sqlite3.Connection, month: str | None = None,
                     kind: str | None = None) -> list[sqlite3.Row]:
    clauses, params = [], []
    if month:
        validate_month(month)
        clauses.append("substr(occurred_on, 1, 7) = ?")
        params.append(month)
    if kind:
        if kind not in {"income", "expense"}:
            raise ValueError("type must be income or expense")
        clauses.append("kind = ?")
        params.append(kind)
    sql = "SELECT * FROM transactions"
    if clauses:
        sql += " WHERE " + " AND ".join(clauses)
    sql += " ORDER BY occurred_on DESC, id DESC"
    return list(conn.execute(sql, params))


def set_budget(conn: sqlite3.Connection, month: str, category: str, amount: str) -> None:
    validate_month(month)
    category = category.strip()
    if not category:
        raise ValueError("category cannot be empty")
    conn.execute("INSERT INTO budgets(month, category, amount_cents) VALUES(?,?,?) "
                 "ON CONFLICT(month, category) DO UPDATE SET amount_cents=excluded.amount_cents",
                 (month, category, to_cents(amount)))
    conn.commit()


def budget_status(conn: sqlite3.Connection, month: str) -> list[dict[str, int | str]]:
    validate_month(month)
    rows = conn.execute("""
        SELECT b.category, b.amount_cents AS budget_cents,
               COALESCE(SUM(t.amount_cents), 0) AS spent_cents
        FROM budgets b LEFT JOIN transactions t
          ON t.category=b.category AND t.kind='expense' AND substr(t.occurred_on,1,7)=b.month
        WHERE b.month=? GROUP BY b.category, b.amount_cents ORDER BY b.category COLLATE NOCASE
    """, (month,)).fetchall()
    return [dict(r) for r in rows]


def summary(conn: sqlite3.Connection, month: str) -> dict:
    validate_month(month)
    totals = {r["kind"]: r["total"] for r in conn.execute(
        "SELECT kind, COALESCE(SUM(amount_cents),0) AS total FROM transactions "
        "WHERE substr(occurred_on,1,7)=? GROUP BY kind", (month,))}
    categories = conn.execute("SELECT category, SUM(amount_cents) AS total FROM transactions "
                              "WHERE kind='expense' AND substr(occurred_on,1,7)=? "
                              "GROUP BY category ORDER BY total DESC, category COLLATE NOCASE", (month,)).fetchall()
    income, expense = totals.get("income", 0), totals.get("expense", 0)
    return {"month": month, "income_cents": income, "expense_cents": expense,
            "net_cents": income - expense, "expense_categories": list(categories),
            "budgets": budget_status(conn, month)}


def export_csv(conn: sqlite3.Connection, filename: str | Path, month: str | None = None) -> int:
    rows = get_transactions(conn, month)
    with Path(filename).expanduser().open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["date", "type", "amount", "category", "note"])
        for row in rows:
            writer.writerow([row["occurred_on"], row["kind"], _csv_amount(row["amount_cents"]),
                             row["category"], row["note"]])
    return len(rows)


def import_csv(conn: sqlite3.Connection, filename: str | Path) -> int:
    """Validate the complete CSV before inserting it, so a bad row imports nothing."""
    parsed = []
    with Path(filename).expanduser().open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        required = {"date", "type", "amount", "category", "note"}
        if not reader.fieldnames or not required.issubset(set(reader.fieldnames)):
            raise ValueError("CSV must include columns: date,type,amount,category,note")
        for line, row in enumerate(reader, 2):
            try:
                kind = (row.get("type") or "").strip().lower()
                if kind not in {"income", "expense"}:
                    raise ValueError("type must be income or expense")
                day = _validate_date((row.get("date") or "").strip())
                category = (row.get("category") or "").strip()
                if not category:
                    raise ValueError("category cannot be empty")
                parsed.append((kind, to_cents(row.get("amount") or ""), category, day,
                               (row.get("note") or "").strip()))
            except ValueError as exc:
                raise ValueError(f"CSV line {line}: {exc}") from exc
    with conn:
        conn.executemany("INSERT INTO transactions(kind, amount_cents, category, occurred_on, note) "
                         "VALUES(?,?,?,?,?)", parsed)
    return len(parsed)


def _print_rows(rows: Iterable[sqlite3.Row]) -> None:
    print(f"{'ID':>4}  {'DATE':10}  {'TYPE':7}  {'AMOUNT':>12}  {'CATEGORY':18}  NOTE")
    for r in rows:
        print(f"{r['id']:>4}  {r['occurred_on']:10}  {r['kind']:7}  {money(r['amount_cents']):>12}  "
              f"{r['category'][:18]:18}  {r['note']}")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="pennywise", description="A private, local-first personal finance ledger.")
    p.add_argument("--db", default=os.getenv("PENNYWISE_DB", "~/.local/share/pennywise/ledger.sqlite3"),
                   help="SQLite database path (default: %(default)s)")
    sub = p.add_subparsers(dest="command", required=True)
    a = sub.add_parser("add", help="record an income or expense")
    a.add_argument("type", choices=["income", "expense"])
    a.add_argument("amount", help="positive amount, up to 2 decimal places")
    a.add_argument("category")
    a.add_argument("--date", dest="occurred_on", help="YYYY-MM-DD (default: today)")
    a.add_argument("--note", default="")
    l = sub.add_parser("list", help="list transactions")
    l.add_argument("--month", help="filter by YYYY-MM")
    l.add_argument("--type", choices=["income", "expense"])
    s = sub.add_parser("summary", help="show monthly totals, categories and budget usage")
    s.add_argument("--month", default=date.today().strftime("%Y-%m"))
    b = sub.add_parser("budget", help="manage monthly category budgets")
    bs = b.add_subparsers(dest="budget_command", required=True)
    sb = bs.add_parser("set", help="create or update a budget")
    sb.add_argument("category"); sb.add_argument("amount"); sb.add_argument("--month", required=True)
    lb = bs.add_parser("list", help="show budgets and spending")
    lb.add_argument("--month", default=date.today().strftime("%Y-%m"))
    e = sub.add_parser("export", help="export transactions to CSV")
    e.add_argument("filename"); e.add_argument("--month")
    i = sub.add_parser("import", help="import a CSV (date,type,amount,category,note)")
    i.add_argument("filename")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        with closing(connect(args.db)) as conn, conn:
            if args.command == "add":
                row_id = add_transaction(conn, args.type, args.amount, args.category, args.occurred_on, args.note)
                print(f"Added {args.type} #{row_id}: {args.category} {money(to_cents(args.amount))}")
            elif args.command == "list":
                _print_rows(get_transactions(conn, args.month, args.type))
            elif args.command == "summary":
                data = summary(conn, args.month)
                print(f"Summary for {data['month']}\nIncome:  {money(data['income_cents'])}\n"
                      f"Expense: {money(data['expense_cents'])}\nNet:     {money(data['net_cents'])}")
                if data["expense_categories"]:
                    print("\nExpense categories:")
                    for row in data["expense_categories"]:
                        print(f"  {row['category']}: {money(row['total'])}")
                if data["budgets"]:
                    print("\nBudgets:")
                    for row in data["budgets"]:
                        left = row["budget_cents"] - row["spent_cents"]
                        label = "OVER" if left < 0 else "left"
                        print(f"  {row['category']}: {money(row['spent_cents'])} / "
                              f"{money(row['budget_cents'])} ({money(abs(left))} {label})")
            elif args.command == "budget" and args.budget_command == "set":
                set_budget(conn, args.month, args.category, args.amount)
                print(f"Budget set: {args.month} {args.category} {money(to_cents(args.amount))}")
            elif args.command == "budget":
                rows = budget_status(conn, args.month)
                print(f"Budgets for {args.month}")
                for row in rows:
                    print(f"  {row['category']}: {money(row['spent_cents'])} / {money(row['budget_cents'])}")
                if not rows:
                    print("  No budgets set.")
            elif args.command == "export":
                print(f"Exported {export_csv(conn, args.filename, args.month)} transaction(s) to {args.filename}")
            elif args.command == "import":
                print(f"Imported {import_csv(conn, args.filename)} transaction(s) from {args.filename}")
        return 0
    except (ValueError, OSError, sqlite3.Error) as exc:
        print(f"pennywise: error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
