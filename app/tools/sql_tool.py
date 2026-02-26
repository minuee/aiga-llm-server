from pydantic import BaseModel, Field
from ..database.recommandDoctors import getRecommandDoctorWithDiseaseAndDepartment, getRecommandDoctors
from ..database.standardSpecialty import getStandardSpecialty
import re
import os
import json
import asyncio
from typing import Optional, List, Union

from langchain_core.tools import tool
from langchain_community.utilities import SQLDatabase
from langchain_openai import AzureChatOpenAI
from pydantic import SecretStr
from sqlalchemy import text
from geopy.geocoders import Nominatim

from ..config import settings
from functools import wraps

from ..common.logger import logger
from .location_dic import GROUP_LOCATION_EXPANSION_RULES, LOCATION_NORMALIZATION_RULES # Import the rules for group locations

from ..database.searchDoctor import getSearchDoctorsByOnlyDepartment
from ..common.utils import _get_final_limit

def handle_proximity_search(func):
    """
    '근처' 검색 시 좌표를 처리하고, 실패 시 에러를 반환하는 데코레이터.
    'location'이 있으면 해당 지역의 좌표를 우선 조회하고, 없으면 GPS 좌표를 사용합니다.
    """
    @wraps(func)
    async def wrapper(*args, **kwargs):
        is_location_near = kwargs.get('is_location_near', False)
        
        if is_location_near:
            location = kwargs.get('location')
            latitude = kwargs.get('latitude')
            longitude = kwargs.get('longitude')
            coords_for_distance = None
            error_location_name = location # 에러 메시지에 표시될 지역명

            # 1. 'location'이 있으면, 해당 지역의 좌표를 최우선으로 조회
            if location:
                logger.info(f"근접 검색: 'location' 인자 '{location}'이 존재하여, 이 지역의 좌표를 우선 조회합니다.")
                coords_for_distance = await _get_coords_for_location(location)
            
            # 2. 'location'이 없거나 좌표 조회에 실패했을 경우, GPS 좌표를 사용
            elif latitude is not None and longitude is not None:
                logger.info(f"근접 검색: 'location'이 없거나 좌표 조회를 실패하여, GPS 좌표(lat={latitude}, lon={longitude})를 사용합니다.")
                coords_for_distance = {'lat': latitude, 'lon': longitude}
                error_location_name = "현재 위치" # 에러 발생 시 '현재 위치'로 표시

            # '근처' 검색을 위한 좌표를 끝내 찾지 못하면 사용자에게 더 정확한 주소를 요구
            if not coords_for_distance:
                logger.warning(f"근처 검색을 위한 좌표를 찾지 못했습니다: location='{location}'")
                return {"chat_type": "general", "message": f"입력하신 '{error_location_name}'의 위치를 찾을 수 없습니다. 더 정확한 주소(예: 시/도, 시/군/구 포함)를 알려주시겠어요?"}
            
            # 성공 시, 계산된 좌표를 kwargs에 추가하여 원본 함수로 전달
            kwargs['coords_for_distance'] = coords_for_distance
        
        return await func(*args, **kwargs)
    
    return wrapper

# 시/도 이름 전체 집합 생성 (예: '경기도', '경기' 모두 포함)
ALL_PROVINCE_METRO_NAMES = set(name for rule in LOCATION_NORMALIZATION_RULES for name in rule)
VALID_TOP_LEVEL_REGIONS = ALL_PROVINCE_METRO_NAMES.union(set(GROUP_LOCATION_EXPANSION_RULES.keys()))

def escape_string_for_sql(value: str) -> str:
    """SQL 쿼리에 삽입할 문자열을 이스케이프합니다."""
    # 단일 따옴표를 두 배로 늘려 이스케이프합니다. (MySQL 기본 동작)
    return value.replace("'", "''")

def _generate_boolean_term(items: Union[str, List[str]], operator: str = 'AND') -> str:
    """
    여러 단어로 구성된 검색어를 MySQL Boolean Mode 형식으로 변환합니다.
    'AND' 모드: 모든 단어에 '+'를 붙입니다. (+소아 +아토피)
    'OR' 모드: 단어들을 그대로 나열합니다. (소아 아토피)
    """
    if not items:
        return ""
    
    item_list = items if isinstance(items, list) else [items]
    all_tokens = []
    has_space = False
    for item in item_list:
        if not item: continue
        if ' ' in item: has_space = True
        all_tokens.extend(item.strip().split())
    
    if not all_tokens:
        return "", False
    
    if operator.upper() == 'AND':
        return " ".join([f"+{escape_string_for_sql(t)}" for t in all_tokens]), has_space
    else:
        return " ".join([escape_string_for_sql(t) for t in all_tokens]), has_space

def is_sido_included(location: Optional[str]) -> bool:
    """주어진 location 문자열에 최상위 시/도 또는 그룹 지역명이 포함되어 있는지 확인합니다."""
    if not location:
        logger.info(f"is_sido_included: location is empty, returning False.")
        return False
    
    result = any(region in location for region in VALID_TOP_LEVEL_REGIONS)
    logger.info(f"is_sido_included: Checking '{location}'. Result: {result}")
    return result




# SQL agent를 위한 별도 설정 (tools.py와 동일하게 유지)
sql_llm = AzureChatOpenAI(
    azure_deployment=settings.azure_api_model,
    azure_endpoint=settings.azure_endpoint,
    api_key=SecretStr(settings.azure_key),
    api_version=settings.azure_api_version,
    temperature=0
)

# SQLDatabase 인스턴스 생성 (tools.py와 동일하게 유지)
sql_db = SQLDatabase.from_uri(
    f"mysql+mysqlconnector://{settings.mysql_user}:{settings.mysql_password}@{settings.mysql_host}:{settings.mysql_port}/{settings.mysql_db}?collation=utf8mb4_general_ci",
    sample_rows_in_table_info=0,
    include_tables=['hospital', 'hospital_evaluation', 'doctor', 'doctor_basic', 'doctor_career', 'doctor_evaluation']
)


async def _get_coords_for_location(location_name: str):
    """
    DB 조회를 통해 특정 지역명의 평균 좌표를 계산하는 내부 헬퍼 함수.
    다양한 형식의 지역명(예: '서울', '서울 연희동', '서울 서대문구 연희동')을 유연하게 처리하여 좌표를 검색한다.
    """
    logger.info(f"[_get_coords_for_location] 함수 시작. location_name: '{location_name}'")

    # --- START: 신규 로직 추가 (그룹 지역명 + 하위 지역명 조합 처리) ---
    group_name_found = None
    remaining_location = ""

    for g_name in GROUP_LOCATION_EXPANSION_RULES.keys():
        if g_name in location_name:
            # 그룹 이름과 나머지 부분을 분리합니다. "경상도 창원" -> g_name="경상도", remaining="창원"
            remaining = location_name.replace(g_name, "").strip()
            if remaining: # 그룹명 외에 다른 지역명이 있는 경우
                group_name_found = g_name
                remaining_location = remaining
                break
    
    if group_name_found:
        logger.info(f"그룹 지역명 조합 감지: '{group_name_found}' + '{remaining_location}'. 조합 해결 시도.")
        
        # '부울경' -> "(부산 또는 울산 또는 경남)" -> ['부산', '울산', '경남']
        expansion = GROUP_LOCATION_EXPANSION_RULES[group_name_found]
        sub_locations = [loc for loc in re.findall(r'(\w+)', expansion) if loc.lower() not in ['또는', 'or']]

        for sub_loc in sub_locations:
            # "부산 창원", "울산 창원", "경남 창원" 등 새로운 조합 생성
            new_location_name = f"{sub_loc} {remaining_location}"
            logger.info(f"조합 테스트: '{new_location_name}'의 좌표 검색 시도.")
            
            # 재귀적으로 좌표 검색 (단, 무한 루프 방지를 위해 그룹명이 없는 조합으로 호출)
            # 이 재귀 호출은 아래의 일반 로직을 타게 됨
            coords = await _get_coords_for_location(new_location_name)
            if coords:
                logger.info(f"조합 해결 성공: '{new_location_name}'에서 좌표를 찾음.")
                return coords # 첫 번째 성공한 조합의 좌표를 반환

        logger.warning(f"그룹 지역명 '{group_name_found}' 내에서 '{remaining_location}'의 위치를 찾지 못했습니다. 원래 로직으로 계속 진행합니다.")
    # --- END: 신규 로직 추가 ---

    # --- 내부 헬퍼 함수 정의 ---
    def _normalize_location_part(part: str) -> str:
        """지역명 부분을 정규화 (예: '서울시' -> '서울')"""
        for long_name, short_name in LOCATION_NORMALIZATION_RULES:
            if part == long_name:
                return short_name
        return part

    async def _execute_query(conditions: dict):
        """조건(dict)을 받아 DB 쿼리를 실행하고 한 줄의 결과를 반환"""
        where_clauses = []
        params = {}
        for col, val in conditions.items():
            if val:
                param_name = f"param_{col}"
                where_clauses.append(f"{col} = :{param_name}")
                params[param_name] = val
        
        if not where_clauses:
            return None

        where_str = " AND ".join(where_clauses)
        query_str = f"SELECT lat, lon, sidocode_name, sigungu_code_name, eupmyeon FROM hospital WHERE {where_str} AND lat IS NOT NULL AND lon IS NOT NULL AND hid LIKE 'H01KR%' LIMIT 1"
        query = text(query_str)
        
        def _run_sync_query():
            with sql_db._engine.connect() as connection:
                return connection.execute(query, params).fetchone()
        
        return await asyncio.to_thread(_run_sync_query)

    def _process_result(row, original_location):
        """쿼리 결과(row)를 좌표 dict로 변환하고 로깅. 단, 원래 요청된 시/도와 결과가 일치하는지 확인."""
        if not (row and row.lat is not None):
            return None

        sido = row.sidocode_name or ''
        sigungu = row.sigungu_code_name or ''
        
        # 원래 요청에 특정 시/도(경북, 경남 등)가 포함되었는지 확인
        requested_sido = None
        # 요청된 위치 문자열을 순회하며 정규화된 시/도 이름을 찾는다.
        for long_name, short_name in LOCATION_NORMALIZATION_RULES:
            if long_name in original_location or short_name in original_location:
                requested_sido = short_name # 비교를 위해 짧은 이름 사용
                break
        
        # 요청된 시/도 정보가 있고, 결과의 시/도와 일치하지 않으면 유효하지 않은 결과로 처리
        # row.sido가 이미 짧은 이름 형태라고 가정
        if requested_sido and sido and requested_sido != sido:
            logger.debug(f"결과 불일치: 요청된 시/도('{requested_sido}')와 찾은 시/도('{sido}')가 다릅니다. 이 결과는 건너뜁니다.")
            return None

        # 일치하거나, 시/도 제약이 없는 경우에만 성공으로 처리
        coords = {'lat': float(row.lat), 'lon': float(row.lon)}
        eupmyeon = row.eupmyeon or ''
        found_location = f"{sido} {sigungu} {eupmyeon}".strip().replace("  ", " ")
        logger.info(f"내부 Geocoding 성공: '{original_location}' -> '{found_location}' 좌표: {coords}")
        return coords

    # --- 1단계: 그룹 지역명 처리 ---
    if location_name in GROUP_LOCATION_EXPANSION_RULES:
        logger.info(f"1단계: 그룹 지역명 처리 - '{location_name}'")
        expansion = GROUP_LOCATION_EXPANSION_RULES[location_name]
        sub_locations = [loc for loc in re.findall(r'(\w+)', expansion) if loc.lower() not in ['또는', 'or']]
        if sub_locations:
            where_clause = " OR ".join([f"sidocode_name = '{loc}'" for loc in sub_locations])
            query_str = f"SELECT AVG(lat) as lat, AVG(lon) as lon FROM hospital WHERE ({where_clause}) AND lat IS NOT NULL AND lon IS NOT NULL AND hid LIKE 'H01KR%'"
            try:
                def _run_group_query():
                    with sql_db._engine.connect() as connection:
                        return connection.execute(text(query_str)).fetchone()
                row = await asyncio.to_thread(_run_group_query)
                if row and row.lat is not None:
                    coords = {'lat': float(row.lat), 'lon': float(row.lon)}
                    logger.info(f"내부 Geocoding 성공 (그룹 지역명): '{location_name}' -> 좌표: {coords}")
                    return coords
            except Exception as e:
                logger.error(f"그룹 지역명 Geocoding 중 오류 발생 '{location_name}': {e}", exc_info=True)

    # --- 2단계: 일반 지역명 처리 ---
    # 2-1. 입력값 정규화 및 그룹 지역명 분리
    location_to_process = location_name.strip()
    
    # '경상도 창원시' 같은 입력에서 '경상도'를 분리하고 '창원시'만 남김
    for group_name in GROUP_LOCATION_EXPANSION_RULES.keys():
        if group_name in location_to_process:
            # 그룹명을 공백으로 치환하여 나머지 부분만 남긴다.
            # "경상도 창원시" -> " 창원시" -> "창원시"
            location_to_process = location_to_process.replace(group_name, "").strip()
            logger.info(f"그룹 지역명 '{group_name}'을 분리하고, 좌표 검색 대상을 '{location_to_process}'으로 변경합니다.")
            # 만약 그룹명을 제외하고 남는게 없다면(예: '경상도'만 입력됨) 더이상 진행하지 않고,
            # 앞서 1단계에서 처리된 그룹 평균 좌표를 사용하도록 유도.
            if not location_to_process:
                 logger.info("좌표 검색 대상이 비어있어, 1단계의 그룹 평균 좌표 결과를 사용합니다.")
                 return None # 1단계에서 이미 처리되었어야 함
            break # 첫 번째 일치하는 그룹만 처리

    # 2-2. 정규화된 지역명을 공백 기준으로 분리
    location_parts = [_normalize_location_part(p) for p in location_to_process.strip().split()]
    num_parts = len(location_parts)

    # 2-3. 전체 이름으로 검색 (가장 먼저 시도)
    full_name = " ".join(location_parts)
    logger.info(f"2단계: 전체 이름으로 검색 시도 - '{full_name}'")
    for col in ['eupmyeon', 'sigungu_code_name', 'sidocode_name']:
        row = await _execute_query({col: full_name})
        coords = _process_result(row, location_name)
        if coords: return coords

    # 2-4. 조합 검색 (주소 파트가 2개 또는 3개일 경우)
    if num_parts == 2:
        part1, part2 = location_parts[0], location_parts[1]
        logger.info(f"2단계: 2-파트 조합 검색 시도 - '{part1}', '{part2}'")
        combinations = [
            {'sidocode_name': part1, 'sigungu_code_name': part2},
            {'sidocode_name': part1, 'eupmyeon': part2},
            {'sigungu_code_name': part1, 'eupmyeon': part2},
        ]
        for combo in combinations:
            row = await _execute_query(combo)
            coords = _process_result(row, location_name)
            if coords: return coords

    if num_parts == 3:
        part1, part2, part3 = location_parts[0], location_parts[1], location_parts[2]
        logger.info(f"2단계: 3-파트 조합 검색 시도 - '{part1}', '{part2}', '{part3}'")
        row = await _execute_query({'sidocode_name': part1, 'sigungu_code_name': part2, 'eupmyeon': part3})
        coords = _process_result(row, location_name)
        if coords: return coords

    # 2-5. 단일 부분 검색 (폴백)
    logger.info("3단계: 단일 부분으로 폴백 검색 시도")
    for part in reversed(location_parts):
        logger.info(f"단일 부분 검색 - '{part}'")
        for col in ['eupmyeon', 'sigungu_code_name', 'sidocode_name']:
            row = await _execute_query({col: part})
            coords = _process_result(row, location_name)
            if coords: return coords

    # --- 3단계: 외부 Geocoding API 폴백 ---
    logger.info(f"내부 DB 검색 실패. 외부 Geocoding API로 폴백합니다: '{location_name}'")
    try:
        geolocator = Nominatim(user_agent="aiga_llm_server") # user_agent는 필수 항목입니다.
        
        # geopy의 geocode는 동기 함수이므로, asyncio.to_thread를 사용해 비동기적으로 실행합니다.
        location = await asyncio.to_thread(geolocator.geocode, location_name, timeout=5, language='ko')
        
        if location:
            # --- START: 외부 Geocoding 결과 검증 로직 추가 ---
            requested_sido = None
            for long_name, short_name in LOCATION_NORMALIZATION_RULES:
                if long_name in location_name or short_name in location_name:
                    requested_sido = short_name # 비교를 위해 짧은 이름 사용
                    break
            
            # Nominatim이 반환한 주소(location.address)에 요청된 시/도가 포함되어 있는지 확인
            found_address = location.address or ""
            
            # 요청된 시/도가 있는데, 반환된 주소에 해당 시/도의 짧은 이름 또는 긴 이름이 없는 경우, 잘못된 결과로 간주
            if requested_sido:
                # LOCATION_NORMALIZATION_RULES에서 requested_sido에 해당하는 긴 이름을 찾는다
                long_name_for_sido = next((long for long, short in LOCATION_NORMALIZATION_RULES if short == requested_sido), requested_sido)
                if not (requested_sido in found_address or long_name_for_sido in found_address):
                    logger.warning(f"외부 Geocoding 결과 불일치: 요청 위치 '{location_name}'(시/도: {requested_sido})이지만, 찾은 주소는 '{found_address}'입니다. 결과를 무시합니다.")
                    location = None # location을 None으로 설정하여 아래 로직에서 실패 처리되도록 함

            if location: # 검증 후에도 location이 유효한 경우
                coords = {'lat': location.latitude, 'lon': location.longitude}
                logger.info(f"외부 Geocoding 성공: '{location_name}' -> '{location.address}' 좌표: {coords}")
                return coords
        
        # location이 None인 경우 (처음부터 없었거나, 검증 실패로 None이 된 경우)
        logger.warning(f"외부 Geocoding도 실패 또는 검증 실패: '{location_name}'의 좌표를 찾지 못했습니다.")

    except ImportError:
        logger.error("geopy 라이브러리가 설치되지 않아 외부 Geocoding 폴백을 실행할 수 없습니다.")
    except Exception as e:
        logger.error(f"외부 Geocoding 중 오류 발생: {e}", exc_info=True)

    logger.warning(f"내부 및 외부 Geocoding 최종 실패: '{location_name}'의 좌표를 찾지 못했습니다.")
    return None


def _build_location_where_clause(location: Optional[str], latitude: Optional[float] = None, longitude: Optional[float] = None, is_location_near: bool = False) -> str:
    """Helper function to build the location MATCH...AGAINST clause."""
    
    if is_location_near:
        logger.info("근접 검색(is_location_near=True)으로, 주소 MATCH 조건을 생성하지 않습니다.")
        return ""

    if not location:
        return ""
    
    location_str = location.strip()
    if location_str in ["전국", "전체"]:
        return ""
        
    search_terms = []
    
    # 1. Expand group names first (e.g., "부울경" -> "(부산 또는 울산 또는 경남)")
    #    Replace '또는' with 'OR' for MySQL Full-Text Search.
    sorted_group_names = sorted(GROUP_LOCATION_EXPANSION_RULES.keys(), key=len, reverse=True)
    for group_name in sorted_group_names:
        if group_name in location_str:
            expansion_with_or = GROUP_LOCATION_EXPANSION_RULES[group_name].replace('또는', 'OR')
            location_str = location_str.replace(group_name, f"({expansion_with_or})") # 확장된 부분은 괄호로 묶음
            logger.debug(f"그룹 지역명 '{group_name}' 확장 후 location_str: {location_str}")

    # 2. Split into parts: recognize parenthesized OR clauses and individual words
    #    re.findall(r'\([^)]+\)|\S+') will capture "(A OR B)" as one part, and "Word" as another.
    parts = re.findall(r'\([^)]+\)|\S+', location_str, flags=re.IGNORECASE)

    for part in parts:
        part = part.strip()
        if not part:
            continue
        
        # If it's a parenthesized OR clause (e.g., "(부산 OR 울산)")
        if part.startswith('(') and part.endswith(')'):
            search_terms.append(escape_string_for_sql(part))
        else:
            # It's an individual word (e.g., "서울", "중구"), add '+' for AND condition
            search_terms.append(f"+{escape_string_for_sql(part)}")

    if not search_terms:
        return ""

    # Combine all search terms. No special sorting needed as '+' already determines AND logic.
    search_terms_for_match = " ".join(search_terms)

    logger.info(f"Constructed location search terms: {search_terms_for_match}")
    return f"AND MATCH(h.address) AGAINST('{search_terms_for_match}' IN BOOLEAN MODE)"


@tool
@handle_proximity_search
async def search_hospitals_by_location_and_department(department: Union[str, List[str]], location: Optional[str] = None, latitude: Optional[float] = None, longitude: Optional[float] = None, is_location_near: bool = False, coords_for_distance: Optional[dict] = None, limit: Optional[int] = None) -> dict:
    """지역과 진료과를 기반으로 병원을 검색하는 도구.
    
    Args:
        department: 필수 - 찾을 진료과명. 단일 진료과(str) 또는 여러 진료과(list[str])를 모두 처리 가능.
        location: 선택 - 병원을 찾을 지역명 (예: "서울", "경남", "부울경").
        latitude: 선택 - 사용자의 현재 위도. 제공되면 가까운 순으로 정렬.
        longitude: 선택 - 사용자의 현재 경도. 제공되면 가까운 순으로 정렬.
        is_location_near: 선택 - '근처' 검색 여부를 나타내는 플래그. True이면 주소 텍스트 매칭을 비활성화.
        coords_for_distance: (내부용) 데코레이터가 계산한 좌표.
        limit: 선택 - 반환할 결과의 최대 수.
    """
    logger.info(f"DEBUG: search_hospitals_by_location_and_department received args: department='{department}', location='{location}', latitude={latitude}, longitude={longitude}, is_location_near={is_location_near}, limit={limit}")
    
    final_limit = _get_final_limit(limit)

    # 1차 검색어 생성 (AND 방식)
    department_search_term, has_space = _generate_boolean_term(department, 'AND')

    if not department_search_term:
        # 부서 정보가 없으면 검색 의미 없음
        return {"chat_type": "recommand_hospital", "answer": {"hospitals": []}}

    def _perform_hospital_search(search_term):
        department_where_clause = f"AND MATCH(db.deptname) AGAINST('{search_term}' IN BOOLEAN MODE)"
        
        if coords_for_distance:
            distance_km = settings.distance_square_meter
            lat, lon = coords_for_distance['lat'], coords_for_distance['lon']
            lat_range, lon_range = distance_km / 111.0, distance_km / 88.0
            
            query = f"""
                SELECT 
                    h.shortName as name, h.address, h.telephone, h.hospital_site, h.lat, h.lon, h.hid as hospital_id,
                    ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({lon}, {lat})) as distance
                FROM (
                    SELECT DISTINCT hid, lat, lon, shortName, address, telephone ,hospital_site
                    FROM hospital
                    WHERE lat BETWEEN {lat - lat_range} AND {lat + lat_range}
                      AND lon BETWEEN {lon - lon_range} AND {lon + lon_range}
                ) h
                JOIN doctor_basic db ON h.hid = db.hid
                WHERE db.is_active in (1,2) {department_where_clause}
                GROUP BY h.hid
                ORDER BY distance
                LIMIT {final_limit};
            """
        else:
            location_where_clause = _build_location_where_clause(location, latitude, longitude, is_location_near)
            query = f"""
                SELECT 
                    h.shortName as name, h.address, h.telephone,h.hospital_site, h.lat, h.lon, h.hid as hospital_id
                FROM hospital h JOIN doctor_basic db ON h.hid = db.hid
                WHERE 1=1 {location_where_clause} {department_where_clause}
                  AND db.is_active in (1,2)
                GROUP BY h.hid
                LIMIT {final_limit};
            """
        
        logger.info(f"Executing SQL Query: {query}")
        with sql_db._engine.connect() as connection:
            return connection.execute(text(query)).fetchall()

    try:
        # 1단계: AND 검색 수행
        list_of_tuples = await asyncio.to_thread(_perform_hospital_search, department_search_term)
        
        # 결과가 없고 공백이 포함된 경우 2단계: OR 검색 수행
        if not list_of_tuples and has_space:
            logger.info("No results with AND search. Attempting Fallback with OR search.")
            department_search_term_or, _ = _generate_boolean_term(department, 'OR')
            list_of_tuples = await asyncio.to_thread(_perform_hospital_search, department_search_term_or)
        
        hospitals = []
        if list_of_tuples:
            try:
                hospitals = [
                    {
                        "name": row.name,
                        "address": row.address,
                        "telephone": row.telephone,
                        "hospital_site" : row.hospital_site,
                        "lat": row.lat,
                        "lon": row.lon,
                        "hospital_id": row.hospital_id,
                    }
                    for row in list_of_tuples
                ]
            except Exception as e:
                logger.error(f"Error parsing templated SQL result: {e} - Result string: {list_of_tuples}")
                hospitals = []

        logger.info(f"Found {len(hospitals)} hospitals using search_hospitals_by_location_and_department.")
        logger.info(f"search_hospitals_by_location_and_department 툴 실행 성공. {len(hospitals)}개 병원 정보 반환.")
        return {
            "chat_type": "recommand_hospital",
            "answer": {
                "hospitals": hospitals,
            }
        }

    except Exception as e:
        logger.error(f"Error in search_hospitals_by_location_and_department: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"병원 검색 중 오류가 발생했습니다: {str(e)}"}

@tool
async def search_doctor_details_by_name(name: Union[str, List[str]], hospital: Optional[Union[str, List[str]]] = None, latitude: Optional[float] = None, longitude: Optional[float] = None, limit: Optional[int] = None, proposal: str = "") -> dict:
    """의사 이름(name)과 선택적인 병원명(hospital)을 기반으로 의사의 상세 정보를 검색하는 도구.
    
    Args:
        name: 필수 - 검색할 의사 이름. 단일 이름(str) 또는 여러 이름(list[str]) 처리 가능.
        hospital: 선택 - 의사가 소속된 병원 이름. 단일 이름(str) 또는 여러 이름(list[str]) 처리 가능.
        latitude: 선택 - 사용자의 현재 위도. 제공되면 의사가 소속된 병원을 가까운 순으로 정렬.
        longitude: 선택 - 사용자의 현재 경도. 제공되면 의사가 소속된 병원을 가까운 순으로 정렬.
        limit: 선택 - 반환할 결과의 최대 수.
        proposal: 선택 - LLM이 제안하는 핵심 키워드나 제안.
    """
    logger.info(f"tool: search_doctor_details_by_name 시작 - name: {name}, hospital: {hospital}, latitude: {latitude}, longitude: {longitude}, limit: {limit}, proposal: {proposal}")

    final_limit = _get_final_limit(limit)
    params = {}
    name_where_clause = ""
    hospital_where_clause = ""

    # Handle 'name' parameter (string or list)
    if isinstance(name, list) and name:
        name_clauses = []
        for i, n in enumerate(name):
            if n and n.strip():
                param_key = f"name_{i}"
                name_clauses.append(f"db.doctorname LIKE :{param_key}")
                params[param_key] = f"%%{n.strip()}%%"
        if name_clauses:
            name_where_clause = f"AND ({' OR '.join(name_clauses)})"
    elif isinstance(name, str) and name.strip():
        params["name"] = f"%%{name.strip()}%%"
        name_where_clause = "AND db.doctorname LIKE :name"
    
    # Handle 'hospital' parameter (string or list)
    if isinstance(hospital, list) and hospital:
        hospital_clauses = []
        for i, h in enumerate(hospital):
            if h and h.strip():
                param_key = f"hospital_{i}"
                hospital_clauses.append(f"h.shortname LIKE :{param_key}")
                params[param_key] = f"%%{h.strip()}%%"
        if hospital_clauses:
            hospital_where_clause = f"AND ({' OR '.join(hospital_clauses)})"
    elif isinstance(hospital, str) and hospital.strip():
        params["hospital"] = f"%%{hospital.strip()}%%"
        hospital_where_clause = "AND h.shortname LIKE :hospital"

    if not name_where_clause:
        return {"chat_type": "search_doctor", "answer": {"doctors": []}}

    score_weight = float(os.getenv("SCORE_WEIGHT", 0.3))
    total_score_select = f""", (
        IFNULL(de.patient_score, 0) * {score_weight} + 
        IFNULL(de.public_score, 0) * {score_weight}
    ) as total_score"""
    
    distance_select = ""
    order_by_clause = ""
    bounding_box_clause = ""
    if latitude is not None and longitude is not None:
        distance_km = settings.distance_square_meter
        lat, lon = latitude, longitude
        lat_range, lon_range = distance_km / 111.0, distance_km / 88.0
        bounding_box_clause = f" AND (h.lat BETWEEN {lat - lat_range} AND {lat + lat_range}) AND (h.lon BETWEEN {lon - lon_range} AND {lon + lon_range})"
        distance_select = f", ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({longitude}, {latitude})) as distance"
        order_by_clause = "ORDER BY distance ASC, total_score DESC"
    else:
        order_by_clause = "ORDER BY total_score DESC"

    template_query = f"""
        SELECT
            d.doctor_id, HEX(d.rid) as hexrid, h.shortname, h.address, h.lat, h.lon, h.telephone, h.hospital_site, h.hid as hospital_hid,
            db.doctorname, db.deptname, db.specialties, db.doctor_url,
            dc.education, dc.career, db.profileimgurl,
            de.paper_score, de.patient_score, de.public_score,
            de.kindness, de.satisfaction, de.explanation, de.recommendation
            {distance_select}
            {total_score_select}
        FROM
            doctor d LEFT JOIN doctor_basic db ON d.rid = db.rid
            LEFT JOIN hospital h ON db.hid = h.hid
            LEFT JOIN doctor_career dc ON d.rid = dc.rid
            LEFT JOIN (
                SELECT
                    doctor_id, AVG(paper_score) AS paper_score, AVG(patient_score) AS patient_score,
                    AVG(public_score) AS public_score, AVG(kindness) AS kindness, AVG(satisfaction) AS satisfaction,
                    AVG(explanation) AS explanation, AVG(recommendation) AS recommendation
                FROM aiga2025.doctor_evaluation GROUP BY doctor_id
            ) de ON d.doctor_id = de.doctor_id
        WHERE
            db.is_active in (1,2)
            {name_where_clause}
            {hospital_where_clause}
            {bounding_box_clause}
        {order_by_clause}
        LIMIT {final_limit};
    """
    logger.info(f"Generated SQL Query: {template_query}, Params: {params}")
    
    try:
        def _execute_query():
            with sql_db._engine.connect() as connection:
                return connection.execute(text(template_query), params).fetchall()

        list_of_tuples = await asyncio.to_thread(_execute_query)
        
        doctors = []
        if list_of_tuples:
            try:
                for row in list_of_tuples:
                    doctor = {
                        "doctor_id": row.doctor_id, "doctor_rid": row.hexrid, "hospital": row.shortname, "address": row.address,
                        "lat": row.lat, "lon": row.lon, "telephone": row.telephone, "hospital_site": row.hospital_site, "hospital_hid" : row.hospital_hid,
                        "name": row.doctorname,"deptname": row.deptname, "specialties": row.specialties, "url": row.doctor_url,
                        "education": row.education, "career": row.career, "photo": row.profileimgurl,
                        "doctor_score": {"paper_score": row.paper_score or 0.0, "patient_score": row.patient_score or 0.0, "public_score": row.public_score or 0.0, "peer_score": 0.0},
                        "ai_score": {"kindness": (row.kindness or 0.0) * 5.0, "satisfaction": (row.satisfaction or 0.0) * 5.0, "explanation": (row.explanation or 0.0) * 5.0, "recommendation": (row.recommendation or 0.0) * 5.0},
                        "paper": [], "review": []
                    }
                    doctors.append(doctor)
            except Exception as e:
                logger.error(f"Error parsing templated SQL result for doctor details: {e} - Result string: {list_of_tuples}")
                doctors = []
        
        logger.info(f"search_doctor_details_by_name 툴 실행 성공. {len(doctors)}명의 의사 정보 반환.")
        return {"chat_type": "search_doctor", "answer": {"doctors": doctors, "proposal": proposal}}

    except Exception as e:
        logger.error(f"Error in search_doctor_details_by_name: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"의사 상세 정보 검색 중 오류가 발생했습니다: {str(e)}"}


@tool
async def search_hospital_details_by_name(name: Union[str, List[str]], latitude: Optional[float] = None, longitude: Optional[float] = None, limit: Optional[int] = None) -> dict:
    """병원 이름(name)을 기반으로 병원의 상세 정보를 검색하는 도구.
    
    Args:
        name: 필수 - 검색할 병원 이름. 단일 이름(str) 또는 여러 이름(list[str]) 처리 가능.
        latitude: 선택 - 사용자의 현재 위도. 제공되면 병원을 가까운 순으로 정렬.
        longitude: 선택 - 사용자의 현재 경도. 제공되면 병원을 가까운 순으로 정렬.
        limit: 선택 - 반환할 결과의 최대 수.
    """
    logger.info(f"tool: search_hospital_details_by_name 시작 - name: {name}, latitude: {latitude}, longitude: {longitude}, limit: {limit}")

    final_limit = _get_final_limit(limit)
    params = {}
    name_where_clause = ""
    if isinstance(name, list) and name:
        name_clauses = []
        for i, n in enumerate(name):
            if n and n.strip():
                param_key = f"name_{i}"
                name_clauses.append(f"h2.alias_name LIKE :{param_key}")
                params[param_key] = f"%%{n.strip()}%%"
        if name_clauses:
            name_where_clause = f"AND ({' OR '.join(name_clauses)})"
    elif isinstance(name, str) and name.strip():
        params["name"] = f"%%{name.strip()}%%"
        name_where_clause = "AND h2.alias_name LIKE :name"

    if not name_where_clause:
        return {"chat_type": "recommand_hospital", "answer": {"hospitals": []}}

    distance_select = ""
    bounding_box_clause = ""
    if latitude is not None and longitude is not None:
        distance_km = settings.distance_square_meter
        lat, lon = latitude, longitude
        lat_range, lon_range = distance_km / 111.0, distance_km / 88.0
        bounding_box_clause = f" AND (h.lat BETWEEN {lat - lat_range} AND {lat + lat_range}) AND (h.lon BETWEEN {lon - lon_range} AND {lon + lon_range})"
        distance_select = f", ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({longitude}, {latitude})) as distance"

    final_order_by = "ORDER BY h2.aid"
    if latitude is not None and longitude is not None:
        final_order_by = "ORDER BY distance, h2.aid"

    template_query = f"""
        SELECT
            h.hid as hospital_id, h.shortName as name, h.address, h.telephone, h.hospital_site,  h.lat, h.lon
            {distance_select}
        FROM
            hospital_alias h2 INNER JOIN hospital h ON h.hid = h2.hid
        WHERE
            1=1
            {name_where_clause}
            {bounding_box_clause}
        {final_order_by}
        LIMIT {final_limit};
    """

    try:
        def _execute_query():
            with sql_db._engine.connect() as connection:
                return connection.execute(text(template_query), params).fetchall()

        list_of_tuples = await asyncio.to_thread(_execute_query)
        
        hospitals = []
        if list_of_tuples:
            try:
                hospitals = [
                    {"hospital_id": row.hospital_id, "name": row.name, "address": row.address, "telephone": row.telephone, "hospital_site" : row.hospital_site, "lat": row.lat, "lon": row.lon}
                    for row in list_of_tuples
                ]
            except Exception as e:
                logger.error(f"Error parsing templated SQL result for hospital details: {e} - Result string: {list_of_tuples}")
                hospitals = []

        logger.info(f"search_hospital_details_by_name 툴 실행 성공. {len(hospitals)}개 병원 정보 반환.")
        return {
            "chat_type": "recommand_hospital",
            "answer": {"hospitals": hospitals}
        }

    except Exception as e:
        logger.error(f"Error in search_hospital_details_by_name: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"병원 상세 정보 검색 중 오류가 발생했습니다: {str(e)}"}

@tool
@handle_proximity_search
async def search_doctors_by_location_and_department(department: Union[str, List[str]], location: Optional[str] = None, latitude: Optional[float] = None, longitude: Optional[float] = None, is_location_near: bool = False, coords_for_distance: Optional[dict] = None, limit: Optional[int] = None, proposal: str = "") -> dict:
    """지역과 진료과를 기반으로 의사를 검색하는 도구.
    
    Args:
        department: 필수 - 찾을 진료과명. 단일 진료과(str) 또는 여러 진료과(list[str])를 모두 처리 가능.
        location: 선택 - 의사를 찾을 지역명 (예: "서울", "경남", "부울경").
        latitude: 선택 - 사용자의 현재 위도. 제공되면 의사가 소속된 병원을 가까운 순으로 정렬.
        longitude: 선택 - 사용자의 현재 경도. 제공되면 의사가 소속된 병원을 가까운 순으로 정렬.
        is_location_near: 선택 - '근처' 검색 여부를 나타내는 플래그. True이면 주소 텍스트 매칭을 비활성화.
        coords_for_distance: (내부용) 데코레이터가 계산한 좌표.
        limit: 선택 - 반환할 결과의 최대 수.
        proposal: 선택 - LLM이 제안하는 핵심 키워드나 제안.
    """
    logger.info(f"tool: search_doctors_by_location_and_department 시작 - location: {location}, department: {department}, latitude: {latitude}, longitude: {longitude}, limit: {limit}, proposal: {proposal}")

    final_limit = _get_final_limit(limit)

    # 1차 검색어 생성 (AND 방식)
    department_search_term, has_space = _generate_boolean_term(department, 'AND')

    if not department_search_term:
        return {"chat_type": "search_doctor", "answer": {"doctors": []}}

    def _perform_doctor_search(search_term):
        department_where_clause = f"AND MATCH(db.deptname) AGAINST('{search_term}' IN BOOLEAN MODE)"
        score_weight = float(os.getenv("SCORE_WEIGHT", 0.3))
        total_score_select = f""", (
            IFNULL(de.patient_score, 0) * {score_weight} + 
            IFNULL(de.public_score, 0) * {score_weight}
        ) as total_score"""
        
        distance_select = ""
        order_by_clause = ""
        from_clause = ""

        if coords_for_distance:
            distance_km = settings.distance_square_meter
            lat, lon = coords_for_distance['lat'], coords_for_distance['lon']
            lat_range, lon_range = distance_km / 111.0, distance_km / 88.0

            from_clause = f"""
            FROM (
                SELECT DISTINCT hid, lat, lon, shortName, address, telephone , hospital_site
                FROM hospital
                WHERE lat BETWEEN {lat - lat_range} AND {lat + lat_range}
                  AND lon BETWEEN {lon - lon_range} AND {lon + lon_range}
            ) h
            INNER JOIN doctor_basic db ON db.hid = h.hid
            """
            distance_select = f", ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({lon}, {lat})) as distance"
            order_by_clause = "ORDER BY distance ASC, total_score DESC"
        else:
            location_where_clause = _build_location_where_clause(location, latitude, longitude, is_location_near)
            from_clause = f"FROM doctor_basic db INNER JOIN hospital h ON db.hid = h.hid AND 1=1 {location_where_clause}"
            order_by_clause = "ORDER BY total_score DESC"

        query = f"""
            SELECT
                d.doctor_id, h.shortname, h.address, h.lat, h.lon, h.telephone, h.hospital_site, h.hid as hospital_hid,
                db.doctorname, db.deptname, db.specialties, db.doctor_url,
                dc.education, dc.career, db.profileimgurl,
                de.paper_score, de.patient_score, de.public_score, de.kindness,
                de.satisfaction, de.explanation, de.recommendation
                {distance_select}
                {total_score_select}
            {from_clause}
            LEFT JOIN doctor d ON db.rid = d.rid
            LEFT JOIN doctor_career dc ON d.rid = dc.rid
            LEFT JOIN (
                SELECT
                    doctor_id, AVG(paper_score) AS paper_score, AVG(patient_score) AS patient_score,
                    AVG(public_score) AS public_score, AVG(kindness) AS kindness, AVG(satisfaction) AS satisfaction,
                    AVG(explanation) AS explanation, AVG(recommendation) AS recommendation
                FROM aiga2025.doctor_evaluation
                GROUP BY doctor_id
            ) de ON d.doctor_id = de.doctor_id
            WHERE
                db.is_active in (1,2)
                {department_where_clause}
            {order_by_clause}
            LIMIT {final_limit};
        """
        logger.info(f"Executing SQL Query: {query}")
        with sql_db._engine.connect() as connection:
            return connection.execute(text(query)).fetchall()

    try:
        # 1단계: AND 검색 수행
        list_of_tuples = await asyncio.to_thread(_perform_doctor_search, department_search_term)
        
        # 결과가 없고 공백이 포함된 경우 2단계: OR 검색 수행
        if not list_of_tuples and has_space:
            logger.info("No results with AND search. Attempting Fallback with OR search.")
            department_search_term_or, _ = _generate_boolean_term(department, 'OR')
            list_of_tuples = await asyncio.to_thread(_perform_doctor_search, department_search_term_or)
        
        doctors = []
        if list_of_tuples:
            try:
                for row in list_of_tuples:
                    doctor = {
                        "doctor_id": row.doctor_id, "hospital": row.shortname, "address": row.address,
                        "lat": row.lat, "lon": row.lon, "telephone": row.telephone, "hospital_site" : row.hospital_site, "hospital_hid" : row.hospital_hid,
                        "name": row.doctorname, "deptname": row.deptname, "specialties": row.specialties,
                        "url": row.doctor_url, "education": row.education, "career": row.career, "photo": row.profileimgurl,
                        "doctor_score": {"paper_score": row.paper_score or 0.0, "patient_score": row.patient_score or 0.0, "public_score": row.public_score or 0.0, "peer_score": 0.0},
                        "ai_score": {"kindness": (row.kindness or 0.0) * 5.0, "satisfaction": (row.satisfaction or 0.0) * 5.0, "explanation": (row.explanation or 0.0) * 5.0, "recommendation": (row.recommendation or 0.0) * 5.0},
                        "paper": [], "review": []
                    }
                    doctors.append(doctor)
            except Exception as e:
                logger.error(f"Error parsing templated SQL result for doctor search: {e} - Result string: {list_of_tuples}")
                doctors = []
    
        logger.info(f"search_doctors_by_location_and_department 툴 실행 성공. {len(doctors)}명의 의사 정보 반환.")
        return {
            "chat_type": "search_doctor",
            "answer": {
                "doctors": doctors,
                "proposal": proposal
            }
        }

    except Exception as e:
        logger.error(f"Error in search_doctors_by_location_and_department: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"의사 검색 중 오류가 발생했습니다: {str(e)}"}


@tool
@handle_proximity_search
async def search_doctors_by_disease_and_location(disease: Union[str, List[str]], location: Optional[str] = None, latitude: Optional[float] = None, longitude: Optional[float] = None, is_location_near: bool = False, coords_for_distance: Optional[dict] = None, limit: Optional[int] = None, proposal: str = "") -> dict:
    """지역과 질환을 기반으로 의사를 검색하는 도구. (parse_specialties 컬럼 활용)
    
    Args:
        disease: 필수 - 찾을 질환명. 단일 질환(str) 또는 여러 질환(list[str])을 모두 처리 가능.
        location: 선택 - 의사를 찾을 지역명 (예: "서울", "경남", "부울경").
        latitude: 선택 - 사용자의 현재 위도. 제공되면 의사가 소속된 병원을 가까운 순으로 정렬.
        longitude: 선택 - 사용자의 현재 경도. 제공되면 의사가 소속된 병원을 가까운 순으로 정렬.
        is_location_near: 선택 - '근처' 검색 여부를 나타내는 플래그. True이면 주소 텍스트 매칭을 비활성화.
        coords_for_distance: (내부용) 데코레이터가 계산한 좌표.
        limit: 선택 - 반환할 결과의 최대 수.
        proposal: 선택 - LLM이 제안하는 핵심 키워드나 제안.
    """
    logger.info(f"tool: search_doctors_by_disease_and_location 시작 - location: {location}, disease: {disease}, latitude: {latitude}, longitude: {longitude}, limit: {limit}, proposal: {proposal}")

    final_limit = _get_final_limit(limit)

    # 1차 검색어 생성 (AND 방식)
    disease_search_term, has_space = _generate_boolean_term(disease, 'AND')

    if not disease_search_term:
        return {"chat_type": "search_doctor", "answer": {"doctors": []}}

    def _perform_disease_doctor_search(search_term):
        disease_where_clause = f"AND MATCH(db.parse_specialties) AGAINST('{search_term}' IN BOOLEAN MODE)"
        score_weight = float(os.getenv("SCORE_WEIGHT", 0.3))
        total_score_select = f""", (
            IFNULL(de.patient_score, 0) * {score_weight} + 
            IFNULL(de.public_score, 0) * {score_weight}
        ) as total_score"""

        distance_select = ""
        order_by_clause = ""
        from_clause = ""

        if coords_for_distance:
            distance_km = settings.distance_square_meter
            lat, lon = coords_for_distance['lat'], coords_for_distance['lon']
            lat_range, lon_range = distance_km / 111.0, distance_km / 88.0

            from_clause = f"""
            FROM (
                SELECT DISTINCT hid, lat, lon, shortName, address, telephone ,hospital_site
                FROM hospital
                WHERE lat BETWEEN {lat - lat_range} AND {lat + lat_range}
                  AND lon BETWEEN {lon - lon_range} AND {lon + lon_range}
            ) h
            INNER JOIN doctor_basic db ON db.hid = h.hid
            """
            distance_select = f", ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({lon}, {lat})) as distance"
            order_by_clause = "ORDER BY distance ASC, total_score DESC"
        else:
            location_where_clause = _build_location_where_clause(location, latitude, longitude, is_location_near)
            from_clause = f"FROM doctor_basic db INNER JOIN hospital h ON db.hid = h.hid AND 1=1 {location_where_clause}"
            order_by_clause = "ORDER BY total_score DESC"

        query = f"""
            SELECT
                d.doctor_id, h.shortname, h.address, h.lat, h.lon, h.telephone, h.hospital_site, h.hid as hospital_hid,
                db.doctorname, db.deptname, db.specialties, db.doctor_url,
                dc.education, dc.career, db.profileimgurl,
                de.paper_score, de.patient_score, de.public_score, de.kindness,
                de.satisfaction, de.explanation, de.recommendation
                {distance_select}
                {total_score_select}
            {from_clause}
            LEFT JOIN doctor d ON db.rid = d.rid
            LEFT JOIN doctor_career dc ON d.rid = dc.rid
            LEFT JOIN (
                SELECT
                    doctor_id, AVG(paper_score) AS paper_score, AVG(patient_score) AS patient_score,
                    AVG(public_score) AS public_score, AVG(kindness) AS kindness, AVG(satisfaction) AS satisfaction,
                    AVG(explanation) AS explanation, AVG(recommendation) AS recommendation
                FROM aiga2025.doctor_evaluation
                GROUP BY doctor_id
            ) de ON d.doctor_id = de.doctor_id
            WHERE
                db.is_active in (1,2)
                {disease_where_clause}
            {order_by_clause}
            LIMIT {final_limit};
        """
        logger.info(f"Executing SQL Query: {query}")
        with sql_db._engine.connect() as connection:
            return connection.execute(text(query)).fetchall()

    try:
        # 1단계: AND 검색 수행
        list_of_tuples = await asyncio.to_thread(_perform_disease_doctor_search, disease_search_term)
        
        # 결과가 없고 공백이 포함된 경우 2단계: OR 검색 수행
        if not list_of_tuples and has_space:
            logger.info("No results with AND search. Attempting Fallback with OR search.")
            disease_search_term_or, _ = _generate_boolean_term(disease, 'OR')
            list_of_tuples = await asyncio.to_thread(_perform_disease_doctor_search, disease_search_term_or)
        
        doctors = []
        if list_of_tuples:
            try:
                for row in list_of_tuples:
                    doctor = {
                        "doctor_id": row.doctor_id, "hospital": row.shortname, "address": row.address,
                        "lat": row.lat, "lon": row.lon, "telephone": row.telephone, "hospital_site" :  row.hospital_site, "hospital_hid" : row.hospital_hid,
                        "name": row.doctorname, "deptname": row.deptname, "specialties": row.specialties,
                        "url": row.doctor_url, "education": row.education, "career": row.career, "photo": row.profileimgurl,
                        "doctor_score": {"paper_score": row.paper_score or 0.0, "patient_score": row.patient_score or 0.0, "public_score": row.public_score or 0.0, "peer_score": 0.0},
                        "ai_score": {"kindness": (row.kindness or 0.0) * 5.0, "satisfaction": (row.satisfaction or 0.0) * 5.0, "explanation": (row.explanation or 0.0) * 5.0, "recommendation": (row.recommendation or 0.0) * 5.0},
                        "paper": [], "review": []
                    }
                    doctors.append(doctor)
            except Exception as e:
                logger.error(f"Error parsing templated SQL result for doctor search: {e} - Result string: {list_of_tuples}")
                doctors = []
        
        logger.info(f"search_doctors_by_disease_and_location 툴 실행 성공. {len(doctors)}명의 의사 정보 반환.")
        return {
            "chat_type": "search_doctor",
            "answer": {
                "doctors": doctors,
                "proposal": proposal
            }
        }

    except Exception as e:
        logger.error(f"Error in search_doctors_by_disease_and_location: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"의사 검색 중 오류가 발생했습니다: {str(e)}"}

@tool
@handle_proximity_search
async def search_hospital_by_disease_and_location(disease: Union[str, List[str]], location: Optional[str] = None, latitude: Optional[float] = None, longitude: Optional[float] = None, is_location_near: bool = False, coords_for_distance: Optional[dict] = None, limit: Optional[int] = None) -> dict:
    """지역과 질환을 기반으로 병원을 검색하는 도구.
    
    Args:
        disease: 필수 - 찾을 질환명. 단일 질환(str) 또는 여러 질환(list[str])을 모두 처리 가능.
        location: 선택 - 병원을 찾을 지역명 (예: "서울", "경남", "부울경").
        latitude: 선택 - 사용자의 현재 위도. 제공되면 병원을 가까운 순으로 정렬.
        longitude: 선택 - 사용자의 현재 경도. 제공되면 병원을 가까운 순으로 정렬.
        is_location_near: 선택 - '근처' 검색 여부를 나타내는 플래그. True이면 주소 텍스트 매칭을 비활성화.
        coords_for_distance: (내부용) 데코레이터가 계산한 좌표.
        limit: 선택 - 반환할 결과의 최대 수.
    """
    logger.info(f"tool: search_hospital_by_disease_and_location 시작 - location: {location}, disease: {disease}, latitude: {latitude}, longitude: {longitude}, limit: {limit}")

    final_limit = _get_final_limit(limit)

    # 1차 검색어 생성 (AND 방식)
    disease_search_term, has_space = _generate_boolean_term(disease, 'AND')

    if not disease_search_term:
        return {"chat_type": "recommand_hospital", "answer": {"hospitals": []}}

    def _perform_disease_hospital_search(search_term):
        disease_where_clause = f"AND MATCH(db.parse_specialties) AGAINST('{search_term}' IN BOOLEAN MODE)"
        
        if coords_for_distance:
            distance_km = settings.distance_square_meter
            lat, lon = coords_for_distance['lat'], coords_for_distance['lon']
            lat_range, lon_range = distance_km / 111.0, distance_km / 88.0
            
            query = f"""
                SELECT
                    h.shortName as name, h.address, h.telephone, h.hospital_site, h.lat, h.lon, h.hid as hospital_id,
                    ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({lon}, {lat})) as distance
                FROM (
                    SELECT DISTINCT hid, lat, lon, shortName, address, telephone ,hospital_site
                    FROM hospital
                    WHERE lat BETWEEN {lat - lat_range} AND {lat + lat_range}
                      AND lon BETWEEN {lon - lon_range} AND {lon + lon_range}
                ) h
                JOIN doctor_basic db ON h.hid = db.hid
                WHERE db.is_active in (1,2) {disease_where_clause}
                GROUP BY h.hid
                ORDER BY distance
                LIMIT {final_limit};
            """
        else:
            location_where_clause = _build_location_where_clause(location, latitude, longitude, is_location_near)
            query = f"""
                SELECT
                    h.shortName as name, h.address, h.telephone, h.hospital_site, h.lat, h.lon, h.hid as hospital_id
                FROM
                    doctor_basic db
                    INNER JOIN hospital h ON db.hid = h.hid AND 1=1 {location_where_clause}
                WHERE
                    db.is_active in (1,2)
                    {disease_where_clause}
                GROUP BY h.hid
                LIMIT {final_limit};
            """
        
        logger.info(f"Executing SQL Query: {query}")
        with sql_db._engine.connect() as connection:
            return connection.execute(text(query)).fetchall()

    try:
        # 1단계: AND 검색 수행
        list_of_tuples = await asyncio.to_thread(_perform_disease_hospital_search, disease_search_term)
        
        # 결과가 없고 공백이 포함된 경우 2단계: OR 검색 수행
        if not list_of_tuples and has_space:
            logger.info("No results with AND search. Attempting Fallback with OR search.")
            disease_search_term_or, _ = _generate_boolean_term(disease, 'OR')
            list_of_tuples = await asyncio.to_thread(_perform_disease_hospital_search, disease_search_term_or)
        
        hospitals = []
        if list_of_tuples:
            try:
                hospitals = [
                    {"name": row.name, "address": row.address, "telephone": row.telephone, "hospital_site" : row.hospital_site, "lat": row.lat, "lon": row.lon, "hospital_id": row.hospital_id}
                    for row in list_of_tuples
                ]
            except Exception as e:
                logger.error(f"Error parsing templated SQL result: {e} - Result string: {list_of_tuples}")
                hospitals = []
        
        logger.info(f"search_hospital_by_disease_and_location 툴 실행 성공. {len(hospitals)}개 병원 정보 반환.")
        return {
            "chat_type": "recommand_hospital",
            "answer": {"hospitals": hospitals}
        }

    except Exception as e:
        logger.error(f"Error in search_hospital_by_disease_and_location: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"병원 검색 중 오류가 발생했습니다: {str(e)}"}

@tool
async def search_hospital_by_disease(disease: Union[str, List[str]], limit: Optional[int] = None) -> dict:
    """질환을 기반으로 병원을 검색하는 도구.
    
    Args:
        disease: 필수 - 찾을 질환명. 단일 질환(str) 또는 여러 질환(list[str])을 모두 처리 가능.
        limit: 선택 - 반환할 결과의 최대 수.
    """
    logger.info(f"tool: search_hospital_by_disease 시작 - disease: {disease}, limit: {limit}")

    final_limit = _get_final_limit(limit)

    # 1차 검색어 생성 (AND 방식)
    disease_search_term, has_space = _generate_boolean_term(disease, 'AND')

    if not disease_search_term:
        return {"chat_type": "recommand_hospital", "answer": {"hospitals": []}}

    def _perform_simple_disease_hospital_search(search_term):
        disease_where_clause = f"AND MATCH(db.parse_specialties) AGAINST('{search_term}' IN BOOLEAN MODE)"
        query = f"""
            SELECT
                h.shortName as name, h.address, h.telephone, h.hospital_site,h.lat, h.lon, h.hid as hospital_id
            FROM
                doctor_basic db
                INNER JOIN hospital h ON db.hid = h.hid
            WHERE
                db.is_active in (1,2)
                {disease_where_clause}
            GROUP BY h.hid
            LIMIT {final_limit};
        """
        logger.info(f"Executing SQL Query: {query}")
        with sql_db._engine.connect() as connection:
            return connection.execute(text(query)).fetchall()

    try:
        # 1단계: AND 검색 수행
        list_of_tuples = await asyncio.to_thread(_perform_simple_disease_hospital_search, disease_search_term)
        
        # 결과가 없고 공백이 포함된 경우 2단계: OR 검색 수행
        if not list_of_tuples and has_space:
            logger.info("No results with AND search. Attempting Fallback with OR search.")
            disease_search_term_or, _ = _generate_boolean_term(disease, 'OR')
            list_of_tuples = await asyncio.to_thread(_perform_simple_disease_hospital_search, disease_search_term_or)
        
        hospitals = []
        if list_of_tuples:
            try:
                hospitals = [
                    {"name": row.name, "address": row.address, "telephone": row.telephone,"hospital_site" : row.hospital_site, "lat": row.lat, "lon": row.lon, "hospital_id": row.hospital_id}
                    for row in list_of_tuples
                ]
            except Exception as e:
                logger.error(f"Error parsing templated SQL result: {e} - Result string: {list_of_tuples}")
                hospitals = []
        
        logger.info(f"search_hospital_by_disease 툴 실행 성공. {len(hospitals)}개 병원 정보 반환.")
        return {
            "chat_type": "recommand_hospital",
            "answer": {"hospitals": hospitals}
        }

    except Exception as e:
        logger.error(f"Error in search_hospital_by_disease: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"병원 검색 중 오류가 발생했습니다: {str(e)}"}

@tool
@handle_proximity_search
async def search_hospital_by_disease_and_department(disease: Union[str, List[str]], department: Union[str, List[str]], location: Optional[str] = None, latitude: Optional[float] = None, longitude: Optional[float] = None, is_location_near: bool = False, coords_for_distance: Optional[dict] = None, limit: Optional[int] = None) -> dict:
    """질환과 진료과목을 기반으로 병원을 검색하는 도구.
    
    Args:
        disease: 필수 - 찾을 질환명. 단일 질환(str) 또는 여러 질환(list[str])을 모두 처리 가능.
        department: 필수 - 찾을 진료과목. 단일 진료과(str) 또는 여러 진료과(list[str])를 모두 처리 가능.
        location: 선택 - 병원을 찾을 지역명 (예: "서울", "경남", "부울경").
        latitude: 선택 - 사용자의 현재 위도. 제공되면 병원을 가까운 순으로 정렬.
        longitude: 선택 - 사용자의 현재 경도. 제공되면 병원을 가까운 순으로 정렬.
        is_location_near: 선택 - '근처' 검색 여부를 나타내는 플래그. True이면 주소 텍스트 매칭을 비활성화.
        coords_for_distance: (내부용) 데코레이터가 계산한 좌표.
        limit: 선택 - 반환할 결과의 최대 수.
    """
    logger.info(f"tool: search_hospital_by_disease_and_department 시작 - disease: {disease}, department: {department}, location: {location}, latitude: {latitude}, longitude: {longitude}, is_location_near: {is_location_near}, limit: {limit}")

    final_limit = _get_final_limit(limit)

    # 입력 값 정규화 및 비교 로직
    l_disease = disease
    l_department = department

    disease_val = [d.strip() for d in (l_disease if isinstance(l_disease, list) else [l_disease]) if d and d.strip()]
    department_val = [d.strip() for d in (l_department if isinstance(l_department, list) else [l_department]) if d and d.strip()]

    is_same_input = set(disease_val) == set(department_val) and disease_val

    if is_same_input:
        search_term_raw = disease_val[0]
        if search_term_raw.endswith("과"):
            logger.info(f"질환/진료과 동일 값 '{search_term_raw}' 감지. '과'로 끝나므로 진료과 단독 검색으로 처리합니다.")
            l_disease = []  # 질환 값을 비움
        else:
            logger.info(f"질환/진료과 동일 값 '{search_term_raw}' 감지. '과'로 끝나지 않으므로 질환 단독 검색으로 처리합니다.")
            l_department = []  # 진료과 값을 비움

    # potentially nullified 값으로 검색어 재생성
    # 1차 검색어 생성 (AND 방식)
    disease_search_term, d_has_space = _generate_boolean_term(l_disease, 'AND')
    department_search_term, dept_has_space = _generate_boolean_term(l_department, 'AND')
    has_space = d_has_space or dept_has_space

    def _perform_combined_hospital_search(d_term, dept_term):
        clauses = []
        if d_term:
            clauses.append(f"MATCH(db.parse_specialties) AGAINST('{d_term}' IN BOOLEAN MODE)")
        if dept_term:
            clauses.append(f"MATCH(db.deptname) AGAINST('{dept_term}' IN BOOLEAN MODE)")

        if not clauses:
            return []
        
        # 원래 입력이 동일했다면 OR, 달랐다면 AND로 조합
        join_operator = ' OR ' if is_same_input else ' AND '
        combined_where_clause = f"AND ({join_operator.join(clauses)})"
        
        if coords_for_distance:
            distance_km = settings.distance_square_meter
            lat, lon = coords_for_distance['lat'], coords_for_distance['lon']
            lat_range, lon_range = distance_km / 111.0, distance_km / 88.0
            
            query = f"""
                SELECT 
                    h.shortName as name, h.address, h.telephone,h.hospital_site,h.lat, h.lon, h.hid as hospital_id,
                    ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({lon}, {lat})) as distance
                FROM (
                    SELECT DISTINCT hid, lat, lon, shortName, address, telephone, hospital_site 
                    FROM hospital
                    WHERE lat BETWEEN {lat - lat_range} AND {lat + lat_range}
                      AND lon BETWEEN {lon - lon_range} AND {lon + lon_range}
                ) h
                JOIN doctor_basic db ON h.hid = db.hid
                WHERE db.is_active in (1,2) {combined_where_clause}
                GROUP BY h.hid
                ORDER BY distance
                LIMIT {final_limit};
            """
        else:
            location_where_clause = _build_location_where_clause(location, latitude, longitude, is_location_near)
            query = f"""
                SELECT 
                    h.shortName as name, h.address, h.telephone, h.hospital_site, h.lat, h.lon, h.hid as hospital_id
                FROM
                    doctor_basic db
                    INNER JOIN hospital h ON db.hid = h.hid AND 1=1 {location_where_clause}
                WHERE
                    db.is_active in (1,2)
                    {combined_where_clause}
                GROUP BY h.hid
                LIMIT {final_limit};
            """
        
        logger.info(f"Executing SQL Query: {query}")
        with sql_db._engine.connect() as connection:
            return connection.execute(text(query)).fetchall()

    try:
        # 1단계: AND 검색 수행
        list_of_tuples = await asyncio.to_thread(_perform_combined_hospital_search, disease_search_term, department_search_term)
        
        # 결과가 없고 공백이 포함된 경우 2단계: OR 검색 수행
        if not list_of_tuples and has_space:
            logger.info("No results with AND search. Attempting Fallback with OR search.")
            disease_search_term_or, _ = _generate_boolean_term(l_disease, 'OR')
            department_search_term_or, _ = _generate_boolean_term(l_department, 'OR')
            list_of_tuples = await asyncio.to_thread(_perform_combined_hospital_search, disease_search_term_or, department_search_term_or)
        
        hospitals = []
        if list_of_tuples:
            try:
                hospitals = [
                    {"name": row.name, "address": row.address, "telephone": row.telephone, "hospital_site" : row.hospital_site,  "lat": row.lat, "lon": row.lon, "hospital_id": row.hospital_id}
                    for row in list_of_tuples
                ]
            except Exception as e:
                logger.error(f"Error parsing templated SQL result: {e} - Result string: {list_of_tuples}")
                hospitals = []
        
        logger.info(f"search_hospital_by_disease_and_department 툴 실행 성공. {len(hospitals)}개 병원 정보 반환.")
        return {
            "chat_type": "recommand_hospital",
            "answer": {"hospitals": hospitals}
        }

    except Exception as e:
        logger.error(f"Error in search_hospital_by_disease_and_department: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"병원 검색 중 오류가 발생했습니다: {str(e)}"}


@tool
async def search_doctors_by_hospital_name(hospital_name: Union[str, List[str]], limit: Optional[int] = None, proposal: str = "") -> dict:
    """오직 병원명만을 기반으로 해당 병원의 의사 목록을 검색하는 도구.
    
    Args:
        hospital_name: 필수 - 의사를 찾을 병원 이름. 단일 또는 여러 병원 이름(list[str]) 처리 가능.
        limit: 선택 - 반환할 의사의 최대 수 (기본값: 10).
        proposal: 선택 - LLM이 제안하는 핵심 키워드나 제안.
    """
    final_limit = _get_final_limit(limit)
    logger.info(f"tool: search_doctors_by_hospital_name 시작 - hospital_name: {hospital_name}, limit: {final_limit}, proposal: {proposal}")

    params = {"limit": final_limit}
    name_where_clause = ""
    if isinstance(hospital_name, list) and hospital_name:
        name_clauses = []
        for i, n in enumerate(hospital_name):
            if n and n.strip():
                param_key = f"hname_{i}"
                name_clauses.append(f"ha.alias_name LIKE :{param_key}")
                params[param_key] = f"%%{n.strip()}%%"
        if name_clauses:
            name_where_clause = f"AND ({' OR '.join(name_clauses)})"
    elif isinstance(hospital_name, str) and hospital_name.strip():
        params["hospital_name"] = f"%%{hospital_name.strip()}%%"
        name_where_clause = "AND ha.alias_name LIKE :hospital_name"

    if not name_where_clause:
        return {"chat_type": "search_doctor", "answer": {"doctors": []}}
    
    template_query = f"""
        SELECT
            d.doctor_id, HEX(d.rid) as hexrid, h.shortname, h.address, h.lat, h.lon, h.telephone,h.hospital_site,h.hid as hospital_hid,
            db.doctorname, db.deptname, db.specialties, db.doctor_url,
            dc.education, dc.career, db.profileimgurl,
            de.paper_score, de.patient_score, de.public_score,
            de.kindness, de.satisfaction, de.explanation, de.recommendation
        FROM
            hospital_alias ha 
            INNER JOIN hospital h ON ha.hid = h.hid
            INNER JOIN doctor_basic db ON h.hid = db.hid
            LEFT JOIN doctor d ON db.rid = d.rid
            LEFT JOIN doctor_career dc ON d.rid = dc.rid
            LEFT JOIN (
                SELECT
                    doctor_id, AVG(paper_score) AS paper_score, AVG(patient_score) AS patient_score,
                    AVG(public_score) AS public_score, AVG(kindness) AS kindness, AVG(satisfaction) AS satisfaction,
                    AVG(explanation) AS explanation, AVG(recommendation) AS recommendation
                FROM aiga2025.doctor_evaluation GROUP BY doctor_id
            ) de ON d.doctor_id = de.doctor_id
        WHERE
            db.is_active IN (1,2)
            {name_where_clause}
        ORDER BY
            db.doctorname
        LIMIT :limit;
    """
    logger.info(f"Generated SQL Query for search_doctors_by_hospital_name: {template_query}")

    try:
        def _execute_query():
            with sql_db._engine.connect() as connection:
                return connection.execute(text(template_query), params).fetchall()

        list_of_tuples = await asyncio.to_thread(_execute_query)
        
        doctors = []
        if list_of_tuples:
            try:
                for row in list_of_tuples:
                    doctor = {
                        "doctor_id": row.doctor_id, "doctor_rid": row.hexrid, "hospital": row.shortname, "address": row.address,
                        "lat": row.lat, "lon": row.lon, "telephone": row.telephone, "hospital_site" : row.hospital_site, "hospital_hid" : row.hospital_hid,
                        "name": row.doctorname,"deptname": row.deptname, "specialties": row.specialties, "url": row.doctor_url,
                        "education": row.education, "career": row.career, "photo": row.profileimgurl,
                        "doctor_score": {"paper_score": row.paper_score or 0.0, "patient_score": row.patient_score or 0.0, "public_score": row.public_score or 0.0, "peer_score": 0.0},
                        "ai_score": {"kindness": (row.kindness or 0.0) * 5.0, "satisfaction": (row.satisfaction or 0.0) * 5.0, "explanation": (row.explanation or 0.0) * 5.0, "recommendation": (row.recommendation or 0.0) * 5.0},
                        "paper": [], "review": []
                    }
                    doctors.append(doctor)
            except Exception as e:
                logger.error(f"Error parsing templated SQL result for search_doctors_by_hospital_name: {e} - Result string: {list_of_tuples}")
                doctors = []
        
        logger.info(f"search_doctors_by_hospital_name 툴 실행 성공. {len(doctors)}명의 의사 정보 반환.")
        return {
            "chat_type": "search_doctor",
            "answer": {
                "doctors": doctors,
                "hospital": hospital_name if isinstance(hospital_name, str) else ', '.join(hospital_name),
                "proposal": proposal
            }
        }

    except Exception as e:
        logger.error(f"Error in search_doctors_by_hospital_name: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"병원명 기반 의사 검색 중 오류가 발생했습니다: {str(e)}"}

@tool
@handle_proximity_search
async def search_by_location_only(location: str, target: str, is_location_near: bool = False, latitude: Optional[float] = None, longitude: Optional[float] = None, coords_for_distance: Optional[dict] = None, limit: Optional[int] = None, proposal: str = "") -> dict:
    """
    오직 지역 정보만을 기반으로 의사 또는 병원을 검색하는 도구.
    '근처' 검색이 활성화되면, 명시된 지역 근처에서 검색합니다.

    Args:
        location: 필수 - 검색할 지역명 (예: "서울", "경남", "부울경").
        target: 필수 - 검색 대상. '의사' 또는 '병원'.
        is_location_near: 선택 - '근처' 검색 여부를 나타내는 플래그. True이면 거리순으로 정렬.
        latitude: 선택 - 사용자의 현재 위도. '근처' 검색 시 사용될 수 있음.
        longitude: 선택 - 사용자의 현재 경도. '근처' 검색 시 사용될 수 있음.
        coords_for_distance: (내부용) 데코레이터가 계산한 좌표.
        limit: 선택 - 반환할 결과의 최대 수.
        proposal: 선택 - LLM이 제안하는 핵심 키워드나 제안.
    """
    logger.info(f"tool: search_by_location_only 시작 - location: {location}, target: {target}, is_location_near: {is_location_near}, latitude: {latitude}, longitude: {longitude}, limit: {limit}, proposal: {proposal}")

    final_limit = _get_final_limit(limit)

    if target not in ['의사', '병원']:
        return {"chat_type": "error", "message": "잘못된 검색 대상입니다. '의사' 또는 '병원' 중에서 선택해야 합니다."}
    
    if target == '병원':
        template_query = ""
        if coords_for_distance:
            distance_km = settings.distance_square_meter
            lat, lon = coords_for_distance['lat'], coords_for_distance['lon']
            lat_range, lon_range = distance_km / 111.0, distance_km / 88.0
            
            template_query = f"""
                SELECT 
                    h.shortName as name, h.address, h.telephone, h.hospital_site, h.lat, h.lon, h.hid as hospital_id,
                    ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({lon}, {lat})) as distance
                FROM hospital h
                WHERE h.lat BETWEEN {lat - lat_range} AND {lat + lat_range}
                  AND h.lon BETWEEN {lon - lon_range} AND {lon + lon_range}
                ORDER BY distance
                LIMIT {final_limit};
            """
        else:
            location_where_clause = _build_location_where_clause(location, latitude, longitude, is_location_near)
            if not location_where_clause:
                 return {"chat_type": "error", "message": "지역 정보를 찾을 수 없습니다."}
            template_query = f"""
                SELECT 
                    h.shortName as name, h.address, h.telephone, h.hospital_site, h.lat, h.lon, h.hid as hospital_id
                FROM hospital h
                WHERE 1=1 {location_where_clause}
                LIMIT {final_limit};
            """
        
        logger.info(f"Generated SQL Query for hospitals (location only): {template_query}")
        try:
            def _execute_query():
                with sql_db._engine.connect() as connection:
                    return connection.execute(text(template_query)).fetchall()

            list_of_tuples = await asyncio.to_thread(_execute_query)
            
            hospitals = []
            if list_of_tuples:
                hospitals = [
                    {"name": row.name, "address": row.address, "telephone": row.telephone, "hospital_site" : row.hospital_site, "lat": row.lat, "lon": row.lon, "hospital_id": row.hospital_id}
                    for row in list_of_tuples
                ]
            
            logger.info(f"search_by_location_only (병원) 툴 실행 성공. {len(hospitals)}개 병원 정보 반환.")
            return {
                "chat_type": "recommand_hospital",
                "answer": {"hospitals": hospitals}
            }        
        except Exception as e:
            logger.error(f"Error in search_by_location_only (병원): {e}", exc_info=True)
            return {"chat_type": "error", "message": f"지역 기반 병원 검색 중 오류가 발생했습니다: {str(e)}"}
    
    elif target == '의사':
        score_weight = float(os.getenv("SCORE_WEIGHT", 0.3))
        total_score_select = f""", (
            IFNULL(de.patient_score, 0) * {score_weight} + 
            IFNULL(de.public_score, 0) * {score_weight}
        ) as total_score"""
        
        distance_select = ""
        order_by_clause = ""
        from_clause = ""
        params = {}

        if coords_for_distance:
            distance_km = settings.distance_square_meter
            lat, lon = coords_for_distance['lat'], coords_for_distance['lon']
            lat_range, lon_range = distance_km / 111.0, distance_km / 88.0

            from_clause = f"""
            FROM (
                SELECT DISTINCT hid, lat, lon, shortName, address, telephone , hospital_site
                FROM hospital
                WHERE lat BETWEEN {lat - lat_range} AND {lat + lat_range}
                  AND lon BETWEEN {lon - lon_range} AND {lon + lon_range}
            ) h
            INNER JOIN doctor_basic db ON db.hid = h.hid
            """
            distance_select = f", ST_DISTANCE_SPHERE(POINT(h.lon, h.lat), POINT({lon}, {lat})) as distance"
            order_by_clause = "ORDER BY distance ASC, total_score DESC"
        else:
            location_where_clause = _build_location_where_clause(location, latitude, longitude, is_location_near)
            if not location_where_clause:
                 return {"chat_type": "error", "message": "지역 정보를 찾을 수 없습니다."}
            from_clause = f"FROM doctor_basic db INNER JOIN hospital h ON db.hid = h.hid AND 1=1 {location_where_clause}"
            order_by_clause = "ORDER BY total_score DESC"

        template_query = f"""
            SELECT
                d.doctor_id, h.shortname, h.address, h.lat, h.lon, h.telephone, h.hospital_site, h.hid as hospital_hid,
                db.doctorname, db.deptname, db.specialties, db.doctor_url,
                dc.education, dc.career, db.profileimgurl,
                de.paper_score, de.patient_score, de.public_score, de.kindness,
                de.satisfaction, de.explanation, de.recommendation
                {distance_select}
                {total_score_select}
            {from_clause}
            LEFT JOIN doctor d ON db.rid = d.rid
            LEFT JOIN doctor_career dc ON d.rid = dc.rid
            LEFT JOIN (
                SELECT doctor_id, AVG(paper_score) AS paper_score, AVG(patient_score) AS patient_score,
                       AVG(public_score) AS public_score, AVG(kindness) AS kindness, AVG(satisfaction) AS satisfaction,
                       AVG(explanation) AS explanation, AVG(recommendation) AS recommendation
                FROM aiga2025.doctor_evaluation GROUP BY doctor_id
            ) de ON d.doctor_id = de.doctor_id
            WHERE db.is_active in (1,2)
            {order_by_clause}
            LIMIT {final_limit};
        """
        logger.info(f"Generated SQL Query for doctors (location only): {template_query}")
        
        try:
            def _execute_query():
                with sql_db._engine.connect() as connection:
                    return connection.execute(text(template_query), params).fetchall()

            list_of_tuples = await asyncio.to_thread(_execute_query)
            
            doctors = []
            if list_of_tuples:
                doctors = [
                    {
                        "doctor_id": row.doctor_id, "hospital": row.shortname, "address": row.address,
                        "lat": row.lat, "lon": row.lon, "telephone": row.telephone,"hospital_site":row.hospital_site,"hospital_hid":row.hospital_hid,
                        "name": row.doctorname, "deptname": row.deptname, "specialties": row.specialties,
                        "url": row.doctor_url, "education": row.education, "career": row.career, "photo": row.profileimgurl,
                        "doctor_score": {"paper_score": row.paper_score or 0.0, "patient_score": row.patient_score or 0.0, "public_score": row.public_score or 0.0, "peer_score": 0.0},
                        "ai_score": {"kindness": (row.kindness or 0.0) * 5.0, "satisfaction": (row.satisfaction or 0.0) * 5.0, "explanation": (row.explanation or 0.0) * 5.0, "recommendation": (row.recommendation or 0.0) * 5.0},
                        "paper": [], "review": []
                    }
                    for row in list_of_tuples
                ]
            
            logger.info(f"search_by_location_only (의사) 툴 실행 성공. {len(doctors)}명의 의사 정보 반환.")
            return {
                "chat_type": "search_doctor",
                "answer": {"doctors": doctors, "proposal": proposal}
            }

        except Exception as e:
            logger.error(f"Error in search_by_location_only (의사): {e}", exc_info=True)
            return {"chat_type": "error", "message": f"지역 기반 의사 검색 중 오류가 발생했습니다: {str(e)}"}

@tool
async def search_doctors_by_department_only(department: Union[str, List[str]], limit: Optional[int] = None, proposal: str = "") -> dict:
    """
    진료과목만을 기반으로 의사를 검색하는 도구.
    
    Args:
        department: 필수 - 찾을 진료과목. 단일 진료과(str) 또는 여러 진료과(list[str])를 모두 처리 가능.
        limit: 선택 - 반환할 결과의 최대 수.
        proposal: 선택 - LLM이 제안하는 핵심 키워드나 제안.
    """
    logger.info(f"tool: search_doctors_by_department_only 시작 - department: {department}, limit: {limit}, proposal: {proposal}")
    
    final_limit = _get_final_limit(limit)

    try:
        doctors_data = await asyncio.to_thread(getSearchDoctorsByOnlyDepartment, department, final_limit)
        
        doctors = []
        if doctors_data:
            for row in doctors_data:
                doctor = {
                    "doctor_id": row.get("doctor_id"),
                    "hospital": row.get("shortname"),
                    "hospital_hid" : row.get("hid"),
                    "address": row.get("address"),
                    "lat": row.get("lat"),
                    "lon": row.get("lon"),
                    "telephone": row.get("telephone"),
                    "hospital_site" : row.get("hospital_site"),
                    "name": row.get("doctorname"),
                    "deptname": row.get("deptname"),
                    "specialties": row.get("specialties"),
                    "url": row.get("doctor_url"),
                    "education": row.get("education"),
                    "career": row.get("career"),
                    "photo": row.get("profileimgurl"),
                    "doctor_score": {
                        "paper_score": row.get("paper_score", 0.0),
                        "patient_score": row.get("patient_score", 0.0),
                        "public_score": row.get("public_score", 0.0),
                        "peer_score": row.get("peer_score", 0.0)
                    },
                    "ai_score": {
                        "kindness": row.get("kindness", 0.0) * 5.0,
                        "satisfaction": row.get("satisfaction", 0.0) * 5.0,
                        "explanation": row.get("explanation", 0.0) * 5.0,
                        "recommendation": row.get("recommendation", 0.0) * 5.0
                    },
                    "paper": [], "review": []
                }
                doctors.append(doctor)
        
        logger.info(f"search_doctors_by_department_only 툴 실행 성공. {len(doctors)}명의 의사 정보 반환.")
        return {
            "chat_type": "search_doctor",
            "answer": {"doctors": doctors, "proposal": proposal}
        }
    except Exception as e:
        logger.error(f"Error in search_doctors_by_department_only: {e}", exc_info=True)
        return {"chat_type": "error", "message": f"진료과목 기반 의사 검색 중 오류가 발생했습니다: {str(e)}"}

class SearchDoctorsByDiseaseAndDepartmentInput(BaseModel):
    disease: Union[str, List[str]] = Field(description="The name of the disease to search for.")
    department: str = Field(description="The name of the department to search for.")
    limit: Optional[int] = Field(default=None, description="The maximum number of doctors to return.")
    proposal: Optional[str] = Field(default=None, description="The original search query from the user, used for AI-based evaluation.")

@tool("search_doctors_by_disease_and_department", args_schema=SearchDoctorsByDiseaseAndDepartmentInput)
async def search_doctors_by_disease_and_department(disease: Union[str, List[str]], department: str, limit: Optional[int] = None, proposal: str = "") -> dict:
    """
    질환과 진료과를 기반으로 의사를 검색하는 도구.

    Args:
        disease: 필수 - 찾을 질환명. 단일 질환(str) 또는 여러 질환(list[str])을 모두 처리 가능.
        department: 필수 - 찾을 진료과명.
        limit: 선택 - 반환할 결과의 최대 수.
        proposal: 선택 - LLM이 제안하는 핵심 키워드나 제안.
    """
    logger.info(f"tool: search_doctors_by_disease_and_department 시작 - disease: {disease}, department: {department}, limit: {limit}, proposal: {proposal}")

    # 입력 값 정규화 및 비교
    disease_val = [d.strip() for d in (disease if isinstance(disease, list) else [disease]) if d and d.strip()]
    department_val = [d.strip() for d in ([department] if isinstance(department, str) else department) if d and d.strip()]

    final_limit = _get_final_limit(limit)

    if set(disease_val) == set(department_val) and disease_val:
        search_term = disease_val[0]
        if search_term.endswith("과"):
            logger.info(f"질환/진료과 동일 값 '{search_term}'을 감지했습니다. '과'로 끝나므로 진료과 단독 검색으로 전환합니다.")
            return await search_doctors_by_department_only.ainvoke({"department": department, "limit": limit, "proposal": proposal})
        else:
            logger.info(f"질환/진료과 동일 값 '{search_term}'을 감지했습니다. '과'로 끝나지 않으므로 질환 단독 검색(getRecommandDoctors)으로 전환합니다.")
            disease_list = disease if isinstance(disease, list) else [disease]
            standard_diseases = []
            if disease_list:
                for d in disease_list:
                    standard_diseases.extend(getStandardSpecialty(d))
            
            try:
                doctors_data = await asyncio.to_thread(getRecommandDoctors, standard_diseases, disease_list)
            except Exception as e:
                logger.error(f"Error calling getRecommandDoctors: {e}", exc_info=True)
                return {"chat_type": "error", "message": "질환 기반 의사 추천 중 오류가 발생했습니다."}
            # 이 아래부터는 결과 포맷팅 로직입니다.
    else:
        # 원래 로직
        disease_list = disease if isinstance(disease, list) else [disease]
        standard_diseases = []
        if disease_list:
            for d in disease_list:
                standard_diseases.extend(getStandardSpecialty(d))
        
        try:
            doctors_data = await asyncio.to_thread(getRecommandDoctorWithDiseaseAndDepartment, standard_diseases, disease_list, department, limit=final_limit)
        except Exception as e:
            logger.error(f"Error calling getRecommandDoctorWithDiseaseAndDepartment: {e}", exc_info=True)
            return {"chat_type": "error", "message": "질환/진료과 기반 의사 검색 중 오류가 발생했습니다."}
    
    # 공통 결과 처리 로직
    try:
        doctors = []
        if doctors_data and doctors_data.get("data"):
            for row in doctors_data["data"]:
                doctor = {
                    "doctor_id": row.get("doctor_id"),
                    "hospital": row.get("shortname"),
                    "hospital_hid": row.get("hid"),
                    "address": row.get("address"),
                    "lat": row.get("lat"),
                    "lon": row.get("lon"),
                    "telephone": row.get("telephone"),
                    "hospital_site": row.get("hospital_site"),
                    "name": row.get("doctorname"),
                    "deptname": row.get("deptname"),
                    "specialties": row.get("specialties"),
                    "url": row.get("doctor_url"),
                    "education": row.get("education"),
                    "career": row.get("career"),
                    "photo": row.get("profileimgurl"),
                    "doctor_score": {
                        "paper_score": row.get("paper_score", 0.0),
                        "patient_score": row.get("patient_score", 0.0),
                        "public_score": row.get("public_score", 0.0),
                        "peer_score": row.get("peer_score", 0.0)
                    },
                    "ai_score": {
                        "kindness": row.get("kindness", 0.0) * 5.0,
                        "satisfaction": row.get("satisfaction", 0.0) * 5.0,
                        "explanation": row.get("explanation", 0.0) * 5.0,
                        "recommendation": row.get("recommendation", 0.0) * 5.0
                    },
                    "paper": [], "review": []
                }
                doctors.append(doctor)

        logger.info(f"search_doctors_by_disease_and_department 툴 실행 성공. {len(doctors)}명의 의사 정보 반환.")
        return {
            "chat_type": "search_doctor",
            "answer": {"doctors": doctors, "proposal": proposal}
        }
    except Exception as e:
        logger.error(f"Error in search_doctors_by_disease_and_department (result processing): {e}", exc_info=True)
        return {"chat_type": "error", "message": f"의사 검색 결과 처리 중 오류가 발생했습니다: {str(e)}"}
