from typing import Any, Dict, Optional, Tuple, ClassVar, Sequence

from typing import Any, Dict, Optional, Sequence, Tuple, ClassVar, List, Set

from .utils import Serialize

###{standalone
TOKEN_DEFAULT_PRIORITY = 0


class Symbol(Serialize):
    __slots__ = ('name',)

    name: str
    is_term: ClassVar[bool] = NotImplemented

    def __init__(self, name: str) -> None:
        self.name = name

    def __eq__(self, other):
        if not isinstance(other, Symbol):
            return NotImplemented
        return self.is_term == other.is_term and self.name == other.name

    def __ne__(self, other):
        return not (self == other)

    def __hash__(self):
        return hash(self.name)

    def __repr__(self):
        return '%s(%r)' % (type(self).__name__, self.name)

    fullrepr = property(__repr__)

    def renamed(self, f):
        return type(self)(f(self.name))


class Terminal(Symbol):
    __serialize_fields__ = 'name', 'filter_out'

    is_term: ClassVar[bool] = True

    def __init__(self, name: str, filter_out: bool = False) -> None:
        self.name = name
        self.filter_out = filter_out

    @property
    def fullrepr(self):
        return '%s(%r, %r)' % (type(self).__name__, self.name, self.filter_out)

    def renamed(self, f):
        return type(self)(f(self.name), self.filter_out)


class NonTerminal(Symbol):
    __serialize_fields__ = 'name',

    is_term: ClassVar[bool] = False

    def serialize(self, memo=None) -> Dict[str, Any]:
        # TODO this is here because self.name can be a Token instance.
        #      remove this function when the issue is fixed. (backwards-incompatible)
        return {'name': str(self.name), '__type__': 'NonTerminal'}


class RuleOptions(Serialize):
    __serialize_fields__ = 'keep_all_tokens', 'expand1', 'priority', 'template_source', 'empty_indices'

    keep_all_tokens: bool
    expand1: bool
    priority: Optional[int]
    template_source: Optional[str]
    empty_indices: Tuple[bool, ...]

    def __init__(self, keep_all_tokens: bool=False, expand1: bool=False, priority: Optional[int]=None, template_source: Optional[str]=None, empty_indices: Tuple[bool, ...]=()) -> None:
        self.keep_all_tokens = keep_all_tokens
        self.expand1 = expand1
        self.priority = priority
        self.template_source = template_source
        self.empty_indices = empty_indices

    def __repr__(self):
        return 'RuleOptions(%r, %r, %r, %r)' % (
            self.keep_all_tokens,
            self.expand1,
            self.priority,
            self.template_source
        )


class Rule(Serialize):
    """
        origin : a symbol
        expansion : a list of symbols
        order : index of this expansion amongst all rules of the same name
    """
    __slots__ = ('origin', 'expansion', 'alias', 'options', 'order', '_hash')

    __serialize_fields__ = 'origin', 'expansion', 'order', 'alias', 'options'
    __serialize_namespace__ = Terminal, NonTerminal, RuleOptions

    origin: NonTerminal
    expansion: Sequence[Symbol]
    order: int
    alias: Optional[str]
    options: RuleOptions
    _hash: int

    def __init__(self, origin: NonTerminal, expansion: Sequence[Symbol],
                 order: int=0, alias: Optional[str]=None, options: Optional[RuleOptions]=None):
        self.origin = origin
        self.expansion = expansion
        self.alias = alias
        self.order = order
        self.options = options or RuleOptions()
        self._hash = hash((self.origin, tuple(self.expansion)))

    def _deserialize(self):
        self._hash = hash((self.origin, tuple(self.expansion)))

    def __str__(self):
        return '<%s : %s>' % (self.origin.name, ' '.join(x.name for x in self.expansion))

    def __repr__(self):
        return 'Rule(%r, %r, %r, %r)' % (self.origin, self.expansion, self.alias, self.options)

    def __hash__(self):
        return self._hash

    def __eq__(self, other):
        if not isinstance(other, Rule):
            return False
        return self.origin == other.origin and self.expansion == other.expansion


###}


def augment_grammar_for_template_mode(rules: List[Rule], terminals: List['TerminalDef']) -> Dict[str, str]:
    """Augment grammar with terminals and rules for template tree splicing.

    Returns a mapping from tree labels to the generated terminal names.
    """

    from .lexer import PatternTree, TerminalDef  # Local import to avoid circular dependency

    terminals_by_name = {t.name: t for t in terminals}
    labels_per_origin: Dict[NonTerminal, Set[str]] = {}
    max_order_per_origin: Dict[NonTerminal, int] = {}

    for rule in rules:
        labels = labels_per_origin.setdefault(rule.origin, set())
        label = rule.alias if rule.alias is not None else rule.origin.name
        labels.add(label)
        max_order_per_origin[rule.origin] = max(max_order_per_origin.get(rule.origin, -1), rule.order)

    all_labels: Set[str] = set()
    for label_set in labels_per_origin.values():
        all_labels.update(label_set)

    tree_terminal_map: Dict[str, str] = {}
    for label in all_labels:
        term_name = f'TREE__{label.upper()}'
        if term_name not in terminals_by_name:
            terminals_by_name[term_name] = TerminalDef(term_name, PatternTree(label))
            terminals.append(terminals_by_name[term_name])
        tree_terminal_map[label] = term_name

    existing_rules = {
        (r.origin, tuple(sym.name for sym in r.expansion))
        for r in rules
    }

    for origin, labels in labels_per_origin.items():
        for label in labels:
            term_name = tree_terminal_map[label]
            signature = (origin, (term_name,))
            if signature in existing_rules:
                continue

            order = max_order_per_origin.get(origin, -1) + 1
            max_order_per_origin[origin] = order

            new_rule = Rule(origin, [Terminal(term_name)], order, alias=None, options=RuleOptions(expand1=True))
            rules.append(new_rule)
            existing_rules.add(signature)

    return tree_terminal_map
