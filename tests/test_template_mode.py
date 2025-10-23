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

        static = t"42"
        tree_from_template = parser.parse(static)
        tree_from_plain = parser.parse("42")

        self.assertEqual(tree_from_template, tree_from_plain)

    def test_pyobj_untyped(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "value:" PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        self.assertIsNotNone(parser.parse(t"value:{42}"))
        self.assertIsNotNone(parser.parse(t"value:{'hello'}"))
        self.assertIsNotNone(parser.parse(t"value:{[1, 2, 3]}"))

    def test_pyobj_typed_success(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'num': int})

        result = parser.parse(t"{7}")
        self.assertIsInstance(result, Tree)

    def test_pyobj_typed_type_error(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'num': int})

        with self.assertRaises(TypeError):
            parser.parse(t"{'not an int'}")

    def test_missing_pyobj_types_configuration(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[image]
        """
        with self.assertRaises(ConfigurationError):
            Lark(grammar, parser="earley", lexer="template")

    def test_tree_splicing(self):
        grammar = r"""
        ?start: expr
        ?expr: term
             | expr "+" term -> add
        ?term: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template", start=['start', 'expr'])

        fragment = parser.parse("1+2", start='expr')
        self.assertEqual(fragment.data, 'add')

        combined = parser.parse(t"{fragment}", start='start')
        self.assertEqual(combined.data, 'add')

    def test_consecutive_objects(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        tree = parser.parse(t"{1}{2}")
        self.assertIsInstance(tree, Tree)

    def test_mixed_static_content(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "static" PYOBJ expr
        expr: NUMBER
        %import common.NUMBER
        %ignore " "
        """
        parser = Lark(grammar, parser="earley", lexer="template", start=['start', 'expr'])

        chunk = parser.parse("42", start='expr')
        result = parser.parse(t"static {100} {chunk}", start='start')
        self.assertEqual(result.data, 'start')

    def test_error_no_pyobj(self):
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        with self.assertRaises(UnexpectedToken):
            parser.parse(t"{5}")

    def test_error_wrong_tree_label(self):
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        wrong = Tree('unknown', [])
        with self.assertRaises((UnexpectedToken, ValueError)):
            parser.parse(t"{wrong}")

    def test_requires_earley_parser(self):
        grammar = 'start: "x"'
        with self.assertRaises(ConfigurationError):
            Lark(grammar, parser="lalr", lexer="template")


class TestPaintDSL(unittest.TestCase):
    class Image:
        def __init__(self, path: str) -> None:
            self.path = path

    GRAMMAR = r"""
    %import template (PYOBJ)

    start: object+

    object: "object" "{" "stroke:" paint "fill:" paint "}"

    paint: color
         | image

    color: NUMBER "," NUMBER "," NUMBER -> color

    image: PYOBJ[image] -> image

    %import common.NUMBER
    %ignore " "
    """

    def setUp(self):
        self.parser = Lark(
            self.GRAMMAR,
            parser="earley",
            lexer="template",
            pyobj_types={'image': self.Image},
            start=['start', 'color'],
        )

    def test_interpolate_images(self):
        tex = self.Image("tex.png")
        pat = self.Image("pattern.png")

        tree = self.parser.parse(t"object {{ stroke: {tex} fill: {pat} }}", start='start')

        obj = tree.children[0]
        stroke = obj.children[0].children[0]
        fill = obj.children[1].children[0]

        stroke_token = stroke.children[0]
        fill_token = fill.children[0]

        self.assertEqual(stroke_token.type, 'PYOBJ__IMAGE')
        self.assertIs(stroke_token.value, tex)
        self.assertEqual(fill_token.type, 'PYOBJ__IMAGE')
        self.assertIs(fill_token.value, pat)

    def test_type_mismatch_raises(self):
        with self.assertRaises(TypeError) as ctx:
            self.parser.parse(t"object {{ stroke: {'not an image'} fill: 0,0,0 }}", start='start')

        self.assertIn('Expected Image', str(ctx.exception))

    def test_tree_splicing(self):
        red = self.parser.parse("255,0,0", start='color')
        program = t"object {{ stroke: {red} fill: {red} }}"

        tree = self.parser.parse(program, start='start')
        obj = tree.children[0]
        self.assertEqual(obj.children[0].children[0].data, 'color')
        self.assertEqual(obj.children[1].children[0].data, 'color')


if __name__ == '__main__':
    unittest.main()
