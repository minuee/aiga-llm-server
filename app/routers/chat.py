import re
from fastapi import APIRouter, Depends, Request
from ..services.service import startQuery, stopQuery
from ..database.db import get_db
from fastapi.responses import JSONResponse
from ..services.service import findDoctor
from ..common.logger import logger
from ..common.schemas import ChatRequest, StopRequest, ChatResponse
from ..common.sanitizer import sanitize_prompt


router = APIRouter(prefix="/chat", tags=["chat"])

@router.post("/start", response_model=ChatResponse)
async def startChat(req: ChatRequest, request: Request, db=Depends(get_db)):
    logger.info(f" NOHLOGGER : startChat start - session_id: {req.session_id}, message: {req.message}, locale: {req.locale}, latitude: {req.latitude}, longitude: {req.longitude}")
    
    # Generate a sanitized message for filter avoidance, but keep the original message for context.
    # req.sanitized_message = sanitize_prompt(req.message)
    
    reply = await startQuery(req, request)
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