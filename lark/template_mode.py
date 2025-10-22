"""Utilities for template parsing mode."""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .common import LexerConf
from .exceptions import UnexpectedCharacters
from .lexer import Token, LexerState
from .tree import Tree
from .utils import TextSlice


@dataclass
class TemplateContext:
    lexer_conf: LexerConf
    lexer: 'BasicLexer'
    tree_terminal_map: Dict[str, str]
    source_info: Optional['SourceInfo']
    overall_start: Optional[int] = None
    overall_end: Optional[int] = None


@dataclass
class SourceInfo:
    filename: str
    text: str
    segment_spans: List[Tuple[int, int]]
    interpolation_spans: List[Tuple[int, int]]


def tokenize_template(template, ctx: TemplateContext) -> Iterator[Token]:
    strings = template.strings
    interpolations = template.interpolations

    callbacks = ctx.lexer_conf.callbacks
    has_source = ctx.source_info is not None

    def _record_span(start: int, end: int) -> None:
        if ctx.overall_start is None or start < ctx.overall_start:
            ctx.overall_start = start
        if ctx.overall_end is None or end > ctx.overall_end:
            ctx.overall_end = end

    for i, static_str in enumerate(strings):
        if has_source:
            start, end = ctx.source_info.segment_spans[i]
            _record_span(start, end)

        if static_str:
            if has_source:
                text_slice = TextSlice(ctx.source_info.text, start, end)
            else:
                text_slice = TextSlice.cast_from(static_str)

            lexer_state = LexerState(text_slice)
            while True:
                try:
                    token = ctx.lexer.next_token(lexer_state, None)
                except EOFError:
                    break
                except UnexpectedCharacters as exc:
                    if exc.char.isspace():
                        lexer_state.line_ctr.feed(exc.char)
                        continue
                    raise
                if not has_source:
                    _clear_token_meta(token)
                yield token

        if i < len(interpolations):
            interp = interpolations[i]
            value = interp.value

            if has_source:
                span_start, span_end = ctx.source_info.interpolation_spans[i]
                meta = _offset_to_meta(ctx.source_info.text, span_start, span_end)
                _record_span(span_start, span_end)
            else:
                meta = {
                    'start_pos': None,
                    'line': None,
                    'column': None,
                    'end_pos': None,
                    'end_line': None,
                    'end_column': None,
                }

            if isinstance(value, Tree):
                label = value.data
                term_name = ctx.tree_terminal_map.get(label)
                if term_name is None:
                    raise ValueError(f"Cannot splice Tree('{label}'): grammar does not produce this label")

                if getattr(value, 'meta', None):
                    meta_obj = value.meta
                    token = Token(
                        term_name,
                        value,
                        start_pos=getattr(meta_obj, 'start_pos', None),
                        line=getattr(meta_obj, 'line', None),
                        column=getattr(meta_obj, 'column', None),
                        end_pos=getattr(meta_obj, 'end_pos', None),
                        end_line=getattr(meta_obj, 'end_line', None),
                        end_column=getattr(meta_obj, 'end_column', None),
                    )
                else:
                    token = Token(term_name, value, **meta)
            else:
                token = Token('PYOBJ', value, **meta)

            callback = callbacks.get(token.type)
            if callback:
                token = callback(token)

            yield token


def _offset_to_meta(text: str, start: int, end: int) -> dict:
    line = text.count('\n', 0, start) + 1
    line_start = text.rfind('\n', 0, start) + 1
    column = start - line_start + 1

    end_line = line + text.count('\n', start, end)
    if end_line == line:
        end_column = column + (end - start)
    else:
        last_line_start = text.rfind('\n', 0, end) + 1
        end_column = end - last_line_start + 1

    return {
        'start_pos': start,
        'line': line,
        'column': column,
        'end_pos': end,
        'end_line': end_line,
        'end_column': end_column,
    }


def _clear_token_meta(token: Token) -> None:
    token.start_pos = None
    token.line = None
    token.column = None
    token.end_pos = None
    token.end_line = None
    token.end_column = None


def splice_inserted_trees(node):
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
