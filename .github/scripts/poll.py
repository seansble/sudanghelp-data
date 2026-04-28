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

# GitHub API — cron 자체 헬스체크 (라이브 사이트 배포와 무관)
GH_REPO = "seansble/Sudanghelp"
GH_FILE_PATHS = {
    "rates":  "compoundcalc/rates/rates.json",
    "promos": "compoundcalc/rates/featured_promos.json",
}
GH_TOKEN = os.environ.get("MAIN_REPO_PAT", "")


def fetch(url: str, headers: dict | None = None) -> tuple[bytes, dict]:
    h = {"User-Agent": "sudanghelp-monitor-bot/1.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
    return body, dict(r.headers)


def check_cron_via_github(file_key: str, now: datetime) -> dict:
    """GitHub API 로 메인 repo 의 해당 JSON 파일 마지막 커밋 시각 조회.

    cron 자체가 정상 도는지 = 라이브 사이트 배포와 무관하게 판정 가능.
    PAT 미설정 시 status='unauthorized' 반환 (대시보드에서 안내).
    """
    if not GH_TOKEN:
        return {"status": "unauthorized", "note": "MAIN_REPO_PAT secret 미설정"}

    path = GH_FILE_PATHS.get(file_key)
    if not path:
        return {"status": "unknown"}

    url = f"https://api.github.com/repos/{GH_REPO}/commits?path={path}&per_page=1"
    try:
        body, _ = fetch(url, headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        commits = json.loads(body)
        if not commits:
            return {"status": "down", "note": "no commits found"}

        c = commits[0]
        committed_at = c.get("commit", {}).get("committer", {}).get("date", "")
        sha = c.get("sha", "")[:7]
        message = c.get("commit", {}).get("message", "").split("\n")[0]

        try:
            dt = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
            age = int((now - dt).total_seconds() // 60)
        except ValueError:
            age = None

        # cron 은 매일 09:10 KST 실행. 25h 이상 = 진짜 죽음.
        if age is None:
            health = "unknown"
        elif age < 60 * 25:
            health = "ok"
        elif age < 60 * 49:
            health = "stale"
        else:
            health = "down"

        return {
            "status": health,
            "last_commit_at": committed_at,
            "age_min": age,
            "sha": sha,
            "message": message,
        }
    except urllib.error.HTTPError as e:
        return {"status": "down", "http_status": e.code, "error": str(e)}
    except Exception as e:
        return {"status": "down", "error": f"{type(e).__name__}: {e}"}


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
        "schema_version": 2,
        "cron": {},      # cron 자체 헬스 (GitHub API 직접)
        "sources": {},   # 라이브 사이트 freshness (배포 의존)
    }

    any_error = False

    # ---- cron 헬스체크 (GitHub API) ----
    for key in SOURCES.keys():
        snapshot["cron"][key] = check_cron_via_github(key, now)

    # ---- 라이브 사이트 freshness ----
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

    # Overall verdict — cron 우선 판정
    cron_statuses = [c.get("status") for c in snapshot["cron"].values()]
    src_statuses = [s.get("status") for s in snapshot["sources"].values()]

    cron_down = any(s == "down" for s in cron_statuses)
    cron_stale = any(s == "stale" for s in cron_statuses)
    src_down = any(s == "down" for s in src_statuses)
    src_stale = any(s == "stale" for s in src_statuses)
    any_fallback = any(s.get("fallback") for s in snapshot["sources"].values())

    if cron_down:
        snapshot["overall"] = "cron_down"
    elif src_down or src_stale:
        # cron 정상인데 라이브만 stale → 배포 대기
        if all(s in ("ok", "unauthorized", "unknown") for s in cron_statuses):
            snapshot["overall"] = "deploy_pending"
        else:
            snapshot["overall"] = "down"
    elif cron_stale:
        snapshot["overall"] = "stale"
    elif any_fallback:
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
