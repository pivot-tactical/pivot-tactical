"""Version management & updates (spec §3.7).

The only part of the system that touches the internet, and strictly out-of-band:
update checks/downloads are explicit instructor actions, never run during an
active session, and always replaceable by an offline import for air-gapped sites.

This package separates the *pure, testable* policy (release ordering, update
channel filtering, SHA-256 verification, retained-version selection,
schema-boundary checks) from the *platform-specific* mechanics (downloading from
GitHub, the staged folder swap performed by the updater helper). The training
runtime never imports or depends on any of this.
"""

from pivot.updates.manager import (
    Release,
    UpdateManager,
    UpdatePlan,
    classify_release,
    default_asset_pattern,
    filter_channel,
    order_releases,
    verify_sha256,
)

__all__ = [
    "Release",
    "UpdateManager",
    "UpdatePlan",
    "classify_release",
    "default_asset_pattern",
    "filter_channel",
    "order_releases",
    "verify_sha256",
]
