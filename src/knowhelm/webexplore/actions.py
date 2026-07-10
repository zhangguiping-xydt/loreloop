"""Replayable browser interaction scripts.

The action DSL is intentionally small and data-only: a versioned JSON object
with a linear list of steps. It is meant to anchor knowledge to a reproducible
path through a web UI, not to become a programming language.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse

from .browser import Observation, same_origin

_OPS = {"goto", "click", "fill", "select", "wait"}
_TOP_LEVEL_KEYS = {"version", "base", "steps"}
_LOCATOR_KEYS = {"text", "label", "role", "nth"}
_DANGEROUS_CLICK = re.compile(
    r"\b(delete|remove|pay|unsubscribe|transfer)\b|"
    r"删除|移除|支付|付款|确认付款|确认订单|转账|退订",
    re.IGNORECASE,
)
_MAX_WAIT_MS = 10_000


class ActionScriptError(ValueError):
    """The script JSON is malformed or outside the DSL contract."""


class ActionBlocked(RuntimeError):
    """Execution hit a hard safety rule."""


class ActionFailed(RuntimeError):
    """Execution could not complete a deterministic step."""


@dataclass(frozen=True)
class ActionStep:
    op: str
    arg: Any

    def to_json(self) -> dict[str, Any]:
        return {self.op: self.arg}


@dataclass(frozen=True)
class ActionScript:
    version: int
    base: str
    steps: list[ActionStep]

    def to_json(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "base": self.base,
            "steps": [step.to_json() for step in self.steps],
        }

    @property
    def canonical_json(self) -> str:
        return json.dumps(
            self.to_json(), ensure_ascii=False, sort_keys=True, separators=(",", ":")
        )

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.canonical_json.encode()).hexdigest()


@dataclass(frozen=True)
class StepTrace:
    index: int
    action: dict[str, Any]
    status: str
    detail: str
    elapsed_ms: int
    url: str | None = None

    def to_json(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "index": self.index,
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }
        if self.url is not None:
            payload["url"] = self.url
        return payload


@dataclass(frozen=True)
class ActionExecution:
    script_digest: str
    status: str
    steps: list[StepTrace]
    final_observation: Observation | None = None
    reason: str | None = None

    @property
    def succeeded(self) -> bool:
        return self.status == "completed" and self.final_observation is not None

    @property
    def steps_completed(self) -> int:
        return sum(1 for step in self.steps if step.status == "completed")

    def trace_artifact_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": "interaction_trace",
            "script_digest": self.script_digest,
            "status": self.status,
            "steps_completed": self.steps_completed,
            "steps": [step.to_json() for step in self.steps],
        }
        if self.reason:
            payload["reason"] = self.reason
        if self.final_observation is not None:
            payload["final_url"] = self.final_observation.url
            payload["final_snapshot"] = self.final_observation.snapshot_hash
        return payload


def load_action_script(path: Path) -> ActionScript:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ActionScriptError(f"cannot read action script {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ActionScriptError(f"action script is invalid JSON: {exc}") from exc
    return parse_action_script(raw)


def parse_action_script(raw: Any) -> ActionScript:
    if not isinstance(raw, dict):
        raise ActionScriptError("action script must be a JSON object")
    unknown = set(raw) - _TOP_LEVEL_KEYS
    if unknown:
        raise ActionScriptError(f"unknown top-level keys: {sorted(unknown)}")
    if raw.get("version") != 1:
        raise ActionScriptError("action script version must be 1")
    base = raw.get("base")
    if not isinstance(base, str) or not _is_http_origin(base):
        raise ActionScriptError("action script base must be an absolute http(s) URL")
    steps_raw = raw.get("steps")
    if not isinstance(steps_raw, list) or not steps_raw:
        raise ActionScriptError("action script steps must be a non-empty array")
    steps = [_parse_step(step, i) for i, step in enumerate(steps_raw)]
    return ActionScript(version=1, base=base, steps=steps)


def validate_script_origin(script: ActionScript, cli_base: str) -> None:
    if not _is_http_origin(cli_base):
        raise ActionScriptError("verify target must be an absolute http(s) URL")
    if not same_origin(script.base, cli_base):
        raise ActionScriptError(
            "action script base must be same-origin with the verify target"
        )


def script_locator(digest: str) -> str:
    return f"script:{digest}"


def digest_from_locator(locator: str) -> str | None:
    if not locator.startswith("script:"):
        return None
    digest = locator.removeprefix("script:")
    return digest if re.fullmatch(r"[0-9a-f]{64}", digest) else None


def execute_action_script(
    browser,
    script: ActionScript,
    *,
    base_url: str | None = None,
    allow_writes: bool = False,
    timeout_ms: int = 10_000,
) -> ActionExecution:
    if base_url is not None and not same_origin(script.base, base_url):
        return ActionExecution(
            script.digest,
            "blocked",
            [],
            reason="script base is not same-origin with execution target",
        )
    page = getattr(browser, "page", None)
    if page is None:
        raise ActionFailed("browser does not expose a Playwright page")

    trace: list[StepTrace] = []
    for index, step in enumerate(script.steps):
        started = time.monotonic()
        try:
            _perform_step(page, script, step, allow_writes=allow_writes, timeout_ms=timeout_ms)
            _ensure_same_origin(page, script.base)
        except ActionBlocked as exc:
            trace.append(_trace_step(index, step, "blocked", exc, started, page))
            return ActionExecution(script.digest, "blocked", trace, reason=str(exc))
        except Exception as exc:
            trace.append(_trace_step(index, step, "failed", exc, started, page))
            return ActionExecution(script.digest, "failed", trace, reason=str(exc))
        trace.append(_trace_step(index, step, "completed", "ok", started, page))

    try:
        final = browser.observe_current()
    except Exception as exc:
        return ActionExecution(script.digest, "failed", trace, reason=str(exc))
    return ActionExecution(script.digest, "completed", trace, final_observation=final)


def _parse_step(raw: Any, index: int) -> ActionStep:
    if not isinstance(raw, dict) or len(raw) != 1:
        raise ActionScriptError(f"step {index} must be an object with exactly one action")
    op, arg = next(iter(raw.items()))
    if op not in _OPS:
        raise ActionScriptError(f"step {index} has unknown action {op!r}")
    if op == "goto":
        if not isinstance(arg, str) or not arg.strip():
            raise ActionScriptError(f"step {index} goto must be a non-empty relative path")
        if _has_origin(arg):
            raise ActionScriptError(f"step {index} goto must not include an origin")
        return ActionStep(op, arg)
    if op == "click":
        return ActionStep(op, _parse_locator(arg, index))
    if op == "fill":
        if not isinstance(arg, dict) or "value" not in arg:
            raise ActionScriptError(f"step {index} fill must include value")
        locator = _parse_locator({k: v for k, v in arg.items() if k != "value"}, index)
        value = arg["value"]
        if not isinstance(value, str):
            raise ActionScriptError(f"step {index} fill value must be a string")
        return ActionStep(op, {**locator, "value": value})
    if op == "select":
        if not isinstance(arg, dict) or "option" not in arg:
            raise ActionScriptError(f"step {index} select must include option")
        locator = _parse_locator({k: v for k, v in arg.items() if k != "option"}, index)
        option = arg["option"]
        if not isinstance(option, str) or not option.strip():
            raise ActionScriptError(f"step {index} select option must be a non-empty string")
        return ActionStep(op, {**locator, "option": option})
    return ActionStep(op, _parse_wait(arg, index))


def _parse_locator(raw: Any, index: int) -> dict[str, Any]:
    if isinstance(raw, str):
        key, sep, value = raw.partition("=")
        if sep and key in {"text", "label", "role"} and value.strip():
            raw = {key: value}
    if not isinstance(raw, dict):
        raise ActionScriptError(f"step {index} locator must be an object")
    unknown = set(raw) - _LOCATOR_KEYS
    if unknown:
        raise ActionScriptError(f"step {index} locator has unknown keys: {sorted(unknown)}")
    if not any(raw.get(key) for key in ("text", "label", "role")):
        raise ActionScriptError(f"step {index} locator needs text, label or role")
    locator: dict[str, Any] = {}
    for key in ("text", "label", "role"):
        value = raw.get(key)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise ActionScriptError(f"step {index} locator {key} must be a non-empty string")
        locator[key] = value
    nth = raw.get("nth")
    if nth is not None:
        if not isinstance(nth, int) or nth < 0:
            raise ActionScriptError(f"step {index} locator nth must be a non-negative integer")
        locator["nth"] = nth
    return locator


def _parse_wait(raw: Any, index: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ActionScriptError(f"step {index} wait must be an object")
    unknown = set(raw) - {"text", "url", "ms"}
    if unknown:
        raise ActionScriptError(f"step {index} wait has unknown keys: {sorted(unknown)}")
    present = [key for key in ("text", "url", "ms") if key in raw]
    if len(present) != 1:
        raise ActionScriptError(f"step {index} wait must declare exactly one condition")
    key = present[0]
    value = raw[key]
    if key in {"text", "url"}:
        if not isinstance(value, str) or not value.strip():
            raise ActionScriptError(f"step {index} wait {key} must be a non-empty string")
        if key == "text":
            try:
                re.compile(value)
            except re.error as exc:
                raise ActionScriptError(f"step {index} wait text regex is invalid: {exc}") from exc
        if key == "url" and _has_origin(value):
            raise ActionScriptError(f"step {index} wait url must not include an origin")
    else:
        if not isinstance(value, int) or value < 0 or value > _MAX_WAIT_MS:
            raise ActionScriptError(f"step {index} wait ms must be 0..{_MAX_WAIT_MS}")
    return {key: value}


def _perform_step(page, script: ActionScript, step: ActionStep, *, allow_writes: bool, timeout_ms: int) -> None:
    if step.op == "goto":
        target = urljoin(script.base, step.arg)
        if not same_origin(target, script.base):
            raise ActionBlocked("goto resolved outside the script origin")
        page.goto(target, wait_until="domcontentloaded", timeout=timeout_ms)
        _wait_after_action(page)
        return
    if step.op == "click":
        locator = _resolve_locator(page, step.arg)
        meta = _element_metadata(locator)
        label = _label_from_meta(meta)
        if _DANGEROUS_CLICK.search(label):
            raise ActionBlocked(f"refused to click dangerous element text: {label!r}")
        if not allow_writes and _is_post_submit(meta):
            raise ActionBlocked("refused to submit a POST form without --allow-writes")
        before_pages = len(page.context.pages)
        locator.click(timeout=timeout_ms)
        _wait_after_action(page)
        if len(page.context.pages) > before_pages:
            raise ActionFailed("click opened a new tab or window")
        return
    if step.op == "fill":
        value = step.arg["value"]
        locator = _resolve_locator(page, _strip_action_value(step.arg, "value"))
        meta = _element_metadata(locator)
        if meta["type"] == "password":
            raise ActionBlocked("refused to fill a password field")
        if not allow_writes and not _is_idempotent_control(meta):
            raise ActionBlocked("refused to fill a non-search/filter control without --allow-writes")
        locator.fill(value, timeout=timeout_ms)
        if locator.input_value(timeout=timeout_ms) != value:
            raise ActionFailed("filled value did not round-trip")
        return
    if step.op == "select":
        option = step.arg["option"]
        locator = _resolve_locator(page, _strip_action_value(step.arg, "option"))
        meta = _element_metadata(locator)
        if not allow_writes and not _is_idempotent_control(meta):
            raise ActionBlocked("refused to select a non-search/filter control without --allow-writes")
        try:
            locator.select_option(label=option, timeout=timeout_ms)
        except Exception:
            locator.select_option(value=option, timeout=timeout_ms)
        selected = locator.evaluate(
            "el => el.options && el.selectedIndex >= 0 ? "
            "el.options[el.selectedIndex].text || el.value : el.value"
        )
        if selected != option:
            raise ActionFailed("selected option did not round-trip")
        return
    _wait_for(page, script, step.arg, timeout_ms=timeout_ms)


def _resolve_locator(page, locator: dict[str, Any]):
    if "label" in locator:
        candidate = page.get_by_label(locator["label"], exact=True)
    elif "text" in locator and "role" in locator:
        candidate = page.get_by_role(locator["role"], name=locator["text"], exact=True)
    elif "text" in locator:
        candidate = page.get_by_text(locator["text"], exact=True)
    else:
        candidate = page.get_by_role(locator["role"])

    visible = []
    count = candidate.count()
    for i in range(min(count, 100)):
        item = candidate.nth(i)
        try:
            if item.is_visible():
                visible.append(item)
        except Exception:
            continue
    nth = locator.get("nth")
    if nth is not None:
        if nth >= len(visible):
            raise ActionFailed(f"locator matched {len(visible)} visible element(s), nth={nth}")
        return visible[nth]
    if not visible:
        raise ActionFailed("locator matched no visible elements")
    if len(visible) > 1:
        raise ActionFailed("locator is ambiguous; add nth")
    return visible[0]


def _element_metadata(locator) -> dict[str, Any]:
    return locator.evaluate(
        """el => {
            const form = el.closest('form');
            const labels = el.labels ? [...el.labels].map(l => l.innerText.trim()).filter(Boolean) : [];
            const text = (
                el.innerText || el.value || el.getAttribute('aria-label') ||
                el.getAttribute('placeholder') || labels.join(' ') || ''
            ).replace(/\\s+/g, ' ').trim();
            return {
                tag: el.tagName.toLowerCase(),
                type: (el.getAttribute('type') || '').toLowerCase(),
                text,
                name: el.getAttribute('name') || '',
                id: el.id || '',
                placeholder: el.getAttribute('placeholder') || '',
                ariaLabel: el.getAttribute('aria-label') || '',
                formMethod: form ? (form.getAttribute('method') || 'get').toLowerCase() : '',
                inSearch: !!el.closest('[role=search], search, form[role=search]'),
                buttonType: (el.getAttribute('type') || '').toLowerCase(),
            };
        }"""
    )


def _wait_for(page, script: ActionScript, spec: dict[str, Any], *, timeout_ms: int) -> None:
    if "ms" in spec:
        page.wait_for_timeout(spec["ms"])
        return
    deadline = time.monotonic() + (min(timeout_ms, _MAX_WAIT_MS) / 1000)
    if "url" in spec:
        expected = spec["url"]
        prefix = urljoin(script.base, expected)
        while time.monotonic() < deadline:
            if page.url.startswith(prefix):
                return
            page.wait_for_timeout(100)
        raise ActionFailed(f"url did not start with {prefix!r}")
    pattern = re.compile(spec["text"])
    while time.monotonic() < deadline:
        try:
            body = page.inner_text("body", timeout=1000)
        except Exception:
            body = ""
        if pattern.search(body):
            return
        page.wait_for_timeout(100)
    raise ActionFailed(f"text regex did not appear: {spec['text']!r}")


def _wait_after_action(page) -> None:
    try:
        page.wait_for_load_state("networkidle", timeout=3000)
    except Exception:
        pass
    try:
        page.wait_for_function(
            """() => new Promise(resolve => {
                let last = document.body ? document.body.innerText : '';
                let stable = 0;
                const tick = () => {
                    const next = document.body ? document.body.innerText : '';
                    stable = next === last ? stable + 1 : 0;
                    last = next;
                    if (stable >= 2) resolve(true);
                    else setTimeout(tick, 100);
                };
                tick();
            })""",
            timeout=3000,
        )
    except Exception:
        pass


def _ensure_same_origin(page, base: str) -> None:
    if not same_origin(page.url, base):
        raise ActionBlocked(f"navigation left origin: {page.url}")


def _trace_step(index: int, step: ActionStep, status: str, detail: object, started: float, page) -> StepTrace:
    return StepTrace(
        index=index,
        action=step.to_json(),
        status=status,
        detail=str(detail),
        elapsed_ms=int((time.monotonic() - started) * 1000),
        url=getattr(page, "url", None),
    )


def _strip_action_value(arg: dict[str, Any], key: str) -> dict[str, Any]:
    return {k: v for k, v in arg.items() if k != key}


def _is_http_origin(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _has_origin(value: str) -> bool:
    parsed = urlparse(value)
    return bool(parsed.scheme or parsed.netloc) or value.startswith("//")


def _label_from_meta(meta: dict[str, Any]) -> str:
    return " ".join(
        str(meta.get(key, "")) for key in ("text", "ariaLabel", "placeholder", "name", "id")
    ).strip()


def _is_post_submit(meta: dict[str, Any]) -> bool:
    if meta.get("formMethod") != "post":
        return False
    tag = meta.get("tag")
    typ = meta.get("buttonType") or meta.get("type")
    return tag == "button" or (tag == "input" and typ in {"submit", "button"})


def _is_idempotent_control(meta: dict[str, Any]) -> bool:
    if meta.get("inSearch"):
        return True
    method = meta.get("formMethod")
    return method in {"", "get", "dialog"}
