"""
`.where()` string-DSL parser → validated backend-neutral predicate tree.

This module is the SECURITY BOUNDARY for the collection query builder. A
factory author (or YAML / chat / MCP) supplies a small filter string like:

    "token_count >= 400 and document != 'X.pdf' and meta.state == 'vectorised'"

and `parse_where()` returns an AST. The selected datastore compiles it within
its own fixed factory/collection scope, so a filter can never widen scope.

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

__all__ = ["parse_where", "QueryFilterError"]


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


def parse_where(text):
    """Validate the DSL and return a backend-neutral predicate tree."""
    if not isinstance(text, str):
        raise QueryFilterError("filter must be a string")
    if len(text) > _MAX_LEN:
        raise QueryFilterError("filter string too long")
    node = _Parser(_tokenize(text)).parse()
    count = 0

    def validate(item, depth=0):
        nonlocal count
        if depth > _MAX_DEPTH:
            raise QueryFilterError("filter nesting too deep")
        if item[0] in ('and', 'or'):
            validate(item[1], depth + 1)
            validate(item[2], depth + 1)
        elif item[0] == 'not':
            validate(item[1], depth + 1)
        else:
            _, field, op, value = item
            size = len(value[1]) if op in ('in', 'not in') else 1
            if count + size > _MAX_PARAMS:
                raise QueryFilterError("filter has too many values")
            if field[0] != 'col' and op in ('<', '>', '<=', '>='):
                float(value[1])  # Preserve the existing ordering-literal validation timing.
            count += 1
    validate(node)
    return node
