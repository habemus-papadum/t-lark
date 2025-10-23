"""Comprehensive test suite for Template Mode functionality.

This suite focuses on the scenarios highlighted during the review of the
`feat/t-parse-v1` branch while keeping the behaviour of this branch's
implementation intact. The coverage mirrors that suite's structure:

1. Core behaviours around static templates, PYOBJ handling, and tree splicing
2. Error handling and configuration edge cases
3. A graphics DSL demonstrating mixed static content, typed placeholders, and
   tree splicing in more realistic programs
"""

import unittest

from t_lark import Lark, Tree
from t_lark.exceptions import ConfigurationError, UnexpectedToken


class TestTemplateModeBasics(unittest.TestCase):
    """Basic template mode functionality tests."""

    def test_static_only(self):
        """Static template should parse identically to the plain string."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        static = t"42"
        self.assertEqual(parser.parse(static), parser.parse("42"))

    def test_pyobj_untyped(self):
        """PYOBJ should accept arbitrary Python objects without a type map."""
        grammar = r"""
        %import template (PYOBJ)
        start: "value:" PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        self.assertIsNotNone(parser.parse(t"value:{42}"))
        self.assertIsNotNone(parser.parse(t"value:{'hello'}"))
        self.assertIsNotNone(parser.parse(t"value:{[1, 2, 3]}"))

    def test_pyobj_typed_success(self):
        """Typed PYOBJ placeholders should accept objects of the expected type."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'num': int})

        tree = parser.parse(t"{7}")
        self.assertIsInstance(tree, Tree)

    def test_tree_splicing(self):
        """Pre-built trees should splice seamlessly into larger parses."""
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
        """Adjacent placeholders should be handled without extra separators."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        tree = parser.parse(t"{1}{2}")
        self.assertIsInstance(tree, Tree)

    def test_mixed_static_content(self):
        """Mixing static text, tree splicing, and PYOBJ interpolation should work."""
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

    def test_source_info_absent(self):
        """Template objects without source metadata should still parse."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        self.assertIsNotNone(parser.parse(t"{42}"))


class TestTemplateModeErrors(unittest.TestCase):
    """Error handling and configuration coverage."""

    def test_pyobj_typed_type_error(self):
        """Typed placeholders should raise informative errors for mismatched types."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template", pyobj_types={'num': int})

        with self.assertRaises(TypeError) as ctx:
            parser.parse(t"{'not an int'}")

        message = str(ctx.exception)
        self.assertIn("PYOBJ[num]", message)
        self.assertIn("str", message)

    def test_missing_pyobj_types_configuration(self):
        """Using typed PYOBJ without providing a mapping should fail fast."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[image]
        """
        with self.assertRaises(ConfigurationError):
            Lark(grammar, parser="earley", lexer="template")

    def test_error_no_pyobj(self):
        """Interpolating an object where none are allowed should raise."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        with self.assertRaises(UnexpectedToken):
            parser.parse(t"{5}")

    def test_error_wrong_tree_label(self):
        """Splicing a tree the grammar cannot produce should raise."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        wrong = Tree('unknown', [])
        with self.assertRaises((UnexpectedToken, ValueError)) as ctx:
            parser.parse(t"{wrong}")

        self.assertIn("unknown", str(ctx.exception))

    def test_requires_earley_parser(self):
        """Template mode requires the Earley parser backend."""
        grammar = 'start: "x"'
        with self.assertRaises(ConfigurationError):
            Lark(grammar, parser="lalr", lexer="template")


class TestPaintDSL(unittest.TestCase):
    """Realistic DSL exercising typed placeholders, splicing, and errors."""

    class Image:
        def __init__(self, path: str) -> None:
            self.path = path

        def __repr__(self) -> str:  # pragma: no cover - debug helper
            return f"Image({self.path!r})"

        def __eq__(self, other) -> bool:
            return isinstance(other, type(self)) and self.path == other.path

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

    def test_static_only_parse(self):
        """Purely static template should parse into one object with colors."""
        program = t"object {{ stroke: 255,0,0 fill: 0,255,0 }}"
        tree = self.parser.parse(program, start='start')

        self.assertEqual(tree.data, 'start')
        self.assertEqual(len(tree.children), 1)

        obj = tree.children[0]
        self.assertEqual(obj.data, 'object')

        stroke_paint = obj.children[0]
        fill_paint = obj.children[1]
        self.assertEqual(stroke_paint.children[0].data, 'color')
        self.assertEqual(fill_paint.children[0].data, 'color')

    def test_interpolate_image_objects(self):
        """Interpolated Image objects should surface as typed tokens."""
        texture = self.Image("wood_texture.png")
        gradient = self.Image("gradient.png")

        program = t"object {{ stroke: {texture} fill: {gradient} }}"
        tree = self.parser.parse(program, start='start')

        obj = tree.children[0]
        stroke_image = obj.children[0].children[0]
        fill_image = obj.children[1].children[0]

        self.assertEqual(stroke_image.data, 'image')
        self.assertEqual(fill_image.data, 'image')

        stroke_token = stroke_image.children[0]
        fill_token = fill_image.children[0]

        self.assertEqual(stroke_token.type, 'PYOBJ__IMAGE')
        self.assertEqual(fill_token.type, 'PYOBJ__IMAGE')
        self.assertIs(stroke_token.value, texture)
        self.assertIs(fill_token.value, gradient)

    def test_type_mismatch_error(self):
        """Passing an incompatible type should raise TypeError with context."""
        program = t"object {{ stroke: {'not an image'} fill: 0,0,255 }}"

        with self.assertRaises(TypeError) as ctx:
            self.parser.parse(program, start='start')

        message = str(ctx.exception)
        self.assertIn("PYOBJ[image]", message)
        self.assertIn("str", message)

    def test_type_mismatch_integer(self):
        """Type mismatch should report the offending integer type."""
        program = t"object {{ stroke: {42} fill: 0,0,0 }}"

        with self.assertRaises(TypeError) as ctx:
            self.parser.parse(program, start='start')

        message = str(ctx.exception)
        self.assertIn("PYOBJ[image]", message)
        self.assertIn("int", message)

    def test_tree_splicing_color(self):
        """Colors parsed separately can be spliced into paint positions."""
        red = self.parser.parse("255,0,0", start='color')
        program = t"object {{ stroke: {red} fill: {red} }}"

        tree = self.parser.parse(program, start='start')
        obj = tree.children[0]

        stroke_color = obj.children[0].children[0]
        fill_color = obj.children[1].children[0]
        self.assertEqual(stroke_color.data, 'color')
        self.assertEqual(fill_color.data, 'color')

    def test_tree_splicing_with_control_flow(self):
        """Control flow assembling templates should still produce valid trees."""
        red = self.parser.parse("255,0,0", start='color')
        blue = self.parser.parse("0,0,255", start='color')
        green = self.parser.parse("0,255,0", start='color')

        colors = [red, green, blue]
        palette = []

        for index, color in enumerate(colors):
            if index % 2 == 0:
                palette.append(t"object {{ stroke: {color} fill: {color} }}")
            else:
                palette.append(t"object {{ stroke: {color} fill: 0,0,0 }}")

        program = palette[0]
        for fragment in palette[1:]:
            program = program + fragment
        tree = self.parser.parse(program, start='start')

        self.assertEqual(len(tree.children), 3)
        first = tree.children[0]
        second = tree.children[1]

        self.assertEqual(first.children[0].children[0].data, 'color')
        self.assertEqual(first.children[1].children[0].data, 'color')
        self.assertEqual(second.children[0].children[0].data, 'color')
        self.assertEqual(second.children[1].children[0].data, 'color')

    def test_mixed_static_object_tree(self):
        """Mix static segments, spliced trees, and interpolated images."""
        red = self.parser.parse("255,0,0", start='color')
        texture = self.Image("pattern.png")

        program = (
            t"object {{ stroke: 128,128,128 fill: 64,64,64 }} "
            + t"object {{ stroke: {red} fill: {texture} }} "
            + t"object {{ stroke: {texture} fill: 0,255,128 }}"
        )

        tree = self.parser.parse(program, start='start')
        self.assertEqual(len(tree.children), 3)

        obj1, obj2, obj3 = tree.children
        self.assertEqual(obj1.children[0].children[0].data, 'color')
        self.assertEqual(obj1.children[1].children[0].data, 'color')
        self.assertEqual(obj2.children[0].children[0].data, 'color')
        self.assertEqual(obj2.children[1].children[0].data, 'image')
        self.assertEqual(obj3.children[0].children[0].data, 'image')
        self.assertEqual(obj3.children[1].children[0].data, 'color')

    def test_error_wrong_tree_label(self):
        """Splicing an unsupported tree should raise with the label mentioned."""
        wrong_tree = Tree('circle', [])
        program = t"object {{ stroke: {wrong_tree} fill: 0,0,0 }}"

        with self.assertRaises(ValueError) as ctx:
            self.parser.parse(program, start='start')

        self.assertIn("circle", str(ctx.exception))

    def test_error_malformed_static_syntax(self):
        """Static syntax errors should be propagated as UnexpectedToken."""
        program = t"object {{ stroke: 255,0 fill: 0,0,0 }}"

        with self.assertRaises(UnexpectedToken):
            self.parser.parse(program, start='start')

    def test_multiple_objects_mixed_paints(self):
        """Larger programs mixing colors and images should parse cleanly."""
        red = self.parser.parse("255,0,0", start='color')
        img1 = self.Image("texture1.png")
        img2 = self.Image("texture2.png")

        program = (
            t"object {{ stroke: 255,255,255 fill: 0,0,0 }} "
            + t"object {{ stroke: {red} fill: 100,100,100 }} "
            + t"object {{ stroke: {img1} fill: {img2} }} "
            + t"object {{ stroke: 50,50,50 fill: {red} }} "
            + t"object {{ stroke: {img1} fill: 200,200,200 }}"
        )

        tree = self.parser.parse(program, start='start')
        self.assertEqual(len(tree.children), 5)

        for obj in tree.children:
            self.assertEqual(obj.data, 'object')
            self.assertEqual(len(obj.children), 2)
            for paint in obj.children:
                kind = paint.children[0].data
                self.assertIn(kind, {'color', 'image'})

    def test_image_object_preservation(self):
        """Image objects should be preserved verbatim through the parse."""
        image = self.Image("test.png")
        program = t"object {{ stroke: {image} fill: 0,0,0 }}"

        tree = self.parser.parse(program, start='start')
        obj = tree.children[0]
        stroke_image = obj.children[0].children[0]
        image_token = stroke_image.children[0]

        self.assertIs(image_token.value, image)
        self.assertEqual(image_token.value.path, "test.png")

    def test_static_equivalent_to_plain_string(self):
        """Static-only template should match parsing the plain string."""
        tmpl_tree = self.parser.parse(t"object {{ stroke: 255,0,0 fill: 0,255,0 }}", start='start')
        plain_tree = self.parser.parse("object { stroke: 255,0,0 fill: 0,255,0 }", start='start')

        self.assertEqual(tmpl_tree.data, plain_tree.data)
        self.assertEqual(len(tmpl_tree.children), len(plain_tree.children))


if __name__ == '__main__':
    unittest.main()
