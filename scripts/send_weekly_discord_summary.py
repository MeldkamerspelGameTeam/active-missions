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

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
IDS_PATH = DATA_DIR / "inzetten_ids.json"
REPORTED_PATH = DATA_DIR / "reported_missions.json"
WEEKLY_STATUS_PATH = DATA_DIR / "weekly_status.json"


def load_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback

    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return fallback


def save_reported_state(reported: dict[str, str]) -> None:
    """Save reported state while preserving all existing mission tracking data."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load existing data to preserve all mission tracking
    existing = {}
    if REPORTED_PATH.exists():
        try:
            with REPORTED_PATH.open("r", encoding="utf-8") as f:
                existing = json.load(f)
                if not isinstance(existing, dict):
                    existing = {}
        except (json.JSONDecodeError, OSError):
            existing = {}
    
    # Merge: update only the keys from 'reported' parameter
    existing.update(reported)
    
    with REPORTED_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


def load_weekly_status() -> dict[str, str]:
    """Load the weekly status tracking from separate file."""
    if not WEEKLY_STATUS_PATH.exists():
        return {}

    try:
        with WEEKLY_STATUS_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass

    return {}


def save_weekly_status(status: dict[str, str]) -> None:
    """Save weekly status to separate file."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    
    # Load existing to preserve all weekly markers
    existing = load_weekly_status()
    existing.update(status)
    
    with WEEKLY_STATUS_PATH.open("w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


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
    never_seen_count = sum(
        1
        for item in missions
        if item.get("last_seen") == "never"
    )
    old_seen_count = sum(
        1
        for item in missions
        if (seen_dt := parse_seen_date(item.get("last_seen"))) is not None and seen_dt < cutoff
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
                ] + ([{"name": "Old seen (30+ days)", "value": str(old_seen_count), "inline": False}] if old_seen_count > 0 else []),
                "color": 3447003,
                "footer": {"text": f"Generated at {now_utc.isoformat()} UTC"},
            }
        ],
    }


def send_discord(payload: dict[str, Any], webhook_url: str) -> bool:
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

    # Load weekly status from separate file
    weekly_status = load_weekly_status()
    
    iso_year, iso_week, _ = now_utc.isocalendar()
    marker = f"__weekly_totals_{iso_year:04d}-W{iso_week:02d}"
    if marker in weekly_status:
        print(f"Weekly summary already sent for {marker}; skipping.")
        return

    missions = load_json(IDS_PATH, [])
    if not isinstance(missions, list):
        print("data/inzetten_ids.json is missing or invalid; cannot build weekly summary.")
        return

    webhook_urls = [
        url
        for url in (os.environ.get("DISCORD_WEBHOOK_URL"), os.environ.get("DISCORD_WEBHOOK_URL_2"))
        if url
    ]
    if not webhook_urls:
        print("No Discord webhook URLs configured; skipping weekly summary.")
        return

    payload = build_weekly_payload(missions, now_utc, days=30)
    second_webhook_url = os.environ.get("DISCORD_WEBHOOK_URL_2")

    results = []
    for url in webhook_urls:
        if url == second_webhook_url:
            payload_for_url = dict(payload)
            payload_for_url["embeds"] = payload["embeds"] + [
                {
                    "description": "[More information](https://discordapp.com/channels/502098937855868949/502102833877745676/1533178428096778452)",
                    "color": 3447003,
                }
            ]
        else:
            payload_for_url = payload
        results.append(send_discord(payload_for_url, url))

    # Require at least one successful send before marking the week as reported.
    sent = any(results)
    if sent:
        save_weekly_status({marker: "weekly_summary"})
        print(f"Sent weekly summary and stored marker {marker}.")
    else:
        print("Weekly summary was not sent.")


if __name__ == "__main__":
    main()
