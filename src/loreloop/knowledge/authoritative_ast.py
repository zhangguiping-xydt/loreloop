"""Path-neutral nested document AST contracts for authoritative export v4."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from .authoritative_bindings import BindingSet, FieldBinding
from .authoritative_ids import CanonicalScalar

ID_RE = re.compile(r"[A-Z]+-[0-9a-f]{64}")
POINTER_RE = re.compile(r"/(?:[^~/]|~[01])*(?:/(?:[^~/]|~[01])*)*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class AstViolation(ValueError):
    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise AstViolation(reason)


def _valid_pointer(pointer: str) -> bool:
    return pointer == "" or POINTER_RE.fullmatch(pointer) is not None


class DocumentRowKind(StrEnum):
    INTERFACE = "InterfaceRow"
    COMMAND = "CommandRow"
    UI_SURFACE = "UiSurfaceRow"
    CURRENT_DATA = "CurrentDataRow"
    HISTORICAL_DATA = "HistoricalDataRow"
    MIGRATION_OPERATION = "MigrationOperationRow"
    PERMISSION = "PermissionRow"
    CONFIGURATION = "ConfigurationRow"
    DEPLOYMENT = "DeploymentRow"
    STATE = "StateRow"
    ERROR = "ErrorRow"
    TEST = "TestRow"
    WEB_REQUIREMENT = "WebRequirementRow"
    WEB_INTERFACE = "WebInterfaceRow"
    WEB_ARCHITECTURE = "WebArchitectureRow"
    WEB_BEHAVIOR = "WebBehaviorRow"
    WEB_CONSTRAINT = "WebConstraintRow"
    WEB_ACCEPTANCE = "WebAcceptanceRow"
    REQUIREMENT = "RequirementRow"
    DEPENDENCY = "DependencyRow"
    RELATION = "RelationRow"
    ACCEPTANCE = "AcceptanceRow"
    APPLICABILITY = "ApplicabilityRow"
    ANNOTATION = "AnnotationRow"
    IMPLEMENTATION_FACT = "ImplementationFactRow"
    MODULE = "ModuleRow"
    MODULE_REPORT = "ModuleReportRow"
    EVIDENCE = "EvidenceRow"


class RequiredDocumentFamily(StrEnum):
    CAPABILITY_CATALOG = "capability_catalog"
    REQUIREMENTS = "requirements"
    ARCHITECTURE = "architecture"
    DETAILED_DESIGN = "detailed_design"
    USER_GUIDE = "user_guide"
    ACCEPTANCE = "acceptance"


class OptionalDocumentFamily(StrEnum):
    INTERFACE_CONTRACT = "interface_contract"
    DATABASE_DESIGN = "database_design"


class ApplicabilityStatus(StrEnum):
    PRESENT = "present"
    NO_EXPLICIT_MARKER = "no_explicit_marker_within_detector_profile"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class ProjectedValue:
    pointer: str
    value: CanonicalScalar

    def __post_init__(self) -> None:
        _require(_valid_pointer(self.pointer), "invalid projected pointer")


@dataclass(frozen=True, slots=True)
class RenderedFieldAddress:
    section_id: str
    subsection_ids: tuple[str, ...]
    table_id: str
    row_id: str
    column_id: str

    def __post_init__(self) -> None:
        ancestry = (self.section_id, *self.subsection_ids, self.table_id, self.column_id)
        _require(all(bool(identifier) for identifier in ancestry), "empty rendered address id")
        _require(ID_RE.fullmatch(self.row_id) is not None, "invalid rendered row id")

    def json_pointer(self) -> str:
        """Return the stable ancestry pointer for one rendered occurrence."""
        parts = ["sections", self.section_id]
        for subsection_id in self.subsection_ids:
            parts.extend(("subsections", subsection_id))
        parts.extend(("tables", self.table_id, "rows", self.row_id, self.column_id))
        return "/" + "/".join(part.replace("~", "~0").replace("/", "~1") for part in parts)


@dataclass(frozen=True, slots=True)
class RenderedField:
    address: RenderedFieldAddress
    semantic_pointer: str
    value: CanonicalScalar
    binding: FieldBinding
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(_valid_pointer(self.semantic_pointer), "invalid semantic pointer")


@dataclass(frozen=True, slots=True)
class RenderedTable:
    table_id: str
    fields: tuple[RenderedField, ...]

    def __post_init__(self) -> None:
        _require(bool(self.table_id), "empty rendered table id")
        addresses = tuple(field.address for field in self.fields)
        _require(len(addresses) == len(set(addresses)), "duplicate rendered field address")


@dataclass(frozen=True, slots=True)
class AstRow:
    row_kind: DocumentRowKind
    record_id: str
    values: tuple[ProjectedValue, ...]
    refs: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    canonical: bool
    anchor: str | None
    link_target: str | None
    bindings: BindingSet

    def __post_init__(self) -> None:
        _require(ID_RE.fullmatch(self.record_id) is not None, "invalid AST record id")


@dataclass(frozen=True, slots=True)
class Coverage:
    inventory_total: int
    accounted_total: int
    candidate_total: int
    record_total: int
    bound_leaf_total: int
    routed_leaf_total: int
    routed_leaf_expected: int
    bindings: BindingSet
    unknown_total: Literal[0] = 0
    gap_total: Literal[0] = 0

    def __post_init__(self) -> None:
        totals = (
            self.inventory_total,
            self.accounted_total,
            self.candidate_total,
            self.record_total,
            self.bound_leaf_total,
            self.routed_leaf_total,
            self.routed_leaf_expected,
        )
        _require(all(value >= 0 for value in totals), "negative coverage total")
        _require(self.inventory_total == self.accounted_total, "inventory is not fully accounted")
        _require(self.routed_leaf_total == self.routed_leaf_expected, "routed leaf mismatch")
        _require(self.unknown_total == 0 and self.gap_total == 0, "ready coverage contains gaps")


@dataclass(frozen=True, slots=True)
class AuthorityHeader:
    trust_domain_id: str
    repository_config_digest: str
    package_id: str | None
    coverage: Coverage
    bindings: BindingSet
    row_kind: Literal["AuthorityHeaderRow"] = field(default="AuthorityHeaderRow", init=False)
    authority_label: Literal[
        "git_snapshot_verified",
        "git_snapshot_plus_governed_web_projection",
        "git_working_tree_snapshot_verified",
        "git_working_tree_snapshot_plus_governed_web_projection",
    ] = "git_snapshot_verified"
    detector_profile: Literal["detector-v4"] = field(default="detector-v4", init=False)
    knowledge_db_status: Literal["not_loaded", "governed_web_loaded"] = "not_loaded"

    def __post_init__(self) -> None:
        _require(
            self.authority_label
            in {
                "git_snapshot_verified",
                "git_snapshot_plus_governed_web_projection",
                "git_working_tree_snapshot_verified",
                "git_working_tree_snapshot_plus_governed_web_projection",
            },
            "invalid authority label",
        )
        web_projection = self.authority_label in {
            "git_snapshot_plus_governed_web_projection",
            "git_working_tree_snapshot_plus_governed_web_projection",
        }
        _require(
            web_projection == (self.knowledge_db_status == "governed_web_loaded"),
            "authority label and knowledge DB status disagree",
        )
        _require(SHA256_RE.fullmatch(self.trust_domain_id) is not None, "invalid trust domain id")
        _require(
            SHA256_RE.fullmatch(self.repository_config_digest) is not None,
            "invalid repository digest",
        )
        _require(
            self.package_id is None or SHA256_RE.fullmatch(self.package_id) is not None,
            "invalid package id",
        )


@dataclass(frozen=True, slots=True)
class DocumentSection:
    section_id: str
    title: str
    rows: tuple[AstRow, ...]
    bindings: BindingSet
    tables: tuple[RenderedTable, ...] = ()
    subsections: tuple[DocumentSection, ...] = ()

    def __post_init__(self) -> None:
        _require(bool(self.section_id) and bool(self.title), "empty document section")
        table_ids = tuple(table.table_id for table in self.tables)
        subsection_ids = tuple(section.section_id for section in self.subsections)
        _require(len(table_ids) == len(set(table_ids)), "duplicate table id")
        _require(len(subsection_ids) == len(set(subsection_ids)), "duplicate subsection id")


@dataclass(frozen=True, slots=True)
class DocumentAst:
    document_id: str
    path: str
    title: str
    header: AuthorityHeader
    sections: tuple[DocumentSection, ...]
    evidence_rows: tuple[AstRow, ...]
    bindings: BindingSet
    required_family: RequiredDocumentFamily | None = None
    optional_family: OptionalDocumentFamily | None = None
    schema_version: Literal[4] = field(default=4, init=False)

    def __post_init__(self) -> None:
        _require(bool(self.document_id), "empty document id")
        _require(
            bool(self.path)
            and not self.path.startswith("/")
            and all(part not in {"", ".", ".."} for part in self.path.split("/")),
            "invalid document path",
        )
        _require(bool(self.title) and bool(self.sections), "incomplete document AST")
        _require(
            self.required_family is None or self.optional_family is None,
            "document has conflicting families",
        )


@dataclass(frozen=True, slots=True)
class OptionalDocumentApplicability:
    family: OptionalDocumentFamily
    status: ApplicabilityStatus
    reason_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _require(type(self.family) is OptionalDocumentFamily, "invalid applicability family")
        _require(type(self.status) is ApplicabilityStatus, "invalid applicability status")


@dataclass(frozen=True, slots=True)
class DocumentSet:
    documents: tuple[DocumentAst, ...]
    applicability: tuple[OptionalDocumentApplicability, ...]

    def __post_init__(self) -> None:
        _require(6 <= len(self.documents) <= 8, "document count must be between six and eight")
        document_ids = tuple(document.document_id for document in self.documents)
        paths = tuple(document.path for document in self.documents)
        _require(len(document_ids) == len(set(document_ids)), "duplicate document id")
        _require(len(paths) == len(set(paths)), "duplicate document path")
        required = tuple(
            document.required_family
            for document in self.documents
            if document.required_family is not None
        )
        optional = tuple(
            document.optional_family
            for document in self.documents
            if document.optional_family is not None
        )
        _require(
            len(required) == len(set(required)) and set(required) == set(RequiredDocumentFamily),
            "required document families are invalid",
        )
        _require(len(optional) == len(set(optional)), "optional document families are invalid")
        _require(len(required) + len(optional) == len(self.documents), "document family is missing")
        _require(len(self.applicability) == 2, "both optional families require applicability")
        families = tuple(item.family for item in self.applicability)
        _require(
            set(families) == set(OptionalDocumentFamily),
            "optional applicability families are invalid",
        )
        for item in self.applicability:
            _require(
                item.status is not ApplicabilityStatus.UNKNOWN,
                "unknown applicability blocks readiness",
            )
            expected_present = item.family in optional
            _require(
                (item.status is ApplicabilityStatus.PRESENT) == expected_present,
                "applicability disagrees with document set",
            )
