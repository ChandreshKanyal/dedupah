"""Stable person IDs across runs.

The clustering step numbers its groups afresh on every run, so a group's label means nothing next week. Reviewer decisions,
downstream systems and audits need an ID that keeps pointing at the same person. After every run each new group inherits the
ID of the previous group it overlaps most (by source records), and everything else is logged:

  * unchanged / grown / shrunk group   -> keeps its ID
  * two or more old people merge       -> the group keeps the oldest ID; the others are RETIRED with a pointer to the survivor
  * one old person splits              -> the largest part keeps the ID; the other parts get NEW IDs (event split_from)
  * a group of brand-new records       -> a new ID (never a reused one)
  * an ID whose records all vanished   -> retired
  * a retired person's records come back together (e.g. a steward undoes a merge) -> the old ID is REACTIVATED (event
    `reactivated`): same records, same person, same ID

IDs are P + 9 digits, handed out in increasing order and never given to a different person: a retired ID only ever returns to
the group of records it had when it was retired (Jaccard overlap of at least one half). The registry is four small parquet files in `registry_dir` (records, persons, events, retired_members); keep that folder between runs.
"""
from pathlib import Path

import duckdb
import pyarrow as pa

from dedupe import crypto

PREFIX = "P"
_SEP = "\x1f"


def _write(table: pa.Table, path: Path) -> None:
    crypto.write_table(table, path)  # atomic (temp file + replace): a crash never leaves a half-written registry; encrypted when a key is set


def _num(pid: str) -> int:
    return int(pid[len(PREFIX):])


def assign_person_ids(con: duckdb.DuckDBPyConnection, registry_dir: Path) -> dict:
    """Needs tables `records` (rid, source_id, source_record_id) and `cluster_map` (rid, cluster_id = this run's arbitrary label).
    Rewrites cluster_map.cluster_id and cluster_flags.cluster_id to stable person IDs, updates the registry, returns counts."""
    registry_dir.mkdir(parents=True, exist_ok=True)
    f_rec, f_per, f_evt = registry_dir / "records.parquet", registry_dir / "persons.parquet", registry_dir / "events.parquet"
    f_ret = registry_dir / "retired_members.parquet"   # last known records of each retired ID (lets an undone merge get its IDs back)
    have_old = f_rec.exists() and f_per.exists()

    con.execute("""CREATE OR REPLACE TEMP TABLE _nc AS SELECT m.cluster_id AS label, r.source_id, r.source_record_id
                   FROM cluster_map m JOIN records r USING (rid)""")
    if have_old:
        con.execute(f"CREATE OR REPLACE TEMP TABLE _old AS SELECT person_id, source_id, source_record_id FROM {crypto.rp(f_rec)}")
        persons = con.execute(f"SELECT person_id, status, created_run, last_run FROM {crypto.rp(f_per)}").fetchall()
        persons_full = con.execute(f"SELECT person_id, status, created_run, last_run, retired_run, successor FROM {crypto.rp(f_per)}").fetchall()
    else:
        con.execute("CREATE OR REPLACE TEMP TABLE _old (person_id VARCHAR, source_id VARCHAR, source_record_id VARCHAR)")
        persons = persons_full = []
    run = 1 + max([max(p[2], p[3] or 0) for p in persons], default=0)
    if f_ret.exists():
        con.execute(f"CREATE OR REPLACE TEMP TABLE _ret AS SELECT person_id, source_id, source_record_id FROM {crypto.rp(f_ret)}")
    else:
        con.execute("CREATE OR REPLACE TEMP TABLE _ret (person_id VARCHAR, source_id VARCHAR, source_record_id VARCHAR)")

    kmin = dict(con.execute(f"SELECT label, min(source_id || '{_SEP}' || source_record_id) FROM _nc GROUP BY 1").fetchall())
    size = dict(con.execute("SELECT label, count(*) FROM _nc GROUP BY 1").fetchall())
    overlap = con.execute("""SELECT n.label, o.person_id, count(*) AS n FROM _nc n JOIN _old o USING (source_id, source_record_id)
                             GROUP BY 1, 2""").fetchall()
    old_size = dict(con.execute("SELECT person_id, count(*) FROM _old GROUP BY 1").fetchall())

    # greedy: biggest overlap first (ties: the older ID, then the cluster holding the smallest record key) -> deterministic
    assigned: dict[str, str] = {}
    taken: set = set()
    for label, pid, _n in sorted(overlap, key=lambda r: (-r[2], _num(r[1]), kmin[r[0]])):
        if label not in assigned and pid not in taken:
            assigned[label] = pid
            taken.add(pid)
    # reactivation: a group nobody inherited from, whose records are (mostly) the last records of a retired ID, gets that ID back
    reactivated: list[str] = []
    ret_size = dict(con.execute("SELECT person_id, count(*) FROM _ret GROUP BY 1").fetchall())
    ret_overlap = con.execute("""SELECT n.label, r.person_id, count(*) FROM _nc n JOIN _ret r USING (source_id, source_record_id)
                                 GROUP BY 1, 2""").fetchall()
    cands = [(n / (size[lab] + ret_size[pid] - n), pid, lab) for lab, pid, n in ret_overlap if lab not in assigned]
    for _jac, pid, lab in sorted((c for c in cands if c[0] >= 0.5), key=lambda c: (-c[0], _num(c[1]), kmin[c[2]])):
        if lab not in assigned and pid not in taken:
            assigned[lab] = pid
            taken.add(pid)
            reactivated.append(pid)
    next_seq = 1 + max([_num(p[0]) for p in persons], default=0)
    created: list[str] = []
    for label in sorted((lab for lab in size if lab not in assigned), key=lambda lab: kmin[lab]):
        assigned[label] = f"{PREFIX}{next_seq:09d}"
        created.append(assigned[label])
        next_seq += 1

    by_label: dict[str, list] = {}
    by_old: dict[str, list] = {}
    for label, pid, n in overlap:
        by_label.setdefault(label, []).append((pid, n))
        by_old.setdefault(pid, []).append((label, n))

    events: list[tuple] = []   # (run, person_id, event, related_person_id, n_records)
    born = set(created) | set(reactivated)
    for label, pid in assigned.items():
        others = sorted(o for o, _ in by_label.get(label, []) if o != pid)
        for o in others:
            n = dict(by_label[label])[o]
            if o in taken:   # that old person lives on in another group
                events.append((run, pid, "moved_in" if pid not in born else "split_from", o, n))
            else:            # that old person has no group of its own any more: it was merged into this one
                events.append((run, pid, "merged_from", o, n))
        if pid in reactivated:
            events.append((run, pid, "reactivated", None, size[label]))
        elif pid in created and not others:
            events.append((run, pid, "created", None, size[label]))
    new_persons, retired_now = [], 0
    for pid, status, created_run, last_run in persons:
        if status == "retired":
            if pid in reactivated:
                new_persons.append((pid, "active", created_run, run, None, None))
            else:
                prev = next(p for p in persons_full if p[0] == pid)
                new_persons.append((pid, status, created_run, last_run, prev[4], prev[5]))
            continue
        if pid in taken:
            new_persons.append((pid, "active", created_run, run, None, None))
        else:
            succ = max(by_old.get(pid, []), key=lambda t: (t[1], kmin[t[0]]), default=None)
            succ_id = assigned[succ[0]] if succ else None
            events.append((run, pid, "retired", succ_id, old_size.get(pid, 0)))
            new_persons.append((pid, "retired", created_run, last_run, run, succ_id))
            retired_now += 1
    new_persons += [(pid, "active", run, run, None, None) for pid in created]

    newly_retired = {e[1] for e in events if e[2] == "retired"}
    still_retired = con.execute("SELECT person_id, source_id, source_record_id FROM _ret").fetchall()
    gone = con.execute("SELECT person_id, source_id, source_record_id FROM _old").fetchall() if newly_retired else []
    ret_rows = [r for r in still_retired if r[0] not in reactivated] + [r for r in gone if r[0] in newly_retired]
    _write(pa.table({"person_id": pa.array([r[0] for r in ret_rows], pa.string()), "source_id": pa.array([r[1] for r in ret_rows], pa.string()),
                     "source_record_id": pa.array([r[2] for r in ret_rows], pa.string())}), f_ret)
    cols = ["person_id", "status", "created_run", "last_run", "retired_run", "successor"]
    _write(pa.table({c: pa.array([r[i] for r in new_persons], pa.string() if c in ("person_id", "status", "successor") else pa.int32())
                     for i, c in enumerate(cols)}), f_per)
    recs = con.execute("SELECT label, source_id, source_record_id FROM _nc ORDER BY source_id, source_record_id").fetchall()
    _write(pa.table({"person_id": [assigned[r[0]] for r in recs], "source_id": [r[1] for r in recs], "source_record_id": [r[2] for r in recs]}), f_rec)
    old_events = con.execute(f"SELECT * FROM {crypto.rp(f_evt)}").fetchall() if f_evt.exists() else []
    allev = old_events + events
    _write(pa.table({"run": pa.array([e[0] for e in allev], pa.int32()), "person_id": [e[1] for e in allev], "event": [e[2] for e in allev],
                     "related_person_id": pa.array([e[3] for e in allev], pa.string()), "n_records": pa.array([e[4] for e in allev], pa.int32())}), f_evt)

    con.register("_lab_src", pa.table({"label": list(assigned), "person_id": list(assigned.values())}))
    con.execute("CREATE OR REPLACE TEMP TABLE _lab AS SELECT * FROM _lab_src")
    con.execute("CREATE OR REPLACE TABLE cluster_map AS SELECT m.rid, l.person_id AS cluster_id FROM cluster_map m JOIN _lab l ON l.label = m.cluster_id")
    con.execute("CREATE OR REPLACE TABLE cluster_flags AS SELECT l.person_id AS cluster_id, f.flags FROM cluster_flags f JOIN _lab l ON l.label = f.cluster_id")
    for t in ("_nc", "_old", "_lab", "_ret"):
        con.execute(f"DROP TABLE IF EXISTS {t}")
    kept = len(taken)
    return {"run": run, "persons": len(assigned), "kept": kept - len(reactivated), "created": len(created), "reactivated": len(reactivated),
            "retired": retired_now,
            "events": len(events), "registry": str(registry_dir)}
