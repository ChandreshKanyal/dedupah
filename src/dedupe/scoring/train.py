"""Training without hand labels, anchored on identifiers.

Seeds, per candidate pair:  strong IDs agree (PAN / Aadhaar token / passport-licence-voter number) -> same person
                            strong IDs both present and CONFLICT                                   -> different people
Identifier comparisons keep fixed, documented weights (STRONG_BITS).
Every other comparison gets logistic-regression coefficients (L2-regularised) fitted to predict the seed label from
the NON-identifier levels only. Discriminative fitting matters: look-alikes (family, twins, namesakes) share many
fields, and a logistic fit credits each field only for what it adds beyond the others, where naive per-field
frequency ratios (Fellegi-Sunter m/u) double-count correlated fields such as address + phone.
Term-frequency adjustment then scales a level by u_random(level) / tf(value) (rare agreeing value -> more, a
1-Jan birthday or a very common name -> less), limited to 16x either way.
"""
import duckdb
import numpy as np

from .comparisons import COMPARISONS, STRONG_ID
from .levels import level_matrix, materialize, prepare_tf_tables, tf_matrix
from .model import Model

ALPHA = 0.5
TF_FLOOR = 1e-6
TF_CLIP = 16.0
L2 = 0.2  # tuned on size experiments (2.0 over-shrank evidence: F1 .56 -> .74 on small data, .79 -> .88 at 1,200 households)
LN2 = float(np.log(2))

# Evidence in bits for identifier comparisons. Principle: a valid government ID is shared by two different people
# with probability ~1e-4 (data entry / OCR collisions) and differs for one person with probability ~1e-2.
STRONG_BITS = {
    "pan": {1: 13.2, 0: -6.6},
    "aadhaar": {3: 13.2, 2: 8.0, 0: -6.6},
    "gov_id": {1: 13.2, 0: -4.0},
}


def estimate_u_random(con: duckdb.DuckDBPyConnection, n_pairs: int, seed: float = 0.42) -> dict:
    n = con.execute("SELECT count(*) FROM records").fetchone()[0]
    con.execute(f"SELECT setseed({seed})")
    con.execute(f"""CREATE OR REPLACE TABLE rand_pairs AS
        SELECT DISTINCT least(a, b) AS l_rid, greatest(a, b) AS r_rid FROM
          (SELECT floor(random() * {n})::BIGINT AS a, floor(random() * {n})::BIGINT AS b FROM range({n_pairs}))
        WHERE a <> b""")
    materialize(con, "rand_pairs", "rand_levels", with_tf=False)
    L = level_matrix(con, "rand_levels")
    u = {}
    for j, c in enumerate(COMPARISONS):
        col = L[:, j]
        counts = np.array([(col == l).sum() for l in range(c.n_levels)], dtype=float)
        u[c.name] = ((counts + ALPHA) / (counts.sum() + ALPHA * c.n_levels)).tolist()
    return u


def seed_masks(L: np.ndarray):
    pos = np.zeros(len(L), dtype=bool)
    neg = np.zeros(len(L), dtype=bool)
    for j, c in enumerate(COMPARISONS):
        if c.name in STRONG_ID:
            pos |= L[:, j] >= 1
            neg |= L[:, j] == 0
    return pos & ~neg, neg & ~pos


def _design(L: np.ndarray, comps: list[int]):
    """One-hot of every level (incl. 'different'); 'not comparable' -> all zeros. Returns X and column labels."""
    cols, labels = [], []
    for j in comps:
        c = COMPARISONS[j]
        for l in range(c.n_levels):
            cols.append((L[:, j] == l).astype(np.float64))
            labels.append((j, l))
    return np.column_stack(cols), labels


def fit_logistic(X: np.ndarray, y: np.ndarray, l2: float = L2, iters: int = 40):
    n, d = X.shape
    Xb = np.hstack([X, np.ones((n, 1))])
    w = np.zeros(d + 1)
    reg = np.full(d + 1, l2)
    reg[-1] = 0.0
    for _ in range(iters):
        p = 1.0 / (1.0 + np.exp(-np.clip(Xb @ w, -30, 30)))
        g = Xb.T @ (p - y) + reg * w
        H = (Xb * (p * (1 - p))[:, None]).T @ Xb + np.diag(reg) + 1e-9 * np.eye(d + 1)
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-7:
            break
    return w[:-1], w[-1]


def adjusted_weights(weights: dict, u_rand: dict, L: np.ndarray, TF: np.ndarray) -> np.ndarray:
    """Natural-log contribution per pair and comparison, including the term-frequency adjustment."""
    W = np.zeros(L.shape, dtype=np.float64)
    for j, c in enumerate(COMPARISONS):
        tbl = np.append(np.array(weights[c.name]), 0.0)
        W[:, j] = tbl[np.where(L[:, j] < 0, c.n_levels, L[:, j])]
        has_tf = ~np.isnan(TF[:, j]) & (L[:, j] >= 1)
        if has_tf.any():
            ur = np.array(u_rand[c.name])[L[has_tf, j]]
            W[has_tf, j] += np.log(np.clip(ur / np.maximum(TF[has_tf, j], TF_FLOOR), 1 / TF_CLIP, TF_CLIP))
    return W


def train(con: duckdb.DuckDBPyConnection, random_pairs: int | None = None) -> Model:
    n = con.execute("SELECT count(*) FROM records").fetchone()[0]
    random_pairs = random_pairs or int(min(max(2_000_000, 50 * n), 6_000_000))
    prepare_tf_tables(con)
    u_rand = estimate_u_random(con, random_pairs)
    materialize(con, "candidates", "cand_levels", with_tf=True)
    L, TF = level_matrix(con, "cand_levels"), tf_matrix(con, "cand_levels")

    pos, neg = seed_masks(L)
    seeded = pos | neg
    soft = [j for j, c in enumerate(COMPARISONS) if c.name not in STRONG_ID]
    X, labels = _design(L[seeded], soft)
    y = pos[seeded].astype(np.float64)
    coef, bias = fit_logistic(X, y)

    weights = {c.name: [0.0] * c.n_levels for c in COMPARISONS}
    for (j, l), w in zip(labels, coef):
        weights[COMPARISONS[j].name][l] = float(w)
    for name, bits in STRONG_BITS.items():
        for l, b in bits.items():
            weights[name][l] = b * LN2
    # the intercept is the prior for pairs with no identifier evidence; identifier-based pairs are scored as
    # intercept + identifier weight + other evidence (the identifier is evidence beyond the seeds' own prior)
    model = Model(weights=weights, bias=float(bias), u_random=u_rand,
                  meta={"random_pairs": random_pairs, "candidate_pairs": int(len(L)),
                        "seed_pairs": {"same_person": int(pos.sum()), "different_people": int(neg.sum())},
                        "regularisation_l2": L2, "strong_id_bits": STRONG_BITS})
    return model
