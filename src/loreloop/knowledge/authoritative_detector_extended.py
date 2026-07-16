"""Single integration entry for additional deterministic source detectors."""

from __future__ import annotations

from collections.abc import Callable
from typing import Final

from .authoritative_detector_csharp import detect_csharp_source
from .authoritative_detector_dotnet import detect_dotnet_source
from .authoritative_detector_go import detect_go_source
from .authoritative_detector_jvm import detect_jvm_source
from .authoritative_detector_platform import detect_platform_source
from .authoritative_detector_rust import detect_rust_source
from .authoritative_records import DetectionReport

Detector = Callable[[str, str, str], DetectionReport]
_BY_SUFFIX: Final[tuple[tuple[tuple[str, ...], Detector], ...]] = (
    ((".java", ".kt", ".kts"), detect_jvm_source),
    ((".go",), detect_go_source),
    ((".rs",), detect_rust_source),
    ((".cs",), detect_csharp_source),
    ((".csproj", ".vbproj", ".sln", ".config"), detect_dotnet_source),
)

_DOTNET_XML_NAMES = frozenset({"build.xml", "sql.xml", "environmentvariables.xml"})


def is_extended_source(path: str) -> bool:
    """Return whether a path belongs to an additional deterministic detector."""
    lower = path.lower()
    name = lower.rsplit("/", 1)[-1]
    return (
        any(lower.endswith(suffixes) for suffixes, _ in _BY_SUFFIX)
        or name == "dockerfile"
        or name.startswith("dockerfile.")
        or name in {"docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}
        or lower.endswith((".yml", ".yaml"))
        or name in _DOTNET_XML_NAMES
    )


def detect_extended_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Detect a supported additional language or platform source by path."""
    lower = path.lower()
    for suffixes, detector in _BY_SUFFIX:
        if lower.endswith(suffixes):
            return detector(source, repository_alias, path)
    if lower.rsplit("/", 1)[-1] in _DOTNET_XML_NAMES:
        return detect_dotnet_source(source, repository_alias, path)
    return detect_platform_source(source, repository_alias, path)
