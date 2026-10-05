import random

import duckdb
import pyarrow as pa

from dedupe.identity import assign_person_ids


def _run(tmp_path, groups, reg="reg"):
    """groups: list of lists of (source, record_id). Labels are shuffled/arbitrary on purpose. Returns {record: person_id}."""
    recs = [r for g in groups for r in g]
    con = duckdb.connect()
    con.register("r", pa.table({"rid": list(range(len(recs))), "source_id": [r[0] for r in recs], "source_record_id": [r[1] for r in recs]}))
    con.execute("CREATE TABLE records AS SELECT * FROM r")
    labels = [f"cl{random.Random(len(recs) + i).randint(0, 10**8):08d}_{i}" for i in range(len(groups))]
    rows = [(rid, labels[gi]) for gi, g in enumerate(groups) for rid in range(sum(len(x) for x in groups[:gi]), sum(len(x) for x in groups[:gi + 1]))]
    con.register("m", pa.table({"rid": [r[0] for r in rows], "cluster_id": [r[1] for r in rows]}))
    con.execute("CREATE TABLE cluster_map AS SELECT * FROM m")
    con.register("f", pa.table({"cluster_id": labels, "flags": [""] * len(labels)}))
    con.execute("CREATE TABLE cluster_flags AS SELECT * FROM f")
    info = assign_person_ids(con, tmp_path / reg)
    ids = {recs[rid]: pid for rid, pid in con.execute("SELECT rid, cluster_id FROM cluster_map").fetchall()}
    assert {r[0] for r in con.execute("SELECT cluster_id FROM cluster_flags").fetchall()} == set(ids.values())   # flags follow the new ids
    return ids, info


def _events(tmp_path, reg="reg"):
    return duckdb.sql(f"select run, person_id, event, related_person_id from '{tmp_path / reg / 'events.parquet'}' order by run, person_id, event, related_person_id").fetchall()


def _persons(tmp_path, reg="reg"):
    return {r[0]: r[1:] for r in duckdb.sql(f"select person_id, status, retired_run, successor from '{tmp_path / reg / 'persons.parquet'}'").fetchall()}


A, B, C, D, E, F = [("s", x) for x in "abcdef"]


def test_first_run_assigns_ids_in_a_deterministic_order_independent_of_labels(tmp_path):
    ids1, info = _run(tmp_path, [[A, B], [C], [D, E]], reg="r1")
    ids2, _ = _run(tmp_path, [[D, E], [A, B], [C]], reg="r2")       # same groups, different order and labels
    assert ids1 == ids2
    assert ids1[A] == ids1[B] == "P000000001" and ids1[C] == "P000000002" and ids1[D] == "P000000003"
    assert info["run"] == 1 and info["created"] == 3 and info["kept"] == 0


def test_rerun_with_identical_groups_changes_nothing(tmp_path):
    first, _ = _run(tmp_path, [[A, B], [C], [D, E]])
    second, info = _run(tmp_path, [[D, E], [C], [A, B]])
    assert first == second and info["created"] == 0 and info["retired"] == 0 and info["run"] == 2
    assert _events(tmp_path) == [(1, f"P00000000{i}", "created", None) for i in (1, 2, 3)]


def test_new_records_join_existing_ids_and_brand_new_groups_get_new_ids(tmp_path):
    first, _ = _run(tmp_path, [[A, B], [C]])
    second, info = _run(tmp_path, [[A, B, D], [C], [E, F]])         # D joins the first person; E+F are new
    assert second[D] == first[A] and second[C] == first[C] and second[E] == second[F] == "P000000003"
    assert info["created"] == 1 and info["kept"] == 2


def test_merge_keeps_the_oldest_id_and_retires_the_other_with_a_pointer(tmp_path):
    first, _ = _run(tmp_path, [[A, B], [C, D]])
    second, info = _run(tmp_path, [[A, B, C, D]])
    assert set(second.values()) == {first[A]}                        # P1 (older) survives
    assert _persons(tmp_path)[first[C]] == ("retired", 2, first[A]) and _persons(tmp_path)[first[A]][0] == "active"
    assert (2, first[A], "merged_from", first[C]) in _events(tmp_path) and info["retired"] == 1


def test_split_keeps_the_id_on_the_larger_part_and_gives_the_rest_a_new_id(tmp_path):
    first, _ = _run(tmp_path, [[A, B, C, D]])
    second, _ = _run(tmp_path, [[A, B, C], [D]])
    assert second[A] == second[B] == second[C] == first[A] and second[D] == "P000000002"
    assert (2, "P000000002", "split_from", first[A]) in _events(tmp_path)


def test_retired_ids_are_never_reused_and_vanished_records_retire_empty_groups(tmp_path):
    _run(tmp_path, [[A], [B]])
    second, _ = _run(tmp_path, [[A]])                                 # B vanished from the source
    assert _persons(tmp_path)["P000000002"][0] == "retired"
    third, _ = _run(tmp_path, [[A], [C]])                             # a new person appears: must not take P2
    assert third[C] == "P000000003" and third[A] == second[A]
    assert _persons(tmp_path)["P000000002"][0] == "retired"


def test_equal_overlap_ties_are_broken_deterministically(tmp_path):
    def two_runs(sub, second_groups):
        d = tmp_path / sub
        d.mkdir()
        _run(d, [[A, B], [C, D]])
        return _run(d, second_groups)[0]            # every new group overlaps both old people equally
    x = two_runs("x", [[A, C], [B, D]])
    y = two_runs("y", [[B, D], [A, C]])              # same groups, different input order and labels
    assert x == y and x[A] == x[C] == "P000000001" and x[B] == x[D] == "P000000002"
