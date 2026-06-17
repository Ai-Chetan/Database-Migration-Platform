from pydantic import BaseModel
from typing import Dict, Any

class HealthCheckResponse(BaseModel):
    status: str
    version: str
    services: Dict[str, Any]
