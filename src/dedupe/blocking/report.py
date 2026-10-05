"""Blocking quality against ground truth: pairs completeness, reduction ratio, per-rule value and misses."""
from pathlib import Path

import duckdb


def blocking_report(con: duckdb.DuckDBPyConnection, data_dir: Path, gen: dict) -> dict:
    con.execute(f"""CREATE OR REPLACE TABLE truth AS
        SELECT r.rid, t.person_id FROM records r
        JOIN read_csv_auto('{data_dir / 'ground_truth' / 'record_truth.csv'}', all_varchar=true) t
          ON t.source = r.source_id AND t.source_record_id = r.source_record_id WHERE t.role <> 'Co-applicant'""")
    true_total = con.execute("SELECT coalesce(sum(n*(n-1)/2), 0) FROM (SELECT count(*) n FROM truth GROUP BY person_id)").fetchone()[0]
    con.execute("""CREATE OR REPLACE TABLE pair_flags AS
        SELECT p.l, p.r, list(DISTINCT p.rule) AS rules, (a.person_id = b.person_id) AS is_true
        FROM rule_pairs p JOIN truth a ON a.rid = p.l JOIN truth b ON b.rid = p.r GROUP BY p.l, p.r, a.person_id, b.person_id""")
    found, cand = con.execute("SELECT count(*) FILTER (is_true), count(*) FROM pair_flags").fetchone()
    per_rule = {}
    for rule, pairs, tp, uniq in con.execute("""
        SELECT x.rule, count(*), count(*) FILTER (x.is_true),
               count(*) FILTER (x.is_true AND len(x.rules) = 1)
        FROM (SELECT unnest(rules) AS rule, is_true, rules FROM pair_flags) x GROUP BY 1""").fetchall():
        per_rule[rule] = {"pairs": pairs, "true_pairs": tp, "unique_true_pairs": uniq,
                          "true_share": tp / pairs if pairs else 0, **gen["rules"].get(rule, {})}
    missed = con.execute("""
        WITH tp AS (SELECT a.rid l, b.rid r FROM truth a JOIN truth b ON a.person_id = b.person_id AND a.rid < b.rid)
        SELECT least(ra.source_id, rb.source_id) || ' + ' || greatest(ra.source_id, rb.source_id) AS pair_type, count(*)
        FROM tp LEFT JOIN pair_flags f ON f.l = tp.l AND f.r = tp.r
        JOIN records ra ON ra.rid = tp.l JOIN records rb ON rb.rid = tp.r
        WHERE f.l IS NULL GROUP BY 1 ORDER BY 2 DESC LIMIT 6""").fetchall()
    # transitive completeness: with perfect scoring, which true pairs would still end up together via other records?
    parent: dict = {}

    def find(x):
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for l, r in con.execute("SELECT l, r FROM pair_flags WHERE is_true").fetchall():
        parent[find(l)] = find(r)
    comp = con.execute("SELECT rid, person_id FROM truth").fetchall()
    per_person: dict = {}
    for rid, person in comp:
        per_person.setdefault(person, []).append(find(rid))
    reachable = 0
    for roots in per_person.values():
        counts: dict = {}
        for rt in roots:
            counts[rt] = counts.get(rt, 0) + 1
        reachable += sum(n * (n - 1) // 2 for n in counts.values())
    return {"records": gen["records"], "all_pairs": gen["all_pairs"], "candidate_pairs": cand,
            "reduction_ratio": 1 - cand / gen["all_pairs"], "true_pairs_total": int(true_total), "true_pairs_found": found,
            "pairs_completeness": found / true_total if true_total else 1.0,
            "transitive_completeness": reachable / true_total if true_total else 1.0, "false_candidate_pairs": cand - found,
            "per_rule": per_rule, "missed_by_source_pair": missed, "cap": gen["cap"]}


def render(r: dict) -> str:
    lines = [f"records {r['records']:,} | all possible pairs {r['all_pairs']:,} | candidate pairs {r['candidate_pairs']:,} "
             f"(reduction {r['reduction_ratio']:.4%}) | frequency cap {r['cap']}",
             f"true duplicate pairs {r['true_pairs_total']:,} | found {r['true_pairs_found']:,} -> pairs completeness {r['pairs_completeness']:.2%} | reachable via other records (transitive) {r['transitive_completeness']:.2%}",
             f"{'rule':<27}{'pairs':>9}{'true':>8}{'true%':>7}{'only-this-rule':>16}{'capped blocks':>15}"]
    for name, s in sorted(r["per_rule"].items(), key=lambda kv: -kv[1]["true_pairs"]):
        lines.append(f"{name:<27}{s['pairs']:>9,}{s['true_pairs']:>8,}{s['true_share']:>7.1%}"
                     f"{s['unique_true_pairs']:>16,}{s.get('capped_blocks', 0):>15,}")
    lines.append("missed true pairs by source pair: " + "; ".join(f"{k} {v:,}" for k, v in r["missed_by_source_pair"]))
    return "\n".join(lines)
