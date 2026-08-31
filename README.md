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

New to any of this? There is an illustrated walkthrough that assumes no
container knowledge, with screenshots of the real application. It lives in
Public_Challenge at `wiki/12-sop-devcontainer.md`, so reading it needs
organisation access — see **Documentation** below.

The environment carries `pandas`, `numpy`, `pyarrow`, `matplotlib`, `plotly`,
`jupyterlab`, `sqlalchemy`, clients for PostgreSQL, Redis and MinIO, and the
two application frameworks with their libraries: **Streamlit + scikit-learn**
and **Chainlit + Great Expectations**.

There is a working sample of each, meant to be replaced rather than kept:

| | Run it | Then open |
|---|---|---|
| `Data Science/streamlit_app.py` | `streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501` | http://localhost:8501 |
| `Data Engineering/chainlit_app.py` | `chainlit run chainlit_app.py --host 0.0.0.0 --port 8100` | http://localhost:8100 |

Both bind `0.0.0.0` on purpose. Bound to loopback inside a container, the
forwarded port reaches nothing.

Your **first** create builds the image and takes a couple of minutes. Every
create after that is seconds, because the layers are cached on your machine.

## Data

This lab does **not** run its own database. It connects to the data tier from
Public_Challenge, which must be running **on the same machine**.

> **This step needs organisation access.** Public_Challenge is a private
> repository, so the clone below only works once you have been admitted to the
> Innovation Forum organisation. Everything else on this page — the dev
> container, the folders, the toolchain — works without it.

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
> will not bind, that is usually why. Section 6.3 of `wiki/12-sop-devcontainer.md`
> in Public_Challenge has the commands to find and free them.

## Documentation

The architecture documents, the decision log and both standard operating
procedures live in Public_Challenge, under `wiki/`. **They are not published to
a website.** They are for admitted participants, and being admitted is what
gives you the repository that holds them:

```bash
git clone https://github.com/Innovation-forum-Cambridgeshire/Public_Challenge
cd Public_Challenge
uv run python scripts/dev.py docs        # then http://localhost:8000
```

That serves the whole set as a searchable site on your own machine. It needs no
database and no container, so it works before you have set anything else up.

## Where the environment comes from

`.devcontainer/devcontainer.json` builds `.devcontainer/Dockerfile`, which sits
right there in this repository. Nothing is pulled from a registry, so nothing
about opening this workspace depends on having access to anything private.

That Dockerfile is **generated**, and says so in its first line. The real
definition lives in Public_Challenge and is mirrored here whenever it changes.
One definition, one place to change it — the same reason this organisation's
documents derive their figures instead of retyping them.

An edit made to the Dockerfile in this repository is lost on the next sync,
without warning. To add a tool for everyone, change it in Public_Challenge, or
open an issue here if you cannot reach that repository. To add one just for
yourself, `uv pip install` it in your workspace.

## Conventions

Each discipline folder has a README with its own starting points and house
rule. They are short, and worth the minute.
