"""Tests for the tolerant JSON parser in the graph (`app.graph._parse_json`).

A live run against a real model (Opus 4.8) exposed that a bare `json.loads()`
failed whenever the model wrapped its JSON in markdown fences or surrounding
prose — the deterministic mock never did, so no offline test caught it. These
cases pin the tolerant behavior so a future edit can't silently reintroduce that
"(no draft)" failure.
"""

import json

import pytest

from app.graph import _parse_json


def test_clean_json_parses():
    assert _parse_json('{"risk": "low", "findings": []}') == {
        "risk": "low",
        "findings": [],
    }


def test_fenced_json_with_language_tag_parses():
    raw = '```json\n{"risk": "high", "findings": ["a"]}\n```'
    assert _parse_json(raw) == {"risk": "high", "findings": ["a"]}


def test_fenced_json_without_language_tag_parses():
    raw = '```\n{"ok": true}\n```'
    assert _parse_json(raw) == {"ok": True}


def test_prose_wrapped_json_parses():
    raw = 'Here is the review:\n{"risk": "medium", "findings": ["x", "y"]}\nHope that helps.'
    assert _parse_json(raw) == {"risk": "medium", "findings": ["x", "y"]}


def test_nested_object_survives_extraction():
    # The outermost {...} must be captured, not just the first inner brace pair.
    raw = 'Sure:\n{"eval": {"passed": true, "score": 0.9}}\ndone'
    assert _parse_json(raw) == {"eval": {"passed": True, "score": 0.9}}


def test_surrounding_whitespace_parses():
    assert _parse_json('\n\n  {"a": 1}  \n\n') == {"a": 1}


def test_braces_inside_string_values_are_preserved():
    # Clean JSON with braces in a string value must round-trip unchanged.
    assert _parse_json('{"summary": "use {braces} with care"}') == {
        "summary": "use {braces} with care"
    }


def test_no_json_object_raises():
    with pytest.raises(json.JSONDecodeError):
        _parse_json("there is no json object here at all")
