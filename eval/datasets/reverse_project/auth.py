class Unauthorized(Exception):
    pass


class Forbidden(Exception):
    pass


def require_uploader(user) -> None:
    if not user.authenticated:
        raise Unauthorized("login required")
    if "uploader" not in user.roles:
        raise Forbidden("uploader role required")
