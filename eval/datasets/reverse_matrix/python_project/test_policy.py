from policy import audit_expired, premium_request_allowed, upload_allowed


def test_boundaries():
    assert upload_allowed(32)
    assert not upload_allowed(33)
    assert premium_request_allowed(11)
    assert not premium_request_allowed(12)
    assert not audit_expired(90)
    assert audit_expired(91)
