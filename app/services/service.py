from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langchain.callbacks.base import BaseCallbackHandler
from langchain_core.messages import ToolMessage, AIMessage
import json
import asyncio
from typing import Dict
from ..database.searchDoctor import getDoctorById
from ..tools.tools import formattingDoctorInfo
from ..common.logger import logger
import re

# 상태 관리를 위한 클래스
class LangGraphExecutionManager:
    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
    
    async def start_task(self, session_id: str, coro):
        async with self._lock:
            # Dead lock 방지를 위해 주석처리
            # if session_id in self._tasks:
            #     await self.stop_task(session_id)
            
            task = asyncio.create_task(coro)
            self._tasks[session_id] = task
            return task
    
    async def stop_task(self, session_id: str):
        async with self._lock:
            if session_id in self._tasks:
                task = self._tasks[session_id]
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                del self._tasks[session_id]
                return True
            return False
    
    def get_task(self, session_id: str) -> asyncio.Task:
        return self._tasks.get(session_id)
    
# 콜백 핸들러
class CustomCallbackHandler(BaseCallbackHandler):
    def __init__(self):
        self.logs = []

    def on_chain_start(self, serialized, inputs, **kwargs):
        ## - Noh logger.info(f"Chain started with input: {inputs}")
        self.logs.append({"event": "start", "data": inputs})

    def on_chain_end(self, outputs, **kwargs):
        ## - Noh logger.info(f"Chain ended with output: {outputs}")
        self.logs.append({"event": "end", "data": outputs})

    def on_tool_start(self, serialized, input_str, **kwargs):
        logger.info(f"Tool call started with input: {input_str}")
        # self.logs.append({"event": "tool_start", "data": input_str})

    def on_tool_end(self, outputs, **kwargs):
        ## - Noh logger.info(f"Tool call ended with output: {outputs}")
        self.logs.append({"event": "tool_end", "data": outputs})

    def on_text(self, text, **kwargs):
        ## - Noh logger.info(f"Text received: {text}")
        self.logs.append({"event": "text", "data": text})

    def on_error(self, error, **kwargs):
        logger.error(f"Error in callback: {error}")
        self.logs.append({"event": "error", "data": str(error)})

def makeResponse(question: str, result):
    total_tokens = None
    input_tokens = None
    output_tokens = None
    # 뒤에서 첫 번째 AIMessage에서 total_tokens 추출
    #  logger.info(f"messages: {result}")
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage):
            # 1. response_metadata
            if hasattr(msg, "response_metadata") and msg.response_metadata:
                token_usage = msg.response_metadata.get("token_usage", {})
                if "total_tokens" in token_usage:
                    total_tokens = token_usage.get("total_tokens")
                    input_tokens = token_usage.get("prompt_tokens")
                    output_tokens = token_usage.get("completion_tokens")
                    logger.info(f"token_usage 11: {token_usage}")
                    break
            # 2. usage_metadata
            if hasattr(msg, "usage_metadata") and msg.usage_metadata:
                if "total_tokens" in msg.usage_metadata:
                    total_tokens = msg.usage_metadata.get("total_tokens")
                    input_tokens = msg.usage_metadata.get("prompt_tokens")
                    output_tokens = msg.usage_metadata.get("completion_tokens")
                    logger.info(f" msg.usage_metadata 22: {msg.usage_metadata}")
                    break
            # 3. token_usage (혹시 있을 경우)
            if hasattr(msg, "token_usage") and msg.token_usage:
                if "total_tokens" in msg.token_usage:
                    total_tokens = msg.token_usage.get("total_tokens")
                    input_tokens = msg.token_usage.get("prompt_tokens")
                    output_tokens = msg.token_usage.get("completion_tokens")
                    logger.info(f"  msg.token_usage 33: {msg.token_usage}")
                    break
    
    logger.info(f"Token Usage - Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}")
    
    # 캐시 사용 등으로 토큰 정보가 없을 경우 0으로 설정
    if total_tokens is None:
        total_tokens = 0
    if input_tokens is None:
        input_tokens = 0
    if output_tokens is None:
        output_tokens = 0

    # --- START: 요약 토큰 정보 추출 로직 추가 ---
    summary_input_tokens = result.get("summary_input_tokens", 0)
    summary_output_tokens = result.get("summary_output_tokens", 0)
    summary_total_tokens = result.get("summary_total_tokens", 0)
    logger.info(f"Summary Token Usage - Input: {summary_input_tokens}, Output: {summary_output_tokens}, Total: {summary_total_tokens}")
    # --- END: 요약 토큰 정보 추출 로직 추가 ---
    
    last_message = result["messages"][-1]
    second_to_last_message = result["messages"][-2]
    # 마지막에서 두번째 메세지가 ToolMessage이고 마지막 메세지가 AIMessage인 경우
    if isinstance(second_to_last_message, ToolMessage) and isinstance(last_message, AIMessage):
        # tool의 결과가 dict로 바로 반환되는 경우
        try:
            # content가 비어있거나 None인지 확인
            content = second_to_last_message.content
            if not content:
                raise ValueError("Tool message content is empty")
            
            # content가 문자열인지 확인하고 JSON 파싱
            if isinstance(content, str):
                if content.strip() == "":
                    raise ValueError("Tool message content is empty string")
                json_response = json.loads(content)
            elif isinstance(content, dict):
                # content가 이미 dict인 경우
                json_response = content
            else:
                # 기타 타입의 경우 문자열로 변환 후 JSON 파싱 시도
                content_str = str(content)
                json_response = json.loads(content_str)
        except (json.JSONDecodeError, ValueError) as e:
            logger.error(f"Failed to parse tool message content as JSON: {second_to_last_message.content}")
            # JSON 파싱 실패 시 기본 응답 반환
            json_response = {
                "chat_type": "general",
                "answer": str(second_to_last_message.content)
            }
        
        # question 맨 앞에 추가
        json_response = {"question": question, **json_response}

        # 마지막 메세지의 내용을 summary에 추가
        json_response["summary"] = last_message.content

        # total_tokens 추가 output_tokens,input_tokens 2025.08.14 add by Noh.S.N
        json_response["input_tokens"] = input_tokens
        json_response["output_tokens"] = output_tokens
        json_response["total_tokens"] = total_tokens
        # --- 요약 토큰 추가 ---
        json_response["summary_input_tokens"] = summary_input_tokens
        json_response["summary_output_tokens"] = summary_output_tokens
        json_response["summary_total_tokens"] = summary_total_tokens
        return json_response
    elif not isinstance(second_to_last_message, ToolMessage) and isinstance(last_message, AIMessage):
        response = {
            "chat_type": "general",
            "question": question,
            "answer": last_message.content,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            # --- 요약 토큰 추가 ---
            "summary_input_tokens": summary_input_tokens,
            "summary_output_tokens": summary_output_tokens,
            "summary_total_tokens": summary_total_tokens
        }
        return response
    else:
        # 예상치 못한 타입일 때 명확하게 에러 반환
        raise HTTPException(status_code=500, detail=f"Unexpected message type: {type(last_message)}")    

# 실행 관리자 인스턴스 생성
execution_manager = LangGraphExecutionManager()

async def startQuery(prompt: str, session_id: str, request: Request) -> dict:
    try:
        logger.info(f"Starting query for session {session_id}: {prompt[:100]}...")
        
        config = {"configurable": {"thread_id": session_id }}
        cb = CustomCallbackHandler()

        # Get graph from app.state
        current_graph = request.app.state.graph # Access graph from app.state

        task = await execution_manager.start_task(
            session_id,
            current_graph.ainvoke({
                    "messages": [HumanMessage(content=prompt)],
                    "cancelled": False
                },
                config=RunnableConfig(callbacks=[cb], configurable=config["configurable"])
            )
        )

        result = await task
        response = makeResponse(prompt, result)
        logger.info(f"Query completed for session {session_id}")
        return response
        
    except asyncio.CancelledError:
        logger.warning(f"Query cancelled for session {session_id}")
        return {
            "chat_type": 4,
                "question": prompt,
                "answer": "요청이 중지되었습니다.",
                "total_tokens": 0
            }
    except Exception as e:
        logger.error(f"Error in startQuery for session {session_id}, prompt: {prompt[:100]}... Error: {str(e)}", exc_info=True)
        
        # Azure OpenAI content management policy 필터링 에러 감지
        error_message = str(e)
        if "The response was filtered due to the prompt triggering Azure OpenAI's content management policy" in error_message or \
            "Azure has not provided the response due to a content filter being triggered" in error_message:
            status_code = 506
        else:
            status_code = 500
            
        raise HTTPException(status_code=status_code, detail=error_message)
    
async def stopQuery(session_id: str):
    try:
        logger.info(f"Stopping query for session {session_id}")
        stopped = await execution_manager.stop_task(session_id)
        if stopped:
            logger.info(f"Successfully stopped execution for session {session_id}")
            return {
                "status": "success",
                "message": f"Execution stopped for session_id({session_id})"
            }
        logger.warning(f"No active execution found for session {session_id}")
        return {
             "status": "not_found",
            "message": f"No active execution found for session_id({session_id})"
        }
    except Exception as e:
        logger.error(f"Error in stopQuery for session {session_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))    


def findDoctor(message: str, session_id: str):
    try:
        logger.info(f"Finding doctor for session {session_id}: {message}")
        number = re.search(r'\d+', message)  # doctor_id: 1
        if number:
            doctor_id = int(number.group())
        else: 
            logger.error(f"doctor_id not found in message: {message}")
            raise HTTPException(status_code=500, detail=str("doctor_id를 찾을 수 없습니다."))
    
        doctor = getDoctorById(doctor_id)
        formattedDoctors = formattingDoctorInfo(doctor, True)
        logger.info(f"Doctor found for session {session_id}: doctor_id={doctor_id}")
        return {
            "doctors": formattedDoctors
        }
    except Exception as e:
        logger.error(f"Error in findDoctor for session {session_id}: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))           