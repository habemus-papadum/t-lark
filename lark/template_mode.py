"""Template tokenization helpers for Python 3.14 template strings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .common import LexerConf
from .lexer import BasicLexer, LexerThread, Token
from .tree import Tree
from .utils import TextSlice


@dataclass
class SourceInfo:
    """Optional source metadata attached to Template objects."""

    filename: Optional[str]
    text: str
    segment_spans: List[Tuple[int, int]]
    interpolation_spans: List[Tuple[int, int]]


@dataclass
class TemplateContext:
    lexer_conf: LexerConf
    tree_terminal_map: Dict[str, str]
    pyobj_term_name: str
    source_info: Optional[SourceInfo] = None


def tokenize_template(template, ctx: TemplateContext, static_lexer: BasicLexer) -> Iterator[Token]:
    """Yield tokens for a Python Template object."""

    has_source = ctx.source_info is not None
    strings = template.strings
    interpolations = template.interpolations

    for index, static_str in enumerate(strings):
        if static_str:
            if has_source:
                start, end = ctx.source_info.segment_spans[index]
                text_slice = TextSlice(ctx.source_info.text, start, end)
            else:
                text_slice = TextSlice(static_str, 0, len(static_str))

            lexer_thread = LexerThread.from_text(static_lexer, text_slice)
            for token in lexer_thread.lex(None):
                yield token

        if index < len(interpolations):
            interp = interpolations[index]
            value = interp.value

            meta = _interpolation_meta(ctx.source_info, index) if has_source else _empty_meta()

            if isinstance(value, Tree):
                label = value.data
                term_name = ctx.tree_terminal_map.get(label)
                if not term_name:
                    raise ValueError(
                        f"Cannot splice Tree('{label}'): grammar does not accept this label"
                    )
                token_meta = _tree_meta(value, meta)
                yield Token(term_name, value, **token_meta)
            else:
                yield Token(ctx.pyobj_term_name, value, **meta)


def splice_inserted_trees(node: Tree) -> None:
    """Replace TREE__ tokens with their underlying Tree values."""

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


def _empty_meta() -> Dict[str, Optional[int]]:
    return {
        'start_pos': None,
        'line': None,
        'column': None,
        'end_pos': None,
        'end_line': None,
        'end_column': None,
    }


def _interpolation_meta(source_info: SourceInfo, index: int) -> Dict[str, Optional[int]]:
    start, end = source_info.interpolation_spans[index]
    return _offset_to_meta(source_info.text, start, end)


def _tree_meta(tree: Tree, fallback: Dict[str, Optional[int]]) -> Dict[str, Optional[int]]:
    meta = getattr(tree, 'meta', None)
    if not meta:
        return fallback
    return {
        'start_pos': getattr(meta, 'start_pos', fallback['start_pos']),
        'line': getattr(meta, 'line', fallback['line']),
        'column': getattr(meta, 'column', fallback['column']),
        'end_pos': getattr(meta, 'end_pos', fallback['end_pos']),
        'end_line': getattr(meta, 'end_line', fallback['end_line']),
        'end_column': getattr(meta, 'end_column', fallback['end_column']),
    }


def _offset_to_meta(text: str, start: int, end: int) -> Dict[str, Optional[int]]:
    line = text.count('\n', 0, start) + 1
    line_start = text.rfind('\n', 0, start)
    if line_start == -1:
        column = start + 1
    else:
        column = start - line_start

    lines_within = text.count('\n', start, end)
    if lines_within:
        end_line = line + lines_within
        last_newline = text.rfind('\n', 0, end)
        end_column = end - last_newline
    else:
        end_line = line
        end_column = column + (end - start)

    return {
        'start_pos': start,
        'line': line,
        'column': column,
        'end_pos': end,
        'end_line': end_line,
        'end_column': end_column,
    }
