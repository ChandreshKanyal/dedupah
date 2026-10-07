"""The review service: everything a steward does, as plain Python calls (an API and a screen will sit on top of this).

Reads the outputs of a pipeline run (`work` folder) and writes steward decisions to the judgment store; the next pipeline run
applies them (see dedupe.clustering.judged). Person IDs are the stable `P...` IDs, so a queue item stays addressable across runs.

  queue()            ranked list of open person pairs, hiding anything a steward has already looked at
  explain()          side-by-side view of two people: golden values, member records, per-field evidence (why / why not), hard conflicts
  approve_match / reject_match / mark_unsure / split_person / override_field / undo_action   -> judgments
  bulk_approve / bulk_reject                                                              -> many items, each its own undoable action
  pending / approve_pending / decline_pending                                              -> four-eyes approval
  summary()          counts for a dashboard

Safeguards: sensitive fields (PAN, phone, email, documents) are masked unless the policy allows revealing; merging people with
different valid PANs/Aadhaar needs explicit confirmation and a second approver by default; policy can also require a second
approver for big merges, splits and identifier overrides.
"""
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from dedupe import crypto
from dedupe.scoring.comparisons import COMPARISONS
from dedupe.store import JudgmentError, JudgmentStore
from dedupe.survivorship.config import GOLDEN_FIELDS

SENSITIVE = ("pan", "mobile", "mobile_alt", "email", "passport_no", "dl_no", "voter_id", "aadhaar_token", "aadhaar_last4")
_LABELS = {c.name: c.labels() for c in COMPARISONS}
MAX_PAIRS_PER_ACTION = 500


class ReviewError(ValueError):
    pass


@dataclass
class ReviewPolicy:
    four_eyes_merge_min_records: int | None = None                       # merges whose combined size reaches this need a second approver
    four_eyes_split: bool = False
    four_eyes_override_fields: tuple = ("pan", "aadhaar_token", "aadhaar_last4", "dob")
    four_eyes_id_conflict: bool = True
    reveal_sensitive: bool = False


def _mask(field: str, v):
    if v is None or field not in SENSITIVE:
        return v
    v = str(v)
    if field == "email":
        local, _, dom = v.partition("@")
        return (local[:1] + "***@" + dom) if dom else "***"
    return ("*" * max(len(v) - 3, 0)) + v[-3:]


class ReviewService:
    def __init__(self, work: Path, store: JudgmentStore, policy: ReviewPolicy | None = None):
        self.work, self.store, self.policy = Path(work), store, policy or ReviewPolicy()
        w = self.work
        self.f = {"queue": w / "review_queue.parquet", "pm": w / "clusters" / "possible_matches.parquet", "cl": w / "clusters" / "clusters.parquet",
                  "scored": w / "scored_pairs.parquet", "decisions": w / "decisions.parquet", "gold": w / "golden" / "golden_records.parquet",
                  "std": w / "standardised.parquet", "canon": w / "canonical.parquet"}

    # ---- helpers -------------------------------------------------------------------------------------------------
    def _con(self):
        return crypto.connect()

    def _run_info(self) -> dict:
        p = self.work / "pipeline_summary.json"
        if not p.exists():
            return {}
        s = json.loads(p.read_text(encoding="utf-8"))
        configs = {k: {"version": v.get("version"), "hash": v.get("hash")} for k, v in (s.get("configs") or {}).items() if k != "effective_settings"}
        return {"git_commit": s.get("git_commit"), "identity_run": (s.get("identity") or {}).get("run"), "configs": configs}

    def _members(self, con, person: str) -> list[tuple]:
        rows = con.execute(f"SELECT source_id, source_record_id FROM {crypto.rp(self.f['cl'])} WHERE cluster_id = ? ORDER BY source_id, source_record_id",
                           [person]).fetchall()
        if not rows:
            raise ReviewError(f"unknown person {person!r}")
        return rows

    def _pairs_between(self, con, a: str, b: str) -> list[dict]:
        """Review record pairs between two people, best first, each with its stored evidence (when the run kept it)."""
        lo, hi = sorted([a, b])
        cur = con.execute(f"""SELECT * FROM {crypto.rp(self.f['pm'])}
                              WHERE least(cluster_l, cluster_r) = ? AND greatest(cluster_l, cluster_r) = ? ORDER BY probability DESC, l_rid, r_rid""", [lo, hi])
        cols = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            r = dict(zip(cols, row, strict=True))
            r["a"], r["b"] = (r["l_source_id"], r["l_source_record_id"]), (r["r_source_id"], r["r_source_record_id"])
            out.append(r)
        return out

    def _hard_conflicts(self, con, a: str, b: str) -> list[str]:
        out = []
        for col, label in (("pan", "different_pan"), ("aadhaar_token", "different_aadhaar")):
            sets = []
            for person in (a, b):
                sets.append({r[0] for r in con.execute(
                    f"SELECT s.{col} FROM {crypto.rp(self.f['std'])} s JOIN {crypto.rp(self.f['cl'])} c USING (source_id, source_record_id) "
                    f"WHERE c.cluster_id = ? AND s.{col} IS NOT NULL", [person]).fetchall()})
            if sets[0] and sets[1] and not (sets[0] & sets[1]):
                out.append(label)
        return out

    def _evidence_for(self, con, pair: dict) -> dict:
        rec = pair
        if "match_weight" not in pair:   # run produced without stored evidence: fall back to scanning the scored pairs
            row = con.execute(f"SELECT * FROM {crypto.rp(self.f['scored'])} WHERE l_rid = ? AND r_rid = ?", [pair["l_rid"], pair["r_rid"]]).fetchone()
            if row is None:
                return {"probability": pair["probability"], "contributions": [], "decided_by": pair["decided_by"], "reason": pair["reason"]}
            rec = dict(zip([d[0] for d in con.description], row, strict=True))
        contrib = []
        for c in COMPARISONS:
            lv = rec.get(f"lv_{c.name}")
            contrib.append({"comparison": c.name, "level": "not comparable (a value is missing)" if lv is None or lv < 0 else _LABELS[c.name].get(lv, str(lv)),
                            "bits": round(float(rec.get(f"w_{c.name}") or 0.0), 2)})
        contrib.sort(key=lambda x: -abs(x["bits"]))
        return {"probability": round(float(pair["probability"]), 4), "match_weight_bits": round(float(rec["match_weight"]), 2),
                "contributions": contrib, "decided_by": pair["decided_by"], "reason": pair["reason"]}

    # ---- reading -------------------------------------------------------------------------------------------------
    def queue(self, limit: int = 50, offset: int = 0, status: str = "open", min_probability: float = 0.0, max_probability: float = 1.0) -> list[dict]:
        """Ranked person pairs. status: 'open' (nothing decided yet), 'unsure' (snoozed), 'all'. A pair is hidden once a steward decided any of its
        record pairs, even before the next pipeline run (the next run applies the decision and rebuilds the queue)."""
        if status not in ("open", "unsure", "all"):
            raise ReviewError("status is 'open', 'unsure' or 'all'")
        con = self._con()
        decided = {frozenset((tuple(j.subject["a"]), tuple(j.subject["b"]))): j.kind for j in self.store.history(("same_person", "different_person", "unsure"))
                   if j.status in ("active", "pending_approval")}
        out, skipped, batch, pos = [], 0, 500, 0
        while len(out) < limit:
            rows = con.execute(f"SELECT * FROM {crypto.rp(self.f['queue'])} WHERE top_probability >= ? AND top_probability <= ? "
                               "ORDER BY priority DESC, cluster_l, cluster_r LIMIT ? OFFSET ?", [min_probability, max_probability, batch, pos]).fetchall()
            if not rows:
                break
            cols = [d[0] for d in con.description]
            pos += len(rows)
            items = [dict(zip(cols, r, strict=True)) for r in rows]
            kinds_by_item = self._decided_kinds(con, items, decided) if decided else {}
            for item in items:
                kinds = kinds_by_item.get((item["cluster_l"], item["cluster_r"]), set())
                state = "open" if not kinds else ("unsure" if kinds == {"unsure"} else "decided")
                if status != "all" and state != status:
                    continue
                if skipped < offset:
                    skipped += 1
                    continue
                item["state"] = state
                out.append(item)
                if len(out) >= limit:
                    break
        return out

    def _decided_kinds(self, con, items: list[dict], decided: dict) -> dict:
        """{(cluster_l, cluster_r): set of steward decision kinds on its record pairs}, for a whole page in one query."""
        import pyarrow as pa
        con.register("page_items", pa.table({"cl": [i["cluster_l"] for i in items], "cr": [i["cluster_r"] for i in items]}))
        rows = con.execute(f"""SELECT least(cluster_l, cluster_r), greatest(cluster_l, cluster_r), l_source_id, l_source_record_id, r_source_id, r_source_record_id
            FROM {crypto.rp(self.f['pm'])} WHERE (least(cluster_l, cluster_r), greatest(cluster_l, cluster_r)) IN (SELECT cl, cr FROM page_items)""").fetchall()
        out: dict = {}
        for cl, cr, ls, lr, rs, rr in rows:
            k = decided.get(frozenset(((ls, lr), (rs, rr))))
            if k:
                out.setdefault((cl, cr), set()).add(k)
        return out

    def person(self, person_id: str, reveal: bool = False) -> dict:
        con = self._con()
        self._members(con, person_id)
        reveal = reveal and self.policy.reveal_sensitive
        g = con.execute(f"SELECT * FROM {crypto.rp(self.f['gold'])} WHERE cluster_id = ?", [person_id]).fetchone()
        gcols = [d[0] for d in con.description]
        golden = {k: (v if reveal else _mask(k, v)) for k, v in zip(gcols, g, strict=True) if k in GOLDEN_FIELDS and v is not None} if g else {}
        meta = dict(zip(gcols, g, strict=True)) if g else {}
        recs = con.execute(f"""SELECT c.source_id, c.source_record_id, k.full_name, s.name_clean, s.dob, s.gender, s.mobile, s.email, k.address, s.city,
                                      s.address_pin, s.pan, s.aadhaar_last4, s.passport_no, s.dl_no, s.voter_id
            FROM {crypto.rp(self.f['cl'])} c JOIN {crypto.rp(self.f['std'])} s USING (source_id, source_record_id)
            JOIN {crypto.rp(self.f['canon'])} k USING (source_id, source_record_id) WHERE c.cluster_id = ? ORDER BY 1, 2 LIMIT 50""", [person_id]).fetchall()
        rcols = ["source_id", "source_record_id", "name_as_written", "name_clean", "dob", "gender", "mobile", "email", "address", "city", "pincode",
                 "pan", "aadhaar_last4", "passport_no", "dl_no", "voter_id"]
        records = [{k: (v if reveal else _mask(k, v)) for k, v in zip(rcols, r, strict=True)} for r in recs]
        return {"person_id": person_id, "golden": golden, "record_count": meta.get("record_count", len(records)), "sources": meta.get("sources"),
                "conflict_fields": meta.get("conflict_fields", []), "records": records}

    def explain(self, person_l: str, person_r: str, reveal: bool = False) -> dict:
        """Side-by-side view for deciding a pair: both people, the strongest record pair's evidence ('for' and 'against'), hard conflicts."""
        con = self._con()
        pairs = self._pairs_between(con, person_l, person_r)
        best = pairs[0] if pairs else None
        ev = self._evidence_for(con, best) if best else None
        return {"persons": [self.person(person_l, reveal), self.person(person_r, reveal)],
                "record_pairs": len(pairs), "best_pair": ({"a": best["a"], "b": best["b"]} if best else None),
                "why_match": [c for c in (ev or {}).get("contributions", []) if c["bits"] > 0.5],
                "why_not": [c for c in (ev or {}).get("contributions", []) if c["bits"] < -0.5],
                "probability": (ev or {}).get("probability"), "decided_by": (ev or {}).get("decided_by"), "reason": (ev or {}).get("reason"),
                "hard_conflicts": self._hard_conflicts(con, person_l, person_r),
                "existing_judgments": [{"id": j.id, "kind": j.kind, "actor": j.actor, "status": j.status, "reason": j.reason}
                                       for p in pairs for j in self.store.history(("same_person", "different_person", "unsure"))
                                       if {tuple(j.subject["a"]), tuple(j.subject["b"])} == {p["a"], p["b"]}]}

    # ---- acting --------------------------------------------------------------------------------------------------
    def _evidence(self, con, pair, extra) -> dict:
        ev = self._evidence_for(con, pair)
        return {"action_id": extra["action_id"], "persons": extra["persons"], "probability": ev["probability"], "run_id": self.work.name,
                "top_evidence": ev["contributions"][:4], "decided_by": ev["decided_by"], "pipeline": self._run_info()}

    def approve_match(self, person_l: str, person_r: str, actor: str, reason: str = "", confirm_id_conflict: bool = False,
                      action_id: str | None = None) -> dict:
        con = self._con()
        pairs = self._pairs_between(con, person_l, person_r)
        if not pairs:
            raise ReviewError("no record pair links these two people in the queue; they may already be merged or never were candidates")
        conflicts = self._hard_conflicts(con, person_l, person_r)
        if conflicts and not confirm_id_conflict:
            raise ReviewError(f"These two people have conflicting identifiers ({', '.join(c.replace('_', ' ') for c in conflicts)}). "
                              "Tick 'I confirm they are one person' and give a reason to merge them anyway.")
        size = len(self._members(con, person_l)) + len(self._members(con, person_r))
        approval = bool(conflicts and self.policy.four_eyes_id_conflict) or (
            self.policy.four_eyes_merge_min_records is not None and size >= self.policy.four_eyes_merge_min_records)
        action_id = action_id or uuid.uuid4().hex[:12]
        best = pairs[0]
        j = self.store.same_person(best["a"], best["b"], actor, reason, self._evidence(con, best, {"action_id": action_id, "persons": [person_l, person_r]}),
                                   requires_approval=approval, confirm_id_conflict=bool(conflicts and confirm_id_conflict))
        return {"action_id": action_id, "judgment_ids": [j], "pending_approval": approval}

    def _decide_all_pairs(self, kind: str, person_l: str, person_r: str, actor: str, reason: str, action_id: str | None) -> dict:
        con = self._con()
        pairs = self._pairs_between(con, person_l, person_r)[:MAX_PAIRS_PER_ACTION]
        if not pairs:
            raise ReviewError("no record pair links these two people in the queue")
        action_id = action_id or uuid.uuid4().hex[:12]
        ids = []
        for p in pairs:   # every review pair between the two people, so the item does not come back after the next run
            ev = self._evidence(con, p, {"action_id": action_id, "persons": [person_l, person_r]})
            ids.append(getattr(self.store, kind)(p["a"], p["b"], actor, reason, ev))
        return {"action_id": action_id, "judgment_ids": ids, "pending_approval": False}

    def reject_match(self, person_l: str, person_r: str, actor: str, reason: str = "", action_id: str | None = None) -> dict:
        return self._decide_all_pairs("different_person", person_l, person_r, actor, reason, action_id)

    def mark_unsure(self, person_l: str, person_r: str, actor: str, reason: str = "", action_id: str | None = None) -> dict:
        return self._decide_all_pairs("unsure", person_l, person_r, actor, reason, action_id)

    def split_person(self, person_id: str, parts: list[list[tuple]], actor: str, reason: str, action_id: str | None = None) -> dict:
        """Undo a wrong merge: `parts` are lists of (source_id, source_record_id) of this person that must become separate people."""
        con = self._con()
        members = set(self._members(con, person_id))
        for part in parts:
            for k in part:
                if tuple(k) not in members:
                    raise ReviewError("a record in the parts does not belong to this person")
        action_id = action_id or uuid.uuid4().hex[:12]
        ev = {"action_id": action_id, "persons": [person_id], "run_id": self.work.name, "pipeline": self._run_info()}
        j = self.store.split(parts, actor, reason, ev, requires_approval=self.policy.four_eyes_split)
        return {"action_id": action_id, "judgment_ids": [j], "pending_approval": self.policy.four_eyes_split}

    def override_field(self, person_id: str, field: str, value: str | None, actor: str, reason: str, action_id: str | None = None) -> dict:
        con = self._con()
        anchors = self._members(con, person_id)[:5]
        action_id = action_id or uuid.uuid4().hex[:12]
        approval = field in self.policy.four_eyes_override_fields
        j = self.store.override_field(field, value, anchors, actor, reason, {"action_id": action_id, "persons": [person_id], "run_id": self.work.name,
                                                                         "pipeline": self._run_info()}, requires_approval=approval)
        return {"action_id": action_id, "judgment_ids": [j], "pending_approval": approval}

    def undo_action(self, action_id: str, actor: str, reason: str = "") -> dict:
        """Revoke every judgment created by one action (a reject covers many record pairs)."""
        ids = [j.id for j in self.store.history(("same_person", "different_person", "unsure", "split", "field_override"))
               if j.evidence.get("action_id") == action_id and j.status in ("active", "pending_approval")]
        if not ids:
            raise ReviewError(f"no active judgments for action {action_id!r}")
        for i in ids:
            self.store.revoke(i, actor, reason or "undo")
        return {"action_id": action_id, "revoked": ids}

    def bulk_approve(self, items: list[tuple], actor: str, reason: str = "") -> dict:
        return self._bulk(self.approve_match, items, actor, reason)

    def bulk_reject(self, items: list[tuple], actor: str, reason: str = "") -> dict:
        return self._bulk(self.reject_match, items, actor, reason)

    def _bulk(self, fn, items, actor, reason) -> dict:
        done, failed = [], []
        for l, r in items:
            try:
                done.append({"pair": (l, r), **fn(l, r, actor, reason)})
            except (ReviewError, JudgmentError) as e:
                failed.append({"pair": (l, r), "error": str(e)})
        return {"done": done, "failed": failed}

    # ---- four eyes + dashboard -----------------------------------------------------------------------------------
    def pending(self) -> list[dict]:
        return [{"id": j.id, "kind": j.kind, "actor": j.actor, "reason": j.reason, "persons": j.evidence.get("persons"), "created_at": j.created_at}
                for j in self.store.pending_approval()]

    def approve_pending(self, judgment_id: int, actor: str, reason: str = "") -> int:
        return self.store.approve(judgment_id, actor, reason)

    def decline_pending(self, judgment_id: int, actor: str, reason: str) -> int:
        return self.store.decline(judgment_id, actor, reason)

    def summary(self) -> dict:
        con = self._con()
        total = con.execute(f"SELECT count(*) FROM {crypto.rp(self.f['queue'])}").fetchone()[0] if self.f["queue"].exists() else 0
        hist = self.store.history(("same_person", "different_person", "unsure", "split", "field_override"))
        by_kind: dict = {}
        for j in hist:
            if j.status == "active":
                by_kind[j.kind] = by_kind.get(j.kind, 0) + 1
        return {"queue_items_in_last_run": total, "open_now": len(self.queue(limit=10**6)) if total <= 5000 else None, "active_judgments": by_kind,
                "pending_approval": len(self.store.pending_approval()), "chain_ok": self.store.verify()["ok"]}


__all__ = ["ReviewService", "ReviewPolicy", "ReviewError"]
