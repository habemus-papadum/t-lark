from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Optional, Tuple

from .common import LexerConf
from .lexer import BasicLexer, LexerState, Token
from .tree import Tree
from .utils import TextSlice


@dataclass
class TemplateContext:
    lexer_conf: LexerConf
    tree_terminal_map: Dict[str, str]
    typed_placeholders: Dict[str, str]
    pyobj_types: Dict[str, type]
    source_info: Optional[Any] = None


def tokenize_template(template, ctx: TemplateContext) -> Iterator[Token]:
    """Tokenize a Template object into a stream of tokens."""

    lexer = BasicLexer(ctx.lexer_conf)
    strings: Tuple[str, ...] = getattr(template, 'strings', ())
    interpolations = getattr(template, 'interpolations', ())
    source_info = ctx.source_info
    has_source = bool(source_info and getattr(source_info, 'text', None) is not None)

    for index, static_text in enumerate(strings):
        if static_text:
            if has_source:
                start, end = source_info.segment_spans[index]
                text_slice = TextSlice(source_info.text, start, end)
                state = LexerState(text_slice)
                yield from lexer.lex(state, None)
            else:
                text_slice = TextSlice(static_text, 0, len(static_text))
                state = LexerState(text_slice)
                for token in lexer.lex(state, None):
                    token.start_pos = token.line = token.column = None
                    token.end_pos = token.end_line = token.end_column = None
                    yield token

        if index < len(interpolations):
            interpolation = interpolations[index]
            value = interpolation.value

            if has_source:
                start, end = source_info.interpolation_spans[index]
                meta = _offset_to_meta(source_info.text, start, end)
            else:
                meta = _null_meta()

            if isinstance(value, Tree):
                label = value.data
                term_name = ctx.tree_terminal_map.get(label)
                if term_name is None:
                    raise ValueError(
                        f"Cannot splice Tree('{label}'): grammar does not produce this label"
                    )

                if hasattr(value, 'meta') and value.meta:
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
                yield token
            else:
                token = Token('PYOBJ', value, **meta)
                yield token


def _null_meta() -> Dict[str, Optional[int]]:
    return {
        'start_pos': None,
        'line': None,
        'column': None,
        'end_pos': None,
        'end_line': None,
        'end_column': None,
    }


def _offset_to_meta(text: str, start: int, end: int) -> Dict[str, Optional[int]]:
    line = text.count('\n', 0, start) + 1
    line_start = text.rfind('\n', 0, start)
    if line_start == -1:
        line_start = 0
    else:
        line_start += 1

    column = start - line_start + 1
    lines_in_span = text.count('\n', start, end)
    end_line = line + lines_in_span

    if lines_in_span == 0:
        end_column = column + (end - start)
    else:
        last_newline = text.rfind('\n', start, end)
        end_column = end - last_newline

    return {
        'start_pos': start,
        'line': line,
        'column': column,
        'end_pos': end,
        'end_line': end_line,
        'end_column': end_column,
    }


def splice_inserted_trees(node):
    """Replace TREE__ tokens with underlying Tree values in-place."""

    if not isinstance(node, Tree):
        return

    new_children: List[Any] = []
    for child in node.children:
        if isinstance(child, Token) and child.type.startswith('TREE__') and isinstance(child.value, Tree):
            new_children.append(child.value)
        else:
            if isinstance(child, Tree):
                splice_inserted_trees(child)
            new_children.append(child)

    node.children = new_children
