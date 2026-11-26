from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from processor.config import Config
from processor.executor import execute_approved, scan_and_submit
from processor.gphotos import GooglePhotosClient
from processor.oauth_helper import (
    run_gphotos_auth,
    run_onedrive_device_auth,
    upsert_env_values,
)
from processor.onedrive import OneDriveClient
from processor.state_db import StateDB
from processor.worker_client import WorkerClient


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="PhotoCull Ubuntu background processor")
    parser.add_argument(
        "command",
        choices=["scan", "execute", "status", "auth-onedrive", "auth-gphotos"],
    )
    parser.add_argument("--env-file", default=None, help="Optional .env file path")
    parser.add_argument(
        "--write-env",
        action="store_true",
        help="Write retrieved refresh_token into --env-file (default .env) with mode 0600",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Local callback port for auth-gphotos (default: 8765)",
    )
    parser.add_argument(
        "--manual",
        action="store_true",
        help="Use manual redirect URL paste for auth-gphotos (recommended over SSH)",
    )
    args = parser.parse_args(argv)

    cfg = Config.from_env(args.env_file)

    if args.command == "auth-onedrive":
        refresh_token = run_onedrive_device_auth(
            cfg.ms_client_id,
            tenant=cfg.ms_tenant,
            client_secret=cfg.ms_client_secret,
        )
        if args.write_env:
            target = Path(args.env_file or ".env")
            upsert_env_values(target, {"MS_REFRESH_TOKEN": refresh_token})
            print(f"Saved MS_REFRESH_TOKEN to {target} (mode 0600)")
        else:
            print(f"MS_REFRESH_TOKEN={refresh_token}")
        return 0

    if args.command == "auth-gphotos":
        refresh_token = run_gphotos_auth(
            cfg.google_client_id,
            cfg.google_client_secret,
            port=args.port,
            manual=args.manual,
        )
        if args.write_env:
            target = Path(args.env_file or ".env")
            upsert_env_values(target, {"GOOGLE_REFRESH_TOKEN": refresh_token})
            print(f"Saved GOOGLE_REFRESH_TOKEN to {target} (mode 0600)")
        else:
            print(f"GOOGLE_REFRESH_TOKEN={refresh_token}")
        return 0

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
