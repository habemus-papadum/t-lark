from dataclasses import dataclass

import pytest

from lark import Lark, Tree
from lark.exceptions import ConfigurationError, UnexpectedToken
from lark.template_mode import SourceInfo


def make_parser(grammar: str, **kwargs) -> Lark:
    return Lark(grammar, parser="earley", lexer="template", **kwargs)


def test_static_only_parses_like_plain_string():
    grammar = """
    start: NUMBER
    %import common.NUMBER
    """
    parser = make_parser(grammar)

    template_value = t"42"
    tree_from_template = parser.parse(template_value)
    tree_from_string = parser.parse("42")

    assert tree_from_template == tree_from_string


def test_pyobj_untyped_accepts_various_objects():
    grammar = """
    %import template (PYOBJ)
    start: "value:" PYOBJ
    """
    parser = make_parser(grammar)

    assert parser.parse(t"value:{42}")
    assert parser.parse(t"value:{'hello'}")
    assert parser.parse(t"value:{[1, 2, 3]}")


def test_pyobj_typed_validates_types():
    grammar = """
    %import template (PYOBJ)
    start: PYOBJ[num]
    """
    parser = make_parser(grammar, pyobj_types={'num': int})

    assert parser.parse(t"{7}")
    with pytest.raises(TypeError):
        parser.parse(t"{'not a number'}")


def test_pyobj_typed_requires_mapping():
    grammar = """
    %import template (PYOBJ)
    start: PYOBJ[num]
    """
    with pytest.raises(ConfigurationError):
        make_parser(grammar)


def test_tree_splicing_merges_subtrees():
    grammar = r"""
    ?start: expr
    ?expr: term
         | expr "+" term -> add
    ?term: NUMBER
    %import common.NUMBER
    %ignore /\s+/
    """
    parser = make_parser(grammar)

    sub_tree = parser.parse("1 + 2")
    result = parser.parse(t"{sub_tree}")
    assert isinstance(result, Tree)
    assert result.data == 'add'
    assert result == sub_tree


def test_consecutive_placeholders():
    grammar = """
    %import template (PYOBJ)
    start: PYOBJ PYOBJ
    """
    parser = make_parser(grammar)
    tree = parser.parse(t"{1}{2}")
    assert isinstance(tree, Tree)


def test_mixed_static_object_and_tree():
    grammar = """
    %import template (PYOBJ)
    start: "static" PYOBJ expr
    expr: NUMBER
    %import common.NUMBER
    %ignore " "
    """
    parser = make_parser(grammar)

    expr_parser = make_parser(grammar, start='expr')
    sub_tree = expr_parser.parse("42")
    tree = parser.parse(t"static {100} {sub_tree}")
    assert tree.children[0].type == 'PYOBJ'
    assert tree.children[0].value == 100
    assert tree.children[1] == sub_tree


def test_error_when_no_pyobj_placeholder():
    grammar = """
    start: NUMBER
    %import common.NUMBER
    """
    parser = make_parser(grammar)

    with pytest.raises(UnexpectedToken):
        parser.parse(t"{42}")


def test_template_requires_earley_parser():
    grammar = 'start: "x"'
    with pytest.raises(ConfigurationError):
        Lark(grammar, parser="lalr", lexer="template")


def test_tree_label_mismatch_raises():
    grammar = """
    start: NUMBER
    %import common.NUMBER
    """
    parser = make_parser(grammar)

    wrong_tree = Tree('expr', [])
    with pytest.raises((UnexpectedToken, ValueError)):
        parser.parse(t"{wrong_tree}")


def test_source_info_positions_propagate():
    grammar = """
    %import template (PYOBJ)
    start: "value:" PYOBJ
    """
    parser = make_parser(grammar)

    value = 42
    template = t"value:{value}"
    proxy = type('TemplateProxy', (), {})()
    proxy.strings = template.strings
    proxy.interpolations = template.interpolations
    proxy.source_info = SourceInfo(
        filename="test.tmpl",
        text="value:{42}",
        segment_spans=[(0, 6), (9, 9)],
        interpolation_spans=[(6, 9)],
    )

    tree = parser.parse(proxy)
    token = tree.children[0]
    assert token.line == 1
    assert token.column == 7
    assert token.end_column == 10


@dataclass
class Image:
    path: str


def test_comprehensive_dsl_with_typed_objects():
    grammar = r"""
    %import template (PYOBJ)
    start: object+

    object: "object" "{" "stroke:" paint "fill:" paint "}"

    paint: color
         | image

    color: NUMBER "," NUMBER "," NUMBER  -> color

    image: PYOBJ[image]  -> image

    %import common.NUMBER
    %ignore /[ \t\n]+/
    """
    parser = make_parser(grammar, pyobj_types={'image': Image})
    color_parser = make_parser(grammar, start='color', pyobj_types={'image': Image})
    red = color_parser.parse("255,0,0")
    texture = Image("wood.png")

    program = t"object {{ stroke: 255,0,0 fill: 0,255,0 }}\nobject {{ stroke: {texture} fill: {red} }}"

    tree = parser.parse(program)
    assert len(tree.children) == 2
    obj_static = tree.children[0]
    assert [child.children[0].data for child in obj_static.children] == ['color', 'color']

    obj_mixed = tree.children[1]
    assert [child.children[0].data for child in obj_mixed.children] == ['image', 'color']

    token = obj_mixed.children[0].children[0].children[0]
    assert token.value is texture
