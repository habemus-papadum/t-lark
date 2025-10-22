"""Template tokenization for Python 3.14 t-strings."""

from dataclasses import dataclass
from typing import Iterator, Optional, List, Tuple, Dict
from .lexer import Token
from .tree import Tree
from .utils import TextSlice


@dataclass
class TemplateContext:
    """Context for template tokenization."""
    lexer_conf: 'LexerConf'
    tree_terminal_map: Dict[str, str]  # label -> "TREE__LABEL"
    source_info: Optional['SourceInfo'] = None


@dataclass
class SourceInfo:
    """Source location info (provided by external tooling)."""
    filename: str
    text: str
    segment_spans: List[Tuple[int, int]]
    interpolation_spans: List[Tuple[int, int]]


def tokenize_template(template, ctx: TemplateContext) -> Iterator[Token]:
    """Tokenize a Template into token stream for Earley.

    Args:
        template: string.templatelib.Template instance
        ctx: TemplateContext with lexer and mappings

    Yields:
        Token instances
    """
    from .lexer import BasicLexer, PatternPlaceholder, PatternTree
    from .common import LexerConf

    # Filter out PYOBJ and TREE__ terminals for static text lexing
    # These terminals are only used for interpolated values
    filtered_terminals = [
        t for t in ctx.lexer_conf.terminals
        if not isinstance(t.pattern, (PatternPlaceholder, PatternTree))
    ]

    # Create a lexer config for static text (without template-specific terminals)
    lexer_conf = LexerConf(
        terminals=filtered_terminals,
        re_module=ctx.lexer_conf.re_module,
        ignore=ctx.lexer_conf.ignore,
        postlex=None,  # No postlex in template mode
        callbacks={},
        g_regex_flags=ctx.lexer_conf.g_regex_flags,
        skip_validation=True,  # Allow zero-width if needed
        use_bytes=ctx.lexer_conf.use_bytes
    )

    # Create lexer for static text
    lexer = BasicLexer(lexer_conf)

    has_source = ctx.source_info is not None

    strings = template.strings
    interpolations = template.interpolations

    # Process alternating strings and interpolations
    for i, static_str in enumerate(strings):
        # Lex static string if non-empty
        if static_str:
            from .lexer import LexerState

            if has_source:
                start, end = ctx.source_info.segment_spans[i]
                text_slice = TextSlice(ctx.source_info.text, start, end)
            else:
                text_slice = TextSlice(static_str, 0, len(static_str))

            # Create lexer state and lex
            lexer_state = LexerState(text_slice)
            for token in lexer.lex(lexer_state, None):
                yield token

        # Process interpolation if not at end
        if i < len(interpolations):
            interp = interpolations[i]
            value = interp.value

            # Calculate metadata
            if has_source:
                start, end = ctx.source_info.interpolation_spans[i]
                meta = _offset_to_meta(ctx.source_info.text, start, end)
            else:
                meta = {
                    'start_pos': None, 'line': None, 'column': None,
                    'end_pos': None, 'end_line': None, 'end_column': None
                }

            # Check if Tree
            if isinstance(value, Tree):
                label = value.data
                term_name = ctx.tree_terminal_map.get(label)

                if not term_name:
                    raise ValueError(
                        f"Cannot splice Tree('{label}'): "
                        f"grammar does not produce this label")

                # Use tree's meta if available
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
                # Python object
                token = Token("PYOBJ", value, **meta)
                yield token


def _offset_to_meta(text: str, start: int, end: int) -> dict:
    """Convert byte offset to line/column metadata."""
    lines_before = text[:start].count('\n')
    line = lines_before + 1

    line_start = text.rfind('\n', 0, start) + 1
    column = start - line_start + 1

    lines_in_span = text[start:end].count('\n')
    end_line = line + lines_in_span

    if lines_in_span == 0:
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
        'end_column': end_column
    }


def splice_inserted_trees(node):
    """Replace TREE__ tokens with underlying Trees.

    Called after parsing to substitute actual Tree objects.
    """
    if not isinstance(node, Tree):
        return

    new_children = []
    for child in node.children:
        if (isinstance(child, Token) and
            child.type.startswith("TREE__") and
            isinstance(child.value, Tree)):
            # Splice in the tree
            new_children.append(child.value)
        else:
            if isinstance(child, Tree):
                splice_inserted_trees(child)
            new_children.append(child)

    node.children = new_children
