"""
Comprehensive test suite for Template Mode functionality.

Tests cover:
1. Happy paths - static, untyped PYOBJ, typed PYOBJ, tree splicing, consecutive placeholders, mixed content
2. Error cases - object where no PYOBJ allowed, wrong tree label, type mismatch, requires Earley parser
3. Graphics DSL comprehensive example (TestPaintDSL class)

This test suite validates the Template Mode implementation as specified in
docs/template_parsing_implementation.md
"""

import unittest
from string.templatelib import Template
from lark import Lark, Tree, Token
from lark.exceptions import GrammarError, ConfigurationError, UnexpectedToken


class TestTemplateModeBasics(unittest.TestCase):
    """Basic template mode functionality tests."""

    def test_static_only(self):
        """Static template should parse like plain string."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        result = parser.parse(t"42")
        self.assertEqual(result, parser.parse("42"))

    def test_pyobj_untyped(self):
        """PYOBJ should accept any Python object."""
        grammar = r"""
        %import template (PYOBJ)
        start: "value:" PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Test various types
        self.assertIsNotNone(parser.parse(t"value:{42}"))
        self.assertIsNotNone(parser.parse(t"value:{'string'}"))
        self.assertIsNotNone(parser.parse(t"value:{[1,2,3]}"))

    def test_pyobj_typed(self):
        """PYOBJ[typename] should validate types."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template",
                      pyobj_types={'num': int})

        # Should work
        parser.parse(t"{42}")

        # Should fail
        with self.assertRaises(TypeError):
            parser.parse(t"{'not a number'}")

    def test_tree_splicing(self):
        """Pre-built trees should splice seamlessly."""
        grammar = r"""
        ?start: expr
        ?expr: term
             | expr "+" term  -> add
        ?term: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Parse a fragment
        sub = parser.parse("1 + 2")

        # Splice it
        result = parser.parse(t"{sub}")
        self.assertEqual(result.data, 'add')

    def test_consecutive_objects(self):
        """Consecutive interpolations should work."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        result = parser.parse(t"{1}{2}")
        self.assertIsNotNone(result)

    def test_mixed_content(self):
        """Mix of static, objects, and trees."""
        grammar = r"""
        %import template (PYOBJ)
        start: "static" PYOBJ expr
        expr: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        sub = parser.parse("42")
        result = parser.parse(t"static {100} {sub}")
        self.assertIsNotNone(result)


class TestTemplateModeErrors(unittest.TestCase):
    """Error handling tests for template mode."""

    def test_error_no_pyobj(self):
        """Object where no PYOBJ allowed should error."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        with self.assertRaises(UnexpectedToken):
            parser.parse(t"{42}")

    def test_error_wrong_tree_label(self):
        """Tree with wrong label should error."""
        grammar = r"""
        start: expr
        expr: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Create tree with label grammar doesn't produce
        wrong_tree = Tree('wrong_label', [])

        with self.assertRaises((UnexpectedToken, ValueError)):
            parser.parse(t"{wrong_tree}")

    def test_error_type_mismatch(self):
        """Type mismatch for typed placeholder should error."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template",
                      pyobj_types={'num': int})

        with self.assertRaises(TypeError):
            parser.parse(t"{'not an int'}")

    def test_requires_earley(self):
        """Template mode should require Earley parser."""
        grammar = "start: 'x'"

        with self.assertRaises(ConfigurationError):
            Lark(grammar, parser="lalr", lexer="template")

    def test_source_info_absent(self):
        """Should work without source_info."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Template without source_info
        result = parser.parse(t"{42}")
        self.assertIsNotNone(result)


class TestPaintDSL(unittest.TestCase):
    """Comprehensive tests for graphics DSL with paint abstraction.

    This test suite demonstrates a realistic use case where a DSL supports
    both static color specifications and interpolated Python objects, as well
    as tree splicing for metaprogramming.
    """

    # Mock Image class for testing
    class Image:
        def __init__(self, path):
            self.path = path
        def __repr__(self):
            return f"Image({self.path!r})"
        def __eq__(self, other):
            return isinstance(other, TestPaintDSL.Image) and self.path == other.path

    GRAPHICS_GRAMMAR = r"""
    %import template (PYOBJ)

    start: object+

    object: "object" "{" "stroke:" paint "fill:" paint "}"

    paint: color
         | image

    color: NUMBER "," NUMBER "," NUMBER  -> color

    image: PYOBJ[image]  -> image

    %import common.NUMBER
    %ignore " "
    """

    def setUp(self):
        """Create parser with Image type mapping."""
        self.parser = Lark(
            self.GRAPHICS_GRAMMAR,
            parser="earley",
            lexer="template",
            pyobj_types={'image': self.Image}
        )

    def test_static_only_parse(self):
        """Pure static string with no interpolations."""
        program = t"object { stroke: 255,0,0 fill: 0,255,0 }"
        tree = self.parser.parse(program)

        # Should have one object
        self.assertEqual(tree.data, 'start')
        self.assertEqual(len(tree.children), 1)

        obj = tree.children[0]
        self.assertEqual(obj.data, 'object')

        # Both paints should be colors
        stroke_paint = obj.children[0]
        fill_paint = obj.children[1]
        self.assertEqual(stroke_paint.data, 'color')
        self.assertEqual(fill_paint.data, 'color')

    def test_interpolate_image_objects(self):
        """Interpolate Image objects via typed PYOBJ placeholders."""
        texture = self.Image("wood_texture.png")
        gradient = self.Image("gradient.png")

        program = t"object { stroke: {texture} fill: {gradient} }"
        tree = self.parser.parse(program)

        obj = tree.children[0]
        stroke_paint = obj.children[0]
        fill_paint = obj.children[1]

        # Both should be image nodes
        self.assertEqual(stroke_paint.data, 'image')
        self.assertEqual(fill_paint.data, 'image')

        # Extract the Image objects from tokens
        stroke_token = stroke_paint.children[0]
        fill_token = fill_paint.children[0]

        self.assertEqual(stroke_token.type, 'PYOBJ__IMAGE')
        self.assertEqual(fill_token.type, 'PYOBJ__IMAGE')

        self.assertEqual(stroke_token.value, texture)
        self.assertEqual(fill_token.value, gradient)

    def test_type_mismatch_error(self):
        """PYOBJ[image] should reject non-Image types."""
        program = t"object { stroke: {'not an image'} fill: 0,0,255 }"

        with self.assertRaises(TypeError) as ctx:
            self.parser.parse(program)

        self.assertIn('Expected Image', str(ctx.exception))
        self.assertIn('got str', str(ctx.exception))

    def test_type_mismatch_integer(self):
        """PYOBJ[image] should reject integers."""
        program = t"object { stroke: {42} fill: 0,0,0 }"

        with self.assertRaises(TypeError) as ctx:
            self.parser.parse(program)

        self.assertIn('Expected Image', str(ctx.exception))
        self.assertIn('got int', str(ctx.exception))

    def test_tree_splicing_color(self):
        """Parse color fragment, then splice it into paint position."""
        # Parse color independently
        red = self.parser.parse("255,0,0")
        self.assertEqual(red.data, 'color')

        # Splice into object
        program = t"object { stroke: {red} fill: {red} }"
        tree = self.parser.parse(program)

        obj = tree.children[0]
        stroke_paint = obj.children[0]
        fill_paint = obj.children[1]

        # Both should be color nodes (spliced)
        self.assertEqual(stroke_paint.data, 'color')
        self.assertEqual(fill_paint.data, 'color')

    def test_tree_splicing_with_control_flow(self):
        """Use Python control flow to build program with spliced trees."""
        red = self.parser.parse("255,0,0")
        blue = self.parser.parse("0,0,255")
        green = self.parser.parse("0,255,0")

        colors = [red, green, blue]
        palette = []

        for i, color in enumerate(colors):
            if i % 2 == 0:
                palette.append(t"object { stroke: {color} fill: {color} }")
            else:
                palette.append(t"object { stroke: {color} fill: 0,0,0 }")

        program = t"".join(palette)
        tree = self.parser.parse(program)

        # Should have 3 objects
        self.assertEqual(len(tree.children), 3)

        # First object (i=0, even): red for both
        obj0 = tree.children[0]
        self.assertEqual(obj0.children[0].data, 'color')
        self.assertEqual(obj0.children[1].data, 'color')

        # Second object (i=1, odd): green stroke, static black fill
        obj1 = tree.children[1]
        self.assertEqual(obj1.children[0].data, 'color')
        self.assertEqual(obj1.children[1].data, 'color')

    def test_mixed_static_object_tree(self):
        """Combine static text, interpolated objects, and spliced trees."""
        red = self.parser.parse("255,0,0")
        texture = self.Image("pattern.png")

        program = t"""
        object { stroke: 128,128,128 fill: 64,64,64 }
        object { stroke: {red} fill: {texture} }
        object { stroke: {texture} fill: 0,255,128 }
        """

        tree = self.parser.parse(program)
        self.assertEqual(len(tree.children), 3)

        # Object 1: static colors only
        obj1 = tree.children[0]
        self.assertEqual(obj1.children[0].data, 'color')
        self.assertEqual(obj1.children[1].data, 'color')

        # Object 2: spliced tree + interpolated image
        obj2 = tree.children[1]
        self.assertEqual(obj2.children[0].data, 'color')  # red tree
        self.assertEqual(obj2.children[1].data, 'image')  # texture object

        # Object 3: interpolated image + static color
        obj3 = tree.children[2]
        self.assertEqual(obj3.children[0].data, 'image')  # texture object
        self.assertEqual(obj3.children[1].data, 'color')  # static color

    def test_error_wrong_tree_label(self):
        """Splicing a tree with label not in grammar should fail."""
        # Create tree with invalid label
        wrong_tree = Tree('circle', [])

        program = t"object { stroke: {wrong_tree} fill: 0,0,0 }"

        with self.assertRaises((UnexpectedToken, ValueError)) as ctx:
            self.parser.parse(program)

        # Should mention the invalid label
        self.assertIn('circle', str(ctx.exception).lower())

    def test_error_malformed_static_syntax(self):
        """Static syntax errors should be caught normally."""
        # Missing third color component
        program = t"object { stroke: 255,0 fill: 0,0,0 }"

        with self.assertRaises(UnexpectedToken):
            self.parser.parse(program)

    def test_multiple_objects_mixed_paints(self):
        """Complex program with many objects using various paint types."""
        red = self.parser.parse("255,0,0")
        img1 = self.Image("texture1.png")
        img2 = self.Image("texture2.png")

        program = t"""
        object { stroke: 255,255,255 fill: 0,0,0 }
        object { stroke: {red} fill: 100,100,100 }
        object { stroke: {img1} fill: {img2} }
        object { stroke: 50,50,50 fill: {red} }
        object { stroke: {img1} fill: 200,200,200 }
        """

        tree = self.parser.parse(program)
        self.assertEqual(len(tree.children), 5)

        # Verify each object parses correctly
        for obj in tree.children:
            self.assertEqual(obj.data, 'object')
            self.assertEqual(len(obj.children), 2)  # stroke and fill

            # Each paint should be either color or image
            for paint in obj.children:
                self.assertIn(paint.data, ['color', 'image'])

    def test_image_object_preservation(self):
        """Verify Image objects are preserved through parsing."""
        img = self.Image("test.png")

        program = t"object { stroke: {img} fill: 0,0,0 }"
        tree = self.parser.parse(program)

        # Extract the image from the parse tree
        image_paint = tree.children[0].children[0]
        image_token = image_paint.children[0]

        # Should be the exact same object
        self.assertIs(image_token.value, img)
        self.assertEqual(image_token.value.path, "test.png")

    def test_static_equivalent_to_plain_string(self):
        """Static-only template should parse identically to plain string."""
        tree1 = self.parser.parse(t"object { stroke: 255,0,0 fill: 0,255,0 }")
        tree2 = self.parser.parse("object { stroke: 255,0,0 fill: 0,255,0 }")

        # Both should produce identical structure
        self.assertEqual(tree1.data, tree2.data)
        self.assertEqual(len(tree1.children), len(tree2.children))


if __name__ == '__main__':
    unittest.main()
