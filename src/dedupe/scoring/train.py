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

from .comparisons import DOB, comparisons, spec
from .levels import level_counts, level_matrix, materialize, prepare_tf_tables, tf_matrix
from .model import Model
from .unsupervised import fit_em, fixed_levels, readiness

ALPHA = 0.5
TF_FLOOR = 1e-6
TF_CLIP = 16.0
# Age-based DOB levels only occur for records without a birth date (leads), which never carry a PAN/Aadhaar seed, so they cannot be
# learned from the seeded pairs. Their weight is log(m/u): u is measured on random pairs (among pairs where age is the only birth
# information, i.e. compatible vs incompatible), m is an ASSUMPTION about how reliable a stated age is (it is right about 93% of the
# time for the same person, with two years of slack).
AGE_M = {"age-based: compatible": 0.93, "age-based: incompatible": 0.07}   # by label: survives renumbering of the DOB ladder
U_FLOOR = 1e-4
L2 = 0.2  # tuned on size experiments (2.0 over-shrank evidence: F1 .56 -> .74 on small data, .79 -> .88 at 1,200 households)
LN2 = float(np.log(2))



def estimate_u_random(con: duckdb.DuckDBPyConnection, n_pairs: int, seed: float = 0.42, chunk: int = 250_000) -> dict:
    """Level frequencies among random record pairs, accumulated chunk by chunk (flat memory)."""
    n = con.execute("SELECT count(*) FROM records").fetchone()[0]
    con.execute(f"SELECT setseed({seed})")
    totals = {c.name: np.zeros(c.n_levels + 1) for c in comparisons()}
    for _ in range(max(1, -(-n_pairs // chunk))):
        con.execute(f"""CREATE OR REPLACE TABLE rand_pairs AS
            SELECT DISTINCT least(a, b) AS l_rid, greatest(a, b) AS r_rid FROM
              (SELECT floor(random() * {n})::BIGINT AS a, floor(random() * {n})::BIGINT AS b FROM range({chunk}))
            WHERE a <> b""")
        for name, cnt in level_counts(con, "rand_pairs").items():
            totals[name] += cnt
    u = {}
    for c in comparisons():
        counts = totals[c.name][: c.n_levels]  # drop the 'not comparable' slot
        u[c.name] = ((counts + ALPHA) / (counts.sum() + ALPHA * c.n_levels)).tolist()
    return u


def seed_masks(L: np.ndarray):
    pos = np.zeros(len(L), dtype=bool)
    neg = np.zeros(len(L), dtype=bool)
    for j, c in enumerate(comparisons()):
        if c.name in spec().strong_id:
            pos |= L[:, j] >= 1
            neg |= L[:, j] == 0
    return pos & ~neg, neg & ~pos


def _design(L: np.ndarray, comps: list[int]):
    """One-hot of every level (incl. 'different'); 'not comparable' -> all zeros. Returns X and column labels."""
    cols, labels = [], []
    for j in comps:
        c = comparisons()[j]
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


def tf_terms(u_rand: dict, L: np.ndarray, TF: np.ndarray) -> np.ndarray:
    """Natural-log term-frequency adjustment per pair and comparison (0 where a level has none): log(u_random(level) / tf(value)), clipped."""
    A = np.zeros(L.shape, dtype=np.float64)
    for j, c in enumerate(comparisons()):
        if c.name in spec().fixed_bits:          # a weight set by hand stays exactly what the project wrote
            continue
        has_tf = ~np.isnan(TF[:, j]) & (L[:, j] >= 1)
        if has_tf.any():
            ur = np.array(u_rand[c.name])[L[has_tf, j]]
            A[has_tf, j] = np.log(np.clip(ur / np.maximum(TF[has_tf, j], TF_FLOOR), 1 / TF_CLIP, TF_CLIP))
    return A


def tf_centres(A: np.ndarray, L: np.ndarray, rows: np.ndarray) -> dict:
    """The average adjustment per comparison and level over `rows` (the pairs the weights were fitted on). A logistic weight already holds
    the average evidence of its level, so only the difference from that average may be added: otherwise the adjustment shifts the learned
    weight itself (agent review E1-03: an exact phone went from +2.96 to -0.32 bits and exact duplicates without a PAN went to review)."""
    out = {}
    for j, c in enumerate(comparisons()):
        for lv in range(1, c.n_levels):
            at = (L[:, j] == lv) & (A[:, j] != 0)
            pick = at & rows if (at & rows).sum() >= 5 else at
            if pick.any():
                out.setdefault(c.name, {})[str(lv)] = float(A[pick, j].mean())
    return out


def adjusted_weights(weights: dict, u_rand: dict, L: np.ndarray, TF: np.ndarray, centres: dict | None = None) -> np.ndarray:
    """Natural-log contribution per pair and comparison, including the term-frequency adjustment (minus its average for the level when the
    model was fitted discriminatively, see tf_centres)."""
    W = np.zeros(L.shape, dtype=np.float64)
    A = tf_terms(u_rand, L, TF)
    for j, c in enumerate(comparisons()):
        tbl = np.append(np.array(weights[c.name]), 0.0)
        W[:, j] = tbl[np.where(L[:, j] < 0, c.n_levels, L[:, j])]
        centre = (centres or {}).get(c.name)
        if centre:
            shift = np.array([centre.get(str(lv), 0.0) for lv in range(c.n_levels)] + [0.0])
            A[:, j] = np.where(A[:, j] != 0, A[:, j] - shift[np.where(L[:, j] < 0, c.n_levels, L[:, j])], 0.0)
    return W + A


def train(con: duckdb.DuckDBPyConnection, random_pairs: int | None = None) -> Model:
    n = con.execute("SELECT count(*) FROM records").fetchone()[0]
    random_pairs = random_pairs or int(min(max(2_000_000, 50 * n), 6_000_000))
    prepare_tf_tables(con)
    u_rand = estimate_u_random(con, random_pairs)
    materialize(con, "candidates", "cand_levels", with_tf=True)
    L = level_matrix(con, "cand_levels")

    pos, neg = seed_masks(L)
    mode, mode_reason = readiness(L, int(pos.sum()), int(neg.sum()))
    sp = spec()
    age_w = {}
    if DOB in sp.comparisons:
        age_levels = {lvl: AGE_M[label] for lvl, label in DOB.labels().items() if label in AGE_M}
        if len(age_levels) != len(AGE_M):
            raise ValueError(f"AGE_M labels {sorted(AGE_M)} must all exist on the DOB comparison")
        age_total = sum(u_rand["dob"][lvl] for lvl in age_levels)
        age_w = {lvl: float(np.log(m / max(u_rand["dob"][lvl] / age_total, U_FLOOR))) for lvl, m in age_levels.items()}
    # never learned: identifiers, age-based birth dates, and whatever the project set by hand (which wins over the other two)
    fixed = fixed_levels(sp.strong_bits, age_w)
    for name, bits in sp.fixed_bits.items():
        fixed[name] = {**fixed.get(name, {}), **{lv: b * LN2 for lv, b in bits.items()}}
    info, centres = {}, {}
    if mode == "unsupervised_em":
        weights, bias, info = fit_em(L, u_rand, fixed, pairs_total=n * (n - 1) / 2)
        if info["likely_matches"] > 0.6 * len(L):          # a file of near-copies, or EM found a pattern that is not "same person": say so
            mode_reason += (f"; note: {info['likely_matches']} of {len(L)} candidate pairs look like the same person, which is unusually many, "
                            "so check a sample of the merges")
    else:
        weights = {c.name: [0.0] * c.n_levels for c in comparisons()}
        bias = 0.0
        if mode == "id_anchored":
            seeded = pos | neg
            soft = [j for j, c in enumerate(comparisons()) if c.name not in sp.strong_id]
            X, labels = _design(L[seeded], soft)
            y = pos[seeded].astype(np.float64)
            coef, bias = fit_logistic(X, y)
            for (j, l), w in zip(labels, coef, strict=True):
                weights[comparisons()[j].name][l] = float(w)
            centres = tf_centres(tf_terms(u_rand, L, tf_matrix(con, "cand_levels")), L, seeded)
        for name, levels in fixed.items():
            for l, w in levels.items():
                weights[name][l] = w
    # the intercept is the prior for pairs with no identifier evidence; identifier-based pairs are scored as
    # intercept + identifier weight + other evidence (the identifier is evidence beyond the seeds' own prior)
    model = Model(weights=weights, bias=float(bias), u_random=u_rand,
                  meta={"random_pairs": random_pairs, "candidate_pairs": int(len(L)),
                        "seed_pairs": {"same_person": int(pos.sum()), "different_people": int(neg.sum())},
                        "scoring_mode": mode, "scoring_mode_reason": mode_reason, "em": info,
                        "regularisation_l2": L2, "strong_id_bits": sp.strong_bits, "fixed_bits": sp.fixed_bits, "tf_centres": centres})
    return model
