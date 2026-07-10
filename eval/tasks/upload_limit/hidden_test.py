from upload_policy import MAX_UPLOAD_BYTES, accepts_upload

expected = 37 * 1024 * 1024
assert MAX_UPLOAD_BYTES == expected, (MAX_UPLOAD_BYTES, expected)
assert accepts_upload(expected)
assert not accepts_upload(expected + 1)
print("upload limit contract passed")
