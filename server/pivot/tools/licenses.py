"""Build-time licence verification (spec §13.7).

Enforces the no-contravention requirement continuously: the build fails if any
*runtime* dependency reports a strong-copyleft (GPL/AGPL) licence that would be
linked into the distributed executable, unless it is explicitly allow-listed with
justification. Weak copyleft (LGPL) is allowed because PIVOT links it dynamically
and ships the relink instructions (REBUILD-LGPL.md, §13.4). Build-only tools
(PyInstaller's GPL-with-linking-exception) are allow-listed since they are not
redistributed inside the binary.

    python -m pivot.tools.licenses            # print the inventory
    python -m pivot.tools.licenses --check    # exit non-zero on a violation
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from importlib import metadata

# Strong copyleft that must never be linked into the distributed exe (§13.7).
# Catches both the acronyms (GPL/AGPL) and the spelled-out names.
_DENY_PATTERNS = [
    re.compile(r"\bA?GPL", re.IGNORECASE),                   # GPL / AGPL acronyms
    re.compile(r"Affero General Public License", re.IGNORECASE),
    re.compile(r"\bGeneral Public License", re.IGNORECASE),  # spelled-out GPL
]
# Patterns that look like GPL but are explicitly permitted. Checked FIRST, so a
# "Lesser General Public License" (LGPL) is allowed despite containing the GPL
# substring, and any "... with a linking exception" (PyInstaller) is allowed.
_ALLOW_LICENSE_PATTERNS = [
    re.compile(r"LGPL", re.IGNORECASE),                          # weak copyleft, dynamic link
    re.compile(r"Lesser General Public License", re.IGNORECASE),
    re.compile(r"Library General Public License", re.IGNORECASE),  # historical LGPL name
    re.compile(r"exception", re.IGNORECASE),                     # linking/classpath exceptions
]
# Distributions allow-listed by name (build tools not shipped in the binary).
_ALLOW_DISTRIBUTIONS = {
    "pyinstaller": "GPL-2.0 with a linking exception; build tool, not redistributed.",
    "pyinstaller-hooks-contrib": "Part of PyInstaller tooling; not redistributed.",
}


@dataclass
class Finding:
    name: str
    version: str
    license: str
    denied: bool
    reason: str = ""


def is_denied(license_id: str) -> bool:
    """True if a licence string denotes denied strong copyleft (§13.7)."""
    if not license_id:
        return False
    if any(p.search(license_id) for p in _ALLOW_LICENSE_PATTERNS):
        return False
    return any(p.search(license_id) for p in _DENY_PATTERNS)


def normalize_license(dist: metadata.Distribution) -> str:
    """Best-effort licence identifier from distribution metadata."""
    meta = dist.metadata
    # Prefer SPDX-ish classifiers, then the License field.
    classifiers = meta.get_all("Classifier") or []
    for c in classifiers:
        if c.startswith("License :: "):
            return c.split("::")[-1].strip()
    lic = meta.get("License")
    if lic and lic != "UNKNOWN":
        return lic.strip().splitlines()[0][:80]
    return "UNKNOWN"


def scan_environment() -> list[Finding]:
    findings: list[Finding] = []
    for dist in metadata.distributions():
        name = (dist.metadata.get("Name") or "").strip()
        if not name:
            continue
        license_id = normalize_license(dist)
        denied = is_denied(license_id)
        reason = ""
        if denied and name.lower() in _ALLOW_DISTRIBUTIONS:
            denied = False
            reason = _ALLOW_DISTRIBUTIONS[name.lower()]
        findings.append(
            Finding(
                name=name,
                version=dist.version or "?",
                license=license_id,
                denied=denied,
                reason=reason,
            )
        )
    return sorted(findings, key=lambda f: f.name.lower())


def violations(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.denied]


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    check = "--check" in argv
    findings = scan_environment()

    for f in findings:
        flag = "DENIED" if f.denied else ("allow" if f.reason else "ok")
        print(f"{flag:>6}  {f.name:<28} {f.version:<12} {f.license}")

    bad = violations(findings)
    if bad:
        print("\nLicence policy violations (GPL/AGPL strong copyleft, §13.7):")
        for f in bad:
            print(f"  - {f.name} {f.version}: {f.license}")
        if check:
            return 1
    elif check:
        print("\nLicence scan passed: no strong-copyleft dependency in the bundle.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
