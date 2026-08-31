# Data Analytics

Work that answers a question someone already has: reporting, dashboards, SQL
against the warehouse, and the presentation rules that make a number readable.

Typical output is a query, a chart, or a short report — something a
non-specialist can act on without reading the code behind it.

## Starting points

- `pandas` and `sqlalchemy` are installed; connect with the `PG*` environment
  variables, which are already set.
- Charts follow the IBCS conventions used across the Innovation Forum:
  message-first titles, one accent colour against grey, no chart junk, units in
  the header and a source note under every exhibit.
- Keep the words and the numbers apart. Prose belongs in a content file, not
  interleaved with layout code — the reasoning is in
  [the WS-3 decision](https://innovation-forum-cambridgeshire.github.io/Innovation-lab/10-decisions/).

## House rule

Every published figure should be derivable, not retyped. A number typed into a
document is a copy, and copies drift silently.
