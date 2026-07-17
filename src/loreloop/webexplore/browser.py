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
    return _origin(a) is not None and _origin(a) == _origin(b)


def require_http_url(url: str) -> str:
    if _origin(url) is None:
        raise BrowserError(f"URL must be absolute HTTP(S), got: {url!r}")
    return url


def _origin(url: str) -> tuple[str, str, int] | None:
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"} or not parsed.hostname:
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return scheme, parsed.hostname.rstrip(".").lower(), port or (443 if scheme == "https" else 80)


class PlaywrightBrowser:
    """Playwright-backed browser. Requires ``pip install loreloop[web]``."""

    def __init__(
        self,
        headed: bool = False,
        timeout_ms: int = 15000,
        *,
        allow_login_handover: bool | None = None,
    ) -> None:
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
            self._context = self._browser.new_context(
                accept_downloads=False,
                service_workers="block",
            )
            self._allowed_origin: str | None = None
            self._login_handover_enabled = (
                headed if allow_login_handover is None else allow_login_handover
            )
            self._handover_origin: str | None = None
            self._allow_writes = False
            self._blocked_requests: list[dict[str, str]] = []
            self._context.route("**/*", self._route_request)
            self._page = self._context.new_page()
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

    @property
    def login_handover_enabled(self) -> bool:
        return self._login_handover_enabled

    def observe(self, url: str) -> Observation:
        require_http_url(url)
        self.set_network_policy(url, allow_writes=False)
        try:
            response = self._page.goto(url, timeout=self._timeout_ms, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise BrowserError(f"page returned HTTP {response.status}: {response.url}")
            if not same_origin(self._page.url, url):
                self._adopt_login_redirect("navigation")
            self._settle()
            return self.observe_current()
        except BrowserError:
            raise
        except Exception as exc:
            raise BrowserError(f"cannot observe {url}: {exc}") from exc

    def observe_current(self) -> Observation:
        if (
            self._allowed_origin
            and not same_origin(self._page.url, self._allowed_origin)
            and not self._is_handover_origin(self._page.url)
        ):
            self._adopt_login_redirect("page")
        if (
            self._allowed_origin
            and not same_origin(self._page.url, self._allowed_origin)
            and not self._is_handover_origin(self._page.url)
        ):
            raise BrowserError(f"page left allowed origin: {self._page.url}")
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
            url=self._page.url,
            title=title,
            text=text,
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
        previous = self._allow_writes
        self._allow_writes = True
        try:
            input("[LoreLoop] press Enter when done... ")
        finally:
            self._allow_writes = previous
        self._settle()
        if self._allowed_origin and same_origin(self._page.url, self._allowed_origin):
            self._handover_origin = None
            self._blocked_requests.clear()

    def set_network_policy(self, base_url: str, *, allow_writes: bool = False) -> None:
        require_http_url(base_url)
        self._allowed_origin = base_url
        self._handover_origin = None
        self._allow_writes = allow_writes
        self._blocked_requests.clear()

    def consume_blocked_requests(self) -> list[dict[str, str]]:
        blocked = list(self._blocked_requests)
        self._blocked_requests.clear()
        return blocked

    def _route_request(self, route, request) -> None:
        parsed = urlparse(request.url)
        if parsed.scheme in {"data", "blob", "about"}:
            route.continue_()
            return
        reason = None
        if self._may_start_login_handover(request):
            self._handover_origin = request.url
        if (
            self._allowed_origin
            and not same_origin(request.url, self._allowed_origin)
            and not self._is_handover_origin(request.url)
        ):
            reason = "cross-origin"
        elif request.method.upper() not in {"GET", "HEAD", "OPTIONS"} and not self._allow_writes:
            reason = "write-method"
        if reason:
            self._blocked_requests.append(
                {"url": request.url, "method": request.method.upper(), "reason": reason}
            )
            route.abort("blockedbyclient")
            return
        route.continue_()

    def _may_start_login_handover(self, request) -> bool:
        if not self._login_handover_enabled or not self._allowed_origin:
            return False
        if same_origin(request.url, self._allowed_origin):
            return False
        try:
            return (
                request.method.upper() in {"GET", "HEAD"}
                and request.is_navigation_request()
                and request.resource_type == "document"
            )
        except Exception:
            return False

    def _is_handover_origin(self, url: str) -> bool:
        return bool(
            self._login_handover_enabled
            and self._handover_origin
            and same_origin(url, self._handover_origin)
        )

    def _adopt_login_redirect(self, label: str) -> None:
        if not self._login_handover_enabled:
            raise BrowserError(f"{label} left allowed origin: {self._page.url}")
        # Redirect requests are not consistently exposed to Playwright routing.
        # Adopt the visible identity-provider origin, then reload once so its
        # same-origin scripts/styles can load under the bounded handover policy.
        self._handover_origin = self._page.url
        self._blocked_requests.clear()
        try:
            response = self._page.reload(
                timeout=self._timeout_ms, wait_until="domcontentloaded"
            )
        except Exception as exc:
            raise BrowserError(f"cannot load login handover page: {exc}") from exc
        if response is not None and response.status >= 400:
            raise BrowserError(f"page returned HTTP {response.status}: {response.url}")
        self._settle()

    def close(self) -> None:
        self._context.close()
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
