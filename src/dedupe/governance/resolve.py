"""Which configuration does a run use? The ACTIVE version in the config store when there is one, else the file; always recorded."""
import json
from dataclasses import dataclass
from pathlib import Path

from dedupe.decision.config import DecisionConfig
from dedupe.survivorship.config import SurvivorshipConfig

from .settings import MatchingSettings
from .store import ConfigStore, canonical, content_hash


@dataclass
class Resolved:
    decision: DecisionConfig
    survivorship: SurvivorshipConfig
    settings: MatchingSettings | None       # None = no active settings version: the pipeline's own values apply
    used: dict                              # name -> {"source": "store" | "file", "version": int | None, "hash": str}
    contents: dict                          # name -> canonical content that was used


def resolve(store_url: str | None, decision_path: Path, survivorship_path: Path) -> Resolved:
    store = ConfigStore.open(store_url) if store_url else None
    files = {"decision": Path(decision_path), "survivorship": Path(survivorship_path)}
    models, used, contents = {}, {}, {}
    for name, path in files.items():
        act = store.active(name) if store else None
        if act:
            content, used[name] = act.content, {"source": "store", "version": act.version, "hash": act.hash}
        else:
            content = canonical(name, json.loads(path.read_text(encoding="utf-8")))
            used[name] = {"source": "file", "version": None, "hash": content_hash(content), "path": str(path)}
        contents[name] = content
        models[name] = (DecisionConfig if name == "decision" else SurvivorshipConfig).model_validate(content)
    settings = None
    act = store.active("settings") if store else None
    if act:
        settings = MatchingSettings.model_validate(act.content)
        used["settings"], contents["settings"] = {"source": "store", "version": act.version, "hash": act.hash}, act.content
    return Resolved(models["decision"], models["survivorship"], settings, used, contents)
