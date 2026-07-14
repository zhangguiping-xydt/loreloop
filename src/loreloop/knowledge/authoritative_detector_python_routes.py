"""Python symbol and caller-visible interface extraction helpers."""

from __future__ import annotations

import ast
from collections.abc import Sequence
from typing import Final, TypeAlias

from .authoritative_records import (
    InterfaceRecord,
    ParameterRecord,
    SourceRef,
    SymbolRecord,
)

FunctionNode: TypeAlias = ast.FunctionDef | ast.AsyncFunctionDef
_HTTP_METHODS: Final = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})


def source_ref(alias: str, path: str, node: ast.AST) -> SourceRef:
    return SourceRef(alias, path, getattr(node, "lineno", 1))


def expression(node: ast.AST | None) -> str | None:
    return None if node is None else ast.unparse(node)


def call_name(node: ast.AST) -> str:
    match node:
        case ast.Name(id=name):
            return name
        case ast.Attribute(value=value, attr=attribute):
            prefix = call_name(value)
            return f"{prefix}.{attribute}" if prefix else attribute
        case _:
            return ""


def constant_text(node: ast.AST | None) -> str | None:
    match node:
        case ast.Constant(value=str() as value):
            return value
        case ast.Constant(value=None) | None:
            return None
        case _:
            return ast.unparse(node)


def _parameters(node: FunctionNode) -> tuple[ParameterRecord, ...]:
    positional = (*node.args.posonlyargs, *node.args.args)
    required_until = len(positional) - len(node.args.defaults)
    parameters = [
        ParameterRecord(argument.arg, expression(argument.annotation), index < required_until)
        for index, argument in enumerate(positional)
        if argument.arg not in {"self", "cls"}
    ]
    parameters.extend(
        ParameterRecord(argument.arg, expression(argument.annotation), default is None)
        for argument, default in zip(node.args.kwonlyargs, node.args.kw_defaults, strict=True)
    )
    if node.args.vararg is not None:
        parameters.append(
            ParameterRecord(node.args.vararg.arg, expression(node.args.vararg.annotation), False)
        )
    if node.args.kwarg is not None:
        parameters.append(
            ParameterRecord(node.args.kwarg.arg, expression(node.args.kwarg.annotation), False)
        )
    return tuple(parameters)


def keyword(call: ast.Call, name: str) -> ast.AST | None:
    return next((item.value for item in call.keywords if item.arg == name), None)


def _methods(node: ast.AST | None) -> tuple[str, ...]:
    match node:
        case ast.List(elts=items) | ast.Tuple(elts=items) | ast.Set(elts=items):
            return tuple(
                value.upper() for item in items if (value := constant_text(item)) is not None
            )
        case _:
            return ()


def _decorator_interfaces(
    node: FunctionNode,
    alias: str,
    path: str,
) -> tuple[InterfaceRecord, ...]:
    records: list[InterfaceRecord] = []
    for decorator in node.decorator_list:
        if not isinstance(decorator, ast.Call):
            continue
        attribute = call_name(decorator.func).rsplit(".", 1)[-1].lower()
        route = constant_text(decorator.args[0]) if decorator.args else None
        if attribute in _HTTP_METHODS and route is not None:
            methods = (attribute.upper(),)
        elif attribute in {"route", "api_route"} and route is not None:
            methods = _methods(keyword(decorator, "methods")) or ("GET",)
        else:
            methods = ()
        records.extend(
            InterfaceRecord(
                "http",
                node.name,
                method,
                route or "",
                _parameters(node),
                expression(node.returns),
                source_ref(alias, path, node),
            )
            for method in methods
        )
        if attribute == "command":
            command = constant_text(keyword(decorator, "name"))
            if command is None and decorator.args:
                command = constant_text(decorator.args[0])
            records.append(
                InterfaceRecord(
                    "cli",
                    node.name,
                    "COMMAND",
                    command or node.name.replace("_", "-"),
                    _parameters(node),
                    expression(node.returns),
                    source_ref(alias, path, node),
                )
            )
    return tuple(records)


def definitions(
    statements: Sequence[ast.stmt],
    alias: str,
    path: str,
    prefix: str = "",
) -> tuple[tuple[SymbolRecord, ...], tuple[InterfaceRecord, ...]]:
    symbols: list[SymbolRecord] = []
    interfaces: list[InterfaceRecord] = []
    for statement in statements:
        if isinstance(statement, ast.ClassDef):
            qualified = f"{prefix}.{statement.name}" if prefix else statement.name
            symbols.append(
                SymbolRecord("class", qualified, qualified, source_ref(alias, path, statement))
            )
            nested_symbols, nested_interfaces = definitions(statement.body, alias, path, qualified)
        elif isinstance(statement, (ast.FunctionDef, ast.AsyncFunctionDef)):
            qualified = f"{prefix}.{statement.name}" if prefix else statement.name
            kind = "async_function" if isinstance(statement, ast.AsyncFunctionDef) else "function"
            signature = (
                f"{statement.name}({', '.join(item.name for item in _parameters(statement))})"
            )
            symbols.append(
                SymbolRecord(kind, qualified, signature, source_ref(alias, path, statement))
            )
            interfaces.extend(_decorator_interfaces(statement, alias, path))
            nested_symbols, nested_interfaces = definitions(statement.body, alias, path, qualified)
        else:
            continue
        symbols.extend(nested_symbols)
        interfaces.extend(nested_interfaces)
    return tuple(symbols), tuple(interfaces)


def django_interfaces(tree: ast.AST, alias: str, path: str) -> tuple[InterfaceRecord, ...]:
    records: list[InterfaceRecord] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or call_name(node.func).rsplit(".", 1)[-1] not in {
            "path",
            "re_path",
        }:
            continue
        route = constant_text(node.args[0]) if node.args else None
        if route is None or len(node.args) < 2:
            continue
        handler = ast.unparse(node.args[1])
        records.append(
            InterfaceRecord("http", handler, "ANY", route, (), None, source_ref(alias, path, node))
        )
    return tuple(records)
