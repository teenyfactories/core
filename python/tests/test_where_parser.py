"""Unit tests for teenyfactories.where_parser — the `.where()` string DSL.

Pure functions (no DB). The parser is the security boundary, so these tests
focus on: parameterization (no value ever in SQL text), namespace resolution,
cast rules, structural rejection of dangerous input, and the DoS caps.
"""

import pytest

from teenyfactories.where_parser import compile_where, QueryFilterError


def _norm(sql):
    return " ".join(sql.split())


class TestParameterization:
    def test_every_literal_is_a_param(self):
        sql, params = compile_where("document == 'X.pdf'")
        assert "%s" in sql
        assert "X.pdf" not in sql            # value never in SQL text
        assert params == ["X.pdf"]

    def test_numeric_value_bound_not_inlined(self):
        sql, params = compile_where("token_count >= 400")
        assert "400" not in sql
        assert params == [400]

    def test_multiple_values_ordered(self):
        sql, params = compile_where("a == 'x' and b == 'y'")
        assert params == ["x", "y"]


class TestNamespaces:
    def test_bare_field_is_payload(self):
        sql, _ = compile_where("document == 'x'")
        assert "value->>'document'" in sql

    def test_data_prefix_is_payload(self):
        sql, _ = compile_where("data.document == 'x'")
        assert "value->>'document'" in sql

    def test_meta_resolves_to_column(self):
        sql, _ = compile_where("meta.state == 'vectorised'")
        assert "d.state" in sql
        assert "value->>'state'" not in sql

    def test_bare_state_is_payload_not_column(self):
        # the collision case: address.state == 'VIC' must hit the payload
        sql, _ = compile_where("state == 'VIC'")
        assert "value->>'state'" in sql
        assert _norm(sql).startswith("d.value->>'state'")

    def test_unknown_meta_column_rejected(self):
        with pytest.raises(QueryFilterError):
            compile_where("meta.factory_name == 'other'")   # excluded from whitelist
        with pytest.raises(QueryFilterError):
            compile_where("meta.bogus == 'x'")

    def test_unknown_namespace_rejected(self):
        with pytest.raises(QueryFilterError):
            compile_where("foo.bar == 'x'")


class TestCasts:
    def test_ordering_casts_numeric_with_guard(self):
        sql, params = compile_where("token_count > 5")
        assert "::numeric" in sql
        assert "~" in sql                    # malformed-row guard present
        assert params == [5]

    def test_numeric_equality_casts(self):
        sql, _ = compile_where("chunk_index == 0")
        assert "::numeric" in sql

    def test_string_equality_no_cast(self):
        sql, _ = compile_where("document == 'x'")
        assert "::numeric" not in sql

    def test_ne_uses_is_distinct_from(self):
        sql, _ = compile_where("document != 'x'")
        assert "IS DISTINCT FROM" in sql

    def test_meta_column_not_cast(self):
        sql, _ = compile_where("meta.created_at > '2026-01-01'")
        assert "::numeric" not in sql
        assert "d.created_at >" in _norm(sql)


class TestInOperator:
    def test_in_string_list(self):
        sql, params = compile_where("document in ['a.pdf','b.pdf']")
        assert "= ANY(%s)" in sql
        assert params == [["a.pdf", "b.pdf"]]

    def test_not_in(self):
        sql, _ = compile_where("document not in ['a','b']")
        assert "<> ALL(%s)" in sql

    def test_numeric_in_guarded(self):
        sql, params = compile_where("start_page in [1,2,3]")
        assert "::numeric" in sql and "~" in sql
        assert params == [[1.0, 2.0, 3.0]]

    def test_empty_list_rejected(self):
        with pytest.raises(QueryFilterError):
            compile_where("document in []")


class TestBoolLogic:
    def test_and_or_not_precedence(self):
        sql, _ = compile_where("a == 'x' or b == 'y' and c == 'z'")
        # and binds tighter than or
        assert _norm(sql) == "(d.value->>'a' = %s OR (d.value->>'b' = %s AND d.value->>'c' = %s))"

    def test_parens_override(self):
        sql, _ = compile_where("(a == 'x' or b == 'y') and c == 'z'")
        assert _norm(sql).startswith("((d.value->>'a' = %s OR d.value->>'b' = %s) AND")

    def test_not(self):
        sql, _ = compile_where("not document == 'x'")
        assert "NOT" in sql


class TestRejection:
    @pytest.mark.parametrize("bad", [
        "document == 'x'; DROP TABLE factory_data",
        "document == 'x' --comment",
        "document == 'x' /* c */",
        "1 == 1 UNION SELECT * FROM admin.secrets",
        "document == (SELECT key FROM factory_data)",
        "pg_sleep(10) == 1",
        "document == 'x\\'",                 # backslash
        "",                                  # empty
        "document ==",                       # dangling
        "== 'x'",                            # no field
        "document = 'x'",                    # single = not an operator
        "document === 'x'",
    ])
    def test_malformed_rejected(self, bad):
        with pytest.raises(QueryFilterError):
            compile_where(bad)

    def test_semicolon_unlexable(self):
        with pytest.raises(QueryFilterError):
            compile_where("a == 'x';")


class TestCaps:
    def test_string_length_cap(self):
        with pytest.raises(QueryFilterError):
            compile_where("a == 'x' and " * 400 + "a == 'x'")

    def test_param_count_cap(self):
        huge = "[" + ",".join("'v'" for _ in range(1001)) + "]"
        with pytest.raises(QueryFilterError):
            compile_where("document in " + huge)

    def test_depth_cap(self):
        with pytest.raises(QueryFilterError):
            compile_where("(" * 60 + "a == 'x'" + ")" * 60)


class TestErrorHygiene:
    def test_error_does_not_echo_values(self):
        try:
            compile_where("document == 'super-secret-value'")  # valid actually
        except QueryFilterError:
            pass
        # a malformed one referencing a value must not echo the value
        with pytest.raises(QueryFilterError) as ei:
            compile_where("document == 'secret' bogus")
        assert "secret" not in str(ei.value)
