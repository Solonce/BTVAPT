"""Entry point: `studio` (web UI + worker) or `python -m studio`."""
from __future__ import annotations

import argparse
import logging
import os


def main() -> None:
    ap = argparse.ArgumentParser(description="Faceless Studio")
    ap.add_argument("--host", default=os.environ.get("STUDIO_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("STUDIO_PORT", "8765")))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import uvicorn
    uvicorn.run("studio.web:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
