"""Browser abstraction for web exploration and verification.

``Observation`` is the single structured unit both exploration and acceptance
verification consume. ``snapshot_hash`` anchors freshness for web-channel
knowledge entries: whitespace is collapsed before hashing so formatting churn
does not fake a drift, but any content change does.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import Protocol
from urllib.parse import urlparse

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class Observation:
    url: str
    title: str
    text: str
    links: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)

    @property
    def snapshot_hash(self) -> str:
        material = "\n".join(
            [
                _WS.sub(" ", self.title).strip(),
                _WS.sub(" ", self.text).strip(),
                "|".join(sorted(self.forms)),
            ]
        )
        return hashlib.sha256(material.encode()).hexdigest()

    @property
    def looks_like_login(self) -> bool:
        return any("password" in f.lower() for f in self.forms)


class Browser(Protocol):
    def observe(self, url: str) -> Observation: ...
    def close(self) -> None: ...


def same_origin(a: str, b: str) -> bool:
    pa, pb = urlparse(a), urlparse(b)
    return (pa.scheme, pa.netloc) == (pb.scheme, pb.netloc)


class PlaywrightBrowser:
    """Playwright-backed browser. Requires ``pip install knowhelm[web]``."""

    def __init__(self, headed: bool = False, timeout_ms: int = 15000) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise RuntimeError(
                "playwright is not installed; run: pip install 'knowhelm[web]' "
                "&& playwright install chromium"
            ) from exc
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not headed)
        self._page = self._browser.new_page()
        self._timeout_ms = timeout_ms

    def observe(self, url: str) -> Observation:
        self._page.goto(url, timeout=self._timeout_ms, wait_until="domcontentloaded")
        title = self._page.title()
        text = self._page.inner_text("body")[:20_000]
        links = self._page.eval_on_selector_all(
            "a[href]", "els => els.map(e => e.href)"
        )
        forms = self._page.eval_on_selector_all(
            "form",
            """forms => forms.map(f => {
                const inputs = [...f.querySelectorAll('input,select,textarea')]
                    .map(i => `${i.tagName.toLowerCase()}:${i.type || ''}:${i.name || i.id || ''}`);
                return inputs.join(',');
            })""",
        )
        return Observation(
            url=self._page.url, title=title, text=text,
            links=[l for l in links if l.startswith("http")], forms=forms,
        )

    def wait_for_user(self, message: str) -> None:
        print(f"\n[knowhelm] {message}")
        input("[knowhelm] press Enter when done... ")

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()
