"""Plain-language description of what changed between two versions of a config."""
import json


def _flat(obj, prefix="") -> dict:
    if isinstance(obj, dict) and obj:
        return {k2: v for k, v in obj.items() for k2, v in _flat(v, f"{prefix}{k}.").items()}
    return {prefix.rstrip("."): obj}


def _show(v) -> str:
    s = json.dumps(v, ensure_ascii=False)
    return s if len(s) <= 60 else s[:57] + "..."


def _generic(old: dict, new: dict) -> list[str]:
    a, b = _flat(old), _flat(new)
    out = []
    for k in sorted(set(a) | set(b)):
        if a.get(k, "<unset>") != b.get(k, "<unset>"):
            out.append(f"{k}: {_show(a.get(k, '<unset>'))} -> {_show(b.get(k, '<unset>'))}")
    return out


def _rules(old: dict, new: dict) -> list[str]:
    a = {r["id"]: r for r in old.get("rules", [])}
    b = {r["id"]: r for r in new.get("rules", [])}
    out = []
    for rid in b.keys() - a.keys():
        out.append(f"rule {rid!r} added ({b[rid]['type']}{'' if b[rid]['enabled'] else ', disabled'})")
    for rid in a.keys() - b.keys():
        out.append(f"rule {rid!r} removed ({a[rid]['type']})")
    for rid in a.keys() & b.keys():
        x, y = a[rid], b[rid]
        if x == y:
            continue
        what = []
        if x["enabled"] != y["enabled"]:
            what.append("enabled" if y["enabled"] else "disabled")
        if x["type"] != y["type"]:
            what.append(f"type {x['type']} -> {y['type']}")
        if x["when"] != y["when"]:
            what.append("condition changed")
        if x["priority_offset"] != y["priority_offset"]:
            what.append(f"priority_offset {x['priority_offset']} -> {y['priority_offset']}")
        if x["reason"] != y["reason"]:
            what.append("explanation text changed")
        out.append(f"rule {rid!r}: " + ", ".join(what))
    order = [r["id"] for r in new.get("rules", []) if r["id"] in a]
    if order != [r["id"] for r in old.get("rules", []) if r["id"] in b]:
        out.append("rule order changed (within a rule type the first listed rule is the one credited)")
    return sorted(out)


def _sources(old: dict, new: dict) -> list[str]:
    a = {s["source_id"]: s for s in old.get("sources", [])}
    b = {s["source_id"]: s for s in new.get("sources", [])}
    out = [f"source {sid!r} added ({len(b[sid]['fields'])} fields mapped)" for sid in sorted(b.keys() - a.keys())]
    out += [f"source {sid!r} removed" for sid in sorted(a.keys() - b.keys())]
    for sid in sorted(a.keys() & b.keys()):
        x, y = a[sid], b[sid]
        what = []
        if x["connector"] != y["connector"]:
            what += [f"connector {k} {_show(x['connector'].get(k, '<unset>'))} -> {_show(y['connector'].get(k, '<unset>'))}"
                     for k in sorted(set(x["connector"]) | set(y["connector"])) if x["connector"].get(k) != y["connector"].get(k)]
        if x["record_id"] != y["record_id"]:
            what.append(f"record id column {x['record_id']} -> {y['record_id']}")
        for f in sorted(set(x["fields"]) | set(y["fields"])):
            if x["fields"].get(f) != y["fields"].get(f):
                what.append(f"field {f}: {_show(x['fields'].get(f, '<unmapped>'))} -> {_show(y['fields'].get(f, '<unmapped>'))}")
        if x["attributes"] != y["attributes"]:
            what.append("carried-along columns changed")
        out += [f"source {sid!r}: {w}" for w in what]
    return out


def describe_changes(name: str, old: dict | None, new: dict) -> list[str]:
    """Readable list of differences; `old` None means the first version."""
    if old is None:
        return ["first version"]
    if name == "decision":
        out = [f"{k} {old[k]} -> {new[k]}" for k in ("auto_merge_at", "review_at") if old[k] != new[k]]
        return out + _rules(old, new)
    if name == "sources":
        return _sources(old, new)
    return _generic(old, new)
