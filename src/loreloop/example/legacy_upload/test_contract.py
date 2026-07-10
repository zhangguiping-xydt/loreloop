from app import MAX_UPLOAD_MIB, upload_allowed


def test_upload_ceiling_is_inclusive():
    assert MAX_UPLOAD_MIB == 5
    assert upload_allowed(5)
    assert not upload_allowed(6)
