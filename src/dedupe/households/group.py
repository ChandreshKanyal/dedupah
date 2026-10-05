"""Group related people into households. Connected components over relationship edges with a size guard:
a component larger than `max_size` is re-split with a stricter threshold (chaining protection), and flagged if it
still cannot be brought under the limit."""
from collections import defaultdict


def _components(nodes, edges):
    parent = {n: n for n in nodes}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _ in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[max(ra, rb)] = min(ra, rb)
    groups = defaultdict(set)
    for n in nodes:
        groups[find(n)].add(n)
    return list(groups.values())


def group_households(nodes, edges, threshold: float = 5.0, max_size: int = 10, step: float = 1.0, max_threshold: float = 14.0):
    """
    nodes  iterable of person (cluster) ids; edges: iterable of (a, b, score)
    Returns (household_of: {person: household_id}, info: {...}); household_id = smallest member id.
    """
    nodes = list(nodes)
    strong = [e for e in edges if e[2] >= threshold]
    household_of, flags, escalated = {}, {}, 0
    stack = [(comp, threshold) for comp in _components(nodes, strong)]
    while stack:
        comp, t = stack.pop()
        if len(comp) > max_size and t + step <= max_threshold:
            escalated += 1
            es = [e for e in edges if e[2] >= t + step and e[0] in comp and e[1] in comp]
            stack.extend((c, t + step) for c in _components(comp, es))
            continue
        hid = min(comp)
        for n in comp:
            household_of[n] = hid
        if len(comp) > max_size:
            flags[hid] = "oversize"
    sizes = defaultdict(int)
    for h in household_of.values():
        sizes[h] += 1
    return household_of, {"households": len(sizes), "singletons": sum(1 for s in sizes.values() if s == 1),
                          "largest": max(sizes.values(), default=0), "escalated_components": escalated,
                          "oversize_households": len(flags), "flags": flags}
