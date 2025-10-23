"""Utilities for Python 3.14 template-string parsing."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, Optional, Tuple, List, Mapping, Any

from .lexer import Token, BasicLexer, LexerState
from .tree import Tree
from .utils import TextSlice


@dataclass
class SourceInfo:
    filename: str
    text: str
    segment_spans: List[Tuple[int, int]]
    interpolation_spans: List[Tuple[int, int]]


@dataclass
class TemplateContext:
    lexer_conf: 'LexerConf'
    tree_terminal_map: Dict[str, str]
    pyobj_types: Mapping[str, Any]
    source_info: Optional[SourceInfo] = None


def _sanitize_type_name(type_name: str) -> str:
    import re

    sanitized = re.sub(r'[^0-9A-Za-z_]', '_', type_name)
    return sanitized.upper()


def tokenize_template(template, ctx: TemplateContext) -> Iterator[Token]:
    """Yield tokens for a template object."""

    lexer = BasicLexer(ctx.lexer_conf)
    has_source = ctx.source_info is not None
    strings = template.strings
    interpolations = template.interpolations

    for index, static_str in enumerate(strings):
        if static_str:
            if has_source:
                span_start, span_end = ctx.source_info.segment_spans[index]
                text_slice = TextSlice(ctx.source_info.text, span_start, span_end)
            else:
                text_slice = TextSlice(static_str, 0, len(static_str))
            state = LexerState(text_slice)
            yield from lexer.lex(state, None)

        if index < len(interpolations):
            interp = interpolations[index]
            value = interp.value
            meta = _interpolation_meta(ctx.source_info, index)

            if isinstance(value, Tree):
                label = value.data
                term_name = ctx.tree_terminal_map.get(label)
                if term_name is None:
                    raise ValueError(
                        f"Cannot splice Tree('{label}'): grammar does not accept this label")

                if hasattr(value, 'meta') and value.meta:
                    m = value.meta
                    token = Token(term_name, value,
                                  start_pos=getattr(m, 'start_pos', None),
                                  line=getattr(m, 'line', None),
                                  column=getattr(m, 'column', None),
                                  end_pos=getattr(m, 'end_pos', None),
                                  end_line=getattr(m, 'end_line', None),
                                  end_column=getattr(m, 'end_column', None))
                else:
                    token = Token(term_name, value, **meta)
                yield token
            else:
                token_type = 'PYOBJ'
                matched_type = _match_pyobj_type(value, ctx.pyobj_types)
                if matched_type is not None:
                    token_type = f'PYOBJ__{matched_type}'
                token = Token(token_type, value, **meta)
                yield token


def _match_pyobj_type(value: Any, type_map: Mapping[str, Any]) -> Optional[str]:
    for type_name, expected in type_map.items():
        if isinstance(value, expected):
            return _sanitize_type_name(type_name)
    return None


def _interpolation_meta(source_info: Optional[SourceInfo], index: int) -> Dict[str, Optional[int]]:
    if source_info is None:
        return {}

    start, end = source_info.interpolation_spans[index]
    text = source_info.text

    line_start = text.count('\n', 0, start) + 1
    column_start = start - text.rfind('\n', 0, start) if '\n' in text[:start] else start + 1

    lines_span = text.count('\n', start, end)
    end_line = line_start + lines_span
    if lines_span:
        last_newline = text.rfind('\n', start, end)
        end_column = end - last_newline
    else:
        end_column = column_start + (end - start)

    return {
        'start_pos': start,
        'line': line_start,
        'column': column_start,
        'end_pos': end,
        'end_line': end_line,
        'end_column': end_column,
    }


def splice_inserted_trees(node):
    """Replace TREE__ tokens in a tree with their embedded Tree values."""

    if not isinstance(node, Tree):
        return

    new_children = []
    for child in node.children:
        if isinstance(child, Token) and child.type.startswith('TREE__') and isinstance(child.value, Tree):
            new_children.append(child.value)
        else:
            if isinstance(child, Tree):
                splice_inserted_trees(child)
            new_children.append(child)

    node.children = new_children


from .common import LexerConf  # Late import to avoid circular reference in type checking

