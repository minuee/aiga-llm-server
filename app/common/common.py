from ..database.hospital import getHospitalStandardName
from ..common.logger import logger

def calculate_similarity(s1, s2):
    """두 한글 문자열의 음절 단위 유사도를 비교하는 함수"""

    set1 = set(s1)
    set2 = set(s2)
 
    intersection = len(set1.intersection(set2))
    union = len(set1.union(set2))
 
    if union == 0:
        return 0
 
    return intersection / union

def generate_hospital_aliases(name: str) -> list[str]:
    """
    병원 이름에 대한 기본 별칭을 생성합니다.
    - '대학교'를 '대'로 축약합니다. (예: 건국대학교병원 -> 건국대병원)
    - 이름 끝의 '병원'을 제거합니다. (예: 건국대병원 -> 건국대)
    
    Args:
        name: 원본 병원 이름
    
    Returns:
        생성된 별칭 리스트 (원본 이름 포함)
    """
    aliases = {name}
    
    # 처리할 이름들의 집합. 초기는 원본 이름만 포함.
    queue = {name}
    processed = set()

    while queue:
        current_name = queue.pop()
        if current_name in processed:
            continue
        processed.add(current_name)

        # 규칙 1: '대학교' -> '대'
        if "대학교" in current_name:
            alias = current_name.replace("대학교", "대")
            if alias not in aliases:
                aliases.add(alias)
                queue.add(alias)

        # 규칙 2: 이름 끝의 '병원' 제거
        if current_name.endswith("병원"):
            alias = current_name[:-2]
            # '병원'만 있는 이름이거나, 제거 후 빈 문자열이 되는 경우 방지
            if alias and alias not in aliases:
                aliases.add(alias)
                queue.add(alias)
                
    # 길이를 기준으로 내림차순 정렬하여 대표 이름이 먼저 오도록 함
    return sorted(list(aliases), key=len, reverse=True)


def getValidHospitalName(hospital: str):
    """병원 이름을 표준화하는 함수"""
    logger.info(f" NOHLOGGER : getValidHospitalName hospital: {hospital}")
    result = getHospitalStandardName(hospital);
    logger.info(f"NOHLOGGER : getValidHospitalName result: {result}")
    return result["data"][0]['standard_name'] if len(result["data"]) > 0 else None

def getValidHospitalName_old(hospital: str):
    """병원 이름을 표준화하는 함수"""
    shotNames = [
        ["강북삼성병원", "강북삼성"],
        ["건국대병원", "건국대", "건대병원", "건국대학교", "건국대학교병원"],
        ["경희대병원", "경희대", "경희대학교", "경희대학교병원", "경희의료원"],
        ["고려대구로병원", "고려대구로", "고대구로", "구로병원", "고려대학교구로", "고려대학교구로병원"],
        ["삼성서울병원", "삼성", "삼성서울", "서울삼성", "서울삼성병원", "삼성병원", "강남삼성병원", "서울ㅅㅅ병원"],
        ["서울대병원", "서울대", "서울대학교", "서울대학병원", "서울대학교병원"],
        ["강남세브란스병원", "강남세브란스", "강남ㅅㅂㄹㅅ", "강남ㅅㅂㄹㅅ병원", "ㄱㄴㅅㅂㄹㅅ", "ㄱㄴㅅㅂㄹㅅ병원"],
        ["세브란스병원", "세브란스", "신촌세브", "신촌세브란스", "신촌ㅅㅂㄹㅅ", "신촌ㅅㅂㄹㅅ병원", "ㅅㅊㅅㅂㄹㅅ", "ㅅㅊㅅㅂㄹㅅ병원"],
        ["이화여대목동병원", "이화여대", "이화여대목동", "이대목동", "이화여자대학교", "이대목동병원"],
        ["서울아산병원", "서울아산", "아산병원", "서울ㅇㅅ병원", "ㅅㅇㅇㅅㅂㅇ"],
        ["중앙대병원", "중앙대", "중앙대학교", "중앙대학교병원"],
        ["고려대안암병원", "구려대안암", "고대안암", "안암병원", "고려대학교안암", "고려대학교안암병원"],
        ["서울성모병원", "서울성모", "성모병원", "강남성모", "강남성모병원", "ㅅㅇㅅㅁ"],
        ["한양대병원", "한양대", "한양대학교", "한양대학교병원", "한양대학교서울병원"],
        ["가톨릭대인천성모병원", "가톨릭대학교인천성모병원", "인천성모병원"],
        ["순천향대부천병원", "순천향대학교부속부천병원"],
        ["가천대길병원", "의료법인 길의료재단 길병원", "길병원"],
        ["인하대학교부속병원", "인하대학교의과대학부속병원", "인하대병원"],
        ["가톨릭대성빈센트병원", "가톨릭대학교 성빈센트병원", "성빈센트병원", "가톨릭성빈센트병원"],
        ["고려대안산병원", "고려대학교의과대학부속안산병원", "고대안산병원"],
        ["분당서울대병원", "분당서울대학교병원"],
        ["아주대학교병원", "아주대병원"],
        ["한림대학교성심병원", "한림대성심병원"],
        ["강릉아산병원"],
        ["원주세브란스기독병원", "연세대학교 원주세브란스기독병원", "원주기독병원"],
        ["충북대학교병원", "충북대병원"],
        ["단국대학교병원", "단국대병원", "단국대학교의과대학부속병원"],
        ["충남대학교병원", "충남대병원"],
        ["건양대학교병원", "학교법인 건양교육재단 건양대학교병원"],
        ["원광대학교병원", "원광대병원"],
        ["전북대학교병원", "전북대병원"],
        ["전남대학교병원", "전남대병원"],
        ["조선대학교병원", "조선대병원"],
        ["화순전남대학교병원", "화순전남대병원"],
        ["경북대학교병원", "경북대병원"],
        ["계명대학교동산병원", "계명대동산병원"],
        ["대구가톨릭대학교병원", "대구가톨릭대병원"],
        ["영남대학교병원", "영남대병원"],
        ["칠곡경북대학교병원", "칠곡경북대병원"],
        ["고신대학교복음병원", "고신대복음병원", "고신대병원"],
        ["동아대학교병원", "동아대병원"],
        ["부산대학교병원", "부산대병원"],
        ["양산부산대학교병원", "양산부산대병원"],
        ["인제대학교부산백병원", "부산백병원"],
        ["학교법인 울산공업학원 울산대학교병원", "울산대병원"],
        ["경상국립대학교병원", "경상대병원"],
        ["성균관대삼성창원병원", "삼성창원병원", "학교법인성균관대학삼성창원병원"]
    ]
    found = ""
    for i in shotNames:
        for j in i:
            if j == hospital:
                found = i[0]
                break
        if found:
            break
    return found