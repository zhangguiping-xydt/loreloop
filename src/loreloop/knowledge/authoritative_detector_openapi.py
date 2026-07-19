"""Static OpenAPI and Swagger contract detector for JSON and safe YAML."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import TypeAlias, cast

from .authoritative_detector_yaml import YamlValue, parse_yaml_contract
from .authoritative_records import (
    ContractFieldRecord,
    DetectionError,
    DetectionReport,
    InterfaceRecord,
    ParameterRecord,
    SourceRef,
    SymbolRecord,
)

Object: TypeAlias = Mapping[str, YamlValue]
_METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "trace")
_YAML_ROOT_VERSION = re.compile(
    r"""(?mx)
    ^(?P<key_quote>["']?)(?P<key>openapi|swagger)(?P=key_quote)\s*:\s*
    (?P<value_quote>["']?)(?P<version>[^\s#"']+)(?P=value_quote)\s*(?:\#.*)?$
    """
)


def _object(value: YamlValue, label: str) -> Object:
    if not isinstance(value, dict):
        raise DetectionError(f"OpenAPI {label} must be an object")
    return cast(Object, value)


def _text(value: YamlValue | None, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise DetectionError(f"OpenAPI {label} must be non-empty text")
    return value


def _line(source: str, needle: str, start: int = 0) -> int:
    key = re.search(rf"(?m)^\s*[\"']?{re.escape(needle)}[\"']?\s*:", source[start:])
    if key is not None:
        return source.count("\n", 0, start + key.start()) + 1
    variants = (f'"{needle}"', f"'{needle}'", needle)
    positions = [source.find(value, start) for value in variants]
    position = min((value for value in positions if value >= 0), default=0)
    return source.count("\n", 0, position) + 1


def _json_object(pairs: list[tuple[str, YamlValue]]) -> dict[str, YamlValue]:
    result: dict[str, YamlValue] = {}
    for key, value in pairs:
        if key in result:
            raise DetectionError(f"duplicate OpenAPI JSON key: {key}")
        result[key] = value
    return result


def _supported_version(key: str, value: object) -> bool:
    if not isinstance(value, str):
        return False
    if key == "openapi":
        return re.fullmatch(r"3(?:\.\d+){1,2}", value) is not None
    return key == "swagger" and value == "2.0"


def _json_string_end(source: str, start: int) -> int:
    index = start + 1
    escaped = False
    while index < len(source):
        character = source[index]
        if not escaped and character == '"':
            return index + 1
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        index += 1
    return len(source)


def _bounded_json_string(source: str, start: int, end: int) -> str | None:
    if end - start > 256:
        return None
    try:
        value = json.loads(source[start:end])
    except (json.JSONDecodeError, ValueError):
        return None
    return value if isinstance(value, str) else None


def _skip_json_value(source: str, start: int) -> int:
    """Skip one JSON value lexically without materializing its nested object graph."""
    if start >= len(source):
        return start
    if source[start] == '"':
        return _json_string_end(source, start)
    if source[start] not in "[{":
        index = start
        while index < len(source) and source[index] not in ",}":
            index += 1
        return index
    closing = {"[": "]", "{": "}"}
    stack = [closing[source[start]]]
    index = start + 1
    while index < len(source) and stack:
        character = source[index]
        if character == '"':
            index = _json_string_end(source, index)
            continue
        if character in closing:
            if len(stack) >= 128:
                return len(source)
            stack.append(closing[character])
        elif character == stack[-1]:
            stack.pop()
        index += 1
    return index


def _json_root_versions(source: str) -> tuple[tuple[str, object], ...]:
    """Read root markers while lexically skipping all unrelated values."""
    index = 0
    length = len(source)

    def whitespace(position: int) -> int:
        while position < length and source[position].isspace():
            position += 1
        return position

    index = whitespace(index)
    if index >= length or source[index] != "{":
        return ()
    index += 1
    markers: list[tuple[str, object]] = []
    while True:
        index = whitespace(index)
        if index >= length or source[index] == "}":
            return tuple(markers)
        if source[index] != '"':
            return tuple(markers)
        key_start = index
        index = _json_string_end(source, index)
        key = _bounded_json_string(source, key_start, index)
        index = whitespace(index)
        if index >= length or source[index] != ":":
            return tuple(markers)
        index = whitespace(index + 1)
        value_start = index
        index = _skip_json_value(source, index)
        if key in {"openapi", "swagger"}:
            value = (
                _bounded_json_string(source, value_start, index)
                if value_start < length and source[value_start] == '"'
                else None
            )
            markers.append((key, value))
        index = whitespace(index)
        if index >= length or source[index] == "}":
            return tuple(markers)
        if source[index] != ",":
            return tuple(markers)
        index += 1


def has_supported_openapi_root(source: str) -> bool:
    """Return true only for a supported root-level OpenAPI/Swagger version marker."""
    start = 0
    while start < len(source) and source[start].isspace():
        start += 1
    if start < len(source) and source[start] == "{":
        return any(_supported_version(key, value) for key, value in _json_root_versions(source))
    for match in _YAML_ROOT_VERSION.finditer(source):
        if _supported_version(match.group("key"), match.group("version")):
            return True
    return False


def _load(source: str, path: str) -> Object:
    try:
        if source.lstrip().startswith(("{", "[")):
            value = cast(YamlValue, json.loads(source, object_pairs_hook=_json_object))
        else:
            value = parse_yaml_contract(source)
    except json.JSONDecodeError as exc:
        raise DetectionError(f"invalid OpenAPI JSON source: {path}") from exc
    root = _object(value, "document")
    has_openapi = "openapi" in root
    has_swagger = "swagger" in root
    if has_openapi == has_swagger:
        raise DetectionError("contract document is not OpenAPI or Swagger")
    version = _text(root.get("openapi" if has_openapi else "swagger"), "version")
    valid = (
        re.fullmatch(r"3(?:\.\d+){1,2}", version) is not None if has_openapi else version == "2.0"
    )
    if not valid:
        raise DetectionError(f"unsupported OpenAPI/Swagger version: {version}")
    return root


def _resolve(root: Object, value: YamlValue, label: str) -> Object:
    current = _object(value, label)
    reference = current.get("$ref")
    if reference is None:
        return current
    pointer = _text(reference, f"{label} $ref")
    if not pointer.startswith("#/"):
        return current
    resolved: YamlValue = cast(YamlValue, root)
    for raw in pointer[2:].split("/"):
        key = raw.replace("~1", "/").replace("~0", "~")
        resolved = _object(resolved, f"reference {pointer}").get(key)
        if resolved is None:
            raise DetectionError(f"unresolved OpenAPI reference: {pointer}")
    return _object(resolved, f"reference {pointer}")


def _schema_type(root: Object, value: YamlValue | None) -> str | None:
    if value is None:
        return None
    unresolved = _object(value, "schema")
    reference = unresolved.get("$ref")
    if isinstance(reference, str):
        return reference.rsplit("/", 1)[-1]
    schema = _resolve(root, value, "schema")
    variants: list[str] = []
    for key in ("oneOf", "anyOf", "allOf"):
        raw = schema.get(key)
        if isinstance(raw, list):
            variants.extend(filter(None, (_schema_type(root, item) for item in raw)))
            if variants:
                separator = " | " if key != "allOf" else " & "
                return separator.join(variants)
    kind = schema.get("type")
    if kind == "array":
        return f"array[{_schema_type(root, schema.get('items')) or 'unknown'}]"
    if isinstance(kind, str):
        return kind
    return "object" if isinstance(schema.get("properties"), dict) else None


def _parameters(root: Object, values: YamlValue | None) -> tuple[ParameterRecord, ...]:
    if values is None:
        return ()
    if not isinstance(values, list):
        raise DetectionError("OpenAPI parameters must be an array")
    records: list[ParameterRecord] = []
    for value in values:
        parameter = _resolve(root, value, "parameter")
        name = _text(parameter.get("name"), "parameter name")
        location = _text(parameter.get("in"), f"parameter {name} location")
        schema = parameter.get("schema")
        annotation = _schema_type(root, schema) if schema is not None else None
        if annotation is None and isinstance(parameter.get("type"), str):
            annotation = cast(str, parameter["type"])
        required = parameter.get("required") is True or location == "path"
        records.append(ParameterRecord(f"{location}:{name}", annotation, required))
    return tuple(records)


def _response_type(root: Object, operation: Object) -> str | None:
    raw = operation.get("responses")
    if raw is None:
        return None
    responses = _object(raw, "responses")
    success = next((responses[key] for key in sorted(responses) if str(key).startswith("2")), None)
    if success is None and "default" in responses:
        success = responses["default"]
    if success is None:
        return None
    response = _resolve(root, success, "response")
    if response.get("schema") is not None:
        return _schema_type(root, response.get("schema"))
    content = response.get("content")
    if content is None:
        return None
    media = _object(content, "response content")
    if not media:
        return None
    representation = _object(media[sorted(media)[0]], "response media type")
    return _schema_type(root, representation.get("schema"))


def _interfaces(root: Object, source: str, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    paths = _object(root.get("paths", {}), "paths")
    records: list[InterfaceRecord] = []
    for route, raw_item in paths.items():
        item = _resolve(root, raw_item, f"path {route}")
        shared = _parameters(root, item.get("parameters"))
        for method in _METHODS:
            raw_operation = item.get(method)
            if raw_operation is None:
                continue
            operation = _object(raw_operation, f"operation {method} {route}")
            name_value = operation.get("operationId")
            name = name_value if isinstance(name_value, str) and name_value else f"{method} {route}"
            parameters = (*shared, *_parameters(root, operation.get("parameters")))
            body = operation.get("requestBody")
            if body is not None:
                request = _resolve(root, body, "request body")
                content = _object(request.get("content", {}), "request body content")
                if content:
                    media = _object(content[sorted(content)[0]], "request body media type")
                    parameters = (
                        *parameters,
                        ParameterRecord(
                            "body",
                            _schema_type(root, media.get("schema")),
                            request.get("required") is True,
                        ),
                    )
            records.append(
                InterfaceRecord(
                    "http",
                    name,
                    method.upper(),
                    route,
                    tuple(parameters),
                    _response_type(root, operation),
                    SourceRef(alias, path, _line(source, name if name_value else route)),
                )
            )
    return tuple(records)


def _symbols(root: Object, source: str, alias: str, path: str) -> tuple[SymbolRecord, ...]:
    components = root.get("components")
    schemas: Object = {}
    if components is not None:
        schemas = _object(_object(components, "components").get("schemas", {}), "schemas")
    elif root.get("definitions") is not None:
        schemas = _object(root.get("definitions"), "definitions")
    records: list[SymbolRecord] = []
    for name, raw_schema in schemas.items():
        schema = _resolve(root, raw_schema, f"schema {name}")
        properties = _object(schema.get("properties", {}), f"schema {name} properties")
        required_value = schema.get("required", [])
        if not isinstance(required_value, list) or not all(
            isinstance(item, str) for item in required_value
        ):
            raise DetectionError(f"OpenAPI schema {name} required must be an array of names")
        required = set(cast(list[str], required_value))
        fields = ", ".join(
            f"{field}:{_schema_type(root, value) or 'unknown'}{'!' if field in required else ''}"
            for field, value in properties.items()
        )
        records.append(
            SymbolRecord(
                "class",
                name,
                f"schema {name}({fields})",
                SourceRef(alias, path, _line(source, name)),
            )
        )
    return tuple(records)


def _contract_fields(
    root: Object, source: str, alias: str, path: str
) -> tuple[ContractFieldRecord, ...]:
    components = root.get("components")
    schemas: Object = {}
    if components is not None:
        schemas = _object(_object(components, "components").get("schemas", {}), "schemas")
    elif root.get("definitions") is not None:
        schemas = _object(root.get("definitions"), "definitions")
    records: list[ContractFieldRecord] = []
    for owner, raw_schema in schemas.items():
        schema = _resolve(root, raw_schema, f"schema {owner}")
        properties = _object(schema.get("properties", {}), f"schema {owner} properties")
        required_value = schema.get("required", [])
        if not isinstance(required_value, list) or not all(
            isinstance(item, str) for item in required_value
        ):
            raise DetectionError(f"OpenAPI schema {owner} required must be an array of names")
        required = set(cast(list[str], required_value))
        for name, raw_property in properties.items():
            property_schema = _resolve(root, raw_property, f"schema {owner} property {name}")
            nullable_value = property_schema.get("nullable")
            records.append(
                ContractFieldRecord(
                    owner,
                    name,
                    _schema_type(root, raw_property) or "unknown",
                    name in required,
                    nullable_value if isinstance(nullable_value, bool) else None,
                    SourceRef(alias, path, _line(source, name)),
                )
            )
    return tuple(records)


def detect_openapi_source(source: str, repository_alias: str, path: str) -> DetectionReport:
    """Extract operations and data types without resolving external files or executing code."""
    root = _load(source, path)
    return DetectionReport(
        interfaces=_interfaces(root, source, repository_alias, path),
        contract_fields=_contract_fields(root, source, repository_alias, path),
        symbols=_symbols(root, source, repository_alias, path),
    )
