# Template Mode Implementation Plan

Implementation plan for adding a template mode to Lark that parses Python 3.14 `string.templatelib.Template` objects (t-strings) with:



## High-level Design

### New Mode, No New API Surface

Users opt into template parsing by constructing Lark with Earley and a new lexer mode:

```python
parser = Lark(grammar, parser="earley", lexer="template")
tree = parser.parse(a_template_object)   # accepts Template or plain str
```

- If `lexer="template"` and input is a Template, the template-aware pipeline runs
- If input is a plain str, it is treated as a single literal segment and parsed normally

### Grammar Extension: ∏ Placeholders for Python Objects (non-Tree)

- ∏ introduces a placeholder terminal that matches any interpolated Python object (including str, because strings are Python objects), not a Lark Tree
- For the initial version, treat ∏ as Any (future: allow ∏Type constraints)
- If a grammar contains no ∏, interpolated Python objects will not be accepted

### Subtree Splicing is Always On

In template mode, you may splice in a pre-existing Tree regardless of whether the grammar mentions ∏.

Internally, the grammar is auto-augmented with tiny injection rules that let any nonterminal accept a prebuilt subtree whose label matches what the grammar would normally produce there.

### Tokenization of Template

The template is linearized into a token stream:

- **Static text segments** → lexed into normal tokens using the grammar's regex/literal rules
- **Interpolated Python objects (non-Tree)** → a single placeholder token (type from ∏), value = the Python object (including str)
- **Interpolated Tree** → a single special token that stands for that completed subtree. The parser reduces it via the injection rules and we splice the underlying Tree back into the final result

### Source Location Tracking

Every produced token carries absolute file line, column, start_pos, end_pos sourced from:

- Static segment metadata (filename, offsets)
- Interpolation expression metadata (location of {...} expression)

When we inject a Tree, its token inherits the tree's own meta (if present); after splice, the parent meta remains correct.

### Errors

- Parse stops on the first syntax error (as Lark normally does)
- Error messages report file/line/column for the failing token (static or interpolation)
- If a Python object appears where no ∏ is allowed, the error points to the interpolation's source

## Implementation Details (Step-by-Step)

### 0) Files You'll Touch (Orientation)

**Grammar & building:**
- `lark/load_grammar.py` (or the grammar loader module)
- `lark/grammar.py` (TerminalDef, Rule, RuleOptions)

**Lexing / frontends:**
- `lark/parser_frontends.py` (new frontend selection)
- `lark/lexer.py` (add a tiny Pattern subclass or helper)

**Earley integration:**
- `lark/parsers/earley.py` (no edits to algorithm; pass a custom term matcher)

**Template mode:**
- New module: `lark/template_mode.py` (template tokenizer, utilities)

**Tests:**
- `tests/test_template_mode.py`

**Key principle:** Keep core algorithms unmodified; hook via frontends and small grammar augmentation.

### 1) Grammar Support for ∏ (Python Objects; Non-Tree Only)

**Goal:** Parse ∏ inside grammar rules as a terminal that accepts external Python objects.

**Parsing ∏:**
- Extend Lark's grammar grammar to recognize a placeholder terminal token `PI_PLACEHOLDER` for the literal ∏
- In the post-parse grammar builder, whenever ∏ appears in a production RHS, replace it with a generated terminal name (all-caps), e.g. `PYOBJ`
- Initial version: one global terminal `PYOBJ` for all occurrences (Any)
- Future: ∏Type → PYOBJ_TYPENAME, and store expected_type

**TerminalDef for PYOBJ:**
- Create a `TerminalDef('PYOBJ', PatternPlaceholder())`
- Implement `PatternPlaceholder` (lightweight Pattern subclass) whose `to_regexp()` returns an impossible regex (it is never matched by text; it's matched by our custom matcher against injected tokens)

**Flagging the grammar:**
- Set `grammar.uses_python_placeholders = True` if any ∏ seen (for helpful diagnostics, e.g., reject `lexer!="template"` with an actionable message)
- Behavior: Only grammars that contain ∏ can accept interpolated Python objects. Otherwise, such objects cause a syntax error

### 2) Always-On Subtree Splicing via Grammar Augmentation

**Goal:** Allow splicing in prebuilt Tree values anywhere a corresponding nonterminal (or labeled production) is expected—without requiring the grammar to mention it.

**Collect accepted labels per nonterminal:**
- For each grammar rule N, compute the set labels(N) of tree labels the grammar produces for N:
  - If a production has an alias `-> label`, include `label`
  - Else include the rule name N (default label)
- Build the union set AllLabels = ⋃_N labels(N)

**Create special terminals for trees:**
- For each label in AllLabels, introduce a terminal `TREE__{LABEL_UPPER}` with a `PatternTree(label='label')`

**Add injection rules:**
- For each nonterminal N and for each label ∈ labels(N), add:
  ```
  N: TREE__LABEL   // with RuleOptions.expand1 = True
  ```
- No alias here, but set `expand1=True` so that this rule collapses to its single child at build time
- During parsing, shifting `TREE__LABEL` then reducing `N: TREE__LABEL` will inline the child (which we later replace by the actual Tree)

**Why expand1=True?**
It prevents creating an extra N wrapper in the result; the output stays shaped like a normal parse would (top label stays label).

**Result:** Any inserted Tree with `.data == label` can be accepted wherever the grammar expects N that can produce label.

### 3) Template Frontend: Select the Template Lexing Pipeline

In `parser_frontends.get_frontend`, add a case:

```python
elif parser == "earley" and lexer == "template":
    return TemplateEarley  # new frontend class
```

**TemplateEarley.__init__(...):**
- Build `parser_conf` as Earley does (same grammar analysis)
- Build and stash:
  - `labels_per_nonterminal` (from step 2)
  - maps for `TREE__LABEL` terminals
  - flag if `PYOBJ` exists
- Create the Earley parser instance with a custom term matcher (next step)

**TemplateEarley.parse(input):**
- If input is Template, call `template_token_stream = tokenize_template(input, ...)`
- Else (plain str), treat as a single static segment (works as normal string input)
- Pass the token stream to Earley: `parser.parse(template_token_stream, start)`

### 4) The Template Tokenizer (The Heart)

Create `tokenize_template(template, ctx)` in `lark/template_mode.py`:

**Inputs:**
- `template`: a Python 3.14 `string.templatelib.Template` instance
- `ctx` provides:
  - access to the grammar's Traditional/Basic Lexer (for static text)
  - filename and a source map for each static segment and interpolation {...}:
    - for each static string: `(file, start_offset, end_offset)`
    - for each interpolation: `(file, expr_start, expr_end)`
  - mapping `label -> TREE__LABEL` terminal names

**Algorithm:**

Iterate `zip(template.strings, template.interpolations)` plus trailing string:

1. **For each static string s:**
   - If empty, skip
   - Lex s using the grammar's lexer with a TextSlice that embeds the absolute offsets so token `.line/.column/.pos` reflect the real file positions
   - Yield all produced tokens (ignore tokens are omitted as usual)

2. **For each interpolation v:**
   - **If v is a Lark Tree:**
     - Determine its top label `lbl = v.data`
     - Lookup the terminal name `tname = "TREE__" + lbl.upper()`
     - Create `Token(tname, v)`, set its meta from `v.meta` if available, else from the interpolation source span
     - Yield the token
   - **Else (v is a Python object, including str):**
     - Require that grammar defines ∏ (i.e., PYOBJ exists); else it will be an unexpected token at parse time (we let Earley report it)
     - Create `Token("PYOBJ", v)`, set meta from the interpolation source span
     - Yield the token

3. After the loop, lex and yield tokens for the final trailing static string (if any)

**Important:** Interpolated Python strings are not merged into adjacent static text; they appear as PYOBJ tokens. If someone wants merging semantics, they should use plain t-strings outside this mode.

### 5) Terminal Matching in Earley

Provide a term matcher to Earley that understands our special terminals:

```python
def term_match(term, token):
    name = term.name
    if name == "PYOBJ":
        # initial version: accept any Python object
        return isinstance(token, Token) and token.type == "PYOBJ"
    if name.startswith("TREE__"):
        # safety: token must be that exact terminal, and its value must be a Tree
        return (isinstance(token, Token)
                and token.type == name
                and isinstance(token.value, Tree)
                and token.value.data == name[len("TREE__"):].lower())
    # normal terminals
    return isinstance(token, Token) and token.type == name
```

No changes to Earley algorithm; we just pass this matcher to `earley.Parser(...)`.

### 6) Splicing Actual Subtrees in the Final Result

After `Earley.parse(...)` returns a tree:

- Replace any `Token("TREE__LABEL", value=<Tree ...>)` child by the underlying Tree
- Thanks to the `expand1=True` injection rules, the extra N wrapper was already collapsed during construction, so the surrounding structure matches what a normal parse would produce

This can be a tiny recursive post-pass:

```python
def splice_inserted_trees(node):
    if isinstance(node, Tree):
        new_children = []
        for ch in node.children:
            if isinstance(ch, Token) and ch.type.startswith("TREE__") and isinstance(ch.value, Tree):
                new_children.append(ch.value)
            else:
                if isinstance(ch, Tree):
                    splice_inserted_trees(ch)
                new_children.append(ch)
        node.children = new_children
```

### 7) Source Locations (Meta) Correctness

**Static tokens:** By lexing with `TextSlice(file_text, start, end)`, tokens get precise `.line`, `.column`, `.pos_in_stream` in the file domain.

**Object tokens (PYOBJ):** Set line/column/start_pos/end_pos from the interpolation expression source span.

**Tree tokens (TREE__LABEL):**
- If the inserted Tree already has `.meta` from a prior parse, copy those fields onto the token (so the parent's meta is computed correctly)
- Otherwise, fall back to the interpolation source span
- Since the injection rule collapses with `expand1=True`, parent node meta is computed from the child, so final meta remains accurate after the splice step

### 8) Errors

**Unexpected object where grammar has no ∏:**
- Earley raises with token position; we can wrap to say: `"Interpolated object at {file}:{line}:{col} not allowed here (no ∏ placeholder in this context)."`

**Tree label not accepted:**
- If a `Tree('foo')` appears where the expected nonterminal N does not list foo in labels(N), the derivation won't match
- Earley error will point to that token's source
- The message can include `"tree label 'foo' not valid for nonterminal 'N'"` if you enhance the error reporter (optional)

As with Lark, parsing stops at the first error by default.

## Edge Cases & Nuances to Account For

- **Consecutive objects** (`… {obj1}{obj2} …`): you'll emit back-to-back PYOBJ/TREE__… tokens. That's fine; grammar must allow them (e.g., via `∏ ∏` or a repetition)

- **Strings as objects:** a Python str interpolation is just another PYOBJ. It does not join with neighbors; grammar must explicitly accept ∏

- **Tree label mapping:** We only accept a spliced Tree by its top label. We do not deep-validate internal shape; we assume it was produced by the same (or compatible) grammar (you can add an optional verifier later)

- **Aliases vs rule names:** Because we compute labels(N) from aliases (`-> label`) and default labels (rule name), injected trees labeled `label` will be accepted where N can produce `label`. This mirrors normal Lark output structure

- **Positions when the Template strips braces:** use the interpolation's {...} expression position for PYOBJ/TREE__… tokens so error arrows point at the expression that supplied the value

## Short Usage Example (Mentally Test the Pipeline)
```lark
// grammar.lark
?start: stmt+

stmt: "print" "(" ∏ ")" ";"
    | expr ";"

?expr: term
     | expr "+" term  -> add
?term: NUMBER
%import common.NUMBER
%ignore " "
```

```python
from lark import Lark, Tree
from string.templatelib import Template  # Python 3.13

parser = Lark(open('grammar.lark').read(), parser="earley", lexer="template")

T = Template(["print(", "", ");"], [42])         # static "print(", PYOBJ=42, static ");"
print(parser.parse(T))                           # OK: matches ∏

# splice an existing subtree (Tree('add', [ ... ])) into an expr position:
sub = Tree('add', [Tree('term', [Token('NUMBER','1')]),
                   Tree('term', [Token('NUMBER','2')])])

T2 = Template(["", ";"], [sub])                  # only a spliced tree + ';'
print(parser.parse(T2))                          # OK even though grammar has no ∏ here
```

## Testing Plan (Minimal but Sufficient)

### Happy Paths

- Static-only template equals plain parse
- ∏ accepts str, int, custom objects as PYOBJ
- Splice `Tree('label')` where N can produce label
- Consecutive placeholders (object, tree, object)

### Errors

- Interpolated object where no ∏ allowed → error at interpolation span
- Tree with label that is not produced by the expected nonterminal → error at interpolation span

### Meta

Verify token and node meta (line/col/start/end) for:
- Static tokens inside first/middle/last segments
- Object token meta points to {...} expression
- Spliced tree meta preserved

### Structure

- Ensure result trees from splicing are shape-identical to normal parses (no extra wrapper nodes) thanks to expand1

## Work Breakdown (Concrete Tasks)

### Grammar Loader

- Parse ∏ → inject `TerminalDef('PYOBJ', PatternPlaceholder())`
- Flag grammar: `uses_python_placeholders`

### Tree-Injection Augmentation

During grammar finalization:
- Build `labels_per_nonterminal`
- Create terminals `TREE__{LABEL}` with `PatternTree(label)`
- Add rules `N: TREE__LABEL` with `RuleOptions.expand1 = True`

### Frontend

- Add `TemplateEarley` in `parser_frontends.py`
- Provide `term_match` as specified
- Route `lexer="template"` to this frontend

### Template Tokenizer

Implement `tokenize_template(template, ctx)`:
- Lex static strings with TextSlice to preserve absolute positions
- Yield `Token('PYOBJ', value)` for objects; `Token('TREE__LABEL', tree)` for trees
- Attach meta as described

### Post-Parse Splice

- Implement `splice_inserted_trees(tree)`; call before returning

### Errors / Messages (Optional Polish)

- Wrap UnexpectedInput to mention "interpolated object/tree" and source span

### Tests

- Create `tests/test_template_mode.py` covering the matrix above

## Notes on Future Enhancements (Out of Scope for First Cut)

- **Typed placeholders:** ∏Type with runtime isinstance checks in term_match
- **Multi-node splicing:** allow an interpolation to produce multiple siblings (would require grammar support or a richer token→sequence mechanism)
- **Multiple-error recovery:** exploit Earley forest to report more than one error

## Done Criteria

`lexer="template"` + `parser="earley"` parses:

a) Template built from static + objects + trees
b) Plain strings (for parity)

Trees spliced via interpolation produce the same shape as normal parses (no stray wrapper nodes).

Source positions on errors and in final tree are accurate for both static and interpolated parts.
