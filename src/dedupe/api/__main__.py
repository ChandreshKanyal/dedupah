"""Run the API: `python -m dedupe.api [--host 127.0.0.1] [--port 8000]`. Binds to localhost unless told otherwise; in production run it behind
your TLS-terminating proxy / gateway (set DEDUPE_TRUST_PROXY=true only then)."""
import argparse

import uvicorn

from .app import create_app
from .settings import ApiSettings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    settings = ApiSettings.from_env()
    uvicorn.run(create_app(settings), host=a.host, port=a.port, server_header=False, proxy_headers=False, access_log=False)


if __name__ == "__main__":
    main()
