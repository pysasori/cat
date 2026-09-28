"""PwCatBot — підготовка особистої лавки Perfect World через веб-інтерфейс."""
from __future__ import annotations

import argparse
import sys
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", default=8770, type=int)
    result.add_argument("--no-browser", action="store_true")
    return result


def main() -> None:
    args = parser().parse_args()
    if not args.no_browser:
        webbrowser.open(f"http://{args.host}:{args.port}/")
    import uvicorn

    uvicorn.run("app.web:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
