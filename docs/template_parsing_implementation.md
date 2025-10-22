# Template Mode Implementation Guide

This document provides detailed implementation steps for adding template parsing mode to Lark. Read [template_parsing.md](./template_parsing.md) first for the design overview and motivation.

## Table of Contents

1. [Files to Modify](#files-to-modify)
2. [Step 1: Pattern Classes](#step-1-pattern-classes)
3. [Step 2: Grammar Loader](#step-2-grammar-loader)
4. [Step 3: Grammar Augmentation](#step-3-grammar-augmentation)
5. [Step 4: Template Frontend](#step-4-template-frontend)
6. [Step 5: Template Tokenizer](#step-5-template-tokenizer)
7. [Step 6: Lark Constructor API](#step-6-lark-constructor-api)
8. [Testing Checklist](#testing-checklist)

## Files to Modify

### Core Files

- `lark/lexer.py` - Add `PatternPlaceholder` and `PatternTree` classes
- `lark/load_grammar.py` - Parse `%import template (PYOBJ)` syntax
- `lark/grammar.py` - Grammar augmentation for tree injection
- `lark/parser_frontends.py` - Add `TemplateEarleyFrontend`
- `lark/lark.py` - Add `pyobj_types` parameter

### New Files

- `lark/template_mode.py` - Template tokenization logic

### Test Files

- `tests/test_template_mode.py` - Comprehensive test suite

## Existing Lark Concepts (Context)

Before implementing, understand these existing Lark components:

### Pattern Class

Abstract base for terminal patterns in `lark/lexer.py`:

```python
class Pattern(Serialize, ABC):
    value: str
    flags: Collection[str]

    @abstractmethod
    def to_regexp(self) -> str:
        """Return regex string for matching."""

    @property
    @abstractmethod
    def min_width(self) -> int:
        """Minimum match length."""

    @property
    @abstractmethod
    def max_width(self) -> int:
        """Maximum match length."""
```

Subclasses: `PatternStr` (literals), `PatternRE` (regexes)

### TerminalDef

Defines a terminal in `lark/lexer.py`:

```python
class TerminalDef(Serialize):
    name: str          # e.g., "NUMBER"
    pattern: Pattern   # How to match it
    priority: int      # Resolution priority
```

### Token

Tokens produced by lexer in `lark/lexer.py`:

```python
Token(
    type='NUMBER',      # Terminal name
    value='42',         # Matched text (or any Python object for our use)
    start_pos=0,        # Position in input
    line=1,             # Line number
    column=1,           # Column number
    end_line=1,
    end_column=3,
    end_pos=2
)
```

### TextSlice

View into text without copying (`lark/utils.py`):

```python
TextSlice(text='Hello, World!', start=7, end=12)
# Represents "World" without creating a new string
```

Used for preserving source locations when lexing substrings.

### RuleOptions

Configuration for grammar rules:

```python
RuleOptions(
    expand_single_child=True,  # aka expand1: collapse single-child rules
    priority=None,
    ...
)
```

### Earley Parser API

From `lark/parsers/earley.py`:

```python
Parser(
    lexer_conf: LexerConf,
    parser_conf: ParserConf,
    term_matcher: Callable[[Term, Token], bool],  # Custom terminal matching
    resolve_ambiguity: bool = True,
    debug: bool = False,
    tree_class: Type = Tree,
    ordered_sets: bool = True
)
```

The `term_matcher` function decides if a token matches a terminal.

## Step 1: Pattern Classes

**File**: `lark/lexer.py`

Add two new Pattern subclasses after existing pattern definitions:

```python
class PatternPlaceholder(Pattern):
    """Pattern for Python object placeholders (PYOBJ terminals).

    Never matches text via regex. Only matched by custom term matcher in template mode.
    """

    __serialize_fields__ = 'value', 'flags', 'expected_type'
    type: ClassVar[str] = "placeholder"

    def __init__(self, expected_type: Optional[str] = None):
        """
        Args:
            expected_type: Type name for PYOBJ[typename] (e.g., "image").
                          None for untyped PYOBJ.
        """
        super().__init__("<pyobj>", flags=())
        self.expected_type = expected_type

    def to_regexp(self) -> str:
        # Return impossible-to-match pattern
        # This ensures no text accidentally matches PYOBJ
        return r'(?!.*)'

    @property
    def min_width(self) -> int:
        return 0

    @property
    def max_width(self) -> int:
        return 0


class PatternTree(Pattern):
    """Pattern for spliced Tree objects (TREE__ terminals).

    Never matches text via regex. Only matched by custom term matcher in template mode.
    """

    __serialize_fields__ = 'value', 'flags', 'label'
    type: ClassVar[str] = "tree"

    def __init__(self, label: str):
        """
        Args:
            label: Expected tree label (e.g., "add" for Tree('add', ...))
        """
        super().__init__(f"<tree:{label}>", flags=())
        self.label = label

    def to_regexp(self) -> str:
        return r'(?!.*)'  # Impossible pattern

    @property
    def min_width(self) -> int:
        return 0

    @property
    def max_width(self) -> int:
        return 0
```

**Update TerminalDef serialization**:

```python
class TerminalDef(Serialize):
    __serialize_namespace__ = PatternStr, PatternRE, PatternPlaceholder, PatternTree
    # ... rest of class unchanged
```

## Step 2: Grammar Loader

**File**: `lark/load_grammar.py`

### Recognize `%import template (PYOBJ)`

Locate the import statement parser (look for `%import` handling). Add special case:

```python
def _import_statement(self, stmt):
    # ... existing import logic ...

    # Check for special template import
    if module_name == "template":
        if len(imported_names) != 1 or imported_names[0] != "PYOBJ":
            raise GrammarError(
                "%import template only supports: %import template (PYOBJ)")

        # Flag grammar as using template mode
        self.grammar.uses_pyobj_placeholders = True

        # Create PYOBJ terminal
        self._create_pyobj_terminal()
        return

    # ... existing import logic continues ...
```

### Create PYOBJ Terminal

```python
def _create_pyobj_terminal(self):
    """Create the PYOBJ terminal definition."""
    from .lexer import PatternPlaceholder, TerminalDef

    # Create untyped PYOBJ
    pyobj_term = TerminalDef('PYOBJ', PatternPlaceholder())
    self.grammar.add_terminal(pyobj_term)
```

### Parse `PYOBJ[typename]` Syntax

In the symbol parser (where terminal references are recognized):

```python
def _parse_symbol(self, symbol_text):
    """Parse a symbol that might be PYOBJ[typename]."""

    # Check for typed placeholder
    if symbol_text.startswith('PYOBJ[') and symbol_text.endswith(']'):
        if not self.grammar.uses_pyobj_placeholders:
            raise GrammarError(
                "PYOBJ requires: %import template (PYOBJ)")

        # Extract type name
        type_name = symbol_text[6:-1]  # Strip "PYOBJ[" and "]"

        # Create typed terminal: PYOBJ[image] -> PYOBJ__IMAGE
        term_name = f"PYOBJ__{type_name.upper()}"

        # Create if doesn't exist
        if term_name not in self.grammar.terminals:
            from .lexer import PatternPlaceholder, TerminalDef
            typed_term = TerminalDef(term_name, PatternPlaceholder(type_name))
            self.grammar.add_terminal(typed_term)

        return Terminal(term_name)

    # ... existing symbol parsing logic ...
```

### Flag Grammar

Add attribute to Grammar class (`lark/grammar.py`):

```python
class Grammar:
    def __init__(self, ...):
        # ... existing init ...
        self.uses_pyobj_placeholders = False
```

## Step 3: Grammar Augmentation

**File**: `lark/grammar.py` or wherever grammar finalization happens

This happens after grammar is loaded, before parser creation.

```python
def augment_grammar_for_template_mode(grammar):
    """Add tree injection rules for template mode.

    For each nonterminal N and each label it can produce,
    add: N: TREE__LABEL with expand_single_child=True
    """
    from .lexer import PatternTree, TerminalDef
    from .grammar import Rule, RuleOptions

    # Step 1: Compute labels per nonterminal
    labels_per_nonterminal = {}

    for rule in grammar.rules:
        origin = rule.origin
        if origin not in labels_per_nonterminal:
            labels_per_nonterminal[origin] = set()

        # Label is alias if present, else origin name
        label = rule.alias if rule.alias else origin.name
        labels_per_nonterminal[origin].add(label)

    # Step 2: Collect all unique labels
    all_labels = set()
    for label_set in labels_per_nonterminal.values():
        all_labels.update(label_set)

    # Step 3: Create TREE__LABEL terminals
    tree_terminals = {}
    for label in all_labels:
        term_name = f"TREE__{label.upper()}"
        tree_term = TerminalDef(term_name, PatternTree(label))
        grammar.add_terminal(tree_term)
        tree_terminals[label] = Terminal(term_name)

    # Step 4: Add injection rules
    new_rules = []
    for origin, labels in labels_per_nonterminal.items():
        for label in labels:
            term = tree_terminals[label]

            # Create rule: origin: TREE__LABEL
            rule = Rule(
                origin=origin,
                expansion=[term],
                alias=None,  # No alias needed
                options=RuleOptions(expand_single_child=True)
            )
            new_rules.append(rule)

    # Add rules to grammar
    grammar.rules.extend(new_rules)

    return tree_terminals  # Return mapping for frontend use
```

Call this in parser construction if `lexer=="template"`.

## Step 4: Template Frontend

**File**: `lark/parser_frontends.py`

### Frontend Selection

Update `_construct_parsing_frontend`:

```python
def _construct_parsing_frontend(parser_type, lexer_type, lexer_conf, parser_conf, options):
    # ... existing cases ...

    if parser_type == "earley" and lexer_type == "template":
        return TemplateEarleyFrontend(lexer_conf, parser_conf, options)

    # ... existing code ...
```

Update validation:

```python
def _validate_frontend_args(parser, lexer):
    # ... existing validation ...

    if lexer == "template":
        assert_config(parser, ('earley',),
                      'Template lexer requires parser="earley", got %r')
```

### TemplateEarleyFrontend Class

Add new class in `lark/parser_frontends.py`:

```python
class TemplateEarleyFrontend:
    """Frontend for parsing Python 3.14 Template objects with Earley."""

    def __init__(self, lexer_conf: LexerConf, parser_conf: ParserConf, options):
        self.lexer_conf = lexer_conf
        self.parser_conf = parser_conf
        self.options = options

        # Augment grammar for tree splicing
        from .grammar import augment_grammar_for_template_mode
        self.tree_terminal_map = augment_grammar_for_template_mode(
            parser_conf.grammar)

        # Validate
        if not getattr(parser_conf.grammar, 'uses_pyobj_placeholders', False):
            logger.warning(
                "Template mode without %import template (PYOBJ): "
                "only Tree splicing will work")

        # Get type mappings
        self.pyobj_types = getattr(options, 'pyobj_types', {})

        # Build label map for quick lookup
        self.labels_per_nonterminal = self._compute_labels()

        # Create custom term matcher
        term_matcher = self._create_term_matcher()

        # Create Earley parser
        from .parsers.earley import Parser as EarleyParser
        resolve_ambiguity = options.ambiguity == 'resolve'
        debug = options.debug if options else False
        tree_class = options.tree_class or Tree if options.ambiguity != 'forest' else None

        self.parser = EarleyParser(
            lexer_conf, parser_conf, term_matcher,
            resolve_ambiguity=resolve_ambiguity,
            debug=debug,
            tree_class=tree_class,
            ordered_sets=getattr(options, 'ordered_sets', True)
        )

    def _compute_labels(self):
        """Compute which labels each nonterminal can produce."""
        labels = {}
        for rule in self.parser_conf.rules:
            if rule.origin not in labels:
                labels[rule.origin] = set()
            label = rule.alias if rule.alias else rule.origin.name
            labels[rule.origin].add(label)
        return labels

    def _create_term_matcher(self):
        """Create custom term matcher for PYOBJ and TREE__ terminals."""

        def term_match(term, token):
            if not isinstance(token, Token):
                return False

            term_name = term.name

            # Handle PYOBJ (untyped)
            if term_name == "PYOBJ":
                return token.type == "PYOBJ"

            # Handle PYOBJ__TYPENAME (typed)
            if term_name.startswith("PYOBJ__"):
                if token.type != term_name:
                    return False

                # Validate type if mapping provided
                if hasattr(term, 'pattern') and hasattr(term.pattern, 'expected_type'):
                    type_name = term.pattern.expected_type
                    if type_name and type_name in self.pyobj_types:
                        expected_type = self.pyobj_types[type_name]
                        if not isinstance(token.value, expected_type):
                            raise TypeError(
                                f"Expected {expected_type.__name__} for "
                                f"PYOBJ[{type_name}], got "
                                f"{type(token.value).__name__}")
                return True

            # Handle TREE__LABEL
            if term_name.startswith("TREE__"):
                if token.type != term_name:
                    return False
                if not isinstance(token.value, Tree):
                    return False
                expected_label = term_name[len("TREE__"):].lower()
                return token.value.data == expected_label

            # Normal terminals
            return token.type == term_name

        return term_match

    def _verify_start(self, start=None):
        """Verify start rule is valid."""
        if start is None:
            start_decls = self.parser_conf.start
            if len(start_decls) > 1:
                raise ConfigurationError(
                    "Lark initialized with more than 1 possible start rule. "
                    "Must specify which start rule to parse", start_decls)
            start ,= start_decls
        elif start not in self.parser_conf.start:
            raise ConfigurationError(
                f"Unknown start rule {start}. "
                f"Must be one of {self.parser_conf.start}")
        return start

    def parse(self, input_data, start=None, on_error=None):
        """Parse a Template or plain string."""
        from string.templatelib import Template

        chosen_start = self._verify_start(start)
        kw = {} if on_error is None else {'on_error': on_error}

        # Route to appropriate tokenization
        if isinstance(input_data, Template):
            token_stream = self._tokenize_template(input_data)
        else:
            # Plain string: use basic lexer
            token_stream = self._lex_string(input_data)

        # Parse
        try:
            tree = self.parser.parse(token_stream, chosen_start, **kw)
        except UnexpectedInput as e:
            self._enhance_error(e, input_data)
            raise

        # Post-process: splice trees
        if tree:
            from .template_mode import splice_inserted_trees
            splice_inserted_trees(tree)

        return tree

    def _tokenize_template(self, template):
        """Tokenize a Template object."""
        from .template_mode import tokenize_template, TemplateContext

        ctx = TemplateContext(
            lexer_conf=self.lexer_conf,
            tree_terminal_map={label: f"TREE__{label.upper()}"
                              for label in self._all_labels()},
            source_info=getattr(template, 'source_info', None)
        )

        return tokenize_template(template, ctx)

    def _lex_string(self, text):
        """Lex a plain string."""
        from .lexer import BasicLexer
        lexer = BasicLexer(self.lexer_conf)
        text_slice = TextSlice.cast_from(text)
        return lexer.lex(text_slice, None)

    def _all_labels(self):
        """Get all labels from grammar."""
        labels = set()
        for label_set in self.labels_per_nonterminal.values():
            labels.update(label_set)
        return labels

    def _enhance_error(self, exc, input_data):
        """Add template-specific context to errors."""
        from string.templatelib import Template

        if not isinstance(input_data, Template):
            return

        if not hasattr(exc, 'token') or not exc.token:
            return

        token_type = exc.token.type

        if token_type == "PYOBJ" or token_type.startswith("PYOBJ__"):
            exc.args = (
                f"Interpolated Python object at {exc.line}:{exc.column} "
                f"not allowed here (no PYOBJ placeholder in this context). "
                f"Original: {exc.args[0] if exc.args else ''}",
            )
        elif token_type.startswith("TREE__"):
            label = token_type[len("TREE__"):].lower()
            exc.args = (
                f"Interpolated Tree('{label}') at {exc.line}:{exc.column} "
                f"not valid in this context. "
                f"Original: {exc.args[0] if exc.args else ''}",
            )
```

## Step 5: Template Tokenizer

**File**: `lark/template_mode.py` (new file)

```python
"""Template tokenization for Python 3.14 t-strings."""

from dataclasses import dataclass
from typing import Iterator, Optional, List, Tuple, Dict
from .lexer import Token, BasicLexer
from .tree import Tree
from .utils import TextSlice
from .common import LexerConf


@dataclass
class TemplateContext:
    """Context for template tokenization."""
    lexer_conf: LexerConf
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
    # Create lexer for static text
    lexer = BasicLexer(ctx.lexer_conf)

    has_source = ctx.source_info is not None

    strings = template.strings
    interpolations = template.interpolations

    # Process alternating strings and interpolations
    for i, static_str in enumerate(strings):
        # Lex static string if non-empty
        if static_str:
            if has_source:
                start, end = ctx.source_info.segment_spans[i]
                text_slice = TextSlice(ctx.source_info.text, start, end)
            else:
                text_slice = TextSlice(static_str, 0, len(static_str))

            # Yield all tokens from lexing
            for token in lexer.lex(text_slice, None):
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
```

## Step 6: Lark Constructor API

**File**: `lark/lark.py`

Add `pyobj_types` parameter:

```python
class Lark:
    def __init__(self, grammar, ..., pyobj_types=None, ...):
        """
        Args:
            ...
            pyobj_types: Dict mapping type names to Python types for PYOBJ[typename].
                        Example: {'image': Image.Image, 'tensor': torch.Tensor}
            ...
        """
        # ... existing code ...

        # Store in options for frontend access
        options.pyobj_types = pyobj_types or {}

        # ... rest of init ...
```

## Testing Checklist

**File**: `tests/test_template_mode.py`

```python
import unittest
from string.templatelib import Template
from lark import Lark, Tree, Token
from lark.exceptions import GrammarError, ConfigurationError, UnexpectedToken


class TestTemplateMode(unittest.TestCase):

    def test_static_only(self):
        """Static template should parse like plain string."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        result = parser.parse(t"42")
        self.assertEqual(result, parser.parse("42"))

    def test_pyobj_untyped(self):
        """PYOBJ should accept any Python object."""
        grammar = r"""
        %import template (PYOBJ)
        start: "value:" PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Test various types
        self.assertIsNotNone(parser.parse(t"value:{42}"))
        self.assertIsNotNone(parser.parse(t"value:{'string'}"))
        self.assertIsNotNone(parser.parse(t"value:{[1,2,3]}"))

    def test_pyobj_typed(self):
        """PYOBJ[typename] should validate types."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template",
                      pyobj_types={'num': int})

        # Should work
        parser.parse(t"{42}")

        # Should fail
        with self.assertRaises(TypeError):
            parser.parse(t"{'not a number'}")

    def test_tree_splicing(self):
        """Pre-built trees should splice seamlessly."""
        grammar = r"""
        ?start: expr
        ?expr: term
             | expr "+" term -> add
        ?term: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Parse a fragment
        sub = parser.parse("1 + 2")

        # Splice it
        result = parser.parse(t"{sub}")
        self.assertEqual(result.data, 'add')

    def test_consecutive_objects(self):
        """Consecutive interpolations should work."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        result = parser.parse(t"{1}{2}")
        self.assertIsNotNone(result)

    def test_mixed_content(self):
        """Mix of static, objects, and trees."""
        grammar = r"""
        %import template (PYOBJ)
        start: "static" PYOBJ expr
        expr: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        sub = parser.parse("42")
        result = parser.parse(t"static {100} {sub}")
        self.assertIsNotNone(result)

    def test_error_no_pyobj(self):
        """Object where no PYOBJ allowed should error."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        with self.assertRaises(UnexpectedToken):
            parser.parse(t"{42}")

    def test_error_wrong_tree_label(self):
        """Tree with wrong label should error."""
        grammar = r"""
        start: expr
        expr: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Create tree with label grammar doesn't produce
        wrong_tree = Tree('wrong_label', [])

        with self.assertRaises((UnexpectedToken, ValueError)):
            parser.parse(t"{wrong_tree}")

    def test_requires_earley(self):
        """Template mode should require Earley parser."""
        grammar = "start: 'x'"

        with self.assertRaises(ConfigurationError):
            Lark(grammar, parser="lalr", lexer="template")

    def test_source_info_absent(self):
        """Should work without source_info."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Template without source_info
        result = parser.parse(t"{42}")
        self.assertIsNotNone(result)

    # TODO: Add test with source_info present
    # TODO: Add test for meta preservation


if __name__ == '__main__':
    unittest.main()
```

## Implementation Order

Suggested order to minimize debugging:

1. **Pattern classes** (Step 1) - Foundation, easy to test in isolation
2. **Grammar loader** (Step 2) - Test by loading grammars with `%import template (PYOBJ)`
3. **Grammar augmentation** (Step 3) - Test by inspecting augmented rules
4. **Template tokenizer** (Step 5) - Can test standalone with mock context
5. **Template frontend** (Step 4) - Brings it all together
6. **Lark API** (Step 6) - Final integration
7. **Tests** - Comprehensive validation

## Debugging Tips

- **Print token streams**: Add logging in `tokenize_template()` to see what tokens are produced
- **Check term matcher**: Add debug prints in `term_match()` to see what's being compared
- **Inspect augmented grammar**: Print `grammar.rules` after augmentation to verify injection rules
- **Test incrementally**: Start with static-only templates, then add PYOBJ, then Tree splicing
- **Use small grammars**: Debug with minimal grammars before trying complex ones

## Performance Considerations

- Grammar augmentation is O(rules × labels), done once at parser creation
- For large grammars with many labels, consider caching augmented grammars
- Token stream creation is linear in template size
- No per-parse overhead from template mode itself

## Future Work

Areas marked "TODO" or "Future":

- Cache augmented grammars
- Support other parser types (LALR, CYK)
- Better type checking (unions, generics)
- Multi-node splicing
- Multiple error reporting
