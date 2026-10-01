"""Polite HTTP fetching shared by all source adapters.

- descriptive User-Agent
- at least `min_interval` seconds between requests to the same host
- retry with exponential backoff on 429 / 5xx / network errors
- 1 hour on-disk cache of successful GET responses
- robots.txt checked (and cached) before every crawl request
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

USER_AGENT = (
    "healthy-news-intake/0.1 (personal once-a-day news digest; "
    "+https://github.com/dom1911k/healthy_news_intake)"
)


class FetchError(Exception):
    pass


class RobotsDisallowed(FetchError):
    pass


@dataclass
class Response:
    url: str
    status: int
    headers: dict[str, str] = field(default_factory=dict)
    body: bytes = b""
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    def text(self) -> str:
        ctype = self.headers.get("content-type", "")
        charset = "utf-8"
        if "charset=" in ctype:
            charset = ctype.split("charset=", 1)[1].split(";")[0].strip() or "utf-8"
        return self.body.decode(charset, errors="replace")

    def json(self):
        return json.loads(self.text())


class Fetcher:
    def __init__(
        self,
        cache_dir: Path | str = ".cache/http",
        cache_ttl: float = 3600,
        min_interval: float = 2.0,
        retries: int = 3,
        timeout: float = 30,
        user_agent: str = USER_AGENT,
    ):
        self.cache_dir = Path(cache_dir)
        self.cache_ttl = cache_ttl
        self.min_interval = min_interval
        self.retries = retries
        self.timeout = timeout
        self.user_agent = user_agent
        self._last_request: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.robots_unreachable: set[str] = set()  # origins whose robots.txt could not be fetched
        self._host_interval: dict[str, float] = {}  # robots.txt Crawl-delay, if longer than min_interval

    # -- robots.txt ---------------------------------------------------------

    def robots(self, url: str) -> urllib.robotparser.RobotFileParser:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = urllib.robotparser.RobotFileParser(origin + "/robots.txt")
        try:
            resp = self._request("GET", origin + "/robots.txt", use_cache=True)
        except FetchError:
            # Unreachable robots.txt: be conservative and treat as full disallow.
            rp.disallow_all = True
            self.robots_unreachable.add(origin)
        else:
            if resp.status in (401, 403):
                rp.disallow_all = True
            elif 400 <= resp.status < 500:
                rp.allow_all = True
            elif resp.ok:
                rp.parse(resp.text().splitlines())
                delay = rp.crawl_delay(self.user_agent)
                if delay:
                    self._host_interval[parts.netloc] = max(self.min_interval, float(delay))
            else:
                rp.disallow_all = True
                self.robots_unreachable.add(origin)
        self._robots[origin] = rp
        return rp

    def allowed(self, url: str) -> bool:
        return self.robots(url).can_fetch(self.user_agent, url)

    # -- requests -----------------------------------------------------------

    def get(self, url: str, *, headers: dict[str, str] | None = None,
            check_robots: bool = True, use_cache: bool = True) -> Response:
        """GET a page. `check_robots=False` is only for authenticated API calls,
        which are governed by the API's terms rather than robots.txt."""
        if check_robots and not self.allowed(url):
            parts = urlsplit(url)
            if f"{parts.scheme}://{parts.netloc}" in self.robots_unreachable:
                raise RobotsDisallowed(f"robots.txt unreachable (host blocked or down), not fetching {url}")
            raise RobotsDisallowed(f"robots.txt disallows {url}")
        return self._request("GET", url, headers=headers, use_cache=use_cache)

    def post(self, url: str, data: bytes, *, headers: dict[str, str] | None = None) -> Response:
        return self._request("POST", url, data=data, headers=headers, use_cache=False)

    def _cache_path(self, url: str) -> Path:
        return self.cache_dir / hashlib.sha256(url.encode()).hexdigest()

    def _read_cache(self, url: str) -> Response | None:
        path = self._cache_path(url)
        meta_path = path.with_suffix(".json")
        if not meta_path.exists() or time.time() - meta_path.stat().st_mtime > self.cache_ttl:
            return None
        meta = json.loads(meta_path.read_text())
        return Response(meta["url"], meta["status"], meta["headers"], path.read_bytes(), from_cache=True)

    def _write_cache(self, url: str, resp: Response) -> None:
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path = self._cache_path(url)
        path.write_bytes(resp.body)
        path.with_suffix(".json").write_text(
            json.dumps({"url": resp.url, "status": resp.status, "headers": resp.headers})
        )

    def _throttle(self, url: str) -> None:
        host = urlsplit(url).netloc
        interval = self._host_interval.get(host, self.min_interval)
        wait = self._last_request.get(host, 0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        self._last_request[host] = time.monotonic()

    def _request(self, method: str, url: str, *, data: bytes | None = None,
                 headers: dict[str, str] | None = None, use_cache: bool = True) -> Response:
        if use_cache and method == "GET":
            cached = self._read_cache(url)
            if cached is not None:
                return cached

        req_headers = {"User-Agent": self.user_agent, "Accept-Encoding": "identity"}
        req_headers.update(headers or {})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            req = urllib.request.Request(url, data=data, headers=req_headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    resp = Response(r.geturl(), r.status, {k.lower(): v for k, v in r.headers.items()}, r.read())
            except urllib.error.HTTPError as e:
                resp = Response(url, e.code, {k.lower(): v for k, v in (e.headers or {}).items()}, e.read() or b"")
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last_error = e
                resp = None

            if resp is not None and resp.status != 429 and resp.status < 500:
                if use_cache and method == "GET" and resp.ok:
                    self._write_cache(url, resp)
                return resp
            if attempt == self.retries:
                break
            delay = 2 ** (attempt + 1)
            if resp is not None and resp.headers.get("retry-after", "").isdigit():
                delay = min(int(resp.headers["retry-after"]), 60)
            time.sleep(delay)

        if resp is not None:
            return resp
        raise FetchError(f"{method} {url} failed: {last_error}")
