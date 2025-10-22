import unittest

from dataclasses import dataclass

from lark import Lark, Tree
from lark.exceptions import ConfigurationError, UnexpectedToken
from lark.template_mode import SourceInfo


class TestTemplateMode(unittest.TestCase):
    def test_static_only(self):
        grammar = r"""
        start: "42"
        """
        parser = Lark(grammar, parser="earley", lexer="template", start=['start', 'expr'])

        template = eval('t"42"')
        result = parser.parse(template)
        self.assertEqual(result, parser.parse("42"))

    def test_pyobj_untyped(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "value:" PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        self.assertIsNotNone(parser.parse(eval('t"value:{42}"')))
        self.assertIsNotNone(parser.parse(eval('t"value:{\'string\'}"')))
        self.assertIsNotNone(parser.parse(eval('t"value:{[1,2,3]}"')))

    def test_pyobj_typed(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'num': int})

        parser.parse(eval('t"{42}"'))
        with self.assertRaises(TypeError):
            parser.parse(eval('t"{\'not a number\'}"'))

    def test_tree_splicing(self):
        grammar = r"""
        ?start: expr
        ?expr: term
            | expr "+" term -> add
        ?term: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        sub = parser.parse("1+2")
        result = parser.parse(eval('t"{sub}"'))
        self.assertIsInstance(result, Tree)
        self.assertEqual(result.data, 'add')

    def test_consecutive_objects(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        template = eval('t"{1}{2}"')
        self.assertIsNotNone(parser.parse(template))

    def test_mixed_content(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "static" PYOBJ expr
        expr: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        sub = parser.parse("42", start='expr')
        template = eval('t"static {100} {sub}"')
        self.assertIsNotNone(parser.parse(template))

    def test_error_no_pyobj(self):
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        with self.assertRaises(UnexpectedToken):
            parser.parse(eval('t"{42}"'))

    def test_error_wrong_tree_label(self):
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        bad_tree = Tree('other', [])
        with self.assertRaises(ValueError):
            parser.parse(eval('t"{bad_tree}"'))

    def test_requires_earley(self):
        grammar = 'start: "x"'
        with self.assertRaises(ConfigurationError):
            Lark(grammar, parser="lalr", lexer="template")

    def test_source_info_absent(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        template = eval('t"{42}"')
        self.assertIsNotNone(parser.parse(template))

    def test_source_info_meta(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "a" PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template", propagate_positions=True)

        text = "a{value}"
        template = eval('t"a{123}"')
        info = SourceInfo(
            filename="example",
            text=text,
            segment_spans=[(0, 1), (7, 7)],
            interpolation_spans=[(1, 6)],
        )

        tree = parser.parse(TemplateWithSource(template, info))
        self.assertEqual(tree.meta.start_pos, 0)
        self.assertEqual(tree.meta.end_pos, 7)


if __name__ == '__main__':
    unittest.main()
@dataclass
class TemplateWithSource:
    strings: tuple
    interpolations: tuple
    source_info: SourceInfo

    def __init__(self, template, source_info):
        object.__setattr__(self, 'strings', template.strings)
        object.__setattr__(self, 'interpolations', template.interpolations)
        object.__setattr__(self, 'source_info', source_info)
