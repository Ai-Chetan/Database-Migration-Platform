from typing import Optional, Dict, Any

class PlatformException(Exception):
    def __init__(self, code: str, message: str, http_status: int = 500, details: Optional[Dict[str, Any]] = None):
        self.error_code = code
        self.message = message
        self.http_status = http_status
        self.details = details or {}
        super().__init__(self.message)
