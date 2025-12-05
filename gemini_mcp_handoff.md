
# MCP 기능 구현 핸드오프 문서

이 문서는 Gemini 에이전트가 `admin-aiga-wep` 프로젝트에서 `llm-server` 프로젝트로 작업을 이전하기 위한 컨텍스트 핸드오프 파일입니다.

## 1. 최종 목표

`admin-aiga-wep` 관리자 사이트에 새로운 페이지를 만들어, 사용자가 자연어(비정형 텍스트)로 질문하면 MCP(Model Context Protocol)를 통해 LLM 서버와 통신하여 데이터베이스의 정보를 조회하고 그 결과를 UI에 표시하는 기능을 구현합니다.

## 2. `admin-aiga-wep` (프론트엔드) 프로젝트 작업 완료 내역

다음 파일들을 생성하고 수정했습니다.

### A. MCP 조회 페이지 UI 생성
- **파일 경로**: `src/app/v1/mcp-query/page.tsx`
- **내용**: 사용자가 질문을 입력할 수 있는 `Textarea`와 '조회하기' `Button`, 결과를 표시할 영역을 Chakra UI를 사용하여 구현했습니다.
- **동작**: '조회하기' 버튼을 클릭하면 `/api/mcp-query` 로 `POST` 요청을 보냅니다. (Request Body: `{ "query": "사용자 질문" }`)

### B. 백엔드 통신을 위한 API 라우트 (프록시) 생성
- **파일 경로**: `src/app/api/mcp-query/route.ts`
- **내용**: UI로부터 요청을 받아 실제 LLM 서버로 전달하는 중간 다리 역할을 할 API를 생성했습니다.
- **현재 상태**: 실제 LLM 서버가 없으므로, 현재는 2초 지연 후 임시 모의(mock) 응답을 반환하도록 구현되어 있습니다.

### C. 사이드바 메뉴 추가
- **파일 경로**: `src/routes.tsx`
- **내용**: 관리자 사이트의 사이드바에 'MCP 데이터 조회' 메뉴를 추가하여 `v1/mcp-query` 페이지로 이동할 수 있도록 링크를 추가했습니다.

## 3. `llm-server` (백엔드) 프로젝트에서 해야 할 일 (To-Do)

**핵심 정보**: 사용자는 **Python 기반의 LangChain을 사용하는 LLM 서버**를 이미 가지고 있습니다.

1.  **API 엔드포인트 확인/생성**: Python LLM 서버에 `admin-aiga-wep`의 API 라우트가 호출할 수 있는 HTTP 엔드포인트(예: `/mcp-query`)가 있는지 확인하거나 생성해야 합니다.
    - 이 엔드포인트는 JSON 형식의 요청 (`{ "query": "..." }`)을 받을 수 있어야 합니다.

2.  **Next.js API 라우트 수정**: `admin-aiga-wep` 프로젝트의 `src/app/api/mcp-query/route.ts` 파일에 있는 **TODO** 부분을 수정해야 합니다.
    - 현재의 모의 응답 로직을 삭제하고, 1번에서 확인/생성한 Python LLM 서버의 실제 엔드포인트 URL을 `fetch` 하도록 코드를 변경해야 합니다.

## 4. 전체 아키텍처

```
[Admin UI (Next.js)] ---> [Next.js API Route (/api/mcp-query)] ---> [Python LLM Server (LangChain)] ---> [Database]
  (사용자 질문 입력)      (요청 중개/프록시)                      (자연어 처리 및 DB 조회)         (병원/의사 정보)
```
