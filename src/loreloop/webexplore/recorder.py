"""Headed user-journey recording into the bounded ActionScript DSL."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable
from urllib.parse import urlsplit

from ..evidence.artifacts import ArtifactStore
from .actions import parse_action_script
from .browser import require_http_url
from .scenarios import ScenarioAssertion, WebScenario, WebScenarioError

_SENSITIVE = re.compile(r"password|secret|token|credential|api.?key", re.IGNORECASE)
_LOCATOR_KEYS = {"text", "label", "role", "nth"}

_RECORDER_JS = r"""
(() => {
  if (window.__loreloopRecorderInstalled) return;
  window.__loreloopRecorderInstalled = true;
  const clean = value => (value || '').replace(/\s+/g, ' ').trim().slice(0, 512);
  const label = el => {
    if (el.labels && el.labels.length) return clean([...el.labels].map(x => x.innerText).join(' '));
    return clean(el.getAttribute('aria-label') || el.getAttribute('placeholder'));
  };
  const locator = el => {
    const role = clean(el.getAttribute('role'));
    const text = clean(el.innerText || el.value || el.getAttribute('aria-label'));
    const fieldLabel = label(el);
    if (fieldLabel) return {label: fieldLabel};
    if (role && text) return {role, text};
    if (text) return {text};
    return null;
  };
  document.addEventListener('click', event => {
    const el = event.target && event.target.closest('button,a,[role=button],input[type=button],input[type=submit]');
    if (!el) return;
    const target = locator(el);
    if (target) window.__loreloopRecord({op: 'click', locator: target});
  }, true);
  document.addEventListener('change', event => {
    const el = event.target;
    if (!el || !['INPUT','TEXTAREA','SELECT'].includes(el.tagName)) return;
    const sensitive = `${el.type || ''} ${el.name || ''} ${el.id || ''} ${el.autocomplete || ''}`;
    if (/password|secret|token|credential|api.?key/i.test(sensitive)) return;
    const target = locator(el);
    if (!target) return;
    if (el.tagName === 'SELECT') {
      const option = el.options && el.selectedIndex >= 0
        ? clean(el.options[el.selectedIndex].text || el.value) : clean(el.value);
      if (option) window.__loreloopRecord({op: 'select', locator: target, option});
    } else {
      const value = clean(el.value);
      if (value) window.__loreloopRecord({op: 'fill', locator: target, value});
    }
  }, true);
})();
"""


def record_scenario(
    browser,
    artifacts: ArtifactStore,
    url: str,
    *,
    title: str | None = None,
    risk: str = "read-only",
    allow_writes: bool = False,
    wait_for_operator: Callable[[str], str] = input,
) -> WebScenario:
    """Record one headed browser journey until the operator presses Enter."""
    require_http_url(url)
    if risk not in {"read-only", "writes"}:
        raise WebScenarioError("recorded scenario risk must be read-only or writes")
    if risk == "writes" and not allow_writes:
        raise WebScenarioError("write-risk recording requires --allow-writes")
    page = browser.page
    events: list[dict] = []

    def capture(_source, event) -> None:
        step = _event_step(event) if isinstance(event, dict) else None
        if step is not None and len(events) < 1_000:
            events.append(step)

    page.expose_binding("__loreloopRecord", capture)
    page.context.add_init_script(_RECORDER_JS)
    browser.set_network_policy(url, allow_writes=allow_writes)
    page.goto(url, wait_until="domcontentloaded")
    wait_for_operator(
        "Complete the browser journey, then return here and press Enter to stop recording: "
    )
    observation = browser.observe_current()
    artifact = artifacts.save_observation(observation)[0]
    parsed = urlsplit(url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    initial = parsed.path or "/"
    if parsed.query:
        initial += f"?{parsed.query}"
    if parsed.fragment:
        initial += f"#{parsed.fragment}"
    steps: list[dict] = [{"goto": initial}]
    for step in events:
        if not steps or steps[-1] != step:
            steps.append(step)
    assertions: list[ScenarioAssertion] = []
    if observation.title.strip():
        assertions.append(ScenarioAssertion("title-contains", observation.title.strip()[:512]))
    if observation.headings:
        assertions.append(ScenarioAssertion("contains", observation.headings[0][:512]))
    if not assertions:
        raise WebScenarioError("recorded page has no stable title or heading assertion")
    digest_material = f"{url}\0{observation.snapshot_hash}\0{steps!r}"
    scenario_id = f"recorded-{hashlib.sha256(digest_material.encode()).hexdigest()[:20]}"
    return WebScenario(
        scenario_id,
        title or f"Recorded journey: {observation.title or parsed.path}",
        parse_action_script({"version": 1, "base": base, "steps": steps}),
        tuple(assertions),
        risk,
        tags=("recorded", "web"),
        source_artifact=artifact,
        source_snapshot=observation.snapshot_hash,
    )


def _event_step(event: dict) -> dict | None:
    operation = event.get("op")
    locator = event.get("locator")
    if operation not in {"click", "fill", "select"} or not isinstance(locator, dict):
        return None
    expected = {"op", "locator"} | (
        {"value"} if operation == "fill" else {"option"} if operation == "select" else set()
    )
    if set(event) != expected or not locator or set(locator) - _LOCATOR_KEYS:
        return None
    clean_locator: dict = {}
    for key, value in locator.items():
        if key == "nth":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
                return None
            clean_locator[key] = value
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > 512:
            return None
        if _SENSITIVE.search(value):
            return None
        clean_locator[key] = value.strip()
    if operation == "click":
        return {"click": clean_locator}
    if operation == "fill":
        value = event.get("value")
        return (
            {"fill": {**clean_locator, "value": value}}
            if isinstance(value, str) and value and len(value) <= 512
            else None
        )
    option = event.get("option")
    return (
        {"select": {**clean_locator, "option": option}}
        if isinstance(option, str) and option and len(option) <= 512
        else None
    )
