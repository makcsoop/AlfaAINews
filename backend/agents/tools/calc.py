"""Безопасный калькулятор: разбор через ast, без eval/exec."""

import ast
import math
import operator
from typing import Any

from backend.agents.tools.context import ToolError

MAX_LEN = 200
MAX_NODES = 60
MAX_EXP = 100
MAX_ABS = 1e30

_BIN: dict[type, Any] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
    ast.Mod: operator.mod,
}
_UN: dict[type, Any] = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS: dict[str, Any] = {"round": round, "abs": abs, "min": min, "max": max}


def _bad(msg: str) -> ToolError:
    return ToolError("bad_expression", msg)


def _check(x: Any) -> Any:
    if isinstance(x, float) and not math.isfinite(x):
        raise _bad("результат не конечен")
    if abs(x) > MAX_ABS:
        raise _bad("слишком большое число")
    return x


def _eval(node: ast.AST) -> Any:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if type(node.value) not in (int, float):
            raise _bad("допустимы только числа")
        return _check(node.value)
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXP:
            raise _bad("слишком большая степень")
        try:
            res = _BIN[type(node.op)](left, right)
        except ZeroDivisionError:
            raise _bad("деление на ноль") from None
        except (OverflowError, ValueError):
            raise _bad("ошибка вычисления") from None
        if isinstance(res, complex):
            raise _bad("комплексные числа не поддерживаются")
        return _check(res)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UN:
        return _check(_UN[type(node.op)](_eval(node.operand)))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCS
        and not node.keywords
        and 1 <= len(node.args) <= 8
    ):
        name = node.func.id
        args = [_eval(a) for a in node.args]
        if name == "abs" and len(args) != 1:
            raise _bad("abs принимает один аргумент")
        if name == "round":
            if len(args) > 2:
                raise _bad("round принимает до двух аргументов")
            if len(args) == 2 and not (0 <= args[1] <= 10 and float(args[1]).is_integer()):
                raise _bad("round: недопустимое число знаков")
            if len(args) == 2:
                return _check(round(args[0], int(args[1])))
        try:
            return _check(_FUNCS[name](*args))
        except (TypeError, ValueError, OverflowError):
            raise _bad("ошибка вызова функции") from None
    raise _bad("недопустимая конструкция")


def safe_eval(expression: str) -> float:
    if len(expression) > MAX_LEN:
        raise _bad("выражение слишком длинное")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except (SyntaxError, ValueError, RecursionError, MemoryError):
        raise _bad("не удалось разобрать выражение") from None
    if sum(1 for _ in ast.walk(tree)) > MAX_NODES:
        raise _bad("выражение слишком сложное")
    try:
        return _eval(tree)
    except RecursionError:
        raise _bad("выражение слишком глубокое") from None
