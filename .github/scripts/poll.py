#!/usr/bin/env python3
"""Poll sudanghelp.co.kr health signals and write a snapshot.

Schema v3: groups-based architecture for future expansion.
- daily group: GitHub Actions cron-driven sources (rates, promos)
- hourly group: Cloudflare Worker-driven sources (exchange rates)
- realtime group: KV-backed sources (feedback)

Each source produces a unified shape: {status, metrics, freshness, delta, cron?}
History is stored daily; delta vs yesterday is computed when prior snapshot exists.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

# ───────── Paths ─────────
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
LATEST = os.path.join(ROOT, "status", "latest.json")
HISTORY_DIR = os.path.join(ROOT, "status", "history")

# ───────── GitHub API (cron health) ─────────
GH_REPO = "seansble/Sudanghelp"
GH_TOKEN = os.environ.get("MAIN_REPO_PAT", "")

# ───────── Group / Source Config ─────────
# Pure data — adding/removing sources requires no code changes.
# `fetch` types: "json" (live JSON poll) | "pending" (placeholder, B 패키지)
# `cron_path`: GitHub repo path to track for cron commit health (daily group only)
GROUPS: dict = {
    "daily": {
        "label": "하루 1회 갱신",
        "description": "GitHub Actions cron · 09:10 KST 의도",
        "trigger": "github-actions",
        "history_policy": "full",
        "retention_days": 365,
        "sources": {
            "rates": {
                "label": "금리 비교 (FSS)",
                "page_label": "금리 비교 페이지",
                "tech_context": "GitHub Actions cron · 금감원 공시 OpenAPI",
                "data_label": "FSS 예적금 금리 일일 공시",
                "url": "https://sudanghelp.co.kr/compoundcalc/rates/rates.json",
                "page_url": "https://sudanghelp.co.kr/compoundcalc/rates/",
                "fetch": "json",
                "updated_field": "updatedAtIso",
                "items_kind": "rates",
                "cron_path": "compoundcalc/rates/rates.json",
            },
            "promos": {
                "label": "SNS 화제 특판",
                "page_label": "금리 비교 페이지 · SNS 특판 탭",
                "tech_context": "네이버 블로그 검색 + 카카오 Local API",
                "data_label": "SNS 적금 특판 (블로거 교차검증)",
                "url": "https://sudanghelp.co.kr/compoundcalc/rates/featured_promos.json",
                "page_url": "https://sudanghelp.co.kr/compoundcalc/rates/#sns-promos",
                "fetch": "json",
                "updated_field": "updatedAt",
                "items_kind": "items",
                "cron_path": "compoundcalc/rates/featured_promos.json",
            },
        },
    },
    "hourly": {
        "label": "시간 단위 갱신",
        "description": "Cloudflare Worker · 매시간 fetch",
        "trigger": "cloudflare-worker",
        "history_policy": "metrics_only",
        "retention_days": 30,
        "sources": {
            "exchange-rates": {
                "label": "일반 환율 (USD/JPY/EUR …)",
                "page_label": "환율 계산기 · 여행 비용 페이지",
                "tech_context": "Cloudflare Worker · sudanghelp-rates",
                "data_label": "ExchangeRate API base 변환 (49 통화)",
                "url": "https://sudanghelp-rates.sehwan4696.workers.dev/",
                "page_url": "https://sudanghelp.co.kr/travel/exchange-calculator/",
                "fetch": "json",
                "updated_field": "updated_at",   # ISO 8601 UTC
                "items_kind": "currencies",       # len(rates dict)
            },
            "bank-exchange": {
                "label": "은행별 환율 + 스프레드",
                "page_label": "환전 분석 페이지",
                "tech_context": "Cloudflare Worker · bank-exchange-rates → Supabase",
                "data_label": "은행별 환율 + 스프레드 (현찰/송금)",
                "url": "https://bank-exchange-rates.sehwan4696.workers.dev/",
                "page_url": "https://sudanghelp.co.kr/travel/exchange-analysis/",
                "fetch": "json",
                "updated_field": "updated_at",   # "YYYY-MM-DD HH:MM:SS" (KST naive)
                "items_kind": "data_array",       # len(data array)
            },
        },
    },
    "realtime": {
        "label": "실시간 (On-demand)",
        "description": "Cloudflare Worker · KV 즉시 반영",
        "trigger": "cloudflare-worker-kv",
        "history_policy": "none",
        "retention_days": 0,
        "sources": {
            "feedback": {
                "label": "피드백 위젯",
                "page_label": "여행 페이지 전반 · 피드백 위젯",
                "tech_context": "Cloudflare Worker · sudanghelpfeedback (KV)",
                "data_label": "리뷰 · 좋아요/싫어요 · 조회수",
                "url": "https://sudanghelpfeedback.sehwan4696.workers.dev/api/stats",
                "page_url": "https://sudanghelp.co.kr/travel/",
                "fetch": "json",
                "updated_field": None,            # KV 응답에 timestamp 없음 — HTTP 200 으로 충분
                "items_kind": "feedback_views",   # views 카운트
            },
        },
    },
}

# Health thresholds (minutes) — group-aware
THRESHOLDS_MIN = {
    "daily":    {"ok": 60 * 25, "stale": 60 * 49},
    "hourly":   {"ok": 60 * 2,  "stale": 60 * 6},
    "realtime": {"ok": 5,       "stale": 30},
}


# ───────── Network ─────────
def fetch(url: str, headers: dict | None = None) -> tuple[bytes, dict]:
    h = {"User-Agent": "sudanghelp-monitor-bot/1.0"}
    if headers:
        h.update(headers)
    req = urllib.request.Request(url, headers=h)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read(), dict(r.headers)


# ───────── Time helpers ─────────
def parse_updated(s) -> str:
    """Normalize various updatedAt formats to ISO 8601 UTC. Empty on failure.

    Supports:
    - ISO 8601 (with or without TZ, with or without 'Z')          rates.json
    - "YYYY-MM-DD HH:MM KST"                                       featured_promos.json
    - "YYYY-MM-DD HH:MM:SS" (naive — assumed KST)                  bank-exchange-rates worker
    - int / float — Unix timestamp seconds OR milliseconds         optional fallback
    """
    if s is None or s == "":
        return ""

    # numeric Unix timestamp (sec or ms)
    if isinstance(s, (int, float)):
        n = float(s)
        if n > 1e12:    # ms
            n /= 1000
        try:
            return datetime.fromtimestamp(n, timezone.utc).isoformat()
        except (OverflowError, ValueError, OSError):
            return ""

    s = str(s)

    # ISO 8601
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc).isoformat()
    except ValueError:
        pass

    # "YYYY-MM-DD HH:MM KST"
    if s.endswith(" KST"):
        try:
            dt = datetime.strptime(s[:-4], "%Y-%m-%d %H:%M")
            return (dt - timedelta(hours=9)).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass

    # naive "YYYY-MM-DD HH:MM:SS" — assume KST
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s, fmt)
            return (dt - timedelta(hours=9)).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue

    return ""


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


def classify_freshness(age_min: int | None, group_key: str) -> str:
    """Map age to status using group-specific thresholds."""
    if age_min is None:
        return "unknown"
    th = THRESHOLDS_MIN.get(group_key, THRESHOLDS_MIN["daily"])
    if age_min < th["ok"]:
        return "ok"
    if age_min < th["stale"]:
        return "stale"
    return "down"


# ───────── Items counter ─────────
def count_items(data: dict, kind: str) -> int:
    """Returns a representative item count for the given source kind."""
    if kind == "rates":           # FSS rates.json: deposits + savings
        return len(data.get("deposits", [])) + len(data.get("savings", []))
    if kind == "items":           # featured_promos.json
        return len(data.get("items", []))
    if kind == "currencies":      # sudanghelp-rates worker: rates dict size
        return len(data.get("rates", {}))
    if kind == "data_array":      # bank-exchange-rates worker: data list
        return len(data.get("data", []))
    if kind == "feedback_views":  # sudanghelpfeedback worker: views as proxy
        v = data.get("views")
        return int(v) if isinstance(v, (int, float)) else 0
    return 0


# ───────── Cron health (GitHub API) ─────────
def check_cron(path: str, now: datetime) -> dict:
    """Last commit timestamp on a specific file in the main repo."""
    if not GH_TOKEN:
        return {"status": "unauthorized", "note": "MAIN_REPO_PAT secret 미설정"}

    url = f"https://api.github.com/repos/{GH_REPO}/commits?path={urllib.parse.quote(path)}&per_page=1"
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
        try:
            dt = datetime.fromisoformat(committed_at.replace("Z", "+00:00"))
            age = int((now - dt).total_seconds() // 60)
        except ValueError:
            age = None

        return {
            "status": classify_freshness(age, "daily"),
            "last_commit_at": committed_at,
            "age_min": age,
            "sha": c.get("sha", "")[:7],
            "message": c.get("commit", {}).get("message", "").split("\n")[0],
        }
    except urllib.error.HTTPError as e:
        return {"status": "down", "http_status": e.code, "error": str(e)}
    except Exception as e:
        return {"status": "down", "error": f"{type(e).__name__}: {e}"}


# ───────── Per-source pollers ─────────
def poll_json_source(group_key: str, src_cfg: dict, now: datetime, prev_metrics: dict | None) -> dict:
    """Fetch a JSON URL and produce a unified source result."""
    out: dict = {
        "label":        src_cfg["label"],
        "page_label":   src_cfg["page_label"],
        "tech_context": src_cfg["tech_context"],
        "data_label":   src_cfg["data_label"],
        "url":          src_cfg["url"],
        "page_url":     src_cfg["page_url"],
        "status":       "unknown",
        "metrics":      None,
        "freshness":    None,
        "delta":        None,
    }

    try:
        body, _ = fetch(src_cfg["url"])
        sha = hashlib.sha256(body).hexdigest()
        data = json.loads(body)
        items = count_items(data, src_cfg.get("items_kind", ""))
        size_bytes = len(body)

        updated_field = src_cfg.get("updated_field")
        if updated_field:
            updated_raw_val = data.get(updated_field, "")
            updated_raw = str(updated_raw_val) if updated_raw_val != "" else ""
            updated_iso = parse_updated(updated_raw_val)
            age = age_minutes(updated_iso, now)
            status = classify_freshness(age, group_key)
        else:
            # 응답에 timestamp 없음 (e.g. feedback KV) — HTTP 200 = ok
            updated_raw = ""
            updated_iso = ""
            age = None
            status = "ok"

        same_as_last_poll = bool(prev_metrics and prev_metrics.get("hash") == sha)

        out["status"] = status
        out["http_status"] = 200
        out["metrics"] = {
            "items": items,
            "size_bytes": size_bytes,
            "hash": sha,
        }
        out["freshness"] = {
            "updated_at": updated_iso,
            "updated_at_raw": updated_raw,
            "age_min": age,
            "fallback": same_as_last_poll,
        }
    except urllib.error.HTTPError as e:
        out["status"] = "down"
        out["http_status"] = e.code
        out["error"] = str(e)
    except Exception as e:
        out["status"] = "down"
        out["error"] = f"{type(e).__name__}: {e}"

    return out


def make_pending_source(cfg: dict) -> dict:
    """Placeholder source for B 패키지 미구현 워커들."""
    return {
        "label":        cfg["label"],
        "page_label":   cfg["page_label"],
        "tech_context": cfg["tech_context"],
        "data_label":   cfg["data_label"],
        "page_url":     cfg.get("page_url"),
        "status":       "pending",
        "metrics":      None,
        "freshness":    None,
        "delta":        None,
    }


# ───────── Snapshot loaders ─────────
def load_previous_latest() -> dict:
    if not os.path.exists(LATEST):
        return {}
    try:
        with open(LATEST, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def load_yesterday(now: datetime) -> dict | None:
    """Return yesterday's history snapshot, or None if not yet available."""
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    path = os.path.join(HISTORY_DIR, f"{yesterday}.json")
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def get_metrics_from_snapshot(snap: dict, group_key: str, src_key: str) -> dict | None:
    """Find source metrics in a snapshot. Handles v2 (flat sources) and v3 (groups)."""
    if not snap:
        return None
    # v3
    metrics = (snap.get("groups", {})
                   .get(group_key, {})
                   .get("sources", {})
                   .get(src_key, {})
                   .get("metrics"))
    if metrics:
        return metrics
    # v2 fallback
    legacy = snap.get("sources", {}).get(src_key, {})
    if legacy.get("hash"):
        return {
            "items": legacy.get("items"),
            "size_bytes": legacy.get("size_bytes"),
            "hash": legacy.get("hash"),
        }
    return None


def compute_delta(curr: dict | None, prev_day: dict | None) -> dict | None:
    """Compute item/size/hash delta vs yesterday. None if either side missing."""
    if not curr or not prev_day:
        return None

    def diff(field: str) -> int | None:
        a = curr.get(field)
        b = prev_day.get(field)
        if a is None or b is None:
            return None
        return a - b

    size_delta = diff("size_bytes")
    # 변동 판정 기준: size_bytes (사용자 요청). 0 이면 변동 없음, 0 외 = 변동.
    changed = (size_delta != 0) if size_delta is not None else None

    return {
        "items": diff("items"),
        "size_bytes": size_delta,
        "hash_changed": curr.get("hash") != prev_day.get("hash"),
        "changed_vs_yesterday": changed,
        "compared_to": "yesterday",
    }


# ───────── Overall verdict ─────────
def determine_overall(groups_out: dict) -> str:
    """Compute overall health from non-pending sources across all groups."""
    cron_statuses: list[str] = []
    src_statuses: list[str] = []
    any_fallback = False

    for group in groups_out.values():
        for src in group.get("sources", {}).values():
            if src.get("status") == "pending":
                continue
            if src.get("cron"):
                cron_statuses.append(src["cron"].get("status", "unknown"))
            src_statuses.append(src.get("status", "unknown"))
            fr = src.get("freshness") or {}
            if fr.get("fallback"):
                any_fallback = True

    cron_down  = any(s == "down"  for s in cron_statuses)
    cron_stale = any(s == "stale" for s in cron_statuses)
    src_down   = any(s == "down"  for s in src_statuses)
    src_stale  = any(s == "stale" for s in src_statuses)

    if cron_down:
        return "cron_down"
    if src_down or src_stale:
        if all(s in ("ok", "unauthorized", "unknown") for s in cron_statuses):
            return "deploy_pending"
        return "down"
    if cron_stale:
        return "stale"
    if any_fallback:
        return "fallback"
    return "ok"


# ───────── Main ─────────
def main() -> int:
    now = datetime.now(timezone.utc)
    prev_latest = load_previous_latest()
    yesterday = load_yesterday(now)

    groups_out: dict = {}
    any_fetch_error = False

    for group_key, group_cfg in GROUPS.items():
        group_out = {
            "label":          group_cfg["label"],
            "description":    group_cfg["description"],
            "trigger":        group_cfg["trigger"],
            "history_policy": group_cfg["history_policy"],
            "retention_days": group_cfg["retention_days"],
            "sources":        {},
        }

        for src_key, src_cfg in group_cfg["sources"].items():
            fetch_kind = src_cfg.get("fetch", "pending")

            if fetch_kind == "json":
                prev_m = get_metrics_from_snapshot(prev_latest, group_key, src_key)
                src_out = poll_json_source(group_key, src_cfg, now, prev_m)
                if src_out.get("status") == "down":
                    any_fetch_error = True

                yday_m = get_metrics_from_snapshot(yesterday, group_key, src_key)
                src_out["delta"] = compute_delta(src_out.get("metrics"), yday_m)

                cron_path = src_cfg.get("cron_path")
                if cron_path:
                    src_out["cron"] = check_cron(cron_path, now)
            else:
                src_out = make_pending_source(src_cfg)

            group_out["sources"][src_key] = src_out

        groups_out[group_key] = group_out

    snapshot = {
        "schema_version": 3,
        "checked_at": now.isoformat().replace("+00:00", "Z"),
        "groups": groups_out,
        "overall": determine_overall(groups_out),
    }

    os.makedirs(os.path.dirname(LATEST), exist_ok=True)
    os.makedirs(HISTORY_DIR, exist_ok=True)

    with open(LATEST, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    today = now.strftime("%Y-%m-%d")
    with open(os.path.join(HISTORY_DIR, f"{today}.json"), "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    summary = {
        gk: {sk: s.get("status") for sk, s in g["sources"].items()}
        for gk, g in groups_out.items()
    }
    print(f"[poll] overall={snapshot['overall']} groups={summary}")
    return 1 if any_fetch_error else 0


if __name__ == "__main__":
    sys.exit(main())
