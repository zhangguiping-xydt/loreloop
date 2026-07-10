from backend import create_document


def test_document_contract():
    assert create_document(24) == (202, "/v1/documents")
    assert create_document(25)[0] == 413
