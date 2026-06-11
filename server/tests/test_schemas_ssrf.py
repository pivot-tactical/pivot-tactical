import pytest
from pydantic import ValidationError

from pivot.api.schemas import ApplyUpdateRequest


def test_apply_update_request_valid_github_urls():
    req = ApplyUpdateRequest(
        tag="1.0.0",
        asset_name="PIVOT.zip",
        asset_url="https://github.com/pivot-tactical/pivot/releases/download/v1.0.0/PIVOT.zip",
        sha256_url="https://api.github.com/repos/pivot/releases/assets/123",
        sig_url="https://objects.githubusercontent.com/github-production-release-asset-2e65be/..."
    )
    assert req.asset_url.startswith("https://github.com")

def test_apply_update_request_invalid_urls():
    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="1.0.0",
            asset_name="PIVOT.zip",
            asset_url="http://github.com/pivot-tactical/pivot/releases/download/v1.0.0/PIVOT.zip",
        )
    assert "must be an https URL" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="1.0.0",
            asset_name="PIVOT.zip",
            asset_url="https://evil.com/malicious.zip",
        )
    assert "URL must point to GitHub" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="1.0.0",
            asset_name="PIVOT.zip",
            asset_url="https://github.com.evil.com/malicious.zip",
        )
    assert "URL must point to GitHub" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="1.0.0",
            asset_name="PIVOT.zip",
            asset_url="https://evil.githubusercontent.com/malicious.zip",
        )
    assert "URL must point to GitHub" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="1.0.0",
            asset_name="PIVOT.zip",
            asset_url="https://github.com@evil.com/malicious.zip",
        )
    assert "URL must point to GitHub" in str(exc_info.value)

def test_apply_update_request_path_traversal():
    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="../../etc",
            asset_name="PIVOT.zip",
            asset_url="https://github.com/pivot-tactical/pivot/releases/download/v1.0.0/PIVOT.zip",
        )
    assert "must be a valid filename without path separators" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="1.0.0",
            asset_name="../../etc/passwd",
            asset_url="https://github.com/pivot-tactical/pivot/releases/download/v1.0.0/PIVOT.zip",
        )
    assert "must be a valid filename without path separators" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        ApplyUpdateRequest(
            tag="1.0.0",
            asset_name="malicious\\..\\..\\path",
            asset_url="https://github.com/pivot-tactical/pivot/releases/download/v1.0.0/PIVOT.zip",
        )
    assert "must be a valid filename without path separators" in str(exc_info.value)
