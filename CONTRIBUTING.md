# Contributing

Thanks for considering a contribution!

## Development setup

```bash
git clone https://github.com/cometrue171/paper-vocab-trainer
cd paper-vocab-trainer
uv sync                                  # install runtime + dev dependencies
uv run manage.py init --no-dict          # create the SQLite schema (fast, no downloads)
uv run pytest -q                         # run the smoke tests
uv run manage.py serve                   # http://127.0.0.1:5010
```

`manage.py init` (without `--no-dict`) also downloads the ~150 MB ECDICT dictionary — only
needed when you want real Chinese glosses.

## Guidelines

- Keep dependencies light: **Flask + SQLite + pypdf** (plus `requests`, `fonttools`).
- Any new feature should keep `uv run manage.py init --no-dict && uv run pytest` green.
- Never commit: downloaded corpora (`data/lit.db`, `data/ecdict.csv`, `fulltext/`), the Android
  build project (`mobile/`, contains signing keys), or credentials.
- Adding a new research **direction**: drop `data/seed_<domain>.tsv`
  (`word<TAB>Chinese gloss`) and extend `lib/directions.py`.

## Reporting issues

Please include: what you ran, what you expected, what happened, and your OS/Python version.
