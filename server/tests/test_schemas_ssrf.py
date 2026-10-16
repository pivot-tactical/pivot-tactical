import pytest
from pydantic import ValidationError

from pivot.api.schemas import ApplyUpdateRequest, RollbackRequest


def test_apply_update_request_valid_github_urls():
    req = ApplyUpdateRequest(
        tag="1.0.0",
        asset_name="PIVOT.zip",
        asset_url="https://github.com/pivot-tactical/pivot/releases/download/v1.0.0/PIVOT.zip",
        sha256_url="https://api.github.com/repos/pivot/releases/assets/123",
        sig_url="https://objects.githubusercontent.com/github-production-release-asset-2e65be/...",
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


def test_rollback_request_parsing():
    req_default = RollbackRequest()
    assert req_default.tag is None

    req_omitted = RollbackRequest.model_validate({})
    assert req_omitted.tag is None

    req_explicit_none = RollbackRequest(tag=None)
    assert req_explicit_none.tag is None

    req_with_tag = RollbackRequest(tag="1.0.0")
    assert req_with_tag.tag == "1.0.0"

    req_from_dict = RollbackRequest.model_validate({"tag": "1.1.0"})
    assert req_from_dict.tag == "1.1.0"
