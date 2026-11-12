"""Soak monitor: pure row/summary logic and a quota-safe CLI smoke with a fake panel."""
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import soak  # noqa: E402


def test_build_row_counts_sessions_and_flags_login_issue():
    health = {
        "version": "0.15.0",
        "running_workers": 2,
        "unresolved_unknown": 3,
        "disk_free_mb": 1000,
        "disk_low": False,
        "effort_below_requested_24h": 1,
        "effort_mixed_24h": 2,
    }
    accounts = [
        {"session": "connected", "runtime": "online"},
        {"session": "unverified", "runtime": "online"},
        {"session": "out", "runtime": "stopped"},
        {"session": "connected", "archived_at": "2026-01-01"},
    ]
    row = soak.build_row(health, accounts, {"queued": 2, "running": 1}, ts="T")
    assert row["accounts"] == 3  # archived account excluded
    assert row["sessions_connected"] == 1
    assert row["sessions_unverified"] == 1
    assert row["sessions_out"] == 1
    assert row["login_issue"] is True  # an online worker is unverified
    assert row["queued"] == 2 and row["running"] == 1
    assert row["effort_below_24h"] == 1


def test_build_row_no_accounts_is_not_a_login_issue():
    row = soak.build_row({}, [], {}, ts="T")
    assert row["login_issue"] is False
    assert row["accounts"] == 0


def test_unreachable_row_has_every_field_and_note():
    row = soak.unreachable_row("boom")
    assert set(row) == set(soak.FIELDS)
    assert row["reachable"] is False and "boom" in row["note"]


def test_summarize_counts_availability_and_issues():
    rows = [
        soak.build_row({}, [{"session": "connected", "runtime": "online"}], {}, ts="1"),
        soak.build_row({}, [{"session": "unverified", "runtime": "online"}], {}, ts="2"),
        soak.unreachable_row("down", "3"),
    ]
    summary = soak.summarize(rows)
    assert summary["cycles"] == 3
    assert summary["reachable"] == 2
    assert summary["availability_percent"] == 66.67
    assert summary["login_issue_cycles"] == 1


def test_write_rows_is_append_safe(tmp_path):
    path = tmp_path / "soak.csv"
    soak.write_rows(path, [soak.build_row({}, [], {}, ts="1")])
    soak.write_rows(path, [soak.build_row({}, [], {}, ts="2")], append=True)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert [row["ts_utc"] for row in rows] == ["1", "2"]
    assert list(rows[0]) == list(soak.FIELDS)


class _FakePanel:
    def __init__(self, *args, **kwargs):
        self.submitted = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def login(self):
        return self

    def health(self):
        return {"version": "0.15.0", "running_workers": 1, "unresolved_unknown": 0}

    def get_json(self, path):
        assert path == "/api/accounts"
        return [{"id": "a", "runtime": "online", "session": "connected"}]

    def queue(self):
        return {"queued": 0, "running": 0}

    def post_json(self, path, payload):
        assert path == "/api/generate"
        self.submitted.append(payload)
        return {"id": "job-123"}


def test_cli_records_cycles_and_quota_safe_submission(tmp_path, monkeypatch):
    monkeypatch.setattr(soak, "Panel", _FakePanel)
    out = tmp_path / "soak.csv"
    summary = tmp_path / "summary.json"
    rc = soak.main(
        [
            "--password",
            "test-password",
            "--cycles",
            "2",
            "--interval",
            "0",
            "--out",
            str(out),
            "--summary",
            str(summary),
        ]
    )
    assert rc == 0
    rows = list(csv.DictReader(out.open(encoding="utf-8")))
    assert len(rows) == 2
    data = json.loads(summary.read_text(encoding="utf-8"))
    assert data["cycles"] == 2 and data["submitted_jobs"] == 0


def test_cli_resumes_an_existing_csv_without_duplicate_header(tmp_path, monkeypatch):
    monkeypatch.setattr(soak, "Panel", _FakePanel)
    out = tmp_path / "soak.csv"
    args = [
        "--password", "test-password",
        "--cycles", "1",
        "--interval", "0",
        "--out", str(out),
    ]
    assert soak.main(args) == 0
    assert soak.main(args) == 0
    lines = out.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("ts_utc,")
    assert sum(1 for line in lines if line.startswith("ts_utc,")) == 1
    assert len(list(csv.DictReader(out.open(encoding="utf-8")))) == 2


def test_submit_requires_explicit_budget():
    try:
        soak.main(["--password", "x", "--submit", "--cycles", "1", "--out", "unused.csv"])
    except SystemExit as exc:
        assert "requires --yes" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected SystemExit")
