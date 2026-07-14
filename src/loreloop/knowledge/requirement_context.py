"""Load committed requirement documents into a current-session task boundary."""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .repos import RepoConfigError, resolve_repo, validate_repo_name

_MAX_DOCUMENT_BYTES = 512_000
_MAX_TOTAL_BYTES = 1_500_000


class RequirementContextError(ValueError):
    """A requested requirement document is not a safe committed Git blob."""


@dataclass(frozen=True, slots=True)
class RequirementMaterial:
    locator: str
    commit: str
    sha256: str
    text: str

    def evidence_payload(self) -> dict[str, str]:
        return {"locator": self.locator, "commit": self.commit, "sha256": self.sha256}


def _locator(value: str) -> tuple[str, str]:
    alias = "."
    path = value
    if value.startswith("repo:"):
        alias, separator, path = value[5:].partition("/")
        if not separator:
            raise RequirementContextError(f"invalid requirement locator: {value}")
        try:
            validate_repo_name(alias)
        except RepoConfigError as exc:
            raise RequirementContextError(str(exc)) from exc
    parsed = PurePosixPath(path)
    if (
        not path
        or path.startswith("/")
        or "\\" in path
        or str(parsed) != path
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        raise RequirementContextError(f"invalid requirement locator: {value}")
    return alias, path


def _git(repo: Path, *args: str) -> bytes:
    completed = subprocess.run(["git", *args], cwd=repo, check=False, capture_output=True)
    if completed.returncode != 0:
        raise RequirementContextError("requirement material is not committed at HEAD")
    return completed.stdout


def load_requirement_materials(
    workdir: Path, locators: tuple[str, ...]
) -> tuple[RequirementMaterial, ...]:
    """Read exact HEAD blobs; worktree copies cannot silently replace them."""
    materials: list[RequirementMaterial] = []
    total = 0
    for locator in locators:
        alias, path = _locator(locator)
        try:
            repo = resolve_repo(workdir, alias)
        except RepoConfigError as exc:
            raise RequirementContextError(str(exc)) from exc
        commit = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
        data = _git(repo, "cat-file", "blob", f"HEAD:{path}")
        if len(data) > _MAX_DOCUMENT_BYTES:
            raise RequirementContextError(f"requirement material is too large: {locator}")
        total += len(data)
        if total > _MAX_TOTAL_BYTES:
            raise RequirementContextError("combined requirement materials are too large")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RequirementContextError(
                f"requirement material is not UTF-8: {locator}"
            ) from exc
        materials.append(
            RequirementMaterial(locator, commit, hashlib.sha256(data).hexdigest(), text)
        )
    return tuple(materials)


def render_requirement_context(materials: tuple[RequirementMaterial, ...]) -> str:
    if not materials:
        return ""
    lines = ["# Requirement materials (committed Git blobs)", ""]
    for material in materials:
        lines.extend(
            (
                f"## {material.locator}",
                "",
                f"- Commit: `{material.commit}`",
                f"- SHA-256: `{material.sha256}`",
                "",
                material.text.rstrip(),
                "",
            )
        )
    return "\n".join(lines).rstrip() + "\n"
