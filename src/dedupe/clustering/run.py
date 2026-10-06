"""Decisions -> clusters (one per person). Writes clusters, cluster stats and 'possible matches' between clusters.

    python -m dedupe.clustering --data data/synthetic
"""
import argparse
import json
from pathlib import Path

import pyarrow as pa

from dedupe import crypto
from dedupe.decision.config import DecisionConfig
from dedupe.evaluation.loaders import load_truth, record_key, write_predictions
from dedupe.evaluation.metrics import evaluate, render
from dedupe.identity import assign_person_ids

from .graph import ClusterConfig, cluster


def load_inputs(con, std: Path, cand: Path, decisions: Path, decision_cfg: DecisionConfig):
    index = cand.with_suffix(".index.parquet")
    con.execute(f"""CREATE OR REPLACE TABLE records AS SELECT i.rid, s.* FROM {crypto.rp(std)} s
                    JOIN {crypto.rp(index)} i USING (source_id, source_record_id)""")
    con.execute(f"CREATE OR REPLACE TABLE decisions AS SELECT * FROM {crypto.rp(decisions)}")
    veto_ids = [r.id for r in decision_cfg.rules if r.type == "never_merge" and r.enabled]
    nodes = [r[0] for r in con.execute("SELECT rid FROM records ORDER BY rid").fetchall()]
    edges = con.execute("SELECT l_rid, r_rid, probability FROM decisions WHERE decision = 'auto_merge'").fetchall()
    cannot = []
    if veto_ids:
        lst = ", ".join(f"'{v}'" for v in veto_ids)
        cannot = con.execute(f"SELECT l_rid, r_rid FROM decisions WHERE decided_by IN ({lst})").fetchall()
    ids = {rid: {"pan": pan, "aadhaar": aad} for rid, pan, aad in
           con.execute("SELECT rid, pan, aadhaar_token FROM records WHERE pan IS NOT NULL OR aadhaar_token IS NOT NULL").fetchall()}
    return nodes, edges, cannot, ids


def write_outputs(con, res, out_dir: Path, registry_dir: Path | None = None, scored: Path | None = None) -> dict | None:
    """Writes clusters etc. With `registry_dir`, group labels are replaced by stable person IDs (see dedupe.identity) and the
    id summary is returned; without it the per-run labels are kept (stand-alone / test use). With `scored`, possible_matches also
    carries each review pair's per-field evidence (lv_*, w_*, match_weight) so a review screen never has to scan all scored pairs."""
    out_dir.mkdir(parents=True, exist_ok=True)
    rids = list(res.labels)
    con.register("cluster_map_src", pa.table({"rid": pa.array(rids, pa.int64()),
                                              "cluster_id": [f"cl{res.labels[r]:08d}" for r in rids]}))
    con.execute("CREATE OR REPLACE TABLE cluster_map AS SELECT * FROM cluster_map_src")
    con.register("cluster_flags_src", pa.table({"cluster_id": pa.array([f"cl{lab:08d}" for lab in res.flags], pa.string()),
                                                "flags": pa.array([",".join(f) for f in res.flags.values()], pa.string())}))
    con.execute("CREATE OR REPLACE TABLE cluster_flags AS SELECT * FROM cluster_flags_src")
    ident = assign_person_ids(con, registry_dir) if registry_dir else None
    con.execute(f"""COPY (SELECT r.rid, r.source_id, r.source_record_id, m.cluster_id FROM records r JOIN cluster_map m USING (rid) ORDER BY rid)
                    TO '{out_dir / 'clusters.parquet'}' ({crypto.fmt()})""")
    con.execute(f"""COPY (SELECT m.cluster_id, count(*) AS size, count(DISTINCT r.source_id) AS sources, f.flags
                          FROM cluster_map m JOIN records r USING (rid) LEFT JOIN cluster_flags f USING (cluster_id)
                          GROUP BY m.cluster_id, f.flags ORDER BY size DESC) TO '{out_dir / 'cluster_stats.parquet'}' ({crypto.fmt()})""")
    ev_join = (f"JOIN {crypto.rp(scored)} s ON s.l_rid = d.l_rid AND s.r_rid = d.r_rid" if scored else "")
    ev_cols = ", s.match_weight, s.* EXCLUDE (l_rid, r_rid, rules, probability, match_weight)" if scored else ""
    con.execute(f"""COPY (SELECT a.cluster_id AS cluster_l, b.cluster_id AS cluster_r, d.l_rid, d.r_rid, d.probability, d.decided_by, d.reason,
                                 rl.source_id AS l_source_id, rl.source_record_id AS l_source_record_id,
                                 rr.source_id AS r_source_id, rr.source_record_id AS r_source_record_id{ev_cols}
                          FROM decisions d JOIN cluster_map a ON a.rid = d.l_rid JOIN cluster_map b ON b.rid = d.r_rid
                          JOIN records rl ON rl.rid = d.l_rid JOIN records rr ON rr.rid = d.r_rid {ev_join}
                          WHERE d.decision = 'review' AND a.cluster_id <> b.cluster_id
                          ORDER BY least(a.cluster_id, b.cluster_id), greatest(a.cluster_id, b.cluster_id), d.probability DESC)
                    TO '{out_dir / 'possible_matches.parquet'}' ({crypto.fmt()})""")
    (out_dir / "removed_edges.json").write_text(json.dumps(
        [{"l_rid": a, "r_rid": b, "probability": p, "reason": why} for a, b, p, why in res.removed_edges], indent=1), encoding="utf-8")
    return ident


def predictions(con, res) -> dict:
    keys = {rid: record_key(s, i) for rid, s, i in con.execute("SELECT rid, source_id, source_record_id FROM records").fetchall()}
    return {keys[rid]: f"cl{lab:08d}" for rid, lab in res.labels.items()}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--std", type=Path, default=Path("data/staging/standardised.parquet"))
    ap.add_argument("--cand", type=Path, default=Path("data/staging/candidates.parquet"))
    ap.add_argument("--decisions", type=Path, default=Path("data/staging/decisions.parquet"))
    ap.add_argument("--decision-config", type=Path, default=Path("configs/decision_default.json"))
    ap.add_argument("--out", type=Path, default=Path("data/staging/clusters"))
    ap.add_argument("--data", type=Path, default=Path("data/synthetic"), help="ground truth for evaluation (dev only)")
    ap.add_argument("--max-size", type=int, default=15)
    a = ap.parse_args()

    con = crypto.connect()
    nodes, edges, cannot, ids = load_inputs(con, a.std, a.cand, a.decisions, DecisionConfig.load(a.decision_config))
    cfg = ClusterConfig(max_cluster_size=a.max_size)
    guarded = cluster(nodes, edges, ids, cannot, cfg, guard=True)
    plain = cluster(nodes, edges, ids, cannot, cfg, guard=False)
    write_outputs(con, guarded, a.out)

    truth, hh, traps = load_truth(a.data)
    rep_g = evaluate(predictions(con, guarded), truth, hh, traps)
    rep_p = evaluate(predictions(con, plain), truth, hh, traps)
    write_predictions(Path("data/runs/clusters_guarded.csv"), predictions(con, guarded))
    (a.out / "report.json").write_text(json.dumps({"guarded": {"stats": guarded.stats, "eval": rep_g},
                                                   "plain_components": {"stats": plain.stats, "eval": rep_p}}, indent=2), encoding="utf-8")
    # dev-only ceiling: if stewards approved exactly the real matches in the review queue
    keys = {rid: record_key(src, i) for rid, src, i in con.execute("SELECT rid, source_id, source_record_id FROM records").fetchall()}
    review = con.execute("SELECT l_rid, r_rid, probability FROM decisions WHERE decision = 'review'").fetchall()
    approved = [(l, r, p) for l, r, p in review if truth.get(keys[l]) is not None and truth.get(keys[l]) == truth.get(keys[r])]
    rep_o = evaluate(predictions(con, cluster(nodes, edges + approved, ids, cannot, cfg, guard=True)), truth, hh, traps)
    s = guarded.stats
    print(f"clusters {s['clusters']:,} ({s['singletons']:,} singletons) | largest {s['largest_cluster']} | oversize flagged {s['oversize_clusters']} | "
          f"contradictory groups {s['contradictory_components']} -> {s['edges_removed']} edges removed, {s['unresolved_components']} unresolved")
    print(render(rep_g, "GUARDED clustering"))
    print(f"\nIDEAL-STEWARD ceiling (dev only: every real match in the review queue approved, {len(approved):,} of {len(review):,} review pairs): "
          f"precision {rep_o['precision']:.4f} recall {rep_o['recall']:.4f} F1 {rep_o['f1']:.4f}")
    print(f"without contradiction checks (plain components): precision {rep_p['precision']:.4f} recall {rep_p['recall']:.4f} F1 {rep_p['f1']:.4f}, "
          f"largest {plain.stats['largest_cluster']}, impure clusters {rep_p['impure_clusters']}")


if __name__ == "__main__":
    main()
