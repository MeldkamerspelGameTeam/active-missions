#!/usr/bin/env python3
"""Send weekly mission totals to Discord."""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
IDS_PATH = DATA_DIR / "inzetten_ids.json"
REPORTED_PATH = DATA_DIR / "reported_missions.json"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return fallback


def save_reported_state(reported: dict[str, str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with REPORTED_PATH.open("w", encoding="utf-8") as f:
        json.dump(reported, f, ensure_ascii=False, indent=2)


def parse_seen_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "never":
        return None

    try:
        dt = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        return None

    return dt.replace(tzinfo=timezone.utc)


def build_weekly_payload(missions: list[dict[str, Any]], now_utc: datetime, days: int = 30) -> dict[str, Any]:
    total = len(missions)
    inactive_count = sum(1 for item in missions if bool(item.get("inactive", False)))
    active_count = total - inactive_count

    cutoff = now_utc - timedelta(days=days)
    active_seen_last_days = sum(
        1
        for item in missions
        if not bool(item.get("inactive", False))
        and (seen_dt := parse_seen_date(item.get("last_seen"))) is not None
        and cutoff <= seen_dt <= now_utc
    )
    active_seen_pct = (active_seen_last_days / active_count * 100) if active_count else 0.0

    seen_last_days = sum(
        1
        for item in missions
        if (seen_dt := parse_seen_date(item.get("last_seen"))) is not None and cutoff <= seen_dt <= now_utc
    )
    not_seen_count = total - seen_last_days

    return {
        "content": "📊 Weekly mission totals",
        "embeds": [
            {
                "title": "Mission summary",
                "fields": [
                    {"name": "Active missions", "value": str(active_count), "inline": False},
                    {"name": "Inactive missions", "value": str(inactive_count), "inline": False},
                    {
                        "name": "Seen last 30 days",
                        "value": f"{seen_last_days} ({active_seen_pct:.1f}% of active)",
                        "inline": False,
                    },
                    {"name": "Not seen", "value": str(not_seen_count), "inline": False},
                ],
                "color": 3447003,
                "footer": {"text": f"Generated at {now_utc.isoformat()} UTC"},
            }
        ],
    }


def send_discord(payload: dict[str, Any]) -> bool:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        print("DISCORD_WEBHOOK_URL is not configured; skipping weekly summary.")
        return False

    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            webhook_url,
            data=data,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "DiscordBot (https://github.com, 1.0)",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status == 204
    except urllib.error.HTTPError as err:
        print(f"Failed to send weekly summary (HTTP {err.code}): {err.read().decode()}")
        return False
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as err:
        print(f"Failed to send weekly summary: {err}")
        return False


def main() -> None:
    now_utc = datetime.now(timezone.utc)

    reported = load_json(REPORTED_PATH, {})
    if not isinstance(reported, dict):
        reported = {}

    iso_year, iso_week, _ = now_utc.isocalendar()
    marker = f"__weekly_totals_{iso_year:04d}-W{iso_week:02d}"
    if marker in reported:
        print(f"Weekly summary already sent for {marker}; skipping.")
        return

    missions = load_json(IDS_PATH, [])
    if not isinstance(missions, list):
        print("data/inzetten_ids.json is missing or invalid; cannot build weekly summary.")
        return

    payload = build_weekly_payload(missions, now_utc, days=30)
    if send_discord(payload):
        reported[marker] = "weekly_summary"
        save_reported_state({str(k): str(v) for k, v in reported.items()})
        print(f"Sent weekly summary and stored marker {marker}.")
    else:
        print("Weekly summary was not sent.")


if __name__ == "__main__":
    main()
