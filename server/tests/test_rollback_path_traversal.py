import pytest
from pydantic import ValidationError
from pivot.api.schemas import RollbackRequest

def test_rollback_request_path_traversal():
    with pytest.raises(ValidationError):
        RollbackRequest(tag="../../../etc/passwd")
    with pytest.raises(ValidationError):
        RollbackRequest(tag="/etc/passwd")
    with pytest.raises(ValidationError):
        RollbackRequest(tag="..\\..\\..\\Windows\\System32")
    with pytest.raises(ValidationError):
        RollbackRequest(tag="C:\\Windows\\System32")

def test_rollback_request_valid():
    req = RollbackRequest(tag="v1.0.0")
    assert req.tag == "v1.0.0"

    req = RollbackRequest(tag=None)
    assert req.tag is None
