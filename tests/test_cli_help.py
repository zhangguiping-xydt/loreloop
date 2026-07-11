import argparse
from pathlib import Path

from loreloop.cli import build_parser, main


PUBLIC_HELP_PATHS = [
    (),
    ("doctor",),
    ("init",),
    ("demo",),
    ("ingest",),
    ("repo",),
    ("repo", "add"),
    ("repo", "list"),
    ("repo", "remove"),
    ("project",),
    ("project", "add"),
    ("project", "list"),
    ("project", "remove"),
    ("verify",),
    ("begin",),
    ("complete",),
    ("run",),
    ("check",),
    ("report",),
    ("harvest",),
    ("knowledge",),
    ("knowledge", "list"),
    ("knowledge", "show"),
    ("knowledge", "review"),
    ("knowledge", "search"),
    ("knowledge", "import"),
    ("knowledge", "export"),
    ("knowledge", "approve"),
    ("knowledge", "reject"),
    ("knowledge", "reopen"),
    ("knowledge", "supersede"),
    ("knowledge", "unsupersede"),
    ("knowledge", "verify"),
    ("knowledge", "usage"),
]


def _subparser(parser: argparse.ArgumentParser, token: str) -> argparse.ArgumentParser:
    action = next(item for item in parser._actions if isinstance(item, argparse._SubParsersAction))
    return action.choices[token]


def render_help_snapshot() -> str:
    root = build_parser()
    sections = []
    for path in PUBLIC_HELP_PATHS:
        parser = root
        for token in path:
            parser = _subparser(parser, token)
        command = " ".join(("loreloop", *path, "--help"))
        sections.append(f"$ {command}\n{parser.format_help().rstrip()}")
    return "\n\n".join(sections) + "\n"


def _normalize_argparse_help(text: str) -> str:
    """Ignore the subparser ellipsis placement changed by Python 3.13."""
    normalized = []
    in_usage = False
    for line in text.splitlines():
        if line.startswith("usage: "):
            in_usage = True
        elif in_usage and not line.strip():
            in_usage = False
        if in_usage and line.strip() == "...":
            continue
        if in_usage and line.endswith(" ..."):
            line = line[:-4]
        normalized.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(normalized) + suffix


def test_all_public_help_matches_reviewed_snapshot():
    snapshot = Path(__file__).with_name("snapshots") / "cli-help.txt"
    assert _normalize_argparse_help(render_help_snapshot()) == _normalize_argparse_help(
        snapshot.read_text(encoding="utf-8")
    )


def test_argparse_subparser_ellipsis_normalization_is_version_stable():
    old = "usage: loreloop [-h] {run}\n                ...\n\noptions:\n"
    new = "usage: loreloop [-h] {run} ...\n\noptions:\n"
    assert _normalize_argparse_help(old) == _normalize_argparse_help(new)


def test_action_help_uses_specific_positional_names():
    text = render_help_snapshot()
    for name in (
        "REPO_PATH",
        "REPO_NAME",
        "PROJECT_PATH",
        "PROJECT_ID",
        "QUERY",
        "ENTRY_ID",
        "NEW_ENTRY_ID",
        "OLD_ENTRY_ID",
    ):
        assert name in text


def test_active_filter_help_describes_all_chain_retired_entries():
    parser = _subparser(_subparser(build_parser(), "knowledge"), "list")

    assert "exclude all chain-retired entries" in parser.format_help()


def test_every_public_help_path_runs_through_cli(capsys):
    for path in PUBLIC_HELP_PATHS:
        assert main([*path, "--help"]) == 0
        shown = capsys.readouterr().out
        assert shown.startswith("usage: loreloop")


def test_parse_failure_has_error_reason_and_next_action(capsys):
    assert main(["knowledge", "approve"]) == 2
    lines = capsys.readouterr().err.splitlines()
    assert len(lines) == 3
    assert lines[0] == "error: invalid command"
    assert lines[1].startswith("reason: ")
    assert lines[2].startswith("next: ")


def test_agent_override_is_accepted_before_or_after_action_name():
    parser = build_parser()
    before = parser.parse_args(["--agent", "codex", "run", "task"])
    after = parser.parse_args(["run", "task", "--agent", "codex"])
    assert before.agent == "codex"
    assert after.agent == "codex"
