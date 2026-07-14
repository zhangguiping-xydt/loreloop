"""Closed field bindings and producer semantics for authoritative export v4."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, TypeAlias, assert_never

from .authoritative_ids import CanonicalScalar

ID_RE = re.compile(r"[A-Z]+-[0-9a-f]{64}")
POINTER_RE = re.compile(r"/(?:[^~/]|~[01])*(?:/(?:[^~/]|~[01])*)*")
SHA256_RE = re.compile(r"[0-9a-f]{64}")


class BindingViolation(ValueError):
    """A binding shape or its producer semantics are invalid."""

    reason: str

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def _require(condition: bool, reason: str) -> None:
    if not condition:
        raise BindingViolation(reason)


def _valid_pointer(pointer: str) -> bool:
    return pointer == "" or POINTER_RE.fullmatch(pointer) is not None


class BindingKind(StrEnum):
    SOURCE = "source"
    DERIVED = "derived"
    FIXED = "fixed"
    OBSERVED = "observed"


class SourceTransform(StrEnum):
    IDENTITY = "identity"
    NFC = "nfc"
    IDENTIFIER_TOKENS = "identifier_tokens"
    AST_SHAPE = "ast_shape"
    TOKEN_STREAM = "token_stream"
    REDACTED_LITERAL = "redacted_literal"


class DerivedProjection(StrEnum):
    ORDERED_COPY = "ordered_copy"
    SORTED_UNIQUE = "sorted_unique"
    COUNT = "count"
    BOOLEAN_ANY = "boolean_any"
    BOOLEAN_ALL = "boolean_all"
    SEMANTIC_KEY = "semantic_key"
    RECORD_ID = "record_id"
    ROUTE = "route"
    COVERAGE = "coverage"
    CURRENT_SCHEMA_FOLD = "current_schema_fold"
    ACCEPTANCE_CASE = "acceptance_case"
    MODULE_SUMMARY = "module_summary"
    HASH = "hash"
    PAYLOAD_TREE = "payload_tree"


class ObservationKind(StrEnum):
    CLOCK = "clock"
    TOOL_STAT = "tool_stat"
    FILESYSTEM_STAT = "filesystem_stat"
    TRUST_RECORD = "trust_record"
    PACKAGE_DERIVATION = "package_derivation"


class ObservedProjection(StrEnum):
    IDENTITY = "identity"
    STAT_TUPLE = "stat_tuple"
    CANONICAL_JSON = "canonical_json"


@dataclass(frozen=True, slots=True)
class SourceBinding:
    evidence_id: str
    atom_id: str
    atom_pointer: str
    transform: SourceTransform
    kind: Literal["source"] = field(default="source", init=False)

    def __post_init__(self) -> None:
        _require(
            self.evidence_id.startswith("EVD-") and ID_RE.fullmatch(self.evidence_id) is not None,
            "invalid evidence id",
        )
        _require(
            self.atom_id.startswith("ATM-") and ID_RE.fullmatch(self.atom_id) is not None,
            "invalid atom id",
        )
        _require(_valid_pointer(self.atom_pointer), "invalid atom pointer")


@dataclass(frozen=True, slots=True)
class DerivedInput:
    bindable_id: str
    pointer: str

    def __post_init__(self) -> None:
        _require(ID_RE.fullmatch(self.bindable_id) is not None, "invalid bindable id")
        _require(_valid_pointer(self.pointer), "invalid derived pointer")


@dataclass(frozen=True, slots=True)
class DerivedBinding:
    rule_id: str
    inputs: tuple[DerivedInput, ...]
    projection: DerivedProjection
    kind: Literal["derived"] = field(default="derived", init=False)

    def __post_init__(self) -> None:
        _require(bool(self.rule_id), "empty derived rule id")
        _require(bool(self.inputs), "derived binding requires inputs")


@dataclass(frozen=True, slots=True)
class FixedBinding:
    rule_id: str
    literal: CanonicalScalar
    kind: Literal["fixed"] = field(default="fixed", init=False)

    def __post_init__(self) -> None:
        _require(bool(self.rule_id), "empty fixed rule id")


@dataclass(frozen=True, slots=True)
class ObservedBinding:
    observation_kind: ObservationKind
    observation_sha256: str
    projection: ObservedProjection
    kind: Literal["observed"] = field(default="observed", init=False)

    def __post_init__(self) -> None:
        _require(
            SHA256_RE.fullmatch(self.observation_sha256) is not None, "invalid observation digest"
        )


FieldBinding: TypeAlias = SourceBinding | DerivedBinding | FixedBinding | ObservedBinding


@dataclass(frozen=True, slots=True)
class BindingEntry:
    pointer: str
    binding: FieldBinding

    def __post_init__(self) -> None:
        _require(_valid_pointer(self.pointer), "invalid binding pointer")


BindingSet: TypeAlias = tuple[BindingEntry, ...]


@dataclass(frozen=True, slots=True)
class ProducerBindingInfo:
    bindable_id: str
    owner_kind: str
    construction_ordinal: int
    pointers: frozenset[str]
    source_evidence_id: str | None = None

    def __post_init__(self) -> None:
        _require(ID_RE.fullmatch(self.bindable_id) is not None, "invalid producer id")
        _require(self.construction_ordinal > 0, "invalid producer ordinal")
        _require(
            all(_valid_pointer(pointer) for pointer in self.pointers), "invalid producer pointer"
        )
        _require(
            self.source_evidence_id is None
            or (
                self.source_evidence_id.startswith("EVD-")
                and ID_RE.fullmatch(self.source_evidence_id) is not None
            ),
            "invalid producer evidence id",
        )


@dataclass(frozen=True, slots=True)
class BindingPolicy:
    owner_kind: str
    owner_pointer: str
    consumer_ordinal: int
    binding_kind: BindingKind
    rule_id: str | None
    projection: DerivedProjection | ObservedProjection | None
    observation_kind: ObservationKind | None
    source_atom_kind: str | None = None
    source_transform: SourceTransform | None = None
    owned_value: CanonicalScalar = None

    def __post_init__(self) -> None:
        _require(bool(self.owner_kind), "empty binding owner kind")
        _require(_valid_pointer(self.owner_pointer), "invalid owner pointer")
        _require(self.consumer_ordinal > 0, "invalid consumer ordinal")


def _producer(identifier: str, producers: tuple[ProducerBindingInfo, ...]) -> ProducerBindingInfo:
    matches = tuple(producer for producer in producers if producer.bindable_id == identifier)
    _require(len(matches) == 1, "binding producer is missing or ambiguous")
    return matches[0]


def _require_earlier(producer: ProducerBindingInfo, policy: BindingPolicy, pointer: str) -> None:
    _require(producer.construction_ordinal < policy.consumer_ordinal, "producer is not earlier")
    _require(pointer in producer.pointers, "producer pointer is not owned")


def validate_binding_semantics(
    binding: FieldBinding,
    policy: BindingPolicy,
    producers: tuple[ProducerBindingInfo, ...],
) -> None:
    """Validate binding kind, rule/projection, and truthful producer ancestry."""
    match binding:
        case SourceBinding(
            evidence_id=evidence_id,
            atom_id=atom_id,
            atom_pointer=pointer,
            transform=transform,
        ):
            _require(policy.binding_kind is BindingKind.SOURCE, "binding kind violates policy")
            atom = _producer(atom_id, producers)
            evidence = _producer(evidence_id, producers)
            _require_earlier(atom, policy, pointer)
            _require_earlier(evidence, policy, "")
            _require(
                atom.source_evidence_id == evidence_id,
                "evidence/atom pair violates policy",
            )
            if policy.source_atom_kind is not None:
                _require(
                    atom.owner_kind == policy.source_atom_kind, "source atom kind violates policy"
                )
            if policy.source_transform is not None:
                _require(transform is policy.source_transform, "source transform violates policy")
        case DerivedBinding(rule_id=rule_id, inputs=inputs, projection=projection):
            _require(policy.binding_kind is BindingKind.DERIVED, "binding kind violates policy")
            _require(
                rule_id == policy.rule_id and projection == policy.projection,
                "derived rule violates policy",
            )
            for derived_input in inputs:
                _require_earlier(
                    _producer(derived_input.bindable_id, producers), policy, derived_input.pointer
                )
        case FixedBinding(rule_id=rule_id, literal=literal):
            _require(policy.binding_kind is BindingKind.FIXED, "binding kind violates policy")
            _require(
                rule_id == policy.rule_id and literal == policy.owned_value,
                "fixed rule violates policy",
            )
        case ObservedBinding(observation_kind=kind, projection=projection):
            _require(policy.binding_kind is BindingKind.OBSERVED, "binding kind violates policy")
            _require(
                kind == policy.observation_kind and projection == policy.projection,
                "observation violates policy",
            )
        case _:
            assert_never(binding)
