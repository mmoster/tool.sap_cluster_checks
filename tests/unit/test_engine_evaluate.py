"""Tests for RulesEngine._evaluate_expectation()."""

from tool.sap_cluster_checks.rules.engine import RulesEngine


def _evaluate(parsed, expectation):
    """Helper: call _evaluate_expectation on a fresh engine."""
    engine = RulesEngine()
    return engine._evaluate_expectation(parsed, expectation)


class TestExistsOperator:
    def test_value_present_passes(self):
        passed, msg, pass_msg = _evaluate({"key": "value"}, {"key": "key", "operator": "exists"})
        assert passed is True

    def test_value_none_fails(self):
        passed, msg, pass_msg = _evaluate({"key": None}, {"key": "key", "operator": "exists"})
        assert passed is False

    def test_key_missing_fails(self):
        passed, msg, pass_msg = _evaluate({}, {"key": "key", "operator": "exists"})
        assert passed is False

    def test_expected_false_inverts_value_present(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "value"}, {"key": "key", "operator": "exists", "value": False}
        )
        assert passed is False

    def test_expected_false_inverts_value_none(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None}, {"key": "key", "operator": "exists", "value": False}
        )
        assert passed is True

    def test_empty_string_counts_as_exists(self):
        passed, msg, pass_msg = _evaluate({"key": ""}, {"key": "key", "operator": "exists"})
        assert passed is True

    def test_zero_counts_as_exists(self):
        passed, msg, pass_msg = _evaluate({"key": 0}, {"key": "key", "operator": "exists"})
        assert passed is True


class TestNotExistsOperator:
    def test_value_none_passes(self):
        passed, msg, pass_msg = _evaluate({"key": None}, {"key": "key", "operator": "not_exists"})
        assert passed is True

    def test_key_missing_passes(self):
        passed, msg, pass_msg = _evaluate({}, {"key": "key", "operator": "not_exists"})
        assert passed is True

    def test_value_present_fails(self):
        passed, msg, pass_msg = _evaluate({"key": "value"}, {"key": "key", "operator": "not_exists"})
        assert passed is False


class TestEqOperator:
    def test_string_equality(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "hello"}, {"key": "key", "operator": "eq", "value": "hello"}
        )
        assert passed is True

    def test_string_inequality(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "hello"}, {"key": "key", "operator": "eq", "value": "world"}
        )
        assert passed is False

    def test_int_equality(self):
        passed, msg, pass_msg = _evaluate({"key": 42}, {"key": "key", "operator": "eq", "value": 42})
        assert passed is True

    def test_none_vs_value(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None}, {"key": "key", "operator": "eq", "value": "hello"}
        )
        assert passed is False


class TestEqEdgeCases:
    def test_string_vs_int_no_coercion(self):
        """Engine uses strict comparison - no type coercion between str and int."""
        passed, msg, pass_msg = _evaluate(
            {"key": "42"}, {"key": "key", "operator": "eq", "value": 42}
        )
        assert passed is False

    def test_int_vs_int_equality(self):
        passed, msg, pass_msg = _evaluate(
            {"key": 42}, {"key": "key", "operator": "eq", "value": 42}
        )
        assert passed is True

    def test_missing_key_vs_value(self):
        passed, msg, pass_msg = _evaluate(
            {}, {"key": "key", "operator": "eq", "value": "hello"}
        )
        assert passed is False


class TestNeOperator:
    def test_different_values_passes(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "hello"}, {"key": "key", "operator": "ne", "value": "world"}
        )
        assert passed is True

    def test_same_values_fails(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "hello"}, {"key": "key", "operator": "ne", "value": "hello"}
        )
        assert passed is False


class TestInOperator:
    def test_value_in_list(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "b"}, {"key": "key", "operator": "in", "value": ["a", "b", "c"]}
        )
        assert passed is True

    def test_value_not_in_list(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "d"}, {"key": "key", "operator": "in", "value": ["a", "b", "c"]}
        )
        assert passed is False

    def test_single_value_fallback(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "hello"}, {"key": "key", "operator": "in", "value": "hello"}
        )
        assert passed is True


class TestNotInOperator:
    def test_value_not_in_list(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "d"}, {"key": "key", "operator": "not_in", "value": ["a", "b", "c"]}
        )
        assert passed is True

    def test_value_in_list(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "b"}, {"key": "key", "operator": "not_in", "value": ["a", "b", "c"]}
        )
        assert passed is False

    def test_single_value_fallback(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "other"}, {"key": "key", "operator": "not_in", "value": "hello"}
        )
        assert passed is True


class TestInEdgeCases:
    def test_in_empty_list(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "a"}, {"key": "key", "operator": "in", "value": []}
        )
        assert passed is False

    def test_not_in_empty_list(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "a"}, {"key": "key", "operator": "not_in", "value": []}
        )
        assert passed is True


class TestContainsOperator:
    def test_substring_match(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "hello world"}, {"key": "key", "operator": "contains", "value": "world"}
        )
        assert passed is True

    def test_no_substring_match(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "hello"}, {"key": "key", "operator": "contains", "value": "world"}
        )
        assert passed is False

    def test_none_value_fails(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None}, {"key": "key", "operator": "contains", "value": "hello"}
        )
        assert passed is False


class TestRegexOperator:
    def test_pattern_match(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "version-1.2.3"}, {"key": "key", "operator": "regex", "value": r"\d+\.\d+\.\d+"}
        )
        assert passed is True

    def test_no_match(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "no-version"}, {"key": "key", "operator": "regex", "value": r"^\d+$"}
        )
        assert passed is False

    def test_none_value_fails(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None}, {"key": "key", "operator": "regex", "value": r".*"}
        )
        assert passed is False


class TestGtLtOperators:
    def test_gt_passes(self):
        passed, msg, pass_msg = _evaluate({"key": "10"}, {"key": "key", "operator": "gt", "value": "5"})
        assert passed is True

    def test_gt_fails(self):
        passed, msg, pass_msg = _evaluate({"key": "3"}, {"key": "key", "operator": "gt", "value": "5"})
        assert passed is False

    def test_lt_passes(self):
        passed, msg, pass_msg = _evaluate({"key": "3"}, {"key": "key", "operator": "lt", "value": "5"})
        assert passed is True

    def test_lt_fails(self):
        passed, msg, pass_msg = _evaluate({"key": "10"}, {"key": "key", "operator": "lt", "value": "5"})
        assert passed is False

    def test_gt_non_numeric_fails(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "abc"}, {"key": "key", "operator": "gt", "value": "5"}
        )
        assert passed is False

    def test_lt_non_numeric_fails(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "abc"}, {"key": "key", "operator": "lt", "value": "5"}
        )
        assert passed is False


class TestGtLtBoundary:
    def test_gt_equal_values_fails(self):
        passed, msg, pass_msg = _evaluate({"key": "5"}, {"key": "key", "operator": "gt", "value": "5"})
        assert passed is False

    def test_lt_equal_values_fails(self):
        passed, msg, pass_msg = _evaluate({"key": "5"}, {"key": "key", "operator": "lt", "value": "5"})
        assert passed is False

    def test_gt_none_value_fails(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None}, {"key": "key", "operator": "gt", "value": "5"}
        )
        assert passed is False

    def test_lt_none_value_fails(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None}, {"key": "key", "operator": "lt", "value": "5"}
        )
        assert passed is False


class TestInfoIfExistsOperator:
    def test_always_passes_with_value(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "value"},
            {"key": "key", "operator": "info_if_exists", "pass_message": "Found: ${key}"},
        )
        assert passed is True
        assert pass_msg == "Found: value"

    def test_always_passes_without_value(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None},
            {"key": "key", "operator": "info_if_exists", "pass_message": "Info"},
        )
        assert passed is True
        assert pass_msg is None

    def test_always_passes_no_pass_message(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "value"}, {"key": "key", "operator": "info_if_exists"}
        )
        assert passed is True
        assert pass_msg is None


class TestTemplateVariableSubstitution:
    def test_single_variable(self):
        parsed = {"name": "hello", "version": "1.0"}
        passed, msg, pass_msg = _evaluate(
            parsed,
            {
                "key": "name",
                "operator": "exists",
                "pass_message": "Name is ${name}",
            },
        )
        assert pass_msg == "Name is hello"

    def test_multiple_variables(self):
        parsed = {"name": "test", "version": "2.0"}
        passed, msg, pass_msg = _evaluate(
            parsed,
            {
                "key": "name",
                "operator": "exists",
                "pass_message": "${name} v${version}",
            },
        )
        assert pass_msg == "test v2.0"

    def test_missing_variable_preserved(self):
        parsed = {"name": "test"}
        passed, msg, pass_msg = _evaluate(
            parsed,
            {
                "key": "name",
                "operator": "exists",
                "pass_message": "${name} - ${missing}",
            },
        )
        assert pass_msg == "test - ${missing}"


class TestUnknownOperator:
    def test_unknown_operator_returns_false(self):
        passed, msg, pass_msg = _evaluate(
            {"key": "value"},
            {"key": "key", "operator": "unknown_op", "value": "x"},
        )
        assert passed is False
        assert "Unknown operator" in msg


class TestCustomMessage:
    def test_custom_fail_message(self):
        passed, msg, pass_msg = _evaluate(
            {"key": None},
            {"key": "key", "operator": "exists", "message": "Key is required!"},
        )
        assert msg == "Key is required!"

    def test_default_fail_message(self):
        passed, msg, pass_msg = _evaluate({"key": None}, {"key": "mykey", "operator": "exists"})
        assert "mykey" in msg
