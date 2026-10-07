"""Learning the evidence weights when a file carries no (or too few) identifiers to learn from.

The normal route learns from pairs that a government ID proves are the same / different people. A file without PAN, Aadhaar or document numbers
has no such pairs, so nothing can be learned that way. This module is the fallback: the classic Fellegi-Sunter model fitted with EM (expectation
maximisation), as in Splink and the record-linkage literature:

  u  how often a level shows up for two RANDOM records (measured, fixed)
  m  how often a level shows up for two records of the SAME person (unknown: learned by EM)
  weight of a level = log(m / u)

EM starts from a rough guess at which candidate pairs are real matches (name sounds alike plus other agreeing fields), then alternates between
"how likely is each pair a match given the current m" and "what is m given those likelihoods" until it settles. Weaknesses, stated plainly:
the fields are treated as independent (name, address and phone are correlated, so confidence is somewhat too high: weights are tempered and
capped for that reason); it needs enough candidate pairs and enough plausible matches to find the pattern; and it is only as good as the
starting guess. It is tested on SYNTHETIC data only.
"""
import numpy as np

from .comparisons import COMPARISONS, DOB, STRONG_ID

MIN_SEEDS = 20           # identifier-proven pairs needed, of EACH kind (same / different), to learn from identifiers
MIN_PAIRS = 150          # candidate pairs needed for EM to find a pattern
MIN_PLAUSIBLE = 15       # plausible matches in the starting guess
ITERATIONS = 40
SMOOTH = 0.5             # pseudo-count per level so an unseen level never gets weight -infinity
WEIGHT_CAP_BITS = 9.0    # no single comparison can contribute more than this either way
TEMPER = 0.8             # shrinks every learned weight: the independence assumption makes EM over-confident
IX = {c.name: i for i, c in enumerate(COMPARISONS)}


def plausible_matches(L: np.ndarray) -> np.ndarray:
    """A rough, hand-set first guess: names that sound alike plus some other agreeing evidence. Only the STARTING point of EM."""
    name = L[:, IX["name"]]
    agree = ((L[:, IX["phone"]] >= 1).astype(int) + (L[:, IX["email"]] >= 1) + np.isin(L[:, IX["dob"]], (4, 5))
             + (L[:, IX["address"]] >= 3) + (L[:, IX["address"]] == 5))
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


def fit_em(L: np.ndarray, u_rand: dict, fixed: dict) -> tuple[dict, float, dict]:
    """Returns (weights by comparison in natural log, bias in natural log, info). `fixed` holds the weights that are NOT learned
    (identifier levels and age-based birth-date levels), as {comparison: {level: weight}}."""
    n = len(L)
    resp = np.where(plausible_matches(L), 0.9, 0.02)
    free = {}
    for j, c in enumerate(COMPARISONS):
        lv = [l for l in range(c.n_levels) if l not in fixed.get(c.name, {})]
        u = np.array([u_rand[c.name][l] for l in lv])
        free[j] = (lv, u / u.sum())
    log_ratio = {j: np.zeros(c.n_levels) for j, c in enumerate(COMPARISONS)}
    for j, c in enumerate(COMPARISONS):
        for l, w in fixed.get(c.name, {}).items():
            log_ratio[j][l] = w
    fixed_part = np.zeros(n)
    for j, c in enumerate(COMPARISONS):
        for l, w in fixed.get(c.name, {}).items():
            fixed_part[L[:, j] == l] += w
    lam = float(resp.mean())
    done = 0
    for _ in range(ITERATIONS):
        done += 1
        for j in range(len(COMPARISONS)):
            lv, u = free[j]
            if not lv:
                continue
            in_scope = np.isin(L[:, j], lv)
            tot = resp[in_scope].sum() + SMOOTH * len(lv)
            m = np.array([(resp[L[:, j] == l].sum() + SMOOTH) / tot for l in lv])
            w = np.clip(TEMPER * np.log(m / u), -WEIGHT_CAP_BITS * np.log(2), WEIGHT_CAP_BITS * np.log(2))
            for l, wl in zip(lv, w, strict=True):
                log_ratio[j][l] = wl
        lam = float(np.clip(resp.mean(), 1e-3, 0.5))
        logit = np.log(lam / (1 - lam)) + fixed_part
        for j in range(len(COMPARISONS)):
            lv, _ = free[j]
            known = np.isin(L[:, j], lv)
            logit[known] += log_ratio[j][L[known, j]]
        new = 1.0 / (1.0 + np.exp(-np.clip(logit, -30, 30)))
        moved = float(np.abs(new - resp).max())
        resp = new
        if moved < 1e-4:
            break
    weights = {}
    for j, c in enumerate(COMPARISONS):
        weights[c.name] = [float(log_ratio[j][l]) for l in range(c.n_levels)]
    return weights, float(np.log(lam / (1 - lam))), {"iterations": done, "match_share": round(lam, 4), "plausible_start": int(plausible_matches(L).sum()),
                                                      "likely_matches": int((resp >= 0.5).sum())}


def fixed_levels(strong_bits: dict, age_weights: dict) -> dict:
    ln2 = float(np.log(2))
    out = {name: {l: b * ln2 for l, b in bits.items()} for name, bits in strong_bits.items() if name in STRONG_ID}
    out["dob"] = dict(age_weights)
    return out


__all__ = ["DOB", "fit_em", "fixed_levels", "plausible_matches", "readiness"]
