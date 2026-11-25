from __future__ import annotations

import argparse
import json
import sys

from processor.config import Config
from processor.executor import execute_approved, scan_and_submit
from processor.gphotos import GooglePhotosClient
from processor.onedrive import OneDriveClient
from processor.state_db import StateDB
from processor.worker_client import WorkerClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PhotoCull Ubuntu background processor")
    parser.add_argument("command", choices=["scan", "execute", "status"])
    parser.add_argument("--env-file", default=None, help="Optional .env file path")
    args = parser.parse_args(argv)

    cfg = Config.from_env(args.env_file)
    state_db = StateDB(cfg.local_state_db)

    try:
        if args.command == "status":
            print(json.dumps(state_db.summary_counts(), ensure_ascii=False, indent=2))
            return 0

        onedrive = OneDriveClient(
            client_id=cfg.ms_client_id,
            refresh_token=cfg.ms_refresh_token,
            tenant=cfg.ms_tenant,
            client_secret=cfg.ms_client_secret,
        )
        worker = WorkerClient(cfg.worker_base_url, cfg.processor_token)

        if args.command == "scan":
            summary = scan_and_submit(cfg, state_db, onedrive, worker)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            return 0

        gphotos = None
        if cfg.gphotos_enabled and cfg.google_client_id and cfg.google_refresh_token:
            gphotos = GooglePhotosClient(
                client_id=cfg.google_client_id,
                client_secret=cfg.google_client_secret,
                refresh_token=cfg.google_refresh_token,
            )

        summary = execute_approved(cfg, state_db, onedrive, gphotos, worker)
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 1 if summary.get("failed_count", 0) > 0 else 0
    finally:
        state_db.close()


if __name__ == "__main__":
    sys.exit(main())
