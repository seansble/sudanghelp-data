#!/usr/bin/env python3
"""Poll sudanghelp.co.kr live JSONs and write a minimal status snapshot.

Detects:
- Freshness: how old is `updatedAt` in each JSON
- Fallback: hash unchanged from previous poll = stale data being reserved

Outputs:
- status/latest.json — current snapshot
- status/history/YYYY-MM-DD.json — daily archive (overwritten within same UTC day)
"""

import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LATEST = os.path.join(ROOT, "status", "latest.json")
HISTORY_DIR = os.path.join(ROOT, "status", "history")

SOURCES = {
    "rates":  "https://sudanghelp.co.kr/compoundcalc/rates/rates.json",
    "promos": "https://sudanghelp.co.kr/compoundcalc/rates/featured_promos.json",
}

UPDATED_FIELD = {
    "rates":  "updatedAtIso",   # ISO 8601 with TZ
    "promos": "updatedAt",      # "2026-04-27 12:58 KST"
}

ITEMS_COUNTER = {
    "rates":  lambda d: len(d.get("deposits", [])) + len(d.get("savings", [])),
    "promos": lambda d: len(d.get("items", [])),
}


def fetch(url: str) -> tuple[bytes, dict]:
    req = urllib.request.Request(url, headers={"User-Agent": "sudanghelp-monitor-bot/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
    return body, dict(r.headers)


def parse_updated(s: str) -> str:
    """Normalize updatedAt to ISO 8601 UTC string. Returns input unchanged on failure."""
    if not s:
        return ""
    # Already ISO with TZ
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass
    # "YYYY-MM-DD HH:MM KST"
    try:
        if s.endswith(" KST"):
            base = s[:-4]
            dt = datetime.strptime(base, "%Y-%m-%d %H:%M")
            # KST = UTC+9
            from datetime import timedelta
            dt = dt - timedelta(hours=9)
            return dt.replace(tzinfo=timezone.utc).isoformat()
    except ValueError:
        pass
    return s


def age_minutes(iso_utc: str, now: datetime) -> int | None:
    if not iso_utc:
        return None
    try:
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return int((now - dt).total_seconds() // 60)
    except ValueError:
        return None


def load_previous() -> dict:
    if not os.path.exists(LATEST):
        return {}
    try:
        with open(LATEST, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def main() -> int:
    now = datetime.now(timezone.utc)
    prev = load_previous()

    snapshot: dict = {
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "schema_version": 1,
        "sources": {},
    }

    any_error = False

    for key, url in SOURCES.items():
        prev_src = (prev.get("sources") or {}).get(key) or {}
        prev_hash = prev_src.get("hash")
        prev_updated = prev_src.get("updated_at")

        result: dict = {"url": url}
        try:
            body, headers = fetch(url)
            sha = hashlib.sha256(body).hexdigest()
            data = json.loads(body)

            updated_raw = str(data.get(UPDATED_FIELD[key], ""))
            updated_iso = parse_updated(updated_raw)
            items = ITEMS_COUNTER[key](data)
            age = age_minutes(updated_iso, now)

            # Fallback heuristic:
            # Same hash AND same updated_at as previous run = no fresh data this poll.
            # Different hash but same updated_at = malformed (shouldn't happen).
            same_content = (prev_hash and prev_hash == sha)
            fallback = bool(same_content)

            # Health classification
            if age is None:
                health = "unknown"
            elif age < 60 * 24:
                health = "ok"
            elif age < 60 * 48:
                health = "stale"
            else:
                health = "down"

            result.update({
                "status": health,
                "http_status": 200,
                "updated_at": updated_iso,
                "updated_at_raw": updated_raw,
                "age_min": age,
                "items": items,
                "hash": sha,
                "fallback": fallback,
                "size_bytes": len(body),
            })
        except urllib.error.HTTPError as e:
            any_error = True
            result.update({
                "status": "down",
                "http_status": e.code,
                "error": str(e),
                "fallback": False,
            })
        except Exception as e:
            any_error = True
            result.update({
                "status": "down",
                "http_status": None,
                "error": f"{type(e).__name__}: {e}",
                "fallback": False,
            })

        snapshot["sources"][key] = result

    # Overall verdict
    statuses = [s.get("status") for s in snapshot["sources"].values()]
    if any(s == "down" for s in statuses):
        snapshot["overall"] = "down"
    elif any(s == "stale" for s in statuses):
        snapshot["overall"] = "stale"
    elif any(s.get("fallback") for s in snapshot["sources"].values()):
        snapshot["overall"] = "fallback"
    else:
        snapshot["overall"] = "ok"

    # Write
    os.makedirs(os.path.dirname(LATEST), exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)

    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    today = now.strftime("%Y-%m-%d")
    history_file = os.path.join(HISTORY_DIR, f"{today}.json")
    with open(history_file, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    print(f"[poll] overall={snapshot['overall']} sources={ {k: v['status'] for k, v in snapshot['sources'].items()} }")
    return 1 if any_error else 0


if __name__ == "__main__":
    sys.exit(main())
