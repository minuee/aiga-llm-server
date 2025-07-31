from .db import fetchData
from ..common.logger import logger

def buildDoctorQuery(name: str, hospital: str, deptname: str) -> tuple:
    """의사 정보를 가져오기 위한 쿼리와 파라미터를 생성하는 함수"""
    
    if hospital:
        query = "select * from hospital where baseName = :hospital or shortName = :hospital"
        param = {"hospital": hospital}

        logger.debug(f"fechData: hospital")
        result = fetchData(query, param)
        hid = result["data"][0]['hid']
        
        logger.debug(f"result, hid:{hid}")

        if deptname:
            query = """select s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname, 
            a.specialties, a.doctor_url, a.profileimgurl,
            b.education, b.career, b.jsondata, a.rid,
            IFNULL(e.paper_score, 0) as paper_score, 
            IFNULL(e.patient_score, 0) as patient_score, 
            IFNULL(e.public_score, 0) as public_score, 
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness, 
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation, 
            IFNULL(e.recommendation, 0) as recommendation
            from 
                (select * from doctor_basic where doctor_id is not null and hid = :hid and deptname = :deptname and doctorname = :name) a
                left join doctor_career b
                on a.rid = b.rid 
                left join (
                    select doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,
                    avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
                    from doctor_evaluation group by doctor_id) e
                on a.doctor_id = e.doctor_id
                left join hospital s 
                on a.hid = s.hid"""
            param = {"hid": hid, "deptname": deptname, "name": name}
        else:
            query = """select s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname, 
            a.specialties, a.doctor_url, a.profileimgurl,
            b.education, b.career, b.jsondata, a.rid,
            IFNULL(e.paper_score, 0) as paper_score, 
            IFNULL(e.patient_score, 0) as patient_score, 
            IFNULL(e.public_score, 0) as public_score, 
            IFNULL(e.peer_score, 0) as peer_score,
            IFNULL(e.kindness, 0) as kindness, 
            IFNULL(e.satisfaction, 0) as satisfaction,
            IFNULL(e.explanation, 0) as explanation, 
            IFNULL(e.recommendation, 0) as recommendation
            from 
                (select * from doctor_basic where doctor_id is not null and hid = :hid and doctorname = :name) a
                left join doctor_career b
                on a.rid = b.rid 
                left join (
                    select doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,
                    avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
                    from doctor_evaluation group by doctor_id) e
                on a.doctor_id = e.doctor_id
                left join hospital s 
                on a.hid = s.hid"""
            param = {"hid": hid, "name": name}
       
    else:
        query = """select s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname,
        a.specialties, a.doctor_url, a.profileimgurl,
        b.education, b.career, b.jsondata, a.rid,
        IFNULL(e.paper_score, 0) as paper_score, 
        IFNULL(e.patient_score, 0) as patient_score, 
        IFNULL(e.public_score, 0) as public_score, 
        IFNULL(e.peer_score, 0) as peer_score,
        IFNULL(e.kindness, 0) as kindness, 
        IFNULL(e.satisfaction, 0) as satisfaction,
        IFNULL(e.explanation, 0) as explanation, 
        IFNULL(e.recommendation, 0) as recommendation
        from 
            (select * from doctor_basic where doctor_id is not null and doctorname = :name) a
            left join doctor_career b
            on a.rid = b.rid 
            left join (
                select doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,
                avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
                from doctor_evaluation group by doctor_id) e
            on a.doctor_id = e.doctor_id
            left join hospital s 
            on a.hid = s.hid"""     
        param = {"name": name}

    logger.debug(f"fechData: doctor_career")
    return query, param

def getSearchDoctors(name: str, hospital: str = "", deptname: str = "") -> dict:
    """의사 상세 정보를 가져오는 함수"""

    query, param = buildDoctorQuery(name, hospital, deptname)

    result = fetchData(query, param)["data"]
    return result

def getSearchDoctorsByHospitalAndDept(hospital: str, deptname: str) -> dict:
    """병원과 진료과로 의사를 검색하는 함수"""
    
    # 병원 ID 가져오기
    query = "select * from hospital where baseName = :hospital or shortName = :hospital"
    param = {"hospital": hospital}
    
    logger.debug(f"fechData: hospital")
    result = fetchData(query, param)
    
    if not result["data"]:
        return []
    
    hid = result["data"][0]['hid']
    
    # 해당 병원의 해당 진료과 의사들 가져오기
    query = """select s.shortName, s.address, s.lat, s.lon, s.telephone, a.doctor_id, a.doctorname, a.deptname, 
    a.specialties, a.doctor_url, a.profileimgurl,
    b.education, b.career, b.jsondata, a.rid,
    IFNULL(e.paper_score, 0) as paper_score, 
    IFNULL(e.patient_score, 0) as patient_score, 
    IFNULL(e.public_score, 0) as public_score, 
    IFNULL(e.peer_score, 0) as peer_score,
    IFNULL(e.kindness, 0) as kindness, 
    IFNULL(e.satisfaction, 0) as satisfaction,
    IFNULL(e.explanation, 0) as explanation, 
    IFNULL(e.recommendation, 0) as recommendation
    from 
        (select * from doctor_basic where doctor_id is not null and hid = :hid and deptname = :deptname) a
        left join doctor_career b
        on a.rid = b.rid 
        left join (
            select doctor_id, avg(paper_score) as paper_score, avg(patient_score) as patient_score, avg(public_score) as public_score, avg(peer_score) as peer_score,
            avg(kindness) as kindness, avg(satisfaction) as satisfaction, avg(explanation) as explanation, avg(recommendation) as recommendation
            from doctor_evaluation group by doctor_id) e
        on a.doctor_id = e.doctor_id
        left join hospital s 
        on a.hid = s.hid
    order by e.paper_score desc, e.patient_score desc"""
    
    param = {"hid": hid, "deptname": deptname}
    
    logger.debug(f"fechData: doctors by hospital and dept")
    result = fetchData(query, param)["data"]
    return result

def getDoctorById(doctor_id: int) -> dict:
    """의사 상세 정보를 가져오는 함수"""

    query = """select b.doctorname, b.deptname, c.shortname from doctor a
    left join doctor_basic b on a.doctor_id = b.doctor_id
    left join hospital c on b.hid = c.hid
    where a.doctor_id = :doctor_id"""

    param = {"doctor_id": doctor_id}
    result = fetchData(query, param)["data"]

    name = result[0]['doctorname']
    hospital = result[0]['shortname']
    deptname = result[0]['deptname']

    query, param = buildDoctorQuery(name, hospital, deptname)

    result = fetchData(query, param)["data"]
    return result    