import unittest

from lark import Lark, Tree
from lark.exceptions import ConfigurationError, UnexpectedToken


class TestTemplateMode(unittest.TestCase):
    def test_static_only(self):
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        self.assertEqual(parser.parse(t"42"), parser.parse("42"))

    def test_pyobj_untyped(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "value:" PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        parser.parse(t"value:{42}")
        parser.parse(t"value:{'string'}")
        parser.parse(t"value:{[1, 2, 3]}")

    def test_pyobj_typed(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(
            grammar,
            parser="earley",
            lexer="template",
            pyobj_types={'num': int},
        )

        parser.parse(t"{42}")
        with self.assertRaises(TypeError):
            parser.parse(t"{'not a number'}")

    def test_tree_splicing(self):
        grammar = r"""
        %import template (PYOBJ)
        ?start: expr
        ?expr: term
             | expr "+" term -> add
        ?term: NUMBER
        %import common.NUMBER
        %import common.WS_INLINE
        %ignore WS_INLINE
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        sub = parser.parse("1 + 2")
        result = parser.parse(t"{sub}")
        self.assertEqual(result.data, 'add')

    def test_consecutive_objects(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        parser.parse(t"{1}{2}")

    def test_mixed_content(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "static" PYOBJ expr
        expr: NUMBER
        %import common.NUMBER
        %import common.WS_INLINE
        %ignore WS_INLINE
        """
        parser = Lark(
            grammar,
            parser="earley",
            lexer="template",
            start=["start", "expr"],
        )

        sub = parser.parse("42", start='expr')
        parser.parse(t"static {100} {sub}", start='start')

    def test_error_no_pyobj(self):
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        with self.assertRaises(UnexpectedToken):
            parser.parse(t"{42}")

    def test_error_wrong_tree_label(self):
        grammar = r"""
        %import template (PYOBJ)
        start: expr
        expr: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        wrong_tree = Tree('wrong_label', [])
        with self.assertRaises((UnexpectedToken, ValueError)):
            parser.parse(t"{wrong_tree}")

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

        parser.parse(t"{42}")


if __name__ == '__main__':
    unittest.main()
