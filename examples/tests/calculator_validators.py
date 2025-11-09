"""Helpers that validate generated calculator UIs via the jsdom bridge."""

from __future__ import annotations

def _has_id(html: str, identifier: str) -> bool:
    marker_double = f'id="{identifier}"'
    marker_single = f"id='{identifier}'"
    return marker_double in html or marker_single in html


def ensure_trig_calculator_behaviour(html: str) -> None:
    """Validates that trig calculator markup includes required elements."""

    alternatives = [
        ['angleDegrees', 'degreesInput', 'trigAngleDegrees'],
        ['angleRadians', 'radiansInput', 'trigAngleRadians'],
        ['sinResult', 'sinValue', 'trigSin'],
        ['cosResult', 'cosValue', 'trigCos'],
    ]
    missing_groups = [
        group
        for group in alternatives
        if not any(_has_id(html, candidate) for candidate in group)
    ]
    assert not missing_groups, f"Missing expected trig calculator ids: {missing_groups}"


def ensure_basic_calculator_behaviour(html: str) -> None:
    """Validates the basic calculator markup by checking required ids."""

    alternatives = [
        ['numberA'],
        ['numberB'],
        ['operator'],
        ['compute', 'computeButton', 'calculateButton', 'trigCompute'],
        ['result', 'output', 'resultValue'],
    ]
    missing_groups = [
        group
        for group in alternatives
        if not any(_has_id(html, candidate) for candidate in group)
    ]
    assert not missing_groups, f"Missing expected calculator ids: {missing_groups}"
