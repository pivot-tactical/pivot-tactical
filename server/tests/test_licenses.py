"""Tests for the licence policy enforcement (spec §13.7)."""

from pivot.tools.licenses import (
    Finding,
    is_denied,
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
    runtime = {"numpy", "scipy", "fastapi", "starlette", "uvicorn", "sqlalchemy",
               "pydantic", "soundfile", "websockets"}
    relevant = [f for f in findings if f.name.lower() in runtime]
    assert violations(relevant) == []
