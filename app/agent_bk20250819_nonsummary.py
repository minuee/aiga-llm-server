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

# llm = AzureOpenAI(
#     azure_endpoint=settings.azure_endpoint,
#     api_key=settings.azure_key,
#     azure_deployment="gpt-35-turbo",
#     api_version="2024-02-01",
#     temperature=0.7,
# )

# 모델 설정
llm = AzureChatOpenAI(
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
            # AIMessage의 tool_calls 정보도 키에 포함
            if msg.tool_calls:
                tool_calls_str = ",".join(f"T:{tc['name']}({tc['args']})" for tc in msg.tool_calls)
                summary.append(f"A:{msg.content}[{tool_calls_str}]")
            else:
                summary.append(f"A:{msg.content}")
        elif isinstance(msg, ToolMessage):
            # ToolMessage의 내용과 tool_call_id를 키에 포함
            summary.append(f"ToolResult({msg.tool_call_id}):{msg.content}")
            
    return "\n".join(summary)

# 상태 정의
# class AgentState(TypedDict):
#     messages: list
#     current_step: str
class AgentState(MessagesState):
    messages: Annotated[list, add_messages]
    retry: Annotated[int, 0]  # ✅ 마지막 값으로 병합
    valid: Annotated[bool, False]   # ← 이것도 있으면 추가로 지정

# 1️⃣ 에이전트 노드
async def agent_node(state: AgentState):
    """모델을 통해 답변하는 Agent 노드"""

    # 메시지 상태 가져오기
    messages = state["messages"]

     # 시스템 메시지가 추가: 없는 경우(첫 번째 호출) 시스템 메시지 추가
    if not any(isinstance(msg, SystemMessage) for msg in messages):
        system_message = SystemMessage(content=SYSTEM_PROMPT)
        messages.insert(0, system_message)

    # 3-1. 캐시 key 생성
    cache_key = f"chat:{summarize_messages(messages)}"


    # 3-2. 캐시 조회
    cached_content = await redis_client.get(cache_key)
    logger.info(f"cached_content: {cached_content}")
    if cached_content:
        # 캐시 hit: AIMessage 객체로 감싸서 반환
        logger.info(f"cached_content if if if ")
        return {"messages": [AIMessage(content=cached_content)], "retry": state.get("retry", 0)}

    # 응답 지연시 중지(/stop) 테스트를 위한 코드 주석처리
    # for i in range(10):
    #     await asyncio.sleep(1)
    #     logger.debug(f"Slow node processing... {i+1}s")

    # 모델 호출
    response = await model.ainvoke(messages)

    # 3-4. 캐시에 저장 (예: TTL 1시간)
    await redis_client.set(cache_key, response.content, ex=3600)
    
    retry = state.get("retry", 0)
    return {"messages": [response], "retry": retry}


# 2️⃣ 분기 엣지
def should_continue(state: AgentState):
    """모델 답변에 따라 분기 처리하는 edge"""
    messages = state["messages"]
    last_message = messages[-1]

    if last_message.tool_calls:
        for tool in last_message.tool_calls:
            logger.info(f"Tool called: {tool['name']}")
        return "tools"
    
    logger.info("Validation called")
    return "validate"

    # if settings.validation_enable:
    #     print("should_continue, validate called")
    #     return "validate"
    # else:
    #     print("should_continue, END called")
    #     return END
    

# 3️⃣ 검증 노드
async def validate_node(state: AgentState) -> AgentState:
    """응답의 적절성 여부 판단"""        

    retry = state.get("retry", 0)

    # 검증(false)이면 리턴 또는 재시도가 limit 이상이면 리턴.
    if not settings.validation_enable or retry >= settings.validation_retry_limit:
        return {
            "messages": state["messages"],
            "retry": 0,
            "valid": True
        }

    answer = state["messages"][-1].content

    question = ""
    # 메시지에서 마지막 질문/답변 추출 (단순 구조 기준)
    for msg in reversed(state["messages"]):
        if msg.type == "human":
            question = msg.content
            break

    # 질문(question)과 답변(answer)에 대해 LLM에게 평가 요청
    prompt = VALIDATION_PROMPT.format(question=question, answer=answer)
    result = await llm.ainvoke(prompt)

    is_valid = result.content.strip().lower() == "yes"
       
    # 검증 성공
    if is_valid:
        return {
            "messages": state["messages"],
            "retry": 0,
            "valid": True
        }
       
    # 가장 최근의 HumanMessage부터 그 이후(더 과거)의 모든 메시지들만 남기고, 
    # 그보다 최신 메시지들은 버린다.
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

    # messages를 직접 업데이트
    state["messages"].clear()  # 기존 메시지 모두 제거
    state["messages"].extend(new_messages)  # 새 메시지로 업데이트
    
    return {
        "messages": state["messages"],  # 초기화된 메시지
        "retry": retry + 1,
        "valid": False
    }
    


# 4️⃣ 재시도 분기 처리 함수
def should_retry(state: AgentState) -> Literal["agent", END]:
    """검증이후에 분기하는 edge"""

    if state["valid"]:
        return END
    else:
        retry_count = state.get('retry', 0)
        logger.warning(f"Retry attempt {retry_count}")
        return "agent"
    

# New async function to compile the graph
async def get_compiled_graph():
    # 그래프 생성
    workflow = StateGraph(AgentState)

    # tool 노드
    tool_node = ToolNode(tools=tools)

    # 노드 및 엣지 정의
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", tool_node)
    workflow.add_node("validate", validate_node)

    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", should_continue)
    workflow.add_edge("tools", "agent")
    workflow.add_conditional_edges("validate", should_retry, {
        "agent": "agent",
        END: END
    })

    # 상태 관리자 정의
    # Use aiosqlite.connect for async connection
    conn = await aiosqlite.connect(settings.sqlite_directory, check_same_thread=False)
    memory = AsyncSqliteSaver(conn)

    # 그래프 컴파일
    graph = workflow.compile(checkpointer=memory)
    return graph
