#!/usr/bin/env python3
"""Download mission sources and store each result as a JSON file."""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

SOURCES = {
    "inzetten": "https://github.com/Piet2001/Inzetten/raw/refs/heads/main/inzetten.json",
    "missions_log": "https://piet2001-mks.hf.space/missions/log",
}

OUTPUT_DIR = Path(__file__).resolve().parent / "data"


def fetch_text(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; data-fetcher/1.0)",
            "Accept": "application/json, text/plain;q=0.9, */*;q=0.8",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read().decode("utf-8", errors="replace")


def parse_to_json(text: str) -> Any:
    # Prefer regular JSON payloads.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Fall back to JSON Lines if the endpoint returns line-based logs.
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        parsed_lines = []
        for line in lines:
            try:
                parsed_lines.append(json.loads(line))
            except json.JSONDecodeError:
                break
        else:
            return parsed_lines

    # Last resort: keep the raw payload inside a JSON object.
    return {"raw": text}


def save_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)


def save_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write(content)


def sort_missions_log(payload: Any) -> Any:
    if not isinstance(payload, list):
        return payload

    def natural_parts(value: str) -> tuple[Any, ...]:
        parts = re.split(r"(\d+)", value)
        key_parts: list[Any] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key_parts.append((0, int(part)))
            else:
                key_parts.append((1, part.lower()))
        return tuple(key_parts)

    def mission_key(item: Any) -> tuple[int, str]:
        if isinstance(item, dict) and "mission" in item:
            return (0, natural_parts(str(item["mission"])))
        return (1, tuple())

    return sorted(payload, key=mission_key)


def parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None

    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"

    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None

    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def is_inzet_active_now(item: Any, now_utc: datetime) -> bool:
    if not isinstance(item, dict):
        return False

    additional = item.get("additional")
    if not isinstance(additional, dict):
        return True

    start = parse_iso_datetime(additional.get("date_start"))
    end = parse_iso_datetime(additional.get("date_end"))

    # Include always-on missions that do not define an active date window.
    if start is None and end is None:
        return True

    if start is not None and end is not None:
        return start <= now_utc <= end

    return False


def build_last_seen_map(missions_log_payload: Any) -> dict[str, str]:
    if not isinstance(missions_log_payload, list):
        return {}

    last_seen: dict[str, str] = {}
    for item in missions_log_payload:
        if not isinstance(item, dict):
            continue

        mission = item.get("mission")
        seen_date = item.get("date")
        if mission is None or not isinstance(seen_date, str) or not seen_date:
            continue

        mission_key = str(mission)
        current = last_seen.get(mission_key)
        if current is None or seen_date > current:
            last_seen[mission_key] = seen_date

    return last_seen


def extract_inzetten_ids(payload: Any, missions_log_payload: Any) -> Any:
    if not isinstance(payload, list):
        return payload

    now_utc = datetime.now(timezone.utc)
    last_seen_map = build_last_seen_map(missions_log_payload)
    ids = []
    for item in payload:
        if isinstance(item, dict) and "id" in item:
            mission_id = str(item["id"])
            active_now = is_inzet_active_now(item, now_utc)
            additional = item.get("additional") if isinstance(item.get("additional"), dict) else {}
            ids.append(
                {
                    "id": mission_id,
                    "name": item.get("name"),
                    "average_credits": item.get("average_credits"),
                    "last_seen": last_seen_map.get(mission_id, "never"),
                    "inactive": not active_now,
                    "date_start": additional.get("date_start"),
                    "date_end": additional.get("date_end"),
                }
            )
    return ids


def parse_seen_date(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip() or value == "never":
        return None

    text = value.strip()
    try:
        dt = datetime.strptime(text, "%Y-%m-%d")
    except ValueError:
        return None
    return dt.replace(tzinfo=timezone.utc)


def filter_never_or_stale_missions(payload: Any, now_utc: datetime, days: int = 30) -> Any:
    if not isinstance(payload, list):
        return payload

    cutoff = now_utc - timedelta(days=days)
    filtered = []
    for item in payload:
        if not isinstance(item, dict):
            continue

        last_seen = item.get("last_seen")
        if last_seen == "never":
            filtered.append(item)
            continue

        seen_dt = parse_seen_date(last_seen)
        if seen_dt is not None and seen_dt < cutoff:
            filtered.append(item)

    return filtered


def summarize_missions_markdown(title: str, missions: list[dict[str, Any]], widths: list[int] | None = None) -> str:
    headers = ["ID", "Name", "Avg Credits", "Last Seen", "Inactive"]

    rows: list[list[str]] = []
    for item in missions:
        rows.append(
            [
                str(item.get("id", "")).replace("|", "\\|"),
                str(item.get("name", "")).replace("|", "\\|"),
                str(item.get("average_credits", "")).replace("|", "\\|"),
                str(item.get("last_seen", "")).replace("|", "\\|"),
                str(item.get("inactive", "")).replace("|", "\\|"),
            ]
        )

    if widths is None:
        widths = [len(header) for header in headers]
        for row in rows:
            for index, value in enumerate(row):
                widths[index] = max(widths[index], len(value))

    def render_row(values: list[str]) -> str:
        return "| " + " | ".join(values[i].ljust(widths[i]) for i in range(len(values))) + " |"

    align_parts = [
        "-" * widths[0],
        "-" * widths[1],
        "-" * max(3, widths[2] - 1) + ":",
        "-" * widths[3],
        "-" * widths[4],
    ]
    align_row = "| " + " | ".join(align_parts) + " |"

    lines = [f"# {title}", "", f"Count: {len(missions)}", "", render_row(headers), align_row]
    for row in rows:
        lines.append(render_row(row))

    return "\n".join(lines) + "\n"


def summarize_old_seen_markdown(title: str, missions: list[dict[str, Any]]) -> str:
    active_old_seen = [item for item in missions if not bool(item.get("inactive", False))]
    inactive_old_seen = [item for item in missions if bool(item.get("inactive", False))]

    lines = [f"# {title}", "", f"Count: {len(missions)}", ""]
    lines.append(summarize_missions_markdown("Active old-seen missions", active_old_seen).rstrip())
    lines.append("")
    lines.append(summarize_missions_markdown("Inactive old-seen missions", inactive_old_seen).rstrip())
    lines.append("")

    return "\n".join(lines)


def summarize_never_seen_markdown(title: str, missions: list[dict[str, Any]]) -> str:
    active_never_seen = [item for item in missions if not bool(item.get("inactive", False))]
    inactive_never_seen = [item for item in missions if bool(item.get("inactive", False))]

    lines = [f"# {title}", "", f"Count: {len(missions)}", ""]
    lines.append(summarize_missions_markdown("Active never-seen missions", active_never_seen).rstrip())
    lines.append("")
    lines.append(summarize_missions_markdown("Inactive never-seen missions", inactive_never_seen).rstrip())
    lines.append("")

    return "\n".join(lines)


def summarize_inactive_grouped_by_date_markdown(missions: list[dict[str, Any]]) -> str:
    inactive = [item for item in missions if bool(item.get("inactive", False))]

    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for item in inactive:
        start = str(item.get("date_start") or "none")
        end = str(item.get("date_end") or "none")
        key = (start, end)
        groups.setdefault(key, []).append(item)

    sorted_keys = sorted(groups.keys(), key=lambda k: (k[0], k[1]))

    all_group_items = [item for key in sorted_keys for item in groups[key]]
    headers = ["ID", "Name", "Avg Credits", "Last Seen", "Inactive"]
    global_widths = [len(h) for h in headers]
    for item in all_group_items:
        row = [
            str(item.get("id", "")).replace("|", "\\|"),
            str(item.get("name", "")).replace("|", "\\|"),
            str(item.get("average_credits", "")).replace("|", "\\|"),
            str(item.get("last_seen", "")).replace("|", "\\|"),
            str(item.get("inactive", "")).replace("|", "\\|"),
        ]
        for idx, value in enumerate(row):
            global_widths[idx] = max(global_widths[idx], len(value))

    lines = [
        "# Inactive missions grouped by date window",
        "",
        f"Inactive missions: {len(inactive)}",
        f"Date window groups: {len(sorted_keys)}",
        "",
    ]

    for start, end in sorted_keys:
        group_items = groups[(start, end)]
        lines.append(f"## Start: {start} | End: {end} | Count: {len(group_items)}")
        lines.append("")
        lines.append(summarize_missions_markdown("Missions", group_items, global_widths).rstrip())
        lines.append("")

    return "\n".join(lines)


def summarize_readme_markdown(missions: list[dict[str, Any]], never_seen: list[dict[str, Any]], old_seen: list[dict[str, Any]]) -> str:
    total = len(missions)
    now_utc = datetime.now(timezone.utc)
    recent_cutoff = now_utc - timedelta(days=30)

    inactive_count = sum(1 for item in missions if bool(item.get("inactive", False)))
    active_count = total - inactive_count
    seen_last_30_days = sum(
        1
        for item in missions
        if (seen_dt := parse_seen_date(item.get("last_seen"))) is not None and recent_cutoff <= seen_dt <= now_utc
    )

    never_seen_inactive = sum(1 for item in never_seen if bool(item.get("inactive", False)))
    never_seen_active = len(never_seen) - never_seen_inactive

    old_seen_inactive = sum(1 for item in old_seen if bool(item.get("inactive", False)))
    old_seen_active = len(old_seen) - old_seen_inactive

    lines = [
        "# Active Missions Report",
        "",
        "This README is auto-updated by main.py and the GitHub workflow.",
        "",
        "## Summary",
        "",
        "### Overall Metrics",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Total missions | {total} |",
        f"| Active missions | {active_count} |",
        f"| Inactive missions | {inactive_count} |",
        f"| Missions seen in last 30 days | {seen_last_30_days} |",
        "",
        "### Never-Seen Breakdown",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Never seen missions (total) | {len(never_seen)} |",
        f"| Never seen active missions | {never_seen_active} |",
        f"| Never seen inactive missions | {never_seen_inactive} |",
        "",
        "### Old-Seen Breakdown (30+ days)",
        "",
        "| Metric | Value |",
        "| --- | ---: |",
        f"| Old seen missions (older than 30 days) | {len(old_seen)} |",
        f"| Old seen active missions | {old_seen_active} |",
        f"| Old seen inactive missions | {old_seen_inactive} |",
        "",
        "## Generated Files",
        "",
        "- data/inzetten.json",
        "- data/missions_log.json",
        "- data/inzetten_ids.json",
        "- never_seen_missions_summary.md (active and inactive sections)",
        "- old_seen_missions_summary.md",
        "- inactive_missions_grouped_by_date.md",
        "- missions_seen_last_30_days_grouped.md (generated only between 23:00 and 23:59 Dutch time)",
        "",
        "## Workflow Windows",
        "",
        "- The weekly totals Discord summary runs only on Monday between 08:00 and 08:59 Dutch time (Europe/Amsterdam).",
        "- The grouped recent-seen overview script runs only between 23:00 and 23:59 Dutch time (Europe/Amsterdam).",
        "- Outside that window, missions_seen_last_30_days_grouped.md is not regenerated by the workflow.",
        "",
        "## Discord Notifications",
        "",
        "The script sends automated notifications to Discord when mission statuses change. Messages are batched to respect Discord's character limits.",
        "",
        "| Notification Type | Emoji | Color | Trigger | Format |",
        "| --- | --- | --- | --- | --- |",
        "| **Newly Discovered** | ✨ | 🟦 Cyan (16776960) | Never-seen missions detected for the first time | Batch message with all new missions |",
        "| **Never→Active** | 🎯 | 🟨 Yellow (65535) | A never-seen mission gets its first activity | Batch message with transitions |",
        "| **Not Seen 30+ Days** | 🚨 | 🔴 Red (16711680) | Mission hasn't been seen in 30+ days | Batch message with stale missions |",
        "| **Back to Activity** | ✅ | 🟢 Green (65280) | Previously inactive mission (30+ days) is active again | Batch message with resumed missions |",
        "| **Weekly Totals** | 📊 | 🔵 Blue (3447003) | Monday 08:00-08:59 Dutch time | Single summary message with totals |",
        "",
        "### Message Format",
        "",
        "- **Batch messages** (Newly Discovered, Never→Active): Compact format listing multiple missions with ID, name, and credits",
        "  - Example: `101: Mission Name (500 cr)`",
        "  - Automatically splits into multiple messages if exceeding 2000 characters (Part 1/X, Part 2/X, etc.)",
        "",
        "- **Batch messages** (Not Seen 30+ Days, Back to Activity): Compact format listing multiple missions",
        "  - Shows: Mission ID, name, and average credits",
        "",
        "- **Weekly totals message**: Single summary with total counts",
        "  - Shows: Active missions, Inactive missions, Seen last 30 days, Not seen",
        "  - Seen last 30 days includes percentage of active missions in parentheses",
        "",
    ]
    return "\n".join(lines)


def split_never_and_old_missions(payload: Any, now_utc: datetime, days: int = 30) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, list):
        return [], []

    cutoff = now_utc - timedelta(days=days)
    never_seen: list[dict[str, Any]] = []
    old_seen: list[dict[str, Any]] = []

    for item in payload:
        if not isinstance(item, dict):
            continue

        last_seen = item.get("last_seen")
        if last_seen == "never":
            never_seen.append(item)
            continue

        seen_dt = parse_seen_date(last_seen)
        if seen_dt is not None and seen_dt < cutoff:
            old_seen.append(item)

    return never_seen, old_seen


def load_reported_missions() -> dict[str, bool]:
    """Load the set of mission IDs already reported to Discord."""
    reported_path = OUTPUT_DIR / "reported_missions.json"
    if not reported_path.exists():
        return {}

    try:
        with reported_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except (json.JSONDecodeError, OSError):
        pass

    return {}


def save_reported_missions(reported: dict[str, bool]) -> None:
    """Save the set of reported mission IDs to file."""
    reported_path = OUTPUT_DIR / "reported_missions.json"

    def mission_sort_key(mission_id: str) -> tuple[tuple[int, Any], ...]:
        parts = re.split(r"(\d+)", mission_id)
        key_parts: list[tuple[int, Any]] = []
        for part in parts:
            if not part:
                continue
            if part.isdigit():
                key_parts.append((0, int(part)))
            else:
                key_parts.append((1, part.lower()))
        return tuple(key_parts)

    ordered_reported = {key: reported[key] for key in sorted(reported.keys(), key=mission_sort_key)}
    save_json(reported_path, ordered_reported)


def send_discord_webhook(mission_id: str, mission_name: str, average_credits: str, title: str = "🚨 Mission not seen for 30+ days!", color: int = 16711680, last_seen: str = "") -> bool:
    """Send a Discord webhook notification for mission status changes."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        # Silently skip if webhook URL is not configured
        return False

    try:
        fields = []
        if last_seen:
            fields.append({
                "name": "Last Seen",
                "value": last_seen,
                "inline": True,
            })
        fields.append({
            "name": "Average Credits",
            "value": str(average_credits) if average_credits else "Unknown",
            "inline": True,
        })

        payload = {
            "content": title,
            "embeds": [
                {
                    "title": mission_name or f"Mission {mission_id}",
                    "description": f"Mission ID: `{mission_id}`",
                    "fields": fields,
                    "color": color,
                }
            ],
        }

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
            if response.status == 204:
                time.sleep(3)
                return True
            return False
    except urllib.error.HTTPError as err:
        error_body = err.read().decode()
        print(f"Failed to send Discord notification for mission {mission_id} (HTTP {err.code}): {error_body}")
        return False
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as err:
        print(f"Failed to send Discord notification for mission {mission_id}: {err}")
        return False


def report_new_old_seen_missions(old_seen: list[dict[str, Any]], ids_payload: list[dict[str, Any]]) -> None:
    """Report mission status changes: old-seen discoveries and back-to-activity resumptions."""
    reported = load_reported_missions()
    new_old_seen_missions = []
    back_active_missions = []

    # Find newly reported old-seen missions
    for mission in old_seen:
        mission_id = str(mission.get("id", ""))
        if mission_id and mission_id not in reported:
            new_old_seen_missions.append(mission)
            reported[mission_id] = "old_seen"

    # Find missions that were reported as old-seen but are now active again
    old_seen_ids = {str(m.get("id", "")) for m in old_seen}
    for mission in ids_payload:
        mission_id = str(mission.get("id", ""))
        if mission_id in reported and reported[mission_id] == "old_seen" and mission_id not in old_seen_ids:
            back_active_missions.append(mission)
            reported[mission_id] = "back_active"

    # Save before sending so tracking persists even if the process is interrupted
    save_reported_missions(reported)

    # Send one batch message per notification type.
    if new_old_seen_missions:
        send_batch_discord_webhook(new_old_seen_missions, "🚨 Missions not seen for 30+ days!", 16711680)

    if back_active_missions:
        send_batch_discord_webhook(back_active_missions, "✅ Missions back to activity!", 65280)


def send_batch_discord_webhook(missions: list[dict[str, Any]], title: str, color: int) -> None:
    """Send batched Discord webhook notifications, splitting into multiple messages if needed."""
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url or not missions:
        return

    # Discord message limit is 2000 characters for content + embeds
    max_chars = 1900  # Conservative limit to account for formatting
    
    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_size = len(title) + 50  # Base size for title and formatting

    for mission in missions:
        mission_id = str(mission.get("id", ""))
        mission_name = mission.get("name", "")
        average_credits = mission.get("average_credits", "")
        
        # Format: "ID: name (credits cr)\n"
        entry = f"{mission_id}: {mission_name} ({average_credits} cr)\n"
        entry_size = len(entry)

        if current_size + entry_size > max_chars and current_batch:
            # Start a new batch if adding this mission exceeds limit
            batches.append(current_batch)
            current_batch = [mission]
            current_size = len(title) + 50 + entry_size
        else:
            current_batch.append(mission)
            current_size += entry_size

    if current_batch:
        batches.append(current_batch)

    # Send each batch as a separate message
    for batch_index, batch in enumerate(batches):
        mission_lines = []
        for mission in batch:
            mission_id = str(mission.get("id", ""))
            mission_name = mission.get("name", "")
            average_credits = mission.get("average_credits", "")
            mission_lines.append(f"{mission_id}: {mission_name} ({average_credits} cr)")

        description = "\n".join(mission_lines)
        batch_indicator = f" (Part {batch_index + 1}/{len(batches)})" if len(batches) > 1 else ""

        try:
            payload = {
                "content": f"{title}{batch_indicator}",
                "embeds": [
                    {
                        "description": description,
                        "color": color,
                    }
                ],
            }

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
                if response.status == 204:
                    print(f"Sent batch notification (part {batch_index + 1}/{len(batches)}) with {len(batch)} missions")
                    time.sleep(3)
        except urllib.error.HTTPError as err:
            print(f"Failed to send batch Discord notification (HTTP {err.code}): {err.read().decode()}")
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as err:
            print(f"Failed to send batch Discord notification: {err}")


def report_newly_discovered_missions(never_seen: list[dict[str, Any]], ids_payload: list[dict[str, Any]]) -> None:
    """Report newly discovered missions and never-to-active transitions."""
    reported = load_reported_missions()
    new_discoveries = []
    newly_active_missions = []

    # Track current never-seen mission IDs
    never_seen_ids = {str(m.get("id", "")) for m in never_seen}

    # Find newly reported never-seen missions
    for mission in never_seen:
        mission_id = str(mission.get("id", ""))
        if mission_id and mission_id not in reported:
            new_discoveries.append(mission)
            reported[mission_id] = "newly_discovered"

    # If a mission is discovered for the first time and already has a seen date,
    # treat it as a direct never-to-active transition.
    for mission in ids_payload:
        mission_id = str(mission.get("id", ""))
        if not mission_id or mission_id in reported:
            continue

        last_seen = mission.get("last_seen")
        if isinstance(last_seen, str) and last_seen != "never":
            newly_active_missions.append(mission)
            reported[mission_id] = "newly_active"

    # Find missions that were never-seen but now have activity (no longer in never_seen list)
    for mission in ids_payload:
        mission_id = str(mission.get("id", ""))
        if mission_id in reported and reported[mission_id] == "newly_discovered" and mission_id not in never_seen_ids:
            newly_active_missions.append(mission)
            reported[mission_id] = "newly_active"

    # Send batch notification for newly discovered missions
    # Save before sending so tracking persists even if the process is interrupted
    save_reported_missions(reported)

    if new_discoveries:
        send_batch_discord_webhook(new_discoveries, "✨ Newly discovered missions!", 16776960)

    # Send batch notification for missions transitioning from never to active
    if newly_active_missions:
        send_batch_discord_webhook(newly_active_missions, "🎯 Never-seen missions now active!", 65535)


def main() -> None:
    payloads: dict[str, Any] = {}

    for name, url in SOURCES.items():
        try:
            text = fetch_text(url)
            payload = parse_to_json(text)
            if name == "missions_log":
                payload = sort_missions_log(payload)

            payloads[name] = payload
            output_path = OUTPUT_DIR / f"{name}.json"
            save_json(output_path, payload)
            print(f"Saved {url} -> {output_path}")
        except urllib.error.URLError as err:
            print(f"Failed to fetch {url}: {err}")
        except OSError as err:
            print(f"Failed to write {name}.json: {err}")

    if "inzetten" in payloads:
        now_utc = datetime.now(timezone.utc)
        ids_output_path = OUTPUT_DIR / "inzetten_ids.json"
        ids_payload = extract_inzetten_ids(payloads["inzetten"], payloads.get("missions_log"))
        save_json(ids_output_path, ids_payload)
        print(f"Saved derived IDs -> {ids_output_path}")

        never_seen, old_seen = split_never_and_old_missions(ids_payload, now_utc, days=30)

        root_dir = Path(__file__).resolve().parent
        never_seen_output = root_dir / "never_seen_missions_summary.md"
        never_seen_md = summarize_never_seen_markdown("Never seen missions", never_seen)
        save_text(never_seen_output, never_seen_md)
        print(f"Saved never-seen summary -> {never_seen_output}")

        old_seen_output = root_dir / "old_seen_missions_summary.md"
        old_seen_md = summarize_old_seen_markdown("Old seen missions (older than 30 days)", old_seen)
        save_text(old_seen_output, old_seen_md)
        print(f"Saved old-seen summary -> {old_seen_output}")

        inactive_grouped_output = root_dir / "inactive_missions_grouped_by_date.md"
        inactive_grouped_md = summarize_inactive_grouped_by_date_markdown(ids_payload)
        save_text(inactive_grouped_output, inactive_grouped_md)
        print(f"Saved inactive grouped report -> {inactive_grouped_output}")

        readme_output = root_dir / "README.md"
        readme_md = summarize_readme_markdown(ids_payload, never_seen, old_seen)
        save_text(readme_output, readme_md)
        print(f"Saved README summary -> {readme_output}")

        # Report mission discoveries to Discord
        report_newly_discovered_missions(never_seen, ids_payload)
        report_new_old_seen_missions(old_seen, ids_payload)


if __name__ == "__main__":
    main()
