from typing import Any, Callable, Dict, Optional, Collection, Union, TYPE_CHECKING

from .exceptions import ConfigurationError, GrammarError, UnexpectedInput, assert_config
from .utils import get_regexp_width, Serialize, TextOrSlice, TextSlice
from .lexer import LexerThread, BasicLexer, ContextualLexer, Lexer, Token
from .parsers import earley, xearley, cyk
from .parsers.lalr_parser import LALR_Parser
from .tree import Tree
from .common import LexerConf, ParserConf, _ParserArgType, _LexerArgType

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


class _TemplateTokenStream:
    def __init__(self, tokens: Collection[Token]):
        self._tokens = list(tokens)
        self._consumed = False

    def lex(self, expects):
        if self._consumed:
            return iter(())
        self._consumed = True

        def _iterator():
            for token in self._tokens:
                yield token

        return _iterator()


class TemplateEarleyFrontend:
    """Frontend for parsing Python template literals with Earley."""

    def __init__(self, lexer_conf: LexerConf, parser_conf: ParserConf, options):
        self.lexer_conf = lexer_conf
        self.parser_conf = parser_conf
        self.options = options

        if not isinstance(self.lexer_conf.terminals, list):
            self.lexer_conf.terminals = list(self.lexer_conf.terminals)

        from .grammar import augment_grammar_for_template_mode

        self.tree_terminals = augment_grammar_for_template_mode(self.lexer_conf.terminals, self.parser_conf.rules)
        self.lexer_conf.terminals_by_name = {t.name: t for t in self.lexer_conf.terminals}
        self.tree_terminal_names = {label: terminal.name for label, terminal in self.tree_terminals.items()}

        self.uses_pyobj_placeholders = getattr(self.lexer_conf, 'uses_pyobj_placeholders', False)
        self.pyobj_types = dict(getattr(options, 'pyobj_types', {}) or {})

        required_types = {
            term.pattern.expected_type
            for term in self.lexer_conf.terminals
            if hasattr(term.pattern, 'expected_type') and term.pattern.expected_type
        }
        missing_types = sorted(t for t in required_types if t not in self.pyobj_types)
        if missing_types:
            raise ConfigurationError(
                "Missing pyobj_types entries for: %s" % ', '.join(missing_types)
            )

        if options.ambiguity != 'forest':
            from .parse_tree_builder import ParseTreeBuilder

            tree_class_for_callbacks = options.tree_class or Tree
            propagate_positions = options.propagate_positions
            is_explicit_ambiguity = options.parser != 'lalr' and options.ambiguity == 'explicit'
            maybe_placeholders = options.maybe_placeholders

            builder = ParseTreeBuilder(
                self.parser_conf.rules,
                tree_class_for_callbacks,
                propagate_positions,
                is_explicit_ambiguity,
                maybe_placeholders,
            )
            rule_callbacks = builder.create_callback(options.transformer)
            self.parser_conf.callbacks.update(rule_callbacks)

        term_matcher = self._create_term_matcher()

        from .parsers.earley import Parser as EarleyParser

        resolve_ambiguity = options.ambiguity == 'resolve'
        debug = options.debug if options else False
        tree_class = options.tree_class or Tree if options.ambiguity != 'forest' else None
        ordered_sets = getattr(options, 'ordered_sets', True)

        self.parser = EarleyParser(
            self.lexer_conf,
            self.parser_conf,
            term_matcher,
            resolve_ambiguity=resolve_ambiguity,
            debug=debug,
            tree_class=tree_class,
            ordered_sets=ordered_sets,
        )

        self._basic_lexer = BasicLexer(self.lexer_conf)

    def _create_term_matcher(self):
        terminals_by_name = self.lexer_conf.terminals_by_name
        pyobj_types = self.pyobj_types

        def match(term, token):
            if not isinstance(token, Token):
                return False

            term_name = term.name

            if term_name == 'PYOBJ':
                return token.type == 'PYOBJ'

            if term_name.startswith('PYOBJ__'):
                if token.type not in ('PYOBJ', term_name):
                    return False

                term_def = terminals_by_name.get(term_name)
                expected_name = getattr(getattr(term_def, 'pattern', None), 'expected_type', None)
                if expected_name:
                    expected_type = pyobj_types.get(expected_name)
                    if expected_type is None:
                        raise ConfigurationError(
                            f"No pyobj_types entry provided for PYOBJ[{expected_name}]"
                        )
                    if not isinstance(token.value, expected_type):
                        raise TypeError(
                            f"Expected {expected_type.__name__} for PYOBJ[{expected_name}], got "
                            f"{type(token.value).__name__}"
                        )
                return True

            if term_name.startswith('TREE__'):
                if token.type != term_name:
                    return False
                return isinstance(token.value, Tree)

            return token.type == term_name

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
        elif start not in self.parser_conf.start:
            raise ConfigurationError(
                "Unknown start rule %s. Must be one of %r" % (start, self.parser_conf.start)
            )
        return start

    def parse(self, data: Optional[TextOrSlice], start=None, on_error=None):
        from string.templatelib import Template

        chosen_start = self._verify_start(start)
        kw = {} if on_error is None else {'on_error': on_error}

        if isinstance(data, Template):
            lexer = self._tokenize_template(data)
        else:
            lexer = self._lex_plain_string(data)

        try:
            result = self.parser.parse(lexer, chosen_start, **kw)
        except UnexpectedInput as exc:
            self._enhance_error(exc, data)
            raise

        if isinstance(result, Token) and result.type.startswith('TREE__') and isinstance(result.value, Tree):
            from .template_mode import splice_inserted_trees

            splice_inserted_trees(result.value)
            return result.value

        if isinstance(result, Tree):
            from .template_mode import splice_inserted_trees

            splice_inserted_trees(result)

        return result

    def parse_interactive(self, *args, **kwargs):
        raise NotImplementedError("Template mode does not support parse_interactive")

    def _lex_plain_string(self, text: Optional[TextOrSlice]):
        if text is None:
            return LexerThread(self._basic_lexer, None)
        text_slice = TextSlice.cast_from(text)
        return LexerThread.from_text(self._basic_lexer, text_slice)

    def _tokenize_template(self, template):
        from .template_mode import TemplateContext, tokenize_template

        ctx = TemplateContext(
            lexer_conf=self.lexer_conf,
            tree_terminal_map=self.tree_terminal_names,
            source_info=getattr(template, 'source_info', None),
        )
        tokens = tokenize_template(template, ctx)
        return _TemplateTokenStream(tokens)

    def _enhance_error(self, exc: UnexpectedInput, data):
        from string.templatelib import Template

        if not isinstance(data, Template):
            return

        token = getattr(exc, 'token', None)
        if token is None:
            return

        message = None
        if token.type == 'PYOBJ':
            message = (
                f"Interpolated Python object at {exc.line}:{exc.column} not allowed here"
            )
        elif token.type.startswith('PYOBJ__'):
            type_name = token.type[len('PYOBJ__'):].lower()
            message = (
                f"Interpolated Python object for PYOBJ[{type_name}] at {exc.line}:{exc.column}"
            )
        elif token.type.startswith('TREE__'):
            label = token.type[len('TREE__'):].lower()
            message = (
                f"Interpolated Tree('{label}') at {exc.line}:{exc.column} not valid in this context"
            )

        if message:
            original = exc.args[0] if exc.args else ''
            exc.args = (f"{message}. {original}".strip(),)


def _validate_frontend_args(parser, lexer) -> None:
    assert_config(parser, ('lalr', 'earley', 'cyk'))
    if not isinstance(lexer, type):     # not custom lexer?
        if lexer == 'template':
            assert_config(
                parser,
                ('earley',),
                'Template lexer requires parser="earley", got %r (expected one of %s)'
            )
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
    parser_conf.parser_type = parser_type
    lexer_conf.lexer_type = lexer_type
    if lexer_type == 'template':
        return TemplateEarleyFrontend(lexer_conf, parser_conf, options)
    return ParsingFrontend(lexer_conf, parser_conf, options)
