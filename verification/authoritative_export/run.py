#!/usr/bin/env python3
"""Run the frozen authoritative-export proof contract without OMO/plugin machinery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from dataclasses import asdict, dataclass
from pathlib import Path

CONTRACT = Path("docs/verification/authoritative-export-v5.md")
MIN_DOGFOOD_TRACKED_FILES = 5_000
MIN_DOGFOOD_UNCOMPRESSED_BYTES = 50 * 1024 * 1024
_PUBLIC_GITHUB_REMOTE = re.compile(r"https://github\.com/[^/\s]+/[^/\s]+(?:\.git)?")
_PROOF_ENV_PASSTHROUGH = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TZ",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
)


@dataclass(frozen=True, slots=True)
class GateResult:
    name: str
    command: tuple[str, ...]
    exit_code: int
    duration_seconds: float
    log: str
    log_sha256: str


def _git_environment() -> dict[str, str]:
    environment = {
        name: value for name, value in os.environ.items() if not name.startswith("GIT_")
    }
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    return environment


def _anonymous_git_environment(home: Path) -> dict[str, str]:
    environment = _git_environment()
    for name in ("GH_TOKEN", "GITHUB_TOKEN", "SSH_ASKPASS"):
        environment.pop(name, None)
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home),
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
        }
    )
    return environment


def _playwright_browsers_path() -> Path | None:
    """Locate an installed browser cache before replacing the operator HOME."""
    candidates: list[Path] = []
    configured = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if configured and configured != "0":
        candidates.append(Path(configured).expanduser())
    cache_home = os.environ.get("XDG_CACHE_HOME")
    if cache_home:
        candidates.append(Path(cache_home).expanduser() / "ms-playwright")
    operator_home = os.environ.get("HOME")
    if operator_home:
        candidates.append(Path(operator_home).expanduser() / ".cache" / "ms-playwright")
    candidates.append(Path.home() / ".cache" / "ms-playwright")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate.resolve()
    return None


def _proof_environment(
    checkout: Path,
    home: Path,
    *,
    playwright_browsers_path: Path | None = None,
) -> dict[str, str]:
    """Build a closed gate environment without caller-controlled test selection."""
    environment = {
        name: os.environ[name] for name in _PROOF_ENV_PASSTHROUGH if name in os.environ
    }
    environment.update(
        {
            "HOME": str(home),
            "XDG_CONFIG_HOME": str(home),
            "PYTHONPATH": str(checkout / "src"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONHASHSEED": "0",
            "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    if playwright_browsers_path is not None:
        environment["PLAYWRIGHT_BROWSERS_PATH"] = str(playwright_browsers_path)
    return environment


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(repo: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_git_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.strip()


def _run_gate(
    name: str,
    command: tuple[str, ...],
    *,
    cwd: Path,
    logs: Path,
    env: dict[str, str],
    timeout: int,
) -> GateResult:
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            env=env,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        output = (
            f"$ {' '.join(command)}\n\n[stdout]\n{completed.stdout}"
            f"\n[stderr]\n{completed.stderr}"
        )
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = (
            f"$ {' '.join(command)}\n\n[TIMEOUT after {timeout}s]\n"
            f"[stdout]\n{exc.stdout or ''}\n[stderr]\n{exc.stderr or ''}"
        )
        exit_code = 124
    except OSError as exc:
        output = f"$ {' '.join(command)}\n\n[EXECUTION ERROR]\n{exc}\n"
        exit_code = 127
    duration = time.monotonic() - started
    log = logs / f"{len(tuple(logs.glob('*.log'))) + 1:02d}-{name}.log"
    log.write_text(output, encoding="utf-8")
    return GateResult(
        name,
        command,
        exit_code,
        round(duration, 3),
        str(log.relative_to(logs.parent)),
        _sha256(log),
    )


def _clone_at(source: Path, destination: Path, commit: str) -> None:
    subprocess.run(
        ["git", "clone", "--quiet", "--no-hardlinks", str(source), str(destination)],
        env=_git_environment(),
        check=True,
    )
    subprocess.run(
        ["git", "checkout", "--quiet", "--detach", commit],
        cwd=destination,
        env=_git_environment(),
        check=True,
    )


def _filesystem(value: str) -> tuple[str, Path]:
    label, separator, raw_path = value.partition("=")
    if not separator or not label or not raw_path:
        raise argparse.ArgumentTypeError("filesystem must be LABEL=/existing/path")
    path = Path(raw_path).expanduser().resolve()
    if not path.is_dir():
        raise argparse.ArgumentTypeError(f"filesystem path is not a directory: {path}")
    return label, path


def _filesystem_metadata(items: list[tuple[str, Path]]) -> list[dict[str, int | str]]:
    by_label = {label: path for label, path in items}
    if set(by_label) != {"xfs", "ext4"} or len(items) != 2:
        raise SystemExit("proof requires exactly one xfs and one ext4 filesystem")
    evidence: list[dict[str, int | str]] = []
    devices: set[int] = set()
    for label, path in sorted(by_label.items()):
        fields: dict[str, str] = {}
        for field in ("FSTYPE", "SOURCE", "TARGET"):
            completed = subprocess.run(
                ["findmnt", "-n", "-o", field, "-T", str(path)],
                check=False,
                capture_output=True,
                text=True,
            )
            value = completed.stdout.strip()
            if completed.returncode != 0 or not value:
                raise SystemExit(f"cannot identify filesystem for {path}")
            fields[field.lower()] = value
        if fields["fstype"] != label:
            raise SystemExit(
                f"filesystem label {label!r} does not match {fields['fstype']!r} at {path}"
            )
        device = path.stat().st_dev
        if device in devices:
            raise SystemExit("xfs and ext4 proof roots must be different mounted devices")
        devices.add(device)
        evidence.append(
            {
                "label": label,
                "path": str(path),
                "device": device,
                **fields,
            }
        )
    return evidence


def _dogfood_metadata(source: Path, commit: str, public_ref: str) -> dict[str, object]:
    try:
        remote_url = _git(source, "remote", "get-url", "origin")
    except subprocess.CalledProcessError as exc:
        raise SystemExit("dogfood repository must have an origin remote") from exc
    if _PUBLIC_GITHUB_REMOTE.fullmatch(remote_url) is None:
        raise SystemExit("dogfood origin must be a public GitHub HTTPS repository URL")
    if (
        not public_ref.startswith(("refs/heads/", "refs/tags/"))
        or "*" in public_ref
        or public_ref.endswith("/")
    ):
        raise SystemExit("dogfood public ref must name one concrete branch or tag")
    tracked_files = len(
        _git(source, "ls-tree", "-r", "--name-only", commit).splitlines()
    )
    if tracked_files < MIN_DOGFOOD_TRACKED_FILES:
        raise SystemExit(
            f"dogfood repository must contain at least {MIN_DOGFOOD_TRACKED_FILES} tracked files"
        )
    return {
        "remote_url": remote_url,
        "public_ref": public_ref,
        "tracked_files": tracked_files,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=Path.cwd())
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--dogfood-repo", type=Path)
    parser.add_argument("--dogfood-commit", default="HEAD")
    parser.add_argument("--dogfood-public-ref", default="refs/heads/main")
    parser.add_argument("--filesystem", action="append", type=_filesystem, default=[])
    parser.add_argument("--force", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.source.expanduser().resolve()
    commit = _git(source, "rev-parse", args.commit)
    output = args.output.expanduser().absolute()
    if output == source or output in source.parents:
        raise SystemExit("output must not be the source repository or one of its parents")
    if output.is_symlink() or (output.exists() and not output.is_dir()):
        raise SystemExit(f"unsafe output directory: {output}")
    replace_output = False
    if output.exists() and any(output.iterdir()):
        if not args.force:
            raise SystemExit(f"output directory is not empty: {output}")
        marker = output / "manifest.json"
        try:
            existing = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"refusing to replace a directory without a proof manifest: {output}"
            ) from exc
        if not isinstance(existing, dict) or existing.get("schema_version") != 1:
            raise SystemExit(f"refusing to replace an unrecognized proof directory: {output}")
        replace_output = True
    if args.dogfood_repo is None:
        raise SystemExit("proof requires --dogfood-repo and a frozen large-project commit")
    dogfood_source = args.dogfood_repo.expanduser().resolve()
    dogfood_commit = _git(dogfood_source, "rev-parse", args.dogfood_commit)
    dogfood_metadata = _dogfood_metadata(
        dogfood_source, dogfood_commit, args.dogfood_public_ref
    )
    filesystem_evidence = _filesystem_metadata(args.filesystem)
    if replace_output:
        shutil.rmtree(output)
    output.mkdir(parents=True, exist_ok=True)
    logs = output / "logs"
    logs.mkdir()
    artifacts = output / "artifacts"
    artifacts.mkdir()
    scratch = Path(tempfile.mkdtemp(prefix="loreloop-authoritative-proof-"))
    gates: list[GateResult] = []
    dogfood: dict[str, object] | None = None
    wheel: dict[str, object] | None = None
    try:
        checkout = scratch / "loreloop"
        _clone_at(source, checkout, commit)
        contract = checkout / CONTRACT
        if not contract.is_file():
            raise SystemExit(f"contract is absent from frozen commit: {CONTRACT}")
        proof_home = scratch / "proof-home"
        proof_home.mkdir(mode=0o700)
        playwright_browsers_path = _playwright_browsers_path()
        env = _proof_environment(
            checkout,
            proof_home,
            playwright_browsers_path=playwright_browsers_path,
        )
        python = sys.executable
        gate_specs: list[tuple[str, tuple[str, ...], int]] = [
            ("pytest-collect", (python, "-m", "pytest", "--collect-only", "-q"), 300),
            ("full-test-suite", (python, "-m", "pytest", "-q"), 900),
            ("ruff", ("ruff", "check", "src", "tests", "plugins"), 300),
            (
                "bandit-medium-high",
                ("bandit", "-q", "-r", "src/loreloop", "-x", "src/loreloop/example", "-lll"),
                300,
            ),
            (
                "six-seven-eight-and-aggregate",
                (
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_knowledge_document_export.py",
                    "tests/test_document_archive.py",
                    "tests/test_document_detector_matrix.py",
                    "tests/test_document_git_snapshot.py",
                ),
                600,
            ),
            (
                "capsule-mutants-and-trust",
                (
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_document_capsule.py",
                    "tests/test_document_capsule_replay.py",
                    "tests/test_cli_capsule_replay.py",
                    "tests/test_authoritative_trust.py",
                ),
                600,
            ),
            (
                "wheel",
                (
                    "uv",
                    "build",
                    "--wheel",
                    "--out-dir",
                    str(scratch / "dist"),
                ),
                300,
            ),
            (
                "cli-package-help",
                (python, "-m", "loreloop.cli", "knowledge", "export", "--help"),
                60,
            ),
        ]
        for name, command, timeout in gate_specs:
            gates.append(
                _run_gate(name, command, cwd=checkout, logs=logs, env=env, timeout=timeout)
            )
        wheels = tuple((scratch / "dist").glob("*.whl"))
        if len(wheels) == 1:
            wheel_source = wheels[0]
            wheel_target = artifacts / wheel_source.name
            shutil.copyfile(wheel_source, wheel_target)
            with zipfile.ZipFile(wheel_target) as archive:
                wheel_entries = tuple(sorted(archive.namelist()))
            wheel = {
                "path": str(wheel_target.relative_to(output)),
                "bytes": wheel_target.stat().st_size,
                "sha256": _sha256(wheel_target),
                "entries": wheel_entries,
            }
            wheel_venv = scratch / "wheel-venv"
            wheel_env = dict(env)
            wheel_env.pop("PYTHONPATH", None)
            gates.append(
                _run_gate(
                    "wheel-venv",
                    ("uv", "venv", "--python", python, str(wheel_venv)),
                    cwd=checkout,
                    logs=logs,
                    env=wheel_env,
                    timeout=120,
                )
            )
            gates.append(
                _run_gate(
                    "wheel-install",
                    (
                        "uv",
                        "pip",
                        "install",
                        "--python",
                        str(wheel_venv / "bin/python"),
                        str(wheel_target),
                    ),
                    cwd=checkout,
                    logs=logs,
                    env=wheel_env,
                    timeout=180,
                )
            )
            wheel_repo = scratch / "wheel-smoke-repo"
            wheel_repo.mkdir()
            _git(wheel_repo, "init", "-q")
            _git(wheel_repo, "config", "user.name", "LoreLoop Proof")
            _git(wheel_repo, "config", "user.email", "proof@example.invalid")
            (wheel_repo / "app.py").write_text(
                '@app.get("/health")\ndef health(): return True\n', encoding="utf-8"
            )
            _git(wheel_repo, "add", "-A")
            _git(wheel_repo, "commit", "-q", "-m", "fixture")
            wheel_cli = wheel_venv / "bin/loreloop"
            wheel_package = scratch / "wheel-smoke.zip"
            gates.append(
                _run_gate(
                    "wheel-export-smoke",
                    (
                        str(wheel_cli),
                        "knowledge",
                        "export",
                        "--format",
                        "package",
                        "--output",
                        str(wheel_package),
                    ),
                    cwd=wheel_repo,
                    logs=logs,
                    env=wheel_env,
                    timeout=180,
                )
            )
            gates.append(
                _run_gate(
                    "wheel-replay-smoke",
                    (str(wheel_cli), "knowledge", "replay", str(wheel_package)),
                    cwd=wheel_repo,
                    logs=logs,
                    env=wheel_env,
                    timeout=180,
                )
            )
        for label, root in args.filesystem:
            base = Path(tempfile.mkdtemp(prefix=f"loreloop-{label}-", dir=root))
            try:
                command = (
                    python,
                    "-m",
                    "pytest",
                    "-q",
                    "tests/test_authoritative_publish.py",
                    f"--basetemp={base / 'pytest'}",
                )
                gates.append(
                    _run_gate(
                        f"publication-{label}",
                        command,
                        cwd=checkout,
                        logs=logs,
                        env=env,
                        timeout=300,
                    )
                )
            finally:
                shutil.rmtree(base, ignore_errors=True)
        if args.dogfood_repo is not None:
            dogfood_checkout = scratch / "dogfood"
            _clone_at(dogfood_source, dogfood_checkout, dogfood_commit)
            public_target = "refs/loreloop-proof/public-dogfood"
            anonymous_home = scratch / "anonymous-git-home"
            anonymous_home.mkdir(mode=0o700)
            gates.append(
                _run_gate(
                    "dogfood-public-fetch",
                    (
                        "git",
                        "-c",
                        "credential.helper=",
                        "fetch",
                        "--quiet",
                        "--no-tags",
                        str(dogfood_metadata["remote_url"]),
                        f"+{dogfood_metadata['public_ref']}:{public_target}",
                    ),
                    cwd=dogfood_checkout,
                    logs=logs,
                    env=_anonymous_git_environment(anonymous_home),
                    timeout=300,
                )
            )
            if gates[-1].exit_code == 0:
                dogfood_metadata["public_ref_tip"] = _git(
                    dogfood_checkout, "rev-parse", public_target
                )
                gates.append(
                    _run_gate(
                        "dogfood-public-ancestry",
                        (
                            "git",
                            "merge-base",
                            "--is-ancestor",
                            dogfood_commit,
                            public_target,
                        ),
                        cwd=dogfood_checkout,
                        logs=logs,
                        env=_git_environment(),
                        timeout=60,
                    )
                )
            package = scratch / "dogfood-knowledge.zip"
            export_command = (
                python,
                "-m",
                "loreloop.cli",
                "knowledge",
                "export",
                "--format",
                "package",
                "--output",
                str(package),
                "--project-name",
                dogfood_checkout.name,
            )
            gates.append(
                _run_gate(
                    "large-project-export",
                    export_command,
                    cwd=dogfood_checkout,
                    logs=logs,
                    env=env,
                    timeout=1200,
                )
            )
            replay_command = (
                python,
                "-m",
                "loreloop.cli",
                "knowledge",
                "replay",
                str(package),
            )
            gates.append(
                _run_gate(
                    "large-project-replay",
                    replay_command,
                    cwd=scratch,
                    logs=logs,
                    env=env,
                    timeout=1200,
                )
            )
            if package.is_file():
                retained_package = artifacts / "dogfood-knowledge.zip"
                shutil.copyfile(package, retained_package)
                with zipfile.ZipFile(package) as archive:
                    names = tuple(sorted(archive.namelist()))
                    uncompressed = sum(item.file_size for item in archive.infolist())
                dogfood = {
                    "source": str(dogfood_source),
                    "commit": dogfood_commit,
                    **dogfood_metadata,
                    "zip_bytes": package.stat().st_size,
                    "uncompressed_bytes": uncompressed,
                    "zip_sha256": _sha256(retained_package),
                    "path": str(retained_package.relative_to(output)),
                    "entries": names,
                }
        tree_files = tuple(
            line
            for line in _git(checkout, "ls-tree", "-r", "--name-only", commit).splitlines()
            if line
        )
        gate_names = {gate.name for gate in gates}
        required_gates = {
            "pytest-collect",
            "full-test-suite",
            "ruff",
            "bandit-medium-high",
            "six-seven-eight-and-aggregate",
            "capsule-mutants-and-trust",
            "wheel",
            "cli-package-help",
            "wheel-venv",
            "wheel-install",
            "wheel-export-smoke",
            "wheel-replay-smoke",
            "publication-xfs",
            "publication-ext4",
            "large-project-export",
            "large-project-replay",
            "dogfood-public-fetch",
            "dogfood-public-ancestry",
        }
        proof_complete = (
            wheel is not None
            and dogfood is not None
            and int(dogfood["tracked_files"]) >= MIN_DOGFOOD_TRACKED_FILES
            and int(dogfood["uncompressed_bytes"]) >= MIN_DOGFOOD_UNCOMPRESSED_BYTES
            and required_gates <= gate_names
            and len(filesystem_evidence) == 2
        )
        manifest = {
            "schema_version": 1,
            "status": "passed"
            if proof_complete and all(gate.exit_code == 0 for gate in gates)
            else "failed",
            "implementation_commit": commit,
            "contract": str(CONTRACT),
            "contract_sha256": _sha256(contract),
            "proof_runner_sha256": _sha256(
                checkout / "verification/authoritative_export/run.py"
            ),
            "git_tree_id": _git(checkout, "rev-parse", f"{commit}^{{tree}}"),
            "git_tree_listing_sha256": hashlib.sha256(
                _git(checkout, "ls-tree", "-r", "--full-tree", commit).encode()
            ).hexdigest(),
            "source_sha256": {
                path: _sha256(checkout / path)
                for path in tree_files
                if path.startswith("src/loreloop/") and path.endswith(".py")
            },
            "tracked_file_count": len(tree_files),
            "environment": {
                "python": sys.version,
                "platform": platform.platform(),
                "policy": "proof-environment-whitelist-v1",
                "pytest_plugin_autoload": False,
                "passthrough": list(_PROOF_ENV_PASSTHROUGH),
                "playwright_browsers_path": (
                    str(playwright_browsers_path)
                    if playwright_browsers_path is not None
                    else None
                ),
            },
            "gates": [asdict(gate) for gate in gates],
            "filesystems": filesystem_evidence,
            "wheel": wheel,
            "dogfood": dogfood,
        }
        manifest_path = output / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"proof manifest: {manifest_path}")
        print(f"implementation commit: {commit}")
        print(f"status: {manifest['status']}")
        return 0 if manifest["status"] == "passed" else 1
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
