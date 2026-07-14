"""Deterministic Docker, Compose, and Kubernetes configuration detector."""

from __future__ import annotations

import re
import shlex

from .authoritative_detector_platform_yaml import (
    YAML_FIELD,
    YAML_LIST,
    configuration,
    is_env_key,
    unquote,
    yaml_environment,
    yaml_lines,
)
from .authoritative_records import (
    ConfigurationRecord,
    DependencyRecord,
    DetectionError,
    DetectionReport,
    InterfaceRecord,
    SourceRef,
)


def _image_name(requirement: str) -> str:
    value = requirement.split("@", 1)[0]
    tag = value.rfind(":")
    return value[:tag] if tag > value.rfind("/") else value


def _docker_instructions(source: str) -> tuple[tuple[int, str], ...]:
    instructions: list[tuple[int, str]] = []
    pending = ""
    start = 1
    for number, raw in enumerate(source.splitlines(), 1):
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start = number
        pending = f"{pending} {stripped}".strip()
        if pending.endswith("\\"):
            pending = pending[:-1].rstrip()
            continue
        instructions.append((start, pending))
        pending = ""
    if pending:
        instructions.append((start, pending))
    return tuple(instructions)


def _dockerfile(source: str, alias: str, path: str) -> DetectionReport:
    dependencies: list[DependencyRecord] = []
    configurations: list[ConfigurationRecord] = []
    for number, line in _docker_instructions(source):
        instruction, _, body = line.partition(" ")
        instruction = instruction.upper()
        if instruction not in {"FROM", "EXPOSE", "ENV"}:
            continue
        try:
            words = shlex.split(body, comments=True)
        except ValueError as exc:
            raise DetectionError(f"invalid Dockerfile instruction at {path}:{number}") from exc
        if instruction == "FROM":
            image = next((word for word in words if not word.startswith("--")), "")
            if image and "$" not in image:
                dependencies.append(
                    DependencyRecord(
                        _image_name(image),
                        image,
                        "container_base_image",
                        SourceRef(alias, path, number),
                    )
                )
        elif instruction == "EXPOSE":
            configurations.extend(
                configuration(alias, path, number, f"docker.expose.{port}", port)
                for port in words
                if "$" not in port
            )
        elif instruction == "ENV" and words:
            pairs = (
                words
                if all("=" in word for word in words)
                else [f"{words[0]}={' '.join(words[1:])}"]
            )
            for pair in pairs:
                key, separator, value = pair.partition("=")
                if separator and is_env_key(key):
                    configurations.append(configuration(alias, path, number, key, value or None))
    return DetectionReport(
        configurations=tuple(configurations),
        dependencies=tuple(dependencies),
    )


def _compose(source: str, alias: str, path: str) -> DetectionReport:
    lines = yaml_lines(source)
    dependencies = tuple(
        DependencyRecord(
            _image_name(item.value),
            item.value,
            "container_image",
            SourceRef(alias, path, item.line),
        )
        for item in lines
        if item.key == "image" and item.value and "$" not in item.value
    )
    configurations = list(yaml_environment(lines, alias, path))
    port_indent: int | None = None
    port_kind = "port"
    environment_indent: int | None = None
    for number, raw in enumerate(source.splitlines(), 1):
        field = YAML_FIELD.match(raw)
        if field and field.group("key") in {"ports", "expose"} and not (
            field.group("value") or ""
        ).strip():
            port_indent = len(field.group("indent"))
            port_kind = "port" if field.group("key") == "ports" else "expose"
            continue
        if field and field.group("key") == "environment" and not (
            field.group("value") or ""
        ).strip():
            environment_indent = len(field.group("indent"))
            continue
        listed = YAML_LIST.match(raw)
        if listed is not None and environment_indent is not None:
            if len(listed.group("indent")) > environment_indent:
                key, separator, value = unquote(listed.group("value")).partition("=")
                if separator and is_env_key(key):
                    configurations.append(
                        configuration(alias, path, number, key, value or None)
                    )
                    continue
            environment_indent = None
        if listed is None or port_indent is None or len(listed.group("indent")) <= port_indent:
            if raw.strip() and len(raw) - len(raw.lstrip()) <= (port_indent or -1):
                port_indent = None
            continue
        port = unquote(listed.group("value").split(" #", 1)[0])
        if "$" not in port:
            configurations.append(
                configuration(alias, path, number, f"compose.{port_kind}.{port}", port)
            )
    return DetectionReport(configurations=tuple(configurations), dependencies=dependencies)


def _kubernetes(source: str, alias: str, path: str) -> DetectionReport:
    lines = yaml_lines(source)
    dependencies = tuple(
        DependencyRecord(
            _image_name(item.value),
            item.value,
            "container_image",
            SourceRef(alias, path, item.line),
        )
        for item in lines
        if item.key == "image" and item.value and "$" not in item.value
    )
    configurations = list(yaml_environment(lines, alias, path))
    configurations.extend(
        configuration(
            alias,
            path,
            item.line,
            f"kubernetes.{item.key}.{item.value}",
            item.value,
        )
        for item in lines
        if item.key in {"containerPort", "hostPort", "port", "targetPort", "nodePort"}
        and item.value.isdigit()
    )
    interfaces: list[InterfaceRecord] = []
    offset = 0
    for document in re.split(r"(?m)^---\s*$", source):
        if re.search(r"(?m)^\s*kind:\s*Ingress\s*$", document):
            for match in re.finditer(
                r'''(?m)^\s*(?:-\s*)?path:\s*["']?(?P<path>/[^\s"']*)["']?\s*$''',
                document,
            ):
                interfaces.append(
                    InterfaceRecord(
                        "http",
                        f"ingress {match.group('path')}",
                        "ANY",
                        match.group("path"),
                        (),
                        None,
                        SourceRef(alias, path, source.count("\n", 0, offset + match.start()) + 1),
                    )
                )
        offset += len(document) + 4
    return DetectionReport(
        interfaces=tuple(interfaces),
        configurations=tuple(configurations),
        dependencies=dependencies,
    )


def detect_platform_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Dispatch supported platform files without executing tools or templates."""
    name = path.rsplit("/", 1)[-1].lower()
    if name == "dockerfile" or name.startswith("dockerfile."):
        return _dockerfile(source, repository_alias, path)
    if name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}:
        return _compose(source, repository_alias, path)
    if name.endswith((".yml", ".yaml")) and re.search(
        r"(?m)^\s*apiVersion:\s*[^\s]+\s*$", source
    ):
        return _kubernetes(source, repository_alias, path)
    return DetectionReport()
