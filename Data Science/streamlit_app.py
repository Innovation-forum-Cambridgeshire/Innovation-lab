"""Under-documented knowledge assets: a worked scikit-learn pipeline on acme_sad.

Business question
-----------------
Given an asset's criticality, its type, how long its owner has before retirement,
and how much data hangs off it, can we predict whether it is under-documented?

Read the "Why the training data is synthetic" panel before you trust a number on
this page. The real tables hold five assets. The model is a teaching prop.
"""
from __future__ import annotations

import datetime as dt
import os

import matplotlib

# Streamlit renders figures as PNGs, and a container has no display. Choosing Agg
# before pyplot is imported avoids a backend probe that fails on headless hosts.
matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402
from matplotlib.patches import Rectangle  # noqa: E402
from sklearn.compose import ColumnTransformer  # noqa: E402
from sklearn.ensemble import (  # noqa: E402
    GradientBoostingClassifier,
    RandomForestClassifier,
)
from sklearn.impute import SimpleImputer  # noqa: E402
from sklearn.inspection import permutation_importance  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    classification_report,
    confusion_matrix,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split  # noqa: E402
from sklearn.pipeline import Pipeline  # noqa: E402
from sklearn.preprocessing import OneHotEncoder, StandardScaler  # noqa: E402

# ---------------------------------------------------------------------------
# IBCS chart settings
# ---------------------------------------------------------------------------
# One accent against one grey. Every chart on the page uses these two and nothing
# else, so a reader learns in the first chart what the blue means and carries it.
ACCENT = "#1F4E79"
GREY = "#C9CFD6"
INK = "#333333"
MUTED = "#6B7280"

NUMERIC_FEATURES = [
    "criticality_rating",
    "owner_retirement_months",
    "department_head_count",
    "critical_data_elements",
    "days_since_last_review",
]
CATEGORICAL_FEATURES = ["asset_type", "sop_status", "owner_is_steward"]

st.set_page_config(page_title="Under-documented asset model", layout="wide")


# ---------------------------------------------------------------------------
# Database access
# ---------------------------------------------------------------------------
# Everything comes from PG* environment variables. Nothing about the host or the
# credentials is written down here, so the same file runs against a local
# container and a managed instance without an edit.
PG_VARS = ["PGHOST", "PGPORT", "PGUSER", "PGDATABASE"]

PROFILE_QUERIES = {
    "assets": "SELECT criticality, documentation_coverage, asset_type FROM knowledge_asset",
    "people": "SELECT retirement_risk_date, is_steward FROM person",
    "departments": "SELECT head_count FROM department",
    "elements": "SELECT asset_id FROM critical_data_element",
    "sop": "SELECT status, last_review_date FROM sop_version",
}

# Used when the database is unreachable. These are shape assumptions, not data:
# a 1-5 criticality scale, coverage that clusters mid-range, a few asset types.
# They keep the teaching material on screen instead of an error page.
FALLBACK_PROFILE = {
    "source": "fallback",
    "criticality_mean": 3.4,
    "criticality_sd": 1.2,
    "coverage_mean": 52.0,
    "coverage_sd": 22.0,
    "asset_type_levels": ["Procedure", "Dataset", "Model", "Register", "Guidance"],
    "asset_type_weights": [0.30, 0.25, 0.15, 0.15, 0.15],
    "retirement_months_mean": 54.0,
    "retirement_months_sd": 34.0,
    "retirement_unknown_rate": 0.30,
    "head_count_mean": 24.0,
    "head_count_sd": 12.0,
    "elements_per_asset": 1.8,
    "sop_status_levels": ["Approved", "Draft", "No SOP"],
    "sop_status_weights": [0.40, 0.20, 0.40],
    "review_age_days_mean": 300.0,
    "n_real_assets": 0,
}


def env_summary() -> pd.DataFrame:
    """Show which connection variables are set, never what they contain.

    PGPASSWORD is deliberately absent from the readback. A screen-shared app
    should not put a password one hover away.
    """
    rows = [{"variable": name, "value": os.environ.get(name) or "(not set)"} for name in PG_VARS]
    rows.append(
        {
            "variable": "PGPASSWORD",
            "value": "set (hidden)" if os.environ.get("PGPASSWORD") else "(not set)",
        }
    )
    return pd.DataFrame(rows)


def _fetch_frame(connection, sql: str) -> pd.DataFrame:
    """Build a DataFrame from a psycopg cursor.

    Through the cursor rather than pandas.read_sql, which warns for any
    connection that is not a SQLAlchemy engine. This drops a dependency we
    would otherwise carry only to silence a warning.
    """
    with connection.cursor() as cursor:
        cursor.execute(sql)
        columns = [description[0] for description in cursor.description]
        return pd.DataFrame(cursor.fetchall(), columns=columns)


@st.cache_data(ttl=300, show_spinner="Reading acme_sad...")
def load_real_tables() -> tuple[dict, str, str]:
    """Read the five small tables we profile. Returns (frames, status, detail).

    Cached because Streamlit reruns this whole script on every slider move, and a
    learner dragging a hyperparameter should not open a connection per drag.
    """
    try:
        import psycopg
    except ImportError as exc:
        return {}, "unavailable", f"psycopg is not installed ({exc})."

    if not os.environ.get("PGHOST"):
        return {}, "unavailable", "PGHOST is not set, so there is nothing to connect to."

    try:
        # No connection string: libpq reads PGHOST/PGPORT/PGUSER/PGPASSWORD/
        # PGDATABASE from the environment itself, which is exactly what we want
        # -- this file then holds no host and no password, on any machine.
        #
        # psycopg (v3) closes the socket when its context manager exits, which
        # v2 did not. Streamlit reruns often enough that the difference shows.
        with psycopg.connect(connect_timeout=5) as connection:
            frames = {
                name: _fetch_frame(connection, sql) for name, sql in PROFILE_QUERIES.items()
            }
    except Exception as exc:  # noqa: BLE001 - any driver or network failure lands here
        return {}, "unavailable", f"{type(exc).__name__}: {exc}"

    return frames, "connected", f"Read {sum(len(f) for f in frames.values())} rows across 5 tables."


def _weights_from(series: pd.Series, minimum_levels: list) -> tuple[list, list]:
    """Turn an observed category column into levels and sampling weights.

    Levels seen zero times still get a small weight. With five rows a category
    can be absent by luck, and a model that has never seen a level cannot learn
    anything about it later.
    """
    counts = series.dropna().astype(str).value_counts()
    levels = list(dict.fromkeys(list(counts.index) + minimum_levels))
    raw = np.array([counts.get(level, 0) + 0.5 for level in levels], dtype=float)
    return levels, list(raw / raw.sum())


def build_profile(frames: dict) -> dict:
    """Fit simple marginal distributions to the real columns.

    Marginals only: means, spreads, category shares. Five rows cannot support a
    correlation estimate, so we do not pretend to one. What this buys us is a
    synthetic sample whose columns sit in the same range as the real ones, which
    is what makes the charts recognisable to someone who knows the data.
    """
    if not frames:
        return dict(FALLBACK_PROFILE)

    profile = dict(FALLBACK_PROFILE)
    profile["source"] = "database"

    assets = frames.get("assets", pd.DataFrame())
    if not assets.empty:
        criticality = pd.to_numeric(assets["criticality"], errors="coerce").dropna()
        coverage = pd.to_numeric(assets["documentation_coverage"], errors="coerce").dropna()
        if len(criticality):
            profile["criticality_mean"] = float(criticality.mean())
            # ddof=0 on purpose. The sample standard deviation of five rows with
            # ddof=1 swings wildly; we only need a plausible spread, not an
            # unbiased estimate of a population we will never see.
            profile["criticality_sd"] = float(max(criticality.std(ddof=0), 0.5))
        if len(coverage):
            profile["coverage_mean"] = float(coverage.mean())
            profile["coverage_sd"] = float(max(coverage.std(ddof=0), 8.0))
        levels, weights = _weights_from(assets["asset_type"], FALLBACK_PROFILE["asset_type_levels"])
        profile["asset_type_levels"] = levels
        profile["asset_type_weights"] = weights
        profile["n_real_assets"] = int(len(assets))

    people = frames.get("people", pd.DataFrame())
    if not people.empty:
        today = dt.date.today()
        dates = pd.to_datetime(people["retirement_risk_date"], errors="coerce")
        months = (dates - pd.Timestamp(today)).dt.days / 30.44
        known = months.dropna()
        if len(known):
            profile["retirement_months_mean"] = float(known.mean())
            profile["retirement_months_sd"] = float(max(known.std(ddof=0), 6.0))
        profile["retirement_unknown_rate"] = float(months.isna().mean())

    departments = frames.get("departments", pd.DataFrame())
    if not departments.empty:
        head_count = pd.to_numeric(departments["head_count"], errors="coerce").dropna()
        if len(head_count):
            profile["head_count_mean"] = float(head_count.mean())
            profile["head_count_sd"] = float(max(head_count.std(ddof=0), 3.0))

    elements = frames.get("elements", pd.DataFrame())
    n_assets = max(profile["n_real_assets"], 1)
    if not elements.empty:
        profile["elements_per_asset"] = float(len(elements) / n_assets)

    sop = frames.get("sop", pd.DataFrame())
    if not sop.empty:
        # Assets with no SOP row at all are a real category, not missing data.
        # We fold that count into the status distribution so the synthetic sample
        # carries the same "never written down" share the real tables show.
        missing_share = max(0.0, 1.0 - len(sop) / n_assets)
        levels, weights = _weights_from(sop["status"], ["Approved", "Draft"])
        # "No SOP" is appended below, so drop any observed copy of it first.
        kept = [(str(l), w) for l, w in zip(levels, weights) if str(l) != "No SOP"]
        levels = [l for l, _ in kept] + ["No SOP"]
        weights = [w * (1.0 - missing_share) for _, w in kept] + [missing_share]
        total = sum(weights)
        profile["sop_status_levels"] = levels
        profile["sop_status_weights"] = [w / total for w in weights]

        reviewed = pd.to_datetime(sop["last_review_date"], errors="coerce").dropna()
        if len(reviewed):
            ages = (pd.Timestamp(dt.date.today()) - reviewed).dt.days
            profile["review_age_days_mean"] = float(max(ages.mean(), 30.0))

    return profile


# ---------------------------------------------------------------------------
# Synthetic sample
# ---------------------------------------------------------------------------
@st.cache_data(show_spinner=False)
def make_synthetic(profile: dict, n_rows: int, noise: float, seed: int) -> pd.DataFrame:
    """Draw a training set from the fitted marginals, then label it by a rule.

    Two separate acts of invention, and they are not equally defensible:

    1. The feature columns are sampled from distributions fitted to real columns.
       Ranges and category shares carry over from acme_sad.
    2. The link between features and coverage is a rule written below by hand.
       Nothing in a five-row table could have told us that criticality drives
       documentation. We assert it so there is something for the model to find.

    So the metrics on this page measure whether the pipeline can recover a rule
    we already know. They are not evidence about council documentation.
    """
    rng = np.random.default_rng(seed)

    criticality = np.clip(
        np.round(rng.normal(profile["criticality_mean"], profile["criticality_sd"], n_rows)), 1, 5
    ).astype(int)

    retirement = rng.normal(
        profile["retirement_months_mean"], profile["retirement_months_sd"], n_rows
    ).clip(0, 240)
    # A blank retirement_risk_date is common in the real person table. Keeping the
    # gap gives the imputer something to do and the learner something to argue about.
    unknown = rng.random(n_rows) < profile["retirement_unknown_rate"]
    retirement_observed = np.where(unknown, np.nan, retirement)

    head_count = np.clip(
        np.round(rng.normal(profile["head_count_mean"], profile["head_count_sd"], n_rows)), 1, None
    ).astype(int)
    elements = rng.poisson(max(profile["elements_per_asset"], 0.1), n_rows)

    asset_type = rng.choice(
        profile["asset_type_levels"], size=n_rows, p=profile["asset_type_weights"]
    )
    sop_status = rng.choice(
        profile["sop_status_levels"], size=n_rows, p=profile["sop_status_weights"]
    )
    is_steward = rng.random(n_rows) < 0.35

    review_age = rng.exponential(profile["review_age_days_mean"], n_rows).clip(0, 2000)
    # An asset with no SOP has no review date. This blank is structural, not an
    # accident of collection, which is why the pipeline adds a missing-indicator
    # instead of quietly filling it with the median of the assets that do have one.
    review_age_observed = np.where(sop_status == "No SOP", np.nan, review_age)

    # The generating rule: what each column does to documentation coverage.
    effect = np.zeros(n_rows, dtype=float)
    effect -= 6.0 * (criticality - 3)  # high-criticality work gets written up last
    effect += 8.0 * is_steward  # stewards keep their own documentation current
    effect += np.where(sop_status == "Approved", 12.0, 0.0)
    effect -= np.where(sop_status == "No SOP", 14.0, 0.0)
    effect -= 0.012 * np.nan_to_num(review_age_observed, nan=profile["review_age_days_mean"])
    effect -= 1.5 * elements  # more data elements, more surface left undescribed
    # A near retirement date is where the risk concentrates: the knowledge is about
    # to walk, and it is exactly those assets that tend to be thinnest on paper.
    horizon = np.nan_to_num(retirement_observed, nan=profile["retirement_months_mean"])
    effect -= 12.0 * np.exp(-horizon / 24.0)
    effect += 0.05 * (head_count - profile["head_count_mean"])

    # Centre the effects before adding the base. Every term above happens to pull
    # downwards on an average asset, so adding them raw would land the sample a
    # long way under the real coverage mean and quietly contradict the claim on
    # screen that coverage is sampled around it. Subtracting the mean keeps the
    # spread the rule creates and puts the centre where the real table is.
    latent = profile["coverage_mean"] + (effect - effect.mean())

    coverage = np.clip(latent + rng.normal(0, max(noise, 1.0), n_rows), 0, 100)

    return pd.DataFrame(
        {
            "criticality_rating": criticality,
            "owner_retirement_months": np.round(retirement_observed, 1),
            "department_head_count": head_count,
            "critical_data_elements": elements,
            "days_since_last_review": np.round(review_age_observed, 0),
            "asset_type": asset_type,
            "sop_status": sop_status,
            "owner_is_steward": np.where(is_steward, "Yes", "No"),
            "documentation_coverage": np.round(coverage, 1),
        }
    )


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------
def make_onehot() -> OneHotEncoder:
    """Return a dense one-hot encoder across sklearn versions.

    The keyword was renamed from sparse to sparse_output in 1.2 and the old name
    was removed in 1.4. Trying the new one first means the file works on both
    sides of that change instead of failing on import day.
    """
    try:
        return OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    except TypeError:
        return OneHotEncoder(handle_unknown="ignore", sparse=False)


def build_pipeline(model_name: str, params: dict) -> Pipeline:
    """Preprocessing and estimator in one object, so the split cannot leak.

    Fitting the scaler outside the pipeline would fit it on rows the test set is
    about to reuse. Wrapping both means train_test_split happens first and every
    statistic the preprocessor learns comes from training rows only.
    """
    numeric = Pipeline(
        [
            # add_indicator keeps the fact that a value was missing as its own
            # column. days_since_last_review is blank precisely when there is no
            # SOP, so the blank carries signal the median would erase.
            ("impute", SimpleImputer(strategy="median", add_indicator=True)),
            # A forest ignores scale. The scaler stays so that swapping in
            # logistic regression from the sidebar later is a one-line change
            # rather than a silent accuracy drop.
            ("scale", StandardScaler()),
        ]
    )
    categorical = Pipeline(
        [
            ("impute", SimpleImputer(strategy="most_frequent")),
            ("encode", make_onehot()),
        ]
    )
    preprocess = ColumnTransformer(
        [
            ("numeric", numeric, NUMERIC_FEATURES),
            ("categorical", categorical, CATEGORICAL_FEATURES),
        ]
    )

    if model_name == "Random forest":
        estimator = RandomForestClassifier(
            n_estimators=params["n_estimators"],
            max_depth=params["max_depth"],
            min_samples_leaf=params["min_samples_leaf"],
            class_weight="balanced" if params["balance_classes"] else None,
            random_state=params["seed"],
            n_jobs=-1,
        )
    else:
        estimator = GradientBoostingClassifier(
            n_estimators=params["n_estimators"],
            max_depth=min(params["max_depth"] or 3, 5),
            min_samples_leaf=params["min_samples_leaf"],
            learning_rate=params["learning_rate"],
            random_state=params["seed"],
        )

    return Pipeline([("prep", preprocess), ("model", estimator)])


def transformed_feature_names(pipeline: Pipeline, fallback_width: int) -> list:
    """Recover post-encoding column names, with a plain fallback.

    Name plumbing through ColumnTransformer is the part of sklearn that has moved
    most between versions. A missing name should cost a nice label, not the page.
    """
    try:
        return list(pipeline.named_steps["prep"].get_feature_names_out())
    except Exception:  # noqa: BLE001
        return [f"feature_{i}" for i in range(fallback_width)]


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------
def style_axes(ax) -> None:
    """Strip everything that is not data or a label.

    No gridlines, no box, no tick marks. What is left is the bars, one baseline
    and the words, which is the whole of the IBCS argument about chart junk.
    """
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GREY)
    ax.tick_params(axis="both", length=0, colors=MUTED, labelsize=9)
    ax.set_axisbelow(True)


def finish(fig, ax, message: str, source: str) -> None:
    """Message-first title, source note at the foot."""
    ax.set_title(message, loc="left", fontsize=12, color=INK, fontweight="bold", pad=14)
    fig.text(0.01, 0.01, source, fontsize=7.5, color=MUTED, ha="left")
    fig.tight_layout(rect=(0, 0.05, 1, 1))


def render(fig) -> None:
    """Draw the figure, then close it.

    st.pyplot clears a figure but leaves it registered with pyplot. Streamlit
    reruns the script on every widget change, so without this the twentieth
    slider drag prints a max-open-figures warning over the page.
    """
    st.pyplot(fig)
    plt.close(fig)


def coverage_chart(frame: pd.DataFrame, threshold: int, source: str):
    below = frame["documentation_coverage"] < threshold
    share = float(below.mean())

    fig, ax = plt.subplots(figsize=(7, 3.4))
    bins = np.linspace(0, 100, 26)
    counts, edges = np.histogram(frame["documentation_coverage"], bins=bins)
    centres = (edges[:-1] + edges[1:]) / 2
    # Accent marks the class we are trying to predict. Everything else is grey,
    # so the eye lands on the group the model exists to find.
    colours = [ACCENT if centre < threshold else GREY for centre in centres]
    ax.bar(centres, counts, width=(edges[1] - edges[0]) * 0.9, color=colours)
    ax.axvline(threshold, color=INK, linewidth=1)
    ax.text(threshold + 1, counts.max() * 0.95, f"{threshold}% line", fontsize=8, color=INK)
    ax.set_xlabel("Documentation coverage (% of asset documented)", fontsize=9, color=MUTED)
    ax.set_ylabel("Assets (count)", fontsize=9, color=MUTED)
    style_axes(ax)
    finish(
        fig,
        ax,
        f"{share:.0%} of the sample sits below the {threshold}% documentation line",
        source,
    )
    return fig


def confusion_chart(matrix: np.ndarray, source: str):
    labels = ["Documented", "Under-documented"]
    total = matrix.sum()
    missed = int(matrix[1, 0])
    caught = int(matrix[1, 1])
    recall = caught / max(caught + missed, 1)

    fig, ax = plt.subplots(figsize=(5.6, 3.8))
    for row in range(2):
        for col in range(2):
            correct = row == col
            ax.add_patch(
                Rectangle(
                    (col, 1 - row),
                    1,
                    1,
                    facecolor=ACCENT if correct else GREY,
                    edgecolor="white",
                    linewidth=2,
                )
            )
            ax.text(
                col + 0.5,
                1.5 - row,
                f"{int(matrix[row, col])}",
                ha="center",
                va="center",
                fontsize=15,
                color="white" if correct else INK,
                fontweight="bold",
            )
    ax.set_xlim(0, 2)
    ax.set_ylim(0, 2)
    ax.set_xticks([0.5, 1.5])
    ax.set_xticklabels([f"Predicted\n{label}" for label in labels], fontsize=9)
    ax.set_yticks([1.5, 0.5])
    ax.set_yticklabels([f"Actually\n{label}" for label in labels], fontsize=9)
    ax.set_xlabel("Held-out assets (count)", fontsize=9, color=MUTED)
    for side in ("top", "right", "left", "bottom"):
        ax.spines[side].set_visible(False)
    ax.tick_params(length=0, colors=MUTED)
    finish(
        fig,
        ax,
        f"Model finds {recall:.0%} of under-documented assets; {missed} of {total} slip through",
        source,
    )
    return fig


def importance_chart(names: list, values: np.ndarray, source: str):
    order = np.argsort(values)
    names = [names[i] for i in order]
    values = values[order]
    top = names[-1] if names else "No feature"

    fig, ax = plt.subplots(figsize=(7, 0.42 * len(names) + 1.8))
    colours = [GREY] * len(values)
    if len(colours):
        colours[-1] = ACCENT  # only the leading driver earns the accent
    ax.barh(range(len(values)), values, color=colours, height=0.62)
    ax.set_yticks(range(len(values)))
    ax.set_yticklabels([name.replace("_", " ") for name in names], fontsize=9)
    for index, value in enumerate(values):
        ax.text(value + max(values.max(), 0.01) * 0.02, index, f"{value:.1f}", va="center", fontsize=8, color=INK)
    ax.set_xlabel(
        "Drop in balanced accuracy when the column is shuffled (percentage points)",
        fontsize=9,
        color=MUTED,
    )
    style_axes(ax)
    finish(fig, ax, f"{top.replace('_', ' ').capitalize()} moves the prediction most", source)
    return fig


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------
st.title("Which knowledge assets are under-documented?")
st.caption(
    "A worked scikit-learn pipeline on the acme_sad council knowledge-management schema."
)

frames, db_status, db_detail = load_real_tables()
profile = build_profile(frames)

with st.sidebar:
    st.header("Connection")
    if db_status == "connected":
        st.success(db_detail)
    else:
        st.warning("Database unreachable. Running on fallback shape assumptions.")
        st.caption(db_detail)
    with st.expander("Environment variables"):
        st.dataframe(env_summary(), hide_index=True, width='stretch')

    st.header("Training sample")
    n_rows = st.slider("Synthetic rows", 200, 5000, 1200, step=100)
    threshold = st.slider("Under-documented below (% coverage)", 20, 80, 50, step=5)
    noise = st.slider("Label noise (coverage points)", 1, 30, 10)
    st.caption("More noise means the rule is harder to recover. Watch accuracy fall.")

    st.header("Model")
    model_name = st.selectbox("Estimator", ["Random forest", "Gradient boosting"])
    n_estimators = st.slider("Trees", 10, 500, 200, step=10)
    max_depth_raw = st.slider("Max depth (0 = unlimited)", 0, 20, 6)
    min_samples_leaf = st.slider("Min samples per leaf", 1, 50, 5)
    learning_rate = st.slider("Learning rate (boosting only)", 0.01, 0.5, 0.1, step=0.01)
    balance_classes = st.checkbox("Balance classes (forest only)", value=True)
    test_size = st.slider("Test share", 0.1, 0.5, 0.25, step=0.05)
    seed = st.number_input("Random seed", min_value=0, max_value=9999, value=42, step=1)

params = {
    "n_estimators": n_estimators,
    "max_depth": max_depth_raw or None,
    "min_samples_leaf": min_samples_leaf,
    "learning_rate": learning_rate,
    "balance_classes": balance_classes,
    "seed": int(seed),
}

st.warning(
    f"**The training data on this page is synthetic.** knowledge_asset holds "
    f"{profile['n_real_assets'] or 'about 5'} rows. A train/test split of five rows is a coin "
    "flip, not a model. So the app fits simple distributions to the real columns, samples "
    f"{n_rows:,} rows from them, and labels those rows with a rule written into the code."
)

with st.expander("Why the training data is synthetic, and what that costs you"):
    st.markdown(
        """
**What carries over from the real database**

Ranges and shares. Criticality is sampled around the real mean of
`knowledge_asset.criticality`, coverage around the real mean of
`documentation_coverage`, asset types in the observed proportions, retirement
horizons from the real `person.retirement_risk_date` spread, and the share of
assets with no SOP row from `sop_version`.

**What does not carry over**

The relationship between the features and the target. Five rows cannot tell you
whether criticality drives documentation. The app asserts a rule (higher
criticality lowers coverage, an approved SOP raises it, a near retirement date
lowers it) and then measures whether the pipeline can recover it.

**So what do the metrics mean?**

They measure the pipeline, not the council. A high F1 here says the
ColumnTransformer, the split and the forest are wired correctly. It says nothing
about which real assets are under-documented. Reporting these numbers as a
finding would be a mistake.

**What would make this real**

Around a thousand real assets with recorded coverage, or an accepted proxy for
coverage measured independently of the person who owns the asset. Below that,
this stays a teaching prop and should be labelled as one.
        """
    )

sample = make_synthetic(profile, n_rows, float(noise), int(seed))
sample["under_documented"] = (sample["documentation_coverage"] < threshold).astype(int)

source_note = (
    f"Source: synthetic sample (n={n_rows:,}) drawn from acme_sad table statistics"
    f" ({'live' if db_status == 'connected' else 'fallback'} profile), {dt.date.today():%d %b %Y}."
)

tab_data, tab_model, tab_learn = st.tabs(["1. Data", "2. Model", "3. What to take away"])

with tab_data:
    left, right = st.columns([3, 2])
    with left:
        st.subheader("The distribution we are trying to split")
        render(coverage_chart(sample, threshold, source_note))
    with right:
        st.subheader("Fitted from the real tables")
        st.dataframe(
            pd.DataFrame(
                [
                    ("Criticality mean", f"{profile['criticality_mean']:.2f}"),
                    ("Criticality spread", f"{profile['criticality_sd']:.2f}"),
                    ("Coverage mean (%)", f"{profile['coverage_mean']:.1f}"),
                    ("Coverage spread (%)", f"{profile['coverage_sd']:.1f}"),
                    ("Retirement horizon mean (months)", f"{profile['retirement_months_mean']:.0f}"),
                    ("Retirement date unknown", f"{profile['retirement_unknown_rate']:.0%}"),
                    ("Department head count mean", f"{profile['head_count_mean']:.1f}"),
                    ("Data elements per asset", f"{profile['elements_per_asset']:.2f}"),
                    ("Asset types", ", ".join(map(str, profile["asset_type_levels"]))),
                    ("Profile source", profile["source"]),
                ],
                columns=["statistic", "value"],
            ),
            hide_index=True,
            width='stretch',
        )

    positives = int(sample["under_documented"].sum())
    st.metric(
        "Under-documented in the sample",
        f"{positives:,} of {len(sample):,}",
        f"{positives / len(sample):.0%} positive class",
    )
    st.dataframe(sample.head(12), width='stretch')

    if db_status == "connected" and frames:
        with st.expander("The real rows, all of them"):
            for name, frame in frames.items():
                st.markdown(f"**{name}** ({len(frame)} rows)")
                st.dataframe(frame, width='stretch', hide_index=True)

with tab_model:
    features = NUMERIC_FEATURES + CATEGORICAL_FEATURES
    X = sample[features]
    y = sample["under_documented"]

    if y.nunique() < 2:
        st.error(
            "Every row landed in one class. Move the coverage threshold so both classes "
            "exist, or a classifier has nothing to separate."
        )
    else:
        # Stratify so the test set holds the same class mix as the training set. On an
        # unbalanced target an unstratified split can hand you a test fold with almost
        # no positives, and the score that follows is noise.
        X_train, X_test, y_train, y_test = train_test_split(
            X, y, test_size=test_size, random_state=int(seed), stratify=y
        )

        pipeline = build_pipeline(model_name, params)
        pipeline.fit(X_train, y_train)
        predictions = pipeline.predict(X_test)
        probabilities = pipeline.predict_proba(X_test)[:, 1]

        report = classification_report(
            y_test,
            predictions,
            target_names=["Documented", "Under-documented"],
            output_dict=True,
            zero_division=0,
        )
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Accuracy", f"{report['accuracy']:.1%}")
        col_b.metric("Recall on under-documented", f"{report['Under-documented']['recall']:.1%}")
        col_c.metric("ROC AUC", f"{roc_auc_score(y_test, probabilities):.3f}")

        chart_left, chart_right = st.columns(2)
        with chart_left:
            render(confusion_chart(confusion_matrix(y_test, predictions), source_note))
            st.caption(
                "Recall matters more than accuracy here. Missing an under-documented "
                "critical asset costs more than sending someone to check a documented one."
            )
        with chart_right:
            st.subheader("Classification report")
            st.dataframe(
                pd.DataFrame(report).transpose().round(3), width='stretch'
            )

        st.subheader("What the model is actually using")
        # Permutation importance on the held-out rows, not the forest's built-in
        # impurity importance. Impurity importance inflates high-cardinality and
        # continuous columns, so a learner reading it would over-credit
        # days_since_last_review purely for having many distinct values.
        importance = permutation_importance(
            pipeline,
            X_test,
            y_test,
            n_repeats=10,
            random_state=int(seed),
            scoring="balanced_accuracy",
            n_jobs=-1,
        )
        render(importance_chart(features, importance.importances_mean * 100, source_note))

        with st.expander("Compare against the model's built-in importance"):
            try:
                names = transformed_feature_names(
                    pipeline, pipeline.named_steps["model"].n_features_in_
                )
                built_in = pd.DataFrame(
                    {
                        "encoded feature": names,
                        "impurity importance": pipeline.named_steps["model"].feature_importances_,
                    }
                ).sort_values("impurity importance", ascending=False)
                st.dataframe(built_in.head(15), hide_index=True, width='stretch')
                st.caption(
                    "These are one-hot columns, so a five-level asset_type gets split "
                    "across five rows and looks weaker than it is. That mismatch in "
                    "granularity is one reason the two rankings disagree."
                )
            except Exception as exc:  # noqa: BLE001
                st.info(f"Built-in importances unavailable on this sklearn build: {exc}")

with tab_learn:
    st.markdown(
        """
### Five things this page is meant to show

**1. A pipeline is one object, not a sequence of steps you remember to repeat.**
`ColumnTransformer` inside `Pipeline` means the imputer and the scaler learn their
statistics from training rows only. Fit them by hand before the split and the test
score flatters you.

**2. Missing is sometimes information.** `days_since_last_review` is blank exactly
when there is no SOP. `SimpleImputer(add_indicator=True)` fills the value and keeps
a flag saying it was filled, so the model can use the blank itself.

**3. Categories need an unknown door.** `OneHotEncoder(handle_unknown="ignore")`
means a new asset type at prediction time gets all-zeros instead of an exception.

**4. Importance depends on how you measure it.** Permutation importance on held-out
rows answers "what does the model rely on to be right here". Impurity importance
answers "where did the trees split", and rewards columns with many distinct values.
They will not agree.

**5. Small data has no clever workaround.** Everything above runs on invented rows.
The honest output of a five-row table is a description of five rows.

### Things to try in the sidebar

- Push **label noise** to 30. Accuracy falls towards the base rate, because the rule
  is buried. This is what an unlearnable problem looks like.
- Set **min samples per leaf** to 1 and **max depth** to unlimited. Training fit gets
  better, held-out accuracy usually does not.
- Move the **coverage threshold** to 20. The positive class shrinks, accuracy climbs,
  and recall collapses. That is why accuracy alone is a poor target on rare events.
- Turn off **balance classes** at a low threshold and watch the same thing.
        """
    )
    st.info(
        "To point this at real data, replace make_synthetic with a query that returns "
        "one row per asset and the same column names. Nothing else on the page changes."
    )

st.caption(source_note)
