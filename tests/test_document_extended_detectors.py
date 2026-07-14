from __future__ import annotations

from loreloop.knowledge.authoritative_detector_extended import (
    detect_extended_source,
    is_extended_source,
)
from loreloop.knowledge.authoritative_detector_platform import detect_platform_source


def test_jvm_detector_extracts_java_and_kotlin_facts_without_comment_examples() -> None:
    java = """
import org.springframework.web.bind.annotation.PostMapping;
// @GetMapping("/not-real") public String fake() { return ""; }
@RequestMapping("/api/users")
public class UserController {
  @PostMapping("/{id}")
  public User update(String id) {
    String region = System.getenv("APP_REGION");
    return service.update(id);
  }
}
"""
    kotlin = """
import io.ktor.server.routing.get
class HealthRoutes {
  fun install() {
    val stage = System.getenv("APP_STAGE")
    get("/health") { call.respondText("ok") }
  }
}
"""

    java_report = detect_extended_source(java, "backend", "src/UserController.java")
    kotlin_report = detect_extended_source(kotlin, "backend", "src/HealthRoutes.kt")

    assert [(item.method, item.path, item.name) for item in java_report.interfaces] == [
        ("POST", "/api/users/{id}", "update")
    ]
    assert {item.qualified_name for item in java_report.symbols} == {"UserController", "update"}
    assert [item.key for item in java_report.configurations] == ["APP_REGION"]
    assert [item.name for item in java_report.dependencies] == [
        "org.springframework.web.bind.annotation.PostMapping"
    ]
    assert [(item.method, item.path) for item in kotlin_report.interfaces] == [
        ("GET", "/health")
    ]
    assert {item.qualified_name for item in kotlin_report.symbols} == {
        "HealthRoutes",
        "install",
    }


def test_go_detector_extracts_routes_symbols_environment_and_imports() -> None:
    source = r'''
package api
import (
    "net/http"
    "github.com/gin-gonic/gin"
)
type Server struct {}
func (s *Server) create(c *gin.Context) {}
func health(w http.ResponseWriter, r *http.Request) {}
func routes(router *gin.Engine) {
    router.POST("/users", s.create)
    http.HandleFunc("/health", health)
    _ = os.Getenv("SERVICE_REGION")
    // router.DELETE("/fake", remove)
}
'''

    report = detect_extended_source(source, "backend", "api/server.go")

    assert {(item.method, item.path, item.name) for item in report.interfaces} == {
        ("POST", "/users", "s.create"),
        ("ANY", "/health", "health"),
    }
    assert {item.qualified_name for item in report.symbols} == {
        "Server",
        "Server.create",
        "health",
        "routes",
    }
    assert [item.key for item in report.configurations] == ["SERVICE_REGION"]
    assert [item.name for item in report.dependencies] == [
        "net/http",
        "github.com/gin-gonic/gin",
    ]


def test_rust_detector_extracts_actix_axum_symbols_env_and_external_crates() -> None:
    source = r'''
use axum::{routing::post, Router};
use std::env;
pub struct AppState {}
#[get("/health")]
async fn health() -> &'static str { "ok" }
fn routes() -> Router {
    let region = env::var("SERVICE_REGION").unwrap();
    Router::new().route("/users", post(create_user))
}
// #[delete("/fake")] fn fake() {}
/* outer /* inner */ #[delete("/also-fake")] fn also_fake() {} */
'''

    report = detect_extended_source(source, "backend", "src/main.rs")

    assert {(item.method, item.path, item.name) for item in report.interfaces} == {
        ("GET", "/health", "health"),
        ("POST", "/users", "create_user"),
    }
    assert {item.qualified_name for item in report.symbols} == {"AppState", "health", "routes"}
    assert [item.key for item in report.configurations] == ["SERVICE_REGION"]
    assert [item.name for item in report.dependencies] == ["axum"]


def test_csharp_detector_extracts_controller_and_minimal_api_facts() -> None:
    source = r'''
using Microsoft.AspNetCore.Mvc;
[Route("api/[controller]")]
public class UsersController {
    [HttpGet("{id}")]
    public async Task<User> GetUser(string id) {
        var region = Environment.GetEnvironmentVariable("SERVICE_REGION");
        return await service.Get(id);
    }
}
app.MapPost("/users", CreateUser);
// app.MapDelete("/fake", DeleteUser);
'''

    report = detect_extended_source(source, "backend", "Controllers/UsersController.cs")

    assert {(item.method, item.path, item.name) for item in report.interfaces} == {
        ("GET", "/api/Users/{id}", "GetUser"),
        ("POST", "/users", "CreateUser"),
    }
    assert {item.qualified_name for item in report.symbols} == {"UsersController", "GetUser"}
    assert [item.key for item in report.configurations] == ["SERVICE_REGION"]
    assert [item.name for item in report.dependencies] == ["Microsoft.AspNetCore.Mvc"]


def test_platform_detector_extracts_docker_compose_and_kubernetes_facts() -> None:
    dockerfile = """
FROM python:3.13-slim AS runtime
ENV APP_ENV=production API_TOKEN=must-not-leak
EXPOSE 8080/tcp
"""
    compose = """
services:
  api:
    image: ghcr.io/example/api:1.2.3
    environment:
      APP_ENV: production
      API_TOKEN: must-not-leak
    ports:
      - "8080:8000"
"""
    kubernetes = """
apiVersion: apps/v1
kind: Deployment
spec:
  template:
    spec:
      containers:
        - name: api
          image: ghcr.io/example/api:1.2.3
          ports:
            - containerPort: 8000
          env:
            - name: APP_ENV
              value: production
            - name: API_TOKEN
              valueFrom:
                secretKeyRef:
                  name: api-secret
---
apiVersion: networking.k8s.io/v1
kind: Ingress
spec:
  rules:
    - http:
        paths:
          - path: /api
            pathType: Prefix
"""

    docker_report = detect_platform_source(dockerfile, ".", "Dockerfile")
    compose_report = detect_platform_source(compose, ".", "compose.yaml")
    kube_report = detect_platform_source(kubernetes, ".", "deploy/app.yaml")

    assert [(item.name, item.requirement) for item in docker_report.dependencies] == [
        ("python", "python:3.13-slim")
    ]
    assert {item.key for item in docker_report.configurations} == {
        "APP_ENV",
        "API_TOKEN",
        "docker.expose.8080/tcp",
    }
    assert next(item for item in docker_report.configurations if item.key == "API_TOKEN").redacted
    assert [(item.name, item.requirement) for item in compose_report.dependencies] == [
        ("ghcr.io/example/api", "ghcr.io/example/api:1.2.3")
    ]
    assert {item.key for item in compose_report.configurations} == {
        "APP_ENV",
        "API_TOKEN",
        "compose.port.8080:8000",
    }
    assert [item.key for item in kube_report.configurations] == [
        "APP_ENV",
        "API_TOKEN",
        "kubernetes.containerPort.8000",
    ]
    assert next(item for item in kube_report.configurations if item.key == "API_TOKEN").required
    assert [(item.method, item.path) for item in kube_report.interfaces] == [("ANY", "/api")]
    assert "must-not-leak" not in repr((docker_report, compose_report))


def test_dockerfile_ignores_multiline_build_commands() -> None:
    report = detect_platform_source(
        "FROM ubuntu:24.04\nRUN apt-get update \\\n+  && apt-get install -y curl\n",
        ".",
        "Dockerfile",
    )

    assert [(item.name, item.requirement) for item in report.dependencies] == [
        ("ubuntu", "ubuntu:24.04")
    ]


def test_dockerfile_parses_multiline_environment_instruction() -> None:
    report = detect_platform_source(
        "ENV GIT_COMMIT=${GIT_COMMIT} \\\n  BUILD_TIMESTAMP=${BUILD_TIMESTAMP}\n",
        ".",
        "Dockerfile",
    )

    assert [item.key for item in report.configurations] == [
        "GIT_COMMIT",
        "BUILD_TIMESTAMP",
    ]


def test_extended_router_reports_only_supported_path_families() -> None:
    assert is_extended_source("src/main.go")
    assert is_extended_source("deploy/app.yaml")
    assert not is_extended_source("docs/design.md")
    assert detect_extended_source("plain text", ".", "docs/design.md").symbols == ()
