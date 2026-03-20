"""Safe expression evaluator for condition nodes.

Replaces Python's built-in eval() with a restricted AST-walking evaluator
that only allows comparison, boolean, and arithmetic operations on data
from the execution context.  No attribute access, function calls, imports,
or code execution of any kind.

Supported syntax examples:
    context.node_1.status == "completed"
    output.node_2.score > 0.8
    output.node_1.count >= 5 and output.node_2.active == true
    trigger.priority in ["high", "critical"]
    not output.node_3.error
"""

from __future__ import annotations

import ast
import logging
import operator
from typing import Any

logger = logging.getLogger(__name__)

_COMPARE_OPS = {
    ast.Eq: operator.eq,
    ast.NotEq: operator.ne,
    ast.Lt: operator.lt,
    ast.LtE: operator.le,
    ast.Gt: operator.gt,
    ast.GtE: operator.ge,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Is: operator.is_,
    ast.IsNot: operator.is_not,
}

_BOOL_OPS = {
    ast.And: all,
    ast.Or: any,
}

_UNARY_OPS = {
    ast.Not: operator.not_,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Mod: operator.mod,
}


class SafeEvalError(Exception):
    """Raised when the expression contains disallowed syntax."""


def safe_eval(expr: str, variables: dict[str, Any]) -> Any:
    """Evaluate a restricted expression string against the given variables.

    Only literals, name lookups, attribute access on variable values,
    subscript access, comparisons, boolean ops, and basic arithmetic
    are allowed.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise SafeEvalError(f"Invalid expression syntax: {exc}") from exc

    return _eval_node(tree.body, variables)


def _eval_node(node: ast.AST, env: dict[str, Any]) -> Any:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, env)

    if isinstance(node, ast.Constant):
        return node.value

    if isinstance(node, ast.Name):
        if node.id == "true":
            return True
        if node.id == "false":
            return False
        if node.id == "none" or node.id == "None":
            return None
        if node.id not in env:
            raise SafeEvalError(f"Unknown variable: {node.id}")
        return env[node.id]

    if isinstance(node, ast.Attribute):
        val = _eval_node(node.value, env)
        if isinstance(val, dict):
            return val.get(node.attr)
        raise SafeEvalError(f"Attribute access not allowed on {type(val).__name__}")

    if isinstance(node, ast.Subscript):
        val = _eval_node(node.value, env)
        key = _eval_node(node.slice, env)
        if isinstance(val, (dict, list)):
            try:
                return val[key]
            except (KeyError, IndexError, TypeError):
                return None
        raise SafeEvalError(f"Subscript access not allowed on {type(val).__name__}")

    if isinstance(node, ast.Compare):
        left = _eval_node(node.left, env)
        for op_node, comparator in zip(node.ops, node.comparators):
            op_func = _COMPARE_OPS.get(type(op_node))
            if not op_func:
                raise SafeEvalError(f"Unsupported comparison: {type(op_node).__name__}")
            right = _eval_node(comparator, env)
            if not op_func(left, right):
                return False
            left = right
        return True

    if isinstance(node, ast.BoolOp):
        op_func = _BOOL_OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Unsupported boolean op: {type(node.op).__name__}")
        values = [_eval_node(v, env) for v in node.values]
        return op_func(values)

    if isinstance(node, ast.UnaryOp):
        op_func = _UNARY_OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Unsupported unary op: {type(node.op).__name__}")
        return op_func(_eval_node(node.operand, env))

    if isinstance(node, ast.BinOp):
        op_func = _BIN_OPS.get(type(node.op))
        if not op_func:
            raise SafeEvalError(f"Unsupported binary op: {type(node.op).__name__}")
        return op_func(_eval_node(node.left, env), _eval_node(node.right, env))

    if isinstance(node, ast.List):
        return [_eval_node(el, env) for el in node.elts]

    if isinstance(node, ast.Tuple):
        return tuple(_eval_node(el, env) for el in node.elts)

    if isinstance(node, ast.Dict):
        return {
            _eval_node(k, env): _eval_node(v, env)
            for k, v in zip(node.keys, node.values)
        }

    if isinstance(node, ast.IfExp):
        test = _eval_node(node.test, env)
        return _eval_node(node.body, env) if test else _eval_node(node.orelse, env)

    raise SafeEvalError(
        f"Disallowed expression node: {type(node).__name__}. "
        "Only comparisons, boolean ops, arithmetic, and variable lookups are allowed."
    )
