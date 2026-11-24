"""dedupe: find duplicate customers in files from a terminal, or run the engine as a headless service.

  dedupe inspect FILE...                 what each column holds and which field it would fill (no data is printed)
  dedupe init FILE... -o job.json        write a job (files, mapping, rules, outputs) to check and edit
  dedupe run FILE... -o OUT              deduplicate: golden records, record map, review pairs ... into OUT
  dedupe run --job job.json -o OUT       the same from an edited job
  dedupe explain OUT PERSON_ID           why that person's records were joined
  dedupe serve [--no-ui]                 the REST API (and the portal unless --no-ui) for other programs

Everything runs on this machine; nothing is sent anywhere. Exit code 0 on success, 2 on a problem you can fix (the message says what).
"""
import argparse
import json
import sys
from pathlib import Path

PRESET_HELP = "careful: rules only (fewest wrong merges, misses many); balanced: rules plus the match score (recommended); catch_more: merges and asks from lower scores"


def _files(a) -> list:
    return [{"path": f, "sheet": a.sheet} if getattr(a, "sheet", None) else f for f in a.files]


def _print_inspect(r: dict) -> None:
    print(f"\n{r['file']}: {len(r['columns'])} columns; each row's id: {r['record_id'] or '(none found: the row number is used)'}")
    if r["mapping"]:
        w = max(len(k) for k in r["mapping"])
        for field, m in sorted(r["mapping"].items()):
            print(f"  {field:<{w}}  <- {m['column']}   ({m['confidence']:.0%} sure, from the {m['why']})")
    if r["unused"]:
        print("  not used: " + ", ".join(f"{c} ({r['types'].get(c) or 'unknown'})" for c in r["unused"]))


def _print_summary(s: dict, out: Path) -> None:
    print(f"\n{s['records']:,} records became {s['people']:,} people ({s['preset']}):")
    print(f"  built from several records: {s['built_from_several_records']:,}")
    print(f"  no match (one record each): {s['no_match']:,}")
    print(f"  pairs merged automatically: {s['merged_pairs']:,}")
    print(f"  pairs for a person to decide: {s['pairs_for_a_person']:,}")
    rejected = {src: v for src, v in (s.get("rejected_values") or {}).items() if v}
    if rejected:
        print("  values rejected by the checks (not used): " + "; ".join(src + ": " + ", ".join(f"{k} {n}" for k, n in v.items()) for src, v in rejected.items()))
    if s.get("rules_not_applied"):
        print("  rules not applied (their field is missing): " + ", ".join(s["rules_not_applied"]))
    print(f"\nwritten to {out}:")
    for name, path in s["files"].items():
        print(f"  {name:<7} {path}")
    print(f"  summary {out / 'summary.json'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dedupe", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("inspect", help="what the engine finds in each file")
    p.add_argument("files", nargs="+")
    p.add_argument("--sheet", help="Excel: the sheet to read (default: the first)")
    p.add_argument("--json", action="store_true", help="print JSON instead of text")
    p = sub.add_parser("init", help="write a job file to check and edit")
    p.add_argument("files", nargs="+")
    p.add_argument("--sheet")
    p.add_argument("-o", "--output", default="job.json")
    p.add_argument("--preset", default="balanced", choices=["careful", "balanced", "catch_more"], help=PRESET_HELP)
    p.add_argument("--format", default="csv", choices=["csv", "xlsx", "parquet", "json"])
    p.add_argument("--outputs", default="golden,map,review", help="comma-separated: golden, map, review, clean, links")
    p = sub.add_parser("run", help="deduplicate files into a folder")
    p.add_argument("files", nargs="*")
    p.add_argument("--job", help="a job file from `dedupe init` (instead of files)")
    p.add_argument("--sheet")
    p.add_argument("-o", "--out", default="dedupe_out", help="the folder for the results (default dedupe_out)")
    p.add_argument("--preset", default="balanced", choices=["careful", "balanced", "catch_more"], help=PRESET_HELP)
    p.add_argument("--format", default="csv", choices=["csv", "xlsx", "parquet", "json"], help="the file type of the results")
    p.add_argument("--outputs", default="golden,map,review", help="comma-separated: golden, map, review, clean, links (see the README)")
    p.add_argument("--rules", help="your own matching rules (JSON) instead of the shipped ones")
    p.add_argument("--golden-rules", help="your own golden-record rules (JSON)")
    p.add_argument("--json", action="store_true", help="print the summary as JSON")
    p.add_argument("-v", "--verbose", action="store_true", help="show the engine's own progress lines")
    p = sub.add_parser("explain", help="why one person's records were joined")
    p.add_argument("out")
    p.add_argument("person_id")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("serve", help="run the REST API (headless with --no-ui)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--no-ui", action="store_true", help="no screens: the REST API only")
    a = ap.parse_args(argv)

    from dedupe import sdk
    try:
        if a.cmd == "inspect":
            found = [sdk.inspect(f, a.sheet) for f in a.files]
            if a.json:
                print(json.dumps(found, indent=2))
            else:
                for r in found:
                    _print_inspect(r)
        elif a.cmd == "init":
            job = sdk.job_for(_files(a), a.preset, a.format, tuple(x.strip() for x in a.outputs.split(",") if x.strip()))
            Path(a.output).write_text(json.dumps(job, indent=2), encoding="utf-8")
            print(f"wrote {a.output}: {len(job['sources'])} source(s). Check each source's 'fields' (field <- column), then: dedupe run --job {a.output} -o OUT")
        elif a.cmd == "run":
            if not a.files and not a.job:
                ap.error("give files to deduplicate, or --job")
            res = sdk.run(_files(a) if a.files else None, out=a.out, preset=a.preset, fmt=a.format, job=a.job, rules=a.rules, golden_rules=a.golden_rules,
                          outputs=tuple(x.strip() for x in a.outputs.split(",") if x.strip()), quiet=not a.verbose)
            if a.json:
                print(json.dumps(res.summary, indent=2, default=str))
            else:
                _print_summary(res.summary, res.out)
        elif a.cmd == "explain":
            r = sdk.explain(a.out, a.person_id)
            if a.json:
                print(json.dumps(r, indent=2, default=str))
            else:
                print(f"{a.person_id}: {r['records']} records")
                for x in r["links"]:
                    how = x["rule"] if x["how"] == "rule" else "match score alone" if x["how"] == "score" else "a person: " + x.get("actor", "")
                    also = f"  (also true: {', '.join(x['also'])})" if x["also"] else ""
                    score = f"  score {x['probability']:.0%}" if x["probability"] is not None else ""
                    print(f"  {' '.join(x['a'])}  +  {' '.join(x['b'])}  <- {how}{score}{also}")
        elif a.cmd == "serve":
            import os
            if a.no_ui:
                os.environ["DEDUPE_UI"] = "0"
            try:
                import uvicorn

                from dedupe.api.app import create_app
                from dedupe.api.settings import ApiSettings
            except ImportError:
                print("dedupe serve needs the API extras: pip install 'dedupe[api]'", file=sys.stderr)
                return 2
            uvicorn.run(create_app(ApiSettings.from_env()), host=a.host, port=a.port, server_header=False, proxy_headers=False, access_log=False)
    except (sdk.SdkError, ValueError, OSError) as e:
        print(f"dedupe: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
