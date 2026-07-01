"""Tests for the licence policy enforcement (spec §13.7)."""

from unittest.mock import patch

from pivot.tools.licenses import (
    Finding,
    is_denied,
    main,
    scan_environment,
    violations,
)


def test_denies_gpl_and_agpl():
    assert is_denied("GPL-3.0") is True
    assert is_denied("GNU General Public License v3 (GPLv3)") is True
    assert is_denied("AGPL-3.0") is True
    assert is_denied("GNU Affero General Public License v3") is True


def test_allows_permissive_and_lgpl():
    assert is_denied("MIT License") is False
    assert is_denied("BSD-3-Clause") is False
    assert is_denied("Apache-2.0") is False
    # Weak copyleft is allowed (dynamically linked, §13.4).
    assert is_denied("GNU Lesser General Public License v3 (LGPLv3)") is False
    assert is_denied("LGPL-2.1") is False


def test_allows_gpl_with_linking_exception():
    # PyInstaller-style licence is permitted (build tool, §13.7).
    assert is_denied("GPL-2.0-with-linking-exception") is False
    assert is_denied("GNU GPL with a linking exception") is False


def test_empty_license_not_denied():
    assert is_denied("") is False
    assert is_denied("UNKNOWN") is False


def test_scan_environment_runs():
    findings = scan_environment()
    assert findings
    assert all(isinstance(f, Finding) for f in findings)


def test_core_runtime_deps_have_no_violations():
    """The core install (numpy/scipy/fastapi/sqlalchemy/pydantic/...) must pass
    the policy — acceptance criteria #25/#26."""
    findings = scan_environment()
    runtime = {
        "numpy",
        "scipy",
        "fastapi",
        "starlette",
        "uvicorn",
        "sqlalchemy",
        "pydantic",
        "soundfile",
        "websockets",
    }
    relevant = [f for f in findings if f.name.lower() in runtime]
    assert violations(relevant) == []


def test_is_denied_none():
    assert is_denied(None) is False


def test_is_denied_case_insensitivity():
    assert is_denied("gpl-3.0") is True
    assert is_denied("agpl-3.0") is True
    assert is_denied("gnu general public license") is True
    assert is_denied("affero general public license") is True


def test_is_denied_word_boundaries():
    assert is_denied("NotGPL") is False
    assert is_denied("MyGPL") is False
    # Verify that valid names do hit the acronym
    assert is_denied("GPL") is True
    assert is_denied("AGPL") is True


def test_is_denied_historical_lgpl():
    assert is_denied("Library General Public License") is False


def test_scan_environment_empty_name(monkeypatch):
    class MockDist:
        def __init__(self, name):
            self.metadata = {"Name": name}
            self.version = "1.0"

        def get(self, key):
            return self.metadata.get(key)

    def mock_distributions():
        return [MockDist(""), MockDist("  ")]

    monkeypatch.setattr("pivot.tools.licenses.metadata.distributions", mock_distributions)
    findings = scan_environment()
    assert findings == []


def test_main_no_violations(capsys, monkeypatch):
    monkeypatch.setattr("pivot.tools.licenses.scan_environment", lambda: [])
    assert main([]) == 0
    captured = capsys.readouterr()
    assert captured.out == ""

    assert main(["--check"]) == 0
    captured = capsys.readouterr()
    assert "Licence scan passed" in captured.out


def test_main_with_violations(capsys, monkeypatch):
    bad_finding = Finding(name="bad-lib", version="1.0", license="GPL", denied=True, reason="")
    monkeypatch.setattr("pivot.tools.licenses.scan_environment", lambda: [bad_finding])

    # Without check, it should just print and return 0
    assert main([]) == 0
    captured = capsys.readouterr()
    assert "Licence policy violations" in captured.out
    assert "bad-lib" in captured.out

    # With check, it should return 1
    assert main(["--check"]) == 1


def test_main_system_exit(monkeypatch):
    with patch("sys.argv", ["licenses.py"]):
        assert main() == 0
