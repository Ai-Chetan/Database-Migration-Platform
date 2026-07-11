from .base import PlatformException

class AuthenticationException(PlatformException):
    def __init__(self, code: str, message: str, details: dict = None):
        super().__init__(code=code, message=message, http_status=401, details=details)
