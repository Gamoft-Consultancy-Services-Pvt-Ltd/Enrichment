"""
LEAD-49 — SignalEvaluator (Epic 6: Lead Scoring Runtime)

Evaluates machine-readable SignalConditions against LeadFeatures.

Hard rules (acceptance criteria):
- Missing field  -> False for every operator except exists / not_exists.
- Type mismatch  -> False, never an exception. Scoring must not crash on
  messy enriched data; an unevaluable condition is simply "did not fire".
"""

from __future__ import annotations

from numbers import Number
from typing import Any

from schemas.lead_features import LeadFeatures
from schemas.signal_set import ConditionOperator, SignalCondition

_MISSING = object()


def _as_number(value: Any) -> float | None:
    """Coerce to float for numeric comparison; None if not a real number.

    Booleans are rejected: `True > 0` is technically valid Python but is
    almost always a data bug in this context.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Number):
        return float(value)
    return None


class SignalEvaluator:
    """Stateless condition evaluator. Pure logic — no I/O, no LLM."""

    def evaluate(self, condition: SignalCondition, features: LeadFeatures) -> bool:
        op = condition.operator
        actual = features.get_field(condition.field, _MISSING)

        if op == ConditionOperator.EXISTS:
            return actual is not _MISSING and actual is not None
        if op == ConditionOperator.NOT_EXISTS:
            return actual is _MISSING or actual is None

        if actual is _MISSING or actual is None:
            return False

        try:
            return self._compare(op, actual, condition.value, condition.value2)
        except (TypeError, ValueError):
            # LEAD-49-S10: incompatible types never raise
            return False

    # ------------------------------------------------------------------

    def _compare(self, op: ConditionOperator, actual: Any,
                 expected: Any, expected2: Any) -> bool:
        if op == ConditionOperator.EQ:
            return actual == expected
        if op == ConditionOperator.NEQ:
            return actual != expected

        if op in (ConditionOperator.GT, ConditionOperator.GTE,
                  ConditionOperator.LT, ConditionOperator.LTE):
            a, e = _as_number(actual), _as_number(expected)
            if a is None or e is None:
                return False  # LEAD-49-S3: non-numeric comparison -> False
            return {
                ConditionOperator.GT: a > e,
                ConditionOperator.GTE: a >= e,
                ConditionOperator.LT: a < e,
                ConditionOperator.LTE: a <= e,
            }[op]

        if op == ConditionOperator.IN:
            return actual in expected
        if op == ConditionOperator.NOT_IN:
            return actual not in expected

        if op == ConditionOperator.BETWEEN:  # inclusive bounds
            a = _as_number(actual)
            lo, hi = _as_number(expected), _as_number(expected2)
            if a is None or lo is None or hi is None:
                return False
            return lo <= a <= hi

        if op == ConditionOperator.CONTAINS:
            return self._contains(actual, expected)
        if op == ConditionOperator.NOT_CONTAINS:
            return not self._contains(actual, expected)

        return False  # unknown operator: fail closed

    @staticmethod
    def _contains(actual: Any, expected: Any) -> bool:
        if isinstance(actual, str):
            return str(expected).lower() in actual.lower()
        if isinstance(actual, (list, tuple, set)):
            return expected in actual
        raise TypeError(f"contains not supported for {type(actual).__name__}")
