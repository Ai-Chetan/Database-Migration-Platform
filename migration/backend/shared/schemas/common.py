from pydantic import BaseModel
from typing import Optional, Dict, Any

class BaseSchema(BaseModel):
    class Config:
        from_attributes = True

class StatusResponse(BaseModel):
    status: str
    message: Optional[str] = None
