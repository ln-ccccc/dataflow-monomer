#  python /uni-curator/user/lcc/lcc/dataflow-dp/pubchem_rate_test.py   --burst-total 10   --burst-concurrency 2   --steady-duration 200   --steady-concurrency 1   --steady-qps 2
import argparse
import os
import statistics
import threading
import time
import urllib.parse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import requests


def normalize_name(name: str) -> str:
    s = str(name or "")
    s = s.replace("′", "'").replace("’", "'").replace("–", "-").replace("—", "-")
    s = s.replace("−", "-").replace("\u00a0", " ")
    s = " ".join(s.split())
    s = s.strip(" ;,")
    return s


def pubchem_name_url(name: str) -> str:
    encoded = urllib.parse.quote(urllib.parse.unquote(name), safe="")
    return (
        "https://pubchem.ncbi.nlm.nih.gov/rest/pug/compound/name/"
        f"{encoded}/property/IsomericSMILES,CanonicalSMILES,ConnectivitySMILES/JSON"
    )


@dataclass
class Result:
    ok: bool
    status: int
    elapsed_s: float
    err: str
    server: str
    via: str
    retry_after: str


_session_local = threading.local()
_no_proxy = False


def _get_session() -> requests.Session:
    session = getattr(_session_local, "session", None)
    if session is None:
        session = requests.Session()
        if _no_proxy:
            session.trust_env = False
        _session_local.session = session
    return session


def one_request(url: str, timeout: float) -> Result:
    t0 = time.perf_counter()
    try:
        session = _get_session()
        res = session.get(url, timeout=timeout)
        elapsed = time.perf_counter() - t0
        server = res.headers.get("Server") or ""
        via = res.headers.get("Via") or ""
        retry_after = res.headers.get("Retry-After") or ""
        return Result(
            ok=(res.status_code == 200),
            status=int(res.status_code),
            elapsed_s=float(elapsed),
            err="",
            server=server,
            via=via,
            retry_after=retry_after,
        )
    except Exception as e:
        elapsed = time.perf_counter() - t0
        return Result(
            ok=False,
            status=0,
            elapsed_s=float(elapsed),
            err=repr(e),
            server="",
            via="",
            retry_after="",
        )


def pct(values, p):
    if not values:
        return None
    values = sorted(values)
    k = int(round((p / 100.0) * (len(values) - 1)))
    return values[max(0, min(len(values) - 1, k))]


def run_burst(url: str, concurrency: int, total: int, timeout: float):
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futs = [ex.submit(one_request, url, timeout) for _ in range(total)]
        for f in as_completed(futs):
            results.append(f.result())
    return results


def run_steady(url: str, concurrency: int, qps: float, duration_s: float, timeout: float):
    results = []
    start = time.perf_counter()
    next_t = start
    submitted = 0
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        inflight = []
        while True:
            now = time.perf_counter()
            if now - start >= duration_s:
                break
            if now < next_t:
                time.sleep(min(0.05, next_t - now))
                continue
            inflight.append(ex.submit(one_request, url, timeout))
            submitted += 1
            if qps > 0:
                next_t += 1.0 / qps
            else:
                next_t = now
            if len(inflight) > concurrency * 4:
                done = []
                for f in inflight:
                    if f.done():
                        done.append(f)
                if not done:
                    time.sleep(0.01)
                    continue
                for f in done:
                    inflight.remove(f)
                    results.append(f.result())
        for f in as_completed(inflight):
            results.append(f.result())
    return results


def summarize(label: str, results):
    codes = Counter([r.status for r in results])
    errors = [r for r in results if r.status == 0]
    lat = [r.elapsed_s for r in results if r.elapsed_s is not None and r.status != 0]
    ok = codes.get(200, 0)
    total = len(results)
    err_rate = (total - ok) / total if total else 0.0
    servers = Counter([r.server for r in results if r.server])
    vias = Counter([r.via for r in results if r.via])
    retry_after = Counter([r.retry_after for r in results if r.retry_after])
    print("=" * 80)
    print(label)
    print("total:", total, "ok200:", ok, "non200:", total - ok, "err_rate:", f"{err_rate:.2%}")
    print("status_codes:", dict(sorted(codes.items(), key=lambda x: (-x[1], x[0]))))
    if errors:
        sample = errors[0].err
        print("network_errors:", len(errors), "sample:", sample)
    if lat:
        print(
            "latency_s:",
            "p50", f"{pct(lat, 50):.3f}",
            "p90", f"{pct(lat, 90):.3f}",
            "p99", f"{pct(lat, 99):.3f}",
            "max", f"{max(lat):.3f}",
        )
    if servers:
        print("server_headers:", dict(servers.most_common(3)))
    if vias:
        print("via_headers:", dict(vias.most_common(3)))
    if retry_after:
        print("retry_after_headers:", dict(retry_after.most_common(3)))
    for code in sorted([c for c in codes.keys() if c not in (0, 200)])[:3]:
        sample = next((r for r in results if r.status == code), None)
        if sample:
            print("sample_non200:", {"status": sample.status, "server": sample.server, "via": sample.via, "retry_after": sample.retry_after})
    print("=" * 80)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--name", default="4,4'-(4,4′-isopropylidenediphenoxy)bis(phthalic anhydride)")
    ap.add_argument("--timeout", type=float, default=15.0)
    ap.add_argument("--no-proxy", action="store_true")
    ap.add_argument("--burst-total", type=int, default=40)
    ap.add_argument("--burst-concurrency", type=str, default="1,2,4,8")
    ap.add_argument("--steady-duration", type=float, default=10.0)
    ap.add_argument("--steady-concurrency", type=int, default=2)
    ap.add_argument("--steady-qps", type=str, default="0.5,1,2,4,8")
    args = ap.parse_args()

    global _no_proxy
    _no_proxy = bool(args.no_proxy)

    name = normalize_name(args.name)
    url = pubchem_name_url(name)

    print("env HTTP_PROXY:", os.getenv("HTTP_PROXY") or "")
    print("env HTTPS_PROXY:", os.getenv("HTTPS_PROXY") or "")
    print("env http_proxy:", os.getenv("http_proxy") or "")
    print("env https_proxy:", os.getenv("https_proxy") or "")
    print("name:", name)
    print("url:", url)

    burst_levels = []
    for x in (args.burst_concurrency or "").split(","):
        x = x.strip()
        if not x:
            continue
        burst_levels.append(int(x))

    for c in burst_levels:
        results = run_burst(url=url, concurrency=c, total=args.burst_total, timeout=args.timeout)
        summarize(f"BURST concurrency={c} total={args.burst_total}", results)

    qps_levels = []
    for x in (args.steady_qps or "").split(","):
        x = x.strip()
        if not x:
            continue
        qps_levels.append(float(x))

    for qps in qps_levels:
        results = run_steady(
            url=url,
            concurrency=args.steady_concurrency,
            qps=qps,
            duration_s=args.steady_duration,
            timeout=args.timeout,
        )
        summarize(f"STEADY concurrency={args.steady_concurrency} qps={qps} duration_s={args.steady_duration}", results)


if __name__ == "__main__":
    main()
