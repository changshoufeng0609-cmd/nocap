"""Polite SEC EDGAR client with the shared content-hash cache (ported from spikes/a_edgar.py).

Cache layout is identical to the spikes so every agent shares one cache:
    data/cache/edgar/<sha256(url)>.bin    response body
    data/cache/edgar/<sha256(url)>.json   {"url", "fetched_at", "sha256", "bytes"}

Politeness (SEC fair-access policy):
  * declared User-Agent from SEC_USER_AGENT (python-dotenv loads the repo .env; the value is never printed)
  * at most 2 live requests per second per process, shared across threads and clients, so parallel
    agents stay under SEC's 10 req/s total
  * retries with backoff on 429/503 (honouring Retry-After); a 403 raises a message explaining the cause

Timestamps: the Submissions API `acceptanceDateTime` is real UTC. Verified against the filing index
page, which prints the same instant in Eastern time (NVDA 8-K: "Accepted 2026-08-26 16:21:19" ET
== "2026-08-26T20:21:19.000Z").
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "cache" / "edgar"
MIN_INTERVAL = 0.5  # seconds between live requests: 2 req/s per process

SEC_WWW = "https://www.sec.gov"
SEC_DATA = "https://data.sec.gov"

# Submissions columns kept for each filing row.
SUBMISSION_FIELDS = ("accessionNumber", "form", "filingDate", "reportDate", "acceptanceDateTime",
                     "primaryDocument", "items")


class EdgarError(RuntimeError):
    """Any EDGAR failure the caller should see (bad User-Agent, 403, budget exhausted, retries spent)."""


class EdgarNotFound(EdgarError):
    """HTTP 404 from SEC."""


class _RateLimiter:
    """Process-wide limiter: one live request per `interval` seconds across all threads and clients."""

    def __init__(self, interval: float):
        self.interval = interval
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            delay = self.interval - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


_LIMITER = _RateLimiter(MIN_INTERVAL)


def user_agent_from_env() -> str:
    """SEC_USER_AGENT from the environment or the repo .env. Raises if missing or a placeholder."""
    load_dotenv(ROOT / ".env")
    ua = os.environ.get("SEC_USER_AGENT", "").strip().strip('"').strip("'")
    if not ua or "@" not in ua or "example.com" in ua:
        raise EdgarError("SEC_USER_AGENT is missing or still the placeholder. SEC requires 'Name email' "
                         "in the User-Agent: run `cp env.example .env` and fill in your name and email.")
    return ua


def parse_accepted(value: str) -> datetime:
    """Submissions `acceptanceDateTime` ("2026-08-26T20:36:00.000Z") -> aware UTC datetime."""
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).replace(microsecond=0)


def accession_folder(cik: int, accession: str) -> str:
    return f"{SEC_WWW}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"


def filing_url(cik: int, accession: str, primary_doc: Optional[str]) -> str:
    """URL of the filing's primary document, or its index page when there is none."""
    if primary_doc:
        return f"{accession_folder(cik, accession)}/{primary_doc}"
    return index_url(cik, accession)


def index_url(cik: int, accession: str) -> str:
    return f"{accession_folder(cik, accession)}/{accession}-index.htm"


def _norm_exhibit(kind: str) -> str:
    """'ex-99.01' -> 'EX-99.1'; 'EX-99' stays 'EX-99'."""
    s = re.sub(r"\s+", "", kind.upper())
    m = re.match(r"^(EX-\d+)(?:\.0*(\d+))?", s)
    if not m:
        return s
    return f"{m.group(1)}.{m.group(2)}" if m.group(2) else m.group(1)


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f"{path.name}.tmp{os.getpid()}.{threading.get_ident()}")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _retry_after(resp: httpx.Response) -> Optional[float]:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None


class Edgar:
    """Polite EDGAR client. Cache first; live requests are rate limited, retried, and counted.

    offline=True never touches the network (a cache miss raises EdgarError).
    max_live_requests caps live requests for this client (a budget guard for batch jobs).
    """

    def __init__(self, user_agent: Optional[str] = None, *, cache_dir: Path = CACHE_DIR,
                 offline: bool = False, max_live_requests: Optional[int] = None, timeout: float = 30.0):
        self._user_agent = user_agent
        self.cache_dir = Path(cache_dir)
        self.offline = offline
        self.max_live_requests = max_live_requests
        self.timeout = timeout
        self.live_requests = 0
        self.cache_hits = 0
        self._client: Optional[httpx.Client] = None
        self._client_lock = threading.Lock()
        self._budget_lock = threading.Lock()

    # ---------------------------------------------------------------- transport

    def _http(self) -> httpx.Client:
        with self._client_lock:
            if self._client is None:
                ua = self._user_agent or user_agent_from_env()
                self._client = httpx.Client(
                    headers={"User-Agent": ua, "Accept-Encoding": "gzip, deflate"},
                    timeout=self.timeout, follow_redirects=True)
            return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def _paths(self, url: str) -> tuple[Path, Path]:
        key = hashlib.sha256(url.encode()).hexdigest()
        return self.cache_dir / f"{key}.bin", self.cache_dir / f"{key}.json"

    def is_cached(self, url: str) -> bool:
        body_path, meta_path = self._paths(url)
        return body_path.exists() and meta_path.exists()

    @staticmethod
    def _stale(meta_path: Path, max_age: Optional[float]) -> bool:
        if max_age is None:
            return False
        try:
            fetched = datetime.fromisoformat(json.loads(meta_path.read_text())["fetched_at"])
        except (OSError, ValueError, KeyError):
            return True
        return (datetime.now(timezone.utc) - fetched).total_seconds() > max_age

    def get(self, url: str, *, max_age: Optional[float] = None) -> bytes:
        """Response body for `url`, from the cache unless missing or older than `max_age` seconds."""
        body_path, meta_path = self._paths(url)
        if body_path.exists() and meta_path.exists() and not self._stale(meta_path, max_age):
            self.cache_hits += 1
            return body_path.read_bytes()
        if self.offline:
            raise EdgarError(f"offline mode and not cached: {url}")
        body = self._fetch(url)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(body_path, body)  # body first: readers require both files to exist
        _atomic_write(meta_path, json.dumps({
            "url": url,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
            "sha256": hashlib.sha256(body).hexdigest(),
            "bytes": len(body),
        }).encode())
        return body

    def _fetch(self, url: str, attempts: int = 6) -> bytes:
        problem = "no attempt made"
        for attempt in range(attempts):
            with self._budget_lock:
                if self.max_live_requests is not None and self.live_requests >= self.max_live_requests:
                    raise EdgarError(f"live request budget of {self.max_live_requests} exhausted; refusing {url}")
                self.live_requests += 1
            _LIMITER.wait()
            try:
                r = self._http().get(url)
            except httpx.TransportError as e:
                problem = f"{type(e).__name__}: {e}"
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code in (429, 503) or r.status_code >= 500:
                problem = f"HTTP {r.status_code}"
                time.sleep(min(_retry_after(r) or 2 ** attempt, 60))
                continue
            if r.status_code == 403:
                raise EdgarError(
                    f"SEC returned 403 Forbidden for {url}. Either SEC_USER_AGENT in .env is not a real "
                    "'Name email@domain' contact (placeholders are blocked), or this IP exceeded 10 req/s "
                    "across all processes and is temporarily blocked (wait ~10 minutes before retrying).")
            if r.status_code == 404:
                raise EdgarNotFound(f"SEC returned 404 for {url}")
            if r.status_code >= 400:
                raise EdgarError(f"SEC returned HTTP {r.status_code} for {url}")
            return r.content
        raise EdgarError(f"giving up on {url} after {attempts} attempts ({problem})")

    def json(self, url: str, *, max_age: Optional[float] = None) -> Any:
        return json.loads(self.get(url, max_age=max_age))

    # ---------------------------------------------------------------- helpers

    def company_tickers(self, *, max_age: Optional[float] = None) -> dict:
        return self.json(f"{SEC_WWW}/files/company_tickers.json", max_age=max_age)

    def ticker_to_cik(self, ticker: str) -> tuple[int, str]:
        """'NVDA' -> (1045810, 'NVIDIA CORP')."""
        want = ticker.strip().upper().replace(".", "-")
        for row in self.company_tickers().values():
            if row["ticker"].upper() == want:
                return int(row["cik_str"]), row["title"]
        raise EdgarError(f"ticker {ticker!r} not found in SEC company_tickers.json")

    def submissions(self, cik: int, *, since: Optional[date] = None,
                    max_age: Optional[float] = None) -> tuple[dict, list[dict]]:
        """(company metadata, filing rows filed on or after `since`), newest first.

        Merges the `recent` block with the older pages (`filings.files`) whose range reaches `since`.
        Heavy filers such as JPM and BAC need many pages: their `recent` block covers only one year.
        """
        sub = self.json(f"{SEC_DATA}/submissions/CIK{int(cik):010d}.json", max_age=max_age)
        blocks = [sub["filings"]["recent"]]
        for page in sub["filings"].get("files", []):
            if since is None or date.fromisoformat(page["filingTo"]) >= since:
                blocks.append(self.json(f"{SEC_DATA}/submissions/{page['name']}", max_age=max_age))
        rows: list[dict] = []
        seen: set[str] = set()
        floor = since.isoformat() if since else ""
        for block in blocks:
            for i in range(len(block["accessionNumber"])):
                accession = block["accessionNumber"][i]
                if accession in seen or block["filingDate"][i] < floor:
                    continue
                seen.add(accession)
                rows.append({k: (block[k][i] if k in block else None) for k in SUBMISSION_FIELDS})
        rows.sort(key=lambda r: r["acceptanceDateTime"] or r["filingDate"], reverse=True)
        meta = {k: v for k, v in sub.items() if k != "filings"}
        return meta, rows

    def companyfacts(self, cik: int, *, max_age: Optional[float] = None) -> dict:
        return self.json(f"{SEC_DATA}/api/xbrl/companyfacts/CIK{int(cik):010d}.json", max_age=max_age)

    def filing_index(self, cik: int, accession: str) -> list[dict]:
        """Documents listed on the filing index page: seq, description, document, type, size, url."""
        soup = BeautifulSoup(self.get(index_url(cik, accession)), "lxml")
        docs = []
        for tr in soup.find_all("tr"):
            tds = tr.find_all("td")
            if len(tds) < 4:
                continue
            a = tds[2].find("a")
            if not a or not a.get("href"):
                continue
            href = a["href"].replace("/ix?doc=", "")
            docs.append({
                "seq": tds[0].get_text(strip=True),
                "description": tds[1].get_text(" ", strip=True),
                "document": a.get_text(strip=True),
                "type": tds[3].get_text(strip=True),
                "size": tds[4].get_text(strip=True) if len(tds) > 4 else "",
                "url": SEC_WWW + href if href.startswith("/") else href,
            })
        return docs

    def exhibit_url(self, cik: int, accession: str, exhibit: str = "EX-99.1") -> Optional[str]:
        """URL of the first document of type `exhibit` (EX-99.1, EX-99.2, ...) in a filing, or None."""
        want = _norm_exhibit(exhibit)
        try:
            docs = self.filing_index(cik, accession)
        except EdgarNotFound:
            return None
        for doc in docs:
            if _norm_exhibit(doc["type"]) == want:
                return doc["url"]
        if want.endswith(".1"):  # some filers label their only press release plain "EX-99"
            base = want.rsplit(".", 1)[0]
            for doc in docs:
                if _norm_exhibit(doc["type"]) == base:
                    return doc["url"]
        return None
