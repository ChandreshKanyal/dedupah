"""Learning the evidence weights when a file carries no (or too few) identifiers to learn from.

The normal route learns from pairs that a government ID proves are the same / different people. A file without PAN, Aadhaar or document numbers
has no such pairs, so nothing can be learned that way. This module is the fallback: the classic Fellegi-Sunter model fitted with EM (expectation
maximisation), as in Splink and the record-linkage literature:

  u  how often a level shows up for two RANDOM records (measured, fixed)
  m  how often a level shows up for two records of the SAME person (unknown: learned by EM)
  weight of a level = log(m / u)

EM starts from a rough guess at which candidate pairs are real matches (name sounds alike plus other agreeing fields), then alternates between
"how likely is each pair a match given the current m" and "what is m given those likelihoods" until it settles.

Two things keep it honest (agent review E1-02). The prior is the share of matches among ALL record pairs, not among the candidates: u is
measured on random pairs, so judging candidates (already chosen because their names agree) against a candidate-level prior counted the name
twice and let EM decide that nearly every candidate was a match, after which "different phone, different email" weighed nothing and
namesakes merged. Weaknesses, stated plainly:
the fields are treated as independent (name, address and phone are correlated, so confidence is somewhat too high: weights are tempered and
capped for that reason); it needs enough candidate pairs and enough plausible matches to find the pattern; and it is only as good as the
starting guess. It is tested on SYNTHETIC data only.
"""
import numpy as np

from .comparisons import DOB, comparisons

MIN_SEEDS = 20           # identifier-proven pairs needed, of EACH kind (same / different), to learn from identifiers
MIN_PAIRS = 150          # candidate pairs needed for EM to find a pattern
MIN_PLAUSIBLE = 15       # plausible matches in the starting guess
ITERATIONS = 40
SMOOTH = 0.5             # pseudo-count per level so an unseen level never gets weight -infinity
WEIGHT_CAP_BITS = 9.0    # no single comparison can contribute more than this either way
U_KEEP = 0.05            # taking the matches out of u never leaves less than this share of the measured value (the estimate is noisy)
TEMPER = 0.9             # shrinks every learned weight a little: the independence assumption makes EM over-confident (0.8 cost recall once the prior became the random-pair prior)


def plausible_matches(L: np.ndarray) -> np.ndarray:
    """A rough, hand-set first guess: names that sound alike plus some other agreeing evidence. Only the STARTING point of EM."""
    comps = comparisons()
    ix = {c.name: i for i, c in enumerate(comps)}

    def col(name):
        return L[:, ix[name]] if name in ix else np.full(len(L), -1, dtype=L.dtype)
    name = col("name").astype(int)
    agree = ((col("phone") >= 1).astype(int) + (col("email") >= 1) + np.isin(col("dob"), (4, 5))
             + (col("address") >= 3) + (col("address") == 5))
    # the project's own fields: a name-like text stands in for the name (its top level as an identical name, a close one as a near
    # name); every other field that agrees counts as one more agreeing field
    primary = "name" in ix
    for j, c in enumerate(comps):
        if c.agree_from is None:
            continue
        if c.primary:
            primary = True
            top = max(l.value for l in c.levels)
            name = np.maximum(name, np.where(L[:, j] >= top, 5, np.where(L[:, j] >= c.agree_from, 4, 0)))
        else:
            agree = agree + (L[:, j] >= c.agree_from)
    if not primary:          # nothing name-like to lean on: two agreeing fields are the starting guess
        return agree >= 2
    return ((name >= 5) & (agree >= 1)) | ((name >= 4) & (agree >= 2)) | (agree >= 3)


def readiness(L: np.ndarray, pos: int, neg: int) -> tuple[str, str]:
    """Which way the weights can be learned from this file: ('id_anchored' | 'unsupervised_em' | 'insufficient', plain-language reason)."""
    if pos >= MIN_SEEDS and neg >= MIN_SEEDS:
        return "id_anchored", f"{pos} same-person and {neg} different-people pairs proven by identifiers"
    n_plausible = int(plausible_matches(L).sum()) if len(L) else 0
    have = f"identifiers proved only {pos} same-person and {neg} different-people pairs (need {MIN_SEEDS} of each)"
    if len(L) >= MIN_PAIRS and n_plausible >= MIN_PLAUSIBLE:
        return "unsupervised_em", have + "; weights were learned from the data itself instead"
    return "insufficient", (have + f"; and the file is too small or has too little overlap to learn from itself ({len(L)} candidate pairs, "
                            f"{n_plausible} plausible matches, need {MIN_PAIRS} and {MIN_PLAUSIBLE})")


def fit_em(L: np.ndarray, u_rand: dict, fixed: dict, pairs_total: float | None = None) -> tuple[dict, float, dict]:
    """Returns (weights by comparison in natural log, bias in natural log, info). `fixed` holds the weights that are NOT learned
    (identifier levels and age-based birth-date levels), as {comparison: {level: weight}}. `pairs_total`: how many record pairs exist in
    the data (n(n-1)/2), the population u was measured on; the bias is the log-odds of a match among them (default: the candidates)."""
    n = len(L)
    total = float(pairs_total or n)
    resp = np.where(plausible_matches(L), 0.9, 0.02)
    free = {}
    for j, c in enumerate(comparisons()):
        lv = [l for l in range(c.n_levels) if l not in fixed.get(c.name, {})]
        u = np.array([u_rand[c.name][l] for l in lv])
        free[j] = (lv, u / u.sum())
    log_ratio = {j: np.zeros(c.n_levels) for j, c in enumerate(comparisons())}
    fixed_part = np.zeros(n)
    for j, c in enumerate(comparisons()):
        for l, w in fixed.get(c.name, {}).items():
            log_ratio[j][l] = w
            fixed_part[L[:, j] == l] += w
    cap = WEIGHT_CAP_BITS * np.log(2)
    lam, done = float(np.clip(resp.sum() / total, 1e-12, 0.5)), 0
    for _ in range(ITERATIONS):
        done += 1
        for j in range(len(comparisons())):
            lv, u = free[j]
            if not lv:
                continue
            in_scope = np.isin(L[:, j], lv)
            tot = resp[in_scope].sum() + SMOOTH * len(lv)
            m = np.array([(resp[L[:, j] == l].sum() + SMOOTH) / tot for l in lv])
            # u was measured on random pairs, and some random pairs ARE matches. For a near-unique value (an exact phone, a company's
            # name) those few matches are most of what a random pair ever shows at the level, so the measured u is really m seen through
            # the match rate, and the level looks far weaker than it is. Take the matches' share out: u_nonmatch = (u - lam*m) / (1 - lam).
            u = np.maximum((u - lam * m) / (1 - lam), u * U_KEEP)
            u = u / u.sum()
            for l, wl in zip(lv, np.clip(TEMPER * np.log(m / u), -cap, cap), strict=True):
                log_ratio[j][l] = wl
        lam = float(np.clip(resp.sum() / total, 1e-12, 0.5))          # matches among all pairs: matches outside the candidates are ~0
        logit = np.log(lam / (1 - lam)) + fixed_part
        for j in range(len(comparisons())):
            lv, _ = free[j]
            known = np.isin(L[:, j], lv)
            logit[known] += log_ratio[j][L[known, j]]
        new = 1.0 / (1.0 + np.exp(-np.clip(logit, -30, 30)))
        moved = float(np.abs(new - resp).max())
        resp = new
        if moved < 1e-4:
            break
    weights = {c.name: [float(log_ratio[j][l]) for l in range(c.n_levels)] for j, c in enumerate(comparisons())}
    likely = int((resp >= 0.5).sum())
    return weights, float(np.log(lam / (1 - lam))), {"iterations": done, "match_share": round(float(resp.mean()), 4) if n else 0.0,
                                                      "prior_bits": round(float(np.log2(lam / (1 - lam))), 2),
                                                      "plausible_start": int(plausible_matches(L).sum()), "likely_matches": likely}


def fixed_levels(strong_bits: dict, age_weights: dict) -> dict:
    ln2 = float(np.log(2))
    out = {name: {l: b * ln2 for l, b in bits.items()} for name, bits in strong_bits.items()}
    if age_weights:
        out["dob"] = dict(age_weights)
    return out


__all__ = ["DOB", "fit_em", "fixed_levels", "plausible_matches", "readiness"]
