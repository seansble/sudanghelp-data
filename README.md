# sudanghelp-data

Operational ledger and open dataset for [sudanghelp.co.kr](https://sudanghelp.co.kr) — daily snapshots of Korean bank deposit/savings rates, KRW FX rates, and high-yield bank promotions, with explicit fallback tracking.

한국 금융 데이터(예적금 금리·환율·특판) 일별 아카이브 + 운영 상태 로그.

---

## Live status

<!-- badges populated by .github/workflows/mirror-cron.yml on first run -->

| Source                | Status | Last success         | Items | Fallback today |
| :-------------------- | :----- | :------------------- | :---- | :------------- |
| FSS deposit/savings   | —      | —                    | —     | —              |
| Naver blog search     | —      | —                    | —     | —              |
| Kakao Local           | —      | —                    | —     | —              |
| Exchange Worker       | —      | —                    | —     | —              |
| Feedback Worker       | —      | —                    | —     | —              |

Auto-updated daily at 09:10 KST via GitHub Actions. See [`status/latest.json`](status/latest.json) for the machine-readable snapshot.

---

## What this is

Two roles, one repository:

1. **Operational ledger.** Every cron run records whether each upstream signal (FSS API, Naver Blog Search, Kakao Local, Cloudflare Workers) returned fresh data or fell back to the previous snapshot. Detects silent quota exhaustion, schema drift, and partial outages that would otherwise look "green" on the live site.
2. **Open dataset.** The same snapshots, archived per day, form a public time-series of Korean bank rates and KRW FX. Released under CC-BY-4.0 — cite [sudanghelp.co.kr](https://sudanghelp.co.kr) when reused.

Operational visibility is the primary goal; the dataset is a byproduct of running it in public.

---

## Data sources

| Layer        | Provider                                | Frequency        | Auth                       | Notes                                |
| :----------- | :-------------------------------------- | :--------------- | :------------------------- | :----------------------------------- |
| Bank rates   | FSS 금융감독원 공시 API                  | daily 09:10 KST  | `FSS_API_KEY`              | Stdlib only, no pip deps             |
| SNS promos   | Naver Blog Search + Kakao Local         | daily 09:10 KST  | Naver + Kakao keys         | Cross-validated, ≥2 bloggers required |
| FX rates     | Cloudflare Worker (ExchangeRate API)     | hourly           | none (public)              | `sudanghelp-rates.sehwan4696.workers.dev` |
| Feedback     | Cloudflare Worker (KV-backed)            | health ping only | none                       | `sudanghelpfeedback.sehwan4696.workers.dev` |

---

## Repository layout

```
sudanghelp-data/
├── README.md
├── LICENSE                          # CC-BY-4.0 (data)
├── status/
│   ├── latest.json                  # current health snapshot
│   └── history/
│       └── YYYY-MM-DD.json          # daily archive
├── data/
│   ├── rates/
│   │   ├── latest.json
│   │   └── history/YYYY-MM-DD.json
│   ├── promos/
│   │   ├── latest.json
│   │   └── history/YYYY-MM-DD.json
│   └── exchange/
│       ├── latest.json              # latest worker poll
│       └── history/YYYY-MM-DD.json  # daily 23:55 KST closing
└── .github/workflows/
    ├── mirror-cron.yml              # receives push from main repo cron
    └── poll-workers.yml             # hourly Cloudflare Worker health check
```

---

## Schemas

### `status/latest.json`

```json
{
  "generated_at": "2026-04-28T00:10:23Z",
  "run_id": "github_actions_run_id",
  "sources": {
    "fss": {
      "status": "ok",
      "items": 47,
      "duration_ms": 1820,
      "fallback": false
    },
    "naver": {
      "status": "ok",
      "queries": 14,
      "blog_results": 168,
      "fallback": false
    },
    "kakao": {
      "status": "degraded",
      "lookups": 8,
      "matched": 5,
      "fallback": false,
      "note": "3 branches not found in Kakao Local"
    },
    "exchange_worker": {
      "status": "ok",
      "response_ms": 142,
      "data_age_min": 23,
      "checked_at": "2026-04-28T00:08:11Z"
    },
    "feedback_worker": {
      "status": "ok",
      "response_ms": 88
    }
  },
  "outputs": {
    "rates_items": 47,
    "promos_items": 12,
    "promos_tiers": { "hot": 2, "trending": 4, "verified": 6 }
  },
  "reverts": []
}
```

### `data/rates/YYYY-MM-DD.json`

Mirror of `compoundcalc/rates/rates.json` from the main site, plus `_meta.snapshot_date`. Schema is owned by [`fetch_rates.py`](https://github.com/seansble/sudanghelp/blob/master/compoundcalc/rates/scripts/fetch_rates.py) — see the main repo for field semantics.

---

## Fallback semantics

The cron is designed to **never silently degrade**. When an upstream call fails:

| Failure mode                          | Behavior                                          | `status` field           |
| :------------------------------------ | :------------------------------------------------ | :----------------------- |
| FSS API non-200                        | Previous `rates.json` retained                    | `fss.status = "down"`    |
| FSS API returns 0 items                | Previous `rates.json` retained                    | `fss.status = "empty"`   |
| Naver quota exceeded                   | Promo step skipped, previous `featured_promos.json` retained | `naver.status = "down"` |
| Kakao Local partial miss               | Promos still emitted, missing branches dropped    | `kakao.status = "degraded"` |
| `featured_promos.json` items == 0      | Auto-revert via `git checkout`                    | `reverts: ["promos"]`    |

`fallback: true` means the data shown today is **carried over from a previous successful run**, not freshly fetched. Always check this field before treating a snapshot as "today's" data.

---

## Using the data

Pin to `@main` for latest, or to a specific commit SHA for reproducibility:

```bash
# Latest health snapshot
curl https://cdn.jsdelivr.net/gh/seansble/sudanghelp-data@main/status/latest.json

# Bank rates for a specific day
curl https://cdn.jsdelivr.net/gh/seansble/sudanghelp-data@main/data/rates/history/2026-04-28.json

# Reproducible (commit-pinned)
curl https://cdn.jsdelivr.net/gh/seansble/sudanghelp-data@<sha>/data/rates/latest.json
```

`raw.githubusercontent.com` works too but has no CDN — prefer jsdelivr for production.

---

## Update schedule

| Job                | Cron (UTC)    | KST              | Triggers                        |
| :----------------- | :------------ | :--------------- | :------------------------------ |
| `mirror-cron.yml`  | `10 0 * * *`  | 09:10 daily      | Pushed from main repo on success |
| `poll-workers.yml` | `0 * * * *`   | hourly           | Self-scheduled                  |

Source of truth for the bank-rate cron is the main repository: [`seansble/sudanghelp/.github/workflows/update-rates.yml`](https://github.com/seansble/sudanghelp/blob/master/.github/workflows/update-rates.yml). This repo only mirrors the resulting JSON.

---

## Citation

This dataset is released under [CC-BY-4.0](LICENSE). When using the data:

> Source: sudanghelp.co.kr — Korean bank rates and FX dataset.
> Retrieved from https://github.com/seansble/sudanghelp-data, snapshot date YYYY-MM-DD.

BibTeX:

```bibtex
@misc{sudanghelp_data,
  title  = {sudanghelp-data: Korean bank rates and FX time-series},
  author = {sudanghelp.co.kr},
  year   = {2026},
  url    = {https://github.com/seansble/sudanghelp-data}
}
```

---

## License

- **Data** (`status/`, `data/`): [CC-BY-4.0](LICENSE)
- **Workflows** (`.github/workflows/`): MIT

Upstream provider terms still apply — FSS public data, Naver/Kakao API ToS for derived signals.

---

## Related

- Main site: https://sudanghelp.co.kr
- Bank rates page: https://sudanghelp.co.kr/compoundcalc/rates/
- Exchange calculator: https://sudanghelp.co.kr/travel/exchange-calculator/
- Main repository: https://github.com/seansble/sudanghelp

---

## Roadmap

- [ ] First mirror push from main repo cron (wires up `status/latest.json`)
- [ ] Backfill 11 days of `data/rates/history/` from main repo `git log`
- [ ] `poll-workers.yml` hourly Cloudflare Worker health check
- [ ] Shields.io endpoint JSON for live status badges in README header
- [ ] Mermaid time-series chart in README, regenerated daily
- [ ] schema.org `Dataset` JSON-LD on a public landing page (sudanghelp.co.kr/data/)

Status: **v0 — repository scaffolding only.** Workflows not yet active.
