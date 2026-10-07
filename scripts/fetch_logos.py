"""Download the brand logos shown on the connection screens into src/dedupe/api/ui/logos/ (run once; the files are committed).

Sources: Simple Icons (CC0 1.0, simpleicons.org) coloured with each brand's official hex from its data file, and devicon (MIT, devicon.dev) for
brands Simple Icons no longer carries (Oracle, SQL Server, AWS, Azure, Salesforce). Logos are trademarks of their owners and are shown only to
identify the product a connector reads from. Every file is checked: an SVG with scripts, event handlers, external references or foreign objects
is refused. Usage: python scripts/fetch_logos.py
"""
import json
import re
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "src" / "dedupe" / "api" / "ui" / "logos"
SI = "https://cdn.jsdelivr.net/npm/simple-icons@16.34.0"
DEV = "https://cdn.jsdelivr.net/gh/devicons/devicon@v2.17.0/icons"
SIMPLE = {"postgresql": "postgresql", "mysql": "mysql", "mariadb": "mariadb", "snowflake": "snowflake", "bigquery": "googlebigquery",
          "databricks": "databricks", "sqlite": "sqlite", "duckdb": "duckdb", "mongodb": "mongodb", "googlesheets": "googlesheets",
          "hubspot": "hubspot", "parquet": "apacheparquet", "json": "json", "xml": "xml", "avro": "apacheavro", "minio": "minio",
          "gcs": "googlecloudstorage", "googlecloud": "googlecloud"}
DEVICON = {"oracle": "oracle/oracle-original.svg", "sqlserver": "microsoftsqlserver/microsoftsqlserver-plain.svg",
           "aws": "amazonwebservices/amazonwebservices-original-wordmark.svg", "azure": "azure/azure-original.svg",
           "azuresql": "azuresqldatabase/azuresqldatabase-original.svg", "salesforce": "salesforce/salesforce-original.svg"}
# Brand colours too pale to read on the light logo tile: Simple Icons allows any single colour, so a dark one is used.
COLOR_OVERRIDE = {"duckdb": "1A1A1A", "gcs": "4285F4"}
# Our own neutral badges (no third-party artwork): file types, and brands without a freely licensed logo (shown as a monogram).
BADGE = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><path d="M14 4h26l12 12v42a2 2 0 0 1-2 2H14a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" fill="#fff" stroke="{c}" stroke-width="3"/><path d="M40 4v12h12" fill="none" stroke="{c}" stroke-width="3"/><rect x="4" y="32" width="48" height="18" rx="3" fill="{c}"/><text x="28" y="45.5" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="{s}" font-weight="700" fill="#fff" text-anchor="middle">{t}</text></svg>'''
MONO = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect x="4" y="4" width="56" height="56" rx="12" fill="{c}"/><text x="32" y="40" font-family="Segoe UI,Helvetica,Arial,sans-serif" font-size="{s}" font-weight="700" fill="#fff" text-anchor="middle">{t}</text></svg>'''
GLYPH = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none" stroke="{c}" stroke-width="4" stroke-linecap="round" stroke-linejoin="round">{d}</svg>'''
BADGES = {"csv": ("CSV", "#0F766E", 13), "tsv": ("TSV", "#0E7490", 13), "xlsx": ("XLSX", "#1D7044", 12), "xls": ("XLS", "#3F6E4E", 13),
          "fixedwidth": ("FIXED", "#57534E", 11), "orc": ("ORC", "#475569", 13), "jsonl": ("JSONL", "#6D28D9", 10), "txt": ("TXT", "#6B7280", 13)}
MONOS = {"db2": ("Db2", "#0F62FE", 22), "dynamics": ("D365", "#002050", 18), "sql": ("SQL", "#334155", 20)}
GLYPHS = {"folder": ("#B45309", '<path d="M6 16a4 4 0 0 1 4-4h14l6 6h24a4 4 0 0 1 4 4v28a4 4 0 0 1-4 4H10a4 4 0 0 1-4-4z" fill="#FDE7C2"/><path d="M6 26h52"/>'),
          "upload": ("#2563EB", '<path d="M20 46h-4a12 12 0 0 1-1-24 16 16 0 0 1 31-3 11 11 0 0 1 2 22h-4" fill="#DBEAFE"/><path d="M32 52V30 M24 38l8-8 8 8"/>'),
          "sftp": ("#0F766E", '<rect x="6" y="10" width="52" height="38" rx="4" fill="#CCFBF1"/><path d="M16 22l8 6-8 6 M28 36h12 M22 56h20 M32 48v8"/>'),
          "restapi": ("#7C3AED", '<path d="M22 10c-6 0-6 4-6 10s-4 8-8 12c4 4 8 6 8 12s0 10 6 10 M42 10c6 0 6 4 6 10s4 8 8 12c-4 4-8 6-8 12s0 10-6 10"/><circle cx="32" cy="32" r="4" fill="#7C3AED"/>'),
          "database": ("#334155", '<ellipse cx="32" cy="14" rx="20" ry="7" fill="#E2E8F0"/><path d="M12 14v36c0 4 9 7 20 7s20-3 20-7V14 M12 26c0 4 9 7 20 7s20-3 20-7 M12 38c0 4 9 7 20 7s20-3 20-7"/>')}
_BAD = re.compile(r"<script|<foreignObject|\bon[a-z]+\s*=|javascript:|(?:xlink:)?href\s*=\s*[\"'](?!#)|<!ENTITY|<!DOCTYPE|@import|url\((?!#)", re.I)


def get(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=60) as r:  # nosec B310 - fixed https CDN addresses
        return r.read()


def safe(svg: str, name: str) -> str:
    if _BAD.search(svg):
        sys.exit(f"refused {name}: the SVG contains active content or external references")
    svg = re.sub(r"<\?xml[^>]*>\s*", "", svg).strip()
    return re.sub(r"<title>.*?</title>", "", svg, flags=re.S)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    data = json.loads(get(f"{SI}/data/simple-icons.json"))
    icons = data if isinstance(data, list) else data.get("icons", [])
    hexes = {}
    for i in icons:
        slug = i.get("slug") or re.sub(r"[^a-z0-9]", "", i["title"].lower().replace("+", "plus").replace(".", "dot").replace("&", "and"))
        hexes[slug] = i["hex"]
    for name, slug in SIMPLE.items():
        svg = safe(get(f"{SI}/icons/{slug}.svg").decode(), name)
        colour = COLOR_OVERRIDE.get(name) or hexes.get(slug, "333333")
        svg = svg.replace("<path ", f'<path fill="#{colour}" ', 1) if "fill=" not in svg else svg
        (OUT / f"{name}.svg").write_text(svg, encoding="utf-8")
        print("simple-icons", name, hexes.get(slug))
    for name, path in DEVICON.items():
        (OUT / f"{name}.svg").write_text(safe(get(f"{DEV}/{path}").decode(), name), encoding="utf-8")
        print("devicon", name)
    for name, (t, c, s) in BADGES.items():
        (OUT / f"{name}.svg").write_text(BADGE.format(t=t, c=c, s=s), encoding="utf-8")
    for name, (t, c, s) in MONOS.items():
        (OUT / f"{name}.svg").write_text(MONO.format(t=t, c=c, s=s), encoding="utf-8")
    for name, (c, d) in GLYPHS.items():
        (OUT / f"{name}.svg").write_text(GLYPH.format(c=c, d=d), encoding="utf-8")
    (OUT / "LICENSES.md").write_text(
        "# Logos on the connection screens\n\n"
        "- Brand logos from Simple Icons (CC0 1.0, https://simpleicons.org): " + ", ".join(sorted(SIMPLE)) + ".\n"
        "- Brand logos from devicon (MIT licence, https://devicon.dev): " + ", ".join(sorted(DEVICON)) + ".\n"
        "- Our own neutral badges (file types; monograms for brands without a freely licensed logo): " + ", ".join(sorted([*BADGES, *MONOS, *GLYPHS])) + ".\n\n"
        "Logos are trademarks of their owners. They are shown only to identify the product a connector reads from (no endorsement implied).\n"
        "Regenerate with `python scripts/fetch_logos.py`.\n", encoding="utf-8")


if __name__ == "__main__":
    main()
