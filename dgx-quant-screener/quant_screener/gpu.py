"""GPU stack detection for DGX Spark (RAPIDS cudf/cuml, cuOpt).

Every consumer imports through this module so the rest of the codebase is
agnostic to whether it is running on the GB10 GPU or falling back to CPU.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)

HAS_CUDF = False
HAS_CUML = False
HAS_CUOPT = False

try:  # RAPIDS dataframe
    import cudf  # noqa: F401

    HAS_CUDF = True
except Exception:  # pragma: no cover - depends on host
    cudf = None

try:  # RAPIDS ML
    import cuml  # noqa: F401

    HAS_CUML = True
except Exception:  # pragma: no cover
    cuml = None

try:  # NVIDIA cuOpt LP solver (>=25.x exposes a linear_programming API)
    from cuopt import linear_programming as cuopt_lp  # noqa: F401

    HAS_CUOPT = True
except Exception:  # pragma: no cover
    cuopt_lp = None


def backend_summary() -> str:
    return (
        f"cudf={'GPU' if HAS_CUDF else 'pandas-fallback'} "
        f"cuml={'GPU' if HAS_CUML else 'sklearn-fallback'} "
        f"cuopt={'GPU' if HAS_CUOPT else 'scipy-HiGHS-fallback'}"
    )


def get_logistic_regression(**kw):
    if HAS_CUML:
        from cuml.linear_model import LogisticRegression

        kw.pop("random_state", None)  # cuML LR has no random_state
        return LogisticRegression(**kw)
    from sklearn.linear_model import LogisticRegression

    return LogisticRegression(max_iter=2000, **kw)


def get_random_forest_classifier(**kw):
    if HAS_CUML:
        from cuml.ensemble import RandomForestClassifier

        return RandomForestClassifier(**kw)
    from sklearn.ensemble import RandomForestClassifier

    return RandomForestClassifier(n_jobs=-1, **kw)


def get_random_forest_regressor(**kw):
    if HAS_CUML:
        from cuml.ensemble import RandomForestRegressor

        return RandomForestRegressor(**kw)
    from sklearn.ensemble import RandomForestRegressor

    return RandomForestRegressor(n_jobs=-1, **kw)


def get_pca(**kw):
    if HAS_CUML:
        from cuml.decomposition import PCA

        return PCA(**kw)
    from sklearn.decomposition import PCA

    return PCA(**kw)


def get_kmeans(**kw):
    if HAS_CUML:
        from cuml.cluster import KMeans

        return KMeans(**kw)
    from sklearn.cluster import KMeans

    return KMeans(n_init=10, **kw)


def to_device(df):
    """pandas -> cudf when GPU available (no-op otherwise)."""
    if HAS_CUDF and not isinstance(df, cudf.DataFrame):
        return cudf.DataFrame.from_pandas(df)
    return df


def to_host(df):
    """cudf -> pandas (no-op for pandas)."""
    if HAS_CUDF and isinstance(df, cudf.DataFrame):
        return df.to_pandas()
    return df
