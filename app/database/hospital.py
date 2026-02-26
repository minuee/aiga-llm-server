from .db import fetchData

def getHospitalStandardName(hospital_name:str):
    """병원 추천 검색"""
    
    query = """SELECT 
	h.shortname as standard_name 
FROM 
	aiga2025.hospital_alias h left join aiga2025.hospital_evaluation he ON h.hid = he.hid
where 
	h.alias_name like :search_name 
order by he.public_score desc
LIMIT 1"""
    param = {"search_name": f"%{hospital_name}%"}
    result = fetchData(query, param)
    return result

