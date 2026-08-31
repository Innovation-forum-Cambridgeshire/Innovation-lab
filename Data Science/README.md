# Data Science

Work that answers a question nobody has settled yet: exploration, modelling,
statistics, and the honest accounting of what a model does and does not know.

Typical output is a notebook, a model with its evaluation, or a written finding
with the uncertainty attached.

## Starting points

- `jupyterlab`, `pandas`, `numpy` and `pyarrow` are installed. Start a notebook
  with `jupyter lab`, or open an `.ipynb` directly in VS Code.
- Clear notebook outputs before committing. Stored outputs turn every run into
  a diff and leak whatever the cells printed into the repository history.
- Make notebooks assert. A notebook that only prints cannot fail, which makes
  it documentation pretending to be a check. There is a worked example at
  `notebooks/document-lifecycle.ipynb` in Public_Challenge.

## The sample application

`streamlit_app.py` predicts whether a knowledge asset is under-documented, and
is meant to be replaced by your own work rather than kept.

```bash
streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port 8501
```

Then open **http://localhost:8501**. `--server.address 0.0.0.0` is not optional
inside a container: bound to loopback the port is forwarded to nothing.

It is honest about a real limitation. `knowledge_asset` holds five rows, which
cannot train anything, so the app fits distributions to the real columns and
samples a training set from them — and says so on the page, including which
parts carry over from the real data and which do not. A model demonstrated on
invented data is fine. A model demonstrated on invented data that pretends
otherwise is not.

## House rule

If a scenario labelled "worst" outperforms one labelled "best", the parameters
and the formula disagree about which way "worse" points. Sort the definition
before the numbers.
