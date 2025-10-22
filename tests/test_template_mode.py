"""Tests for template mode parsing (Python 3.14 t-strings)."""

import unittest
import sys
from dataclasses import dataclass
from typing import Tuple, List

from lark import Lark, Tree, Token
from lark.exceptions import GrammarError, ConfigurationError, UnexpectedToken


# Mock Template and Interpolation classes for testing
# (Python 3.14's actual classes will be in string.templatelib)
@dataclass
class Interpolation:
    value: object
    expression: str = ""
    conversion: str = None
    format_spec: str = ""


@dataclass
class Template:
    strings: Tuple[str, ...]
    interpolations: Tuple[Interpolation, ...]

    @property
    def values(self):
        return tuple(i.value for i in self.interpolations)


# Helper to create templates easily
def make_template(parts, *values):
    """Create a Template from string parts and interpolated values."""
    if len(values) != len(parts) - 1:
        raise ValueError("values must be one less than parts")

    interpolations = tuple(Interpolation(value=v) for v in values)
    return Template(strings=tuple(parts), interpolations=interpolations)


class TestTemplateModeBasics(unittest.TestCase):
    """Test basic template mode functionality."""

    def test_requires_earley_parser(self):
        """Template mode should require Earley parser."""
        grammar = "start: 'x'"

        with self.assertRaises((ConfigurationError, AssertionError)):
            Lark(grammar, parser="lalr", lexer="template")

    def test_static_only_template(self):
        """Static-only template should parse like plain string."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        %ignore " "
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Static template
        template = Template(strings=("42",), interpolations=())
        result = parser.parse(template)

        # Should parse identically to plain string
        plain_result = parser.parse("42")

        self.assertIsNotNone(result)
        self.assertEqual(result.data, plain_result.data)

    def test_plain_string_still_works(self):
        """Plain strings should still work in template mode."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        result = parser.parse("42")
        self.assertIsNotNone(result)


class TestPyobjPlaceholders(unittest.TestCase):
    """Test PYOBJ placeholder functionality."""

    def test_pyobj_import_required(self):
        """Using PYOBJ requires %import template (PYOBJ)."""
        # Grammar without import should fail or not recognize PYOBJ
        grammar = r"""
        start: "value:" PYOBJ
        """

        # This should fail during grammar loading
        with self.assertRaises(Exception):
            Lark(grammar, parser="earley", lexer="template")

    def test_pyobj_untyped_accepts_any_object(self):
        """PYOBJ should accept any Python object."""
        grammar = r"""
        %import template (PYOBJ)
        start: "value:" PYOBJ
        %ignore " "
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Test with integer
        t1 = make_template(["value:", ""], 42)
        result1 = parser.parse(t1)
        self.assertIsNotNone(result1)

        # Test with string
        t2 = make_template(["value:", ""], "hello")
        result2 = parser.parse(t2)
        self.assertIsNotNone(result2)

        # Test with list
        t3 = make_template(["value:", ""], [1, 2, 3])
        result3 = parser.parse(t3)
        self.assertIsNotNone(result3)

    def test_pyobj_typed_validates_types(self):
        """PYOBJ[typename] should validate types."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[num]
        """
        parser = Lark(grammar, parser="earley", lexer="template",
                      pyobj_types={'num': int})

        # Should work with int
        t_good = make_template(["", ""], 42)
        result = parser.parse(t_good)
        self.assertIsNotNone(result)

        # Should fail with string
        t_bad = make_template(["", ""], "not a number")
        with self.assertRaises(TypeError):
            parser.parse(t_bad)

    def test_consecutive_pyobj(self):
        """Consecutive PYOBJ placeholders should work."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        t = make_template(["", "", ""], 1, 2)
        result = parser.parse(t)
        self.assertIsNotNone(result)


class TestTreeSplicing(unittest.TestCase):
    """Test Tree splicing functionality."""

    def test_basic_tree_splicing(self):
        """Pre-built trees should splice seamlessly."""
        grammar = r"""
        ?start: expr
        ?expr: term
             | expr "+" term -> add
        ?term: NUMBER
        %import common.NUMBER
        %ignore " "
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Parse a fragment
        sub = parser.parse("1 + 2")
        self.assertEqual(sub.data, 'add')

        # Splice it into a template
        t = make_template(["", ""], sub)
        result = parser.parse(t)

        # Should get the same tree
        self.assertEqual(result.data, 'add')

    def test_tree_with_static_text(self):
        """Mix tree splicing with static text."""
        grammar = r"""
        ?start: stmt
        stmt: expr ";"
        ?expr: term
             | expr "+" term -> add
        ?term: NUMBER
        %import common.NUMBER
        %ignore " "
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Create a sub-expression
        sub = parser.parse("1 + 2;")

        # This should work - splicing the entire stmt
        t = make_template(["", ""], sub)
        result = parser.parse(t)
        self.assertIsNotNone(result)

    def test_tree_wrong_label_fails(self):
        """Tree with wrong label should fail."""
        grammar = r"""
        start: expr
        expr: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Create tree with label grammar doesn't produce
        wrong_tree = Tree('wrong_label', [])

        t = make_template(["", ""], wrong_tree)

        # Should fail because grammar doesn't produce 'wrong_label'
        with self.assertRaises((UnexpectedToken, ValueError)):
            parser.parse(t)


class TestMixedContent(unittest.TestCase):
    """Test mixing static text, objects, and trees."""

    def test_mixed_static_pyobj_tree(self):
        """Mix of static, PYOBJ, and tree splicing."""
        grammar = r"""
        %import template (PYOBJ)
        start: "static" PYOBJ expr
        expr: NUMBER
        %import common.NUMBER
        %ignore " "
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Create a sub-tree
        sub = parser.parse("42")

        # Mix everything
        t = make_template(["static ", " ", ""], 100, sub)
        result = parser.parse(t)
        self.assertIsNotNone(result)


class TestErrorHandling(unittest.TestCase):
    """Test error handling in template mode."""

    def test_object_where_no_pyobj_allowed(self):
        """Object where no PYOBJ allowed should error."""
        grammar = r"""
        start: NUMBER
        %import common.NUMBER
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Try to pass an object where only NUMBER is allowed
        t = make_template(["", ""], 42)

        # Should fail - no PYOBJ in grammar
        with self.assertRaises(UnexpectedToken):
            parser.parse(t)


class TestSourceLocationTracking(unittest.TestCase):
    """Test source location tracking (meta)."""

    def test_without_source_info(self):
        """Should work without source_info."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")

        # Template without source_info (normal case)
        t = make_template(["", ""], 42)
        result = parser.parse(t)
        self.assertIsNotNone(result)


class TestGrammarImports(unittest.TestCase):
    """Test grammar import syntax."""

    def test_valid_template_import(self):
        """Valid %import template (PYOBJ) should work."""
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")
        self.assertIsNotNone(parser)

    def test_invalid_template_import(self):
        """Invalid template import should fail."""
        # Try to import something other than PYOBJ from template
        grammar = r"""
        %import template (INVALID)
        start: PYOBJ
        """

        with self.assertRaises(GrammarError):
            Lark(grammar, parser="earley", lexer="template")


if __name__ == '__main__':
    unittest.main()
