# Innovation lab

A place to build things. Open it in a dev container, work in the folder that
matches the discipline, and commit what you make back here.

| Folder | For |
|---|---|
| [Data Analytics](./Data%20Analytics) | Reporting, dashboards, SQL, presentation |
| [Data Science](./Data%20Science) | Exploration, modelling, statistics |
| [Data Engineering](./Data%20Engineering) | Ingestion, schemas, migrations, pipelines |

---

## Getting started

You need a container engine (Docker Desktop, colima, Podman or Rancher
Desktop) and [DevPod](https://devpod.sh). Nothing else — no Python, no
database.

```bash
devpod up https://github.com/Innovation-forum-Cambridgeshire/Innovation-lab --ide vscode
```

Or in the DevPod desktop app: **Create Workspace**, paste that URL under
**Git Repo**, choose the `docker` provider and **VS Code**.

New to any of this? The illustrated walkthrough is
[SOP 12](https://innovation-forum-cambridgeshire.github.io/Innovation-lab/12-sop-devcontainer/)
— it assumes no container knowledge and the screenshots are of the real
application.

The environment arrives prebuilt: `pandas`, `numpy`, `pyarrow`, `matplotlib`,
`plotly`, `jupyterlab`, `sqlalchemy`, and clients for PostgreSQL, Redis and
MinIO. Startup is seconds because nothing is compiled locally.

## Data

This lab does **not** run its own database. It connects to the data tier from
Public_Challenge, which must be running **on the same machine**.

> **This step needs organisation access.** Public_Challenge is a private
> repository, so the clone below only works once you have been admitted to the
> Innovation Forum organisation. Everything else on this page — the dev
> container, the folders, the toolchain — works without it. The
> [documentation](https://innovation-forum-cambridgeshire.github.io/Innovation-lab/)
> is public either way.

```bash
git clone https://github.com/Innovation-forum-Cambridgeshire/Public_Challenge
cd Public_Challenge
uv run python scripts/dev.py start        # or: start --uv, for no Docker at all
uv run python scripts/init_db.py          # schema, migrations, seed
```

Then, in a lab terminal:

```python
import os, psycopg
conn = psycopg.connect(
    host=os.environ["PGHOST"], port=os.environ["PGPORT"],
    user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"],
    dbname=os.environ["PGDATABASE"],
)
```

Those variables are already set for you.

**If the tier is not running, connections fail.** That is the trade-off of
sharing one database between the two repositories rather than each carrying
its own: one place to seed, one place to look, but the lab depends on its
neighbour being up.

> DevPod forwards ports 12864-12867 to your host while a workspace exists, and
> stopping the workspace does not release them. If the Public_Challenge tier
> will not bind, that is usually why —
> [the details are here](https://innovation-forum-cambridgeshire.github.io/Innovation-lab/12-sop-devcontainer/#63-ports-12864-12867-are-already-in-use-on-your-own-machine).

## Where the environment comes from

`.devcontainer/devcontainer.json` references a published image:

```
ghcr.io/innovation-forum-cambridgeshire/lab-devcontainer:latest
```

It is built from
`.devcontainer/Dockerfile` in Public_Challenge
and republished when that file changes. Referencing one image rather than
copying the setup into every lab repository means there is a single definition
to maintain — the same reason this organisation's documents derive their
figures instead of retyping them.

To add a tool for everyone, change the Dockerfile in Public_Challenge. To add
one for yourself, `uv pip install` it in your workspace.

## Conventions

Each discipline folder has a README with its own starting points and house
rule. They are short, and worth the minute.
