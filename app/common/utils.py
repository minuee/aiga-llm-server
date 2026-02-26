# app/common/utils.py
from typing import Optional
from ..config import settings

def _get_final_limit(limit: Optional[int] = None) -> int:
    """사용자 입력과 시스템 설정에 따라 최종 limit 값을 계산합니다."""
    if limit is not None and limit > 0:
        # 전달된 limit 값을 사용하되, 최대 10으로 제한합니다.
        final_limit = min(limit, 10)
    else:
        # 기본값으로 settings의 limit_common 값을 사용합니다.
        final_limit = settings.limit_common
    return final_limit

def is_result_empty(tool_name: str, observation: dict) -> bool:
    """Check if the observation from a primary tool contains an empty list of doctors or hospitals."""
    #if tool_name not in [
    #    "recommand_doctor", "recommend_hospital", "search_doctor", "search_doctor_by_hospital",
    #    "search_hospitals_by_location_and_department",
    #    "search_hospital_by_disease_and_location",
    #    "search_hospital_by_disease_and_department",
    #    "search_doctors_by_location_and_department",
    #    "search_doctors_by_disease_and_location",
    #    "search_by_location_only",
    #    "search_hospital_by_disease",
    #]:
    #    return False
        
    try:
        answer = observation.get("answer")
        if not isinstance(answer, dict):
            return False

        if "doctors" in answer and not answer.get("doctors"):
            return True
        if "hospitals" in answer and not answer.get("hospitals"):
            return True
            
    except Exception:
        return False
        
    return False