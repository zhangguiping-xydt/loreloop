from api import upload


def test_oversized_upload_is_rejected(fakes):
    file, user, limiter, store = fakes(
        file_name="report.pdf", file_size=50 * 1024 * 1024 + 1
    )
    status, body = upload(file, user, limiter, store)
    assert status == 413
    assert body["error"] == "file_too_large"
    assert store.saved == []


def test_valid_upload_returns_identifier(fakes):
    file, user, limiter, store = fakes(file_name="report.pdf", file_size=1024)
    store.next_id = "file-123"
    status, body = upload(file, user, limiter, store)
    assert status == 201
    assert body == {"id": "file-123"}
    assert store.saved[0].owner_id == user.id
