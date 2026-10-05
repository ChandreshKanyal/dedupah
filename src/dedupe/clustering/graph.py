"""Graph clustering with contradiction checks. Pure Python, deterministic, no ML: every split is traceable.

Records are nodes; auto-merge pairs are weighted edges (weight = match probability).
1. Connected components of the auto-merge graph.
2. A component is CONTRADICTORY if it contains
     - two different valid PANs, or two different Aadhaar tokens, or
     - a 'cannot-link' pair (a pair a never-merge rule vetoed, or a steward said are different people), or
     - two records from different parts of a steward 'split' (`separations`).
3. A contradictory component is split: find the widest (max-bottleneck) path between a conflicting pair and delete
   its weakest edge; re-form components; repeat until no contradiction remains (bounded by `max_removals`).
   If the bound is hit the component is released as singletons and flagged, so a human decides.

Steward decisions enter as extra edges and constraints: a 'same person' judgment is an edge heavier than any automatic one
(weight 2 + a tiny bonus for newer judgments), so the guard removes automatic edges first and, if two judgments truly
contradict each other, the older one first (the newer decision wins).
"""
import heapq
from collections import defaultdict
from dataclasses import dataclass, field


@dataclass
class ClusterConfig:
    max_cluster_size: int = 15  # larger groups are flagged (not split): one customer rarely has this many records
    max_removals: int = 200  # per original component


@dataclass
class ClusterResult:
    labels: dict  # rid -> cluster label (min rid in the cluster)
    stats: dict = field(default_factory=dict)
    flags: dict = field(default_factory=dict)  # cluster label -> list of flag strings
    removed_edges: list = field(default_factory=list)  # (l, r, probability, reason)


class _UF:
    def __init__(self):
        self.p = {}

    def find(self, x):
        while self.p.setdefault(x, x) != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[max(ra, rb)] = min(ra, rb)


def components(nodes, edges) -> list[set]:
    uf = _UF()
    for n in nodes:
        uf.find(n)
    for l, r, _ in edges:
        uf.union(l, r)
    groups = defaultdict(set)
    for n in nodes:
        groups[uf.find(n)].add(n)
    return list(groups.values())


def _find_conflict(nodes: set, ids: dict, cannot: dict, sep: dict | None = None):
    """Return (x, y, reason) for one contradiction inside `nodes`, or None."""
    for n in sorted(nodes):
        for m in sorted(cannot.get(n, ())):
            if m in nodes:
                return (n, m, "vetoed_pair")
    if sep:
        first: dict = {}
        for n in sorted(nodes):
            for split_id, part in sep.get(n, ()):
                seen = first.setdefault(split_id, (part, n))
                if seen[0] != part:
                    return (seen[1], n, "steward_split")
    for kind in ("pan", "aadhaar"):
        seen: dict = {}
        for n in sorted(nodes):
            v = ids.get(n, {}).get(kind)
            if v and v not in seen:
                seen[v] = n
        if len(seen) > 1:
            (_, a), (_, b) = sorted(seen.items(), key=lambda kv: kv[1])[:2]
            return (a, b, f"different_{kind}")
    return None


def _widest_path_bottleneck(adj: dict, x, y):
    """Edge (min,max) with the lowest probability on the max-bottleneck path x -> y; None if not connected."""
    best = {x: float("inf")}
    parent = {}
    heap = [(-float("inf"), x)]
    while heap:
        negb, u = heapq.heappop(heap)
        b = -negb
        if b < best.get(u, -1):
            continue
        if u == y:
            break
        for v, p in adj[u]:
            nb = min(b, p)
            if nb > best.get(v, -1):
                best[v], parent[v] = nb, (u, p)
                heapq.heappush(heap, (-nb, v))
    if y not in parent:
        return None
    worst, edge = float("inf"), None
    v = y
    while v != x:
        u, p = parent[v]
        key = (min(u, v), max(u, v))
        if p < worst or (p == worst and key < edge):
            worst, edge = p, key
        v = u
    return edge, worst


def cluster(nodes, edges, ids: dict | None = None, cannot_links=(), cfg: ClusterConfig | None = None,
            guard: bool = True, separations: dict | None = None) -> ClusterResult:
    """
    nodes        iterable of record ids (ints)
    edges        iterable of (l, r, probability) auto-merge links
    ids          {rid: {"pan": ..., "aadhaar": ...}} for the contradiction check
    cannot_links iterable of (l, r) pairs that must not end up together (vetoed by a never-merge rule, or by a steward)
    separations  {rid: [(split_id, part_no), ...]} from steward splits: records of different parts must not end up together
    guard=False  plain connected components (for comparison)
    """
    cfg = cfg or ClusterConfig()
    ids = ids or {}
    separations = separations or {}
    nodes = list(nodes)
    edges = list(edges)
    cannot: dict = defaultdict(set)
    for a, b in cannot_links:
        cannot[a].add(b)
        cannot[b].add(a)

    labels, flags, removed = {}, defaultdict(list), []
    stats = {"records": len(nodes), "auto_merge_edges": len(edges), "components": 0, "contradictory_components": 0,
             "edges_removed": 0, "unresolved_components": 0}
    by_comp_edges = defaultdict(list)
    comps = components(nodes, edges)
    node_comp = {n: i for i, c in enumerate(comps) for n in c}
    for e in edges:
        by_comp_edges[node_comp[e[0]]].append(e)
    stats["components"] = len(comps)

    def finalize(group: set, tag: str | None = None):
        lab = min(group)
        for n in group:
            labels[n] = lab
        if tag:
            flags[lab].append(tag)
        if len(group) > cfg.max_cluster_size:
            flags[lab].append("oversize")

    for i, comp in enumerate(comps):
        comp_edges = by_comp_edges.get(i, [])
        if not guard or len(comp) == 1 or _find_conflict(comp, ids, cannot, separations) is None:
            finalize(comp)
            continue
        stats["contradictory_components"] += 1
        removals = 0
        stack = [(comp, comp_edges)]
        while stack:
            nodeset, es = stack.pop()
            conflict = _find_conflict(nodeset, ids, cannot, separations) if len(nodeset) > 1 else None
            if conflict is None:
                finalize(nodeset, "split_from_contradictory_group" if len(nodeset) < len(comp) else None)
                continue
            if removals >= cfg.max_removals:
                stats["unresolved_components"] += 1
                for n in nodeset:
                    finalize({n}, "released_unresolved")
                continue
            adj = defaultdict(list)
            for l, r, p in es:
                adj[l].append((r, p))
                adj[r].append((l, p))
            hit = _widest_path_bottleneck(adj, conflict[0], conflict[1])
            if hit is None:  # cannot happen inside one component, but never loop forever
                stats["unresolved_components"] += 1
                for n in nodeset:
                    finalize({n}, "released_unresolved")
                continue
            (a, b), p = hit
            removed.append((a, b, p, conflict[2]))
            removals += 1
            stats["edges_removed"] += 1
            rest = [e for e in es if (min(e[0], e[1]), max(e[0], e[1])) != (a, b)]
            for part in components(nodeset, rest):
                stack.append((part, [e for e in rest if e[0] in part]))
    sizes = defaultdict(int)
    for lab in labels.values():
        sizes[lab] += 1
    stats["clusters"] = len(sizes)
    stats["singletons"] = sum(1 for s in sizes.values() if s == 1)
    stats["largest_cluster"] = max(sizes.values(), default=0)
    stats["oversize_clusters"] = sum(1 for f in flags.values() if "oversize" in f)
    return ClusterResult(labels=labels, stats=stats, flags=dict(flags), removed_edges=removed)
