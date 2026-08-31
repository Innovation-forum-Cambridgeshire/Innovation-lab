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

## House rule

A single source of truth that nothing compares against is just another copy.
If two places hold the same figure, one of them should derive it, and something
should assert they agree.
