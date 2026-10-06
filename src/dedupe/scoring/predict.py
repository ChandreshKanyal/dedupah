from pathlib import Path

import duckdb
import numpy as np
import pyarrow as pa

from dedupe import crypto

from .comparisons import COMPARISONS
from .levels import level_matrix, tf_matrix
from .model import Model
from .train import adjusted_weights


def score(con: duckdb.DuckDBPyConnection, model: Model, out_path: Path) -> dict:
    """Score `cand_levels` (built by train/materialize). Writes l_rid, r_rid, rules, probability, match_weight,
    and per-comparison levels (lv_*) and contributions in bits (w_*) so every decision can be explained."""
    L, TF = level_matrix(con, "cand_levels"), tf_matrix(con, "cand_levels")
    ids = con.execute("SELECT l_rid, r_rid, rules FROM cand_levels").to_arrow_table()
    W = adjusted_weights(model.weights, model.u_random, L, TF) / np.log(2)  # bits
    prior_bits = model.bias / np.log(2)
    weight = W.sum(axis=1) + prior_bits
    prob = 1.0 / (1.0 + np.exp2(-np.clip(weight, -60, 60)))
    cols = {"l_rid": ids.column(0), "r_rid": ids.column(1), "rules": ids.column(2),
            "probability": pa.array(prob), "match_weight": pa.array(weight.astype(np.float32))}
    for j, c in enumerate(COMPARISONS):
        cols[f"lv_{c.name}"] = pa.array(L[:, j].astype(np.int8))
        cols[f"w_{c.name}"] = pa.array(W[:, j].astype(np.float32))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crypto.write_table(pa.table(cols), out_path)
    return {"pairs": int(len(prob)), "prior_bits": prior_bits}
