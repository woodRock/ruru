"""The skill and the README describe the CLI; these tests keep them honest."""

import argparse
import json
import re
from pathlib import Path

import pytest
import yaml

from ruru import cli

ROOT = Path(__file__).parent.parent
SKILL = (ROOT / "skills" / "ruru" / "SKILL.md").read_text()
README = (ROOT / "README.md").read_text()


def _commands() -> dict[str, set[str]]:
    """Top-level commands -> their actions (for `feeds`/`project`)."""
    parser = cli.build_parser()
    out = {}
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                actions = {n for a in sub._actions if isinstance(a, argparse._SubParsersAction) for n in a.choices}
                out[name] = actions
    return out


def _code(text: str) -> str:
    """Only what is written as code: fenced blocks and `inline spans` (prose says 'ruru has…')."""
    blocks = re.findall(r"```.*?```", text, re.S)
    spans = re.findall(r"`([^`\n]+)`", re.sub(r"```.*?```", "", text, flags=re.S))
    return "\n".join(blocks + spans)


def _named(text: str) -> set[tuple[str, str | None]]:
    """Every `ruru COMMAND [ACTION]` written as code (inline, in blocks or in permission rules)."""
    found = set()
    pattern = r"(?<!>>> )(?<!<<< )\bruru ((?:-[A-Za-z]+ \S+ )*)([a-z]+)(?: ([a-z]+))?"  # not the block markers
    for m in re.finditer(pattern, _code(text)):
        found.add((m.group(2), m.group(3)))
    return found


def test_skill_front_matter():
    assert SKILL.startswith("---\n")
    meta = yaml.safe_load(SKILL.split("---\n")[1])
    assert meta["name"] == "ruru" and len(meta["description"]) > 100


def test_every_command_in_the_skill_exists(capsys):
    commands = _commands()
    help_text = cli.build_parser().format_help()
    named = _named(SKILL)
    assert len({c for c, _ in named}) >= 15
    for command, action in named:
        assert command in commands, f"the skill names `ruru {command}`, which doesn't exist"
        assert re.search(rf"^\s+{command}\b", help_text, re.M), f"`ruru {command}` is missing from `ruru -h`"
        if commands[command] and action and action not in commands[command] and action.isalpha():
            # `ruru feeds add`, `ruru project list`, …: the action must exist too (words like
            # `ruru tag KEY` are arguments, not actions, for commands without actions).
            assert action in {"wizard"} | commands[command], f"`ruru {command} {action}` doesn't exist"


def test_skill_forbids_the_decisions_that_are_jesses():
    assert "never run `ruru keep`, `ruru later` or `ruru cite`" in SKILL
    assert "--by claude --reason" in SKILL and "ruru unskip" in SKILL
    assert "abstract only" in SKILL.lower() and "Never invent a citation" in SKILL


def test_readme_permission_rules():
    block = re.search(r"```json\n(\{\n  \"permissions\".*?)\n```", README, re.S)
    assert block, "README permission snippet not found"
    rules = json.loads(block.group(1))["permissions"]
    commands = _commands()
    for rule in rules["allow"] + rules["deny"]:
        m = re.fullmatch(r"Bash\(ruru ([a-z]+)(?: ([a-z]+))?:\*\)", rule)
        assert m, rule
        assert m.group(1) in commands, rule
        if m.group(2):
            assert m.group(2) in commands[m.group(1)], rule
    for decision in ("keep", "later", "cite"):  # anything that adds to the library or a paper
        assert f"Bash(ruru {decision}:*)" in rules["deny"]
    allowed = {r.split()[1].rstrip(":*)") for r in rules["allow"]}
    assert not allowed & {"keep", "later", "cite", "check"}
    assert "skip" in allowed


@pytest.mark.parametrize("doc", ["README.md", "docs/scheduling.md", "docs/json.md", "CLAUDE.md"])
def test_docs_name_only_real_commands(doc):
    commands = _commands()
    for command, _ in _named((ROOT / doc).read_text()):
        assert command in commands, f"docs mention `ruru {command}`"
