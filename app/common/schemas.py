from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    message: str
    sanitized_message: Optional[str] = None
    session_id: str
    locale: Optional[str] = None
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class StopRequest(BaseModel):
    session_id: str

class ChatResponse(BaseModel):
    reply: str
