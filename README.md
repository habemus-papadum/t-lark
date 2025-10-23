# Fork of lark to explore POC extension to allow parsing python 3.14 t-strings.

## Note:
Created outside of normal GH fork network to temporarily work around https://github.com/openai/codex/issues/4489

## Status:
POC, published to pypi only simply my own testing, would not recommend use for
any more than experiments.   Once I have had a chance to work out my thoughts, I will
see if upstream has interest in a PR.

# Motivation

------------------------------------

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
from t_lark import Lark

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
from t_lark import Lark, Tree, Token
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

### Comprehensive Example: Graphics DSL with Paint Abstraction

This example demonstrates a realistic DSL for styling graphical objects, where paint values can be specified either as static color strings or as interpolated Python image objects. It showcases the power of template mode to seamlessly blend static syntax with typed objects and pre-parsed trees.

#### Grammar

```lark
%import template (PYOBJ)

start: object+

object: "object" "{" "stroke:" paint "fill:" paint "}"

paint: color
     | image

color: NUMBER "," NUMBER "," NUMBER  -> color

image: PYOBJ[image]  -> image

%import common.NUMBER
%ignore " "
```

**Key features:**
- `paint` can be either a static color specification or a typed Python object
- The `color` rule parses RGB triplets from static text
- The `image` rule accepts only objects of type `Image` via typed placeholder
- Objects have both stroke and fill paints

#### Setting Up the Parser

```python
from t_lark import Lark

# Placeholder Image class for this example
class Image:
    def __init__(self, path):
        self.path = path
    def __repr__(self):
        return f"Image({self.path!r})"

# Create parser with type mapping
grammar = open('graphics.lark').read()
parser = Lark(grammar, parser="earley", lexer="template",
              pyobj_types={'image': Image})
```

#### Scenario 1: Pure Static String

Parse a complete object specification from static text only:

```python
# All colors specified as static text
program = t"object { stroke: 255,0,0 fill: 0,255,0 }"
tree = parser.parse(program)

# Result: Tree with two color nodes
print(tree.pretty())
# start
#   object
#     color    [255,0,0]
#     color    [0,255,0]
```

This works exactly like traditional string parsing - no interpolations.

#### Scenario 2: Interpolating Image Objects

Mix static syntax with runtime Python objects:

```python
# Create image objects at runtime
texture = Image("wood_texture.png")
gradient = Image("gradient.png")

# Interpolate them directly into the DSL
program = t"object { stroke: {texture} fill: {gradient} }"
tree = parser.parse(program)

# The PYOBJ tokens carry the actual Image objects
print(tree.pretty())
# start
#   object
#     image    [Token(PYOBJ__IMAGE, Image('wood_texture.png'))]
#     image    [Token(PYOBJ__IMAGE, Image('gradient.png'))]

# Extract the image objects from the parse tree
stroke_image = tree.children[0].children[0].children[0].value
fill_image = tree.children[0].children[1].children[0].value
print(stroke_image)  # Image('wood_texture.png')
print(fill_image)    # Image('gradient.png')
```

**Type safety**: If you try to pass a non-Image object, you get a clear error:

```python
program = t"object { stroke: {'not an image'} fill: 0,0,255 }"
parser.parse(program)
# Raises: TypeError: Expected Image for PYOBJ[image], got str
```

#### Scenario 3: Tree Splicing for Metaprogramming

Parse color fragments separately, then compose them dynamically:

```python
# Parse colors independently
red = parser.parse("255,0,0")      # Tree('color', [...])
blue = parser.parse("0,0,255")     # Tree('color', [...])
green = parser.parse("0,255,0")    # Tree('color', [...])

# Use Python control flow to select colors
colors = [red, green, blue]
palette = []

for i, color in enumerate(colors):
    if i % 2 == 0:
        # Even indices: use parsed color tree for both stroke and fill
        palette.append(t"object { stroke: {color} fill: {color} }")
    else:
        # Odd indices: mix parsed tree with static color
        palette.append(t"object { stroke: {color} fill: 0,0,0 }")

# Combine into full program
program = t"".join(palette)
tree = parser.parse(program)

print(f"Generated {len(tree.children)} objects")
# Generated 3 objects
```

**Why this works**: The grammar augmentation creates injection rules that let `paint` accept a `TREE__COLOR` token. When we interpolate a `Tree('color', ...)`, it matches seamlessly.

#### Scenario 4: Mixed Static, Objects, and Trees

Combine all three approaches in a single program:

```python
red = parser.parse("255,0,0")
texture = Image("pattern.png")

# Object 1: static color for both
# Object 2: tree splice + interpolated image
# Object 3: interpolated image + static color
program = t"""
object { stroke: 128,128,128 fill: 64,64,64 }
object { stroke: {red} fill: {texture} }
object { stroke: {texture} fill: 0,255,128 }
"""

tree = parser.parse(program)
# All three objects parse correctly, each with different paint combinations
```

#### Error Cases

**1. Interpolating a Python object where no PYOBJ placeholder exists:**

```python
# The grammar has no PYOBJ rule that accepts arbitrary objects
# Only PYOBJ[image] is allowed
some_list = [1, 2, 3]
program = t"object { stroke: {some_list} fill: 0,0,0 }"
parser.parse(program)
# Raises: UnexpectedToken: Interpolated Python object not allowed here
```

**2. Type mismatch for typed placeholder:**

```python
program = t"object { stroke: {42} fill: 0,0,0 }"
parser.parse(program)
# Raises: TypeError: Expected Image for PYOBJ[image], got int
```

**3. Splicing a tree with the wrong label:**

```python
# Create a tree with label that grammar doesn't produce
wrong_tree = Tree('circle', [])
program = t"object { stroke: {wrong_tree} fill: 0,0,0 }"
parser.parse(program)
# Raises: ValueError: Cannot splice Tree('circle'): grammar does not produce this label
```

**4. Malformed static syntax:**

```python
# Missing a color component
program = t"object { stroke: 255,0 fill: 0,0,0 }"
parser.parse(program)
# Raises: UnexpectedToken (standard parse error)
```

#### Key Insights

This example demonstrates:

1. **Seamless integration**: Static text, typed Python objects, and pre-parsed trees all work together naturally
2. **Type safety**: `PYOBJ[image]` ensures only Image objects are accepted, catching errors at parse time
3. **Compositional power**: Parse fragments independently, then use Python's control flow to build complex programs
4. **No serialization overhead**: Image objects (or tensors, arrays, etc.) pass through the parser directly without string conversion
5. **Clear error messages**: Type mismatches and structural errors are reported with helpful context

This pattern is particularly powerful for DSLs in computer graphics, machine learning, and other domains where data is naturally represented as rich Python objects rather than strings.

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




----------------------------------

# Lark - a parsing toolkit for Python

Lark is a parsing toolkit for Python, built with a focus on ergonomics, performance and modularity.

Lark can parse all context-free languages. To put it simply, it means that it is capable of parsing almost any programming language out there, and to some degree most natural languages too.

**Who is it for?**

   - **Beginners**: Lark is very friendly for experimentation. It can parse any grammar you throw at it, no matter how complicated or ambiguous, and do so efficiently. It also constructs an annotated parse-tree for you, using only the grammar and an input, and it gives you convenient and flexible tools to process that parse-tree.

   - **Experts**: Lark implements both Earley(SPPF) and LALR(1), and several different lexers, so you can trade-off power and speed, according to your requirements. It also provides a variety of sophisticated features and utilities.

**What can it do?**

 - Parse all context-free grammars, and handle any ambiguity gracefully
 - Build an annotated parse-tree automagically, no construction code required.
 - Provide first-rate performance in terms of both Big-O complexity and measured run-time (considering that this is Python ;)
 - Run on every Python interpreter (it's pure-python)
 - Generate a stand-alone parser (for LALR(1) grammars)

And many more features. Read ahead and find out!

Most importantly, Lark will save you time and prevent you from getting parsing headaches.

### Quick links

- [Documentation @readthedocs](https://lark-parser.readthedocs.io/)
- [Cheatsheet (PDF)](/docs/_static/lark_cheatsheet.pdf)
- [Online IDE](https://lark-parser.org/ide)
- [Tutorial](/docs/json_tutorial.md) for writing a JSON parser.
- Blog post: [How to write a DSL with Lark](https://eshsoft.com/blog/write-dsl-in-python-with-lark)
- [Gitter chat](https://gitter.im/lark-parser/Lobby)

### Install Lark

    $ pip install t-lark --upgrade

Lark has no dependencies.

[![Tests](https://github.com/lark-parser/lark/actions/workflows/tests.yml/badge.svg)](https://github.com/lark-parser/lark/actions/workflows/tests.yml)

### Syntax Highlighting

Lark provides syntax highlighting for its grammar files (\*.lark):

- [Sublime Text & TextMate](https://github.com/lark-parser/lark_syntax)
- [vscode](https://github.com/lark-parser/vscode-lark)
- [Intellij & PyCharm](https://github.com/lark-parser/intellij-syntax-highlighting)
- [Vim](https://github.com/lark-parser/vim-lark-syntax)
- [Atom](https://github.com/Alhadis/language-grammars)

### Clones

These are implementations of Lark in other languages. They accept Lark grammars, and provide similar utilities.

- [Lerche (Julia)](https://github.com/jamesrhester/Lerche.jl) - an unofficial clone, written entirely in Julia.
- [Lark.js (Javascript)](https://github.com/lark-parser/lark.js) - a port of the stand-alone LALR(1) parser generator to Javascsript.

### Hello World

Here is a little program to parse "Hello, World!" (Or any other similar phrase):

```python
from t_lark import Lark

l = Lark('''start: WORD "," WORD "!"

            %import common.WORD   // imports from terminal library
            %ignore " "           // Disregard spaces in text
         ''')

print( l.parse("Hello, World!") )
```

And the output is:

```python
Tree(start, [Token(WORD, 'Hello'), Token(WORD, 'World')])
```

Notice punctuation doesn't appear in the resulting tree. It's automatically filtered away by Lark.

### Fruit flies like bananas

Lark is great at handling ambiguity. Here is the result of parsing the phrase "fruit flies like bananas":

![fruitflies.png](examples/fruitflies.png)

[Read the code here](https://github.com/lark-parser/lark/tree/master/examples/fruitflies.py), and see [more examples here](https://lark-parser.readthedocs.io/en/latest/examples/index.html).


## List of main features

 - Builds a parse-tree (AST) automagically, based on the structure of the grammar
 - **Earley** parser
    - Can parse all context-free grammars
    - Full support for ambiguous grammars
 - **LALR(1)** parser
    - Fast and light, competitive with PLY
    - Can generate a stand-alone parser ([read more](docs/tools.md#stand-alone-parser))
 - **EBNF** grammar
 - **Unicode** fully supported
 - Automatic line & column tracking
 - Interactive parser for advanced parsing flows and debugging
 - Grammar composition - Import terminals and rules from other grammars
 - Standard library of terminals (strings, numbers, names, etc.)
 - Import grammars from Nearley.js ([read more](/docs/tools.md#importing-grammars-from-nearleyjs))
 - Extensive test suite [![codecov](https://codecov.io/gh/lark-parser/lark/branch/master/graph/badge.svg?token=lPxgVhCVPK)](https://codecov.io/gh/lark-parser/lark)
 - Type annotations (MyPy support)
 - And much more!

See the full list of [features here](https://lark-parser.readthedocs.io/en/latest/features.html)


### Comparison to other libraries

#### Performance comparison

Lark is fast and light (lower is better)

![Run-time Comparison](docs/_static/comparison_runtime.png)

![Memory Usage Comparison](docs/_static/comparison_memory.png)


Check out the [JSON tutorial](/docs/json_tutorial.md#conclusion) for more details on how the comparison was made.

For thorough 3rd-party benchmarks, checkout the [Python Parsing Benchmarks](https://github.com/goodmami/python-parsing-benchmarks) repo.

#### Feature comparison

| Library | Algorithm | Grammar | Builds tree? | Supports ambiguity? | Can handle every CFG? | Line/Column tracking | Generates Stand-alone
|:--------|:----------|:----|:--------|:------------|:------------|:----------|:----------
| **Lark** | Earley/LALR(1) | EBNF | Yes! | Yes! | Yes! | Yes! | Yes! (LALR only) |
| [PLY](http://www.dabeaz.com/ply/) | LALR(1) | BNF | No | No | No | No | No |
| [PyParsing](https://github.com/pyparsing/pyparsing) | PEG | Combinators | No | No | No\* | No | No |
| [Parsley](https://pypi.python.org/pypi/Parsley) | PEG | EBNF | No | No | No\* | No | No |
| [Parsimonious](https://github.com/erikrose/parsimonious) | PEG | EBNF | Yes | No | No\* | No | No |
| [ANTLR](https://github.com/antlr/antlr4) | LL(*) | EBNF | Yes | No | Yes? | Yes | No |


(\* *PEGs cannot handle non-deterministic grammars. Also, according to Wikipedia, it remains unanswered whether PEGs can really parse all deterministic CFGs*)


### Projects using Lark

 - [Poetry](https://github.com/python-poetry/poetry-core) - A utility for dependency management and packaging
 - [Vyper](https://github.com/vyperlang/vyper) - Pythonic Smart Contract Language for the EVM
 - [PyQuil](https://github.com/rigetti/pyquil) - Python library for quantum programming using Quil
 - [Preql](https://github.com/erezsh/preql) - An interpreted relational query language that compiles to SQL
 - [Hypothesis](https://github.com/HypothesisWorks/hypothesis) - Library for property-based testing
 - [mappyfile](https://github.com/geographika/mappyfile) - a MapFile parser for working with MapServer configuration
 - [tartiflette](https://github.com/dailymotion/tartiflette) - GraphQL server by Dailymotion
 - [synapse](https://github.com/vertexproject/synapse) - an intelligence analysis platform
 - [Datacube-core](https://github.com/opendatacube/datacube-core) - Open Data Cube analyses continental scale Earth Observation data through time
 - [SPFlow](https://github.com/SPFlow/SPFlow) - Library for Sum-Product Networks
 - [Torchani](https://github.com/aiqm/torchani) - Accurate Neural Network Potential on PyTorch
 - [Command-Block-Assembly](https://github.com/simon816/Command-Block-Assembly) - An assembly language, and C compiler, for Minecraft commands
 - [EQL](https://github.com/endgameinc/eql) - Event Query Language
 - [Fabric-SDK-Py](https://github.com/hyperledger/fabric-sdk-py) - Hyperledger fabric SDK with Python 3.x
 - [required](https://github.com/shezadkhan137/required) - multi-field validation using docstrings
 - [miniwdl](https://github.com/chanzuckerberg/miniwdl) - A static analysis toolkit for the Workflow Description Language
 - [pytreeview](https://gitlab.com/parmenti/pytreeview) - a lightweight tree-based grammar explorer
 - [harmalysis](https://github.com/napulen/harmalysis) - A language for harmonic analysis and music theory
 - [gersemi](https://github.com/BlankSpruce/gersemi) - A CMake code formatter
 - [MistQL](https://github.com/evinism/mistql) - A query language for JSON-like structures
 - [Outlines](https://github.com/outlines-dev/outlines) - Structured generation with Large Language Models

[Full list](https://github.com/lark-parser/lark/network/dependents?package_id=UGFja2FnZS01MjI1OTE0NQ%3D%3D)

## License

Lark uses the [MIT license](LICENSE).

(The standalone tool is under MPL2)

## Contributors

Lark accepts pull-requests. See [How to develop Lark](/docs/how_to_develop.md)

Big thanks to everyone who contributed so far:

  <img src="https://contributors-img.web.app/image?repo=lark-parser/lark" />
<a href="https://github.com/lark-parser/lark/graphs/contributors">
</a>

## Sponsor

If you like Lark, and want to see us grow, please consider [sponsoring us!](https://github.com/sponsors/lark-parser)

## Contact the author

Questions about code are best asked on [gitter](https://gitter.im/lark-parser/Lobby) or in the issues.

For anything else, I can be reached by email at erezshin at gmail com.

 -- [Erez](https://github.com/erezsh)
