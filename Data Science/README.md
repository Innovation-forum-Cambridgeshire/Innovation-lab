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
  [document-lifecycle.ipynb](https://github.com/Innovation-forum-Cambridgeshire/Public_Challenge/blob/main/notebooks/document-lifecycle.ipynb).

## House rule

If a scenario labelled "worst" outperforms one labelled "best", the parameters
and the formula disagree about which way "worse" points. Sort the definition
before the numbers.
