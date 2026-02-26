import os
from .db import fetchData
from ..common.logger import logger
from app.common.common import calculate_similarity, haversine_distance
from typing import Union, List

def findHospitals(department: Union[str, List[str]], count:int):
  base_query = """
    SELECT
      a.hid as hospital_id, s.shortname as hospital_short_name, s.address, s.lat, s.lon, s.telephone,s.hospital_site, s.hid
    FROM
      (
        SELECT
         hid
        FROM
          hospital_evaluation
        WHERE {dept_where_clause}
        GROUP BY hid
        ORDER BY max(public_score) desc LIMIT :limitCount
      ) a LEFT JOIN hospital s on a.hid = s.hid
  """
  param = {"limitCount": count}

  dept_list = department if isinstance(department, list) else [department]

  dept_clauses = []
  if dept_list:
      for i, d_name in enumerate(dept_list):
          key = f"dept_{i}"
          dept_clauses.append(f"matched_dept LIKE :{key}")
          param[key] = f"%{d_name}%"
      dept_where_clause = " OR ".join(dept_clauses)
  else:
      # 진료과 리스트가 비어있을 경우, 쿼리가 실패하지 않도록 항상 false인 조건을 추가
      dept_where_clause = "1=0"

  query = base_query.format(dept_where_clause=dept_where_clause)

  logger.debug(f"Executing findHospitals query: {query} with params: {param}")
  result = fetchData(query, param)["data"]
  return result

def getRecommandHospitals(department: Union[str, List[str]], count: int, latitude: float = None, longitude: float = None, is_nearby: bool = False):
  result = findHospitals(department, count)

  hospitals = []
  if len(result) > 0: 
    logger.info(f"NOHLOGGER_BEFORE_RENAME: First row: {result[0] if result else 'EMPTY'}")
    for hospital_data in result:
        if 'hospital_short_name' in hospital_data:
            hospital_data['name'] = hospital_data.pop('hospital_short_name')
    hospitals = result
    logger.info(f"NOHLOGGER_AFTER_RENAME: First row: {hospitals[0] if hospitals else 'EMPTY'}")

    # is_nearby 플래그가 True이고 좌표가 있을 때만 거리순으로 정렬
    if is_nearby and latitude is not None and longitude is not None:
        logger.info("is_nearby is True, sorting by distance.")
        for hospital in hospitals:
            hospital_lat = hospital.get('lat')
            hospital_lon = hospital.get('lon')
            if hospital_lat is not None and hospital_lon is not None:
                distance = haversine_distance(latitude, longitude, hospital_lat, hospital_lon)
                hospital['distance'] = distance
            else:
                hospital['distance'] = float('inf')
        
        hospitals.sort(key=lambda x: x.get('distance', float('inf')))
    else:
        logger.info("is_nearby is False or coordinates are missing, maintaining evaluation-based order.")

  return hospitals