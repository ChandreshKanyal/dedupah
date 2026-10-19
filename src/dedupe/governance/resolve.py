"""Which configuration does a run use? The version the run was started with when it chose one (pinned), else the ACTIVE version in the
config store when there is one, else the file; always recorded."""
import json
from dataclasses import dataclass
from pathlib import Path

from dedupe.decision.config import DecisionConfig
from dedupe.survivorship.config import SurvivorshipConfig

from .settings import MatchingSettings
from .store import Active, ConfigStore, canonical, content_hash


@dataclass
class Resolved:
    decision: DecisionConfig
    survivorship: SurvivorshipConfig
    settings: MatchingSettings | None       # None = no active settings version: the pipeline's own values apply
    used: dict                              # name -> {"source": "store" | "file", "version": int | None, "hash": str}
    contents: dict                          # name -> canonical content that was used
    sources: dict | None = None             # the ACTIVE source configuration from the store, if there is one (else the run uses its sources file)


def resolve(store_url: str | None, decision_path: Path, survivorship_path: Path, pinned: dict | None = None) -> Resolved:
    """`pinned`: name -> version number chosen when the run was started (decision, survivorship, settings); those versions are used as they
    are, whether or not they are live now."""
    store = ConfigStore.open(store_url) if store_url else None
    pinned = pinned or {}
    if pinned and store is None:
        raise ValueError("pinned configuration versions need the config store")

    def chosen(name: str):
        if name not in pinned:
            return store.active(name) if store else None
        v = next((x for x in store.versions(name) if x.version == int(pinned[name])), None)
        if v is None or v.declined_by:
            raise ValueError(f"{name} version {pinned[name]} does not exist or was declined")
        return Active(name, v.version, store.get(name, v.version), v.content_hash)
    files = {"decision": Path(decision_path), "survivorship": Path(survivorship_path)}
    models, used, contents = {}, {}, {}
    for name, path in files.items():
        act = chosen(name)
        if act:
            content, used[name] = act.content, {"source": "store", "version": act.version, "hash": act.hash, "pinned": name in pinned}
        else:
            content = canonical(name, json.loads(path.read_text(encoding="utf-8")))
            used[name] = {"source": "file", "version": None, "hash": content_hash(content), "path": str(path)}
        contents[name] = content
        models[name] = (DecisionConfig if name == "decision" else SurvivorshipConfig).model_validate(content)
    settings = None
    act = chosen("settings")
    if act:
        settings = MatchingSettings.model_validate(act.content)
        used["settings"], contents["settings"] = {"source": "store", "version": act.version, "hash": act.hash, "pinned": "settings" in pinned}, act.content
    src = store.active("sources") if store else None
    if src:
        used["sources"], contents["sources"] = {"source": "store", "version": src.version, "hash": src.hash}, src.content
    return Resolved(models["decision"], models["survivorship"], settings, used, contents, src.content if src else None)
