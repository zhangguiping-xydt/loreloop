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


class BrowserUnavailable(RuntimeError):
    pass


class BrowserError(RuntimeError):
    pass


@dataclass(frozen=True)
class Observation:
    url: str
    title: str
    text: str
    links: list[str] = field(default_factory=list)
    forms: list[str] = field(default_factory=list)
    headings: list[str] = field(default_factory=list)
    buttons: list[str] = field(default_factory=list)
    nav: list[str] = field(default_factory=list)

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
    """Playwright-backed browser. Requires ``pip install loreloop[web]``."""

    def __init__(self, headed: bool = False, timeout_ms: int = 15000) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "playwright is not installed; run: pip install 'loreloop[web]' "
                "&& playwright install chromium"
            ) from exc
        try:
            self._pw = sync_playwright().start()
            self._browser = self._pw.chromium.launch(headless=not headed)
            self._page = self._browser.new_page()
        except Exception as exc:
            if hasattr(self, "_pw"):
                self._pw.stop()
            raise BrowserUnavailable(
                "cannot start Chromium; run `python -m playwright install chromium` "
                f"and retry: {exc}"
            ) from exc
        self._timeout_ms = timeout_ms

    @property
    def page(self):
        return self._page

    def observe(self, url: str) -> Observation:
        try:
            response = self._page.goto(
                url, timeout=self._timeout_ms, wait_until="domcontentloaded"
            )
            if response is not None and response.status >= 400:
                raise BrowserError(f"page returned HTTP {response.status}: {response.url}")
            self._settle()
            return self.observe_current()
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"cannot observe {url}: {exc}") from exc

    def observe_current(self) -> Observation:
        title = self._page.title()
        text = self._page.inner_text("body", timeout=self._timeout_ms)[:20_000]
        links = self._page.evaluate(_LINKS_JS)
        forms = self._page.eval_on_selector_all(
            "form",
            """forms => forms.map(f => {
                const inputs = [...f.querySelectorAll('input,select,textarea')]
                    .map(i => {
                        const label = i.labels && i.labels.length
                            ? [...i.labels].map(l => l.innerText.trim()).filter(Boolean).join('|')
                            : (i.getAttribute('aria-label') || i.getAttribute('placeholder') || '');
                        return `${i.tagName.toLowerCase()}:${i.type || ''}:${i.name || i.id || ''}:${label}`;
                    });
                return inputs.join(',');
            })""",
        )
        headings = self._page.evaluate(_TEXT_LIST_JS, "h1,h2,h3,h4,h5,h6,[role=heading]")
        buttons = self._page.evaluate(
            _TEXT_LIST_JS,
            "button,[role=button],input[type=button],input[type=submit],input[type=reset]",
        )
        nav = self._page.evaluate(
            _TEXT_LIST_JS,
            "nav,[role=navigation],header",
        )
        return Observation(
            url=self._page.url, title=title, text=text,
            links=[u for u in links if isinstance(u, str) and u.startswith("http")],
            forms=forms,
            headings=headings,
            buttons=buttons,
            nav=nav,
        )

    def _settle(self) -> None:
        try:
            self._page.wait_for_load_state("networkidle", timeout=min(self._timeout_ms, 5000))
        except Exception:
            pass
        self._scroll_for_lazy_content()
        try:
            self._page.wait_for_load_state("networkidle", timeout=min(self._timeout_ms, 5000))
        except Exception:
            pass

    def _scroll_for_lazy_content(self) -> None:
        self._page.evaluate(
            """async () => {
                const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
                let previous = -1;
                for (let i = 0; i < 10; i++) {
                    const height = document.documentElement.scrollHeight || document.body.scrollHeight || 0;
                    if (height === previous) break;
                    previous = height;
                    window.scrollTo(0, height);
                    await delay(120);
                }
                window.scrollTo(0, 0);
            }"""
        )

    def wait_for_user(self, message: str) -> None:
        print(f"\n[LoreLoop] {message}")
        input("[LoreLoop] press Enter when done... ")

    def close(self) -> None:
        self._browser.close()
        self._pw.stop()


_LINKS_JS = """() => {
    const out = new Set();
    const add = value => {
        if (!value) return;
        try { out.add(new URL(value, document.baseURI).href); } catch (_) {}
    };
    for (const el of document.querySelectorAll('a[href], area[href], [role=link]')) {
        add(el.href || el.getAttribute('href') || el.getAttribute('data-href'));
    }
    for (const el of document.querySelectorAll('[onclick], [data-href], [data-url]')) {
        add(el.getAttribute('data-href') || el.getAttribute('data-url'));
        const onclick = el.getAttribute('onclick') || '';
        const match = onclick.match(/(?:location(?:\\.href)?|location\\.assign|window\\.open)\\s*\\(?\\s*['"]([^'"]+)['"]/);
        if (match) add(match[1]);
    }
    return [...out];
}"""

_TEXT_LIST_JS = """(selector) => {
    const visible = el => {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        return style && style.visibility !== 'hidden' && style.display !== 'none'
            && rect.width > 0 && rect.height > 0;
    };
    const label = el => {
        if (el.matches('input')) {
            return el.value || el.getAttribute('aria-label') || el.getAttribute('title') || '';
        }
        return el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || '';
    };
    const seen = new Set();
    const out = [];
    for (const el of document.querySelectorAll(selector)) {
        if (!visible(el)) continue;
        const text = label(el).replace(/\\s+/g, ' ').trim();
        if (!text || seen.has(text)) continue;
        seen.add(text);
        out.push(text.slice(0, 300));
        if (out.length >= 80) break;
    }
    return out;
}"""
