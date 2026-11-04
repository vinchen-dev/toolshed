from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel, Field

from shed import Tool, UsageError
from shed.args import UnsupportedFieldError, describe_flags, parse_tool_args


class Input(BaseModel):
    name: str = Field(description="Who")
    count: int = Field(2, description="How many")
    loud: bool = False
    mode: Literal["fast", "slow"] | None = None
    tags: list[str] | None = None
    max_size: float = 1.5


TOOL = Tool(name="sample", description="sample", input=Input, run=lambda params, _ctx: params)


def test_describe_flags_maps_fields() -> None:
    flags = describe_flags(Input)
    assert [(f.key, f.kind, f.required) for f in flags] == [
        ("name", "str", True),
        ("count", "int", False),
        ("loud", "bool", False),
        ("mode", "str", False),
        ("tags", "list[str]", False),
        ("max_size", "float", False),
    ]
    assert flags[0].description == "Who"
    assert flags[1].default == 2
    assert flags[3].choices == ("fast", "slow")
    assert flags[5].option == "--max-size"


def test_describe_flags_rejects_nested_models() -> None:
    class Nested(BaseModel):
        inner: Input

    with pytest.raises(UnsupportedFieldError, match="cannot be a CLI flag"):
        describe_flags(Nested)


def test_parses_coerces_and_applies_defaults() -> None:
    argv = [
        "--name",
        "x",
        "--count",
        "5",
        "--loud",
        "--tags",
        "a",
        "--tags",
        "b",
        "--max-size",
        "2",
        "--json",
    ]
    invocation = parse_tool_args(TOOL, argv)
    assert invocation.json_mode is True
    assert invocation.params is not None
    assert invocation.params.model_dump() == {
        "name": "x",
        "count": 5,
        "loud": True,
        "mode": None,
        "tags": ["a", "b"],
        "max_size": 2.0,
    }


def test_accepts_snake_case_spelling_and_no_flag() -> None:
    params = parse_tool_args(TOOL, ["--name", "x", "--max_size", "3", "--no-loud"]).params
    assert isinstance(params, Input)
    assert params.max_size == 3.0
    assert params.loud is False


@pytest.mark.parametrize(
    ("argv", "pattern"),
    [
        ([], "--name"),
        (["--name", "x", "--count", "abc"], "invalid int value"),
        (["--name", "x", "--mode", "medium"], "--mode"),
        (["--name", "x", "--bogus"], "bogus"),
        (["--name", "x", "stray"], "named flags"),
    ],
)
def test_reports_usage_errors(argv: list[str], pattern: str) -> None:
    with pytest.raises(UsageError, match=pattern):
        parse_tool_args(TOOL, argv)


def test_help_short_circuits_validation() -> None:
    assert parse_tool_args(TOOL, ["--help"]).help is True
