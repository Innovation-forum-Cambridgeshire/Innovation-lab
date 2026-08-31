"""Chat front-end for Great Expectations checks against the acme_sad database.

Run with:  chainlit run chainlit_app.py     (serves on http://127.0.0.1:8000)

Type `help` in the chat for the command list.

Design note, because it is the first thing a reviewer will ask about:
every check runs through GX's *pandas* backend, even though the data lives in
PostgreSQL. Two reasons, both specific to this lab:
  1. The pandas backend fills in `unexpected_index_list`, so a failure can name
     the offending row by its `id` instead of saying "3 rows failed somewhere".
     Naming the row is the whole point of the house rule about loud checks.
  2. It makes the offline fallback the identical code path. When the database is
     down we swap the DataFrame and change nothing else, so the fallback cannot
     silently drift away from the real checks.
The tables here are 2-9 rows, so pulling them into memory costs nothing. On a
table that did not fit in memory this would be the wrong call, and you would
switch to `context.data_sources.add_postgres(...)` and give up row-level detail.
"""

# No `from __future__ import annotations` here, deliberately.
#
# Chainlit loads this file with spec.loader.exec_module and never puts
# it in sys.modules. With string annotations, @dataclass then tries to
# resolve them through sys.modules[cls.__module__], gets None, and dies
# with "AttributeError: 'NoneType' object has no attribute '__dict__'"
# before a single line of this app runs -- an error naming dataclasses
# and pointing at neither Chainlit nor the class that triggered it.
#
# Python 3.10+ evaluates `X | None` natively, so nothing here needs it.

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import chainlit as cl
import great_expectations as gx
import great_expectations.expectations as gxe
import pandas as pd
import sqlalchemy as sa
from great_expectations.data_context.types.base import ProgressBarsConfig

# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

# The container already exports these. Reading them at call time rather than at
# import time means a database that comes back up mid-session starts working
# without restarting Chainlit.
_PG_ENV = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE")

# PGPASSWORD is absent under trust or .pgpass auth, and PGPORT has a well-known
# default, so neither being unset is by itself an error worth refusing to start.
_REQUIRED_PG_ENV = ("PGHOST", "PGUSER", "PGDATABASE")


class DatabaseUnavailable(Exception):
    """Raised when we cannot reach acme_sad, carrying text fit to show a learner."""


def _build_url() -> sa.engine.URL:
    """Assemble the connection URL from the environment.

    Nothing here is hardcoded on purpose: the host and password belong to the
    container, not to this file. `sa.engine.URL.create` also escapes a password
    containing `@` or `/`, which hand-built connection strings get wrong.
    """
    missing = [name for name in _REQUIRED_PG_ENV if not os.environ.get(name)]
    if missing:
        raise DatabaseUnavailable(
            "These environment variables are not set: " + ", ".join(missing)
        )

    return sa.engine.URL.create(
        # psycopg 3, which is what the lab image ships. The v2 driver is
        # not installed, and asking SQLAlchemy for it fails at connect
        # time with a ModuleNotFoundError that names neither.
        drivername="postgresql+psycopg",
        host=os.environ["PGHOST"],
        port=int(os.environ.get("PGPORT") or 5432),
        username=os.environ["PGUSER"],
        password=os.environ.get("PGPASSWORD"),
        database=os.environ["PGDATABASE"],
    )


def describe_target() -> str | None:
    """Where we are pointed, password masked. None when nothing is configured.

    `render_as_string` defaults to hiding the password; we pass it explicitly so
    that nobody later "simplifies" the call and leaks a credential into the chat.
    """
    try:
        url = _build_url()
    except DatabaseUnavailable:
        return None
    return url.render_as_string(hide_password=True)


def load_table(table: str) -> pd.DataFrame:
    """Read one whole table, or raise DatabaseUnavailable with a readable reason."""
    # A table name cannot be a bound parameter, so it goes into the SQL text
    # directly. Checking it against CHECKS here rather than trusting the caller
    # means the only strings that ever reach the query are ones written above.
    if table not in CHECKS:
        raise ValueError(f"{table!r} is not a table this app knows about")

    try:
        engine = sa.create_engine(_build_url())
        with engine.connect() as conn:
            return pd.read_sql_query(sa.text(f'SELECT * FROM "{table}"'), conn)
    except DatabaseUnavailable:
        raise
    except Exception as exc:  # sqlalchemy wraps driver errors in many shapes
        raise DatabaseUnavailable(f"{type(exc).__name__}: {exc}") from exc


# ---------------------------------------------------------------------------
# What we check, and why
# ---------------------------------------------------------------------------

QUALITY_DIMENSIONS = [
    "COMPLETENESS",
    "ACCURACY",
    "TIMELINESS",
    "CONSISTENCY",
    "VALIDITY",
    "TRACEABILITY",
]

# A guess at the SOP lifecycle, not something the schema told us. It is here
# because an unbounded status column is how "Aproved" and "approved " end up as
# separate states. When this fires, read the offending values it prints and
# decide whether the data is wrong or this list is - both are real outcomes.
SOP_STATUSES = ["DRAFT", "IN_REVIEW", "APPROVED", "PUBLISHED", "SUPERSEDED", "RETIRED"]


@dataclass(frozen=True)
class TableCheck:
    table: str
    key: str
    why: str
    # A factory, not a list. GX stamps an `id` onto an Expectation when it joins
    # a suite and then refuses to add that same object anywhere else, so reusing
    # one list across two runs raises RuntimeError. Building fresh objects each
    # run sidesteps that entirely.
    build: Callable[[], list[Any]]


CHECKS: dict[str, TableCheck] = {
    "knowledge_asset": TableCheck(
        table="knowledge_asset",
        key="id",
        why="Assets are the spine of the register; an unowned or unscored asset cannot be governed.",
        build=lambda: [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            gxe.ExpectColumnValuesToNotBeNull(column="id"),
            gxe.ExpectColumnValuesToBeUnique(column="id"),
            gxe.ExpectColumnValuesToNotBeNull(column="title"),
            # Stated rule: criticality is a 1-5 scale. A 0 or a 6 means someone
            # imported from a system with a different scale.
            gxe.ExpectColumnValuesToBeBetween(column="criticality", min_value=1, max_value=5),
            # Stated rule: a percentage. Catches the classic 0-1 vs 0-100 mixup.
            gxe.ExpectColumnValuesToBeBetween(
                column="documentation_coverage", min_value=0, max_value=100
            ),
            # Not a schema constraint - a governance one. An asset with no named
            # owner is exactly the finding this register exists to surface.
            gxe.ExpectColumnValuesToNotBeNull(column="owner_person_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="created_at"),
        ],
    ),
    "person": TableCheck(
        table="person",
        key="id",
        why="People carry accountability; a nameless or half-recorded person breaks every ownership trail.",
        build=lambda: [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            gxe.ExpectColumnValuesToNotBeNull(column="id"),
            gxe.ExpectColumnValuesToBeUnique(column="id"),
            gxe.ExpectColumnValuesToNotBeNull(column="name"),
            gxe.ExpectColumnValuesToNotBeNull(column="role"),
            # A null boolean is a third state nobody designed for. Either this
            # person is a steward or they are not; "unknown" is a data gap.
            gxe.ExpectColumnValuesToNotBeNull(column="is_steward"),
        ],
    ),
    "critical_data_element": TableCheck(
        table="critical_data_element",
        key="id",
        why="The stated rule: every CDE has exactly one accountable owner, and a dimension we recognise.",
        build=lambda: [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            gxe.ExpectColumnValuesToNotBeNull(column="id"),
            gxe.ExpectColumnValuesToBeUnique(column="id"),
            # The headline rule for this folder. Declared NOT NULL in the schema,
            # which is precisely why it is worth asserting here too: if this ever
            # fails, the constraint was dropped or the load bypassed it.
            gxe.ExpectColumnValuesToNotBeNull(column="owner_id"),
            # asset_id is deliberately NOT checked. The column is nullable in
            # the schema, and wiki/04's foreign-key list never ties a CDE to
            # one asset -- the register describes elements of TABLES
            # ("process.id", "knowledge_asset.legacy_location"), not of
            # individual asset rows. Asserting it non-null reported all nine
            # rows as broken when the data was right and the expectation was
            # wrong. That is the failure mode a check like this must avoid:
            # a red result nobody believes gets ignored, and then the real
            # one is ignored with it.
            gxe.ExpectColumnValuesToNotBeNull(column="element_name"),
            # An element without a definition is a name, not a data element.
            gxe.ExpectColumnValuesToNotBeNull(column="definition"),
            gxe.ExpectColumnValuesToBeInSet(
                column="quality_dimension", value_set=QUALITY_DIMENSIONS
            ),
        ],
    ),
    "sop_version": TableCheck(
        table="sop_version",
        key="id",
        why="A procedure whose review is due before it takes effect is a control that was never real.",
        build=lambda: [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            gxe.ExpectColumnValuesToNotBeNull(column="id"),
            gxe.ExpectColumnValuesToBeUnique(column="id"),
            gxe.ExpectColumnValuesToNotBeNull(column="asset_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="version"),
            gxe.ExpectColumnValuesToBeInSet(column="status", value_set=SOP_STATUSES),
            # The date rule. `ignore_row_if` matters: a draft legitimately has no
            # effective date yet, and we do not want that noise drowning out the
            # genuine inversions where both dates exist and run backwards.
            gxe.ExpectColumnPairValuesAToBeGreaterThanB(
                column_A="next_review_date",
                column_B="effective_date",
                or_equal=False,
                ignore_row_if="either_value_is_missing",
            ),
        ],
    ),
    "review": TableCheck(
        table="review",
        key="id",
        why="A review is evidence. Undated or unattributed, it proves nothing in an audit.",
        build=lambda: [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            gxe.ExpectColumnValuesToNotBeNull(column="id"),
            gxe.ExpectColumnValuesToBeUnique(column="id"),
            # An orphan review points at no SOP version and cannot be counted.
            gxe.ExpectColumnValuesToNotBeNull(column="sop_version_id"),
            gxe.ExpectColumnValuesToNotBeNull(column="reviewed_by"),
            gxe.ExpectColumnValuesToNotBeNull(column="review_date"),
            gxe.ExpectColumnValuesToNotBeNull(column="outcome"),
        ],
    ),
    "department": TableCheck(
        table="department",
        key="id",
        why="Departments anchor the org rollup; a negative head count breaks every aggregate built on it.",
        build=lambda: [
            gxe.ExpectTableRowCountToBeBetween(min_value=1),
            gxe.ExpectColumnValuesToNotBeNull(column="id"),
            gxe.ExpectColumnValuesToBeUnique(column="id"),
            gxe.ExpectColumnValuesToNotBeNull(column="name"),
            # No upper bound: we know a head count cannot be negative, and we do
            # not know what "too big" is. Asserting only what we know keeps this
            # from failing on a legitimately large department.
            #
            # Worth knowing, and it bites people: every value-level expectation
            # in GX skips nulls. A NULL head_count passes this check. If you want
            # nulls to fail, pair the range check with an explicit not-null one -
            # a range check alone is not the same as "this column is populated".
            gxe.ExpectColumnValuesToBeBetween(column="head_count", min_value=0),
        ],
    ),
}


# ---------------------------------------------------------------------------
# Offline sample data
# ---------------------------------------------------------------------------

def offline_frames() -> dict[str, pd.DataFrame]:
    """Tiny stand-ins used when acme_sad is unreachable.

    Defects are planted deliberately. A fallback that always passes teaches the
    learner that the checks are decorative; this one shows them failing.
    """
    return {
        "critical_data_element": pd.DataFrame(
            {
                "id": [1, 2, 3],
                "asset_id": [10, 10, 11],
                "element_name": ["customer_id", "iban", "risk_band"],
                "definition": ["Unique customer key", "Account number", None],
                "data_type": ["string", "string", "string"],
                # Row 2 has no owner: violates the stated one-owner rule.
                "owner_id": [7, None, 9],
                # Row 3 uses a dimension outside the allowed set.
                "quality_dimension": ["COMPLETENESS", "ACCURACY", "PRECISION"],
                "created_at": pd.to_datetime(["2026-01-04", "2026-01-05", "2026-01-06"]),
            }
        ),
        "sop_version": pd.DataFrame(
            {
                "id": [1, 2, 3],
                "asset_id": [10, 10, 11],
                "version": ["1.0", "1.1", "2.0"],
                # Row 3 uses a status outside the lifecycle we expect.
                "status": ["APPROVED", "DRAFT", "SIGNED_OFF"],
                "effective_date": [
                    pd.Timestamp("2026-02-01").date(),
                    None,
                    pd.Timestamp("2026-03-01").date(),
                ],
                # Row 3's review falls before its effective date.
                "next_review_date": [
                    pd.Timestamp("2027-02-01").date(),
                    None,
                    pd.Timestamp("2026-01-15").date(),
                ],
                "last_review_date": [None, None, None],
            }
        ),
        "department": pd.DataFrame(
            {
                "id": [1, 2],
                "name": ["Risk", "Operations"],
                # A negative head count: impossible, and the kind of thing that
                # quietly poisons a rollup rather than erroring.
                "head_count": [12, -3],
            }
        ),
    }


# ---------------------------------------------------------------------------
# Running the checks
# ---------------------------------------------------------------------------

def run_suite(spec: TableCheck, frame: pd.DataFrame) -> Any:
    """Validate one DataFrame and return the GX suite result.

    A fresh ephemeral context per run keeps this simple: contexts refuse to add
    two suites under one name, and rebuilding costs nothing at this data size.
    """
    context = gx.get_context(mode="ephemeral")
    # GX prints a tqdm bar per metric otherwise, which buries the Chainlit logs.
    context.variables.progress_bars = ProgressBarsConfig(globally=False)

    batch_definition = (
        context.data_sources.add_pandas(name=f"{spec.table}_source")
        .add_dataframe_asset(name=spec.table)
        .add_batch_definition_whole_dataframe(f"{spec.table}_whole")
    )

    suite = context.suites.add(gx.ExpectationSuite(name=f"{spec.table}_suite"))
    for expectation in spec.build():
        suite.add_expectation(expectation)

    validation_definition = context.validation_definitions.add(
        gx.ValidationDefinition(
            name=f"{spec.table}_validation", data=batch_definition, suite=suite
        )
    )

    return validation_definition.run(
        batch_parameters={"dataframe": frame},
        # COMPLETE, not SUMMARY: SUMMARY truncates the failing rows, and a
        # truncated list is how a real defect gets skimmed past.
        # `unexpected_index_column_names` is what lets us say "id=4" instead of
        # "positional row 3", which is the difference between a report someone
        # can act on and one they cannot.
        result_format={
            "result_format": "COMPLETE",
            "unexpected_index_column_names": [spec.key],
        },
    )


# ---------------------------------------------------------------------------
# Turning results into something worth reading
# ---------------------------------------------------------------------------

_MAX_ROWS_SHOWN = 20


def _plain(value: Any) -> str:
    """Render a cell the way a person would write it.

    GX hands back numpy scalars, so a raw f-string prints `np.int64(4)`. Nulls
    become the word NULL because an empty string in a failure list reads as if
    the tool lost the value.
    """
    if value is None:
        return "NULL"
    if hasattr(value, "item"):  # numpy scalar
        try:
            value = value.item()
        except (ValueError, AttributeError):
            pass
    if value is None:
        return "NULL"
    try:
        if pd.isna(value):
            return "NULL"
    except (TypeError, ValueError):
        pass  # arrays and lists are not NA-testable; fall through
    return str(value)


def _num(value: Any) -> str:
    """Print bounds the way they were written.

    GX widens min_value/max_value to float, so a 1-5 scale would otherwise read
    "between 1.0 and 5.0" and invite the reader to wonder about 2.5.
    """
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _expected_phrase(kind: str, kwargs: dict[str, Any]) -> str:
    """Say what the rule was, in words, so the reader need not decode kwargs."""
    column = kwargs.get("column")
    if kind == "expect_column_values_to_not_be_null":
        return f"every row has a value in `{column}`"
    if kind == "expect_column_values_to_be_unique":
        return f"`{column}` is unique across all rows"
    if kind == "expect_column_values_to_be_in_set":
        allowed = ", ".join(str(v) for v in kwargs.get("value_set", []))
        return f"`{column}` is one of: {allowed}"
    if kind == "expect_column_values_to_be_between":
        low, high = kwargs.get("min_value"), kwargs.get("max_value")
        if low is not None and high is not None:
            return f"`{column}` is between {_num(low)} and {_num(high)}"
        if low is not None:
            return f"`{column}` is at least {_num(low)}"
        return f"`{column}` is at most {_num(high)}"
    if kind == "expect_column_pair_values_a_to_be_greater_than_b":
        a, b = kwargs.get("column_A"), kwargs.get("column_B")
        equal = " or equal to" if kwargs.get("or_equal") else ""
        return f"`{a}` is later than{equal} `{b}` (rows missing either date are skipped)"
    if kind == "expect_table_row_count_to_be_between":
        return f"the table holds at least {_num(kwargs.get('min_value'))} row(s)"
    # Anything added later still reports something truthful rather than nothing.
    return f"{kind} with {kwargs}"


def _exception_messages(res: Any) -> list[str]:
    """Pull error text out of a result, whichever shape GX used.

    `exception_info` arrives two different ways: flat
    (`{"raised_exception": False, ...}`) when the expectation ran, and keyed by
    metric id when a metric blew up underneath it. A missing column takes the
    second form, so checking only the flat shape reports "column not found" as
    an ordinary data failure - which is how a schema change gets mistaken for
    dirty data and quietly ignored.
    """
    info = res.exception_info or {}
    if not isinstance(info, dict):
        return []
    if "raised_exception" in info:
        message = info.get("exception_message")
        return [str(message)] if info.get("raised_exception") and message else []

    messages = []
    for entry in info.values():
        if isinstance(entry, dict) and entry.get("exception_message"):
            text = str(entry["exception_message"])
            if text not in messages:  # one missing column trips several metrics
                messages.append(text)
    return messages


def _failure_rows(spec: TableCheck, result: dict[str, Any]) -> str:
    """List the offending rows by primary key, with the value that broke the rule."""
    index_list = result.get("unexpected_index_list") or []
    if index_list and isinstance(index_list[0], dict):
        rendered = []
        for entry in index_list[:_MAX_ROWS_SHOWN]:
            key_part = f"{spec.key}={_plain(entry.get(spec.key))}"
            # Everything except the key is the value(s) that failed.
            others = ", ".join(
                f"{k}={_plain(v)}" for k, v in entry.items() if k != spec.key
            )
            rendered.append(f"{key_part} ({others})" if others else key_part)
        suffix = ""
        if len(index_list) > _MAX_ROWS_SHOWN:
            suffix = f" ... and {len(index_list) - _MAX_ROWS_SHOWN} more"
        return "; ".join(rendered) + suffix

    # Table-level expectations have no row index. Fall back to the bad values.
    values = result.get("unexpected_list") or result.get("partial_unexpected_list") or []
    if values:
        shown = ", ".join(_plain(v) for v in values[:_MAX_ROWS_SHOWN])
        return f"offending values: {shown}"
    if "observed_value" in result:
        return f"observed: {_plain(result['observed_value'])}"
    return "no row detail returned for this expectation"


def format_report(spec: TableCheck, suite_result: Any, source_label: str) -> str:
    """Build the chat message for one table. Failures come first and in full."""
    stats = suite_result.statistics or {}
    passed = stats.get("successful_expectations", 0)
    total = stats.get("evaluated_expectations", 0)

    failures = [r for r in suite_result.results if not r.success]
    # An expectation that threw did not test anything. Separating these keeps a
    # broken check from being read as a clean pass or an ordinary data failure.
    broken = [r for r in failures if _exception_messages(r)]
    data_failures = [r for r in failures if not _exception_messages(r)]

    lines: list[str] = []
    if suite_result.success:
        lines.append(f"## PASS - {spec.table}")
        lines.append(f"All {total} expectations held. Source: {source_label}.")
        return "\n".join(lines)

    lines.append(f"## FAIL - {spec.table}")
    lines.append(
        f"**{len(failures)} of {total} expectations failed** "
        f"({passed} passed). Source: {source_label}."
    )
    if broken:
        lines.append("")
        lines.append(
            f"**{len(broken)} of those never ran** - see 'Checks that could not run' "
            "below. Treat those columns as unverified, not as clean."
        )
    lines.append("")

    for res in data_failures:
        kind = res.expectation_config.type
        kwargs = {k: v for k, v in res.expectation_config.kwargs.items() if k != "batch_id"}
        detail = res.result or {}
        unexpected = detail.get("unexpected_count")
        element_count = detail.get("element_count")

        lines.append(f"### {kind}")
        lines.append(f"- **Expected:** {_expected_phrase(kind, kwargs)}")
        if unexpected is not None and element_count:
            pct = 100.0 * unexpected / element_count
            lines.append(
                f"- **Actual:** {unexpected} of {element_count} rows break it ({pct:.1f}%)"
            )
        elif "observed_value" in detail:
            lines.append(f"- **Actual:** {_plain(detail['observed_value'])}")
        else:
            lines.append("- **Actual:** expectation returned unsuccessful")
        lines.append(f"- **Offending rows:** {_failure_rows(spec, detail)}")
        lines.append("")

    if broken:
        lines.append("### Checks that could not run")
        lines.append(
            "Usually the column is missing or renamed, which means the table no "
            "longer matches what these checks assume."
        )
        lines.append("")
        for res in broken:
            kwargs = {
                k: v for k, v in res.expectation_config.kwargs.items() if k != "batch_id"
            }
            columns = ", ".join(
                str(kwargs[k]) for k in ("column", "column_A", "column_B") if k in kwargs
            )
            where = f" on `{columns}`" if columns else ""
            reason = "; ".join(_exception_messages(res))
            lines.append(f"- `{res.expectation_config.type}`{where} - {reason}")
        lines.append("")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Chat plumbing
# ---------------------------------------------------------------------------

HELP = """**Commands**

- `check <table>` - run the checks for one table
- `check all` - run every table in turn
- `expectations` - list what is asserted, and why
- `tables` - the tables this app knows about
- `status` - where we are pointed and whether it answers
- `help` - this message

Tables: """ + ", ".join(f"`{t}`" for t in CHECKS)


def _expectations_listing() -> str:
    lines = ["# What this app asserts", ""]
    for spec in CHECKS.values():
        lines.append(f"## {spec.table}")
        lines.append(f"_{spec.why}_")
        lines.append("")
        for expectation in spec.build():
            kwargs = {
                k: v
                for k, v in expectation.configuration.kwargs.items()
                if k != "batch_id"
            }
            kind = expectation.configuration.type
            lines.append(f"- `{kind}` - {_expected_phrase(kind, kwargs)}")
        lines.append("")
    return "\n".join(lines)


def _check_one(table: str) -> str:
    """Run one table's checks, falling back to the sample frame if the DB is down."""
    spec = CHECKS[table]
    try:
        frame = load_table(table)
        return format_report(spec, run_suite(spec, frame), f"PostgreSQL `{table}`")
    except DatabaseUnavailable as exc:
        fallback = offline_frames().get(table)
        target = describe_target()
        header = (
            "## Cannot reach the database\n\n"
            + (f"Tried `{target}` and got:\n\n" if target else "")
            + f"```\n{exc}\n```\n"
        )
        if fallback is None:
            return (
                header
                + f"\nI have no offline sample for `{table}`, so nothing was checked. "
                + "Offline samples exist for: "
                + ", ".join(f"`{t}`" for t in offline_frames())
                + "."
            )
        report = format_report(
            spec, run_suite(spec, fallback), f"built-in sample rows for `{table}`"
        )
        return (
            header
            + "\nSo I ran the same expectations against a small in-memory frame "
            + "instead. **These results say nothing about your real data** - they "
            + "only show that the checks work and what a failure looks like.\n\n"
            + report
        )


def handle(text: str) -> str:
    """Route one line of chat. Kept synchronous so it is easy to unit test."""
    command = " ".join(text.strip().split())
    lowered = command.lower()

    if not lowered or lowered in {"help", "?", "commands"}:
        return HELP
    if lowered in {"tables", "list"}:
        return "Known tables:\n" + "\n".join(
            f"- `{s.table}` - {s.why}" for s in CHECKS.values()
        )
    if lowered == "expectations":
        return _expectations_listing()
    if lowered == "status":
        target = describe_target()
        label = f"Target: `{target}`" if target else "No PG* variables are set."
        try:
            load_table(next(iter(CHECKS)))
        except DatabaseUnavailable as exc:
            return f"{label}\n\n**Unreachable:**\n```\n{exc}\n```"
        return f"{label}\n\nConnected, and the tables read cleanly."

    if lowered.startswith("check"):
        argument = command[len("check"):].strip()
        if not argument:
            return "Say `check all`, or `check <table>`.\n\n" + HELP
        if argument.lower() == "all":
            return "\n\n---\n\n".join(_check_one(t) for t in CHECKS)
        target = argument.lower()
        if target not in CHECKS:
            return (
                f"I do not know a table called `{argument}`. Known tables: "
                + ", ".join(f"`{t}`" for t in CHECKS)
            )
        return _check_one(target)

    return f"I did not understand `{command}`.\n\n" + HELP


@cl.on_chat_start
async def on_chat_start() -> None:
    target = describe_target()
    pointer = (
        f"Pointed at: `{target}`"
        if target
        else "**No PG\\* variables are set**, so I can only run the offline samples."
    )
    await cl.Message(
        content=(
            "# acme_sad data quality\n\n"
            f"Great Expectations {gx.__version__}, pandas {pd.__version__}.\n\n"
            f"{pointer}\n\n"
            "I have not connected yet - run `status` or `check all` and I will "
            "tell you plainly if the database is not answering.\n\n" + HELP
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message) -> None:
    # GX and psycopg2 both block. `make_async` moves them to a worker thread so
    # a slow query does not freeze the websocket and stall the whole UI.
    reply = await cl.make_async(handle)(message.content)
    await cl.Message(content=reply).send()
