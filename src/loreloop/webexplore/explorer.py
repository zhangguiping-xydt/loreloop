"""Observe-Plan-Act-Verify exploration loop.

Generic by design: no per-site strategies or selectors. The loop observes any
page, stays within the start origin, records a JSONL trace of every step, and
hands control to a human when it hits a login wall instead of trying to
automate credentials.
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin, urlparse

from ..paths import state_path
from .browser import Browser, Observation, same_origin


@dataclass(frozen=True)
class ExplorationResult:
    pages: list[Observation]
    trace_path: Path
    skipped: list[str] = field(default_factory=list)
    login_walls: list[str] = field(default_factory=list)
    login_resumed: list[str] = field(default_factory=list)


class Explorer:
    def __init__(
        self,
        browser: Browser,
        workdir: Path,
        max_pages: int = 20,
        on_login_wall: str = "handover",  # or "skip"
        discover_seeds: bool = True,
    ) -> None:
        self._browser = browser
        self._workdir = workdir
        self._trace_dir = state_path(workdir, "explorations")
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        self._max_pages = max_pages
        self._on_login_wall = on_login_wall
        self._discover_seeds = discover_seeds

    def explore(self, start_url: str) -> ExplorationResult:
        ts = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        trace_path = self._trace_dir / f"explore-{ts}.jsonl"
        seeds = self._seed_urls(start_url) if self._discover_seeds else [start_url]
        queue: deque[str] = deque(seeds)
        seen: set[str] = set()
        pages: list[Observation] = []
        skipped: list[str] = []
        login_walls: list[str] = []
        login_resumed: list[str] = []

        self._trace(
            trace_path,
            "exploration_started",
            url=start_url,
            max_pages=self._max_pages,
            seeds=len(seeds),
        )
        while queue and len(pages) < self._max_pages:
            url = queue.popleft().split("#")[0].rstrip("/")
            if url in seen:
                continue
            seen.add(url)
            if not same_origin(url, start_url):
                skipped.append(url)
                self._trace(trace_path, "skipped_cross_origin", url=url)
                continue

            try:
                obs = self._browser.observe(url)
            except Exception as exc:
                self._trace(trace_path, "observe_failed", url=url, error=str(exc)[:300])
                skipped.append(url)
                continue

            if not same_origin(obs.url, start_url):
                skipped.append(obs.url)
                self._trace(
                    trace_path,
                    "skipped_cross_origin_redirect",
                    requested_url=url,
                    url=obs.url,
                )
                continue

            if obs.looks_like_login:
                obs = self._handle_login_wall(
                    trace_path, obs, skipped, login_walls, login_resumed
                )
                if obs is None:
                    continue
                if not same_origin(obs.url, start_url):
                    skipped.append(obs.url)
                    self._trace(
                        trace_path,
                        "skipped_cross_origin_redirect",
                        requested_url=url,
                        url=obs.url,
                    )
                    continue

            pages.append(obs)
            self._trace(
                trace_path,
                "page_observed",
                url=obs.url,
                title=obs.title,
                snapshot=obs.snapshot_hash,
                links=len(obs.links),
                forms=len(obs.forms),
            )
            for link in obs.links:
                if link.split("#")[0].rstrip("/") not in seen:
                    queue.append(link)

        self._trace(trace_path, "exploration_finished", pages=len(pages), skipped=len(skipped))
        return ExplorationResult(
            pages=pages,
            trace_path=trace_path,
            skipped=skipped,
            login_walls=login_walls,
            login_resumed=login_resumed,
        )

    def _handle_login_wall(
        self,
        trace_path: Path,
        obs: Observation,
        skipped: list[str],
        login_walls: list[str],
        login_resumed: list[str],
    ) -> Observation | None:
        login_walls.append(obs.url)
        if self._on_login_wall == "handover" and hasattr(self._browser, "wait_for_user"):
            self._trace(trace_path, "human_handover", url=obs.url, reason="login form detected")
            self._browser.wait_for_user(
                f"login required at {obs.url} — please sign in in the browser window"
            )
            observe_current = getattr(self._browser, "observe_current", None)
            try:
                retry = (
                    observe_current()
                    if callable(observe_current)
                    else self._browser.observe(obs.url)
                )
            except Exception as exc:
                self._trace(
                    trace_path,
                    "handover_observe_failed",
                    url=obs.url,
                    error=str(exc)[:300],
                )
                skipped.append(obs.url)
                return None
            if retry.looks_like_login:
                self._trace(trace_path, "handover_abandoned", url=obs.url)
                skipped.append(obs.url)
                return None
            self._trace(
                trace_path,
                "human_handover_completed",
                login_url=obs.url,
                url=retry.url,
            )
            login_resumed.append(retry.url)
            return retry
        self._trace(trace_path, "skipped_login_wall", url=obs.url)
        skipped.append(obs.url)
        return None

    def _trace(self, path: Path, event: str, **fields) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    def _seed_urls(self, start_url: str) -> list[str]:
        seeds = [start_url]
        seen = {start_url.split("#")[0].rstrip("/")}
        for url in [*self._code_route_seeds(start_url), *self._remote_seed_urls(start_url)]:
            clean = url.split("#")[0].rstrip("/")
            if clean in seen or not same_origin(clean, start_url):
                continue
            seen.add(clean)
            seeds.append(clean)
            if len(seeds) >= self._max_pages * 3:
                break
        return seeds

    def _remote_seed_urls(self, start_url: str) -> list[str]:
        parsed = urlparse(start_url)
        if not parsed.hostname or parsed.hostname.endswith(".local"):
            return []
        origin = f"{parsed.scheme}://{parsed.netloc}"
        urls: list[str] = []
        robots = self._fetch_text(urljoin(origin, "/robots.txt"), allowed_origin=origin)
        sitemap_urls = [urljoin(origin, "/sitemap.xml")]
        if robots:
            for line in robots.splitlines():
                key, sep, value = line.partition(":")
                if not sep:
                    continue
                key = key.strip().lower()
                value = value.strip()
                if key == "sitemap" and value:
                    candidate = urljoin(origin, value)
                    if same_origin(candidate, origin):
                        sitemap_urls.append(candidate)
                elif key == "allow" and value and "*" not in value:
                    urls.append(urljoin(origin, value))
        for sitemap in sitemap_urls[:5]:
            data = self._fetch_text(sitemap, allowed_origin=origin)
            if data:
                urls.extend(_parse_sitemap_urls(data, sitemap))
        return urls

    def _fetch_text(self, url: str, *, allowed_origin: str) -> str | None:
        if not same_origin(url, allowed_origin):
            return None
        try:
            with urllib.request.urlopen(url, timeout=0.75) as response:
                final_url = response.geturl()
                if not same_origin(final_url, allowed_origin):
                    return None
                return response.read(250_000).decode("utf-8", errors="replace")
        except (OSError, urllib.error.URLError, UnicodeDecodeError):
            return None

    def _code_route_seeds(self, start_url: str) -> list[str]:
        routes: list[str] = []
        seen: set[str] = set()
        for path in _iter_route_files(self._workdir):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            for match in _ROUTE_RE.finditer(text):
                route = match.group(1)
                if not _looks_like_route(route) or route in seen:
                    continue
                seen.add(route)
                routes.append(urljoin(start_url, route))
                if len(routes) >= 40:
                    return routes
        return routes


_ROUTE_RE = re.compile(r"""["'`](/[A-Za-z0-9._~!$&'()*+,;=:@/%-]{1,120})["'`]""")
_ROUTE_EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".html"}
_SKIP_DIRS = {".git", ".loreloop", "node_modules", ".venv", "venv", "dist", "build"}


def _iter_route_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in _SKIP_DIRS]
        base = Path(dirpath)
        for filename in filenames:
            path = base / filename
            if path.suffix in _ROUTE_EXTENSIONS and path.stat().st_size <= 80_000:
                yield path


def _looks_like_route(route: str) -> bool:
    if route.startswith("//") or any(ch in route for ch in "{}* "):
        return False
    last = route.rsplit("/", 1)[-1]
    if "." in last and not last.endswith(".html"):
        return False
    return True


def _parse_sitemap_urls(data: str, base_url: str) -> list[str]:
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        return re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", data, flags=re.IGNORECASE)
    urls = []
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] == "loc" and elem.text:
            urls.append(urljoin(base_url, elem.text.strip()))
    return urls
