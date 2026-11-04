"""Copied by ``shed new <name>``. Keep ``name`` equal to the folder name."""

from __future__ import annotations

from pydantic import BaseModel, Field

from shed import Tool, ToolContext


class Input(BaseModel):
    text: str = Field(description="Text to echo back")
    upper: bool = Field(False, description="Upper-case the result")


class Output(BaseModel):
    result: str


def run(params: Input, ctx: ToolContext) -> Output:
    ctx.log("working...")  # progress goes to stderr; never print() from a tool
    return Output(result=params.text.upper() if params.upper else params.text)


TOOL = Tool(
    name="__NAME__",
    description="One or two sentences: what this does and when an agent should reach for it.",
    input=Input,
    run=run,
    render=lambda output: output.result,
    examples=("shed __NAME__ --text hello", "shed __NAME__ --text hello --upper --json"),
    version="0.1.0",  # bump when the inputs or the result shape change; note it in CHANGELOG.md
)
