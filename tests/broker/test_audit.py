"""Dedicated unit tests for the ``_AuditLogger`` class."""

from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

from robotsix_agent_comm.broker._audit import _AuditLogger


class TestAuditLoggerInit:
    def test_init_with_valid_path_opens_file(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        assert logger._file is not None
        logger.close()

    def test_init_with_none_path_no_file(self) -> None:
        logger = _AuditLogger(None)
        assert logger._file is None
        logger.close()

    def test_init_with_invalid_path_raises(self, tmp_path: Path) -> None:
        with pytest.raises((IsADirectoryError, OSError)):
            _AuditLogger(str(tmp_path))  # tmp_path is a directory


class TestAuditLoggerLog:
    def test_log_writes_json_line(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        logger.log("register", "agent-1", path="/agents", status=201, detail="ok")
        logger.close()

        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["action"] == "register"
        assert record["agent_id"] == "agent-1"
        assert record["path"] == "/agents"
        assert record["status"] == 201
        assert record["detail"] == "ok"
        assert "timestamp" in record

    def test_log_writes_newline_after_each_entry(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        logger.log("a", "1")
        logger.log("b", "2")
        logger.close()

        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        assert content.endswith("\n")
        assert content.count("\n") == 2

    def test_multiple_log_entries_append(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        for i in range(5):
            logger.log("action", f"agent-{i}")
        logger.close()

        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == 5
        for i, line in enumerate(lines):
            record = json.loads(line)
            assert record["agent_id"] == f"agent-{i}"

    def test_log_to_stdout_when_path_none(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        logger = _AuditLogger(None)
        logger.log("register", "agent-stdout", path="/x", status=200, detail="hi")
        logger.close()

        captured = capsys.readouterr()
        assert captured.out != ""
        record = json.loads(captured.out.strip())
        assert record["action"] == "register"
        assert record["agent_id"] == "agent-stdout"

    def test_log_special_characters(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        detail = "unicode: café – emoji: 🚀 — backslash: \\ — quotes: \"'"
        logger.log("test", "agent-🦀", path="/föö/bär", status=418, detail=detail)
        logger.close()

        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["agent_id"] == "agent-🦀"
        assert record["path"] == "/föö/bär"
        assert record["status"] == 418
        assert record["detail"] == detail

    def test_log_large_detail_string(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        detail = "x" * 100_000
        logger.log("big", "agent", detail=detail)
        logger.close()

        with open(log_path, encoding="utf-8") as f:
            record = json.loads(f.readline())
        assert record["detail"] == detail


class TestAuditLoggerClose:
    def test_close_closes_file_handle(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        logger.close()
        # After close, _file is still the object but .closed should be True.
        assert logger._file is not None
        assert logger._file.closed

    def test_double_close_is_safe(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        logger.close()
        logger.close()  # Should not raise.


class TestAuditLoggerThreadSafety:
    def test_concurrent_log_writes_all_entries(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        n_threads = 10
        n_entries_per_thread = 50

        def write_entries(thread_id: int) -> None:
            for i in range(n_entries_per_thread):
                logger.log("action", f"t{thread_id}-{i}")

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [executor.submit(write_entries, t) for t in range(n_threads)]
            for future in as_completed(futures):
                future.result()

        logger.close()

        with open(log_path, encoding="utf-8") as f:
            lines = f.readlines()
        assert len(lines) == n_threads * n_entries_per_thread

    def test_concurrent_log_no_data_corruption(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        n_threads = 8
        n_entries_per_thread = 25

        def write_entries(thread_id: int) -> None:
            for i in range(n_entries_per_thread):
                logger.log("act", f"agent-{thread_id}", detail=f"entry-{i}")

        with ThreadPoolExecutor(max_workers=n_threads) as executor:
            futures = [executor.submit(write_entries, t) for t in range(n_threads)]
            for future in as_completed(futures):
                future.result()

        logger.close()

        with open(log_path, encoding="utf-8") as f:
            for line in f:
                record = json.loads(line)  # Each line must be valid JSON.
                assert "timestamp" in record
                assert record["action"] == "act"


class TestAuditLoggerFlush:
    def test_log_flushes_to_disk(self, tmp_path: Path) -> None:
        log_path = os.path.join(tmp_path, "audit.jsonl")
        logger = _AuditLogger(log_path)
        logger.log("flush-test", "agent")
        # Do NOT close — read what's already on disk (should be flushed).
        with open(log_path, encoding="utf-8") as f:
            content = f.read()
        assert content != ""
        record = json.loads(content.strip())
        assert record["action"] == "flush-test"
        logger.close()
