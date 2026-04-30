#!/usr/bin/env python3
"""
sudanghelp.co.kr 내부 링크 지도 — Tidy Tree 시각화 (D3 d3.tree)
- 분류 기준 = 브레드크럼-구조.md (BreadcrumbList JSON-LD 진실의 출처)
- 본문 in-degree authority 메트릭
- Cross-link (트리 외 인용) 호버/클릭 시 곡선 표시
- /asset/ 는 브레드크럼-구조.md 가 "미존재 (404)" 로 명시 → hub1_missing 시각화
출력: link-map.html (덮어쓰기)
"""
import os
import re
import json
import argparse
from collections import defaultdict, deque
from urllib.parse import urlparse, urljoin

# --src: 메인 repo (Sudanghelp) 소스 디렉토리 (sitemap·HTML·components 위치)
# --out: link-map.html 출력 경로 (기본 = 본 스크립트의 부모 디렉토리)
parser = argparse.ArgumentParser()
parser.add_argument('--src', default=None, help='메인 repo 소스 디렉토리 (default: 스크립트 부모)')
parser.add_argument('--out', default=None, help='출력 HTML 경로')
args = parser.parse_args()

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.abspath(args.src) if args.src else REPO_ROOT
SITEMAP = os.path.join(SRC, 'sitemap.xml')
NAV_FILE = os.path.join(SRC, 'components', 'hub-global-navi.html')
FOOTER_FILE = os.path.join(SRC, 'components', 'hub-global-footer.html')
OUT_HTML = os.path.abspath(args.out) if args.out else os.path.join(REPO_ROOT, 'link-map.html')
ROOT = SRC  # 기존 변수명 호환

# ---------- sitemap ----------
with open(SITEMAP, 'r', encoding='utf-8') as f:
    smtxt = f.read()
SITE_URLS = sorted(set(re.findall(r'<loc>https://sudanghelp\.co\.kr(/[^<]*)</loc>', smtxt)))
URL_SET = set(SITE_URLS)


def url_to_filepath(url):
    if url == '/':
        return os.path.join(ROOT, 'index.html')
    return os.path.join(ROOT, url.strip('/'), 'index.html')


def normalize_href(href, source_url):
    if not href:
        return None
    href = href.strip()
    if href.startswith(('mailto:', 'tel:', 'javascript:', 'data:', '#')):
        return None
    if href.startswith('//'):
        href = 'https:' + href
    if href.startswith(('http://', 'https://')):
        u = urlparse(href)
        if u.netloc not in ('sudanghelp.co.kr', 'www.sudanghelp.co.kr'):
            return ('EXTERNAL', f'{u.scheme}://{u.netloc}')
        path = u.path or '/'
    else:
        if href.startswith('/'):
            path = href.split('?')[0].split('#')[0]
        else:
            base = source_url if source_url.endswith('/') else source_url + '/'
            joined = urljoin('https://sudanghelp.co.kr' + base, href)
            u = urlparse(joined)
            if u.netloc and u.netloc not in ('sudanghelp.co.kr', 'www.sudanghelp.co.kr'):
                return ('EXTERNAL', f'{u.scheme}://{u.netloc}')
            path = u.path or '/'
    path = path.split('?')[0].split('#')[0]
    if not path:
        path = '/'
    if path.endswith('/index.html'):
        path = path[:-len('index.html')]
    if not path.endswith('/') and '.' not in os.path.basename(path):
        path = path + '/'
    return path


def extract_hrefs(filepath):
    if not os.path.exists(filepath):
        return []
    with open(filepath, 'r', encoding='utf-8') as f:
        txt = f.read()
    return re.findall(r'<a\b[^>]*?\shref\s*=\s*["\']([^"\']+)["\']', txt, re.IGNORECASE)


nav_raw = extract_hrefs(NAV_FILE)
footer_raw = extract_hrefs(FOOTER_FILE)
GLOBAL_HREFS = set()
for h in nav_raw + footer_raw:
    n = normalize_href(h, '/')
    if isinstance(n, str):
        GLOBAL_HREFS.add(n)

# ---------- 본문 엣지 추출 ----------
edges_body = []
in_body = defaultdict(int)
out_body = defaultdict(int)
in_global = defaultdict(int)
out_global = defaultdict(int)
external_per_page = defaultdict(list)

for src in SITE_URLS:
    fp = url_to_filepath(src)
    if not os.path.exists(fp):
        continue
    with open(fp, 'r', encoding='utf-8') as f:
        txt = f.read()
    body = re.sub(r'<!--\s*HUB:NAV:START\s*-->[\s\S]*?<!--\s*HUB:NAV:END\s*-->', '', txt, flags=re.IGNORECASE)
    body = re.sub(r'<!--\s*HUB:FOOTER:START\s*-->[\s\S]*?<!--\s*HUB:FOOTER:END\s*-->', '', body, flags=re.IGNORECASE)
    raw_hrefs = re.findall(r'<a\b[^>]*?\shref\s*=\s*["\']([^"\']+)["\']', body, re.IGNORECASE)
    seen = set()
    for h in raw_hrefs:
        n = normalize_href(h, src)
        if n is None:
            continue
        if isinstance(n, tuple) and n[0] == 'EXTERNAL':
            external_per_page[src].append(n[1])
            continue
        if n == src or n in seen:
            continue
        seen.add(n)
    for dst in seen:
        if dst in URL_SET:
            edges_body.append((src, dst))
            out_body[src] += 1
            in_body[dst] += 1

for src in SITE_URLS:
    for dst in GLOBAL_HREFS:
        if dst == src or dst not in URL_SET:
            continue
        out_global[src] += 1
        in_global[dst] += 1


# ---------- BFS depth ----------
adj = defaultdict(set)
for s, d in edges_body:
    adj[s].add(d)
for src in SITE_URLS:
    for dst in GLOBAL_HREFS:
        if dst != src and dst in URL_SET:
            adj[src].add(dst)
depth = {n: None for n in SITE_URLS}
depth['/'] = 0
q = deque(['/'])
while q:
    u = q.popleft()
    for v in adj[u]:
        if v in depth and depth[v] is None:
            depth[v] = depth[u] + 1
            q.append(v)


# ============================================================================
# 분류 — 브레드크럼-구조.md 기준 (BreadcrumbList JSON-LD 진실의 출처)
# ============================================================================

# 1차 허브 (3개)
HUB1 = {'/income/', '/expense/', '/blog/', '/asset/'}

# 1차 허브 — 미존재 (404). 브레드크럼-구조.md 의 "/asset/ 허브 미존재" 표기는 outdated.
# 실측 (2026-04-30): /asset/index.html 35KB 실재 + 라이브 200 OK 확인 → /asset/ 도 정상 hub1.
HUB1_MISSING = set()

# 2차 허브 (8개) — 브레드크럼 4단계 중 3번째 위치
HUB2 = {
    '/work-income/', '/childcare/', '/military/',  # /income/ 자식
    '/loan/', '/tax/', '/travel/',                 # /expense/ 자식
    '/compoundcalc/', '/tax-account/',             # /asset/ 자식 (asset broken)
}

# 명시적 부모 매핑 — 브레드크럼-구조.md "Step 3: 2차 허브 경유 추가" 결과 기반
PARENT_OVERRIDE = {
    # ── 1차 허브 → root ──
    '/income/': '/',
    '/expense/': '/',
    '/blog/': '/',
    '/asset/': '/',              # broken (hub1_missing) 이지만 트리 위치는 root 직속

    # ── 2차 허브 → 1차 허브 ──
    '/work-income/': '/income/',
    '/childcare/': '/income/',
    '/military/': '/income/',
    '/loan/': '/expense/',
    '/tax/': '/expense/',
    '/travel/': '/expense/',
    '/compoundcalc/': '/asset/',     # /asset/ broken
    '/tax-account/': '/asset/',      # /asset/ broken

    # ── /military/ 직속 (브레드크럼 4단계) ──
    '/military/salary/': '/military/',
    '/military/savings/': '/military/',
    '/military/score/': '/military/',

    # ── /work-income/ 직속 ──
    '/unemployment/': '/work-income/',
    '/unemployment/guide/': '/work-income/',  # 사실은 /unemployment/ 산하지만 브레드크럼 기준 work-income

    # ── /childcare/ 직속 ──
    '/parents/': '/childcare/',
    '/care-allowance/': '/childcare/',
    '/child-allowance/': '/childcare/',
    '/familycare/': '/childcare/',
    '/youth/': '/childcare/',  # 청년 — childcare 클러스터 인접 (브레드크럼-구조.md 미명시 → 추정)

    # ── /loan/ 직속 (creditcalc/* 는 loan 아래) ──
    '/creditcalc/emergency-loan/': '/loan/',
    '/creditcalc/prepay-calc/': '/loan/',
    '/creditcalc/step-loan/': '/loan/',

    # ── /tax/ 직속 (additionaltax/* 는 tax 아래) ──
    '/additionaltax/batch-vat-calc/': '/tax/',
    '/additionaltax/supply-calc/': '/tax/',
    '/additionaltax/simple-tax-check/': '/tax/',

    # ── /asset/ broken 직속 leaf ──
    '/coinmore/': '/asset/',

    # ── /compoundcalc/ 직속 ──
    '/compoundcalc/lump-sum/': '/compoundcalc/',
    '/compoundcalc/lumpvsregular/': '/compoundcalc/',
    '/compoundcalc/regular-saving/': '/compoundcalc/',
    '/compoundcalc/goal100m/': '/compoundcalc/',
    '/compoundcalc/rates/': '/compoundcalc/',

    # ── /travel/ 직속 ──
    '/travel/exchange-calculator/': '/travel/',
    '/travel/exchange-analysis/': '/travel/',
    '/travel/expenses/': '/travel/',

    # ── /tax-account/ 직속 ──
    '/tax-account/compare/': '/tax-account/',
    '/tax-account/limit/': '/tax-account/',
    '/tax-account/exit/': '/tax-account/',
    '/tax-account/year-end/': '/tax-account/',

    # ── /expense/ 직속 (2차 허브 없음) ──
    '/electricity/': '/expense/',

    # ── 법적·정보 ──
    '/about/': '/',
    '/contact/': '/',
    '/privacy/': '/',
    '/terms/': '/',
}

# /travel/exchange-calculator/{country}/ × 10국 → /travel/exchange-calculator/
COUNTRY_SLUGS = ['vietnam', 'thailand', 'philippines', 'indonesia', 'malaysia',
                 'cambodia', 'laos', 'myanmar', 'hongkong', 'taiwan']
for slug in COUNTRY_SLUGS:
    PARENT_OVERRIDE[f'/travel/exchange-calculator/{slug}/'] = '/travel/exchange-calculator/'

# /travel/expenses/ticket/ — expenses 자식
PARENT_OVERRIDE.setdefault('/travel/expenses/ticket/', '/travel/expenses/')


# ---------- 가상 노드 ----------
# 사이트 인덱스 페이지가 없어도 시각화상 묶음용으로 띄움
VIRTUAL_NODES = {
    # /asset/ 는 sitemap 에 있으면 실 노드로 처리됨. 없으면 virtual.
}
if '/asset/' not in URL_SET:
    VIRTUAL_NODES['/asset/'] = {
        'name': '자산·투자 (미존재)',
        'parent': '/',
        'group': 'hub1_missing',
        'virtual': True,
    }

ALL_NODES = set(SITE_URLS) | set(VIRTUAL_NODES.keys())


def get_parent(url):
    if url == '/':
        return None
    if url in PARENT_OVERRIDE:
        return PARENT_OVERRIDE[url]
    if url in VIRTUAL_NODES:
        return VIRTUAL_NODES[url]['parent']
    # 블로그 자식 (모두 /blog/ 직속)
    if url.startswith('/blog/') and url != '/blog/':
        return '/blog/'
    # path 기반 fallback
    parts = url.strip('/').split('/')
    if len(parts) <= 1:
        return '/'
    for i in range(len(parts) - 1, 0, -1):
        cand = '/' + '/'.join(parts[:i]) + '/'
        if cand in ALL_NODES:
            return cand
    return '/'


parents = {u: get_parent(u) for u in ALL_NODES}
parents['/'] = None
# parent 가 ALL_NODES 에 없으면 root 로 redirect
for u, p in list(parents.items()):
    if p is not None and p not in ALL_NODES:
        parents[u] = '/'


# ---------- 분류 (색상 그룹) ----------
SATELLITE = {'/compoundcalc/rates/'}
LEGAL = {'/privacy/', '/terms/', '/about/', '/contact/'}


def classify(url):
    if url == '/':
        return 'root'
    if url in HUB1_MISSING:
        return 'hub1_missing'
    if url in HUB1:
        return 'hub1'
    if url in VIRTUAL_NODES:
        return VIRTUAL_NODES[url]['group']
    if url in HUB2:
        return 'hub2'
    if url in SATELLITE:
        return 'satellite'
    if url in LEGAL:
        return 'legal'
    if url.startswith('/blog/') and url != '/blog/':
        return 'blog'
    # 자식이 있는 4단계 leaf (/travel/exchange-calculator/, /travel/expenses/) 도
    # 브레드크럼 레벨로는 다른 형제 계산기 (exchange-analysis 등) 와 동일.
    # → 모두 calc 통일. 자식 있는 hub 역할은 노드 크기·하위 트리로 충분히 구분됨.
    return 'calc'


# ---------- 한국어 라벨 ----------
LABELS = {
    '/': '홈',
    # 1차 허브
    '/income/': '소득·보장',
    '/expense/': '비용·지출',
    '/blog/': '블로그',
    '/asset/': '자산·투자',
    # 2차 허브
    '/work-income/': '고용·소득',
    '/childcare/': '출산·육아',
    '/military/': '군인',
    '/loan/': '대출 계산기',
    '/tax/': '세금 계산기',
    '/travel/': '여행 비용 플래너',
    '/compoundcalc/': '복리 계산기',
    '/tax-account/': '절세계좌',
    # 군인
    '/military/salary/': '군인월급 계산기',
    '/military/savings/': '군적금 계산기',
    '/military/score/': '공군 점수 계산기',
    # 고용·소득
    '/unemployment/': '실업급여 계산기',
    '/unemployment/guide/': '실업급여 가이드',
    # 출산·육아
    '/parents/': '부모급여 계산기',
    '/care-allowance/': '양육수당 가이드',
    '/child-allowance/': '아동수당 가이드',
    '/familycare/': '가족돌봄수당',
    '/youth/': '청년수당',
    # 대출
    '/creditcalc/emergency-loan/': '비상금대출 계산기',
    '/creditcalc/prepay-calc/': '중도상환수수료 계산기',
    '/creditcalc/step-loan/': '체증식 대출 계산기',
    # 세금
    '/additionaltax/batch-vat-calc/': '부가세 계산기',
    '/additionaltax/supply-calc/': '공급가액 계산기',
    '/additionaltax/simple-tax-check/': '간이과세자 확인',
    # 자산
    '/coinmore/': '코인 물타기 계산기',
    # 복리
    '/compoundcalc/lump-sum/': '거치식 복리',
    '/compoundcalc/regular-saving/': '적립식 복리',
    '/compoundcalc/lumpvsregular/': '거치 vs 적립',
    '/compoundcalc/goal100m/': '1억 모으기',
    '/compoundcalc/rates/': '실시간 금리',
    # 절세계좌
    '/tax-account/compare/': '절세계좌 비교',
    '/tax-account/limit/': '한도 계산',
    '/tax-account/exit/': '중도해지',
    '/tax-account/year-end/': '연말정산',
    # 여행
    '/travel/exchange-calculator/': '환율 계산기 (USD)',
    '/travel/exchange-calculator/vietnam/': '베트남',
    '/travel/exchange-calculator/thailand/': '태국',
    '/travel/exchange-calculator/philippines/': '필리핀',
    '/travel/exchange-calculator/indonesia/': '인도네시아',
    '/travel/exchange-calculator/malaysia/': '말레이시아',
    '/travel/exchange-calculator/cambodia/': '캄보디아',
    '/travel/exchange-calculator/laos/': '라오스',
    '/travel/exchange-calculator/myanmar/': '미얀마',
    '/travel/exchange-calculator/hongkong/': '홍콩',
    '/travel/exchange-calculator/taiwan/': '대만',
    '/travel/exchange-analysis/': '환전 분석기',
    '/travel/expenses/': '여행 가계부',
    '/travel/expenses/ticket/': '여행 지출 공유 (티켓)',
    # 직속 leaf
    '/electricity/': '전기요금 계산기',
    # 법적
    '/about/': '소개',
    '/contact/': '문의',
    '/privacy/': '개인정보',
    '/terms/': '약관',
}


def label_for(url):
    if url in VIRTUAL_NODES:
        return VIRTUAL_NODES[url]['name']
    if url in LABELS:
        return LABELS[url]
    if url.startswith('/blog/'):
        slug = url.strip('/').split('/')[-1]
        return slug
    return url.strip('/').split('/')[-1] or url


# ---------- 브레드크럼 경로 (tooltip 용) ----------
def breadcrumb_path(url):
    """홈 › 소득·보장 › 군인 › 군인월급 계산기 형식 문자열"""
    if url == '/':
        return '홈'
    chain = []
    cur = url
    safety = 0
    while cur is not None and safety < 10:
        chain.append(cur)
        cur = parents.get(cur)
        safety += 1
    chain.reverse()
    return ' › '.join(label_for(u) for u in chain)


# ============================================================================
# PageRank simulation (Google 2026 approx)
# - Classic PR + 외부 백링크 seed + 글로벌 nav 디스카운트
# - kjclub 같은 burst pattern (links/targets ratio > 20) 는 weight ×0.5
# - 결과는 정규화 (max=100), 절대값 의미 없음 (상대 순위만)
# ============================================================================
def compute_pagerank(urls, edges_internal, edges_global, seeds, damping=0.85, iterations=20):
    """
    urls: list of all URLs
    edges_internal: [(src, dst, weight)]   본문 링크, weight=1.0
    edges_global:   [(src, dst, weight)]   글로벌 nav/footer, weight=0.1
    seeds: dict url -> 외부 권한 (1 + external backlinks)
    """
    from collections import defaultdict as _dd
    out_w_sum = _dd(float)
    out_neighbors = _dd(list)
    for s, d, w in edges_internal + edges_global:
        out_neighbors[s].append((d, w))
        out_w_sum[s] += w

    pr = {u: seeds.get(u, 1.0) for u in urls}
    base = (1 - damping)

    for _ in range(iterations):
        new_pr = {u: base * seeds.get(u, 1.0) for u in urls}
        for s, neighbors in out_neighbors.items():
            total = out_w_sum[s]
            if total == 0:
                continue
            share = damping * pr.get(s, 0) / total
            for d, w in neighbors:
                new_pr[d] = new_pr.get(d, 0) + share * w
        pr = new_pr

    # 정규화 (max=100)
    max_pr = max(pr.values()) if pr else 1.0
    if max_pr <= 0:
        max_pr = 1.0
    return {u: round(v / max_pr * 100, 1) for u, v in pr.items()}


# ============================================================================
# LEO Readiness Score (0~100) — 페이지별 인용 가능성
# ============================================================================
def compute_leo_score(filepath, url):
    if not os.path.exists(filepath):
        return 0
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            txt = f.read()
    except (OSError, UnicodeDecodeError):
        return 0

    score = 0
    # Structured data 풀니스
    if re.search(r'"@type"\s*:\s*"Dataset"', txt):           score += 25
    if re.search(r'"@type"\s*:\s*"HowTo"', txt):             score += 15
    if re.search(r'"@type"\s*:\s*"FAQPage"', txt):           score += 15
    if re.search(r'"@type"\s*:\s*"SoftwareApplication"', txt): score += 10
    if re.search(r'"@type"\s*:\s*"BreadcrumbList"', txt):    score += 10
    if re.search(r'"speakable"', txt):                        score += 5

    # CORS 공개 데이터 보유 (rates JSON · RSS feed 호스트 페이지)
    if '/compoundcalc/rates/' in url:
        score += 10  # rates.json + feed.xml 보유

    # Freshness: dateModified ≤ 7일 (G·N 합의 — LEO 우선 신호)
    from datetime import datetime as _dt, timezone as _tz, timedelta as _td
    m = re.search(r'"dateModified"\s*:\s*"([^"]+)"', txt)
    if m:
        try:
            iso = m.group(1).replace('Z', '+00:00')
            dt = _dt.fromisoformat(iso)
            now = _dt.now(_tz.utc)
            age = now - dt
            if age < _td(days=7):
                score += 20
            elif age < _td(days=30):
                score += 10  # partial credit
        except (ValueError, TypeError):
            pass

    return min(score, 100)


# ---------- 외부 백링크 (in-bound, GSC export) — 페이지별 ----------
# data/external-target-pages.csv: 타겟 페이지, 수신 링크, 링크된 사이트
# build_tree 가 inbound_per_page 를 참조하므로 트리 빌드 전에 로드.
inbound_per_page = {}  # url -> {'links': N, 'sites': M}
_target_csv = os.path.join(REPO_ROOT, 'data', 'external-target-pages.csv')
if os.path.exists(_target_csv):
    from urllib.parse import urlparse as _urlparse
    with open(_target_csv, 'r', encoding='utf-8-sig') as _f:
        _lines = _f.read().strip().split('\n')
    for _line in _lines[1:]:
        _parts = [p.strip() for p in _line.split(',')]
        if len(_parts) >= 3:
            try:
                _path = _urlparse(_parts[0]).path or '/'
                inbound_per_page[_path] = {
                    'links': int(_parts[1]),
                    'sites': int(_parts[2]),
                }
            except ValueError:
                pass


# ---------- Seed 계산 (외부 백링크 → PageRank 초기 권한) ----------
# Spam suspect 판정: 도메인의 (links/targets) ratio > 20 → SpamBrain bursty pattern → ×0.5
# 단 사용자 데이터에서는 페이지 단위 source domain 매핑 없음 →
# domain 별 spam suspect 비율을 페이지에 일괄 적용 불가.
# 차선책: 페이지의 inbound_links 가 inbound_sites 의 20배 초과 = 한 사이트가 burst 한 케이스 → ×0.5
seeds = {}
for u in SITE_URLS:
    ib = inbound_per_page.get(u, {})
    links = ib.get('links', 0)
    sites = ib.get('sites', 1) or 1
    ratio = links / sites
    weight = 0.5 if ratio > 20 else 1.0  # bursty pattern penalty
    seeds[u] = 1.0 + links * weight

# ---------- PageRank 시뮬레이션 ----------
edges_internal = [(s, d, 1.0) for (s, d) in edges_body]
edges_global_pr = []
for src in SITE_URLS:
    for dst in GLOBAL_HREFS:
        if dst != src and dst in URL_SET:
            edges_global_pr.append((src, dst, 0.1))

pagerank = compute_pagerank(SITE_URLS, edges_internal, edges_global_pr, seeds,
                             damping=0.85, iterations=20)

# ---------- LEO Score ----------
leo_score = {u: compute_leo_score(url_to_filepath(u), u) for u in SITE_URLS}


# ---------- 트리 빌드 ----------
def build_tree(root_url='/'):
    children_of = defaultdict(list)
    for u, p in parents.items():
        if p is not None:
            children_of[p].append(u)

    def make_node(url):
        # legal (about·contact·privacy·terms) 을 root 자식 정렬 시 가장 위로
        # 1차 허브들은 그 아래로 (asset → income → expense → blog 순)
        def sort_key(c):
            g = classify(c)
            if g == 'legal':
                return (0, c)        # legal 최상단
            order = {'hub1': 1, 'hub1_missing': 1}.get(g, 2)
            return (order, c)
        ch = sorted(children_of.get(url, []), key=sort_key)
        ib = inbound_per_page.get(url, {})
        node = {
            'id': url,
            'name': label_for(url),
            'group': classify(url),
            'in_body': in_body[url],
            'out_body': out_body[url],
            'in_global': in_global[url],
            'out_global': out_global[url],
            'depth_bfs': depth.get(url, -1) if depth.get(url) is not None else -1,
            'authority': round(in_body[url] + 0.3 * in_global[url], 1),
            'breadcrumb': breadcrumb_path(url),
            'inbound_links': ib.get('links', 0),       # 외부 백링크 수
            'inbound_sites': ib.get('sites', 0),       # 백링크 보낸 외부 사이트 수
            'pagerank': pagerank.get(url, 0),          # Google PR (정규화 0~100)
            'leo_score': leo_score.get(url, 0),        # LEO readiness (0~100)
        }
        if ch:
            node['children'] = [make_node(c) for c in ch]
        return node

    return make_node(root_url)


tree = build_tree('/')


# ---------- Cross-links ----------
tree_edges = set()
def collect_tree_edges(node, parent=None):
    if parent is not None:
        tree_edges.add((parent, node['id']))
    for c in node.get('children', []):
        collect_tree_edges(c, node['id'])
collect_tree_edges(tree)

cross_links = []
for s, d in edges_body:
    if (s, d) in tree_edges or (d, s) in tree_edges:
        continue
    cross_links.append({'source': s, 'target': d})


# ---------- 통계 ----------
def top_n(d, n=10):
    items = [(k, v) for k, v in d.items() if k in URL_SET]
    items.sort(key=lambda x: (-x[1], x[0]))
    return items[:n]

isolated = [u for u in SITE_URLS if in_body[u] == 0 and u != '/']
strong = [(u, in_body[u]) for u in SITE_URLS if in_body[u] >= 5]
strong.sort(key=lambda x: -x[1])
authority_w = {u: in_body[u] + 0.3 * in_global[u] for u in SITE_URLS}
top_auth = top_n(authority_w, 10)
top_hub = top_n(out_body, 10)

ext_domain_count = defaultdict(int)
for s, lst in external_per_page.items():
    for dom in lst:
        ext_domain_count[dom] += 1

# ---------- 외부 백링크 (in-bound, GSC export) ----------
# data/external-backlinks.csv: 사이트, 링크된 페이지, 타겟 페이지
# Source: Google Search Console → 좌측 "링크" → 외부 링크 → "Top linking sites" CSV export
inbound_backlinks = []
inbound_csv = os.path.join(REPO_ROOT, 'data', 'external-backlinks.csv')
if os.path.exists(inbound_csv):
    with open(inbound_csv, 'r', encoding='utf-8-sig') as f:
        lines = f.read().strip().split('\n')
    for line in lines[1:]:  # skip header
        parts = [p.strip() for p in line.split(',')]
        if len(parts) >= 3:
            try:
                inbound_backlinks.append({
                    'domain': parts[0],
                    'links': int(parts[1]),
                    'targets': int(parts[2]),
                })
            except ValueError:
                pass
    inbound_backlinks.sort(key=lambda x: -x['links'])

inbound_total_links = sum(b['links'] for b in inbound_backlinks)
inbound_total_targets = max((b['targets'] for b in inbound_backlinks), default=0)

# inbound_per_page 는 build_tree 전에 이미 로드됨 (위 참조)
inbound_pages = sorted(
    [{'url': u, 'name': label_for(u), 'links': v['links'], 'sites': v['sites']}
     for u, v in inbound_per_page.items()],
    key=lambda x: -x['links']
)

summary = {
    'total_urls': len(SITE_URLS),
    'edges_body': len(edges_body),
    'cross_links': len(cross_links),
    'isolated_count': len(isolated),
    'top_authority': [{'url': u, 'score': round(authority_w[u], 1),
                       'body': in_body[u], 'name': label_for(u)} for u, _ in top_auth],
    'top_hub': [{'url': u, 'score': out_body[u], 'name': label_for(u)} for u, _ in top_hub],
    'isolated': [{'url': u, 'name': label_for(u)} for u in isolated],
    'strong_cited': [{'url': u, 'count': c, 'name': label_for(u)} for u, c in strong],
    'external_top': [{'domain': d, 'count': c}
                     for d, c in sorted(ext_domain_count.items(), key=lambda x: -x[1])[:12]],
    'inbound_backlinks': inbound_backlinks,
    'inbound_total_links': inbound_total_links,
    'inbound_total_domains': len(inbound_backlinks),
    'inbound_pages': inbound_pages,                  # 페이지별 외부 백링크 TOP
    'top_pagerank': [{'url': u, 'name': label_for(u), 'score': pagerank[u]}
                     for u in sorted(SITE_URLS, key=lambda x: -pagerank.get(x, 0))[:10]],
    'top_leo': [{'url': u, 'name': label_for(u), 'score': leo_score[u]}
                for u in sorted(SITE_URLS, key=lambda x: -leo_score.get(x, 0))[:10]],
}

DATA = {'tree': tree, 'cross_links': cross_links, 'summary': summary}
data_json = json.dumps(DATA, ensure_ascii=False)


# ============================================================================
# HTML
# ============================================================================
HTML_TPL = r"""<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="utf-8">
<title>sudanghelp.co.kr 링크 지도 — Tidy Tree</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<link rel="preconnect" href="https://cdn.jsdelivr.net" crossorigin>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.css">
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
* { box-sizing: border-box; }
html, body { margin:0; padding:0; height:100%; font-family: 'Pretendard', system-ui, -apple-system, sans-serif; background:#0a0a0a; color:#e8e8e8; }
#wrap { display:grid; grid-template-columns: 360px 1fr; height:100vh; }
#side { overflow:auto; padding:18px; border-right:1px solid #1f1f1f; background:#0d0d0d; }
#graph { position:relative; overflow:hidden; }
h1 { font-size:18px; margin:0 0 4px; color:#10b981; letter-spacing:-.01em; }
h2 { font-size:12px; color:#10b981; margin:18px 0 8px; letter-spacing:.06em; text-transform:uppercase; font-weight:600; }
.lead { font-size:12px; color:#9aa0a6; margin-bottom:14px; line-height:1.55; }
.kpi { display:grid; grid-template-columns: repeat(2, 1fr); gap:6px; margin-bottom:14px; }
.kpi div { background:#141414; border:1px solid #1f1f1f; border-radius:8px; padding:8px 10px; }
.kpi b { display:block; font-size:18px; color:#10b981; font-weight:700; }
.kpi span { font-size:11px; color:#9aa0a6; }
ul.list { list-style:none; padding:0; margin:0; font-size:12px; }
ul.list li { padding:4px 6px; border-radius:4px; cursor:pointer; display:flex; justify-content:space-between; gap:8px; line-height:1.4; }
ul.list li:hover { background:#1a1a1a; }
ul.list li .nm { color:#e8e8e8; font-weight:500; }
ul.list li .pth { color:#6b7280; font-family: ui-monospace, SFMono-Regular, monospace; font-size:10px; display:block; }
ul.list li .v { color:#10b981; font-weight:600; flex-shrink:0; font-variant-numeric: tabular-nums; }
.legend { display:flex; flex-wrap:wrap; gap:6px; margin: 4px 0 14px; font-size:11px; }
.legend span { display:inline-flex; align-items:center; gap:4px; padding:2px 6px; background:#141414; border-radius:4px; }
.legend i { display:inline-block; width:9px; height:9px; border-radius:50%; }
.legend i.broken { border:1.5px dashed #dc2626; background:transparent !important; }
#tip { position:absolute; pointer-events:none; background:#0a0a0a; border:1px solid #10b981; padding:8px 10px; font-size:12px; border-radius:6px; max-width:360px; opacity:0; transition:opacity .12s; z-index:50; box-shadow: 0 4px 16px rgba(0,0,0,0.4); }
#tip code { color:#10b981; word-break:break-all; font-size:11px; }
#tip .row { color:#9aa0a6; margin-top:4px; }
#tip .row b { color:#e8e8e8; font-weight:600; }
#tip .crumb { color:#fbbf24; margin-top:6px; padding-top:6px; border-top:1px dashed #1f1f1f; font-size:11px; line-height:1.5; }
.controls { position:absolute; top:12px; right:12px; display:flex; gap:6px; z-index:10; }
.controls button { background:#141414; color:#e8e8e8; border:1px solid #1f1f1f; padding:6px 12px; border-radius:6px; font-size:11px; cursor:pointer; transition: all .15s; }
.controls button:hover { border-color:#10b981; color:#10b981; }
.controls button.active { background:#10b981; color:#0a0a0a; border-color:#10b981; }
.search { width:100%; background:#141414; color:#e8e8e8; border:1px solid #1f1f1f; padding:7px 10px; border-radius:6px; font-size:12px; margin-bottom:10px; }
.search:focus { outline:none; border-color:#10b981; }
.section { margin-top:16px; padding-top:14px; border-top:1px solid #1a1a1a; }
small.warn { color:#f59e0b; font-size:11px; }
small.muted { color:#9aa0a6; font-size:11px; }
.hint { position:absolute; bottom:12px; left:12px; font-size:11px; color:#6b7280; background:rgba(10,10,10,0.85); padding:6px 10px; border-radius:6px; border:1px solid #1a1a1a; z-index:10; }
.hint b { color:#10b981; }
.broken-tag { display:inline-block; padding:1px 5px; font-size:9px; font-weight:700; color:#dc2626; border:1px solid #dc2626; border-radius:3px; margin-left:4px; vertical-align:middle; }

/* Nav tabs (운영 모니터 ↔ Site Link Map 통일 헤더) */
.nav-tabs { display:flex; gap:16px; margin-bottom:12px; padding-bottom:10px; border-bottom:1px solid var(--border); font-size:12px; letter-spacing:0.02em; }
.nav-tabs a { color:var(--text-3); padding:4px 0; border-bottom:1px solid transparent; transition:color 0.15s, border-color 0.15s; }
.nav-tabs a:hover { color:var(--text-2); }
.nav-tabs a.active { color:var(--accent); border-bottom-color:var(--accent); font-weight:500; }
</style>
</head>
<body>
<div id="wrap">
  <aside id="side">
    <nav class="nav-tabs">
      <a href="./">운영 모니터</a>
      <a href="./link-map.html" class="active">Site Link Map</a>
    </nav>
    <h1>링크 지도</h1>
    <div class="lead">sudanghelp.co.kr · <b style="color:#fbbf24">브레드크럼-구조.md</b> 기준 IA 트리 · cross-link 클릭 시 표시</div>
    <input id="q" class="search" placeholder="페이지 검색 (예: travel, 환율)" />

    <div class="kpi">
      <div><b id="k_urls">-</b><span>indexable URL</span></div>
      <div><b id="k_body">-</b><span>본문 엣지</span></div>
      <div><b id="k_cross">-</b><span>cross-link</span></div>
      <div><b id="k_iso">-</b><span>고립 페이지</span></div>
    </div>

    <div class="legend">
      <span><i style="background:#10b981"></i>root</span>
      <span><i style="background:#34d399"></i>1차허브</span>
      <span><i class="broken"></i>1차허브(404)</span>
      <span><i style="background:#22d3ee"></i>2차허브</span>
      <span><i style="background:#f59e0b"></i>위성</span>
      <span><i style="background:#a78bfa"></i>계산기/leaf</span>
      <span><i style="background:#f472b6"></i>블로그</span>
      <span><i style="background:#94a3b8"></i>법적</span>
    </div>
    <small class="muted">노드 크기 = 본문 in-degree (인용 강도) · 호버 시 브레드크럼 경로 표시</small>

    <div class="section">
      <h2>TOP 10 Authority</h2>
      <ul class="list" id="list_auth"></ul>
    </div>

    <div class="section">
      <h2>TOP 10 Hub (발신)</h2>
      <ul class="list" id="list_hub"></ul>
    </div>

    <div class="section">
      <h2>고립 페이지</h2>
      <small class="warn">본문 인용 없음 (전역 nav 만으로 도달)</small>
      <ul class="list" id="list_iso" style="margin-top:6px;"></ul>
    </div>

    <div class="section">
      <h2>강한 자연 인용 (in≥5)</h2>
      <ul class="list" id="list_strong"></ul>
    </div>

    <div class="section">
      <h2>외부 도메인 인용 TOP</h2>
      <p style="color:#6b7280;font-size:11px;margin:0 0 6px;">수당헬프 → 외부 (out-bound)</p>
      <ul class="list" id="list_ext"></ul>
    </div>

    <div class="section">
      <h2>외부 백링크 — 도메인 <span id="k_inbound_total" style="color:#10b981;font-weight:400;font-size:12px"></span></h2>
      <p style="color:#6b7280;font-size:11px;margin:0 0 6px;">외부 → 수당헬프 (in-bound) · GSC</p>
      <ul class="list" id="list_inbound"></ul>
    </div>

    <div class="section">
      <h2>외부 백링크 받는 페이지 TOP</h2>
      <p style="color:#6b7280;font-size:11px;margin:0 0 6px;">노란 후광 = 외부 인용 받는 페이지</p>
      <ul class="list" id="list_inbound_pages"></ul>
    </div>

    <div class="section">
      <h2>🔥 Google PR TOP 10</h2>
      <p style="color:#6b7280;font-size:11px;margin:0 0 6px;">PageRank 시뮬레이션 (정규화 0~100, 상대순위)</p>
      <ul class="list" id="list_pr"></ul>
    </div>

    <div class="section">
      <h2>🤖 LEO Readiness TOP 10</h2>
      <p style="color:#6b7280;font-size:11px;margin:0 0 6px;">LLM 인용 가능성 (Dataset·HowTo·FAQ·freshness)</p>
      <ul class="list" id="list_leo"></ul>
    </div>

    <div class="section" style="background:#0f1a14;border:1px solid #1a3027;padding:10px;border-radius:4px;">
      <h2 style="color:#10b981;margin-bottom:6px;">🔍 Naver 측면 노트</h2>
      <p style="color:#9aa0a6;font-size:11px;line-height:1.5;margin:0;">
        Naver 는 link map 영향 8-12% 미만.<br>
        C-Rank·D.I.A+·P-Rank 모두 <b>콘텐츠 자체</b> 우선.<br>
        한국 SEO grind: ① Yeti 색인 검증 ② naver.com·tistory 백링크 직접 늘리기 ③ HowTo schema 확장
      </p>
    </div>
  </aside>

  <div id="graph">
    <div class="controls">
      <button id="btn_collapse">전체 접기</button>
      <button id="btn_expand">전체 펼치기</button>
      <button id="btn_cross" class="">Cross-link 토글</button>
      <button id="btn_reset">초기 위치</button>
    </div>
    <div class="hint">노드 <b>클릭</b> = cross-link 표시 · <b>더블클릭</b> = 자식 접기/펴기 · 휠 = 줌 · 드래그 = 이동</div>
    <div id="tip"></div>
    <svg id="svg" width="100%" height="100%"></svg>
  </div>
</div>

<script>
const DATA = __DATA__;
const S = DATA.summary;

// KPI
document.getElementById('k_urls').textContent = S.total_urls;
document.getElementById('k_body').textContent = S.edges_body;
document.getElementById('k_cross').textContent = S.cross_links;
document.getElementById('k_iso').textContent = S.isolated_count;

const COLORS = {
  root: '#10b981',
  hub1: '#34d399',
  hub1_missing: '#dc2626',  // 적색 — broken
  hub2: '#22d3ee',
  satellite: '#f59e0b',
  calc: '#a78bfa',
  blog: '#f472b6',
  legal: '#94a3b8'
};

function fillList(elId, items, render) {
  const el = document.getElementById(elId);
  el.innerHTML = '';
  if (!items.length) {
    el.innerHTML = '<li><span class="nm" style="color:#6b7280">(없음)</span></li>';
    return;
  }
  for (const it of items) {
    const li = document.createElement('li');
    li.innerHTML = render(it);
    li.addEventListener('click', () => focusNodeById(it.url));
    el.appendChild(li);
  }
}
fillList('list_auth', S.top_authority,
  it => `<div><span class="nm">${it.name}</span><span class="pth">${it.url}</span></div><span class="v">${it.score}</span>`);
fillList('list_hub', S.top_hub,
  it => `<div><span class="nm">${it.name}</span><span class="pth">${it.url}</span></div><span class="v">${it.score}</span>`);
fillList('list_iso', S.isolated,
  it => `<div><span class="nm">${it.name}</span><span class="pth">${it.url}</span></div>`);
fillList('list_strong', S.strong_cited,
  it => `<div><span class="nm">${it.name}</span><span class="pth">${it.url}</span></div><span class="v">${it.count}</span>`);
fillList('list_ext', S.external_top,
  it => `<div><span class="nm">${it.domain.replace(/^https?:\/\//,'')}</span></div><span class="v">${it.count}</span>`);

// 외부 백링크 (in-bound, GSC) — 도메인별
const inboundList = document.getElementById('list_inbound');
const inboundTotal = document.getElementById('k_inbound_total');
inboundTotal.textContent = `총 ${S.inbound_total_links}개 · ${S.inbound_total_domains}도메인`;
if (S.inbound_backlinks && S.inbound_backlinks.length) {
  inboundList.innerHTML = '';
  for (const b of S.inbound_backlinks) {
    const li = document.createElement('li');
    li.innerHTML = `<div><span class="nm">${b.domain}</span><span class="pth">${b.targets}개 타겟</span></div><span class="v">${b.links}</span>`;
    inboundList.appendChild(li);
  }
} else {
  inboundList.innerHTML = '<li><span class="nm" style="color:#6b7280">(GSC export 없음)</span></li>';
}

// 외부 백링크 받는 페이지 TOP
fillList('list_inbound_pages', S.inbound_pages || [],
  it => `<div><span class="nm">${it.name}</span><span class="pth">${it.url} · ${it.sites}사이트</span></div><span class="v">${it.links}</span>`);

// Google PR TOP 10
fillList('list_pr', S.top_pagerank || [],
  it => `<div><span class="nm">${it.name}</span><span class="pth">${it.url}</span></div><span class="v">${it.score}</span>`);

// LEO Readiness TOP 10
fillList('list_leo', S.top_leo || [],
  it => `<div><span class="nm">${it.name}</span><span class="pth">${it.url}</span></div><span class="v">${it.score}</span>`);

// ---------- Tree layout ----------
const svgEl = document.getElementById('svg');
let W = svgEl.clientWidth, H = svgEl.clientHeight;
const svg = d3.select('#svg');
const root = d3.hierarchy(DATA.tree);

// 노드 크기 = PageRank (Google 효과 권한). PR 0~100 정규화 됨.
const maxPR = d3.max(root.descendants(), d => d.data.pagerank) || 1;
const rScale = d3.scalePow().exponent(1.3).domain([0, maxPR]).range([3, 32]);

// LEO score 0~100 → 청록 후광 색 강도 (0=회색, 100=진한 청록)
const leoOpacity = d3.scaleLinear().domain([0, 100]).range([0.0, 0.55]).clamp(true);

// nodeSize[수직간격, 수평depth간격]. 큰 노드(32px)+라벨 충돌 방지 위해 수직 간격 확대.
// separation 형제·사촌 모두 동일하게 — 같은 depth 노드들이 균일하게 정렬되도록.
const treeLayout = d3.tree()
  .nodeSize([34, 280])
  .separation((a, b) => 1);

// SVG defs — glow filter (외부 백링크 받는 노드 강조)
const defs = svg.append('defs');
const glow = defs.append('filter').attr('id', 'glow-amber')
  .attr('x', '-50%').attr('y', '-50%').attr('width', '200%').attr('height', '200%');
glow.append('feGaussianBlur').attr('stdDeviation', '3').attr('result', 'blur');
const merge = glow.append('feMerge');
merge.append('feMergeNode').attr('in', 'blur');
merge.append('feMergeNode').attr('in', 'SourceGraphic');

const gAll = svg.append('g').attr('transform', `translate(40, ${H/2})`);
const gLinks = gAll.append('g').attr('class', 'links').attr('fill', 'none');
const gCross = gAll.append('g').attr('class', 'cross').attr('fill', 'none');
const gNodes = gAll.append('g').attr('class', 'nodes');

const zoom = d3.zoom().scaleExtent([0.3, 4])
  .on('zoom', ev => gAll.attr('transform', ev.transform));
svg.call(zoom);

let SHOW_CROSS_GLOBAL = false;
let CROSS_FOCUS_ID = null;

function update() {
  treeLayout(root);

  const linkGen = d3.linkHorizontal().x(d => d.y).y(d => d.x);

  const allLinks = root.links();
  const lk = gLinks.selectAll('path').data(allLinks, d => d.target.data.id);
  lk.exit().remove();
  const lkEnter = lk.enter().append('path')
    .attr('fill', 'none');
  lkEnter.merge(lk)
    .attr('stroke', d => {
      // /asset/ broken 으로 향하거나 그 자식 라인은 점선 적색 톤
      if (d.target.data.group === 'hub1_missing' ||
          d.source.data.group === 'hub1_missing') return '#dc2626';
      return '#2a2a2a';
    })
    .attr('stroke-width', d => 0.6 + (3 - d.target.depth) * 0.3)
    .attr('stroke-opacity', d => {
      if (d.target.data.group === 'hub1_missing' ||
          d.source.data.group === 'hub1_missing') return 0.55;
      return 0.8;
    })
    .attr('stroke-dasharray', d => {
      if (d.target.data.group === 'hub1_missing' ||
          d.source.data.group === 'hub1_missing') return '4,3';
      return null;
    })
    .attr('d', linkGen);

  // 노드
  const nd = gNodes.selectAll('g.node').data(root.descendants(), d => d.data.id);
  nd.exit().remove();
  const ndEnter = nd.enter().append('g').attr('class', 'node')
    .style('cursor', 'pointer');

  // 외부 백링크 ring (별도 outer circle) — 본 circle 보다 먼저 append → 뒤에 깔리도록
  ndEnter.append('circle').attr('class', 'backlink-ring');
  ndEnter.append('circle');
  ndEnter.append('text');

  const ndAll = ndEnter.merge(nd);
  ndAll.attr('transform', d => `translate(${d.y},${d.x})`)
    .attr('data-id', d => d.data.id);

  // Outer ring: 외부 백링크 받는 노드만 노란 후광 + glow filter
  ndAll.select('circle.backlink-ring')
    .attr('r', d => {
      if (!d.data.inbound_links) return 0;
      const baseR = rScale(d.data.pagerank) * (d.data.group === 'root' ? 1.5 : 1);
      // 백링크 수에 따라 ring 반지름 확장 (3~12px 추가)
      const extra = Math.min(3 + Math.log10(d.data.inbound_links + 1) * 4, 12);
      return baseR + extra;
    })
    .attr('fill', '#fbbf24')
    .attr('fill-opacity', d => {
      if (!d.data.inbound_links) return 0;
      // 백링크 많으면 더 진하게
      return Math.min(0.18 + Math.log10(d.data.inbound_links + 1) * 0.12, 0.55);
    })
    .attr('stroke', '#fbbf24')
    .attr('stroke-width', d => d.data.inbound_links > 0 ? 1.5 : 0)
    .attr('stroke-opacity', 0.9)
    .attr('filter', d => d.data.inbound_links > 0 ? 'url(#glow-amber)' : null);

  // 본 노드 circle
  // stroke 색·두께 = LEO score (회색 #2a2a2a → 청록 #14b8a6 그라데이션)
  const leoColor = d3.interpolateRgb('#2a2a2a', '#14b8a6');
  ndAll.select('circle:not(.backlink-ring)')
    .attr('r', d => rScale(d.data.pagerank) * (d.data.group === 'root' ? 1.5 : 1))
    .attr('fill', d => d.data.group === 'hub1_missing' ? 'transparent' : (COLORS[d.data.group] || '#94a3b8'))
    .attr('fill-opacity', d => d._collapsed ? 0.5 : 0.9)
    .attr('stroke', d => {
      if (d.data.group === 'hub1_missing') return '#dc2626';
      if (d._collapsed) return '#10b981';
      // LEO score 0 → 어두움, 100 → 청록 #14b8a6
      const leo = d.data.leo_score || 0;
      if (leo === 0) return '#0a0a0a';
      return leoColor(leo / 100);
    })
    .attr('stroke-width', d => {
      if (d.data.group === 'hub1_missing') return 2;
      if (d._collapsed) return 1.5;
      // LEO 비례 stroke 두께: 0 → 1.2, 100 → 5
      const leo = d.data.leo_score || 0;
      return 1.2 + leo / 26;
    })
    .attr('stroke-dasharray', d => d.data.group === 'hub1_missing' ? '3,2' : null);

  ndAll.select('text')
    .attr('dx', d => {
      // 노드 반지름 + 6px padding. 큰 노드(32px)도 라벨 안 가리게.
      const r = rScale(d.data.pagerank) * (d.data.group === 'root' ? 1.5 : 1);
      const isInternal = (d.children || d._children) && d.depth > 0;
      return isInternal ? -(r + 6) : (r + 6);
    })
    .attr('text-anchor', d => (d.children || d._children) && d.depth > 0 ? 'end' : 'start')
    .attr('dy', 4)
    // 텍스트 외곽선 — 링크/타 노드 위에 라벨이 명확히 보이게
    .attr('paint-order', 'stroke')
    .attr('stroke', '#0a0a0a')
    .attr('stroke-width', 3)
    .attr('stroke-opacity', 0.85)
    .attr('font-size', d => {
      if (d.depth === 0) return 18;
      if (d.depth === 1) return 15;
      // PR 기반 (정규화 0~100)
      if (d.data.pagerank >= 30) return 14;
      if (d.data.pagerank >= 15) return 13;
      if (d.data.pagerank >= 5) return 12;
      return 11;
    })
    .attr('font-weight', d => d.depth <= 1 ? 600 : 400)
    .attr('fill', d => {
      if (d.data.group === 'hub1_missing') return '#dc2626';
      if (d.depth === 0) return '#10b981';
      if (d.data.in_body >= 5) return '#e8e8e8';
      return '#9aa0a6';
    })
    .text(d => {
      const lbl = d.data.name;
      const cnt = d.data.in_body;
      return cnt > 0 ? `${lbl} · ${cnt}` : lbl;
    });

  ndAll.on('mouseenter', (ev, d) => showTip(ev, d))
       .on('mousemove', moveTip)
       .on('mouseleave', hideTip)
       .on('click', (ev, d) => {
         ev.stopPropagation();
         CROSS_FOCUS_ID = (CROSS_FOCUS_ID === d.data.id) ? null : d.data.id;
         drawCrossLinks();
       })
       .on('dblclick', (ev, d) => {
         ev.stopPropagation();
         toggleCollapse(d);
         update();
       });

  drawCrossLinks();
}

function toggleCollapse(d) {
  if (d.children) {
    d._children = d.children;
    d.children = null;
    d._collapsed = true;
  } else if (d._children) {
    d.children = d._children;
    d._children = null;
    d._collapsed = false;
  }
}

function drawCrossLinks() {
  const pos = new Map();
  root.descendants().forEach(d => pos.set(d.data.id, {x: d.x, y: d.y}));

  let toDraw = [];
  if (SHOW_CROSS_GLOBAL) {
    toDraw = DATA.cross_links;
  } else if (CROSS_FOCUS_ID) {
    toDraw = DATA.cross_links.filter(e => e.source === CROSS_FOCUS_ID || e.target === CROSS_FOCUS_ID);
  }

  const visible = new Set(root.descendants().map(d => d.data.id));
  toDraw = toDraw.filter(e => visible.has(e.source) && visible.has(e.target));

  const cl = gCross.selectAll('path').data(toDraw, d => d.source + '→' + d.target);
  cl.exit().remove();
  const clEnter = cl.enter().append('path')
    .attr('fill', 'none')
    .attr('stroke-width', 1.2)
    .attr('stroke-opacity', 0.7);
  clEnter.merge(cl)
    .attr('stroke', d => {
      if (CROSS_FOCUS_ID === d.target) return '#22d3ee';
      if (CROSS_FOCUS_ID === d.source) return '#10b981';
      return '#10b981';
    })
    .attr('stroke-opacity', d => CROSS_FOCUS_ID ? 0.85 : 0.18)
    .attr('stroke-width', d => CROSS_FOCUS_ID ? 1.6 : 0.7)
    .attr('d', d => {
      const a = pos.get(d.source), b = pos.get(d.target);
      if (!a || !b) return '';
      const mx = (a.y + b.y) / 2;
      return `M${a.y},${a.x} C${mx},${a.x} ${mx},${b.x} ${b.y},${b.x}`;
    });

  if (CROSS_FOCUS_ID) {
    const linked = new Set([CROSS_FOCUS_ID]);
    DATA.cross_links.forEach(e => {
      if (e.source === CROSS_FOCUS_ID) linked.add(e.target);
      if (e.target === CROSS_FOCUS_ID) linked.add(e.source);
    });
    gNodes.selectAll('g.node').select('circle')
      .attr('fill-opacity', d => linked.has(d.data.id) ? 1 : 0.18);
    gNodes.selectAll('g.node').select('text')
      .attr('opacity', d => linked.has(d.data.id) ? 1 : 0.25);
  } else {
    gNodes.selectAll('g.node').select('circle')
      .attr('fill-opacity', d => d._collapsed ? 0.5 : 0.9);
    gNodes.selectAll('g.node').select('text').attr('opacity', 1);
  }
}

const tip = document.getElementById('tip');
function showTip(ev, d) {
  tip.style.opacity = 1;
  const dat = d.data;
  const broken = dat.group === 'hub1_missing'
    ? '<span class="broken-tag">404 BROKEN</span>' : '';
  const inboundLine = dat.inbound_links > 0
    ? `<div class="row" style="color:#fbbf24">🔗 외부 백링크: <b>${dat.inbound_links}</b>개 (${dat.inbound_sites}사이트)</div>`
    : '';
  const prLine = `<div class="row" style="color:#a7f3d0">🔥 Google PR: <b>${dat.pagerank}</b>/100 <span style="color:#6b7280">(상대순위)</span></div>`;
  const leoLine = `<div class="row" style="color:#5eead4">🤖 LEO Readiness: <b>${dat.leo_score}</b>/100</div>`;
  tip.innerHTML = `<code>${dat.id}</code>${broken}
    <div class="row"><b>${dat.name}</b> · ${dat.group}</div>
    ${prLine}
    ${leoLine}
    ${inboundLine}
    <div class="row" style="color:#6b7280">본문 in/out: ${dat.in_body}/${dat.out_body} · 전역: ${dat.in_global}/${dat.out_global}</div>
    <div class="crumb">📍 ${dat.breadcrumb}</div>`;
  moveTip(ev);
}
function moveTip(ev) {
  const r = svgEl.getBoundingClientRect();
  let x = ev.clientX - r.left + 14;
  let y = ev.clientY - r.top + 14;
  if (x + 360 > r.width) x = ev.clientX - r.left - 370;
  tip.style.left = x + 'px';
  tip.style.top = y + 'px';
}
function hideTip() { tip.style.opacity = 0; }

document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.toLowerCase().trim();
  if (!q) {
    gNodes.selectAll('g.node').select('circle').attr('fill-opacity', d => d._collapsed ? 0.5 : 0.9);
    gNodes.selectAll('g.node').select('text').attr('opacity', 1);
    return;
  }
  gNodes.selectAll('g.node').select('circle').attr('fill-opacity', d => {
    const hit = d.data.id.toLowerCase().includes(q) || d.data.name.toLowerCase().includes(q);
    return hit ? 1 : 0.12;
  });
  gNodes.selectAll('g.node').select('text').attr('opacity', d => {
    const hit = d.data.id.toLowerCase().includes(q) || d.data.name.toLowerCase().includes(q);
    return hit ? 1 : 0.18;
  });
});

document.getElementById('btn_collapse').addEventListener('click', () => {
  root.descendants().forEach(d => {
    if (d.depth >= 1 && d.children) {
      d._children = d.children;
      d.children = null;
      d._collapsed = true;
    }
  });
  update();
});
document.getElementById('btn_expand').addEventListener('click', () => {
  root.descendants().forEach(d => {
    if (d._children) {
      d.children = d._children;
      d._children = null;
      d._collapsed = false;
    }
  });
  update();
});
document.getElementById('btn_cross').addEventListener('click', () => {
  SHOW_CROSS_GLOBAL = !SHOW_CROSS_GLOBAL;
  document.getElementById('btn_cross').classList.toggle('active', SHOW_CROSS_GLOBAL);
  drawCrossLinks();
});
document.getElementById('btn_reset').addEventListener('click', () => {
  CROSS_FOCUS_ID = null;
  svg.transition().duration(400).call(zoom.transform,
    d3.zoomIdentity.translate(40, H/2));
  drawCrossLinks();
});

function focusNodeById(id) {
  const target = root.descendants().find(d => d.data.id === id);
  if (!target) return;
  CROSS_FOCUS_ID = id;
  drawCrossLinks();
  const k = 1.2;
  svg.transition().duration(500).call(zoom.transform,
    d3.zoomIdentity.translate(W/2 - target.y * k, H/2 - target.x * k).scale(k));
}

svg.on('click', () => {
  if (CROSS_FOCUS_ID) {
    CROSS_FOCUS_ID = null;
    drawCrossLinks();
  }
});

window.addEventListener('resize', () => {
  W = svgEl.clientWidth; H = svgEl.clientHeight;
});

update();
svg.call(zoom.transform, d3.zoomIdentity.translate(40, H/2).scale(1));
</script>
</body>
</html>
"""

with open(OUT_HTML, 'w', encoding='utf-8') as f:
    f.write(HTML_TPL.replace('__DATA__', data_json))

print(f'[ok] tree HTML 생성: {OUT_HTML}')
print(f'    노드: {len(SITE_URLS)}, 본문 엣지: {len(edges_body)}, cross-links: {len(cross_links)}')
print(f'    고립: {len(isolated)}, 강한 인용 (in≥5): {len(strong)}')
print(f'    /asset/ 상태: {"sitemap 포함 (실 노드)" if "/asset/" in URL_SET else "sitemap 미포함 (virtual broken)"}')

def show(node, indent=0):
    if indent > 8:
        return
    mark = ' ' * indent + ('├─ ' if indent else '')
    tag = ''
    if node['group'] == 'hub1_missing':
        tag = ' [BROKEN 404]'
    print(f'{mark}{node["name"]} [{node["group"]}] in={node["in_body"]}{tag} ({node["id"]})')
    for c in node.get('children', [])[:8]:
        show(c, indent + 2)

print('\n# 트리 구조 샘플')
show(tree)
