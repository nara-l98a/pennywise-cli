# Pennywise CLI

A small, private, local-first personal finance ledger. Track income and expenses, set monthly category budgets, review spending, and exchange data through CSV. Your data stays in a SQLite file on your machine; the app has no network calls and no third-party runtime dependencies.

## Requirements

- Python 3.10 or newer
- No runtime packages beyond Python's standard library

## Install

```bash
git clone https://github.com/nara-l98a/pennywise-cli.git
cd pennywise-cli
python3 -m venv .venv
. .venv/bin/activate
pip install .
```

You can also run from a checkout without installing: `PYTHONPATH=src python -m pennywise --help`.

## Quick start

```bash
# Record income and spending; dates default to today.
pennywise add income 4200.00 salary --note "April pay"
pennywise add expense 18.75 food --note "Lunch"
pennywise add expense 63.20 transport --date 2026-04-04

# Review a month and its category breakdown.
pennywise summary --month 2026-04
pennywise list --month 2026-04
pennywise list --month 2026-04 --type expense

# Set a monthly category limit; summary shows amount spent and remaining/overage.
pennywise budget set food 350.00 --month 2026-04
pennywise budget list --month 2026-04

# Back up, share, or restore records with a CSV file.
pennywise export april.csv --month 2026-04
pennywise import april.csv
```

### Example output

```text
Summary for 2026-04
Income:  4,200.00
Expense: 81.95
Net:     4,118.05

Expense categories:
  transport: 63.20
  food: 18.75

Budgets:
  food: 18.75 / 350.00 (331.25 left)
```

## Data location and options

By default, the SQLite database is `~/.local/share/pennywise/ledger.sqlite3`. Pass `--db PATH` before the subcommand to use another file, or set `PENNYWISE_DB`:

```bash
pennywise --db ./demo.sqlite3 add expense 12.50 coffee
PENNYWISE_DB=./ledger.sqlite3 pennywise summary
```

Amounts are stored as integer cents to avoid floating-point accounting errors. The CLI accepts positive values with at most two decimal places. Dates must be ISO `YYYY-MM-DD`; month filters use `YYYY-MM`. CSV imports are validated in full before any rows are inserted, so a malformed row does not result in a partial import. CSV headers are `date,type,amount,category,note`.

## Commands

| Command | Purpose |
|---|---|
| `add income\|expense AMOUNT CATEGORY [--date DATE] [--note TEXT]` | Add a record |
| `list [--month YYYY-MM] [--type income\|expense]` | List matching records |
| `summary [--month YYYY-MM]` | Monthly totals, expense categories and budgets |
| `budget set CATEGORY AMOUNT --month YYYY-MM` | Create or update a budget |
| `budget list [--month YYYY-MM]` | Show budget usage |
| `export FILE [--month YYYY-MM]` | Write records to CSV |
| `import FILE` | Import the documented CSV format |

## Development and tests

```bash
python -m unittest discover -s tests -v
```

Tests use temporary databases and files; they do not touch your real ledger.

## Privacy

The database and exported CSV files may contain sensitive financial information. Keep them in a trusted location and do not commit them to source control. Pennywise never uploads your data.

## License

MIT. See [LICENSE](LICENSE).
