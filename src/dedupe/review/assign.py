"""Who works on which review pair: assignments, so two reviewers do not open the same pair and a lead can share out the work.

An assignment names a pair of PEOPLE (their stable P... ids, the same across runs), so it stays with the pair when the project runs again.
It is a work list, not a decision: the decision itself is still a judgment (who decided what, when and why, in the hash-chained log).
Rules (enforced by the API): anyone who decides may take an unassigned pair or give back their own; giving a pair to someone else, or taking
one from another person, needs `judgment.approve` (a lead). A pair assigned to someone else cannot be decided by others, except by a lead.
"""
import time
from dataclasses import dataclass

from sqlalchemy import Column, MetaData, String, Table, and_, delete, insert, select
from sqlalchemy.engine import Engine

from dedupe.schema import create_and_upgrade

_meta = MetaData()
assignments = Table(
    "review_assignments", _meta,
    Column("project_id", String(40), primary_key=True),
    Column("pair", String(40), primary_key=True),                # "P000000001|P000000002", the smaller id first
    Column("assignee", String(200), nullable=False),
    Column("assigned_by", String(200), nullable=False),
    Column("assigned_at", String(40), nullable=False),
)


def pair_key(a: str, b: str) -> str:
    lo, hi = sorted((a, b))
    return f"{lo}|{hi}"


@dataclass(frozen=True)
class Assignment:
    pair: str
    assignee: str
    assigned_by: str
    assigned_at: str


class Assignments:
    def __init__(self, engine: Engine):
        self.engine = engine
        create_and_upgrade(engine, _meta)

    def of_project(self, project_id: str) -> dict[str, Assignment]:
        with self.engine.connect() as c:
            rows = c.execute(select(assignments).where(assignments.c.project_id == project_id)).fetchall()
        return {r.pair: Assignment(r.pair, r.assignee, r.assigned_by, r.assigned_at) for r in rows}

    def get(self, project_id: str, a: str, b: str) -> Assignment | None:
        return self.of_project_pairs(project_id, [pair_key(a, b)]).get(pair_key(a, b))

    def of_project_pairs(self, project_id: str, pairs: list[str]) -> dict[str, Assignment]:
        if not pairs:
            return {}
        with self.engine.connect() as c:
            rows = c.execute(select(assignments).where(and_(assignments.c.project_id == project_id, assignments.c.pair.in_(pairs)))).fetchall()
        return {r.pair: Assignment(r.pair, r.assignee, r.assigned_by, r.assigned_at) for r in rows}

    def assign(self, project_id: str, pairs: list[str], assignee: str, actor: str) -> int:
        """Give the pairs to `assignee` (replacing whoever had them)."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        with self.engine.begin() as c:
            c.execute(delete(assignments).where(and_(assignments.c.project_id == project_id, assignments.c.pair.in_(pairs))))
            for k in pairs:
                c.execute(insert(assignments).values(project_id=project_id, pair=k, assignee=assignee, assigned_by=actor, assigned_at=now))
        return len(pairs)

    def unassign(self, project_id: str, pairs: list[str]) -> int:
        with self.engine.begin() as c:
            return c.execute(delete(assignments).where(and_(assignments.c.project_id == project_id, assignments.c.pair.in_(pairs)))).rowcount or 0

    def delete_for_project(self, project_id: str) -> int:
        with self.engine.begin() as c:
            return c.execute(delete(assignments).where(assignments.c.project_id == project_id)).rowcount or 0


__all__ = ["Assignments", "Assignment", "pair_key"]
