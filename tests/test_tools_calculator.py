"""Калькулятор: только арифметика, никаких eval/имён/атрибутов."""

import pytest

from newsai.agents.tools import FixtureDataSource, ToolContext, ToolError, run_tool


@pytest.fixture(scope="module")
def ctx() -> ToolContext:
    return ToolContext.for_story(
        FixtureDataSource(),
        story_id="st_2026-09-11_keyrate",
        company_id="U_DEMO_BORROWER",
        role="cfo",
        trace_id="t",
    )


def calc(ctx, expr):
    out, call = run_tool(ctx, "calculator", {"expression": expr})
    assert call.ok
    return out.value


@pytest.mark.parametrize(
    "expr,expected",
    [
        ("1 + 2 * 3", 7),
        ("(1 + 2) * 3", 9),
        ("-5 + 2", -3),
        ("2 ** 10", 1024),
        ("7 % 4", 3),
        ("10 / 4", 2.5),
        ("round(14 * 0.0355, 2)", 0.5),
        ("abs(-3) + min(4, 2) + max(1, 9)", 14),
        ("0.35 * 100", 35),
    ],
)
def test_valid(ctx, expr, expected):
    assert calc(ctx, expr) == pytest.approx(expected)


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('ls')",
        "__import__('os')",
        "().__class__",
        "(1).__add__(2)",
        "x + 1",
        "open('f')",
        "exec('1')",
        "eval('1')",
        "'a' * 3",
        "[1, 2][0]",
        "lambda: 1",
        "1 if True else 2",
        "True + 1",
        "None",
        "1j * 2",
        "9 ** 9 ** 9",
        "10 ** 1000",
        "2 ** -1000",
        "(-8) ** 0.5",
        "1 / 0",
        "5 % 0",
        "1e308 * 10",
        "round(1, 2, 3)",
        "round(1.5, 99)",
        "abs(1, 2)",
        "min()",
        "max(key=abs)",
        "1 + " * 40 + "1",
        "(" * 100 + "1" + ")" * 100,
        "9" * 250,
        "1; import os",
        "a.b",
        "print(1)",
        "round(abs)",
    ],
)
def test_rejected(ctx, expr):
    with pytest.raises(ToolError) as e:
        run_tool(ctx, "calculator", {"expression": expr})
    assert e.value.code in {"bad_expression", "bad_args"}
    assert e.value.call.ok is False
