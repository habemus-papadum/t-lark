import sys
import json

from t_lark.grammar import Rule
from t_lark.lexer import TerminalDef
from t_lark.tools import lalr_argparser, build_lalr

import argparse

argparser = argparse.ArgumentParser(prog='python -m t_lark.tools.serialize', parents=[lalr_argparser],
                                    description="t_lark Serialization Tool - Stores t_lark's internal state & LALR analysis as a JSON file",
                                    epilog='Look at the Lark documentation for more info on the options')


def serialize(lark_inst, outfile):
    data, memo = lark_inst.memo_serialize([TerminalDef, Rule])
    outfile.write('{\n')
    outfile.write('  "data": %s,\n' % json.dumps(data))
    outfile.write('  "memo": %s\n' % json.dumps(memo))
    outfile.write('}\n')


def main():
    if len(sys.argv)==1:
        argparser.print_help(sys.stderr)
        sys.exit(1)
    ns = argparser.parse_args()
    serialize(*build_lalr(ns))


if __name__ == '__main__':
    main()
