# Using Lark to Parse Python 3.14 Template Literals (t-strings)

## Overview

This document specifies a new parsing mode for Lark that directly parses Python 3.14 **template string literals** (t-strings) as structured input. Template strings, introduced in [PEP 750](https://peps.python.org/pep-0750/), produce `Template` objects from the `string.templatelib` module that contain ordered sequences of literal string segments and interpolated Python values.

This feature enables building **domain-specific languages (DSLs)** where interpolated values are treated as first-class grammatical elements rather than being pre-evaluated into strings. This is particularly powerful for:

- **Procedural generation DSLs**: Where interpolated values might be PIL images, numpy arrays, or other domain objects
- **Machine learning DSLs**: Where interpolated values might be PyTorch tensors or TensorFlow operations
- **Metaprogramming**: Where pre-parsed syntax trees can be spliced into larger programs using host language control flow

## Motivation & Use Cases

### Why Parse Templates?

Traditional string-based parsing requires converting all data to strings first, then parsing. This loses type information and forces serialization/deserialization. Template mode allows you to:

1. **Preserve object identity**: Pass complex Python objects (tensors, images, AST nodes) directly through the parser
2. **Leverage host language control flow**: Build programs dynamically using Python's `if`, `for`, etc. to compose syntax trees
3. **Enable type-safe DSLs**: Restrict interpolated values by Python type

### Example Use Case: Procedural Image Generation DSL

```python
from PIL import Image
from lark import Lark

# Grammar for image composition DSL
grammar = r"""
%import template (PYOBJ)
start: command+
command: "show" PYOBJ[image] "at" "(" NUMBER "," NUMBER ")"
       | "blend" PYOBJ[image] PYOBJ[image] "ratio" NUMBER
NUMBER: /\d+/
"""

parser = Lark(grammar, parser="earley", lexer="template",
              pyobj_types={'image': Image.Image})

# Use t-strings to embed actual PIL images
bg = Image.open("background.jpg")
sprite = Image.open("sprite.png")

# Build program using Python control flow
commands = []
for i in range(10):
    x, y = i * 50, i * 30
    commands.append(t"show {sprite} at ({x},{y})\n")

program = t"show {bg} at (0,0)\n" + t"".join(commands)
ast = parser.parse(program)
```

### Example Use Case: Tree Splicing for Metaprogramming

```python
# Parse fragments separately, then compose
expr1 = parser.parse("x + 1")  # Tree('add', [Tree('var', ['x']), Tree('num', ['1'])])
expr2 = parser.parse("y * 2")  # Tree('mul', [Tree('var', ['y']), Tree('num', ['2'])])

# Use Python to decide structure, splice pre-built trees
if condition:
    full = t"print({expr1});"
else:
    full = t"print({expr2});"

final_ast = parser.parse(full)  # Trees spliced seamlessly
```

## Background: Python 3.14 Template API

### Template Creation

Templates are created using the `t` prefix (like f-strings but for templates):

```python
from string.templatelib import Template

# User-facing syntax
template = t"Hello {name}, you have {count} messages"

# Under the hood, produces a Template object with:
# - .strings = ("Hello ", ", you have ", " messages")
# - .interpolations = (Interpolation(...), Interpolation(...))
```

### Template Attributes

**`strings`** : `Tuple[str, ...]`
- Static string segments between interpolations
- Always has exactly one more element than `interpolations`
- Empty strings are preserved (e.g., `t"{x}{y}"` has strings `("", "", "")`)
- Never empty; minimum is `("text",)` for templates with no interpolations

**`interpolations`** : `Tuple[Interpolation, ...]`
- Interpolated expression objects
- Has exactly one fewer element than `strings`

**`values`** : `Tuple[Any, ...]`
- Shorthand for `tuple(i.value for i in template.interpolations)`

### Interpolation Attributes

Each `Interpolation` object has:

- **`value`**: The evaluated result of the expression (e.g., `t'{1+2}'.interpolations[0].value == 3`)
- **`expression`**: String text of the expression (e.g., `"1+2"`)
- **`conversion`**: Optional conversion flag (`'a'`, `'r'`, `'s'`, or `None`)
- **`format_spec`**: Optional format specification string

**Note**: The standard Template API does **not** include source location metadata. See [Source Location Tracking](#source-location-tracking-optional) for how we handle this.

### Template Iteration

```python
for item in template:
    if isinstance(item, str):
        print(f"Static: {item}")
    else:  # Interpolation
        print(f"Dynamic: {item.value}")
```

**Important**: `__iter__` skips empty strings, but `.strings` preserves them.

## High-Level Design

### New Mode, No New API Surface

Users opt into template parsing by constructing Lark with Earley and a new lexer mode:

```python
parser = Lark(grammar, parser="earley", lexer="template")
tree = parser.parse(a_template_object)   # Accepts Template or plain str
```

- If `lexer="template"` and input is a Template, the template-aware pipeline runs
- If input is a plain str, it is treated as a single literal segment and parsed normally
- Template mode **requires** `parser="earley"` (the Earley algorithm is essential to the approach)

### Two Types of Interpolations

Template mode supports two distinct types of interpolated values:

#### 1. Python Objects via `PYOBJ` Terminals

The grammar extension `PYOBJ` introduces a placeholder terminal that matches any interpolated Python object (including strings, since strings are Python objects):

```lark
%import template (PYOBJ)           // Makes PYOBJ available
stmt: "print" "(" PYOBJ ")" ";"    // Accepts any Python object
```

**Typed placeholders (v1)**:
```lark
%import template (PYOBJ)
command: "show" PYOBJ[image] | "load" PYOBJ[path]
```

When creating the parser, provide type mappings:
```python
from PIL import Image
parser = Lark(grammar, parser="earley", lexer="template",
              pyobj_types={'image': Image.Image, 'path': str})
```

At parse time, `PYOBJ[image]` will only accept `isinstance(value, Image.Image)`.

**Important**:
- Interpolated Python strings (e.g., `t"hello {some_str}"`) appear as `PYOBJ` tokens; they are **not** merged with adjacent static text
- If a grammar contains no `PYOBJ`, interpolated non-Tree objects will cause parse errors
- Type checking happens during parsing for typed placeholders

#### 2. Lark Trees via Automatic Splicing

In template mode, you may splice in a pre-existing `Tree` object **regardless of whether the grammar mentions `PYOBJ`**:

```python
# Parse a fragment
sub_expr = parser.parse("x + 1")  # Returns Tree('add', [...])

# Splice it into a larger template
program = t"{sub_expr};"
tree = parser.parse(program)  # Works! The Tree is spliced seamlessly
```

**How it works**: The grammar is auto-augmented with injection rules that let any nonterminal accept a prebuilt subtree whose `.data` label matches what the grammar would normally produce there.

**Use case**: This enables using Python's control flow to dynamically assemble programs at runtime:
```python
# Conditional AST construction
exprs = []
for item in data:
    if item.is_complex():
        exprs.append(parser.parse(f"process({item.id})"))
    else:
        exprs.append(parser.parse(f"simple({item.id})"))

# Compose them using t-strings
commands = [t"{e};" for e in exprs]
program = t"\n".join(commands)
final = parser.parse(program)
```

The Trees may have been created by:
- Prior calls to `parser.parse()`
- Programmatic construction (`Tree('add', [...])`)
- A compiler for a higher-level language

### Tokenization of Templates

The template is linearized into a token stream with these rules:

1. **Static text segments** → lexed into normal tokens using the grammar's regex/literal rules
2. **Interpolated Python objects (non-Tree)** → a single placeholder token of type `PYOBJ`, with the object as its `.value`
3. **Interpolated Tree** → a single special token `TREE__<LABEL>` that stands for that completed subtree

During parsing, `TREE__<LABEL>` tokens are shifted/reduced via injection rules, and after parsing completes, we replace them with the actual Tree objects.

### Source Location Tracking (Optional)

Every produced token carries absolute file line, column, start_pos, end_pos. This information comes from two sources:

1. **For static segments**: The lexer processes a `TextSlice` that embeds absolute offsets, so tokens get precise positions in the original file
2. **For interpolations**: Token metadata reflects the location of the `{expr}` expression in the source

**Source Info Contract**: To enable location tracking, Templates may have an optional `.source_info` attribute (provided by third-party tooling, not by standard Python):

```python
@dataclass
class SourceInfo:
    filename: str                              # Source file path
    text: str                                  # Full file content
    segment_spans: List[Tuple[int, int]]       # (start, end) byte offset for each static string
    interpolation_spans: List[Tuple[int, int]] # (start, end) byte offset for each {expr}
```

If `.source_info` is absent, tokens will have `None` for position fields (parsing still works).

When we inject a Tree, its token inherits the tree's own `.meta` if present; after splicing, the parent meta remains correct.

### Error Handling

- Parse stops on the first syntax error (as Lark normally does)
- Error messages report file/line/column for the failing token (static or interpolation)
- If a Python object appears where no `PYOBJ` is allowed, error points to the interpolation's source location
- If a Tree with label `'foo'` appears where the grammar doesn't produce `'foo'`, error points to that interpolation

## Implementation Details (Step-by-Step)

See the [implementation guide](./template_parsing_implementation.md) for complete technical details on:

- Grammar loader modifications for `%import template (PYOBJ)`
- Pattern classes (`PatternPlaceholder`, `PatternTree`)
- Tree-injection augmentation algorithm
- `TemplateEarleyFrontend` implementation
- Template tokenization in `template_mode.py`
- Error handling and source location tracking

## Usage Examples

### Basic Example: Static + Object + Tree

```lark
// grammar.lark
%import template (PYOBJ)
?start: stmt+

stmt: "print" "(" PYOBJ ")" ";"
    | expr ";"

?expr: term
     | expr "+" term  -> add
?term: NUMBER

%import common.NUMBER
%ignore " "
```

```python
from lark import Lark, Tree, Token
from string.templatelib import Template

parser = Lark(open('grammar.lark').read(), parser="earley", lexer="template")

# 1. Static-only (works like normal string)
T1 = t"42;"
print(parser.parse(T1))  # Tree('start', [Tree('term', [Token('NUMBER', '42')])])

# 2. Interpolated Python object
T2 = t"print({42});"
print(parser.parse(T2))  # Tree('start', [Tree('stmt', [...PYOBJ token...])])

# 3. Splice an existing subtree
sub = Tree('add', [
    Tree('term', [Token('NUMBER', '1')]),
    Tree('term', [Token('NUMBER', '2')])
])

T3 = t"{sub};"
print(parser.parse(T3))  # Tree('start', [Tree('add', [...])]) - sub spliced seamlessly!
```

### Typed Placeholders Example

```lark
%import template (PYOBJ)
command: "show" PYOBJ[image] "at" "(" NUMBER "," NUMBER ")"

%import common.NUMBER
```

```python
from PIL import Image

parser = Lark(grammar, parser="earley", lexer="template",
              pyobj_types={'image': Image.Image})

img = Image.open("test.png")
T = t"show {img} at (10, 20)"
tree = parser.parse(T)  # Works!

# This would fail type check:
T_bad = t"show {'not an image'} at (10, 20)"
parser.parse(T_bad)  # Raises TypeError
```

### Metaprogramming with Control Flow

```python
# Build program dynamically
def compile_conditions(checks):
    exprs = []
    for check in checks:
        if check.type == "simple":
            exprs.append(parser.parse(f"{check.var} > 0"))
        else:
            exprs.append(parser.parse(f"complex({check.var})"))

    # Compose using t-strings and Python's join
    program = t"if " + t" && ".join(t"({e})" for e in exprs) + t" then action();"
    return parser.parse(program)
```

## Edge Cases & Nuances

- **Consecutive objects** (`t"{obj1}{obj2}"`): Emits back-to-back PYOBJ/TREE__ tokens with an empty static string between. Grammar must allow them (e.g., via `PYOBJ PYOBJ` or `PYOBJ+`)

- **Strings as objects**: A Python str interpolation is just another PYOBJ. It does **not** join with neighbors; grammar must explicitly accept `PYOBJ` to receive it

- **Empty strings in `.strings`**: Template.strings preserves empty strings (e.g., `t"{x}"` has strings `("", "")`). Our tokenizer skips empty strings when lexing but preserves structure

- **Tree label mapping**: We only accept a spliced Tree by its top `.data` label. We do not deep-validate internal shape; we assume it was produced by the same (or compatible) grammar

- **Aliases vs rule names**: Because we compute `labels(N)` from aliases (`-> label`) and default labels (rule name), injected trees labeled `label` will be accepted where `N` can produce `label`. This mirrors normal Lark output structure

- **Positions when Template strips braces**: Use the interpolation's `{expr}` expression position for PYOBJ/TREE__ tokens so error arrows point at the expression that supplied the value

- **Performance**: Grammar augmentation with injection rules happens once at parser creation, not per-parse. For grammars with many labels, this may add startup time but not parse-time overhead

## Testing Requirements

### Happy Paths

1. **Static-only template** equals plain string parse
2. **`PYOBJ` accepts all types**: str, int, float, custom objects
3. **Typed `PYOBJ[typename]`** validates isinstance correctly
4. **Splice `Tree('label')`** where grammar produces `label`
5. **Consecutive placeholders**: `t"{obj1}{obj2}"`, `t"{tree1}{tree2}"`
6. **Mixed**: `t"static {obj} more {tree} text"`

### Error Cases

1. **Interpolated object where no `PYOBJ` allowed** → `UnexpectedToken` with helpful message
2. **Tree with label not produced by grammar** → Error with location
3. **Type mismatch for typed placeholder** → `TypeError`
4. **Using `lexer="template"` without `parser="earley"`** → `ConfigurationError`

### Source Locations (Meta)

Verify token and node meta (line/col/start/end) for:
- Static tokens inside first/middle/last segments
- Object token meta points to `{expr}` location
- Spliced tree meta preserved from original parse
- Correct meta when `.source_info` is absent (all `None`)

### Structure

- Ensure result trees from splicing are shape-identical to normal parses (no extra wrapper nodes) thanks to `expand_single_child`
- Verify aliases are respected in tree splicing

## Done Criteria

### Feature Complete

`lexer="template"` + `parser="earley"` successfully parses:

a) Templates built from static strings + Python objects + Trees
b) Plain strings (for backward compatibility)

### Structural Correctness

- Trees spliced via interpolation produce the same shape as normal parses (no stray wrapper nodes) thanks to `expand_single_child`
- All tree labels (aliases and rule names) are correctly mapped to injection rules

### Source Locations

- Token and node metadata (line/col/start/end) is accurate for both static and interpolated parts when `.source_info` is provided
- Parsing works correctly when `.source_info` is absent (positions are `None`)

### Error Quality

- Error messages clearly indicate when interpolated objects/trees are not accepted
- Error locations point to the interpolation expression in source, not the template construction
- Type mismatches for typed placeholders give helpful messages

### Compatibility

- Template mode is opt-in: existing Lark code unaffected
- No performance regression for non-template parsing
- No changes to core Earley algorithm

## Future Enhancements (Out of Scope for v1)

- **Multi-node splicing**: Allow an interpolation to produce multiple siblings (would require grammar support for sequences or a richer token→nodes mechanism)

- **Multiple-error recovery**: Exploit Earley forest to report more than one error per parse

- **Better type checking**: Support union types, generic types, Protocol types in `PYOBJ[...]`

- **Caching**: Cache augmented grammars to avoid recomputing injection rules

- **Other parsers**: Investigate if LALR or CYK could support template mode with different injection strategies
