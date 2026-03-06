import asyncio
from .standard_desease_dic import STANDARD_DESEASE_DIC
from ..database.standardSpecialty import getStandardSpecialty as getStandardSpecialtyFromDB
from ..common.logger import logger

def getStandardDeseaseDictionary(disease: str):
    """표준 진료 분야 사전을 통해서 진료 분야를 구하는 함수"""

    # 1차: 유사어 딕셔너리 처리
    logger.debug("1차: 유사어 딕셔너리 처리")
    standard_disease = ''
    for key, values in STANDARD_DESEASE_DIC.items():
        if disease in values:
            standard_disease = key
            break
    if standard_disease:
        return standard_disease
    
async def getStandardSpecialty(disease: str):
    """표준 진료 분야를 구하는 함수"""

    # 1차: 유사어 딕셔너리 처리
    standardSpecialty = getStandardDeseaseDictionary(disease)
    if standardSpecialty:
        return standardSpecialty

    # 2차: 표준진료분야 선택: DB 조회
    standardSpecialty = await asyncio.to_thread(getStandardSpecialtyFromDB, disease)
    if standardSpecialty:
        return standardSpecialty

    return None
