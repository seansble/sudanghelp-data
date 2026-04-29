#!/usr/bin/env python3
"""Poll sudanghelp.co.kr health signals and write a snapshot.

Schema v4 (세션 14, 2026-04-29).

소스 분류:
  daily:    rates·promos (GH Actions cron) +
            exchange-snapshot (CF Worker daily KV) +
            bank-exchange (CF Worker daily; 24h 후 패턴 재평가)
  hourly:   exchange-current (CF Worker 정각 갱신)
  realtime: feedback (CF Worker KV; updated_field=None 으로 freshness 우회)

Verdict 임계값 (group-aware):
  daily:    age <25h  → ok / 25-28h  → stale (warn) / 28h+  → down (fail)
  hourly:   age <90분 → ok / 90-180분 → stale         / 180+ → down
  realtime: HTTP 200  → ok (updated_field=None 으로 freshness 체크 우회)

GH Actions API 연동 (workflow runs):
  source 에 workflow_file 지정 시 last run conclusion 추적.
    last_run.conclusion == "failure"  → cron status=down, note="cron failed"
    last_run age > 25h  OR 누락        → cron status=down, note="cron 미트리거"
  rates·promos 두 source 가 같은 update-rates.yml 사용 → 결과 캐시.

exchange-snapshot 신설 (kv_dated fetcher):
  url_template 의 {date} 를 KST 오늘 날짜로 치환.
  09 KST 이전 = 오늘 스냅샷 미존재 가능 → fetch 실패 (down 처리).

History v3 → v4 backward compat:
  exchange-rates → exchange-current (이름 변경)
  bank-exchange:  hourly → daily (그룹 이동)
  LEGACY_KEY_MAP 으로 yesterday delta 무중단.
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

# ───────── GitHub API (workflow runs) ─────────
GH_REPO = "seansble/Sudanghelp"
GH_TOKEN = os.environ.get("MAIN_REPO_PAT", "")

# ───────── KST helper ─────────
KST = timezone(timedelta(hours=9))

def kst_today_str(now_utc: datetime) -> str:
    """Today's date in KST as YYYY-MM-DD."""
    return now_utc.astimezone(KST).strftime("%Y-%m-%d")


# ───────── Group / Source Config ─────────
# 카드명·verdict·소비처를 한 곳에. 새 source 추가 시 여기만 편집.
GROUPS: dict = {
    "daily": {
        "label": "하루 1회 갱신",
        "description": "GH Actions cron · CF Worker daily snapshot",
        "trigger": "github-actions / cloudflare-worker",
        "history_policy": "full",
        "retention_days": 365,
        "sources": {
            "rates": {
                "label": "정기예금·적금 금리 비교",
                "page_label": "금리 비교 페이지",
                "tech_context": "GitHub Actions cron · 금감원 공시 OpenAPI",
                "data_label": "FSS 예적금 금리 일일 공시",
                "url": "https://sudanghelp.co.kr/compoundcalc/rates/rates.json",
                "page_url": "https://sudanghelp.co.kr/compoundcalc/rates/",
                "fetch": "json",
                "updated_field": "updatedAtIso",
                "items_kind": "rates",
                "workflow_file": "update-rates.yml",
            },
            "promos": {
                "label": "정기예금·적금 SNS 특판",
                "page_label": "금리 비교 페이지 · SNS 특판 탭",
                "tech_context": "네이버 블로그 검색 + 카카오 Local API",
                "data_label": "SNS 적금 특판 (블로거 교차검증)",
                "url": "https://sudanghelp.co.kr/compoundcalc/rates/featured_promos.json",
                "page_url": "https://sudanghelp.co.kr/compoundcalc/rates/#sns-promos",
                "fetch": "json",
                "updated_field": "updatedAt",
                "items_kind": "items",
                "workflow_file": "update-rates.yml",
            },
            "exchange-snapshot": {
                "label": "여행 가계부 (날짜별 환율)",
                "page_label": "여행 가계부",
                "tech_context": "Cloudflare Worker · sudanghelp-rates 일일 KV 스냅샷",
                "data_label": "오늘 KST 날짜의 환율 스냅샷 (49 통화)",
                "url_template": "https://sudanghelp-rates.sehwan4696.workers.dev/rates/{date}",
                "page_url": "https://sudanghelp.co.kr/travel/expenses/",
                "fetch": "kv_dated",
                "updated_field": "savedAt",
                "items_kind": "currencies",
            },
            "bank-exchange": {
                "label": "환전 분석기",
                "page_label": "환전 분석 페이지",
                "tech_context": "Cloudflare Worker · bank-exchange-rates → Supabase",
                "data_label": "시중은행·핀테크 13곳 환율 + 스프레드",
                "url": "https://bank-exchange-rates.sehwan4696.workers.dev/",
                "page_url": "https://sudanghelp.co.kr/travel/exchange-analysis/",
                "fetch": "json",
                "updated_field": "updated_at",   # "YYYY-MM-DD HH:MM:SS" KST naive
                "items_kind": "data_array",
            },
        },
    },
    "hourly": {
        "label": "시간 단위 갱신",
        "description": "Cloudflare Worker · 매시간 정각 갱신",
        "trigger": "cloudflare-worker",
        "history_policy": "metrics_only",
        "retention_days": 30,
        "sources": {
            "exchange-current": {
                "label": "환율 — 환율 계산기·여행 가계부",
                "page_label": "환율 계산기 · 여행 가계부",
                "tech_context": "Cloudflare Worker · sudanghelp-rates hourly",
                "data_label": "현재 환율 (49 통화, 정각 갱신)",
                "url": "https://sudanghelp-rates.sehwan4696.workers.dev/",
                "page_url": "https://sudanghelp.co.kr/travel/exchange-calculator/",
                "fetch": "json",
                "updated_field": "updated_at",   # ISO 8601 UTC
                "items_kind": "currencies",
            },
        },
    },
    "realtime": {
        "label": "실시간 사용자 활동",
        "description": "Cloudflare Worker · KV 즉시 반영",
        "trigger": "cloudflare-worker-kv",
        "history_policy": "none",
        "retention_days": 0,
        "sources": {
            "feedback": {
                "label": "여행 페이지 피드백 (리뷰·좋아요)",
                "page_label": "여행 페이지 전반 · 피드백 위젯",
                "tech_context": "Cloudflare Worker · sudanghelpfeedback (KV)",
                "data_label": "리뷰 · 좋아요/싫어요 · 조회수",
                "url": "https://sudanghelpfeedback.sehwan4696.workers.dev/api/stats",
                "page_url": "https://sudanghelp.co.kr/travel/",
                "fetch": "json",
                "updated_field": None,            # KV 응답에 timestamp 없음 — HTTP 200 = ok
                "items_kind": "feedback_views",
            },
        },
    },
}

# Health thresholds (minutes) — group-aware.
# 의미: ok = 이내면 ok / stale = 이내면 stale(warn) / 이상이면 down (fail).
THRESHOLDS_MIN = {
    "daily":    {"ok": 60 * 25, "stale": 60 * 28},   # 25h / 28h
    "hourly":   {"ok": 90,       "stale": 180},      # 90분 / 180분
    "realtime": {"ok": 0,        "stale": 0},        # 미사용 (updated_field=None 으로 우회)
}

# v3 → v4 legacy key mapping (yesterday delta backward compat).
LEGACY_KEY_MAP = {
    ("hourly", "exchange-current"): ("hourly", "exchange-rates"),  # 이름 변경
    ("daily",  "bank-exchange"):    ("hourly", "bank-exchange"),   # 그룹 이동
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
    """Normalize updatedAt formats to ISO 8601 UTC. Empty string on failure.

    Supports:
    - int / float — Unix timestamp (sec or ms)
    - ISO 8601 with explicit timezone (Z or ±HH:MM)
    - "YYYY-MM-DD HH:MM KST"
    - naive "YYYY-MM-DD HH:MM:SS" / "YYYY-MM-DD HH:MM" — treated as KST
    """
    if s is None or s == "":
        return ""

    if isinstance(s, (int, float)):
        n = float(s)
        if n > 1e12:
            n /= 1000
        try:
            return datetime.fromtimestamp(n, timezone.utc).isoformat()
        except (OverflowError, ValueError, OSError):
            return ""

    s = str(s).strip()
    if not s:
        return ""

    has_explicit_tz = (
        s.endswith("Z")
        or "+" in s[10:]
        or s.count("-") > 2
    )
    if has_explicit_tz:
        try:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass

    if s.endswith(" KST"):
        try:
            dt = datetime.strptime(s[:-4].strip(), "%Y-%m-%d %H:%M")
            return (dt - timedelta(hours=9)).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            pass

    s_norm = s.replace("T", " ").split(".")[0]
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(s_norm, fmt)
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


# v4.3: 2-tier verdict — 시간(freshness) + 내용(content) 분리, 최종은 worst.
# 랭킹: down 이 가장 worst (확실히 망가진 신호 우선). unknown 은 그보다 약함 (모름).
_STATUS_RANK = {"ok": 0, "stale": 1, "unknown": 2, "down": 3, "pending": 99}

def worst_of(*statuses: str) -> str:
    """Return the worst status. ok < stale < unknown < down."""
    return max(statuses, key=lambda s: _STATUS_RANK.get(s, 99))


# ───────── Items counter ─────────
def count_items(data: dict, kind: str) -> int:
    if kind == "rates":
        return len(data.get("deposits", [])) + len(data.get("savings", []))
    if kind == "items":
        return len(data.get("items", []))
    if kind == "currencies":
        return len(data.get("rates", {}))
    if kind == "data_array":
        return len(data.get("data", []))
    if kind == "feedback_views":
        v = data.get("views")
        return int(v) if isinstance(v, (int, float)) else 0
    return 0


# ───────── Workflow runs API (cron health) ─────────
_workflow_cache: dict = {}

def check_workflow_run(workflow_file: str, now: datetime) -> dict:
    """Last run of a GitHub Actions workflow. Cached per workflow_file.

    Output:
      status:      ok | stale | down | unauthorized | unknown
      conclusion:  success | failure | cancelled | None (in_progress)
      run_id, run_url, started_at, age_min, note
    """
    if workflow_file in _workflow_cache:
        return _workflow_cache[workflow_file]

    if not GH_TOKEN:
        result = {"status": "unauthorized", "note": "MAIN_REPO_PAT secret 미설정"}
        _workflow_cache[workflow_file] = result
        return result

    url = (f"https://api.github.com/repos/{GH_REPO}"
           f"/actions/workflows/{urllib.parse.quote(workflow_file)}/runs?per_page=1")
    try:
        body, _ = fetch(url, headers={
            "Authorization": f"Bearer {GH_TOKEN}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        })
        payload = json.loads(body)
        runs = payload.get("workflow_runs", [])
        if not runs:
            result = {"status": "down", "note": "no workflow runs found"}
            _workflow_cache[workflow_file] = result
            return result

        r = runs[0]
        started_at = r.get("run_started_at") or r.get("created_at", "")
        try:
            dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            age = int((now - dt).total_seconds() // 60)
        except ValueError:
            age = None

        conclusion = r.get("conclusion")    # success | failure | cancelled | None (in_progress)

        if conclusion == "failure":
            status, note = "down", "cron failed"
        elif age is not None and age > 60 * 25:
            status, note = "down", "cron 미트리거 (25h+)"
        elif conclusion == "success":
            status = classify_freshness(age, "daily")
            note = None
        else:
            # in_progress / cancelled / null
            status = "stale"
            note = f"conclusion={conclusion}"

        result = {
            "status":      status,
            "conclusion":  conclusion,
            "run_id":      r.get("id"),
            "run_url":     r.get("html_url"),
            "started_at":  started_at,
            "age_min":     age,
        }
        if note:
            result["note"] = note
        _workflow_cache[workflow_file] = result
        return result
    except urllib.error.HTTPError as e:
        result = {"status": "down", "http_status": e.code, "error": str(e)}
        _workflow_cache[workflow_file] = result
        return result
    except Exception as e:
        result = {"status": "down", "error": f"{type(e).__name__}: {e}"}
        _workflow_cache[workflow_file] = result
        return result


# ───────── Per-source pollers ─────────
def poll_json_source(group_key: str, src_cfg: dict, url: str, now: datetime, prev_metrics: dict | None) -> dict:
    """Fetch a JSON URL and produce a unified source result."""
    out: dict = {
        "label":        src_cfg["label"],
        "page_label":   src_cfg["page_label"],
        "tech_context": src_cfg["tech_context"],
        "data_label":   src_cfg["data_label"],
        "url":          url,
        "page_url":     src_cfg["page_url"],
        "status":       "unknown",
        "metrics":      None,
        "freshness":    None,
        "delta":        None,
    }
    try:
        body, _ = fetch(url)
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
            time_status = classify_freshness(age, group_key)
        else:
            # 응답에 timestamp 없음 (e.g. feedback KV) — 시간 검사 미적용
            updated_raw = ""
            updated_iso = ""
            age = None
            time_status = "ok"

        same_as_last_poll = bool(prev_metrics and prev_metrics.get("hash") == sha)

        # 2-tier verdict
        # 1차 (시간): time_status — age vs cadence 임계값
        # 2차 (내용): content_status — items=0 이면 fallback. feedback 만 예외.
        if items == 0 and src_cfg.get("items_kind") != "feedback_views":
            content_status = "down"
            content_reason = "fallback or empty"
        else:
            content_status = "ok"
            content_reason = None

        # 최종: worst(시간, 내용)
        final_status = worst_of(time_status, content_status)

        out["status"]      = final_status
        out["http_status"] = 200
        out["metrics"] = {
            "items":      items,
            "size_bytes": size_bytes,
            "hash":       sha,
        }
        out["freshness"] = {
            "time_status":    time_status,           # 1차
            "updated_at":     updated_iso,
            "updated_at_raw": updated_raw,
            "age_min":        age,
            "fallback":       same_as_last_poll,
        }
        out["content"] = {                           # 2차 (신설)
            "status": content_status,
            "reason": content_reason,
            "items":  items,
        }
        if content_status != "ok":
            out["error"] = content_reason
    except urllib.error.HTTPError as e:
        out["status"]      = "down"
        out["http_status"] = e.code
        out["error"]       = str(e)
    except Exception as e:
        out["status"] = "down"
        out["error"]  = f"{type(e).__name__}: {e}"
    return out


def poll_kv_dated_source(group_key: str, src_cfg: dict, now: datetime, prev_metrics: dict | None) -> dict:
    """exchange-snapshot 등 날짜별 KV 스냅샷 fetcher.

    URL = url_template.format(date=KST today). 09 KST 이전엔 오늘 스냅샷 미존재 가능.
    """
    today_kst = kst_today_str(now)
    url = src_cfg["url_template"].format(date=today_kst)
    return poll_json_source(group_key, src_cfg, url, now, prev_metrics)


def make_pending_source(cfg: dict) -> dict:
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
    """Find source metrics in a snapshot. Handles v3, v4, and v2 (legacy flat)."""
    if not snap:
        return None
    # v4 / v3 lookup
    metrics = (snap.get("groups", {})
                   .get(group_key, {})
                   .get("sources", {})
                   .get(src_key, {})
                   .get("metrics"))
    if metrics:
        return metrics
    # v3 → v4 backward compat (renamed/moved sources)
    legacy = LEGACY_KEY_MAP.get((group_key, src_key))
    if legacy:
        old_g, old_k = legacy
        metrics = (snap.get("groups", {})
                       .get(old_g, {})
                       .get("sources", {})
                       .get(old_k, {})
                       .get("metrics"))
        if metrics:
            return metrics
    # v2 fallback (flat sources)
    flat = snap.get("sources", {}).get(src_key, {})
    if flat.get("hash"):
        return {
            "items":      flat.get("items"),
            "size_bytes": flat.get("size_bytes"),
            "hash":       flat.get("hash"),
        }
    return None


def compute_delta(curr: dict | None, prev_day: dict | None) -> dict | None:
    """v4: hash diff 가 변동 판정의 진실 (size delta 는 환율·은행 등 거의 안 변함)."""
    if not curr or not prev_day:
        return None

    def diff(field: str) -> int | None:
        a = curr.get(field)
        b = prev_day.get(field)
        if a is None or b is None:
            return None
        return a - b

    hash_changed = curr.get("hash") != prev_day.get("hash")

    return {
        "items":               diff("items"),
        "size_bytes":          diff("size_bytes"),
        "hash_changed":        hash_changed,
        "changed_vs_yesterday": hash_changed,   # v4: hash 기준 (v3 size 기준에서 변경)
        "compared_to":         "yesterday",
    }


# ───────── Overall verdict ─────────
def determine_overall(groups_out: dict) -> str:
    """Compute overall health.

    v4 정책:
    - source.status 와 cron.status 만 verdict 근거. fallback flag 는 informational 만.
    - hash 기반 fallback 은 자연 변동 (정각 cron 미스 등) 에서도 trigger 되어 false positive 다발.
      사용자 spec: worker_updated_at 이 임계값 안이면 ok 로 판단.
    """
    cron_statuses: list[str] = []
    src_statuses: list[str] = []

    for group in groups_out.values():
        for src in group.get("sources", {}).values():
            if src.get("status") == "pending":
                continue
            src_statuses.append(src.get("status", "unknown"))
            if src.get("cron"):
                cron_statuses.append(src["cron"].get("status", "unknown"))

    if any(s == "down" for s in cron_statuses):
        return "cron_down"
    if any(s == "down" for s in src_statuses):
        return "down"
    if any(s == "stale" for s in src_statuses):
        # 모든 cron 정상이면 deploy 대기 가능성
        if all(s in ("ok", "unauthorized", "unknown") for s in cron_statuses):
            return "deploy_pending"
        return "stale"
    if any(s == "stale" for s in cron_statuses):
        return "stale"
    if any(s == "unknown" for s in src_statuses):
        return "unknown"
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
            prev_m = get_metrics_from_snapshot(prev_latest, group_key, src_key)

            if fetch_kind == "json":
                src_out = poll_json_source(group_key, src_cfg, src_cfg["url"], now, prev_m)
            elif fetch_kind == "kv_dated":
                src_out = poll_kv_dated_source(group_key, src_cfg, now, prev_m)
            else:
                src_out = make_pending_source(src_cfg)

            if src_out.get("status") == "down":
                any_fetch_error = True

            yday_m = get_metrics_from_snapshot(yesterday, group_key, src_key)
            src_out["delta"] = compute_delta(src_out.get("metrics"), yday_m)

            workflow_file = src_cfg.get("workflow_file")
            if workflow_file:
                src_out["cron"] = check_workflow_run(workflow_file, now)

            group_out["sources"][src_key] = src_out

        groups_out[group_key] = group_out

    snapshot = {
        "schema_version": 4,
        "checked_at":     now.isoformat().replace("+00:00", "Z"),
        "groups":         groups_out,
        "overall":        determine_overall(groups_out),
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
