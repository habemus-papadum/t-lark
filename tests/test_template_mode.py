import pytest

from lark import Lark, Tree
from lark.exceptions import ConfigurationError, UnexpectedToken


def make_template(src: str, **context):
    env = {'__builtins__': __builtins__}
    env.update(context)
    return eval("t" + repr(src), env)


def test_static_only_template():
    grammar = "start: NUMBER\n%import common.NUMBER"
    parser = Lark(grammar, parser="earley", lexer="template")

    template = make_template("42")
    assert parser.parse(template) == parser.parse("42")


def test_pyobj_untyped():
    grammar = r"""
    %import template (PYOBJ)
    start: "value:" PYOBJ
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    assert parser.parse(make_template("value:{42}"))
    assert parser.parse(make_template("value:{'abc'}"))
    assert parser.parse(make_template("value:{[1, 2, 3]}"))


def test_pyobj_typed_enforces_mapping():
    grammar = r"""
    %import template (PYOBJ)
    start: PYOBJ[num]
    """
    parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'num': int})

    parser.parse(make_template("{5}"))

    with pytest.raises(TypeError):
        parser.parse(make_template("{'bad'}"))


def test_tree_splicing():
    grammar = r"""
    %import template (PYOBJ)
    ?start: expr
    ?expr: term
         | expr "+" term   -> add
    ?term: NUMBER
    %import common.NUMBER
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    subtree = parser.parse("1+2")
    template = make_template("{subtree}", subtree=subtree)
    parsed = parser.parse(template)
    assert isinstance(parsed, Tree)
    assert parsed.data == 'add'


def test_consecutive_interpolations():
    grammar = r"""
    %import template (PYOBJ)
    start: PYOBJ PYOBJ
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    template = make_template("{1}{2}")
    assert parser.parse(template)


def test_mixed_content_tree_and_object():
    grammar = r"""
    %import template (PYOBJ)
    start: "static" PYOBJ expr
    expr: NUMBER
    %import common.NUMBER
    %import common.WS_INLINE
    %ignore WS_INLINE
    """
    parser = Lark(grammar, parser="earley", lexer="template")
    expr_parser = Lark(grammar, parser="earley", lexer="template", start='expr')

    subtree = expr_parser.parse("99")
    template = make_template("static {value} {tree}", value=123, tree=subtree)
    assert parser.parse(template)


def test_error_without_pyobj_import():
    grammar = "start: NUMBER\n%import common.NUMBER"
    parser = Lark(grammar, parser="earley", lexer="template")

    with pytest.raises(UnexpectedToken):
        parser.parse(make_template("{7}"))


def test_error_wrong_tree_label():
    grammar = "start: NUMBER\n%import common.NUMBER"
    parser = Lark(grammar, parser="earley", lexer="template")

    bad_tree = Tree('other', [])
    with pytest.raises((UnexpectedToken, ValueError)):
        parser.parse(make_template("{bad_tree}", bad_tree=bad_tree))


def test_requires_earley_parser():
    grammar = 'start: "x"'
    with pytest.raises(ConfigurationError):
        Lark(grammar, parser="lalr", lexer="template")


def test_plain_string_parsing():
    grammar = "start: NUMBER\n%import common.NUMBER"
    parser = Lark(grammar, parser="earley", lexer="template")
    assert parser.parse("5") == parser.parse(make_template("5"))
