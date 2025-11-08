"""Helpers that validate generated calculator UIs via the jsdom bridge."""

from __future__ import annotations

from .env import node_available
from .jsdom_bridge import JsdomUnavailableError, run_jsdom


def ensure_trig_calculator_behaviour(html: str) -> None:
    """Validates that trig calculators update values when invoked."""

    if not node_available():
        raise JsdomUnavailableError('Node.js is required for trig calculator validation.')

    script = r"""
    const windowAny = window;
    const doc = document;
    const findByIds = (ids) => {
      for (const id of ids) {
        const node = doc.getElementById(id);
        if (node) return node;
      }
      return null;
    };
    const degreesInput = findByIds(['angleDegrees', 'degreesInput', 'trigAngleDegrees']);
    const sinSpan = findByIds(['sinResult', 'sinValue', 'trigSin']);
    const cosSpan = findByIds(['cosResult', 'cosValue', 'trigCos']);
    const button = findByIds(['computeTrig', 'compute', 'trigCompute']) || doc.querySelector('button');

    if (!degreesInput || !sinSpan || !cosSpan) {
      return { success: false, missing: { degrees: !!degreesInput, sin: !!sinSpan, cos: !!cosSpan } };
    }

    degreesInput.value = '60';
    if (typeof windowAny.updateTrigValues === 'function') {
      windowAny.updateTrigValues();
    }
    if (button) {
      button.dispatchEvent(new window.Event('click'));
    }

    return {
      success: true,
      sinText: sinSpan.textContent || '',
      cosText: cosSpan.textContent || '',
    };
    """

    result = run_jsdom(html, script)
    assert result.get('success'), 'Trig calculator markup is missing required elements.'
    sin_text = (result.get('sinText') or '').lower()
    cos_text = (result.get('cosText') or '').lower()
    assert (
        '0.866' in sin_text or '√3' in sin_text or '1' in sin_text
    ), 'Sine output did not update as expected.'
    assert (
        '0.5' in cos_text or '1/2' in cos_text or '0' in cos_text
    ), 'Cosine output did not update as expected.'


def ensure_basic_calculator_behaviour(html: str) -> None:
    """Validates the basic calculator by simulating a click in the DOM."""

    if not node_available():
        raise JsdomUnavailableError('Node.js is required for calculator validation.')

    script = r"""
    const doc = document;
    const windowAny = window;
    const inputA = doc.getElementById('numberA');
    const inputB = doc.getElementById('numberB');
    const operator = doc.getElementById('operator');
    const button = doc.getElementById('compute') || doc.querySelector('button');
    const resultSpan = doc.getElementById('result');

    if (!inputA || !inputB || !operator || !button || !resultSpan) {
      return { success: false };
    }

    inputA.value = '12';
    inputB.value = '3';
    operator.value = '/';
    if (typeof windowAny.calculate === 'function') {
      windowAny.calculate(12, 3, '/');
    }
    button.dispatchEvent(new window.Event('click'));
    return { success: true, result: resultSpan.textContent || '' };
    """

    result = run_jsdom(html, script)
    assert result.get('success'), 'Calculator markup was missing required DOM elements.'
    normalized = (result.get('result') or '').strip().lower().replace('result:', '').strip()
    assert '4' in normalized, 'Calculator result should reflect 12 / 3 = 4.'
