# ─────────────────────────────────────────────────────────────────────────────
# GENERATED FILE — do not edit here.
#
# The definition lives in the Public_Challenge repository at
# .devcontainer/Dockerfile, and is mirrored here by its sync-lab-devcontainer
# workflow. An edit made in this file is lost on the next sync, silently.
#
# Change it there. If you cannot reach that repository, open an issue on this
# one and someone who can will carry it across.
# ─────────────────────────────────────────────────────────────────────────────

# The Innovation Lab dev container image.
#
# Published to GHCR by .github/workflows/publish-devcontainer.yml so that
# Innovation-lab -- and any future lab repository -- can reference it in five
# lines of devcontainer.json instead of copying this repository's container
# setup. A copy is what drifts; a reference is what does not.
#
# Deliberately NOT built from pyproject.toml. That file carries this project's
# document-generation dependencies (mkdocs, python-docx, mkdocs-material) which
# a data lab has no use for. The list below is the lab toolchain, stated
# explicitly so it can be reviewed on its own terms.

FROM mcr.microsoft.com/devcontainers/python:3.11-bullseye

# uv, installed the same way the devcontainer's postCreateCommand does it, so
# the lab and this repo use one package manager.
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv \
    && mv /root/.local/bin/uvx /usr/local/bin/uvx \
    && chmod 755 /usr/local/bin/uv /usr/local/bin/uvx

# Baked into the image so a lab workspace starts in seconds rather than
# resolving and downloading on every create. Kept to the things a data
# analytics / science / engineering workspace actually opens with.
ENV LAB_VENV=/opt/lab-venv
RUN uv venv "$LAB_VENV" --python 3.11 \
    && VIRTUAL_ENV="$LAB_VENV" uv pip install --python "$LAB_VENV/bin/python" \
        pandas numpy pyarrow \
        matplotlib plotly \
        jupyterlab ipykernel \
        "psycopg[binary]" redis minio \
        sqlalchemy \
        pytest ruff \
    && chmod -R a+rX "$LAB_VENV"

# Both the container's own env and any `uv run` in a lab repo resolve here,
# so nothing is rebuilt into a bind-mounted .venv the host would then fight
# over -- the failure documented in .devcontainer/README.md.
ENV UV_PROJECT_ENVIRONMENT=/opt/lab-venv
ENV PATH="/opt/lab-venv/bin:${PATH}"

# ENV PATH alone is not enough. A login shell -- which is what a VS Code
# terminal opens -- re-sources /etc/profile and rebuilds PATH from scratch,
# so `python` resolved to /usr/local/bin/python and importing pandas failed
# in exactly the place a user would first try it. A profile.d entry survives
# that.
RUN printf '%s\n' 'export PATH="/opt/lab-venv/bin:$PATH"' \
    > /etc/profile.d/10-lab-venv.sh \
    && chmod 644 /etc/profile.d/10-lab-venv.sh

USER vscode
