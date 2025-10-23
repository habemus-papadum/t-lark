import unittest

from lark import Lark, Tree, Token
from lark.exceptions import UnexpectedToken


class TestTemplateModeBasic(unittest.TestCase):
    def setUp(self):
        grammar = r"""
        %import template (PYOBJ)
        %import common.NUMBER
        start: "print" "(" (PYOBJ | NUMBER) ")"
        """
        self.parser = Lark(grammar, parser="earley", lexer="template")

    def test_static_template(self):
        tree_from_template = self.parser.parse(t"print(1)")
        tree_from_string = self.parser.parse("print(1)")
        self.assertEqual(tree_from_template.data, tree_from_string.data)

    def test_pyobj_interpolation(self):
        value = object()
        tree = self.parser.parse(t"print({value})")
        pyobj_token = list(tree.scan_values(lambda v: getattr(v, "type", None) == "PYOBJ"))[0]
        self.assertIs(pyobj_token.value, value)

    def test_consecutive_pyobj(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ PYOBJ
        """
        parser = Lark(grammar, parser="earley", lexer="template")
        tree = parser.parse(t"{1}{2}")
        tokens = list(tree.scan_values(lambda v: getattr(v, "type", None) == "PYOBJ"))
        self.assertEqual([token.value for token in tokens], [1, 2])


class TestTemplateTypedObjects(unittest.TestCase):
    class Image:
        def __init__(self, name):
            self.name = name

    def setUp(self):
        grammar = r"""
        %import template (PYOBJ)
        start: "show" " " PYOBJ[image]
        """
        self.parser = Lark(
            grammar,
            parser="earley",
            lexer="template",
            pyobj_types={"image": self.Image},
        )

    def test_typed_pyobj(self):
        img = self.Image("wood")
        tree = self.parser.parse(t"show {img}")
        token = list(tree.scan_values(lambda v: getattr(v, "type", "") == "PYOBJ__IMAGE"))[0]
        self.assertIs(token.value, img)

    def test_type_mismatch(self):
        with self.assertRaises(TypeError):
            self.parser.parse(t"show {123}")

    def test_missing_type_registration(self):
        grammar = r"""
        %import template (PYOBJ)
        start: PYOBJ[missing]
        """
        parser = Lark(grammar, parser="earley", lexer="template")
        with self.assertRaises(TypeError):
            parser.parse(t"{123}")


class TestTemplateTreeSplicing(unittest.TestCase):
    def setUp(self):
        grammar = r"""
        %import template (PYOBJ)
        ?start: expr
        ?expr: expr "+" term -> add
             | term
        ?term: NUMBER
        %import common.NUMBER
        %ignore " "
        """
        self.parser = Lark(grammar, parser="earley", lexer="template")

    def test_splice_tree(self):
        subtree = self.parser.parse("1+2")
        self.assertIsInstance(subtree, Tree)
        result = self.parser.parse(t"{subtree}")
        self.assertIsInstance(result, Tree)
        self.assertEqual(result.data, subtree.data)
        self.assertEqual([child.type if isinstance(child, Token) else child for child in result.children],
                         [child.type if isinstance(child, Token) else child for child in subtree.children])

    def test_invalid_tree_label(self):
        bad_tree = Tree("unknown", [])
        with self.assertRaises((UnexpectedToken, ValueError)):
            self.parser.parse(t"{bad_tree}")


class TestTemplateErrors(unittest.TestCase):
    def test_requires_pyobj_import(self):
        parser = Lark("start: NUMBER\n%import common.NUMBER", parser="earley", lexer="template")
        with self.assertRaises(UnexpectedToken):
            parser.parse(t"{1}")


if __name__ == "__main__":
    unittest.main()
