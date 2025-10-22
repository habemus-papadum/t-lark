"""Template tokenization support for Python template literals."""

from dataclasses import dataclass
from typing import Dict, Iterator, List, Optional, Tuple

from .common import LexerConf
from .lexer import BasicLexer, LexerState, Token
from .tree import Tree
from .utils import TextSlice


@dataclass
class SourceInfo:
    """Source information for templates produced by external tooling."""

    filename: str
    text: str
    segment_spans: List[Tuple[int, int]]
    interpolation_spans: List[Tuple[int, int]]


@dataclass
class TemplateContext:
    lexer_conf: LexerConf
    tree_terminal_map: Dict[str, str]
    source_info: Optional[SourceInfo] = None


def tokenize_template(template, ctx: TemplateContext) -> Iterator[Token]:
    """Tokenize a Python template object into a stream of tokens."""

    lexer = BasicLexer(ctx.lexer_conf)
    has_source = ctx.source_info is not None

    strings = template.strings
    interpolations = template.interpolations

    for index, static in enumerate(strings):
        if static:
            if has_source:
                start, end = ctx.source_info.segment_spans[index]
                text_slice = TextSlice(ctx.source_info.text, start, end)
            else:
                text_slice = TextSlice(static, 0, len(static))

            state = LexerState(text_slice)
            for token in lexer.lex(state, None):
                if not has_source:
                    token.line = token.column = None
                    token.end_line = token.end_column = None
                    token.start_pos = token.end_pos = None
                yield token

        if index >= len(interpolations):
            continue

        interp = interpolations[index]
        value = interp.value

        if has_source:
            start, end = ctx.source_info.interpolation_spans[index]
            meta = _offset_to_meta(ctx.source_info.text, start, end)
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
            if not term_name:
                raise ValueError(
                    f"Cannot splice Tree('{label}'): grammar does not produce this label"
                )

            if hasattr(value, 'meta') and value.meta:
                m = value.meta
                token = Token(
                    term_name,
                    value,
                    start_pos=getattr(m, 'start_pos', None),
                    line=getattr(m, 'line', None),
                    column=getattr(m, 'column', None),
                    end_pos=getattr(m, 'end_pos', None),
                    end_line=getattr(m, 'end_line', None),
                    end_column=getattr(m, 'end_column', None),
                )
            else:
                token = Token(term_name, value, **meta)
            yield token
        else:
            yield Token('PYOBJ', value, **meta)


def _offset_to_meta(text: str, start: int, end: int) -> Dict[str, Optional[int]]:
    line = text.count('\n', 0, start) + 1
    line_start = text.rfind('\n', 0, start)
    if line_start == -1:
        column = start + 1
    else:
        column = start - line_start

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
    """Replace TREE__ tokens in the parse tree with their underlying Tree values."""

    if not isinstance(node, Tree):
        return

    new_children = []
    for child in node.children:
        if (
            isinstance(child, Token)
            and child.type.startswith('TREE__')
            and isinstance(child.value, Tree)
        ):
            splice_inserted_trees(child.value)
            new_children.append(child.value)
        else:
            if isinstance(child, Tree):
                splice_inserted_trees(child)
            new_children.append(child)

    node.children = new_children
