import os
from .db import fetchData
from ..common.logger import logger
from app.common.common import calculate_similarity

def findHospitals(department: str):
  # 2차: 문자열 매칭해서 복수의 표준진료분야 제시해 유저가 선택
  query = """
      select a.hid as hospital_id, s.shortName as name, s.address, s.lat, s.lon, s.telephone from
      (select *, IFNULL(public_score, 0) AS total_score 
          from hospital_evaluation where matched_dept = :department
          order by total_score desc limit 15
      ) a
      left join hospital s 
      on a.hid = s.hid
  """
  param = {"department": department}
  logger.debug(f"fechData: hospital_evaluation")
  
  result = fetchData(query, param)["data"]
  return result

def getRecommandHospitals(department: str):

  # 2차: 문자열 매칭해서 복수의 표준진료분야 제시해 유저가 선택
  result = findHospitals(department)

  hospitals = []
  if len(result) > 0: 
    hospitals = result
  # else:
  #   # 3차: 문자열 매칭해서 복수의 표준진료분야 제시해 유저가 선택
  #   param = {"department": department}
  #   query = "select matched_dept from hospital_evaluation"
  #   result2 = fetchData(query, param)["data"]
  #   similarity_dept = None
  #   max_val = -1
  #   for hospital in result2:
  #     matched_dept = hospital[0]
  #     if matched_dept:
  #         val = calculate_similarity(department, matched_dept)
  #         if val > max_val:
  #             similarity_dept = matched_dept
  #             max_val = val
  #             logger.debug(f"similarity_dept:{similarity_dept}")    

  #   if similarity_dept:
  #     hospitals = findHospitals(similarity_dept)

  return hospitals