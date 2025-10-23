"""
Lark Grammar
============

A reference implementation of the Lark grammar (using LALR(1))
"""
import t_lark
from pathlib import Path

examples_path = Path(__file__).parent
lark_path = Path(t_lark.__file__).parent

parser = t_lark.Lark.open(lark_path / 'grammars/t_lark.t_lark', rel_to=__file__, parser="lalr")


grammar_files = [
    examples_path / 'advanced/python2.t_lark',
    examples_path / 'relative-imports/multiples.t_lark',
    examples_path / 'relative-imports/multiple2.t_lark',
    examples_path / 'relative-imports/multiple3.t_lark',
    examples_path / 'tests/no_newline_at_end.t_lark',
    examples_path / 'tests/negative_priority.t_lark',
    examples_path / 'standalone/json.t_lark',
    lark_path / 'grammars/common.t_lark',
    lark_path / 'grammars/t_lark.t_lark',
    lark_path / 'grammars/unicode.t_lark',
    lark_path / 'grammars/python.t_lark',
]

def test():
    for grammar_file in grammar_files:
        tree = parser.parse(open(grammar_file).read())
    print("All grammars parsed successfully")

if __name__ == '__main__':
    test()
