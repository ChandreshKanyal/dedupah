"""Simulated reviewer (development tool, needs synthetic ground truth): how much does reviewing help, and which order is best?

A finished pipeline run is loaded once. For a review ORDER (strategy), a reviewer (always right, or wrong with probability
`error_rate`) decides the first N queue items; the decisions become constraints exactly as the pipeline applies them
(same-person: a heavy edge on the best pair; different-person: a forbidden pair for every review pair of the two people), only
the clustering step is re-run (seconds, no full pipeline), and the result is scored against the truth. That gives accuracy
versus number of reviews for each strategy and noise level.

    python -m dedupe.review.simulate --work data/staging --truth data/synthetic --out docs/REVIEW_SIMULATION.md
"""
import argparse
import math
import random
from dataclasses import dataclass
from pathlib import Path

from dedupe import crypto
from dedupe.clustering import ClusterConfig, cluster
from dedupe.clustering.judged import STEWARD_WEIGHT
from dedupe.clustering.run import load_inputs
from dedupe.decision.config import DecisionConfig
from dedupe.evaluation.loaders import load_truth, record_key
from dedupe.evaluation.metrics import evaluate


@dataclass
class Item:
    cluster_l: str
    cluster_r: str
    top_probability: float
    priority: float
    size_l: int
    size_r: int
    pairs: list          # [(l_rid, r_rid, probability)] best first
    real: bool = False   # truth: some record pair of the two people is the same person


@dataclass
class Run:
    nodes: list
    edges: list
    vetoes: list
    ids: dict
    keys: dict
    items: list
    truth: dict
    household: dict
    traps: dict


def load_run(work: Path, truth_dir: Path, decision_config: Path = Path("configs/decision_default.json")) -> Run:
    con = crypto.connect()
    nodes, edges, vetoes, ids = load_inputs(con, work / "standardised.parquet", work / "candidates.parquet", work / "decisions.parquet",
                                            DecisionConfig.load(decision_config))
    keys = {rid: record_key(s, r) for rid, s, r in con.execute("SELECT rid, source_id, source_record_id FROM records").fetchall()}
    truth, hh, traps = load_truth(truth_dir)
    rows = con.execute(f"""
        SELECT q.cluster_l, q.cluster_r, q.top_probability, q.priority, q.size_l, q.size_r, p.l_rid, p.r_rid, p.probability
        FROM {crypto.rp(work / 'review_queue.parquet')} q
        JOIN {crypto.rp(work / 'clusters' / 'possible_matches.parquet')} p
          ON least(p.cluster_l, p.cluster_r) = q.cluster_l AND greatest(p.cluster_l, p.cluster_r) = q.cluster_r
        ORDER BY q.cluster_l, q.cluster_r, p.probability DESC""").fetchall()
    items: dict = {}
    for cl, cr, top, pri, sl, sr, a, b, prob in rows:
        it = items.setdefault((cl, cr), Item(cl, cr, top, pri, sl, sr, []))
        it.pairs.append((a, b, prob))
        ta, tb = truth.get(keys[a]), truth.get(keys[b])
        if ta is not None and ta == tb:
            it.real = True
    return Run(nodes, edges, vetoes, ids, keys, list(items.values()), truth, hh, traps)


# ---- review orders -------------------------------------------------------------------------------------------------
def order(run: Run, strategy: str, seed: int = 0) -> list[Item]:
    its = list(run.items)
    if strategy == "priority":          # what the pipeline's queue does today
        return sorted(its, key=lambda i: (-i.priority, i.cluster_l, i.cluster_r))
    if strategy == "probability":
        return sorted(its, key=lambda i: (-i.top_probability, i.cluster_l, i.cluster_r))
    if strategy == "uncertainty":       # most uncertain first (classic active learning)
        return sorted(its, key=lambda i: (abs(i.top_probability - 0.5), i.cluster_l, i.cluster_r))
    if strategy == "expected_gain":     # probability x number of record pairs the merge would connect
        return sorted(its, key=lambda i: (-i.top_probability * i.size_l * i.size_r, i.cluster_l, i.cluster_r))
    if strategy.startswith("blend_"):   # priority plus a damped size term: w * ln(people-records the merge would connect)
        w = float(strategy.split("_", 1)[1])
        return sorted(its, key=lambda i: (-(i.priority + w * math.log(i.size_l * i.size_r)), i.cluster_l, i.cluster_r))
    if strategy == "random":
        random.Random(seed).shuffle(its)  # nosec B311
        return its
    if strategy == "best_possible":     # not achievable: real matches first (the ceiling for any ordering)
        return sorted(its, key=lambda i: (not i.real, -i.priority, i.cluster_l, i.cluster_r))
    raise ValueError(f"unknown strategy {strategy!r}")


STRATEGIES = ("priority", "probability", "uncertainty", "expected_gain", "random", "best_possible")


# ---- reviewer + scoring --------------------------------------------------------------------------------------------
def decisions(items: list[Item], error_rate: float, seed: int) -> list[bool]:
    """True = 'same person'. The reviewer knows the truth and is wrong with probability error_rate."""
    rng = random.Random(seed)  # nosec B311
    return [(it.real != (rng.random() < error_rate)) for it in items]


def partition(run: Run, items: list[Item], said_same: list[bool]) -> dict:
    """The clustering (rid -> label) after the reviewer's decisions on `items` are applied the way the pipeline applies them."""
    must, cannot = [], list(run.vetoes)
    for it, same in zip(items, said_same, strict=True):
        if same:
            a, b, _ = it.pairs[0]
            must.append((a, b, STEWARD_WEIGHT))
        else:
            cannot.extend((a, b) for a, b, _ in it.pairs)
    return cluster(run.nodes, run.edges + must, run.ids, cannot, ClusterConfig()).labels


def score(run: Run, items: list[Item], said_same: list[bool]) -> dict:
    pred = {run.keys[rid]: lab for rid, lab in partition(run, items, said_same).items()}
    m = evaluate(pred, run.truth, run.household, run.traps)
    return {"f1": m["f1"], "precision": m["precision"], "recall": m["recall"], "impure_clusters": m["impure_clusters"], "split_persons": m["split_persons"],
            "exact_persons": exact_person_share(pred, run.truth)}


def exact_person_share(pred: dict, truth: dict) -> float:
    """Customer-level view: of the people with 2+ records, the share whose records ALL sit in one cluster that holds nobody else."""
    persons_in: dict = {}
    clusters_of: dict = {}
    for key, person in truth.items():
        lab = pred.get(key, ("__single__", key))
        persons_in.setdefault(lab, set()).add(person)
        clusters_of.setdefault(person, set()).add(lab)
    size: dict = {}
    for person in truth.values():
        size[person] = size.get(person, 0) + 1
    multi = [p for p, n in size.items() if n >= 2]
    ok = sum(1 for p in multi if len(clusters_of[p]) == 1 and len(persons_in[next(iter(clusters_of[p]))]) == 1)
    return ok / len(multi) if multi else 1.0


def simulate(run: Run, budgets: list[int], error_rates=(0.0, 0.05, 0.10), strategies=STRATEGIES, seeds: int = 3) -> dict:
    """{error_rate: {strategy: [(budget, mean metrics dict)]}}. Random and noisy settings are averaged over `seeds` seeds."""
    out: dict = {}
    for e in error_rates:
        out[e] = {}
        for strat in strategies:
            n_seeds = seeds if (strat == "random" or e > 0) else 1
            acc: dict = {b: [] for b in budgets}
            for sd in range(n_seeds):
                ordered = order(run, strat, sd)
                said = decisions(ordered, e, 1000 + sd)
                for b in budgets:
                    acc[b].append(score(run, ordered[:b], said[:b]))
            out[e][strat] = [(b, {k: sum(r[k] for r in acc[b]) / len(acc[b]) for k in acc[b][0]}) for b in budgets]
    return out


def hit_rate(run: Run, strategy: str, n: int) -> float:
    top = order(run, strategy, 0)[:n]
    return sum(i.real for i in top) / len(top) if top else 0.0


def reviews_to_reach(curve: list, base: float, ceiling: float, share: float = 0.8):
    target = base + share * (ceiling - base)
    for b, m in curve:
        if m["f1"] >= target:
            return b
    return None


def render(run: Run, res: dict, budgets: list[int]) -> str:
    n = len(run.items)
    real = sum(i.real for i in run.items)
    lines = ["# Review simulation (SYNTHETIC data)", "",
             f"Queue of {n:,} person pairs, {real:,} ({real / max(n, 1):.0%}) are the same person. A simulated reviewer decides the first N items of each "
             "order; only the clustering step is re-run. Pairwise F1 of person clusters. Noisy settings and `random` are means over 3 seeds. "
             "**A simulated reviewer is not a human: real reviewers tire, disagree and take time.**", ""]
    for e, by in res.items():
        for metric, title in (("f1", "pairwise F1"), ("exact_persons", "customers recovered exactly (people with 2+ records)")):
            lines += [f"## Reviewer wrong {e:.0%} of the time: {title} after N reviews", "",
                      "| order | " + " | ".join(f"{b:,}" for b in budgets) + " |", "|---|" + "---|" * len(budgets)]
            for strat, curve in by.items():
                lines.append(f"| {strat} | " + " | ".join(f"{m[metric]:.4f}" for _, m in curve) + " |")
            lines.append("")
    c0 = res[0.0]
    base = c0["priority"][0][1]["f1"]
    ceil = max(m["f1"] for _, m in c0["best_possible"])
    lines += ["## Efficiency (perfect reviewer)", "", f"Starting F1 {base:.4f}, ideal-reviewer ceiling {ceil:.4f}.", "",
              "| order | reviews to capture 80% of the possible gain | share of real matches among its first 100 / 500 items |", "|---|---|---|"]
    for strat in c0:
        got = reviews_to_reach(c0[strat], base, ceil)
        lines.append(f"| {strat} | {got if got is not None else 'not within the largest budget'} | {hit_rate(run, strat, 100):.0%} / {hit_rate(run, strat, 500):.0%} |")
    lines.append("")
    return "\n".join(lines)


def default_budgets(n_items: int) -> list[int]:
    steps = [0, 25, 50, 100, 200, 300, 400, 500, 600, 700, 800, 1000, 1200, 1600, 2000, 3200, 6400]
    return [b for b in steps if b < n_items] + [n_items]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--work", type=Path, default=Path("data/staging"))
    ap.add_argument("--truth", type=Path, default=Path("data/synthetic"))
    ap.add_argument("--out", type=Path, default=Path("docs/REVIEW_SIMULATION.md"))
    ap.add_argument("--seeds", type=int, default=3)
    a = ap.parse_args()
    run = load_run(a.work, a.truth)
    budgets = default_budgets(len(run.items))
    res = simulate(run, budgets, seeds=a.seeds)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    a.out.write_text(render(run, res, budgets), encoding="utf-8")
    print(f"wrote {a.out}: {len(run.items):,} queue items, budgets {budgets}")


if __name__ == "__main__":
    main()

