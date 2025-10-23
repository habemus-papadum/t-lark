import pytest
from string.templatelib import Template

from lark import Lark, Tree
from lark.exceptions import ConfigurationError, UnexpectedInput
from lark.template_mode import SourceInfo


class DummyImage:
    pass


def test_static_only_template_parses_like_plain_string():
    grammar = r"""
    start: NUMBER
    %import common.NUMBER
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    static_template = t"42"
    assert parser.parse(static_template) == parser.parse("42")


def test_untyped_pyobj_accepts_arbitrary_objects():
    grammar = r"""
    %import template (PYOBJ)
    start: "value:" PYOBJ
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    assert parser.parse(t"value:{42}")
    assert parser.parse(t"value:{'text'}")

    sample = {1, 2, 3}
    assert parser.parse(t"value:{sample}")


def test_typed_pyobj_enforces_types():
    grammar = r"""
    %import template (PYOBJ)
    start: PYOBJ[num]
    """
    parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'num': int})

    parser.parse(t"{10}")
    with pytest.raises(TypeError):
        parser.parse(t"{'oops'}")


def test_tree_splicing_produces_equivalent_tree():
    grammar = r"""
    ?start: expr
    ?expr: term
         | expr "+" term   -> add
    ?term: NUMBER
    %import common.NUMBER
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    subtree = parser.parse("1+2")
    result = parser.parse(t"{subtree}")
    assert isinstance(result, Tree)
    assert result == subtree


def test_objects_without_pyobj_placeholder_error():
    grammar = r"""
    start: NUMBER
    %import common.NUMBER
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    with pytest.raises(UnexpectedInput):
        parser.parse(t"{5}")


def test_tree_with_unknown_label_errors():
    grammar = r"""
    start: NUMBER
    %import common.NUMBER
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    wrong_tree = Tree('unknown', [])
    with pytest.raises(ValueError):
        parser.parse(t"{wrong_tree}")


def test_template_requires_earley_parser():
    grammar = 'start: "x"'
    with pytest.raises(ConfigurationError):
        Lark(grammar, parser="lalr", lexer="template")


def test_typed_placeholder_type_error_message():
    grammar = r"""
    %import template (PYOBJ)
    start: PYOBJ[item]
    """
    parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'item': list})

    with pytest.raises(TypeError) as exc:
        parser.parse(t"{42}")
    assert 'PYOBJ[item]' in str(exc.value)


def test_source_info_metadata_preserved():
    grammar = r"""
    %import template (PYOBJ)
    start: "hello" PYOBJ
    %ignore " "
    """
    parser = Lark(grammar, parser="earley", lexer="template")

    value = 123
    text = "hello {value}"
    base_template = t"hello {value}"

    info = SourceInfo(
        filename="example.t",
        text=text,
        segment_spans=[(0, 6), (13, 13)],
        interpolation_spans=[(6, 13)],
    )

    class TemplateWithInfo:
        def __init__(self, template, source_info):
            self.strings = template.strings
            self.interpolations = template.interpolations
            self.source_info = source_info

    tmpl = TemplateWithInfo(base_template, info)

    tree = parser.parse(tmpl)
    assert len(tree.children) == 1
    token = tree.children[0]
    assert token.line == 1
    assert token.column == 7
    assert token.end_column == 14


def test_mixed_typed_objects_and_spliced_trees():
    grammar = r"""
    %import template (PYOBJ)
    start: command+

    command: "show" PYOBJ[img]
           | expr

    ?expr: term
         | expr "+" term   -> add
    ?term: NUMBER

    %import common.NUMBER
    %ignore " "
    """
    parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'img': DummyImage})

    image = DummyImage()
    show_command = t"show {image}"
    expr_tree = parser.parse("1+2").children[0]
    program = show_command + t"{expr_tree}"

    tree = parser.parse(program)
    assert len(tree.children) == 2

    show_node = tree.children[0]
    assert show_node.data == 'command'
    img_token = show_node.children[0]
    assert isinstance(img_token.value, DummyImage)

    expr_node = tree.children[1]
    assert isinstance(expr_node, Tree)
    add_node = expr_node.children[0]
    assert add_node.data == 'add'
