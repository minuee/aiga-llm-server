from geopy.geocoders import Nominatim
from geopy.extra.rate_limiter import RateLimiter
import asyncio
import logging

logger = logging.getLogger(__name__)

# Nominatim Geocoder 초기화. user_agent는 필수로 지정해야 합니다.
# rate_limiter를 사용하여 호출 간 지연을 추가하여 서비스 정책을 준수합니다.
geolocator = Nominatim(user_agent="aiga-llm-server-geocoder")
geocode_reverse = RateLimiter(geolocator.reverse, min_delay_seconds=1)

async def get_address_from_coordinates(latitude: float, longitude: float) -> str | None:
    """
    주어진 위도와 경도로부터 주소를 비동기적으로 가져옵니다.
    """
    if latitude is None or longitude is None:
        logger.warning("Latitude or longitude is None. Cannot perform geocoding.")
        return None
        
    try:
        # Nominatim은 비동기를 직접 지원하지 않으므로 asyncio.to_thread를 사용합니다.
        location = await asyncio.to_thread(geocode_reverse, (latitude, longitude))
        if location:
            logger.info(f"Geocoded ({latitude}, {longitude}) to: {location.address}")
            return location.address
        else:
            logger.info(f"No address found for ({latitude}, {longitude}).")
            return None
    except Exception as e:
        logger.error(f"Error during geocoding for ({latitude}, {longitude}): {e}", exc_info=True)
        return None
