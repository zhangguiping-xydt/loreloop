"""Observe-Plan-Act-Verify exploration loop.

Generic by design: no per-site strategies or selectors. The loop observes any
page, stays within the start origin, records a JSONL trace of every step, and
hands control to a human when it hits a login wall instead of trying to
automate credentials.
"""

from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .browser import Browser, Observation, same_origin


@dataclass(frozen=True)
class ExplorationResult:
    pages: list[Observation]
    trace_path: Path
    skipped: list[str] = field(default_factory=list)


class Explorer:
    def __init__(
        self,
        browser: Browser,
        workdir: Path,
        max_pages: int = 20,
        on_login_wall: str = "handover",  # or "skip"
    ) -> None:
        self._browser = browser
        self._trace_dir = workdir / ".knowhelm/explorations"
        self._trace_dir.mkdir(parents=True, exist_ok=True)
        self._max_pages = max_pages
        self._on_login_wall = on_login_wall

    def explore(self, start_url: str) -> ExplorationResult:
        ts = f"{datetime.now(timezone.utc):%Y%m%d%H%M%S}"
        trace_path = self._trace_dir / f"explore-{ts}.jsonl"
        queue: deque[str] = deque([start_url])
        seen: set[str] = set()
        pages: list[Observation] = []
        skipped: list[str] = []

        self._trace(trace_path, "exploration_started", url=start_url, max_pages=self._max_pages)
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

            if obs.looks_like_login and obs.url != start_url:
                obs = self._handle_login_wall(trace_path, obs, skipped)
                if obs is None:
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
        return ExplorationResult(pages=pages, trace_path=trace_path, skipped=skipped)

    def _handle_login_wall(
        self, trace_path: Path, obs: Observation, skipped: list[str]
    ) -> Observation | None:
        if self._on_login_wall == "handover" and hasattr(self._browser, "wait_for_user"):
            self._trace(trace_path, "human_handover", url=obs.url, reason="login form detected")
            self._browser.wait_for_user(
                f"login required at {obs.url} — please sign in in the browser window"
            )
            retry = self._browser.observe(obs.url)
            if retry.looks_like_login:
                self._trace(trace_path, "handover_abandoned", url=obs.url)
                skipped.append(obs.url)
                return None
            return retry
        self._trace(trace_path, "skipped_login_wall", url=obs.url)
        skipped.append(obs.url)
        return None

    def _trace(self, path: Path, event: str, **fields) -> None:
        record = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")
