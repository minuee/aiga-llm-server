import os
import asyncio
from langchain_openai import AzureOpenAI, AzureChatOpenAI
from langgraph.graph import StateGraph, END, START
from typing import TypedDict
from .config import settings
from langchain_core.tools import tool, BaseTool

from typing import Literal, Annotated, Dict
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.prebuilt import ToolNode
from langgraph.graph.message import MessagesState, add_messages
from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage

from .tools.tools import recommend_doctor, recommend_hospital, search_doctor, search_doctor_by_hospital,search_doctor_for_else_question
from .prompt.system_prompt import SYSTEM_PROMPT
from .prompt.validation_prompt import VALIDATION_PROMPT
from .common.logger import logger

import aiosqlite
import redis.asyncio as redis

## - Noh logger.info(f"Azure all: {settings}")
# Azure OpenAI 클라이언트 설정
## - Noh logger.info(f"Azure endpoint: {settings.azure_endpoint}")
## - Noh logger.info(f"Azure API version: {settings.azure_api_version}")
## - Noh logger.info(f"Azure API model: {settings.azure_api_model}")
## - Noh logger.info(f"Validation enable: {settings.validation_enable}")
## - Noh logger.info(f"Validation retry limit: {settings.validation_retry_limit}")
# logger.debug(f"Azure temperature: {settings.azure_temperature}")

# 모델 설정
llm = AzureChatOpenAI(
    model_name=settings.azure_api_model,
    azure_endpoint=settings.azure_endpoint,
    api_key=settings.azure_key,
    api_version=settings.azure_api_version,
    temperature=0
)

# --- 요약 전용 모델 ---
# 비용 효율성을 위해, 요약 작업에는 더 저렴하고 빠른 모델을 사용하는 것을 강력히 권장합니다.
# TODO: 아래 model_name을 gpt-3.5-turbo와 같은 비용 효율적인 모델로 변경하세요.
# (예: 별도의 설정값 `settings.azure_api_summary_model`을 만들어 사용하는 것을 추천합니다.)
# 2025-08-20 Gemini: AZURE_OPENAI_SUMMARY_MODEL에 설정된 배포를 찾지 못해 AZURE_OPENAI_MODEL을 사용하도록 임시 수정합니다.
llm_for_summary = AzureChatOpenAI(
    model_name=settings.azure_api_model,
    azure_endpoint=settings.azure_endpoint,
    api_key=settings.azure_key,
    api_version=settings.azure_api_version,
    temperature=0
)

# 도구 설정
tools = [recommend_doctor, recommend_hospital, search_doctor, search_doctor_by_hospital,search_doctor_for_else_question]
         
# 도구 바인딩
model = llm.bind_tools(tools)

# 1️⃣ Redis 클라이언트 생성
redis_client = redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

# 2️⃣ 요약 함수 (캐시 key 생성용)
def summarize_messages(messages):
    summary = []
    for msg in messages:
        if isinstance(msg, SystemMessage):
            summary.append(f"S:{msg.content}")
        elif isinstance(msg, HumanMessage):
            summary.append(f"H:{msg.content}")
        elif isinstance(msg, AIMessage):
            if msg.tool_calls:
                tool_calls_str = ",".join(f"T:{tc['name']}({tc['args']})" for tc in msg.tool_calls)
                summary.append(f"A:{msg.content}[{tool_calls_str}]")
            else:
                summary.append(f"A:{msg.content}")
        elif isinstance(msg, ToolMessage):
            summary.append(f"ToolResult({msg.tool_call_id}):{msg.content}")
            
    return "\n".join(summary)

# 상태 정의
class AgentState(MessagesState):
    messages: Annotated[list, add_messages]
    retry: Annotated[int, 0]
    valid: Annotated[bool, False]
    # 요약 토큰 사용량 저장을 위한 필드 추가
    summary_input_tokens: Annotated[int, 0]
    summary_output_tokens: Annotated[int, 0]
    summary_total_tokens: Annotated[int, 0]

# 1️⃣ 에이전트 노드
async def agent_node(state: AgentState):
    """모델을 통해 답변하는 Agent 노드. 대화가 길어지면 요약 기능이 동작합니다."""

    # --- START: 대화 요약 로직 ---
    messages = state["messages"]
    
    # 요약 토큰 정보를 담을 변수 초기화
    s_input_tokens, s_output_tokens, s_total_tokens = 0, 0, 0

    # 시스템 메시지가 없는 경우 추가하고, 원본 시스템 메시지 저장
    if not any(isinstance(msg, SystemMessage) for msg in messages):
        system_message = SystemMessage(content=SYSTEM_PROMPT)
        messages.insert(0, system_message)
        original_system_message = system_message
    else:
        original_system_message = messages[0]

    # 요약 설정값
    CHAR_THRESHOLD = 5000  # 이 '글자 수'를 넘으면 요약 실행 (테스트용으로 매우 낮게 설정)
    MESSAGES_TO_KEEP_IF_NO_TOOL = 4 # Tool 없을 때 남길 최근 메시지 수

    # 문자 수 기반으로 토큰 수 근사치 계산
    approx_tokens = sum(len(m.content) for m in messages if hasattr(m, 'content'))
    logger.info(f"DEBUG: Calculated approx_tokens (character count) = {approx_tokens}") # Debug log

    if approx_tokens > CHAR_THRESHOLD:
        logger.info(f"Checkpointer 'put': Character count ({approx_tokens}) exceeds threshold ({CHAR_THRESHOLD}). Summarizing intelligently...")
        
        # --- START: 요약 대상 스마트하게 선택하기 ---
        last_tool_message_index = -1
        for i, msg in reversed(list(enumerate(messages))):
            if isinstance(msg, ToolMessage):
                last_tool_message_index = i
                break

        messages_to_summarize = []
        messages_to_keep = []

        if last_tool_message_index != -1:
            # ToolMessage가 있는 경우: 마지막 ToolMessage와 그 직전의 AIMessage를 함께 보존
            # messages[last_tool_message_index - 1]이 AIMessage(tool_calls)이고,
            # messages[last_tool_message_index]가 ToolMessage인지 명시적으로 확인
            
            if last_tool_message_index > 0 and \
               isinstance(messages[last_tool_message_index - 1], AIMessage) and \
               messages[last_tool_message_index - 1].tool_calls:
                
                # 유효한 AIMessage(tool_calls) - ToolMessage 쌍이 있는 경우
                logger.info(f"Last ToolMessage found. Summarizing history before valid tool call pair.")
                messages_to_summarize = messages[1 : last_tool_message_index - 1] # 시스템 메시지 제외
                messages_to_keep = messages[last_tool_message_index - 1 :] # 이 쌍부터 끝까지 보존
            else:
                # ToolMessage는 있지만, 직전 메시지가 유효한 AIMessage(tool_calls)가 아닌 경우
                # (예: history rewind 등으로 인해 쌍이 깨진 경우)
                # API 에러 방지를 위해 ToolMessage부터는 무조건 보존
                logger.warning(f"ToolMessage found at index {last_tool_message_index} but not preceded by a valid tool-calling AIMessage. Summarizing history before ToolMessage.")
                messages_to_summarize = messages[1 : last_tool_message_index] # ToolMessage 이전까지 요약
                messages_to_keep = messages[last_tool_message_index:] # ToolMessage부터 끝까지 보존
        else:
            # ToolMessage가 없는 경우: 오래된 대화 요약
            logger.info("ToolMessage가 없어 메시지 수 기반으로 요약 대상을 선정합니다.")
            messages_to_summarize = messages[1:-MESSAGES_TO_KEEP_IF_NO_TOOL]
            messages_to_keep = messages[-MESSAGES_TO_KEEP_IF_NO_TOOL:]
        # --- END: 요약 대상 스마트하게 선택하기 ---

        if messages_to_summarize:
            conversation_text = "\n".join([f"{type(m).__name__}: {m.content}" for m in messages_to_summarize])
            summary_prompt_text = f"다음은 AI 챗봇과 사용자의 이전 대화 내용입니다. 이 대화의 핵심적인 맥락과 주요 정보를 유지하면서 간결하게 한국어로 요약해주세요. 이 요약은 나중에 AI가 대화를 자연스럽게 이어가는 데 사용됩니다.\n\n--- 대화 내용 ---\n{conversation_text}\n\n--- 요약 ---"
            
            summary_response = await llm_for_summary.ainvoke([HumanMessage(content=summary_prompt_text)])
            summary_text = summary_response.content
            
            if hasattr(summary_response, 'usage_metadata') and summary_response.usage_metadata:
                summary_tokens = summary_response.usage_metadata
                logger.info(
                    f"Summarization token usage: "
                    f"Input={summary_tokens.get('prompt_tokens', 'N/A')}, "
                    f"Output={summary_tokens.get('completion_tokens', 'N/A')}, "
                    f"Total={summary_tokens.get('total_tokens', 'N/A')}"
                )
                s_input_tokens = summary_tokens.get('prompt_tokens', 0)
                s_output_tokens = summary_tokens.get('completion_tokens', 0)
                s_total_tokens = summary_tokens.get('total_tokens', 0)
            
            logger.info(f"Generated summary for checkpointer: {summary_text[:200]}...")
            
            # 새로운 메시지 목록으로 재구성
            messages = [
                original_system_message,
                HumanMessage(content=f"이전 대화는 다음과 같이 요약되었습니다:\n{summary_text}"),
                *messages_to_keep
            ]
            state["messages"] = messages # 상태에 즉시 반영
        else:
            logger.info("요약 임계점을 넘었지만, 보존 정책에 따라 요약할 메시지가 없습니다.")

    # --- END: 대화 요약 로직 ---

    # 3-1. 캐시 key 생성 (요약되었을 수 있는 메시지 목록 사용)
    cache_key = f"chat:{summarize_messages(messages)}"

    # 3-2. 캐시 조회
    cached_content = await redis_client.get(cache_key)
    if cached_content:
        logger.info(f"Cache hit!")
        return {"messages": [AIMessage(content=cached_content)], "retry": state.get("retry", 0)}

    # 모델 호출
    logger.info("Cache miss. Calling model...")
    response = await model.ainvoke(messages)

    # 3-4. 캐시에 저장 (예: TTL 1시간)
    await redis_client.set(cache_key, response.content, ex=3600)
    
    retry = state.get("retry", 0)
    
    # 최종적으로 상태를 반환할 때, 요약 토큰 정보를 누적하여 함께 반환
    return {
        "messages": [response], 
        "retry": retry,
        "summary_input_tokens": state.get("summary_input_tokens", 0) + s_input_tokens,
        "summary_output_tokens": state.get("summary_output_tokens", 0) + s_output_tokens,
        "summary_total_tokens": state.get("summary_total_tokens", 0) + s_total_tokens,
    }



# 2️⃣ 분기 엣지
def should_continue(state: AgentState):
    """모델 답변에 따라 분기 처리하는 edge"""
    if state["messages"][-1].tool_calls:
        return "tools"
    return "validate"
    
# 3️⃣ 검증 노드
async def validate_node(state: AgentState) -> AgentState:
    """응답의 적절성 여부 판단"""
    retry = state.get("retry", 0)

    if not settings.validation_enable or retry >= settings.validation_retry_limit:
        return {"messages": state["messages"], "retry": 0, "valid": True}

    answer = state["messages"][-1].content
    question = ""
    for msg in reversed(state["messages"]):
        if msg.type == "human":
            question = msg.content
            break

    prompt = VALIDATION_PROMPT.format(question=question, answer=answer)
    result = await llm.ainvoke(prompt)
    is_valid = result.content.strip().lower() == "yes"
       
    if is_valid:
        return {"messages": state["messages"], "retry": 0, "valid": True}
       
    new_messages = []
    reverse_messages = []
    found = False
    for msg in reversed(state["messages"]):
        if found:
            reverse_messages.append(msg)
        if not found and isinstance(msg, HumanMessage):
            reverse_messages.append(msg)
            found = True

    new_messages = list(reversed(reverse_messages))
    
    state["messages"].clear()
    state["messages"].extend(new_messages)
    
    return {"messages": state["messages"], "retry": retry + 1, "valid": False}

# 4️⃣ 재시도 분기 처리 함수
def should_retry(state: AgentState) -> Literal["agent", END]:
    """검증이후에 분기하는 edge"""
    if state["valid"]:
        return END
    else:
        logger.warning(f"Retry attempt {state.get('retry', 0)}")
        return "agent"
    
# 그래프를 생성하고 컴파일하는 함수
async def get_compiled_graph():
    workflow = StateGraph(AgentState)

    tool_node = ToolNode(tools=tools)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("validate", validate_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    workflow.add_conditional_edges("validate", should_retry, {"agent": "agent", END: END})

    conn = await aiosqlite.connect(settings.sqlite_directory, check_same_thread=False)
    memory = AsyncSqliteSaver(conn=conn)

    graph = workflow.compile(checkpointer=memory)
    return graph