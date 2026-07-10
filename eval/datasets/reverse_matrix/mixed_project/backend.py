DOCUMENT_ROUTE = "/v1/documents"
MAX_DOCUMENT_MIB = 24
ACCEPTED_STATUS = 202


def create_document(size_mib: int) -> tuple[int, str]:
    if size_mib > MAX_DOCUMENT_MIB:
        return 413, "document too large"
    return ACCEPTED_STATUS, DOCUMENT_ROUTE
