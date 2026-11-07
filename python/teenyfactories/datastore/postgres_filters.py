"""PostgreSQL filter compilation; the shared DSL parser supplies a validated AST."""
from ..where_parser import parse_where

_NUM_GUARD = r"^-?[0-9]+(\.[0-9]+)?$"
_BOOL_VALS = "('true','false')"


def compile_filter(node):
    params = []
    return _compile(node, params), params


def compile_where(text):
    return compile_filter(parse_where(text))


def _compile(node, params):
    tag = node[0]
    if tag in ('and', 'or'):
        left = _compile(node[1], params)
        right = _compile(node[2], params)
        return f'({left} {tag.upper()} {right})'
    if tag == 'not':
        return f'(NOT {_compile(node[1], params)})'
    _, field, op, value = node
    return _comparison(field, op, value, params)


def _comparison(field, op, value, params):
    is_col = field[0] == 'col'
    # Field identifiers and row-column names are validated by parse_where.
    field_sql = 'd.' + field[1] if is_col else "d.value->>'%s'" % field[1]
    if op in ('in', 'not in'):
        items = value[1]
        anyall = '= ANY(%s)' if op == 'in' else '<> ALL(%s)'
        if is_col:
            params.append(items)
            return f'{field_sql} {anyall}'
        if all(_is_numeric(item) for item in items):
            params.append([float(item) for item in items])
            return f"({field_sql} ~ '{_NUM_GUARD}' AND ({field_sql})::numeric {anyall})"
        params.append([str(item) for item in items])
        return f'{field_sql} {anyall}'
    literal = value[1]
    sql_op = {'==': '=', '!=': 'IS DISTINCT FROM'}.get(op, op)
    if is_col:
        params.append(literal)
        return f'{field_sql} {sql_op} %s'
    if op in ('<', '>', '<=', '>=') or _is_numeric(literal):
        params.append(literal if _is_numeric(literal) else float(literal))
        return f"({field_sql} ~ '{_NUM_GUARD}' AND ({field_sql})::numeric {sql_op} %s)"
    if isinstance(literal, bool):
        params.append(literal)
        return f'({field_sql} IN {_BOOL_VALS} AND ({field_sql})::boolean {sql_op} %s)'
    params.append(literal)
    return f'{field_sql} {sql_op} %s'


def _is_numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)
