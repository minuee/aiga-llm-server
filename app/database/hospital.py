from .db import fetchData

def getHospitalStandardName(hospital_name:str):
    """추천 의사의 논문 검색 함수"""
    
    query = """SELECT standard_name FROM hospital_alias where alias_name = :search_name LIMIT 1"""
    param = {"search_name": hospital_name}
    result = fetchData(query, param)
    return result

