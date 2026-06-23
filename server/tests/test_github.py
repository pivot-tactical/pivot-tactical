import json
import urllib.error
import urllib.request
from unittest import mock

import pytest

from pivot.updates.github import fetch_releases, ttl_cache


@pytest.fixture(autouse=True)
def clear_fetch_releases_cache():
    """Ensure tests don't pollute the global cache."""
    fetch_releases.cache_clear()
    yield
    fetch_releases.cache_clear()


def test_fetch_releases_success():
    mock_data = [
        {"tag_name": "v1.0.0", "name": "Release 1"},
        {"tag_name": "v0.9.0", "name": "Release 0"},
    ]

    mock_response = mock.MagicMock()
    mock_response.read.return_value = json.dumps(mock_data).encode("utf-8")

    mock_urlopen = mock.MagicMock(
        return_value=mock.MagicMock(__enter__=mock.MagicMock(return_value=mock_response))
    )

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        result = fetch_releases("owner/repo")

    assert result == mock_data
    mock_urlopen.assert_called_once()

    # Check that URL is correct
    args, kwargs = mock_urlopen.call_args
    req = args[0]
    assert req.full_url == "https://api.github.com/repos/owner/repo/releases?per_page=100"
    assert req.headers.get("Accept") == "application/vnd.github+json"
    assert req.headers.get("User-agent") == "PIVOT-UpdateCheck"
    assert req.headers.get("X-github-api-version") == "2022-11-28"
    assert "Authorization" not in req.headers


def test_fetch_releases_auth():
    mock_response = mock.MagicMock()
    mock_response.read.return_value = json.dumps([]).encode("utf-8")

    mock_urlopen = mock.MagicMock(
        return_value=mock.MagicMock(__enter__=mock.MagicMock(return_value=mock_response))
    )

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        fetch_releases("owner/repo", token="my-secret-token")

    mock_urlopen.assert_called_once()

    args, kwargs = mock_urlopen.call_args
    req = args[0]
    assert req.headers.get("Authorization") == "Bearer my-secret-token"


def test_fetch_releases_returns_empty_list_for_dict():
    # Sometimes an API error returns a dict like {"message": "Not Found"}
    mock_data = {"message": "Not Found"}

    mock_response = mock.MagicMock()
    mock_response.read.return_value = json.dumps(mock_data).encode("utf-8")

    mock_urlopen = mock.MagicMock(
        return_value=mock.MagicMock(__enter__=mock.MagicMock(return_value=mock_response))
    )

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        result = fetch_releases("owner/repo")

    assert result == []


def test_fetch_releases_bubbles_errors():
    mock_urlopen = mock.MagicMock(side_effect=urllib.error.URLError("connection refused"))

    with mock.patch("urllib.request.urlopen", mock_urlopen):
        with pytest.raises(urllib.error.URLError, match="connection refused"):
            fetch_releases("owner/repo")


def test_ttl_cache_returns_cached_value():
    mock_func = mock.MagicMock(return_value=[{"tag_name": "v1.0.0"}])
    cached_func = ttl_cache(ttl_seconds=300)(mock_func)

    # First call: misses cache, calls original func
    res1 = cached_func("arg1")
    assert res1 == [{"tag_name": "v1.0.0"}]
    assert mock_func.call_count == 1

    # Second call (same args): hits cache, mock_func shouldn't be called
    res2 = cached_func("arg1")
    assert res2 == [{"tag_name": "v1.0.0"}]
    assert mock_func.call_count == 1  # Still 1

    # Third call (different args): misses cache
    res3 = cached_func("arg2")
    assert res3 == [{"tag_name": "v1.0.0"}]
    assert mock_func.call_count == 2


def test_ttl_cache_expires():
    mock_func = mock.MagicMock(return_value=[{"tag_name": "v1.0.0"}])
    cached_func = ttl_cache(ttl_seconds=300)(mock_func)

    with mock.patch("time.time") as mock_time:
        # Initial call at t=100
        mock_time.return_value = 100.0
        cached_func("arg1")
        assert mock_func.call_count == 1

        # Second call at t=200 (within TTL): hits cache
        mock_time.return_value = 200.0
        cached_func("arg1")
        assert mock_func.call_count == 1

        # Third call at t=401 (outside TTL): misses cache
        mock_time.return_value = 401.0
        cached_func("arg1")
        assert mock_func.call_count == 2


def test_ttl_cache_clear():
    mock_func = mock.MagicMock(return_value=[{"tag_name": "v1.0.0"}])
    cached_func = ttl_cache(ttl_seconds=300)(mock_func)

    # First call populates cache
    cached_func("arg1")
    assert mock_func.call_count == 1

    # Second call uses cache
    cached_func("arg1")
    assert mock_func.call_count == 1

    # Clear cache explicitly
    cached_func.cache_clear()

    # Third call should hit the function again
    cached_func("arg1")
    assert mock_func.call_count == 2


def test_ttl_cache_eviction():
    mock_func = mock.MagicMock(return_value=[{"tag_name": "v1.0.0"}])
    # Very small maxsize for testing
    cached_func = ttl_cache(maxsize=2, ttl_seconds=300)(mock_func)

    with mock.patch("time.time") as mock_time:
        # Fill the cache with 2 items
        mock_time.return_value = 100.0
        cached_func("arg1")

        mock_time.return_value = 110.0
        cached_func("arg2")

        assert mock_func.call_count == 2

        # Verify both are cached
        cached_func("arg1")
        cached_func("arg2")
        assert mock_func.call_count == 2

        # Add a 3rd item, exceeding maxsize=2
        # This should evict the oldest item ("arg1" which was added at t=100.0)
        mock_time.return_value = 120.0
        cached_func("arg3")
        assert mock_func.call_count == 3

        # "arg2" and "arg3" should still be cached
        cached_func("arg2")
        cached_func("arg3")
        assert mock_func.call_count == 3

        # "arg1" should have been evicted, so calling it again invokes the func
        cached_func("arg1")
        assert mock_func.call_count == 4
