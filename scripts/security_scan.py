"""Run the local security checks: lint, static analysis, dependency vulnerabilities, software bill of materials.

    .venv/Scripts/python scripts/security_scan.py [--sbom data/sbom.cdx.json]

The same checks run in CI (.github/workflows/ci.yml); secret scanning (gitleaks) and the image scan (trivy) run there and in pre-commit.
"""
import argparse
import subprocess
import sys

STEPS = [
    ("ruff", [sys.executable, "-m", "ruff", "check", "src", "tests"]),
    ("bandit", [sys.executable, "-m", "bandit", "-r", "src", "-c", "pyproject.toml", "-q"]),
    ("pip-audit", [sys.executable, "-m", "pip_audit", "--skip-editable"]),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sbom", default="data/sbom.cdx.json")
    a = ap.parse_args()
    failed = []
    for name, cmd in STEPS:
        print(f"== {name}", flush=True)
        if subprocess.run(cmd).returncode != 0:  # nosec B603
            failed.append(name)
    print("== sbom", flush=True)
    if subprocess.run([sys.executable, "-m", "cyclonedx_py", "environment", "-o", a.sbom]).returncode != 0:  # nosec B603
        failed.append("sbom")
    print("FAILED: " + ", ".join(failed) if failed else "all security checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
