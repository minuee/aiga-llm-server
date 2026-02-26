# 위치 기반 검색 처리 흐름 (Location-Based Search Flow)

이 문서는 사용자의 질문에서 위치 정보를 이해하고, 이를 바탕으로 적절한 도구를 사용하여 검색을 수행하는 전체 과정을 설명합니다. 이 문서는 향후 유지보수 및 기능 확장을 위한 핵심 가이드 역할을 합니다.

## 핵심 처리 원칙

1.  **상태 중심 설계**: 모든 위치 관련 정보는 `AgentState`의 `location_history`에 명시적으로 기록되고, 하위 모듈들은 이 상태를 신뢰하고 참조합니다.
2.  **단일 책임 원칙**: 각 모듈은 명확한 단일 책임을 가집니다.
    *   `location_analyzer.py`: 사용자 메시지를 자연어 처리(NLP)하여 의도를 분석하고 구조화합니다.
    *   `agent.py (agent_node)`: 상태(`location_history`)를 업데이트하고 관리합니다.
    *   `agent.py (custom_tool_node)`: 업데이트된 상태를 읽어, 적절한 도구(Tool)로 라우팅합니다.
    *   `sql_tool.py`: 실제 DB 쿼리를 수행합니다.

---

## 핵심 구성요소

### 1. `AgentState['location_history']`

대화의 위치 컨텍스트를 저장하는 **단일 진실 공급원(Single Source of Truth)** 입니다. 리스트(List) 형태이며, 각 요소는 아래 두 가지 타입 중 하나의 딕셔너리입니다.

*   **GPS 기반 컨텍스트 (내 위치)**
    ```json
    {
      "type": "GPS",
      "latitude": 37.5665,
      "longitude": 126.9780
    }
    ```
*   **지역명 기반 컨텍스트**
    ```json
    {
      "type": "CONTEXTUAL",
      "sigungu": "강남구",
      "sido": "서울",
      "status": "resolved", 
      "is_nearby": false
    }
    ```
    *   `sigungu`, `sido`: 분석된 지역명
    *   `status`: 지역 명확화가 필요한 경우 `pending_clarification`, 해결된 경우 `resolved`.
    *   `is_nearby`: **가장 중요한 플래그.** `True`이면 '근처' 검색, `False`이면 '지역 내' 검색을 의미합니다.

### 2. 모듈별 역할

#### @app/common/location_analyzer.py (자연어 이해)

*   `classify_location_query`: 사용자의 가장 최근 메시지를 형태소 분석하여 **위치 검색 의도**를 파악합니다.
    *   **로직**: 단순히 고유명사(NNP)가 있다는 이유만으로 지역 검색으로 판단하지 않습니다. '병원' 같은 일반 명사(NNG)나 '찾아줘' 같은 동사(VV)가 함께 있을 때만 **검색 쿼리**로 인식합니다. (e.g., "경상남도야" 같은 단순 답변은 무시).
    *   **반환값**: `(분류, 기준 명사, is_nearby)` 3개 값을 반환합니다.
        *   `분류`: `USER_LOCATION`, `NAMED_LOCATION`, `NONE`
        *   `is_nearby`: "근처", "주변" 등의 단어가 있으면 `True`, 없으면 `False`.
*   `update_location_context`: `classify_location_query`의 결과를 바탕으로 `location_history`를 어떻게 업데이트할지 결정합니다. 불명확한 지역명(e.g., '광주')에 대해서는 사용자에게 되묻는 질문을 생성합니다.

#### @app/agent.py (오케스트레이션)

*   `agent_node`: 매 턴(turn)의 시작점입니다.
    1.  `update_location_context`를 호출하여 `location_history`를 최신 상태로 업데이트합니다.
    2.  업데이트된 `location_history`를 `return` 값에 명시적으로 포함하여, 다음 노드로 **상태를 확실하게 전달**합니다. (핵심 수정사항)
*   `custom_tool_node`: **도구 라우팅**을 담당합니다.
    1.  **자체적으로 NLP 분석을 다시 하지 않습니다.**
    2.  `agent_node`로부터 전달받은 `state`의 `location_history` 마지막 값을 읽어 `is_proximity_query`, `classification` 등의 변수를 설정합니다. (핵심 수정사항)
    3.  이 변수들을 기반으로 어떤 도구를 어떤 파라미터로 호출할지 결정합니다.

#### @app/tools/sql_tool.py (도구 실행)

*   **`is_location_near` 파라미터**: 대부분의 위치 검색 도구들이 이 파라미터를 받습니다. `custom_tool_node`에서 설정된 `is_proximity_query` 값이 여기에 전달됩니다.
*   `@handle_proximity_search` 데코레이터: `is_location_near=True`일 때 동작합니다.
    1.  `_get_coords_for_location`을 호출하여 지역명의 좌표를 얻습니다.
    2.  이 좌표를 기반으로 **거리 기반(반경) 검색**을 수행하는 SQL을 생성합니다.
*   `_build_location_where_clause` 함수: `is_location_near=False`일 때 동작합니다.
    1.  주소 컬럼을 대상으로 `MATCH...AGAINST` Full-text 검색을 수행하는 SQL WHERE 절을 생성합니다.

---

## 쿼리 유형별 전체 처리 흐름

### 1. "내 근처" (GPS 기반 검색)

> **사용자**: "내 근처에 있는 내과 찾아줘"

1.  **`agent_node`**:
    *   `classify_location_query`가 메시지를 분석하여 `("USER_LOCATION", None, True)`를 반환합니다.
    *   `update_location_context`는 `location_history`에 `{"type": "GPS", "latitude": ..., "longitude": ...}`를 추가합니다.
    *   LLM이 도구(`search_doctor_for_else_question`)를 호출합니다.
2.  **`custom_tool_node`**:
    *   `location_history`의 마지막 값이 `{"type": "GPS"}`인 것을 보고, `is_proximity_query`를 `True`로, `classification`을 `USER_LOCATION`으로 설정합니다.
    *   라우팅 규칙 `Case 1`에 따라 `search_doctors_by_location_and_department` 도구를 `latitude`, `longitude` 파라미터와 함께 호출합니다.
3.  **`sql_tool.py`**:
    *   `@handle_proximity_search` 데코레이터가 위도/경도를 받아 거리 기반 검색 SQL을 생성합니다.

### 2. "지역명 안에서" (포함 검색)

> **사용자**: "강남구에 있는 피부과 알려줘"

1.  **`agent_node`**:
    *   `classify_location_query`가 '강남구'(NNP)와 '피부과'(NNG)를 보고 `("NAMED_LOCATION", "강남구", False)`를 반환합니다. (`is_nearby`가 `False`)
    *   `update_location_context`는 `location_history`에 `{"type": "CONTEXTUAL", "sigungu": "강남구", "is_nearby": False, ...}`를 추가합니다.
    *   LLM이 도구를 호출합니다.
2.  **`custom_tool_node`**:
    *   `location_history`에서 `is_nearby: False` 값을 읽어 `is_proximity_query`를 `False`로 설정합니다.
    *   LLM이 추출한 `loc='강남구'`와 `is_proximity_query=False`를 사용하여 `search_doctors_by_location_and_department`를 호출합니다.
3.  **`sql_tool.py`**:
    *   `is_location_near`가 `False`이므로 `_build_location_where_clause`가 호출됩니다.
    *   `MATCH(h.address) AGAINST('+강남구' IN BOOLEAN MODE)` 와 같은 주소 텍스트 검색 SQL이 생성됩니다.

### 3. "지역명 근처" (근접 검색)

> **사용자**: "강남역 근처 피부과 알려줘"

1.  **`agent_node`**:
    *   `classify_location_query`가 '강남역'(NNP)과 '근처'(Proximity word)를 보고 `("NAMED_LOCATION", "강남역", True)`를 반환합니다. (`is_nearby`가 `True`)
    *   `update_location_context`는 `location_history`에 `{"type": "CONTEXTUAL", "sigungu": "강남역", "is_nearby": True, ...}`를 추가합니다.
    *   LLM이 도구를 호출합니다.
2.  **`custom_tool_node`**:
    *   `location_history`에서 `is_nearby: True` 값을 읽어 `is_proximity_query`를 `True`로 설정합니다.
    *   LLM이 추출한 `loc='강남역'`과 `is_proximity_query=True`를 사용하여 `search_doctors_by_location_and_department`를 호출합니다.
3.  **`sql_tool.py`**:
    *   `is_location_near`가 `True`이므로 `@handle_proximity_search` 데코레이터가 동작합니다.
    *   `_get_coords_for_location('강남역')`을 호출하여 강남역의 좌표를 가져옵니다.
    *   가져온 좌표를 중심으로 반경 내를 검색하는 거리 기반 SQL이 생성됩니다.
