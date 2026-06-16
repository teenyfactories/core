"""
`.where()` string-DSL parser → parameterized SQL predicate.

This module is the SECURITY BOUNDARY for the collection query builder. A
factory author (or YAML / chat / MCP) supplies a small filter string like:

    "token_count >= 400 and document != 'X.pdf' and meta.state == 'vectorised'"

and `compile_where()` returns `(sql_fragment, params)` where `sql_fragment`
uses `%s` placeholders and `params` is the matching value list. The caller
(query.py) splices the fragment INSIDE a code-built tenant scope prefix
(`factory_name = %s AND collection = %s AND ( <fragment> )`) so the predicate
can never widen scope.

Safety is structural, not a blocklist:
  * Parse → AST → parameterized SQL. Every literal becomes a `%s` param; values
    are NEVER concatenated into SQL text.
  * The grammar has NO production for `;`, SQL keywords, comments (`--`/`/* */`),
    subqueries, function calls or backslash escapes — they are unlexable.
  * Field identifiers are regex-validated; `meta.<col>` resolves only to a fixed
    column whitelist; bare / `data.` fields become `value->>'<field>'`.
  * Caps on string length, AST depth and total bound params bound DoS-by-data.

Namespaces (bare = payload, meta = row column):
    token_count        -> value->>'token_count'   (JSONB payload)
    data.token_count   -> value->>'token_count'   (explicit payload alias)
    meta.state         -> the row's `state` column (whitelist only)

Grammar:
    expr       := or_expr
    or_expr    := and_expr ( "or" and_expr )*
    and_expr   := not_expr ( "and" not_expr )*
    not_expr   := "not" not_expr | primary
    primary    := "(" expr ")" | comparison
    comparison := field ( cmp_op literal | in_op list )
    cmp_op     := == | != | < | > | <= | >=
    in_op      := "in" | "not in"
    field      := IDENT | "data" "." IDENT | "meta" "." META_COL
    list       := "[" literal ( "," literal )* "]"
    literal    := STRING | NUMBER | BOOL
"""

import re

__all__ = ["compile_where", "QueryFilterError"]


class QueryFilterError(ValueError):
    """Raised on any malformed / disallowed `.where()` filter string.

    Messages reference token kinds / positions, NEVER literal values, so an
    error body can't leak payload data to an untrusted caller.
    """


# Limits (security-required, not optional).
_MAX_LEN = 2000  # raw filter-string length
_MAX_DEPTH = 50  # AST nesting depth
_MAX_PARAMS = 1000  # total bound params incl. list elements (<< PG 65535)

# Only these row columns are reachable via `meta.`. factory_name / collection
# are deliberately excluded — they're pinned by the tenant scope prefix.
_META_COLS = frozenset({"state", "key", "user_id", "created_at", "updated_at", "state_changed_at"})

_IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
# SQL literal (fixed, safe) guarding a JSONB numeric cast so a malformed row is
# EXCLUDED rather than aborting the whole query.
_NUM_GUARD = r"^-?[0-9]+(\.[0-9]+)?$"
_BOOL_VALS = "('true','false')"

_CMP_OPS = {"==", "!=", "<", ">", "<=", ">="}
_KEYWORDS = {"and", "or", "not", "in", "true", "false"}


# ── Tokenizer ────────────────────────────────────────────────────────────────


def _tokenize(text):
    """Text → list of (kind, value, pos). Raises on any unlexable character."""
    toks = []
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c.isspace():
            i += 1
            continue
        # strings — single or double quoted, no escapes (backslash is unlexable)
        if c in "'\"":
            j = i + 1
            buf = []
            while j < n and text[j] != c:
                if text[j] == "\\":
                    raise QueryFilterError(f"backslash escapes not allowed (pos {j})")
                buf.append(text[j])
                j += 1
            if j >= n:
                raise QueryFilterError(f"unterminated string (pos {i})")
            toks.append(("string", "".join(buf), i))
            i = j + 1
            continue
        # numbers
        if c.isdigit() or (c == "-" and i + 1 < n and text[i + 1].isdigit()):
            m = re.match(r"-?[0-9]+(\.[0-9]+)?", text[i:])
            toks.append(("number", m.group(0), i))
            i += m.end()
            continue
        # two-char operators
        two = text[i : i + 2]
        if two in ("==", "!=", "<=", ">="):
            toks.append(("op", two, i))
            i += 2
            continue
        if c in "<>":
            toks.append(("op", c, i))
            i += 1
            continue
        if c == "(":
            toks.append(("lparen", c, i))
            i += 1
            continue
        if c == ")":
            toks.append(("rparen", c, i))
            i += 1
            continue
        if c == "[":
            toks.append(("lbracket", c, i))
            i += 1
            continue
        if c == "]":
            toks.append(("rbracket", c, i))
            i += 1
            continue
        if c == ",":
            toks.append(("comma", c, i))
            i += 1
            continue
        # identifiers (optionally dotted) + keywords
        m = _IDENT_RE.match(text, i)
        if m:
            word = m.group(0)
            i = m.end()
            # dotted: namespace.field
            if i < n and text[i] == ".":
                m2 = _IDENT_RE.match(text, i + 1)
                if not m2:
                    raise QueryFilterError(f"expected identifier after '.' (pos {i})")
                word = word + "." + m2.group(0)
                i = m2.end()
                toks.append(("ident", word, m.start()))
                continue
            lw = word.lower()
            if lw in _KEYWORDS:
                toks.append(("kw", lw, m.start()))
            else:
                toks.append(("ident", word, m.start()))
            continue
        raise QueryFilterError(f"unexpected character {c!r} (pos {i})")
    return toks


# ── Parser (recursive descent) → AST ─────────────────────────────────────────
#
# AST nodes (tagged tuples):
#   ("and", l, r) | ("or", l, r) | ("not", x)
#   ("cmp", field, op, value)
#     field = ("payload", name) | ("col", name)
#     value = ("lit", py) | ("list", [py, ...])


class _Parser:
    def __init__(self, toks):
        self.toks = toks
        self.pos = 0
        self.depth = 0

    def _descend(self):
        self.depth += 1
        if self.depth > _MAX_DEPTH:
            raise QueryFilterError("filter nesting too deep")

    def _peek(self):
        return self.toks[self.pos] if self.pos < len(self.toks) else (None, None, -1)

    def _next(self):
        t = self._peek()
        self.pos += 1
        return t

    def _expect(self, kind, value=None):
        k, v, p = self._peek()
        if k != kind or (value is not None and v != value):
            want = value or kind
            raise QueryFilterError(f"expected {want!r} (pos {p})")
        return self._next()

    def parse(self):
        if not self.toks:
            raise QueryFilterError("empty filter")
        node = self._or()
        if self.pos != len(self.toks):
            raise QueryFilterError(f"unexpected trailing input (pos {self._peek()[2]})")
        return node

    def _or(self):
        node = self._and()
        while self._peek()[:2] == ("kw", "or"):
            self._next()
            node = ("or", node, self._and())
        return node

    def _and(self):
        node = self._not()
        while self._peek()[:2] == ("kw", "and"):
            self._next()
            node = ("and", node, self._not())
        return node

    def _not(self):
        if self._peek()[:2] == ("kw", "not"):
            self._next()
            self._descend()
            node = ("not", self._not())
            self.depth -= 1
            return node
        return self._primary()

    def _primary(self):
        k, v, p = self._peek()
        if k == "lparen":
            self._next()
            self._descend()
            node = self._or()
            self.depth -= 1
            self._expect("rparen")
            return node
        return self._comparison()

    def _comparison(self):
        k, v, p = self._peek()
        if k != "ident":
            raise QueryFilterError(f"expected a field name (pos {p})")
        field = self._resolve_field(self._next()[1], p)

        nk, nv, npos = self._peek()
        # in / not in
        if (nk, nv) == ("kw", "in"):
            self._next()
            return ("cmp", field, "in", self._list())
        if (nk, nv) == ("kw", "not"):
            self._next()
            self._expect("kw", "in")
            return ("cmp", field, "not in", self._list())
        # comparison op
        if nk == "op":
            self._next()
            return ("cmp", field, nv, ("lit", self._literal()))
        raise QueryFilterError(f"expected an operator after field (pos {npos})")

    def _resolve_field(self, raw, pos):
        if "." in raw:
            ns, _, name = raw.partition(".")
            ns = ns.lower()
            if ns == "meta":
                if name not in _META_COLS:
                    raise QueryFilterError(f"unknown meta column (pos {pos})")
                return ("col", name)
            if ns == "data":
                return ("payload", name)
            raise QueryFilterError(f"unknown namespace (pos {pos})")
        return ("payload", raw)

    def _list(self):
        self._expect("lbracket")
        items = [self._literal()]
        while self._peek()[0] == "comma":
            self._next()
            items.append(self._literal())
        self._expect("rbracket")
        return ("list", items)

    def _literal(self):
        k, v, p = self._next()
        if k == "string":
            return v
        if k == "number":
            return float(v) if ("." in v) else int(v)
        if k == "kw" and v in ("true", "false"):
            return v == "true"
        raise QueryFilterError(f"expected a literal (pos {p})")


# ── Compiler: AST → (sql, params) ────────────────────────────────────────────


def _field_sql(field):
    kind, name = field
    if kind == "col":
        return "d.%s" % name  # name is from the whitelist → safe
    # payload name was validated by the tokenizer's identifier regex
    return "d.value->>'%s'" % name


def _is_numeric(py):
    return isinstance(py, (int, float)) and not isinstance(py, bool)


def _compile(node, params, depth):
    if depth > _MAX_DEPTH:
        raise QueryFilterError("filter nesting too deep")
    tag = node[0]
    if tag in ("and", "or"):
        left = _compile(node[1], params, depth + 1)
        right = _compile(node[2], params, depth + 1)
        return "(%s %s %s)" % (left, tag.upper(), right)
    if tag == "not":
        return "(NOT %s)" % _compile(node[1], params, depth + 1)
    # comparison
    _, field, op, value = node
    return _compile_cmp(field, op, value, params)


def _add_param(params, val):
    params.append(val)
    if len(params) > _MAX_PARAMS:
        raise QueryFilterError("filter has too many values")


def _compile_cmp(field, op, value, params):
    is_col = field[0] == "col"
    fsql = _field_sql(field)

    if op in ("in", "not in"):
        items = value[1]
        if not items:
            raise QueryFilterError("empty list not allowed")
        # The list binds as ONE param (psycopg2 adapts it for ANY/ALL); the cap
        # counts its elements so a giant list can't blow the param ceiling.
        if len(params) + len(items) > _MAX_PARAMS:
            raise QueryFilterError("filter has too many values")
        anyall = "= ANY(%s)" if op == "in" else "<> ALL(%s)"
        numeric = all(_is_numeric(x) for x in items)
        if is_col:
            params.append(items)
            return "%s %s" % (fsql, anyall)
        if numeric:
            guard = "%s ~ '%s'" % (fsql, _NUM_GUARD)
            params.append([float(x) for x in items])
            return "(%s AND (%s)::numeric %s)" % (guard, fsql, anyall)
        params.append([str(x) for x in items])
        return "%s %s" % (fsql, anyall)

    # scalar comparison
    py = value[1]
    ordering = op in ("<", ">", "<=", ">=")
    sql_op = {"==": "=", "!=": "IS DISTINCT FROM"}.get(op, op)

    if is_col:
        _add_param(params, py)
        return "%s %s %%s" % (fsql, sql_op)

    # payload — cast by literal type / operator
    if ordering or _is_numeric(py):
        guard = "%s ~ '%s'" % (fsql, _NUM_GUARD)
        _add_param(params, float(py) if not _is_numeric(py) else py)
        return "(%s AND (%s)::numeric %s %%s)" % (guard, fsql, sql_op)
    if isinstance(py, bool):
        guard = "%s IN %s" % (fsql, _BOOL_VALS)
        _add_param(params, py)
        return "(%s AND (%s)::boolean %s %%s)" % (guard, fsql, sql_op)
    # string
    _add_param(params, py)
    return "%s %s %%s" % (fsql, sql_op)


def compile_where(text):
    """Compile a `.where()` filter string to `(sql_fragment, params)`.

    `sql_fragment` uses `%s` placeholders and is safe to splice inside the
    caller's parenthesized predicate slot. Raises QueryFilterError on any
    malformed / disallowed input.
    """
    if not isinstance(text, str):
        raise QueryFilterError("filter must be a string")
    if len(text) > _MAX_LEN:
        raise QueryFilterError("filter string too long")
    ast = _Parser(_tokenize(text)).parse()
    params = []
    sql = _compile(ast, params, 0)
    return sql, params
