import hashlib
import json
import os
import subprocess
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]
PLUGIN = ROOT / "plugins/loreloop"
INSTALLER = PLUGIN / "scripts/install-loreloop.sh"


def test_codex_plugin_manifest_matches_package_and_marketplace():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    manifest = json.loads((PLUGIN / ".codex-plugin/plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads(
        (ROOT / ".agents/plugins/marketplace.json").read_text(encoding="utf-8")
    )

    assert manifest["name"] == "loreloop"
    assert manifest["version"] == package["version"]
    assert manifest["skills"] == "./skills/"
    assert marketplace["name"] == "loreloop"
    assert marketplace["plugins"] == [
        {
            "name": "loreloop",
            "source": {"source": "local", "path": "./plugins/loreloop"},
            "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
            "category": "Productivity",
        }
    ]


def test_claude_compatible_plugin_manifest_matches_package_and_marketplace():
    package = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    manifest = json.loads((PLUGIN / ".claude-plugin/plugin.json").read_text(encoding="utf-8"))
    marketplace = json.loads((ROOT / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))

    assert manifest["name"] == "loreloop"
    assert manifest["version"] == package["version"]
    assert manifest["skills"] == "./skills/"
    assert marketplace["name"] == "loreloop"
    assert marketplace["plugins"][0]["name"] == "loreloop"
    assert marketplace["plugins"][0]["source"] == "./plugins/loreloop"


def test_plugin_skill_finishes_installation_with_bundled_installer():
    skill = (PLUGIN / "skills/loreloop/SKILL.md").read_text(encoding="utf-8")

    assert "ask for a second permission" in skill
    assert "scripts/install-loreloop.sh" in skill
    assert "Never download and execute a remote installer script directly" in skill
    assert "explicit invocation authorizes initialization" in skill
    assert "loreloop codex status" in skill
    assert "loreloop claude status" in skill
    assert "loreloop comind status" in skill
    assert "configuration files directly" in skill
    assert "directly" in skill
    assert 'Run `loreloop begin "<task>"`' in skill


def test_release_workflow_publishes_checksummed_installer_assets():
    workflow = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")

    assert "find dist -maxdepth 1 -name 'loreloop-*-py3-none-any.whl'" in workflow
    assert "release-assets/install-loreloop.sh" in workflow
    assert "release-assets/install-loreloop.ps1" in workflow
    assert "SHA256SUMS" in workflow
    assert "release-assets/*" in workflow


def _fake_install_environment(tmp_path: Path, wheel: bytes, *, checksum: str | None = None):
    release = tmp_path / "release"
    release.mkdir()
    wheel_name = "loreloop-0.1.0-py3-none-any.whl"
    (release / wheel_name).write_bytes(wheel)
    digest = checksum or hashlib.sha256(wheel).hexdigest()
    (release / "SHA256SUMS").write_text(f"{digest}  {wheel_name}\n", encoding="utf-8")

    home = tmp_path / "home"
    local_bin = home / ".local/bin"
    local_bin.mkdir(parents=True)
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    uv_log = tmp_path / "uv.log"
    runtime_log = tmp_path / "runtime.log"
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        """#!/bin/sh
set -eu
printf '%s\\n' "$@" > "$TEST_UV_LOG"
cat > "$HOME/.local/bin/loreloop" <<'EOF'
#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_RUNTIME_LOG"
exit 0
EOF
chmod +x "$HOME/.local/bin/loreloop"
""",
        encoding="utf-8",
    )
    fake_uv.chmod(0o755)
    env = {
        **os.environ,
        "HOME": str(home),
        "PATH": f"{fake_bin}:{local_bin}:/usr/bin:/bin",
        "LORELOOP_RELEASE_BASE_URL": release.as_uri(),
        "TEST_UV_LOG": str(uv_log),
        "TEST_RUNTIME_LOG": str(runtime_log),
    }
    return env, uv_log, runtime_log


def test_posix_installer_verifies_wheel_before_installing(tmp_path):
    env, uv_log, runtime_log = _fake_install_environment(tmp_path, b"wheel-content")

    result = subprocess.run(
        [str(INSTALLER), "--with-web", "--init"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert "Verified SHA-256" in result.stdout
    assert "Installed LoreLoop:" in result.stdout
    assert "Runtime" not in result.stdout
    uv_args = uv_log.read_text(encoding="utf-8").splitlines()
    assert uv_args[:3] == ["tool", "install", "--force"]
    assert uv_args[3].endswith("loreloop-0.1.0-py3-none-any.whl[web]")
    assert runtime_log.read_text(encoding="utf-8").splitlines() == ["--help", "init --skill"]


def test_posix_installer_can_install_loreloop_and_codex_plugin_together(tmp_path):
    env, _, runtime_log = _fake_install_environment(tmp_path, b"wheel-content")

    result = subprocess.run(
        [str(INSTALLER), "--codex"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert runtime_log.read_text(encoding="utf-8").splitlines() == ["--help", "codex install"]
    assert "restart the installed coding-agent host" in result.stdout


def test_posix_installer_can_install_all_host_integrations(tmp_path):
    env, _, runtime_log = _fake_install_environment(tmp_path, b"wheel-content")

    result = subprocess.run(
        [str(INSTALLER), "--codex", "--claude", "--opencode", "--comind"],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert runtime_log.read_text(encoding="utf-8").splitlines() == [
        "--help",
        "codex install",
        "claude install",
        "opencode install",
        "comind install",
    ]


def test_posix_installer_refuses_checksum_mismatch(tmp_path):
    env, uv_log, _ = _fake_install_environment(
        tmp_path,
        b"tampered-wheel",
        checksum="0" * 64,
    )

    result = subprocess.run(
        [str(INSTALLER)],
        env=env,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "checksum mismatch" in result.stderr
    assert not uv_log.exists()


def test_installers_do_not_pipe_remote_scripts_to_a_shell():
    shell = INSTALLER.read_text(encoding="utf-8")
    powershell = (PLUGIN / "scripts/install-loreloop.ps1").read_text(encoding="utf-8")

    assert "| sh" not in shell
    assert '"$LORELOOP" codex install' in shell
    assert '"$LORELOOP" claude install' in shell
    assert '"$LORELOOP" opencode install' in shell
    assert '"$LORELOOP" comind install' in shell
    assert "Invoke-Expression" not in powershell
    assert "Get-FileHash -Algorithm SHA256" in powershell
    assert "& $LoreLoopPath codex install" in powershell
    assert "& $LoreLoopPath claude install" in powershell
    assert "& $LoreLoopPath opencode install" in powershell
    assert "& $LoreLoopPath comind install" in powershell


def test_agent_installation_guide_hides_internal_packaging():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    chinese = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

    assert "Install and configure LoreLoop for the coding agent running this conversation" in readme
    assert "README.zh-CN.md" in readme
    assert "请为正在运行本次对话的编码代理安装并配置 LoreLoop" in chinese
    for guide in (readme, chinese):
        assert "--codex" in guide
        assert "--claude" in guide
        assert "--opencode" in guide
        assert "--comind" in guide
        assert "git+https://github.com/zhangguiping-xydt/loreloop.git@main" in guide
        assert "Runtime" not in guide
