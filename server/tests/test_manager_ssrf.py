import pytest
from pivot.updates.manager import _is_safe_github_url, _http_get, _http_download

def test_is_safe_github_url():
    assert _is_safe_github_url("https://api.github.com/repos/foo/bar") is True
    assert _is_safe_github_url("https://github.com/foo/bar") is True
    assert _is_safe_github_url("https://objects.githubusercontent.com/foo") is True
    assert _is_safe_github_url("https://foo.github.com/bar") is True
    assert _is_safe_github_url("http://github.com/foo") is True

    assert _is_safe_github_url("file:///etc/passwd") is False
    assert _is_safe_github_url("https://malicious.com") is False
    assert _is_safe_github_url("https://github.com@malicious.com") is False
    assert _is_safe_github_url("https://github.com.evil.com") is False
    assert _is_safe_github_url("https://evil.com/github.com") is False

def test_http_get_ssrf_protection():
    with pytest.raises(ValueError, match="Invalid or unsafe URL"):
        _http_get("file:///etc/passwd")

    with pytest.raises(ValueError, match="Invalid or unsafe URL"):
        _http_get("https://malicious.com/api.github.com")

def test_http_download_ssrf_protection(tmp_path):
    dest = tmp_path / "test.file"
    with pytest.raises(ValueError, match="Invalid or unsafe URL"):
        _http_download("file:///etc/passwd", dest)

    with pytest.raises(ValueError, match="Invalid or unsafe URL"):
        _http_download("https://malicious.com/api.github.com", dest)
