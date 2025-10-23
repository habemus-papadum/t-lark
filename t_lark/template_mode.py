"""Utilities for Lark's template parsing mode."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, Iterable, Iterator, Mapping, Optional, Sequence, Set, Tuple, cast

from .common import LexerConf, ParserConf
from .grammar import NonTerminal, Rule, RuleOptions, Terminal
from .lexer import BasicLexer, LexerThread, PatternPlaceholder, PatternTree, TerminalDef, Token
from .tree import Tree
from .utils import TextSlice


_SANITIZE_RE = re.compile(r"[^0-9A-Z_]")


def basic_lexer_for_static(lexer_conf: LexerConf) -> BasicLexer:
    """Create a BasicLexer configured to ignore template-only terminals."""

    static_terminals = [
        term for term in lexer_conf.terminals
        if not isinstance(term.pattern, (PatternTree, PatternPlaceholder))
    ]

    filtered_conf = LexerConf(
        static_terminals,
        lexer_conf.re_module,
        lexer_conf.ignore,
        lexer_conf.postlex,
        lexer_conf.callbacks,
        lexer_conf.g_regex_flags,
        skip_validation=True,
        use_bytes=lexer_conf.use_bytes,
        strict=lexer_conf.strict,
    )
    filtered_conf.terminals_by_name = {t.name: t for t in static_terminals}
    filtered_conf.lexer_type = lexer_conf.lexer_type
    return BasicLexer(filtered_conf)


def _tree_injection_callback(children):
    token ,= children
    return token.value


@dataclass
class SourceInfo:
    """Represents source metadata for template segments/interpolations."""

    filename: str
    text: str
    segment_spans: Sequence[Tuple[int, int]]
    interpolation_spans: Sequence[Tuple[int, int]]


@dataclass
class TemplateContext:
    """Context required to tokenize a template."""

    lexer_conf: LexerConf
    tree_terminal_map: Mapping[str, str]
    typed_terminals: Mapping[str, str]
    pyobj_types: Mapping[str, type | tuple[type, ...]]
    source_info: Optional[SourceInfo] = None


class _TemplateLexer:
    """Adapter that exposes `lex` for Earley parser consumption."""

    def __init__(self, template, ctx: TemplateContext):
        self._template = template
        self._ctx = ctx
        self._used = False

    def lex(self, parser_state):
        if self._used:
            return iter(())
        self._used = True
        token_iter: Iterable[Token] = _iterate_template_tokens(self._template, self._ctx, parser_state)
        postlex = self._ctx.lexer_conf.postlex
        if postlex is not None:
            token_iter = postlex.process(iter(token_iter))
        return iter(token_iter)


def tokenize_template(template, ctx: TemplateContext) -> _TemplateLexer:
    """Create a lexer adapter for a template object."""

    return _TemplateLexer(template, ctx)


def _iterate_template_tokens(template, ctx: TemplateContext, parser_state) -> Iterator[Token]:
    basic_lexer = basic_lexer_for_static(ctx.lexer_conf)
    strings = template.strings
    interpolations = template.interpolations

    for index, static_str in enumerate(strings):
        if static_str:
            text_slice = _segment_text_slice(static_str, index, ctx)
            thread = LexerThread.from_text(basic_lexer, text_slice)
            yield from thread.lex(parser_state)

        if index < len(interpolations):
            interp = interpolations[index]
            yield _build_interpolation_token(interp.value, ctx, index)


def _segment_text_slice(static_str: str, index: int, ctx: TemplateContext) -> TextSlice[str]:
    info = ctx.source_info
    if info is None:
        return TextSlice(static_str, 0, len(static_str))

    start, end = info.segment_spans[index]
    return TextSlice(info.text, start, end)


def _build_interpolation_token(value, ctx: TemplateContext, index: int) -> Token:
    info = ctx.source_info
    if info is None:
        meta: Dict[str, Optional[int]] = {
            "start_pos": None,
            "end_pos": None,
            "line": None,
            "column": None,
            "end_line": None,
            "end_column": None,
        }
    else:
        start, end = info.interpolation_spans[index]
        meta = cast(Dict[str, Optional[int]], _offset_to_meta(info.text, start, end))

    if isinstance(value, Tree):
        term_name = ctx.tree_terminal_map.get(value.data)
        if term_name is None:
            raise ValueError(f"Cannot splice Tree('{value.data}'): grammar does not produce this label")

        token = _tree_token(term_name, value, meta)
        return token

    terminal_name, _ = _resolve_pyobj_terminal(value, ctx)
    token_type = terminal_name or "PYOBJ"
    return Token(token_type, value, **meta)


def _tree_token(term_name: str, tree_value: Tree, meta: Dict[str, Optional[int]]) -> Token:
    if hasattr(tree_value, "meta") and tree_value.meta:
        m = tree_value.meta
        return Token(
            term_name,
            tree_value,
            start_pos=getattr(m, "start_pos", meta["start_pos"]),
            line=getattr(m, "line", meta["line"]),
            column=getattr(m, "column", meta["column"]),
            end_pos=getattr(m, "end_pos", meta["end_pos"]),
            end_line=getattr(m, "end_line", meta["end_line"]),
            end_column=getattr(m, "end_column", meta["end_column"]),
        )

    return Token(term_name, tree_value, **meta)


def _resolve_pyobj_terminal(value, ctx: TemplateContext) -> Tuple[Optional[str], Optional[str]]:
    matches: list[Tuple[int, str, str]] = []
    for type_name, expected_type in ctx.pyobj_types.items():
        terminal = ctx.typed_terminals.get(type_name)
        if terminal is None:
            continue

        candidates: Sequence[type]
        if isinstance(expected_type, tuple):
            candidates = expected_type
        else:
            candidates = (expected_type,)

        for candidate in candidates:
            if isinstance(value, candidate):
                distance = _inheritance_distance(candidate, type(value))
                matches.append((distance, type_name, terminal))
                break

    if not matches:
        return None, None

    matches.sort(key=lambda item: (item[0], item[1]))
    _, type_name, terminal_name = matches[0]
    return terminal_name, type_name


def _inheritance_distance(expected: type, actual: type) -> int:
    try:
        return actual.mro().index(expected)
    except ValueError:
        return len(actual.mro())


def _offset_to_meta(text: str, start: int, end: int) -> Dict[str, int]:
    line = text.count("\n", 0, start) + 1
    line_start = text.rfind("\n", 0, start) + 1
    column = start - line_start + 1

    lines_in_span = text.count("\n", start, end)
    end_line = line + lines_in_span

    if lines_in_span == 0:
        end_column = column + (end - start)
    else:
        last_line_start = text.rfind("\n", 0, end) + 1
        end_column = end - last_line_start + 1

    return {
        "start_pos": start,
        "end_pos": end,
        "line": line,
        "column": column,
        "end_line": end_line,
        "end_column": end_column,
    }


def splice_inserted_trees(node):
    """Replace TREE__ tokens with the Tree objects produced during parsing."""

    if not isinstance(node, Tree):
        return

    new_children = []
    for child in node.children:
        if isinstance(child, Token) and child.type.startswith("TREE__") and isinstance(child.value, Tree):
            splice_inserted_trees(child.value)
            new_children.append(child.value)
        else:
            if isinstance(child, Tree):
                splice_inserted_trees(child)
            new_children.append(child)

    node.children = new_children


def _make_tree_terminal_name(label: str) -> str:
    upper = label.upper()
    safe = _SANITIZE_RE.sub('_', upper)
    return f"TREE__{safe}"


def augment_grammar_for_template_mode(lexer_conf: LexerConf, parser_conf: ParserConf) -> Mapping[str, str]:
    """Augment grammar with tree injection rules for template parsing."""

    existing = getattr(parser_conf, "template_tree_terminals", None)
    if existing is not None:
        return existing

    label_map: Dict[str, str] = {}
    labels_per_nonterminal: Dict[NonTerminal, Set[str]] = {}

    for rule in parser_conf.rules:
        labels = labels_per_nonterminal.setdefault(rule.origin, set())
        label = rule.alias if rule.alias else rule.origin.name
        labels.add(str(label))

    terminals = list(lexer_conf.terminals)
    terminals_by_name = dict(lexer_conf.terminals_by_name)

    for labels in labels_per_nonterminal.values():
        for label in labels:
            term_name = _make_tree_terminal_name(label)
            label_map[label] = term_name
            if term_name in terminals_by_name:
                continue
            terminals.append(TerminalDef(term_name, PatternTree(label)))
            terminals_by_name[term_name] = terminals[-1]

    lexer_conf.terminals = terminals
    lexer_conf.terminals_by_name = terminals_by_name

    existing_expansions: Set[Tuple[NonTerminal, Tuple[str, ...]]] = set()
    max_order: Dict[NonTerminal, int] = {}
    for rule in parser_conf.rules:
        expansion = tuple(symbol.name for symbol in rule.expansion)
        existing_expansions.add((rule.origin, expansion))
        max_order[rule.origin] = max(max_order.get(rule.origin, -1), rule.order)

    new_rules: list[Rule] = []

    for origin, labels in labels_per_nonterminal.items():
        order = max_order.get(origin, -1) + 1
        for label in labels:
            term_name = label_map[label]
            key = (origin, (term_name,))
            if key in existing_expansions:
                continue
            symbol = Terminal(term_name)
            new_rules.append(Rule(origin, [symbol], order, alias=None, options=RuleOptions(expand1=True)))
            existing_expansions.add(key)
            order += 1

    if new_rules:
        parser_conf.rules.extend(new_rules)
        for rule in new_rules:
            parser_conf.callbacks[rule] = _tree_injection_callback

    parser_conf.template_tree_terminals = label_map
    return label_map
