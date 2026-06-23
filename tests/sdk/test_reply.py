"""Unit tests for :func:`robotsix_agent_comm.sdk.reply.reply_text`."""

from __future__ import annotations

from robotsix_agent_comm.sdk.reply import reply_text


def test_string_reply_returned_verbatim() -> None:
    assert reply_text({"reply": "hello"}) == "hello"


def test_non_string_reply_coerced() -> None:
    assert reply_text({"reply": 42}) == "42"
    assert reply_text({"reply": 0}) == "0"
    assert reply_text({"reply": True}) == "True"


def test_missing_reply_falls_back_to_default() -> None:
    assert reply_text({"other": 1}) == ""


def test_none_reply_falls_back_to_default() -> None:
    assert reply_text({"reply": None}) == ""


def test_empty_string_reply_falls_back_to_default() -> None:
    assert reply_text({"reply": ""}) == ""


def test_none_body_falls_back_to_default() -> None:
    assert reply_text(None) == ""


def test_non_mapping_body_falls_back_to_default() -> None:
    assert reply_text(42) == ""
    assert reply_text("a string") == ""
    assert reply_text([1, 2, 3]) == ""


def test_custom_default_returned() -> None:
    fallback = "no reply available"
    assert reply_text(None, default=fallback) == fallback
    assert reply_text({"reply": None}, default=fallback) == fallback
    assert reply_text({"reply": ""}, default=fallback) == fallback
    assert reply_text({"other": 1}, default=fallback) == fallback


def test_default_defaults_to_empty_string() -> None:
    assert reply_text(None) == ""
