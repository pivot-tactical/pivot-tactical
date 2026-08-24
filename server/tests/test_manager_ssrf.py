"""The updater only ever talks to GitHub over TLS (SSRF guard on §3.7 fetches)."""

import pytest

from pivot.updates.manager import _http_download, _http_get, _is_safe_github_url


@pytest.mark.parametrize(
    "url",
    [
        "https://api.github.com/repos/foo/bar/releases",
        "https://github.com/foo/bar/releases/download/v1/asset.zip",
        "https://objects.githubusercontent.com/github-production-release-asset/1",
        "https://raw.githubusercontent.com/foo/bar/main/x",
    ],
)
def test_real_github_urls_are_allowed(url):
    assert _is_safe_github_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://169.254.169.254/latest/meta-data/",
        # Plaintext is rejected even for a genuine GitHub host: an update
        # fetched over http can be swapped in transit.
        "http://github.com/foo/bar",
        "https://malicious.example/api.github.com",
        # Host-lookalikes: userinfo, a suffix that only *contains* the domain,
        # and a path that merely mentions it.
        "https://github.com@malicious.example/x",
        "https://github.com.evil.example/x",
        "https://evil.example/github.com",
        "",
    ],
)
def test_untrusted_urls_are_rejected(url):
    assert _is_safe_github_url(url) is False


def test_http_get_refuses_an_untrusted_url():
    with pytest.raises(ValueError, match="Invalid or unsafe URL"):
        _http_get("file:///etc/passwd")


def test_http_download_refuses_an_untrusted_url(tmp_path):
    with pytest.raises(ValueError, match="Invalid or unsafe URL"):
        _http_download("https://malicious.example/api.github.com", tmp_path / "asset.zip")
