import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .comparisons import COMPARISONS, Comparison


@dataclass
class Model:
    """Additive log-odds model over comparison levels.

    weights[c][l]  natural-log evidence for comparison c at level l (0 slot added for 'not comparable').
                   Identifier comparisons (PAN, Aadhaar, documents) carry fixed, documented weights;
                   all others are logistic-regression coefficients learned from identifier-seeded pairs.
    bias           intercept: log-odds of 'same person' for a candidate pair with no evidence either way.
    u_random       P(level | two random records), the reference for term-frequency adjustment.
    """
    weights: dict
    bias: float
    u_random: dict
    meta: dict = field(default_factory=dict)

    def weights_table(self, c: Comparison) -> np.ndarray:
        return np.append(np.array(self.weights[c.name]), 0.0)

    def save(self, path: Path) -> None:
        labels = {c.name: c.labels() for c in COMPARISONS}
        bits = {c.name: {labels[c.name].get(i, str(i)): round(float(w) / np.log(2), 2)
                         for i, w in enumerate(self.weights[c.name]) if i in labels[c.name]} for c in COMPARISONS}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"bias_bits": round(self.bias / np.log(2), 2), "weights_bits_by_level": bits,
                                    "weights": self.weights, "bias": self.bias, "u_random": self.u_random,
                                    "meta": self.meta}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Model":
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(d["weights"], d["bias"], d["u_random"], d.get("meta", {}))
