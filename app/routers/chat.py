from fastapi import APIRouter, Depends
from pydantic import BaseModel
from ..services.service import startQuery, stopQuery
from ..database.db import get_db
from fastapi.responses import JSONResponse
from ..services.service import findDoctor
from ..common.logger import logger

router = APIRouter(prefix="/chat", tags=["chat"])

class ChatRequest(BaseModel):
    message: str
    session_id: str

class StopRequest(BaseModel):
    session_id: str

class ChatResponse(BaseModel):
    reply: str

@router.post("/start", response_model=ChatResponse)
async def startChat(req: ChatRequest, db=Depends(get_db)):
    logger.info(f" NOHLOGGER : startChat start")
    # 필요시 DB 기록 로직 추가   
    reply = await startQuery(req.message, req.session_id)
    if isinstance(reply, dict):
        return JSONResponse(content=reply)
    return ChatResponse(reply=reply)

@router.post("/stop", response_model=ChatResponse)
async def stopChat(req: StopRequest, db=Depends(get_db)):
    # 필요시 DB 기록 로직 추가   
    reply = await stopQuery(req.session_id)
    if isinstance(reply, dict):
        return JSONResponse(content=reply)
    return ChatResponse(reply=reply)

@router.post("/doctor", response_model=ChatResponse)
async def detailDoctor(req: ChatRequest, db=Depends(get_db)):
    # 필요시 DB 기록 로직 추가   
    reply = findDoctor(req.message, req.session_id)
    if isinstance(reply, dict):
        return JSONResponse(content=reply)
    return ChatResponse(reply=reply)    