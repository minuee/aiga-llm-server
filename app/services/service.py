import os
from fastapi import HTTPException, Request
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langchain_core.runnables.config import RunnableConfig
from langchain_core.messages import ToolMessage, AIMessage
import json
import asyncio
from typing import Dict
from ..database.searchDoctor import getDoctorById
from ..tools.tools import formattingDoctorInfo
from ..common.logger import logger
from ..common.callbacks import TokenCountingCallback
from ..config import settings
import re

# 상태 관리를 위한 클래스
class LangGraphExecutionManager:
    def __init__(self):
        self._tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()
    
    async def start_task(self, session_id: str, coro):
        async with self._lock:
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

def makeResponse(question: str, result, token_counter: TokenCountingCallback):
    total_tokens = None
    input_tokens = None
    output_tokens = None
    cached_tokens = 0 # cached_tokens 초기화

    # 뒤에서 첫 번째 AIMessage에서 total_tokens 및 cached_tokens 추출
    for msg in reversed(result["messages"]):
        if isinstance(msg, AIMessage):
            token_usage_info = {}
            if hasattr(msg, "response_metadata") and msg.response_metadata:
                token_usage_info = msg.response_metadata.get("token_usage", {})
                if "prompt_tokens_details" in token_usage_info:
                    cached_tokens = token_usage_info["prompt_tokens_details"].get("cached_tokens", 0)
                elif "input_token_details" in token_usage_info: # usage_metadata 경우
                    cached_tokens = token_usage_info["input_token_details"].get("cache_read", 0)

            elif hasattr(msg, "usage_metadata") and msg.usage_metadata:
                token_usage_info = msg.usage_metadata
                if "input_token_details" in token_usage_info:
                    cached_tokens = token_usage_info["input_token_details"].get("cache_read", 0)
            
            if "total_tokens" in token_usage_info:
                total_tokens = token_usage_info.get("total_tokens")
                input_tokens = token_usage_info.get("prompt_tokens", token_usage_info.get("input_tokens"))
                output_tokens = token_usage_info.get("completion_tokens", token_usage_info.get("output_tokens"))
                break
    logger.info(f"Token Usage - Input: {input_tokens}, Output: {output_tokens}, Total: {total_tokens}, Cached: {cached_tokens}")
    # 캐시 사용 등으로 토큰 정보가 없을 경우 0으로 설정
    if total_tokens is None: total_tokens = 0
    if input_tokens is None: input_tokens = 0
    if output_tokens is None: output_tokens = 0

    last_message = result["messages"][-1]

    if not isinstance(last_message, AIMessage):
        raise HTTPException(status_code=500, detail=f"Unexpected final message type: {type(last_message)}")

    tool_contents = []
    
    for msg in reversed(result["messages"][:-1]):
        if isinstance(msg, ToolMessage):
            try:
                if not msg.content or not msg.content.strip():
                    raise ValueError("Tool message content is empty.")
                content_dict = json.loads(msg.content)
                tool_contents.append(content_dict)
            except (json.JSONDecodeError, ValueError) as e:
                logger.warning(f"Could not parse ToolMessage content: {msg.content}. Error: {e}")
        else:
            break
    
    tool_contents.reverse()
    logger.info(f"Grand Total Token Usage2 - Input: {token_counter.total_prompt_tokens}, Output: {token_counter.total_completion_tokens}, Total: {token_counter.total_tokens}, Cache Tokne : {cached_tokens}")
    
    # --- Build Response ---
    json_response = {
        "question": question,
        "summary": last_message.content,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
        "grand_cache_total_token": cached_tokens, # 새로 추가: 캐시된 토큰
        "grand_total_input_tokens": token_counter.total_prompt_tokens,
        "grand_total_output_tokens": token_counter.total_completion_tokens,
        "grand_total_tokens": token_counter.total_tokens,
        "llm_ai_model": settings.azure_api_model,
    }

    json_response["chat_type"] = "general" # 기본값 설정
    json_response["answer"] = last_message.content # 기본 답변은 LLM의 최종 메시지

    if tool_contents:
        final_doctors_list = []
        final_hospitals_list = []
        answer_template = None

        for content_dict in tool_contents:
            if content_dict.get("migrated") is True:
                continue

            if "chat_type" in content_dict and json_response["chat_type"] == "general":
                 json_response["chat_type"] = content_dict["chat_type"]

            answer = content_dict.get("answer")
            if isinstance(answer, dict):
                if answer_template is None:
                    answer_template = answer.copy()
                
                if "doctors" in answer and isinstance(answer.get("doctors"), list):
                    final_doctors_list.extend(answer["doctors"])
                if "hospitals" in answer and isinstance(answer.get("hospitals"), list):
                    final_hospitals_list.extend(answer.get("hospitals") or [])
        
        if answer_template is not None:
            if "doctors" in answer_template:
                answer_template['doctors'] = final_doctors_list
            if "hospitals" in answer_template:
                answer_template['hospitals'] = final_hospitals_list
            
            json_response["answer"] = answer_template
        # else: dict 형태의 answer가 없는 도구(general)는 기본 요약문을 answer로 사용

    # 마크다운 형식 감지 (환경 변수 설정 시)
    if settings.MESSAGE_MARKDOWN_USE_VERBOSE:
        if json_response["chat_type"] == "general" and isinstance(json_response["answer"], str):
            # 마크다운 패턴: 목록(*, -), 순서 있는 목록(1.), 헤더(#)
            # 이스케이프 문자를 포함하는 정규식 re.compile(r'(\n\s*(\*|\-)\s+|\n\s*\d+\.\s+|^\s*\#)')
            markdown_pattern = re.compile(r'(\n\s*(\*|\-)\s+|\n\s*\d+\.\s+|^\s*\#)')
            if markdown_pattern.search(json_response["answer"]):
                json_response["chat_type"] = "markdown"

    return json_response

from ..common.schemas import ChatRequest

# 실행 관리자 인스턴스 생성
execution_manager = LangGraphExecutionManager()

async def startQuery(req: ChatRequest, request: Request) -> dict:
    try:
        prompt = req.message
        session_id = req.session_id
        logger.info(f"Starting query for session {session_id}: {prompt[:100]}..., latitude: {req.latitude}, longitude: {req.longitude}")
        token_counter = TokenCountingCallback()

        config = {
            "callbacks": [token_counter],
            "configurable": {"thread_id": session_id}
        }

        if os.getenv("LANGSMITH_TRACING") == "true":
            project_name = os.getenv("LANGSMITH_PROJECT", "aiga-llm-server") # 기본값 설정
            config["metadata"] = {
                "tags": [session_id],
                "project_name": project_name
            }
            os.environ["LANGSMITH_TRACING"] = "true"
            os.environ["LANGSMITH_PROJECT"] = project_name
            if os.getenv("LANGSMITH_API_KEY"):
                os.environ["LANGSMITH_API_KEY"] = os.getenv("LANGSMITH_API_KEY")
            if os.getenv("LANGSMITH_ENDPOINT"):
                os.environ["LANGSMITH_ENDPOINT"] = os.getenv("LANGSMITH_ENDPOINT")
            logger.info(f"LangSmith tracing activated for session {session_id} in project {project_name}")


        current_graph = request.app.state.graph
        
        locale = req.locale or settings.default_locale
        latitude = req.latitude
        longitude = req.longitude

        task = await execution_manager.start_task(
            session_id,
            current_graph.ainvoke({
                    "messages": [HumanMessage(content=prompt)],
                    "locale": locale,
                    "latitude": latitude,
                    "longitude": longitude,
                },
                config=config
            )
        )

        result = await task
        response = makeResponse(prompt, result, token_counter)
        logger.info(f"Query completed for session {session_id}")
        return response
        
    except asyncio.CancelledError:
        logger.warning(f"Query cancelled for session {session_id}")
        return {
            "chat_type": "general_error",
            "question": prompt,
            "answer": "요청이 중지되었습니다.",
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
        number = re.search(r'\d+', message)
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