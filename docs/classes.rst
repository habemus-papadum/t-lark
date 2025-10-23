API Reference
=============

Lark
----

.. autoclass:: t_lark.Lark
    :members: open, parse, parse_interactive, lex, save, load, get_terminal, open_from_package


Using Unicode character classes with ``regex``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

Python's builtin ``re`` module has a few persistent known bugs and also won't parse
advanced regex features such as character classes.
With ``pip install t-lark[regex]``, the ``regex`` module will be
installed alongside t_lark and can act as a drop-in replacement to ``re``.

Any instance of Lark instantiated with ``regex=True`` will use the ``regex`` module instead of ``re``.

For example, we can use character classes to match PEP-3131 compliant Python identifiers:

::

    from t_lark import Lark
    >>> g = Lark(r"""
                        ?start: NAME
                        NAME: ID_START ID_CONTINUE*
                        ID_START: /[\p{Lu}\p{Ll}\p{Lt}\p{Lm}\p{Lo}\p{Nl}_]+/
                        ID_CONTINUE: ID_START | /[\p{Mn}\p{Mc}\p{Nd}\p{Pc}·]+/
                    """, regex=True)

    >>> g.parse('வணக்கம்')
    'வணக்கம்'


Tree
----

.. autoclass:: t_lark.Tree
    :members: pretty, find_pred, find_data, iter_subtrees, scan_values,
        iter_subtrees_topdown, __rich__

Token
-----

.. autoclass:: t_lark.Token

Transformer, Visitor & Interpreter
----------------------------------

See :doc:`visitors`.

ForestVisitor, ForestTransformer, & TreeForestTransformer
-----------------------------------------------------------

See :doc:`forest`.

UnexpectedInput
---------------

.. autoclass:: t_lark.exceptions.UnexpectedInput
    :members: get_context, match_examples

.. autoclass:: t_lark.exceptions.UnexpectedToken

.. autoclass:: t_lark.exceptions.UnexpectedCharacters

.. autoclass:: t_lark.exceptions.UnexpectedEOF

InteractiveParser
-----------------

.. autoclass:: t_lark.parsers.lalr_interactive_parser.InteractiveParser
    :members: choices, feed_token, copy, pretty, resume_parse, exhaust_lexer, accepts, as_immutable

.. autoclass:: t_lark.parsers.lalr_interactive_parser.ImmutableInteractiveParser
    :members: choices, feed_token, copy, pretty, resume_parse, exhaust_lexer, accepts, as_mutable


ast_utils
---------

For an example of using ``ast_utils``, see `/examples/advanced/create_ast.py`_

.. autoclass:: t_lark.ast_utils.Ast

.. autoclass:: t_lark.ast_utils.AsList

.. autofunction:: t_lark.ast_utils.create_transformer

.. _/examples/advanced/create_ast.py: examples/advanced/create_ast.html

Indenter
--------

.. autoclass:: t_lark.indenter.Indenter
.. autoclass:: t_lark.indenter.PythonIndenter

TextSlice
---------

.. autoclass:: t_lark.utils.TextSlice
