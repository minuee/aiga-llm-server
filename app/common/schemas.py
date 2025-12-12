from pydantic import BaseModel
from typing import Optional

class ChatRequest(BaseModel):
    message: str
    session_id: str
    locale: Optional[str] = None

class StopRequest(BaseModel):
    session_id: str

class ChatResponse(BaseModel):
    reply: str
