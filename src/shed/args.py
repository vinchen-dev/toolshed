from __future__ import annotations

import argparse
import enum
import types
import typing
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, cast, get_args, get_origin

from pydantic import BaseModel, ValidationError

from shed.define import Tool
from shed.lib.errors import UsageError

ScalarKind = Literal["str", "int", "float", "bool"]
FlagKind = Literal["str", "int", "float", "bool", "list[str]", "list[int]", "list[float]"]

_CONVERTERS: dict[str, Callable[[str], Any]] = {"str": str, "int": int, "float": float}


class UnsupportedFieldError(TypeError):
    """A tool declared an input field whose type cannot be expressed as a CLI flag."""


@dataclass(frozen=True, slots=True)
class FlagSpec:
    key: str
    kind: FlagKind
    description: str
    required: bool
    default: Any
    choices: tuple[str, ...] | None

    @property
    def option(self) -> str:
        return f"--{self.key.replace('_', '-')}"


@dataclass(frozen=True, slots=True)
class Invocation:
    params: BaseModel | None
    """Validated input, or None when ``--help`` was requested."""

    json_mode: bool
    help: bool


def _unwrap_optional(annotation: Any, key: str) -> Any:
    origin = get_origin(annotation)
    if origin is typing.Union or origin is types.UnionType:
        members = [a for a in get_args(annotation) if a is not type(None)]
        if len(members) == 1:
            return members[0]
        raise UnsupportedFieldError(f'input field "{key}": unions other than "X | None" cannot be CLI flags')
    return annotation


def _scalar(annotation: Any, key: str) -> tuple[ScalarKind, tuple[str, ...] | None]:
    if annotation is str:
        return "str", None
    if annotation is bool:
        return "bool", None
    if annotation is int:
        return "int", None
    if annotation is float:
        return "float", None
    if get_origin(annotation) is Literal:
        values = get_args(annotation)
        if not all(isinstance(v, str) for v in values):
            raise UnsupportedFieldError(f'input field "{key}": Literal choices must be strings')
        return "str", tuple(values)
    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return "str", tuple(str(member.value) for member in annotation)
    raise UnsupportedFieldError(
        f'input field "{key}": type {annotation!r} cannot be a CLI flag. '
        "Use str, int, float, bool, Literal[str, ...], a str Enum, or a list of those."
    )


def describe_flags(model: type[BaseModel]) -> list[FlagSpec]:
    """Describe each field of a tool's input model as a CLI flag."""
    flags: list[FlagSpec] = []
    for key, field in model.model_fields.items():
        annotation = _unwrap_optional(field.annotation, key)
        if get_origin(annotation) is list:
            (element,) = get_args(annotation)
            element_kind, choices = _scalar(element, key)
            if element_kind == "bool":
                raise UnsupportedFieldError(f'input field "{key}": lists of bool cannot be CLI flags')
            kind = cast(FlagKind, f"list[{element_kind}]")
        else:
            kind, choices = _scalar(annotation, key)
        required = field.is_required()
        default = None if required else field.get_default(call_default_factory=True)
        flags.append(
            FlagSpec(
                key=key,
                kind=kind,
                description=field.description or "",
                required=required,
                default=default,
                choices=choices,
            )
        )
    return flags


class _Parser(argparse.ArgumentParser):
    """argparse that raises instead of printing and exiting, so the CLI owns all output."""

    def error(self, message: str) -> NoReturn:
        raise UsageError(f"{message}. All inputs are named flags; see `{self.prog} --help`.")


def build_parser(tool: Tool[Any, Any], flags: Sequence[FlagSpec]) -> argparse.ArgumentParser:
    parser = _Parser(prog=f"shed {tool.name}", add_help=False, allow_abbrev=False)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--help", "-h", action="store_true")
    for flag in flags:
        # Accept both --snake_case and --kebab-case spellings of the field name.
        names = [flag.option] if flag.option == f"--{flag.key}" else [flag.option, f"--{flag.key}"]
        if flag.kind == "bool":
            parser.add_argument(
                *names, dest=flag.key, action=argparse.BooleanOptionalAction, default=argparse.SUPPRESS
            )
        elif flag.kind.startswith("list["):
            element = flag.kind[5:-1]
            parser.add_argument(
                *names,
                dest=flag.key,
                action="append",
                type=_CONVERTERS[element],
                default=argparse.SUPPRESS,
                metavar=element,
            )
        else:
            parser.add_argument(
                *names,
                dest=flag.key,
                type=_CONVERTERS[flag.kind],
                default=argparse.SUPPRESS,
                metavar=flag.kind,
            )
    return parser


def parse_tool_args(tool: Tool[Any, Any], argv: Sequence[str]) -> Invocation:
    """Parse argv for a tool into a validated input model plus the global --json / --help flags."""
    flags = describe_flags(tool.input)
    namespace = build_parser(tool, flags).parse_args(list(argv))
    values = vars(namespace)
    json_mode = bool(values.pop("json", False))
    help_mode = bool(values.pop("help", False))
    if help_mode:
        return Invocation(params=None, json_mode=json_mode, help=True)
    try:
        params = tool.input.model_validate(values)
    except ValidationError as exc:
        lines = [f"--{'.'.join(str(p) for p in err['loc']) or 'input'}: {err['msg']}" for err in exc.errors()]
        joined = "\n  ".join(lines)
        raise UsageError(f"Invalid input for {tool.name}:\n  {joined}\nSee `shed help {tool.name}`.") from exc
    return Invocation(params=params, json_mode=json_mode, help=False)


def format_flags(flags: Sequence[FlagSpec]) -> str:
    """Format the flag list for ``shed help <tool>``."""
    if not flags:
        return "  (no flags)"
    left = [f.option if f.kind == "bool" else f"{f.option} <{f.kind}>" for f in flags]
    width = max(len(s) for s in left)
    lines: list[str] = []
    for flag, head in zip(flags, left, strict=True):
        notes: list[str] = []
        if flag.required:
            notes.append("required")
        elif flag.default not in (None, [], ()):
            notes.append(f"default: {flag.default!r}")
        if flag.choices:
            notes.append(f"one of: {', '.join(flag.choices)}")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        lines.append(f"  {head.ljust(width)}  {flag.description}{suffix}")
    return "\n".join(lines)
