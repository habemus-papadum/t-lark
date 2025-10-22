from typing import Any, Callable, Dict, Optional, Collection, Union, TYPE_CHECKING

from .exceptions import ConfigurationError, GrammarError, UnexpectedInput, assert_config
from .utils import get_regexp_width, Serialize, TextOrSlice, TextSlice
from .lexer import LexerThread, BasicLexer, ContextualLexer, Lexer, Token
from .parsers import earley, xearley, cyk
from .parsers.lalr_parser import LALR_Parser
from .tree import Tree
from .common import LexerConf, ParserConf, _ParserArgType, _LexerArgType
from .grammar import augment_grammar_for_template_mode

if TYPE_CHECKING:
    from .parsers.lalr_analysis import ParseTableBase


###{standalone

def _wrap_lexer(lexer_class):
    future_interface = getattr(lexer_class, '__future_interface__', 0)
    if future_interface == 2:
        return lexer_class
    elif future_interface == 1:
        class CustomLexerWrapper1(Lexer):
            def __init__(self, lexer_conf):
                self.lexer = lexer_class(lexer_conf)
            def lex(self, lexer_state, parser_state):
                if not lexer_state.text.is_complete_text():
                    raise TypeError("Interface=1 Custom Lexer don't support TextSlice")
                lexer_state.text = lexer_state.text
                return self.lexer.lex(lexer_state, parser_state)
        return CustomLexerWrapper1
    elif future_interface == 0:
        class CustomLexerWrapper0(Lexer):
            def __init__(self, lexer_conf):
                self.lexer = lexer_class(lexer_conf)

            def lex(self, lexer_state, parser_state):
                if not lexer_state.text.is_complete_text():
                    raise TypeError("Interface=0 Custom Lexer don't support TextSlice")
                return self.lexer.lex(lexer_state.text.text)
        return CustomLexerWrapper0
    else:
        raise ValueError(f"Unknown __future_interface__ value {future_interface}, integer 0-2 expected")


def _deserialize_parsing_frontend(data, memo, lexer_conf, callbacks, options):
    parser_conf = ParserConf.deserialize(data['parser_conf'], memo)
    cls = (options and options._plugins.get('LALR_Parser')) or LALR_Parser
    parser = cls.deserialize(data['parser'], memo, callbacks, options.debug)
    parser_conf.callbacks = callbacks
    return ParsingFrontend(lexer_conf, parser_conf, options, parser=parser)


_parser_creators: 'Dict[str, Callable[[LexerConf, Any, Any], Any]]' = {}


class ParsingFrontend(Serialize):
    __serialize_fields__ = 'lexer_conf', 'parser_conf', 'parser'

    lexer_conf: LexerConf
    parser_conf: ParserConf
    options: Any

    def __init__(self, lexer_conf: LexerConf, parser_conf: ParserConf, options, parser=None):
        self.parser_conf = parser_conf
        self.lexer_conf = lexer_conf
        self.options = options

        # Set-up parser
        if parser:  # From cache
            self.parser = parser
        else:
            create_parser = _parser_creators.get(parser_conf.parser_type)
            assert create_parser is not None, "{} is not supported in standalone mode".format(
                    parser_conf.parser_type
                )
            self.parser = create_parser(lexer_conf, parser_conf, options)

        # Set-up lexer
        lexer_type = lexer_conf.lexer_type
        self.skip_lexer = False
        if lexer_type in ('dynamic', 'dynamic_complete'):
            assert lexer_conf.postlex is None
            self.skip_lexer = True
            return

        if isinstance(lexer_type, type):
            assert issubclass(lexer_type, Lexer)
            self.lexer = _wrap_lexer(lexer_type)(lexer_conf)
        elif isinstance(lexer_type, str):
            create_lexer = {
                'basic': create_basic_lexer,
                'contextual': create_contextual_lexer,
            }[lexer_type]
            self.lexer = create_lexer(lexer_conf, self.parser, lexer_conf.postlex, options)
        else:
            raise TypeError("Bad value for lexer_type: {lexer_type}")

        if lexer_conf.postlex:
            self.lexer = PostLexConnector(self.lexer, lexer_conf.postlex)

    def _verify_start(self, start=None):
        if start is None:
            start_decls = self.parser_conf.start
            if len(start_decls) > 1:
                raise ConfigurationError("Lark initialized with more than 1 possible start rule. Must specify which start rule to parse", start_decls)
            start ,= start_decls
        elif start not in self.parser_conf.start:
            raise ConfigurationError("Unknown start rule %s. Must be one of %r" % (start, self.parser_conf.start))
        return start

    def _make_lexer_thread(self, text: Optional[TextOrSlice]) -> Union[TextOrSlice, LexerThread, None]:
        cls = (self.options and self.options._plugins.get('LexerThread')) or LexerThread
        return text if self.skip_lexer else cls(self.lexer, None) if text is None else cls.from_text(self.lexer, text)

    def parse(self, text: Optional[TextOrSlice], start=None, on_error=None):
        if self.lexer_conf.lexer_type in ("dynamic", "dynamic_complete"):
            if isinstance(text, TextSlice) and not text.is_complete_text():
                raise TypeError(f"Lexer {self.lexer_conf.lexer_type} does not support text slices.")

        chosen_start = self._verify_start(start)
        kw = {} if on_error is None else {'on_error': on_error}
        stream = self._make_lexer_thread(text)
        return self.parser.parse(stream, chosen_start, **kw)

    def parse_interactive(self, text: Optional[TextOrSlice]=None, start=None):
        # TODO BREAK - Change text from Optional[str] to text: str = ''.
        #   Would break behavior of exhaust_lexer(), which currently raises TypeError, and after the change would just return []
        chosen_start = self._verify_start(start)
        if self.parser_conf.parser_type != 'lalr':
            raise ConfigurationError("parse_interactive() currently only works with parser='lalr' ")
        stream = self._make_lexer_thread(text)
        return self.parser.parse_interactive(stream, chosen_start)


def _validate_frontend_args(parser, lexer) -> None:
    assert_config(parser, ('lalr', 'earley', 'cyk'))
    if not isinstance(lexer, type):     # not custom lexer?
        if lexer == 'template':
            if parser != 'earley':
                raise ConfigurationError("Template lexer requires parser='earley'")
            return
        expected = {
            'lalr': ('basic', 'contextual'),
            'earley': ('basic', 'dynamic', 'dynamic_complete'),
            'cyk': ('basic', ),
         }[parser]
        assert_config(lexer, expected, 'Parser %r does not support lexer %%r, expected one of %%s' % parser)


def _get_lexer_callbacks(transformer, terminals):
    result = {}
    for terminal in terminals:
        callback = getattr(transformer, terminal.name, None)
        if callback is not None:
            result[terminal.name] = callback
    return result

class PostLexConnector:
    def __init__(self, lexer, postlexer):
        self.lexer = lexer
        self.postlexer = postlexer

    def lex(self, lexer_state, parser_state):
        i = self.lexer.lex(lexer_state, parser_state)
        return self.postlexer.process(i)



def create_basic_lexer(lexer_conf, parser, postlex, options) -> BasicLexer:
    cls = (options and options._plugins.get('BasicLexer')) or BasicLexer
    return cls(lexer_conf)

def create_contextual_lexer(lexer_conf: LexerConf, parser, postlex, options) -> ContextualLexer:
    cls = (options and options._plugins.get('ContextualLexer')) or ContextualLexer
    parse_table: ParseTableBase[int] = parser._parse_table
    states: Dict[int, Collection[str]] = {idx:list(t.keys()) for idx, t in parse_table.states.items()}
    always_accept: Collection[str] = postlex.always_accept if postlex else ()
    return cls(lexer_conf, states, always_accept=always_accept)

def create_lalr_parser(lexer_conf: LexerConf, parser_conf: ParserConf, options=None) -> LALR_Parser:
    debug = options.debug if options else False
    strict = options.strict if options else False
    cls = (options and options._plugins.get('LALR_Parser')) or LALR_Parser
    return cls(parser_conf, debug=debug, strict=strict)

_parser_creators['lalr'] = create_lalr_parser

###}

class EarleyRegexpMatcher:
    def __init__(self, lexer_conf):
        self.regexps = {}
        for t in lexer_conf.terminals:
            regexp = t.pattern.to_regexp()
            try:
                width = get_regexp_width(regexp)[0]
            except ValueError:
                raise GrammarError("Bad regexp in token %s: %s" % (t.name, regexp))
            else:
                if width == 0:
                    raise GrammarError("Dynamic Earley doesn't allow zero-width regexps", t)
            if lexer_conf.use_bytes:
                regexp = regexp.encode('utf-8')

            self.regexps[t.name] = lexer_conf.re_module.compile(regexp, lexer_conf.g_regex_flags)

    def match(self, term, text, index=0):
        return self.regexps[term.name].match(text, index)


def create_earley_parser__dynamic(lexer_conf: LexerConf, parser_conf: ParserConf, **kw):
    if lexer_conf.callbacks:
        raise GrammarError("Earley's dynamic lexer doesn't support lexer_callbacks.")

    earley_matcher = EarleyRegexpMatcher(lexer_conf)
    return xearley.Parser(lexer_conf, parser_conf, earley_matcher.match, **kw)

def _match_earley_basic(term, token):
    return term.name == token.type

def create_earley_parser__basic(lexer_conf: LexerConf, parser_conf: ParserConf, **kw):
    return earley.Parser(lexer_conf, parser_conf, _match_earley_basic, **kw)

def create_earley_parser(lexer_conf: LexerConf, parser_conf: ParserConf, options) -> earley.Parser:
    resolve_ambiguity = options.ambiguity == 'resolve'
    debug = options.debug if options else False
    tree_class = options.tree_class or Tree if options.ambiguity != 'forest' else None

    extra = {}
    if lexer_conf.lexer_type == 'dynamic':
        f = create_earley_parser__dynamic
    elif lexer_conf.lexer_type == 'dynamic_complete':
        extra['complete_lex'] = True
        f = create_earley_parser__dynamic
    else:
        f = create_earley_parser__basic

    return f(lexer_conf, parser_conf, resolve_ambiguity=resolve_ambiguity,
             debug=debug, tree_class=tree_class, ordered_sets=options.ordered_sets, **extra)



class CYK_FrontEnd:
    def __init__(self, lexer_conf, parser_conf, options=None):
        self.parser = cyk.Parser(parser_conf.rules)

        self.callbacks = parser_conf.callbacks

    def parse(self, lexer_thread, start):
        tokens = list(lexer_thread.lex(None))
        tree = self.parser.parse(tokens, start)
        return self._transform(tree)

    def _transform(self, tree):
        subtrees = list(tree.iter_subtrees())
        for subtree in subtrees:
            subtree.children = [self._apply_callback(c) if isinstance(c, Tree) else c for c in subtree.children]

        return self._apply_callback(tree)

    def _apply_callback(self, tree):
        return self.callbacks[tree.rule](tree.children)


_parser_creators['earley'] = create_earley_parser
_parser_creators['cyk'] = CYK_FrontEnd


def _construct_parsing_frontend(
        parser_type: _ParserArgType,
        lexer_type: _LexerArgType,
        lexer_conf,
        parser_conf,
        options
):
    assert isinstance(lexer_conf, LexerConf)
    assert isinstance(parser_conf, ParserConf)
    if parser_type == 'earley' and lexer_type == 'template':
        parser_conf.parser_type = parser_type
        lexer_conf.lexer_type = lexer_type
        return TemplateEarleyFrontend(lexer_conf, parser_conf, options)
    parser_conf.parser_type = parser_type
    lexer_conf.lexer_type = lexer_type
    return ParsingFrontend(lexer_conf, parser_conf, options)


class _TemplateTokenSource:
    def __init__(self, iterator):
        self._iterator = iter(iterator)

    def lex(self, expects):
        return self._iterator


class TemplateEarleyFrontend:
    """Frontend for parsing Template objects using the Earley parser."""

    def __init__(self, lexer_conf: LexerConf, parser_conf: ParserConf, options) -> None:
        self.lexer_conf = lexer_conf
        self.parser_conf = parser_conf
        self.options = options

        grammar = getattr(parser_conf, 'grammar', None)
        original_rules = set(parser_conf.rules)
        self.tree_terminal_map = augment_grammar_for_template_mode(lexer_conf, parser_conf, grammar)
        injection_rules = [rule for rule in parser_conf.rules if rule not in original_rules]
        for rule in injection_rules:
            parser_conf.callbacks[rule] = self._first_child
        self._tree_terminal_inverse = {v: k for k, v in self.tree_terminal_map.items()}

        self.pyobj_types = dict(getattr(options, 'pyobj_types', {}) or {})
        self._typed_expected = dict(getattr(grammar, 'pyobj_type_names', {}) or {})

        self._nonterminal_names = {rule.origin.name for rule in parser_conf.rules}
        filtered_start = [name for name in parser_conf.start if name in self._nonterminal_names]
        if not filtered_start:
            missing = [name for name in parser_conf.start if name not in self._nonterminal_names]
            if not missing:
                missing = parser_conf.start or ['start']
            raise ConfigurationError(
                "Template parser requires at least one valid start rule. Missing definitions for: %s"
                % ', '.join(missing)
            )
        if filtered_start != parser_conf.start:
            parser_conf.start = filtered_start
            if hasattr(options, 'start'):
                options.start = filtered_start

        self._available_starts = set(filtered_start) | self._nonterminal_names

        lexer_conf.lexer_type = 'template'

        self.basic_lexer = BasicLexer(lexer_conf)
        self.postlexer = lexer_conf.postlex
        if self.postlexer:
            self._plain_lexer = PostLexConnector(self.basic_lexer, self.postlexer)
        else:
            self._plain_lexer = self.basic_lexer

        term_matcher = self._create_term_matcher()

        from .parsers.earley import Parser as EarleyParser

        resolve_ambiguity = options.ambiguity == 'resolve'
        debug = options.debug if options else False
        tree_class = options.tree_class or Tree if options.ambiguity != 'forest' else None
        ordered_sets = getattr(options, 'ordered_sets', True)

        self.parser = EarleyParser(
            lexer_conf,
            parser_conf,
            term_matcher,
            resolve_ambiguity=resolve_ambiguity,
            debug=debug,
            tree_class=tree_class,
            ordered_sets=ordered_sets,
        )

    @staticmethod
    def _first_child(children):
        return children[0] if children else None

    def _create_term_matcher(self):
        typed_expected = self._typed_expected
        pyobj_types = self.pyobj_types

        def match(term, token):
            if not isinstance(token, Token):
                return False

            name = term.name

            if name == 'PYOBJ':
                return token.type == 'PYOBJ'

            if name.startswith('PYOBJ__'):
                if token.type not in (name, 'PYOBJ'):
                    return False
                expected_name = typed_expected.get(name)
                if expected_name and pyobj_types:
                    expected_type = pyobj_types.get(expected_name)
                    if expected_type and not isinstance(token.value, expected_type):
                        if isinstance(expected_type, tuple):
                            type_names = ', '.join(getattr(t, '__name__', repr(t)) for t in expected_type)
                        else:
                            type_names = getattr(expected_type, '__name__', repr(expected_type))
                        actual = type(token.value).__name__
                        raise TypeError(
                            f"Expected {type_names} for PYOBJ[{expected_name}], got {actual}"
                        )
                return True

            if name.startswith('TREE__'):
                if token.type != name:
                    return False
                return isinstance(token.value, Tree)

            return token.type == name

        return match

    def _verify_start(self, start=None):
        if start is None:
            start_decls = self.parser_conf.start
            if len(start_decls) > 1:
                raise ConfigurationError(
                    "Lark initialized with more than 1 possible start rule. Must specify which start rule to parse",
                    start_decls,
                )
            start ,= start_decls
        elif start not in self._available_starts:
            raise ConfigurationError("Unknown start rule %s. Must be one of %r" % (start, self.parser_conf.start))
        return start

    def parse(self, input_data, start=None, on_error=None):
        from .template_mode import TemplateContext, tokenize_template, splice_inserted_trees, _offset_to_meta

        chosen_start = self._verify_start(start)
        kw = {} if on_error is None else {'on_error': on_error}

        is_template = (
            input_data is not None
            and not isinstance(input_data, (str, bytes))
            and hasattr(input_data, 'strings')
            and hasattr(input_data, 'interpolations')
        )

        ctx = None
        if is_template:
            ctx = TemplateContext(
                lexer_conf=self.lexer_conf,
                lexer=self.basic_lexer,
                tree_terminal_map=self.tree_terminal_map,
                source_info=getattr(input_data, 'source_info', None),
            )
            token_iter = tokenize_template(input_data, ctx)
            if self.postlexer:
                token_iter = self.postlexer.process(token_iter)
            token_source = _TemplateTokenSource(token_iter)
        else:
            token_source = self._make_plain_thread(input_data)

        try:
            tree = self.parser.parse(token_source, chosen_start, **kw)
        except UnexpectedInput as exc:
            self._enhance_error(exc, input_data)
            raise

        if isinstance(tree, Token) and tree.type.startswith('TREE__') and isinstance(tree.value, Tree):
            tree = tree.value

        if isinstance(tree, Tree):
            splice_inserted_trees(tree)
            if (
                ctx is not None
                and ctx.source_info is not None
                and getattr(tree, 'meta', None) is not None
                and ctx.overall_start is not None
                and ctx.overall_end is not None
            ):
                full_meta = _offset_to_meta(
                    ctx.source_info.text,
                    ctx.overall_start,
                    ctx.overall_end,
                )
                tree.meta.start_pos = full_meta['start_pos']
                tree.meta.line = full_meta['line']
                tree.meta.column = full_meta['column']
                tree.meta.end_pos = full_meta['end_pos']
                tree.meta.end_line = full_meta['end_line']
                tree.meta.end_column = full_meta['end_column']

        return tree

    def _make_plain_thread(self, text):
        if text is None:
            return LexerThread(self._plain_lexer, None)
        return LexerThread.from_text(self._plain_lexer, TextSlice.cast_from(text))

    def _enhance_error(self, exc, input_data):
        from string.templatelib import Template

        if not isinstance(input_data, Template):
            return

        token = getattr(exc, 'token', None)
        if not isinstance(token, Token):
            return

        if token.type == 'PYOBJ':
            message = exc.args[0] if exc.args else ''
            exc.args = (
                f"Interpolated Python object at {getattr(exc, 'line', '?')}:{getattr(exc, 'column', '?')} not allowed here. "
                f"{message}",
            )
        elif token.type.startswith('TREE__'):
            label = self._tree_terminal_inverse.get(token.type, token.type[len('TREE__'):].lower())
            message = exc.args[0] if exc.args else ''
            exc.args = (
                f"Interpolated Tree('{label}') at {getattr(exc, 'line', '?')}:{getattr(exc, 'column', '?')} not valid in this context. "
                f"{message}",
            )
