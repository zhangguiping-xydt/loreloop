from __future__ import annotations

import json

import pytest

from loreloop.knowledge.authoritative_detector_graphql import detect_graphql_source
from loreloop.knowledge.authoritative_detector_openapi import detect_openapi_source
from loreloop.knowledge.authoritative_detector_proto import detect_proto_source
from loreloop.knowledge.authoritative_records import DetectionError


def test_openapi_yaml_extracts_operations_parameters_responses_and_schemas() -> None:
    source = """openapi: 3.0.3
paths:
  /pets/{id}:
    parameters:
      - name: id
        in: path
        schema:
          type: string
    get:
      operationId: getPet
      responses:
        '200':
          description: found
          content:
            application/json:
              schema:
                $ref: '#/components/schemas/Pet'
components:
  schemas:
    Pet:
      type: object
      required: [id]
      properties:
        id: {type: string}
        tags:
          type: array
          items: {type: string}
"""

    report = detect_openapi_source(source, "backend", "openapi.yaml")

    assert tuple((item.method, item.path, item.name) for item in report.interfaces) == (
        ("GET", "/pets/{id}", "getPet"),
    )
    assert report.interfaces[0].parameters[0].name == "path:id"
    assert report.interfaces[0].parameters[0].required is True
    assert report.interfaces[0].return_type == "Pet"
    assert report.interfaces[0].source.line == 10
    assert report.symbols[0].qualified_name == "Pet"
    assert report.symbols[0].signature == "schema Pet(id:string!, tags:array[string])"
    assert report.symbols[0].source.line == 20


def test_swagger_json_extracts_body_parameter_and_definition_deterministically() -> None:
    document = {
        "swagger": "2.0",
        "paths": {
            "/pets": {
                "post": {
                    "operationId": "createPet",
                    "parameters": [
                        {
                            "name": "pet",
                            "in": "body",
                            "required": True,
                            "schema": {"$ref": "#/definitions/PetInput"},
                        }
                    ],
                    "responses": {"201": {"schema": {"$ref": "#/definitions/Pet"}}},
                }
            }
        },
        "definitions": {
            "Pet": {"type": "object", "properties": {"id": {"type": "integer"}}},
            "PetInput": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    }
    source = json.dumps(document, sort_keys=True, indent=2)

    first = detect_openapi_source(source, ".", "swagger.json")
    second = detect_openapi_source(source, ".", "swagger.json")

    assert first == second
    assert first.interfaces[0].parameters[0].annotation == "PetInput"
    assert first.interfaces[0].return_type == "Pet"
    assert {item.qualified_name for item in first.symbols} == {"Pet", "PetInput"}


def test_openapi_json_rejects_duplicate_keys() -> None:
    source = '{"openapi":"3.0.0","paths":{},"paths":{"/hidden":{}}}'

    with pytest.raises(DetectionError, match="duplicate OpenAPI JSON key"):
        _ = detect_openapi_source(source, ".", "openapi.json")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ('{"openapi":"3.0.0","paths":', "invalid OpenAPI JSON"),
        ("openapi: 3.0.0\npaths:\n  /pets: [unterminated\n", "unterminated YAML scalar"),
        (
            "openapi: 3.0.0\npaths: {}\nmalicious: !!python/object:os.system {}\n",
            "anchors, aliases, and tags",
        ),
    ],
)
def test_openapi_fails_closed_for_malformed_or_executable_input(source: str, message: str) -> None:
    with pytest.raises(DetectionError, match=message):
        _ = detect_openapi_source(source, ".", "openapi.yaml")


def test_graphql_sdl_extracts_custom_root_operations_and_declared_types() -> None:
    source = '''schema {
  query: RootQuery
  mutation: RootMutation
}
scalar DateTime
type RootQuery {
  user(id: ID!): User
  users(limit: Int = 20): [User!]!
}
type RootMutation {
  createUser(input: UserInput!): User!
}
input UserInput { name: String! }
type User { id: ID! name: String! createdAt: DateTime! }
'''

    report = detect_graphql_source(source, "api", "schema.graphql")

    assert tuple((item.name, item.method, item.return_type) for item in report.interfaces) == (
        ("RootQuery.user", "GRAPHQL_QUERY", "User"),
        ("RootQuery.users", "GRAPHQL_QUERY", "[User!]!"),
        ("RootMutation.createUser", "GRAPHQL_MUTATION", "User!"),
    )
    assert report.interfaces[0].parameters[0].annotation == "ID!"
    assert report.interfaces[0].parameters[0].required is True
    assert {item.qualified_name for item in report.symbols} == {
        "DateTime",
        "RootMutation",
        "RootQuery",
        "User",
        "UserInput",
    }


@pytest.mark.parametrize(
    "source",
    [
        "type Query { user(id: ID!): User\n",
        'type Query { user: String @deprecated(reason: "unterminated) }',
        "type Query { user(id ID!): User }",
    ],
)
def test_graphql_sdl_reports_parse_failures(source: str) -> None:
    with pytest.raises(DetectionError):
        _ = detect_graphql_source(source, ".", "schema.graphql")


def test_proto_extracts_messages_enums_and_streaming_service_rpcs() -> None:
    source = '''syntax = "proto3";
package acme.users.v1;
message GetUserRequest { string id = 1; }
message User { string id = 1; repeated string roles = 2; }
enum Status { STATUS_UNSPECIFIED = 0; ACTIVE = 1; }
service UserService {
  rpc GetUser(GetUserRequest) returns (User);
  rpc WatchUsers(stream GetUserRequest) returns (stream User) {}
}
'''

    report = detect_proto_source(source, "contracts", "users.proto")

    assert tuple((item.method, item.path) for item in report.interfaces) == (
        ("RPC", "/acme.users.v1.UserService/GetUser"),
        ("RPC", "/acme.users.v1.UserService/WatchUsers"),
    )
    assert report.interfaces[1].parameters[0].annotation == "stream GetUserRequest"
    assert report.interfaces[1].return_type == "stream User"
    assert {item.qualified_name for item in report.symbols} == {
        "acme.users.v1.GetUserRequest",
        "acme.users.v1.Status",
        "acme.users.v1.User",
    }
    assert next(item for item in report.symbols if item.qualified_name.endswith(".User")).signature == (
        "message User(id:string=1, roles:string[]=2)"
    )


def test_proto_accepts_nested_rpc_option_blocks_without_executing_protoc() -> None:
    source = '''syntax = "proto3";
service Gateway {
  rpc Get(Request) returns (Response) {
    option (google.api.http) = { get: "/v1/items/{id}" };
  }
}
'''

    report = detect_proto_source(source, ".", "gateway.proto")

    assert report.interfaces[0].name == "Gateway.Get"
    assert report.interfaces[0].path == "/Gateway/Get"


@pytest.mark.parametrize(
    "source",
    [
        "message Broken { string id = 1;",
        "service Broken { rpc MissingReturn(Request); }",
        "message Broken { optional = 1; }",
        'syntax = "unterminated;',
    ],
)
def test_proto_reports_parse_failures(source: str) -> None:
    with pytest.raises(DetectionError):
        _ = detect_proto_source(source, ".", "broken.proto")
