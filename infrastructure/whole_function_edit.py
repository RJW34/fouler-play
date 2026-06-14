#!/usr/bin/env python3
"""whole_function_edit.py — AST-anchored whole-function replacement.

ROOT-CAUSE FIX (learning loop, generator side). The improve agent previously
asked the model for a *unified diff* against the 6.7k-line fp/search/main.py and
applied it with git apply / patch --fuzz. Against a file that large the model's
hunk context is routinely a few lines off, so the patch fails to apply
("patch does not apply") -- the dominant `agent_failed` outcome and the reason
the loop has a ~0% apply rate and NEVER reached its acceptance gate.

This module sidesteps diffs entirely: the model returns the ENTIRE replacement
body of ONE named function, and we splice it in by the function's AST node span.
No hunk-context matching, so apply-rate goes ~0% -> ~100% for any syntactically
valid replacement. The splice is validated: the new text must parse, and the
replaced span must correspond to exactly one top-level/def node of that name.
"""
from __future__ import annotations

import ast
import re
import textwrap
from dataclasses import dataclass


@dataclass
class FunctionSpan:
    name: str
    start_line: int  # 1-based, inclusive (the `def`/decorator line)
    end_line: int    # 1-based, inclusive
    col_offset: int  # indentation of the def
    source: str      # the exact current source of the span


def _iter_funcdefs(tree: ast.AST):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def find_function_span(source: str, name: str) -> FunctionSpan | None:
    """Locate a uniquely-named function (top-level or method) by AST span.
    Returns None if absent or ambiguous (more than one def with that name)."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return None
    matches = [n for n in _iter_funcdefs(tree) if n.name == name]
    if len(matches) != 1:
        return None
    node = matches[0]
    # Include leading decorators in the replaced span so we never duplicate them.
    start = node.lineno
    if node.decorator_list:
        start = min(start, min(d.lineno for d in node.decorator_list))
    end = getattr(node, "end_lineno", node.lineno)
    lines = source.splitlines(keepends=True)
    span_src = "".join(lines[start - 1:end])
    return FunctionSpan(
        name=name,
        start_line=start,
        end_line=end,
        col_offset=node.col_offset,
        source=span_src,
    )


def list_function_names(source: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return [n.name for n in _iter_funcdefs(tree)]


def extract_replacement_function(response: str) -> str | None:
    """Pull a single python function body out of a model response.

    Accepts the function fenced in ```python ... ``` or bare. Returns the
    dedented function text starting at the first `def`/`async def`/`@decorator`
    line, or None if no function is present.
    """
    text = response
    # Prefer a fenced python block if present.
    fence = re.search(r"```(?:python|py)?\s*\n(.*?)```", response, re.DOTALL)
    if fence:
        text = fence.group(1)
    lines = text.splitlines()
    # Find the first line that begins a function (allowing a decorator first).
    start_idx = None
    for i, ln in enumerate(lines):
        stripped = ln.lstrip()
        if stripped.startswith("@") or stripped.startswith("def ") or stripped.startswith("async def "):
            start_idx = i
            break
    if start_idx is None:
        return None
    # Take from start to the end of the block (rest of fence/text).
    candidate = "\n".join(lines[start_idx:]).rstrip() + "\n"
    candidate = textwrap.dedent(candidate)
    # Validate it parses as a module containing exactly one function.
    try:
        tree = ast.parse(candidate)
    except SyntaxError:
        return None
    funcs = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(funcs) != 1:
        return None
    return candidate


def _reindent(block: str, target_col: int) -> str:
    """Re-indent a dedented function block to sit at target_col spaces.
    The model returns a top-level (col 0) function; methods need the class
    indentation restored."""
    if target_col <= 0:
        return block
    pad = " " * target_col
    out = []
    for ln in block.splitlines():
        out.append(pad + ln if ln.strip() else ln)
    return "\n".join(out) + ("\n" if block.endswith("\n") else "")


def splice_function(source: str, name: str, new_func_text: str) -> tuple[str | None, str]:
    """Replace function `name` in source with new_func_text.

    Returns (new_source, message). new_source is None on failure (message
    explains why). On success the result is guaranteed to parse and to still
    contain exactly one function named `name`.
    """
    span = find_function_span(source, name)
    if span is None:
        return None, f"target function '{name}' not found or ambiguous in source"

    # Confirm the replacement defines the same function name.
    repl = extract_replacement_function(new_func_text) or new_func_text
    try:
        repl_tree = ast.parse(textwrap.dedent(repl))
    except SyntaxError as e:
        return None, f"replacement does not parse: {e}"
    repl_funcs = [n for n in repl_tree.body
                  if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    if len(repl_funcs) != 1:
        return None, f"replacement must contain exactly one function (found {len(repl_funcs)})"
    if repl_funcs[0].name != name:
        return None, (f"replacement defines '{repl_funcs[0].name}', "
                      f"expected '{name}'")

    repl_block = _reindent(textwrap.dedent(repl).rstrip("\n") + "\n", span.col_offset)

    lines = source.splitlines(keepends=True)
    before = "".join(lines[: span.start_line - 1])
    after = "".join(lines[span.end_line:])
    # Ensure a single newline boundary between segments.
    if before and not before.endswith("\n"):
        before += "\n"
    if not repl_block.endswith("\n"):
        repl_block += "\n"
    new_source = before + repl_block + after

    # Validate the whole module still parses and the function is still unique.
    try:
        new_tree = ast.parse(new_source)
    except SyntaxError as e:
        return None, f"spliced source does not parse: {e}"
    count = sum(1 for n in _iter_funcdefs(new_tree) if n.name == name)
    if count != 1:
        return None, f"splice produced {count} functions named '{name}' (expected 1)"
    return new_source, f"spliced '{name}' ({span.end_line - span.start_line + 1} lines -> {repl_block.count(chr(10))} lines)"
