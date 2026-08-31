# Data Engineering

Work that makes the other two possible: ingestion, schemas, migrations,
pipelines, and the checks that catch a break before a person does.

Typical output is a pipeline, a schema change with its migration, or a
verification script.

## Starting points

- `psycopg`, `redis` and `minio` clients are installed and pointed at the
  shared data tier by the environment variables already set.
- Schema changes are versioned, ordered and idempotent — re-running a migration
  must not change row counts. See
  `wiki/04-schema-and-cdes.md` (Public_Challenge; read it with
  `uv run python scripts/dev.py docs`) for the pattern.
- Prefer a check that fails loudly over a report nobody reads.

## The sample application

`chainlit_app.py` runs Great Expectations against the data tier and answers in
chat. Meant to be replaced by your own work rather than kept.

```bash
chainlit run chainlit_app.py --host 0.0.0.0 --port 8100
```

Then open **http://localhost:8100** and type `check all`, `check <table>`,
`expectations` or `help`.

Port 8100, not Chainlit's default 8000: the neighbouring workspace serves the
wiki on 8000, and two forwarded 8000s collide on your machine. `--host 0.0.0.0`
is not optional inside a container.

Two things it knows that cost an afternoon to find out:

- Chainlit loads your app **without putting it in `sys.modules`**, so
  `@dataclass` together with `from __future__ import annotations` dies at
  import with `AttributeError: 'NoneType' object has no attribute '__dict__'`
  — an error naming neither Chainlit nor your class. That import is left out
  on purpose.
- Chainlit creates a `.files` directory when it is **imported**, so importing
  it from a directory you cannot write to fails outright.

The first thing it found on a real run was an expectation of its own that was
wrong, not a data defect. That is written up where it happened.

## House rule

A single source of truth that nothing compares against is just another copy.
If two places hold the same figure, one of them should derive it, and something
should assert they agree.
