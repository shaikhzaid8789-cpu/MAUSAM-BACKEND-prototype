from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional, List

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ============================================================
# CONFIGURATION
# ============================================================

NOMINATIM_URL = ""

OPEN_METEO_WEATHER_URL = ""

OPEN_METEO_AIR_URL = ""

NOMINATIM_HEADERS = {
    "User-Agent": ""
}

REQUEST_TIMEOUT = 15.0


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("SIH-WeatherApp")


# ============================================================
# STATIC DEMO USERS
# ============================================================

DEMO_USERS = {
    "traveller": {
        "password": "123",
        "name": "Globe Trotter",
        "role": "Traveler"
    },
    "farmer": {
        "password": "456",
        "name": "Agri Expert",
        "role": "Farmer"
    },
    "athlete": {
        "password": "789",
        "name": "Pro Athlete",
        "role": "Athlete"
    },
    "public": {
        "password": "456",
        "name": "Daily Commuter",
        "role": "Public"
    }
}


# ============================================================
# GLOBAL HTTP CLIENT
# ============================================================

http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):

    global http_client

    logger.info("Starting Weather API server...")

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        follow_redirects=True
    )

    yield

    if http_client:
        await http_client.aclose()

    logger.info("Weather API server stopped.")


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="SIH Weather Web Application API",
    description=(
        "Role-based weather dashboard backend for Traveler, "
        "Farmer, Athlete and Public users."
    ),
    version="2.1.0",
    lifespan=lifespan
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


# ============================================================
# PYDANTIC MODELS
# ============================================================

class LoginRequest(BaseModel):
    username: str
    password: str


# ============================================================
# WEATHER CODE DESCRIPTION
# ============================================================

WEATHER_CODE_MAP = {
    0: "Clear sky",

    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",

    45: "Fog",
    48: "Depositing rime fog",

    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",

    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",

    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",

    66: "Light freezing rain",
    67: "Heavy freezing rain",

    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",

    77: "Snow grains",

    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",

    85: "Slight snow showers",
    86: "Heavy snow showers",

    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail"
}


# ============================================================
# AQI CATEGORY
# ============================================================

def get_aqi_category(aqi: Optional[float]) -> str:

    if aqi is None:
        return "Unknown"

    if aqi <= 50:
        return "Good"

    if aqi <= 100:
        return "Moderate"

    if aqi <= 150:
        return "Unhealthy for Sensitive Groups"

    if aqi <= 200:
        return "Unhealthy"

    if aqi <= 300:
        return "Very Unhealthy"

    return "Hazardous"


# ============================================================
# HELPER: REQUIRE HTTP CLIENT
# ============================================================

def get_http_client() -> httpx.AsyncClient:

    if http_client is None:
        raise HTTPException(
            status_code=503,
            detail="Weather service is starting. Please try again."
        )

    return http_client


# ============================================================
# SAFE NUMBER HELPERS
# ============================================================

def safe_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> Optional[int]:

    if value is None:
        return None

    try:
        return int(value)

    except (TypeError, ValueError):
        return None


# ============================================================
# GEOCODING
# ============================================================

async def geocode_city(city: str) -> Dict[str, Any]:

    client = get_http_client()

    city = city.strip()

    if not city:
        raise HTTPException(
            status_code=400,
            detail="City name cannot be empty."
        )

    params = {
        "q": city,
        "format": "json",
        "limit": 1
    }

    try:

        response = await client.get(
            NOMINATIM_URL,
            params=params,
            headers=NOMINATIM_HEADERS
        )

        response.raise_for_status()

        results = response.json()

        if not results:

            raise HTTPException(
                status_code=404,
                detail=f"City '{city}' not found."
            )

        result = results[0]

        return {
            "name": result.get(
                "display_name",
                city
            ),

            "latitude": float(
                result["lat"]
            ),

            "longitude": float(
                result["lon"]
            )
        }

    except HTTPException:
        raise

    except httpx.TimeoutException:

        logger.exception(
            "Nominatim timeout"
        )

        raise HTTPException(
            status_code=504,
            detail="Location service timed out."
        )

    except httpx.HTTPError:

        logger.exception(
            "Nominatim HTTP error"
        )

        raise HTTPException(
            status_code=502,
            detail="Location service is currently unavailable."
        )

    except (ValueError, KeyError):

        logger.exception(
            "Invalid geocoding response"
        )

        raise HTTPException(
            status_code=502,
            detail="Invalid response received from location service."
        )

    except Exception:

        logger.exception(
            "Unexpected geocoding error"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to search city."
        )


# ============================================================
# WEATHER API
# ============================================================

async def fetch_weather(
    latitude: float,
    longitude: float
) -> Dict[str, Any]:

    client = get_http_client()

    params = {

        "latitude": latitude,

        "longitude": longitude,

        # ----------------------------------------------------
        # CURRENT WEATHER
        # ----------------------------------------------------

        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "is_day,"
            "precipitation,"
            "weather_code,"
            "surface_pressure,"
            "wind_speed_10m,"
            "uv_index"
        ),

        # ----------------------------------------------------
        # HOURLY WEATHER
        # ----------------------------------------------------

        "hourly": (
            "temperature_2m,"
            "precipitation_probability,"
            "precipitation,"
            "soil_moisture_0_to_1cm,"
            "weather_code"
        ),

        # ----------------------------------------------------
        # DAILY WEATHER
        # ----------------------------------------------------

        "daily": (
            "sunrise,"
            "sunset,"
            "uv_index_max,"
            "precipitation_sum,"
            "precipitation_probability_max,"
            "weather_code"
        ),

        "timezone": "auto"
    }

    try:

        response = await client.get(
            OPEN_METEO_WEATHER_URL,
            params=params
        )

        response.raise_for_status()

        return response.json()

    except httpx.TimeoutException:

        logger.exception(
            "Weather API timeout"
        )

        raise HTTPException(
            status_code=504,
            detail="Weather service timed out."
        )

    except httpx.HTTPError:

        logger.exception(
            "Weather API HTTP error"
        )

        raise HTTPException(
            status_code=502,
            detail="Weather service is currently unavailable."
        )

    except Exception:

        logger.exception(
            "Unexpected weather error"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to fetch weather data."
        )


# ============================================================
# AIR QUALITY API
# ============================================================

async def fetch_air_quality(
    latitude: float,
    longitude: float
) -> Dict[str, Any]:

    client = get_http_client()

    params = {

        "latitude": latitude,

        "longitude": longitude,

        "current": (
            "us_aqi,"
            "pm10,"
            "pm2_5,"
            "carbon_monoxide,"
            "nitrogen_dioxide"
        )
    }

    try:

        response = await client.get(
            OPEN_METEO_AIR_URL,
            params=params
        )

        response.raise_for_status()

        return response.json()

    except httpx.TimeoutException:

        logger.exception(
            "Air quality API timeout"
        )

        raise HTTPException(
            status_code=504,
            detail="Air quality service timed out."
        )

    except httpx.HTTPError:

        logger.exception(
            "Air quality API HTTP error"
        )

        raise HTTPException(
            status_code=502,
            detail="Air quality service is currently unavailable."
        )

    except Exception:

        logger.exception(
            "Unexpected air quality error"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to fetch air quality data."
        )


# ============================================================
# FETCH WEATHER + AQI CONCURRENTLY
# ============================================================

async def fetch_complete_weather(
    latitude: float,
    longitude: float
) -> Dict[str, Any]:

    weather_data, air_quality_data = await asyncio.gather(

        fetch_weather(
            latitude,
            longitude
        ),

        fetch_air_quality(
            latitude,
            longitude
        )
    )

    return {
        "weather": weather_data,
        "air_quality": air_quality_data
    }


# ============================================================
# FORMAT BASIC WEATHER INFORMATION
# ============================================================

def build_current_weather(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    weather = data.get(
        "weather",
        {}
    )

    current = weather.get(
        "current",
        {}
    )

    current_units = weather.get(
        "current_units",
        {}
    )

    air_quality = data.get(
        "air_quality",
        {}
    )

    aqi_current = air_quality.get(
        "current",
        {}
    )

    aqi_units = air_quality.get(
        "current_units",
        {}
    )

    weather_code = current.get(
        "weather_code"
    )

    aqi = safe_float(
        aqi_current.get("us_aqi")
    )

    return {

        "time": current.get(
            "time"
        ),

        "temperature": current.get(
            "temperature_2m"
        ),

        "temperature_unit": current_units.get(
            "temperature_2m",
            "°C"
        ),

        "humidity": current.get(
            "relative_humidity_2m"
        ),

        "precipitation": current.get(
            "precipitation"
        ),

        "weather_code": weather_code,

        "weather_description": WEATHER_CODE_MAP.get(
            weather_code,
            "Unknown"
        ),

        "is_day": current.get(
            "is_day"
        ),

        "period": (
            "Day"
            if current.get("is_day") == 1
            else "Night"
        ),

        "surface_pressure": current.get(
            "surface_pressure"
        ),

        "wind_speed": current.get(
            "wind_speed_10m"
        ),

        "uv_index": current.get(
            "uv_index"
        ),

        "air_quality": {

            "time": aqi_current.get(
                "time"
            ),

            "us_aqi": aqi,

            "aqi_category": get_aqi_category(
                aqi
            ),

            "pm10": aqi_current.get(
                "pm10"
            ),

            "pm10_unit": aqi_units.get(
                "pm10"
            ),

            "pm2_5": aqi_current.get(
                "pm2_5"
            ),

            "pm2_5_unit": aqi_units.get(
                "pm2_5"
            ),

            "carbon_monoxide": aqi_current.get(
                "carbon_monoxide"
            ),

            "nitrogen_dioxide": aqi_current.get(
                "nitrogen_dioxide"
            )
        }
    }


# ============================================================
# EXTRACT HOURLY INFORMATION
# ============================================================

def build_hourly_data(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    weather = data.get(
        "weather",
        {}
    )

    hourly = weather.get(
        "hourly",
        {}
    )

    hourly_units = weather.get(
        "hourly_units",
        {}
    )

    return {

        "time": hourly.get(
            "time",
            []
        ),

        "temperature": {

            "values": hourly.get(
                "temperature_2m",
                []
            ),

            "unit": hourly_units.get(
                "temperature_2m",
                "°C"
            )
        },

        "precipitation_probability": {

            "values": hourly.get(
                "precipitation_probability",
                []
            ),

            "unit": hourly_units.get(
                "precipitation_probability",
                "%"
            )
        },

        "precipitation": {

            "values": hourly.get(
                "precipitation",
                []
            ),

            "unit": hourly_units.get(
                "precipitation",
                "mm"
            )
        },

        "soil_moisture": {

            "values": hourly.get(
                "soil_moisture_0_to_1cm",
                []
            ),

            "unit": hourly_units.get(
                "soil_moisture_0_to_1cm",
                ""
            )
        },

        "weather_code": {

            "values": hourly.get(
                "weather_code",
                []
            ),

            "unit": ""
        }
    }


# ============================================================
# EXTRACT DAILY INFORMATION
# ============================================================

def build_daily_data(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    weather = data.get(
        "weather",
        {}
    )

    daily = weather.get(
        "daily",
        {}
    )

    daily_units = weather.get(
        "daily_units",
        {}
    )

    return {

        "time": daily.get(
            "time",
            []
        ),

        "sunrise": {

            "values": daily.get(
                "sunrise",
                []
            ),

            "unit": daily_units.get(
                "sunrise",
                ""
            )
        },

        "sunset": {

            "values": daily.get(
                "sunset",
                []
            ),

            "unit": daily_units.get(
                "sunset",
                ""
            )
        },

        "uv_index_max": {

            "values": daily.get(
                "uv_index_max",
                []
            ),

            "unit": daily_units.get(
                "uv_index_max",
                ""
            )
        },

        "precipitation_sum": {

            "values": daily.get(
                "precipitation_sum",
                []
            ),

            "unit": daily_units.get(
                "precipitation_sum",
                "mm"
            )
        },

        "precipitation_probability_max": {

            "values": daily.get(
                "precipitation_probability_max",
                []
            ),

            "unit": daily_units.get(
                "precipitation_probability_max",
                "%"
            )
        },

        "weather_code": {

            "values": daily.get(
                "weather_code",
                []
            ),

            "unit": ""
        }
    }


# ============================================================
# FIND CURRENT HOURLY INDEX
# ============================================================

def get_current_hour_index(
    hourly_times: List[Any],
    current_time: Optional[str]
) -> int:

    if not hourly_times:
        return -1

    if not current_time:
        return 0

    try:

        current_datetime = datetime.fromisoformat(
            str(current_time)
        )

    except (ValueError, TypeError):

        return 0

    best_index = 0

    best_difference = None

    for index, time_value in enumerate(hourly_times):

        try:

            hourly_datetime = datetime.fromisoformat(
                str(time_value)
            )

            difference = abs(
                (
                    hourly_datetime
                    - current_datetime
                ).total_seconds()
            )

            if (
                best_difference is None
                or difference < best_difference
            ):

                best_difference = difference

                best_index = index

        except (ValueError, TypeError):

            continue

    return best_index


# ============================================================
# GET TODAY'S HOURLY VALUES
# ============================================================

def get_today_hourly_indices(
    hourly_times: List[Any],
    current_time: Optional[str]
) -> List[int]:

    if not hourly_times:
        return []

    today_string = None

    if current_time:

        try:

            today_string = str(
                datetime.fromisoformat(
                    str(current_time)
                ).date()
            )

        except (ValueError, TypeError):

            today_string = str(
                hourly_times[0]
            )[:10]

    else:

        today_string = str(
            hourly_times[0]
        )[:10]

    indices = []

    for index, time_value in enumerate(
        hourly_times
    ):

        if str(time_value).startswith(
            today_string
        ):

            indices.append(index)

    return indices


# ============================================================
# WEATHER FORECAST ANALYSIS
# ============================================================

def analyze_forecast(
    complete_data: Dict[str, Any],
    current: Dict[str, Any],
    hourly: Dict[str, Any],
    daily: Dict[str, Any]
) -> Dict[str, Any]:

    hourly_times = hourly.get(
        "time",
        []
    )

    current_time = current.get(
        "time"
    )

    current_index = get_current_hour_index(
        hourly_times,
        current_time
    )

    today_indices = get_today_hourly_indices(
        hourly_times,
        current_time
    )

    # --------------------------------------------------------
    # HOURLY ARRAYS
    # --------------------------------------------------------

    rain_probability_values = (
        hourly.get(
            "precipitation_probability",
            {}
        ).get(
            "values",
            []
        )
    )

    precipitation_values = (
        hourly.get(
            "precipitation",
            {}
        ).get(
            "values",
            []
        )
    )

    temperature_values = (
        hourly.get(
            "temperature",
            {}
        ).get(
            "values",
            []
        )
    )

    soil_moisture_values = (
        hourly.get(
            "soil_moisture",
            {}
        ).get(
            "values",
            []
        )
    )

    weather_code_values = (
        hourly.get(
            "weather_code",
            {}
        ).get(
            "values",
            []
        )
    )

    # --------------------------------------------------------
    # CURRENT HOURLY VALUES
    # --------------------------------------------------------

    current_rain_probability = None

    current_hour_precipitation = None

    current_hour_temperature = None

    current_hour_soil_moisture = None

    current_hour_weather_code = None

    if (
        current_index >= 0
        and current_index < len(
            rain_probability_values
        )
    ):

        current_rain_probability = safe_float(
            rain_probability_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            precipitation_values
        )
    ):

        current_hour_precipitation = safe_float(
            precipitation_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            temperature_values
        )
    ):

        current_hour_temperature = safe_float(
            temperature_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            soil_moisture_values
        )
    ):

        current_hour_soil_moisture = safe_float(
            soil_moisture_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            weather_code_values
        )
    ):

        current_hour_weather_code = safe_int(
            weather_code_values[
                current_index
            ]
        )

    # --------------------------------------------------------
    # TODAY'S REMAINING HOURLY FORECAST
    # --------------------------------------------------------

    remaining_today_indices = [

        index

        for index in today_indices

        if index >= current_index
    ]

    remaining_rain_probabilities = [

        safe_float(
            rain_probability_values[index]
        )

        for index in remaining_today_indices

        if index < len(
            rain_probability_values
        )
    ]

    remaining_precipitation = [

        safe_float(
            precipitation_values[index]
        )

        for index in remaining_today_indices

        if index < len(
            precipitation_values
        )
    ]

    remaining_rain_probabilities = [

        value

        for value in remaining_rain_probabilities

        if value is not None
    ]

    remaining_precipitation = [

        value

        for value in remaining_precipitation

        if value is not None
    ]

    # --------------------------------------------------------
    # TODAY MAX RAIN PROBABILITY
    # --------------------------------------------------------

    today_max_rain_probability = (

        max(
            remaining_rain_probabilities
        )

        if remaining_rain_probabilities

        else None
    )

    # --------------------------------------------------------
    # TODAY TOTAL PRECIPITATION
    # --------------------------------------------------------

    today_hourly_precipitation = sum(
        remaining_precipitation
    )

    # --------------------------------------------------------
    # DAILY VALUES
    # --------------------------------------------------------

    daily_rain_probability_values = (
        daily.get(
            "precipitation_probability_max",
            {}
        ).get(
            "values",
            []
        )
    )

    daily_precipitation_values = (
        daily.get(
            "precipitation_sum",
            {}
        ).get(
            "values",
            []
        )
    )

    daily_weather_codes = (
        daily.get(
            "weather_code",
            {}
        ).get(
            "values",
            []
        )
    )

    today_daily_rain_probability = None

    today_daily_precipitation = None

    today_daily_weather_code = None

    if daily_rain_probability_values:

        today_daily_rain_probability = safe_float(
            daily_rain_probability_values[0]
        )

    if daily_precipitation_values:

        today_daily_precipitation = safe_float(
            daily_precipitation_values[0]
        )

    if daily_weather_codes:

        today_daily_weather_code = safe_int(
            daily_weather_codes[0]
        )

    # --------------------------------------------------------
    # PREFER DAILY FORECAST FOR TODAY
    # --------------------------------------------------------
    #
    # Daily precipitation probability represents the
    # probability during the day and is more meaningful
    # for dashboard cards than only one hourly value.
    #
    # --------------------------------------------------------

    meaningful_today_rain_probability = (
        today_daily_rain_probability
        if today_daily_rain_probability is not None
        else today_max_rain_probability
    )

    meaningful_today_precipitation = (
        today_daily_precipitation
        if today_daily_precipitation is not None
        else today_hourly_precipitation
    )

    # --------------------------------------------------------
    # RAIN EXPECTED LOGIC
    # --------------------------------------------------------

    rain_codes = {
        51,
        53,
        55,
        56,
        57,
        61,
        63,
        65,
        66,
        67,
        80,
        81,
        82,
        95,
        96,
        99
    }

    rain_expected = (

        (
            meaningful_today_rain_probability is not None
            and meaningful_today_rain_probability >= 40
        )

        or

        (
            meaningful_today_precipitation is not None
            and meaningful_today_precipitation > 0.1
        )

        or

        (
            current_hour_weather_code
            in rain_codes
        )

        or

        (
            today_daily_weather_code
            in rain_codes
        )
    )

    # --------------------------------------------------------
    # RAIN INTENSITY
    # --------------------------------------------------------

    if (
        meaningful_today_precipitation is not None
        and meaningful_today_precipitation >= 20
    ):

        rain_intensity = "Heavy"

    elif (
        meaningful_today_precipitation is not None
        and meaningful_today_precipitation >= 5
    ):

        rain_intensity = "Moderate"

    elif rain_expected:

        rain_intensity = "Light / Possible"

    else:

        rain_intensity = "None Expected"

    # --------------------------------------------------------
    # RAIN STATUS
    # --------------------------------------------------------

    if (
        meaningful_today_rain_probability is None
    ):

        rain_status = "Forecast unavailable"

    elif (
        meaningful_today_rain_probability >= 70
    ):

        rain_status = "High chance of rain"

    elif (
        meaningful_today_rain_probability >= 40
    ):

        rain_status = "Possible rain"

    elif (
        meaningful_today_rain_probability >= 20
    ):

        rain_status = "Low chance of rain"

    else:

        rain_status = "Very low chance of rain"

    # --------------------------------------------------------
    # SOIL MOISTURE
    # --------------------------------------------------------

    soil_moisture_status = "Unavailable"

    if current_hour_soil_moisture is not None:

        if current_hour_soil_moisture < 0.15:

            soil_moisture_status = "Low"

        elif current_hour_soil_moisture < 0.30:

            soil_moisture_status = "Moderate"

        elif current_hour_soil_moisture < 0.45:

            soil_moisture_status = "Good"

        else:

            soil_moisture_status = "High"

    # --------------------------------------------------------
    # TEMPERATURE STATUS
    # --------------------------------------------------------

    temperature = safe_float(
        current.get(
            "temperature"
        )
    )

    if temperature is None:

        temperature_status = "Unavailable"

    elif temperature >= 40:

        temperature_status = "Extreme heat"

    elif temperature >= 35:

        temperature_status = "Very hot"

    elif temperature >= 30:

        temperature_status = "Warm"

    elif temperature >= 20:

        temperature_status = "Comfortable"

    elif temperature >= 10:

        temperature_status = "Cool"

    else:

        temperature_status = "Cold"

    # --------------------------------------------------------
    # HUMIDITY STATUS
    # --------------------------------------------------------

    humidity = safe_float(
        current.get(
            "humidity"
        )
    )

    if humidity is None:

        humidity_status = "Unavailable"

    elif humidity >= 85:

        humidity_status = "Very high"

    elif humidity >= 70:

        humidity_status = "High"

    elif humidity >= 40:

        humidity_status = "Comfortable"

    else:

        humidity_status = "Low"

    # --------------------------------------------------------
    # WIND STATUS
    # --------------------------------------------------------

    wind_speed = safe_float(
        current.get(
            "wind_speed"
        )
    )

    if wind_speed is None:

        wind_status = "Unavailable"

    elif wind_speed >= 50:

        wind_status = "Very strong"

    elif wind_speed >= 30:

        wind_status = "Strong"

    elif wind_speed >= 15:

        wind_status = "Moderate"

    else:

        wind_status = "Light"

    # --------------------------------------------------------
    # UV STATUS
    # --------------------------------------------------------

    uv_index = safe_float(
        current.get(
            "uv_index"
        )
    )

    if uv_index is None:

        uv_status = "Unavailable"

    elif uv_index >= 11:

        uv_status = "Extreme"

    elif uv_index >= 8:

        uv_status = "Very high"

    elif uv_index >= 6:

        uv_status = "High"

    elif uv_index >= 3:

        uv_status = "Moderate"

    else:

        uv_status = "Low"

    # --------------------------------------------------------
    # AQI STATUS
    # --------------------------------------------------------

    aqi = safe_float(
        current.get(
            "air_quality",
            {}
        ).get(
            "us_aqi"
        )
    )

    if aqi is None:

        aqi_status = "Unavailable"

    elif aqi <= 50:

        aqi_status = "Good"

    elif aqi <= 100:

        aqi_status = "Moderate"

    elif aqi <= 150:

        aqi_status = "Unhealthy for sensitive groups"

    elif aqi <= 200:

        aqi_status = "Unhealthy"

    else:

        aqi_status = "Poor"

    return {

        "current_hour_index": current_index,

        "current_hour_rain_probability":
            current_rain_probability,

        "current_hour_precipitation":
            current_hour_precipitation,

        "current_hour_temperature":
            current_hour_temperature,

        "current_hour_soil_moisture":
            current_hour_soil_moisture,

        "current_hour_weather_code":
            current_hour_weather_code,

        "today_rain_probability":
            meaningful_today_rain_probability,

        "today_max_hourly_rain_probability":
            today_max_rain_probability,

        "today_precipitation":
            meaningful_today_precipitation,

        "rain_expected":
            rain_expected,

        "rain_status":
            rain_status,

        "rain_intensity":
            rain_intensity,

        "temperature_status":
            temperature_status,

        "humidity_status":
            humidity_status,

        "wind_status":
            wind_status,

        "uv_status":
            uv_status,

        "aqi_status":
            aqi_status,

        "soil_moisture_status":
            soil_moisture_status
    }


# ============================================================
# ROLE VALIDATION
# ============================================================

ROLE_MAP = {

    "traveler": "Traveler",

    "traveller": "Traveler",

    "farmer": "Farmer",

    "athlete": "Athlete",

    "public": "Public"
}


def normalize_role(
    role: str
) -> str:

    normalized = role.strip().lower()

    if normalized not in ROLE_MAP:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid role. Allowed roles: "
                "Traveler, Farmer, Athlete, Public."
            )
        )

    return ROLE_MAP[
        normalized
    ]


# ============================================================
# ROLE-SPECIFIC DASHBOARD
# ============================================================

def build_role_dashboard(
    role: str,
    location: Dict[str, Any],
    complete_data: Dict[str, Any]
) -> Dict[str, Any]:

    role = normalize_role(
        role
    )

    current = build_current_weather(
        complete_data
    )

    hourly = build_hourly_data(
        complete_data
    )

    daily = build_daily_data(
        complete_data
    )

    analysis = analyze_forecast(
        complete_data,
        current,
        hourly,
        daily
    )

    temperature = safe_float(
        current.get(
            "temperature"
        )
    )

    humidity = safe_float(
        current.get(
            "humidity"
        )
    )

    precipitation = safe_float(
        current.get(
            "precipitation"
        )
    )

    wind_speed = safe_float(
        current.get(
            "wind_speed"
        )
    )

    uv_index = safe_float(
        current.get(
            "uv_index"
        )
    )

    aqi_data = current.get(
        "air_quality",
        {}
    )

    aqi = safe_float(
        aqi_data.get(
            "us_aqi"
        )
    )

    today_rain_probability = analysis.get(
        "today_rain_probability"
    )

    today_precipitation = analysis.get(
        "today_precipitation"
    )

    rain_expected = analysis.get(
        "rain_expected"
    )

    rain_status = analysis.get(
        "rain_status"
    )

    # ========================================================
    # TRAVELER
    # ========================================================

    if role == "Traveler":

        if rain_expected:

            travel_rain_advice = (
                "Rain is possible today. "
                "Keep an umbrella or rain protection."
            )

        else:

            travel_rain_advice = (
                "No significant rain is expected "
                "based on the current forecast."
            )

        if (
            wind_speed is not None
            and wind_speed >= 30
        ):

            wind_advice = (
                "Strong winds may affect outdoor "
                "travel and open-area activities."
            )

        else:

            wind_advice = (
                "Wind conditions are generally "
                "manageable for outdoor travel."
            )

        if (
            uv_index is not None
            and uv_index >= 6
        ):

            uv_advice = (
                "UV exposure is high. "
                "Use sun protection during daytime."
            )

        else:

            uv_advice = (
                "UV conditions are relatively manageable."
            )

        return {

            "role": "Traveler",

            "dashboard_title":
                "Traveler Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "weather":
                    current.get(
                        "weather_description"
                    ),

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "today_precipitation":
                    today_precipitation,

                "wind_speed":
                    wind_speed,

                "uv_index":
                    uv_index,

                "air_quality":
                    aqi,

                "aqi_category":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "travel_insights": {

                "rain_expected":
                    rain_expected,

                "rain_advice":
                    travel_rain_advice,

                "high_uv":
                    (
                        uv_index is not None
                        and uv_index >= 6
                    ),

                "uv_advice":
                    uv_advice,

                "strong_wind":
                    (
                        wind_speed is not None
                        and wind_speed >= 30
                    ),

                "wind_advice":
                    wind_advice,

                "air_quality_status":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Destination Weather",

                "Today's Rain Probability",

                "7-Day Forecast",

                "Rain Forecast",

                "UV Index",

                "Wind Information",

                "Air Quality",

                "City Comparison"
            ]
        }

    # ========================================================
    # FARMER
    # ========================================================

    if role == "Farmer":

        soil_moisture = analysis.get(
            "current_hour_soil_moisture"
        )

        soil_status = analysis.get(
            "soil_moisture_status"
        )

        if rain_expected:

            farming_rain_advice = (
                "Rain is expected or possible today. "
                "Consider rainfall before irrigation."
            )

        else:

            farming_rain_advice = (
                "No significant rain is currently "
                "expected today. Irrigation planning "
                "may be required based on soil condition."
            )

        if soil_status == "Low":

            irrigation_advice = (
                "Soil moisture is low. "
                "Check crop water requirements."
            )

        elif soil_status == "Moderate":

            irrigation_advice = (
                "Soil moisture is moderate. "
                "Monitor before irrigation."
            )

        elif soil_status == "Good":

            irrigation_advice = (
                "Soil moisture is in a generally "
                "favorable range."
            )

        elif soil_status == "High":

            irrigation_advice = (
                "Soil moisture is high. "
                "Avoid unnecessary irrigation."
            )

        else:

            irrigation_advice = (
                "Soil moisture data is unavailable."
            )

        return {

            "role": "Farmer",

            "dashboard_title":
                "Farmer Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "temperature_status":
                    analysis.get(
                        "temperature_status"
                    ),

                "humidity":
                    humidity,

                "humidity_status":
                    analysis.get(
                        "humidity_status"
                    ),

                "soil_moisture":
                    soil_moisture,

                "soil_moisture_status":
                    soil_status,

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "precipitation":
                    precipitation,

                "today_precipitation":
                    today_precipitation,

                "wind_speed":
                    wind_speed,

                "wind_status":
                    analysis.get(
                        "wind_status"
                    ),

                "uv_index":
                    uv_index,

                "uv_status":
                    analysis.get(
                        "uv_status"
                    )
            },

            "farming_insights": {

                "rain_expected":
                    rain_expected,

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "rain_intensity":
                    analysis.get(
                        "rain_intensity"
                    ),

                "rain_advice":
                    farming_rain_advice,

                "soil_moisture_available":
                    soil_moisture is not None,

                "soil_moisture_status":
                    soil_status,

                "irrigation_advice":
                    irrigation_advice,

                "high_temperature":
                    (
                        temperature is not None
                        and temperature >= 35
                    ),

                "high_humidity":
                    (
                        humidity is not None
                        and humidity >= 80
                    ),

                "strong_wind":
                    (
                        wind_speed is not None
                        and wind_speed >= 30
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Temperature",

                "Humidity",

                "Soil Moisture",

                "Today's Rain Probability",

                "Rain Forecast",

                "Today's Precipitation",

                "Wind Speed",

                "UV Index",

                "Sunrise & Sunset",

                "7-Day Weather Forecast"
            ]
        }

    # ========================================================
    # ATHLETE
    # ========================================================

    if role == "Athlete":

        high_heat = (
            temperature is not None
            and temperature >= 35
        )

        high_humidity = (
            humidity is not None
            and humidity >= 80
        )

        strong_wind = (
            wind_speed is not None
            and wind_speed >= 30
        )

        high_uv = (
            uv_index is not None
            and uv_index >= 6
        )

        poor_air_quality = (
            aqi is not None
            and aqi > 100
        )

        # ----------------------------------------------------
        # OUTDOOR ACTIVITY STATUS
        # ----------------------------------------------------

        risk_count = sum([
            high_heat,
            high_humidity,
            strong_wind,
            high_uv,
            poor_air_quality,
            rain_expected
        ])

        if risk_count >= 4:

            activity_status = (
                "Challenging outdoor conditions"
            )

        elif risk_count >= 2:

            activity_status = (
                "Use caution for outdoor activity"
            )

        else:

            activity_status = (
                "Generally suitable outdoor conditions"
            )

        if rain_expected:

            athlete_rain_advice = (
                "Rain is possible today. "
                "Check the hourly forecast before outdoor training."
            )

        else:

            athlete_rain_advice = (
                "No significant rain is currently expected."
            )

        return {

            "role": "Athlete",

            "dashboard_title":
                "Athlete Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "temperature_status":
                    analysis.get(
                        "temperature_status"
                    ),

                "humidity":
                    humidity,

                "humidity_status":
                    analysis.get(
                        "humidity_status"
                    ),

                "wind_speed":
                    wind_speed,

                "wind_status":
                    analysis.get(
                        "wind_status"
                    ),

                "uv_index":
                    uv_index,

                "uv_status":
                    analysis.get(
                        "uv_status"
                    ),

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "air_quality":
                    aqi,

                "aqi_category":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "activity_conditions": {

                "activity_status":
                    activity_status,

                "rain_expected":
                    rain_expected,

                "rain_advice":
                    athlete_rain_advice,

                "high_heat":
                    high_heat,

                "high_humidity":
                    high_humidity,

                "strong_wind":
                    strong_wind,

                "high_uv":
                    high_uv,

                "poor_air_quality":
                    poor_air_quality,

                "aqi_status":
                    analysis.get(
                        "aqi_status"
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Outdoor Activity Conditions",

                "Temperature",

                "Humidity",

                "Wind Speed",

                "UV Index",

                "Today's Rain Probability",

                "Rain Forecast",

                "Air Quality",

                "7-Day Forecast"
            ]
        }

    # ========================================================
    # PUBLIC
    # ========================================================

    if role == "Public":

        if rain_expected:

            outdoor_advice = (
                "Rain is possible today. "
                "Carry an umbrella or rain protection."
            )

        else:

            outdoor_advice = (
                "No significant rain is currently expected."
            )

        return {

            "role": "Public",

            "dashboard_title":
                "Daily Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "weather":
                    current.get(
                        "weather_description"
                    ),

                "humidity":
                    humidity,

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "today_precipitation":
                    today_precipitation,

                "wind_speed":
                    wind_speed,

                "uv_index":
                    uv_index,

                "air_quality":
                    aqi,

                "aqi_category":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "daily_information": {

                "sunrise":
                    daily[
                        "sunrise"
                    ][
                        "values"
                    ][0]
                    if daily[
                        "sunrise"
                    ][
                        "values"
                    ]
                    else None,

                "sunset":
                    daily[
                        "sunset"
                    ][
                        "values"
                    ][0]
                    if daily[
                        "sunset"
                    ][
                        "values"
                    ]
                    else None,

                "today_rain_probability":
                    today_rain_probability,

                "today_precipitation":
                    today_precipitation,

                "rain_status":
                    rain_status
            },

            "outdoor_conditions": {

                "rain_expected":
                    rain_expected,

                "rain_advice":
                    outdoor_advice,

                "uv_status":
                    analysis.get(
                        "uv_status"
                    ),

                "wind_status":
                    analysis.get(
                        "wind_status"
                    ),

                "aqi_status":
                    analysis.get(
                        "aqi_status"
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Current Weather",

                "Temperature",

                "Humidity",

                "Today's Rain Probability",

                "Rain Forecast",

                "Today's Precipitation",

                "Wind Speed",

                "UV Index",

                "Air Quality",

                "7-Day Forecast",

                "Sunrise & Sunset"
            ]
        }

    raise HTTPException(
        status_code=400,
        detail="Unsupported role."
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    return {

        "success": True,

        "application":
            "SIH Weather Web Application",

        "version":
            "2.1.0",

        "message":
            "Weather backend is running.",

        "roles": [

            "Traveler",

            "Farmer",

            "Athlete",

            "Public"
        ],

        "endpoints": {

            "login":
                "POST /api/login",

            "search_city":
                "GET /api/search-city?city=Mumbai",

            "weather":
                "GET /api/weather?lat=19.0760&lon=72.8777",

            "dashboard":
                "GET /api/dashboard"
                "?role=Farmer&lat=19.0760&lon=72.8777",

            "compare":
                "GET /api/compare"
                "?city1=Mumbai&city2=Pune",

            "health":
                "GET /health",

            "docs":
                "/docs"
        }
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():

    return {

        "status":
            "healthy",

        "service":
            "SIH Weather API",

        "version":
            "2.1.0"
    }


# ============================================================
# LOGIN
# ============================================================

@app.post("/api/login")
async def login(
    request: LoginRequest
):

    username = (
        request.username
        .strip()
        .lower()
    )

    user = DEMO_USERS.get(
        username
    )

    if user is None:

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password."
        )

    if request.password != user["password"]:

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password."
        )

    return {

        "success": True,

        "user": {

            "username":
                username,

            "name":
                user["name"],

            "role":
                user["role"]
        }
    }


# ============================================================
# SEARCH CITY
# ============================================================

@app.get("/api/search-city")
async def search_city(
    city: str = Query(
        ...,
        min_length=1,
        description="City name"
    )
):

    location = await geocode_city(
        city
    )

    return {

        "success": True,

        "location":
            location
    }


# ============================================================
# WEATHER
# ============================================================

@app.get("/api/weather")
async def weather(

    lat: float = Query(
        ...,
        description="Latitude"
    ),

    lon: float = Query(
        ...,
        description="Longitude"
    )
):

    if not -90 <= lat <= 90:

        raise HTTPException(
            status_code=400,
            detail="Invalid latitude."
        )

    if not -180 <= lon <= 180:

        raise HTTPException(
            status_code=400,
            detail="Invalid longitude."
        )

    data = await fetch_complete_weather(
        lat,
        lon
    )

    return {

        "success": True,

        "latitude":
            lat,

        "longitude":
            lon,

        **data
    }


# ============================================================
# ROLE-BASED DASHBOARD
# ============================================================

@app.get("/api/dashboard")
async def dashboard(

    role: str = Query(
        ...,
        description=(
            "Traveler, Farmer, Athlete or Public"
        )
    ),

    lat: float = Query(
        ...,
        description="Latitude"
    ),

    lon: float = Query(
        ...,
        description="Longitude"
    )
):

    if not -90 <= lat <= 90:

        raise HTTPException(
            status_code=400,
            detail="Invalid latitude."
        )

    if not -180 <= lon <= 180:

        raise HTTPException(
            status_code=400,
            detail="Invalid longitude."
        )

    normalized_role = normalize_role(
        role
    )

    complete_data = (
        await fetch_complete_weather(
            lat,
            lon
        )
    )

    location = {

        "latitude":
            lat,

        "longitude":
            lon
    }

    dashboard_data = (
        build_role_dashboard(
            normalized_role,
            location,
            complete_data
        )
    )

    return {

        "success": True,

        "dashboard":
            dashboard_data
    }


# ============================================================
# COMPARE TWO CITIES
# ============================================================

@app.get("/api/compare")
async def compare(

    city1: str = Query(
        ...,
        min_length=1,
        description="First city"
    ),

    city2: str = Query(
        ...,
        min_length=1,
        description="Second city"
    )
):

    # --------------------------------------------------------
    # GEOCODE BOTH CITIES IN PARALLEL
    # --------------------------------------------------------

    try:

        location1, location2 = await asyncio.gather(

            geocode_city(
                city1
            ),

            geocode_city(
                city2
            )
        )

    except HTTPException:

        raise

    # --------------------------------------------------------
    # FETCH BOTH WEATHER DATASETS IN PARALLEL
    # --------------------------------------------------------

    try:

        data1, data2 = await asyncio.gather(

            fetch_complete_weather(
                location1["latitude"],
                location1["longitude"]
            ),

            fetch_complete_weather(
                location2["latitude"],
                location2["longitude"]
            )
        )

    except HTTPException:

        raise

    # --------------------------------------------------------
    # RETURN COMPARISON
    # --------------------------------------------------------

    return {

        "success": True,

        "city1": {

            "location":
                location1,

            "weather":
                data1
        },

        "city2": {

            "location":
                location2,

            "weather":
                data2
        }
    }


# ============================================================
# RUN DIRECTLY
# ============================================================

if __name__ == "__main__":

    import os
    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "8080"
        )
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )

import asyncio
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Dict, Optional, List

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


# ============================================================
# CONFIGURATION
# ============================================================

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

OPEN_METEO_WEATHER_URL = "https://api.open-meteo.com/v1/forecast"

OPEN_METEO_AIR_URL = "https://air-quality-api.open-meteo.com/v1/air-quality"

NOMINATIM_HEADERS = {
    "User-Agent": "SIH-WeatherApp-Demo/1.0"
}

REQUEST_TIMEOUT = 15.0


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("SIH-WeatherApp")


# ============================================================
# STATIC DEMO USERS
# ============================================================

DEMO_USERS = {
    "traveller": {
        "password": "123",
        "name": "Globe Trotter",
        "role": "Traveler"
    },
    "farmer": {
        "password": "456",
        "name": "Agri Expert",
        "role": "Farmer"
    },
    "athlete": {
        "password": "789",
        "name": "Pro Athlete",
        "role": "Athlete"
    },
    "public": {
        "password": "456",
        "name": "Daily Commuter",
        "role": "Public"
    }
}


# ============================================================
# GLOBAL HTTP CLIENT
# ============================================================

http_client: Optional[httpx.AsyncClient] = None


@asynccontextmanager
async def lifespan(app: FastAPI):

    global http_client

    logger.info("Starting Weather API server...")

    http_client = httpx.AsyncClient(
        timeout=httpx.Timeout(REQUEST_TIMEOUT),
        follow_redirects=True
    )

    yield

    if http_client:
        await http_client.aclose()

    logger.info("Weather API server stopped.")


# ============================================================
# FASTAPI APP
# ============================================================

app = FastAPI(
    title="SIH Weather Web Application API",
    description=(
        "Role-based weather dashboard backend for Traveler, "
        "Farmer, Athlete and Public users."
    ),
    version="2.1.0",
    lifespan=lifespan
)


# ============================================================
# CORS
# ============================================================

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"]
)


# ============================================================
# PYDANTIC MODELS
# ============================================================

class LoginRequest(BaseModel):
    username: str
    password: str


# ============================================================
# WEATHER CODE DESCRIPTION
# ============================================================

WEATHER_CODE_MAP = {
    0: "Clear sky",

    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",

    45: "Fog",
    48: "Depositing rime fog",

    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",

    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",

    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",

    66: "Light freezing rain",
    67: "Heavy freezing rain",

    71: "Slight snow",
    73: "Moderate snow",
    75: "Heavy snow",

    77: "Snow grains",

    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",

    85: "Slight snow showers",
    86: "Heavy snow showers",

    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail"
}


# ============================================================
# AQI CATEGORY
# ============================================================

def get_aqi_category(aqi: Optional[float]) -> str:

    if aqi is None:
        return "Unknown"

    if aqi <= 50:
        return "Good"

    if aqi <= 100:
        return "Moderate"

    if aqi <= 150:
        return "Unhealthy for Sensitive Groups"

    if aqi <= 200:
        return "Unhealthy"

    if aqi <= 300:
        return "Very Unhealthy"

    return "Hazardous"


# ============================================================
# HELPER: REQUIRE HTTP CLIENT
# ============================================================

def get_http_client() -> httpx.AsyncClient:

    if http_client is None:
        raise HTTPException(
            status_code=503,
            detail="Weather service is starting. Please try again."
        )

    return http_client


# ============================================================
# SAFE NUMBER HELPERS
# ============================================================

def safe_float(value: Any) -> Optional[float]:

    if value is None:
        return None

    try:
        return float(value)

    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> Optional[int]:

    if value is None:
        return None

    try:
        return int(value)

    except (TypeError, ValueError):
        return None


# ============================================================
# GEOCODING
# ============================================================

async def geocode_city(city: str) -> Dict[str, Any]:

    client = get_http_client()

    city = city.strip()

    if not city:
        raise HTTPException(
            status_code=400,
            detail="City name cannot be empty."
        )

    params = {
        "q": city,
        "format": "json",
        "limit": 1
    }

    try:

        response = await client.get(
            NOMINATIM_URL,
            params=params,
            headers=NOMINATIM_HEADERS
        )

        response.raise_for_status()

        results = response.json()

        if not results:

            raise HTTPException(
                status_code=404,
                detail=f"City '{city}' not found."
            )

        result = results[0]

        return {
            "name": result.get(
                "display_name",
                city
            ),

            "latitude": float(
                result["lat"]
            ),

            "longitude": float(
                result["lon"]
            )
        }

    except HTTPException:
        raise

    except httpx.TimeoutException:

        logger.exception(
            "Nominatim timeout"
        )

        raise HTTPException(
            status_code=504,
            detail="Location service timed out."
        )

    except httpx.HTTPError:

        logger.exception(
            "Nominatim HTTP error"
        )

        raise HTTPException(
            status_code=502,
            detail="Location service is currently unavailable."
        )

    except (ValueError, KeyError):

        logger.exception(
            "Invalid geocoding response"
        )

        raise HTTPException(
            status_code=502,
            detail="Invalid response received from location service."
        )

    except Exception:

        logger.exception(
            "Unexpected geocoding error"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to search city."
        )


# ============================================================
# WEATHER API
# ============================================================

async def fetch_weather(
    latitude: float,
    longitude: float
) -> Dict[str, Any]:

    client = get_http_client()

    params = {

        "latitude": latitude,

        "longitude": longitude,

        # ----------------------------------------------------
        # CURRENT WEATHER
        # ----------------------------------------------------

        "current": (
            "temperature_2m,"
            "relative_humidity_2m,"
            "is_day,"
            "precipitation,"
            "weather_code,"
            "surface_pressure,"
            "wind_speed_10m,"
            "uv_index"
        ),

        # ----------------------------------------------------
        # HOURLY WEATHER
        # ----------------------------------------------------

        "hourly": (
            "temperature_2m,"
            "precipitation_probability,"
            "precipitation,"
            "soil_moisture_0_to_1cm,"
            "weather_code"
        ),

        # ----------------------------------------------------
        # DAILY WEATHER
        # ----------------------------------------------------

        "daily": (
            "sunrise,"
            "sunset,"
            "uv_index_max,"
            "precipitation_sum,"
            "precipitation_probability_max,"
            "weather_code"
        ),

        "timezone": "auto"
    }

    try:

        response = await client.get(
            OPEN_METEO_WEATHER_URL,
            params=params
        )

        response.raise_for_status()

        return response.json()

    except httpx.TimeoutException:

        logger.exception(
            "Weather API timeout"
        )

        raise HTTPException(
            status_code=504,
            detail="Weather service timed out."
        )

    except httpx.HTTPError:

        logger.exception(
            "Weather API HTTP error"
        )

        raise HTTPException(
            status_code=502,
            detail="Weather service is currently unavailable."
        )

    except Exception:

        logger.exception(
            "Unexpected weather error"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to fetch weather data."
        )


# ============================================================
# AIR QUALITY API
# ============================================================

async def fetch_air_quality(
    latitude: float,
    longitude: float
) -> Dict[str, Any]:

    client = get_http_client()

    params = {

        "latitude": latitude,

        "longitude": longitude,

        "current": (
            "us_aqi,"
            "pm10,"
            "pm2_5,"
            "carbon_monoxide,"
            "nitrogen_dioxide"
        )
    }

    try:

        response = await client.get(
            OPEN_METEO_AIR_URL,
            params=params
        )

        response.raise_for_status()

        return response.json()

    except httpx.TimeoutException:

        logger.exception(
            "Air quality API timeout"
        )

        raise HTTPException(
            status_code=504,
            detail="Air quality service timed out."
        )

    except httpx.HTTPError:

        logger.exception(
            "Air quality API HTTP error"
        )

        raise HTTPException(
            status_code=502,
            detail="Air quality service is currently unavailable."
        )

    except Exception:

        logger.exception(
            "Unexpected air quality error"
        )

        raise HTTPException(
            status_code=500,
            detail="Unable to fetch air quality data."
        )


# ============================================================
# FETCH WEATHER + AQI CONCURRENTLY
# ============================================================

async def fetch_complete_weather(
    latitude: float,
    longitude: float
) -> Dict[str, Any]:

    weather_data, air_quality_data = await asyncio.gather(

        fetch_weather(
            latitude,
            longitude
        ),

        fetch_air_quality(
            latitude,
            longitude
        )
    )

    return {
        "weather": weather_data,
        "air_quality": air_quality_data
    }


# ============================================================
# FORMAT BASIC WEATHER INFORMATION
# ============================================================

def build_current_weather(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    weather = data.get(
        "weather",
        {}
    )

    current = weather.get(
        "current",
        {}
    )

    current_units = weather.get(
        "current_units",
        {}
    )

    air_quality = data.get(
        "air_quality",
        {}
    )

    aqi_current = air_quality.get(
        "current",
        {}
    )

    aqi_units = air_quality.get(
        "current_units",
        {}
    )

    weather_code = current.get(
        "weather_code"
    )

    aqi = safe_float(
        aqi_current.get("us_aqi")
    )

    return {

        "time": current.get(
            "time"
        ),

        "temperature": current.get(
            "temperature_2m"
        ),

        "temperature_unit": current_units.get(
            "temperature_2m",
            "°C"
        ),

        "humidity": current.get(
            "relative_humidity_2m"
        ),

        "precipitation": current.get(
            "precipitation"
        ),

        "weather_code": weather_code,

        "weather_description": WEATHER_CODE_MAP.get(
            weather_code,
            "Unknown"
        ),

        "is_day": current.get(
            "is_day"
        ),

        "period": (
            "Day"
            if current.get("is_day") == 1
            else "Night"
        ),

        "surface_pressure": current.get(
            "surface_pressure"
        ),

        "wind_speed": current.get(
            "wind_speed_10m"
        ),

        "uv_index": current.get(
            "uv_index"
        ),

        "air_quality": {

            "time": aqi_current.get(
                "time"
            ),

            "us_aqi": aqi,

            "aqi_category": get_aqi_category(
                aqi
            ),

            "pm10": aqi_current.get(
                "pm10"
            ),

            "pm10_unit": aqi_units.get(
                "pm10"
            ),

            "pm2_5": aqi_current.get(
                "pm2_5"
            ),

            "pm2_5_unit": aqi_units.get(
                "pm2_5"
            ),

            "carbon_monoxide": aqi_current.get(
                "carbon_monoxide"
            ),

            "nitrogen_dioxide": aqi_current.get(
                "nitrogen_dioxide"
            )
        }
    }


# ============================================================
# EXTRACT HOURLY INFORMATION
# ============================================================

def build_hourly_data(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    weather = data.get(
        "weather",
        {}
    )

    hourly = weather.get(
        "hourly",
        {}
    )

    hourly_units = weather.get(
        "hourly_units",
        {}
    )

    return {

        "time": hourly.get(
            "time",
            []
        ),

        "temperature": {

            "values": hourly.get(
                "temperature_2m",
                []
            ),

            "unit": hourly_units.get(
                "temperature_2m",
                "°C"
            )
        },

        "precipitation_probability": {

            "values": hourly.get(
                "precipitation_probability",
                []
            ),

            "unit": hourly_units.get(
                "precipitation_probability",
                "%"
            )
        },

        "precipitation": {

            "values": hourly.get(
                "precipitation",
                []
            ),

            "unit": hourly_units.get(
                "precipitation",
                "mm"
            )
        },

        "soil_moisture": {

            "values": hourly.get(
                "soil_moisture_0_to_1cm",
                []
            ),

            "unit": hourly_units.get(
                "soil_moisture_0_to_1cm",
                ""
            )
        },

        "weather_code": {

            "values": hourly.get(
                "weather_code",
                []
            ),

            "unit": ""
        }
    }


# ============================================================
# EXTRACT DAILY INFORMATION
# ============================================================

def build_daily_data(
    data: Dict[str, Any]
) -> Dict[str, Any]:

    weather = data.get(
        "weather",
        {}
    )

    daily = weather.get(
        "daily",
        {}
    )

    daily_units = weather.get(
        "daily_units",
        {}
    )

    return {

        "time": daily.get(
            "time",
            []
        ),

        "sunrise": {

            "values": daily.get(
                "sunrise",
                []
            ),

            "unit": daily_units.get(
                "sunrise",
                ""
            )
        },

        "sunset": {

            "values": daily.get(
                "sunset",
                []
            ),

            "unit": daily_units.get(
                "sunset",
                ""
            )
        },

        "uv_index_max": {

            "values": daily.get(
                "uv_index_max",
                []
            ),

            "unit": daily_units.get(
                "uv_index_max",
                ""
            )
        },

        "precipitation_sum": {

            "values": daily.get(
                "precipitation_sum",
                []
            ),

            "unit": daily_units.get(
                "precipitation_sum",
                "mm"
            )
        },

        "precipitation_probability_max": {

            "values": daily.get(
                "precipitation_probability_max",
                []
            ),

            "unit": daily_units.get(
                "precipitation_probability_max",
                "%"
            )
        },

        "weather_code": {

            "values": daily.get(
                "weather_code",
                []
            ),

            "unit": ""
        }
    }


# ============================================================
# FIND CURRENT HOURLY INDEX
# ============================================================

def get_current_hour_index(
    hourly_times: List[Any],
    current_time: Optional[str]
) -> int:

    if not hourly_times:
        return -1

    if not current_time:
        return 0

    try:

        current_datetime = datetime.fromisoformat(
            str(current_time)
        )

    except (ValueError, TypeError):

        return 0

    best_index = 0

    best_difference = None

    for index, time_value in enumerate(hourly_times):

        try:

            hourly_datetime = datetime.fromisoformat(
                str(time_value)
            )

            difference = abs(
                (
                    hourly_datetime
                    - current_datetime
                ).total_seconds()
            )

            if (
                best_difference is None
                or difference < best_difference
            ):

                best_difference = difference

                best_index = index

        except (ValueError, TypeError):

            continue

    return best_index


# ============================================================
# GET TODAY'S HOURLY VALUES
# ============================================================

def get_today_hourly_indices(
    hourly_times: List[Any],
    current_time: Optional[str]
) -> List[int]:

    if not hourly_times:
        return []

    today_string = None

    if current_time:

        try:

            today_string = str(
                datetime.fromisoformat(
                    str(current_time)
                ).date()
            )

        except (ValueError, TypeError):

            today_string = str(
                hourly_times[0]
            )[:10]

    else:

        today_string = str(
            hourly_times[0]
        )[:10]

    indices = []

    for index, time_value in enumerate(
        hourly_times
    ):

        if str(time_value).startswith(
            today_string
        ):

            indices.append(index)

    return indices


# ============================================================
# WEATHER FORECAST ANALYSIS
# ============================================================

def analyze_forecast(
    complete_data: Dict[str, Any],
    current: Dict[str, Any],
    hourly: Dict[str, Any],
    daily: Dict[str, Any]
) -> Dict[str, Any]:

    hourly_times = hourly.get(
        "time",
        []
    )

    current_time = current.get(
        "time"
    )

    current_index = get_current_hour_index(
        hourly_times,
        current_time
    )

    today_indices = get_today_hourly_indices(
        hourly_times,
        current_time
    )

    # --------------------------------------------------------
    # HOURLY ARRAYS
    # --------------------------------------------------------

    rain_probability_values = (
        hourly.get(
            "precipitation_probability",
            {}
        ).get(
            "values",
            []
        )
    )

    precipitation_values = (
        hourly.get(
            "precipitation",
            {}
        ).get(
            "values",
            []
        )
    )

    temperature_values = (
        hourly.get(
            "temperature",
            {}
        ).get(
            "values",
            []
        )
    )

    soil_moisture_values = (
        hourly.get(
            "soil_moisture",
            {}
        ).get(
            "values",
            []
        )
    )

    weather_code_values = (
        hourly.get(
            "weather_code",
            {}
        ).get(
            "values",
            []
        )
    )

    # --------------------------------------------------------
    # CURRENT HOURLY VALUES
    # --------------------------------------------------------

    current_rain_probability = None

    current_hour_precipitation = None

    current_hour_temperature = None

    current_hour_soil_moisture = None

    current_hour_weather_code = None

    if (
        current_index >= 0
        and current_index < len(
            rain_probability_values
        )
    ):

        current_rain_probability = safe_float(
            rain_probability_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            precipitation_values
        )
    ):

        current_hour_precipitation = safe_float(
            precipitation_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            temperature_values
        )
    ):

        current_hour_temperature = safe_float(
            temperature_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            soil_moisture_values
        )
    ):

        current_hour_soil_moisture = safe_float(
            soil_moisture_values[
                current_index
            ]
        )

    if (
        current_index >= 0
        and current_index < len(
            weather_code_values
        )
    ):

        current_hour_weather_code = safe_int(
            weather_code_values[
                current_index
            ]
        )

    # --------------------------------------------------------
    # TODAY'S REMAINING HOURLY FORECAST
    # --------------------------------------------------------

    remaining_today_indices = [

        index

        for index in today_indices

        if index >= current_index
    ]

    remaining_rain_probabilities = [

        safe_float(
            rain_probability_values[index]
        )

        for index in remaining_today_indices

        if index < len(
            rain_probability_values
        )
    ]

    remaining_precipitation = [

        safe_float(
            precipitation_values[index]
        )

        for index in remaining_today_indices

        if index < len(
            precipitation_values
        )
    ]

    remaining_rain_probabilities = [

        value

        for value in remaining_rain_probabilities

        if value is not None
    ]

    remaining_precipitation = [

        value

        for value in remaining_precipitation

        if value is not None
    ]

    # --------------------------------------------------------
    # TODAY MAX RAIN PROBABILITY
    # --------------------------------------------------------

    today_max_rain_probability = (

        max(
            remaining_rain_probabilities
        )

        if remaining_rain_probabilities

        else None
    )

    # --------------------------------------------------------
    # TODAY TOTAL PRECIPITATION
    # --------------------------------------------------------

    today_hourly_precipitation = sum(
        remaining_precipitation
    )

    # --------------------------------------------------------
    # DAILY VALUES
    # --------------------------------------------------------

    daily_rain_probability_values = (
        daily.get(
            "precipitation_probability_max",
            {}
        ).get(
            "values",
            []
        )
    )

    daily_precipitation_values = (
        daily.get(
            "precipitation_sum",
            {}
        ).get(
            "values",
            []
        )
    )

    daily_weather_codes = (
        daily.get(
            "weather_code",
            {}
        ).get(
            "values",
            []
        )
    )

    today_daily_rain_probability = None

    today_daily_precipitation = None

    today_daily_weather_code = None

    if daily_rain_probability_values:

        today_daily_rain_probability = safe_float(
            daily_rain_probability_values[0]
        )

    if daily_precipitation_values:

        today_daily_precipitation = safe_float(
            daily_precipitation_values[0]
        )

    if daily_weather_codes:

        today_daily_weather_code = safe_int(
            daily_weather_codes[0]
        )

    # --------------------------------------------------------
    # PREFER DAILY FORECAST FOR TODAY
    # --------------------------------------------------------
    #
    # Daily precipitation probability represents the
    # probability during the day and is more meaningful
    # for dashboard cards than only one hourly value.
    #
    # --------------------------------------------------------

    meaningful_today_rain_probability = (
        today_daily_rain_probability
        if today_daily_rain_probability is not None
        else today_max_rain_probability
    )

    meaningful_today_precipitation = (
        today_daily_precipitation
        if today_daily_precipitation is not None
        else today_hourly_precipitation
    )

    # --------------------------------------------------------
    # RAIN EXPECTED LOGIC
    # --------------------------------------------------------

    rain_codes = {
        51,
        53,
        55,
        56,
        57,
        61,
        63,
        65,
        66,
        67,
        80,
        81,
        82,
        95,
        96,
        99
    }

    rain_expected = (

        (
            meaningful_today_rain_probability is not None
            and meaningful_today_rain_probability >= 40
        )

        or

        (
            meaningful_today_precipitation is not None
            and meaningful_today_precipitation > 0.1
        )

        or

        (
            current_hour_weather_code
            in rain_codes
        )

        or

        (
            today_daily_weather_code
            in rain_codes
        )
    )

    # --------------------------------------------------------
    # RAIN INTENSITY
    # --------------------------------------------------------

    if (
        meaningful_today_precipitation is not None
        and meaningful_today_precipitation >= 20
    ):

        rain_intensity = "Heavy"

    elif (
        meaningful_today_precipitation is not None
        and meaningful_today_precipitation >= 5
    ):

        rain_intensity = "Moderate"

    elif rain_expected:

        rain_intensity = "Light / Possible"

    else:

        rain_intensity = "None Expected"

    # --------------------------------------------------------
    # RAIN STATUS
    # --------------------------------------------------------

    if (
        meaningful_today_rain_probability is None
    ):

        rain_status = "Forecast unavailable"

    elif (
        meaningful_today_rain_probability >= 70
    ):

        rain_status = "High chance of rain"

    elif (
        meaningful_today_rain_probability >= 40
    ):

        rain_status = "Possible rain"

    elif (
        meaningful_today_rain_probability >= 20
    ):

        rain_status = "Low chance of rain"

    else:

        rain_status = "Very low chance of rain"

    # --------------------------------------------------------
    # SOIL MOISTURE
    # --------------------------------------------------------

    soil_moisture_status = "Unavailable"

    if current_hour_soil_moisture is not None:

        if current_hour_soil_moisture < 0.15:

            soil_moisture_status = "Low"

        elif current_hour_soil_moisture < 0.30:

            soil_moisture_status = "Moderate"

        elif current_hour_soil_moisture < 0.45:

            soil_moisture_status = "Good"

        else:

            soil_moisture_status = "High"

    # --------------------------------------------------------
    # TEMPERATURE STATUS
    # --------------------------------------------------------

    temperature = safe_float(
        current.get(
            "temperature"
        )
    )

    if temperature is None:

        temperature_status = "Unavailable"

    elif temperature >= 40:

        temperature_status = "Extreme heat"

    elif temperature >= 35:

        temperature_status = "Very hot"

    elif temperature >= 30:

        temperature_status = "Warm"

    elif temperature >= 20:

        temperature_status = "Comfortable"

    elif temperature >= 10:

        temperature_status = "Cool"

    else:

        temperature_status = "Cold"

    # --------------------------------------------------------
    # HUMIDITY STATUS
    # --------------------------------------------------------

    humidity = safe_float(
        current.get(
            "humidity"
        )
    )

    if humidity is None:

        humidity_status = "Unavailable"

    elif humidity >= 85:

        humidity_status = "Very high"

    elif humidity >= 70:

        humidity_status = "High"

    elif humidity >= 40:

        humidity_status = "Comfortable"

    else:

        humidity_status = "Low"

    # --------------------------------------------------------
    # WIND STATUS
    # --------------------------------------------------------

    wind_speed = safe_float(
        current.get(
            "wind_speed"
        )
    )

    if wind_speed is None:

        wind_status = "Unavailable"

    elif wind_speed >= 50:

        wind_status = "Very strong"

    elif wind_speed >= 30:

        wind_status = "Strong"

    elif wind_speed >= 15:

        wind_status = "Moderate"

    else:

        wind_status = "Light"

    # --------------------------------------------------------
    # UV STATUS
    # --------------------------------------------------------

    uv_index = safe_float(
        current.get(
            "uv_index"
        )
    )

    if uv_index is None:

        uv_status = "Unavailable"

    elif uv_index >= 11:

        uv_status = "Extreme"

    elif uv_index >= 8:

        uv_status = "Very high"

    elif uv_index >= 6:

        uv_status = "High"

    elif uv_index >= 3:

        uv_status = "Moderate"

    else:

        uv_status = "Low"

    # --------------------------------------------------------
    # AQI STATUS
    # --------------------------------------------------------

    aqi = safe_float(
        current.get(
            "air_quality",
            {}
        ).get(
            "us_aqi"
        )
    )

    if aqi is None:

        aqi_status = "Unavailable"

    elif aqi <= 50:

        aqi_status = "Good"

    elif aqi <= 100:

        aqi_status = "Moderate"

    elif aqi <= 150:

        aqi_status = "Unhealthy for sensitive groups"

    elif aqi <= 200:

        aqi_status = "Unhealthy"

    else:

        aqi_status = "Poor"

    return {

        "current_hour_index": current_index,

        "current_hour_rain_probability":
            current_rain_probability,

        "current_hour_precipitation":
            current_hour_precipitation,

        "current_hour_temperature":
            current_hour_temperature,

        "current_hour_soil_moisture":
            current_hour_soil_moisture,

        "current_hour_weather_code":
            current_hour_weather_code,

        "today_rain_probability":
            meaningful_today_rain_probability,

        "today_max_hourly_rain_probability":
            today_max_rain_probability,

        "today_precipitation":
            meaningful_today_precipitation,

        "rain_expected":
            rain_expected,

        "rain_status":
            rain_status,

        "rain_intensity":
            rain_intensity,

        "temperature_status":
            temperature_status,

        "humidity_status":
            humidity_status,

        "wind_status":
            wind_status,

        "uv_status":
            uv_status,

        "aqi_status":
            aqi_status,

        "soil_moisture_status":
            soil_moisture_status
    }


# ============================================================
# ROLE VALIDATION
# ============================================================

ROLE_MAP = {

    "traveler": "Traveler",

    "traveller": "Traveler",

    "farmer": "Farmer",

    "athlete": "Athlete",

    "public": "Public"
}


def normalize_role(
    role: str
) -> str:

    normalized = role.strip().lower()

    if normalized not in ROLE_MAP:

        raise HTTPException(
            status_code=400,
            detail=(
                "Invalid role. Allowed roles: "
                "Traveler, Farmer, Athlete, Public."
            )
        )

    return ROLE_MAP[
        normalized
    ]


# ============================================================
# ROLE-SPECIFIC DASHBOARD
# ============================================================

def build_role_dashboard(
    role: str,
    location: Dict[str, Any],
    complete_data: Dict[str, Any]
) -> Dict[str, Any]:

    role = normalize_role(
        role
    )

    current = build_current_weather(
        complete_data
    )

    hourly = build_hourly_data(
        complete_data
    )

    daily = build_daily_data(
        complete_data
    )

    analysis = analyze_forecast(
        complete_data,
        current,
        hourly,
        daily
    )

    temperature = safe_float(
        current.get(
            "temperature"
        )
    )

    humidity = safe_float(
        current.get(
            "humidity"
        )
    )

    precipitation = safe_float(
        current.get(
            "precipitation"
        )
    )

    wind_speed = safe_float(
        current.get(
            "wind_speed"
        )
    )

    uv_index = safe_float(
        current.get(
            "uv_index"
        )
    )

    aqi_data = current.get(
        "air_quality",
        {}
    )

    aqi = safe_float(
        aqi_data.get(
            "us_aqi"
        )
    )

    today_rain_probability = analysis.get(
        "today_rain_probability"
    )

    today_precipitation = analysis.get(
        "today_precipitation"
    )

    rain_expected = analysis.get(
        "rain_expected"
    )

    rain_status = analysis.get(
        "rain_status"
    )

    # ========================================================
    # TRAVELER
    # ========================================================

    if role == "Traveler":

        if rain_expected:

            travel_rain_advice = (
                "Rain is possible today. "
                "Keep an umbrella or rain protection."
            )

        else:

            travel_rain_advice = (
                "No significant rain is expected "
                "based on the current forecast."
            )

        if (
            wind_speed is not None
            and wind_speed >= 30
        ):

            wind_advice = (
                "Strong winds may affect outdoor "
                "travel and open-area activities."
            )

        else:

            wind_advice = (
                "Wind conditions are generally "
                "manageable for outdoor travel."
            )

        if (
            uv_index is not None
            and uv_index >= 6
        ):

            uv_advice = (
                "UV exposure is high. "
                "Use sun protection during daytime."
            )

        else:

            uv_advice = (
                "UV conditions are relatively manageable."
            )

        return {

            "role": "Traveler",

            "dashboard_title":
                "Traveler Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "weather":
                    current.get(
                        "weather_description"
                    ),

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "today_precipitation":
                    today_precipitation,

                "wind_speed":
                    wind_speed,

                "uv_index":
                    uv_index,

                "air_quality":
                    aqi,

                "aqi_category":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "travel_insights": {

                "rain_expected":
                    rain_expected,

                "rain_advice":
                    travel_rain_advice,

                "high_uv":
                    (
                        uv_index is not None
                        and uv_index >= 6
                    ),

                "uv_advice":
                    uv_advice,

                "strong_wind":
                    (
                        wind_speed is not None
                        and wind_speed >= 30
                    ),

                "wind_advice":
                    wind_advice,

                "air_quality_status":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Destination Weather",

                "Today's Rain Probability",

                "7-Day Forecast",

                "Rain Forecast",

                "UV Index",

                "Wind Information",

                "Air Quality",

                "City Comparison"
            ]
        }

    # ========================================================
    # FARMER
    # ========================================================

    if role == "Farmer":

        soil_moisture = analysis.get(
            "current_hour_soil_moisture"
        )

        soil_status = analysis.get(
            "soil_moisture_status"
        )

        if rain_expected:

            farming_rain_advice = (
                "Rain is expected or possible today. "
                "Consider rainfall before irrigation."
            )

        else:

            farming_rain_advice = (
                "No significant rain is currently "
                "expected today. Irrigation planning "
                "may be required based on soil condition."
            )

        if soil_status == "Low":

            irrigation_advice = (
                "Soil moisture is low. "
                "Check crop water requirements."
            )

        elif soil_status == "Moderate":

            irrigation_advice = (
                "Soil moisture is moderate. "
                "Monitor before irrigation."
            )

        elif soil_status == "Good":

            irrigation_advice = (
                "Soil moisture is in a generally "
                "favorable range."
            )

        elif soil_status == "High":

            irrigation_advice = (
                "Soil moisture is high. "
                "Avoid unnecessary irrigation."
            )

        else:

            irrigation_advice = (
                "Soil moisture data is unavailable."
            )

        return {

            "role": "Farmer",

            "dashboard_title":
                "Farmer Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "temperature_status":
                    analysis.get(
                        "temperature_status"
                    ),

                "humidity":
                    humidity,

                "humidity_status":
                    analysis.get(
                        "humidity_status"
                    ),

                "soil_moisture":
                    soil_moisture,

                "soil_moisture_status":
                    soil_status,

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "precipitation":
                    precipitation,

                "today_precipitation":
                    today_precipitation,

                "wind_speed":
                    wind_speed,

                "wind_status":
                    analysis.get(
                        "wind_status"
                    ),

                "uv_index":
                    uv_index,

                "uv_status":
                    analysis.get(
                        "uv_status"
                    )
            },

            "farming_insights": {

                "rain_expected":
                    rain_expected,

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "rain_intensity":
                    analysis.get(
                        "rain_intensity"
                    ),

                "rain_advice":
                    farming_rain_advice,

                "soil_moisture_available":
                    soil_moisture is not None,

                "soil_moisture_status":
                    soil_status,

                "irrigation_advice":
                    irrigation_advice,

                "high_temperature":
                    (
                        temperature is not None
                        and temperature >= 35
                    ),

                "high_humidity":
                    (
                        humidity is not None
                        and humidity >= 80
                    ),

                "strong_wind":
                    (
                        wind_speed is not None
                        and wind_speed >= 30
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Temperature",

                "Humidity",

                "Soil Moisture",

                "Today's Rain Probability",

                "Rain Forecast",

                "Today's Precipitation",

                "Wind Speed",

                "UV Index",

                "Sunrise & Sunset",

                "7-Day Weather Forecast"
            ]
        }

    # ========================================================
    # ATHLETE
    # ========================================================

    if role == "Athlete":

        high_heat = (
            temperature is not None
            and temperature >= 35
        )

        high_humidity = (
            humidity is not None
            and humidity >= 80
        )

        strong_wind = (
            wind_speed is not None
            and wind_speed >= 30
        )

        high_uv = (
            uv_index is not None
            and uv_index >= 6
        )

        poor_air_quality = (
            aqi is not None
            and aqi > 100
        )

        # ----------------------------------------------------
        # OUTDOOR ACTIVITY STATUS
        # ----------------------------------------------------

        risk_count = sum([
            high_heat,
            high_humidity,
            strong_wind,
            high_uv,
            poor_air_quality,
            rain_expected
        ])

        if risk_count >= 4:

            activity_status = (
                "Challenging outdoor conditions"
            )

        elif risk_count >= 2:

            activity_status = (
                "Use caution for outdoor activity"
            )

        else:

            activity_status = (
                "Generally suitable outdoor conditions"
            )

        if rain_expected:

            athlete_rain_advice = (
                "Rain is possible today. "
                "Check the hourly forecast before outdoor training."
            )

        else:

            athlete_rain_advice = (
                "No significant rain is currently expected."
            )

        return {

            "role": "Athlete",

            "dashboard_title":
                "Athlete Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "temperature_status":
                    analysis.get(
                        "temperature_status"
                    ),

                "humidity":
                    humidity,

                "humidity_status":
                    analysis.get(
                        "humidity_status"
                    ),

                "wind_speed":
                    wind_speed,

                "wind_status":
                    analysis.get(
                        "wind_status"
                    ),

                "uv_index":
                    uv_index,

                "uv_status":
                    analysis.get(
                        "uv_status"
                    ),

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "air_quality":
                    aqi,

                "aqi_category":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "activity_conditions": {

                "activity_status":
                    activity_status,

                "rain_expected":
                    rain_expected,

                "rain_advice":
                    athlete_rain_advice,

                "high_heat":
                    high_heat,

                "high_humidity":
                    high_humidity,

                "strong_wind":
                    strong_wind,

                "high_uv":
                    high_uv,

                "poor_air_quality":
                    poor_air_quality,

                "aqi_status":
                    analysis.get(
                        "aqi_status"
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Outdoor Activity Conditions",

                "Temperature",

                "Humidity",

                "Wind Speed",

                "UV Index",

                "Today's Rain Probability",

                "Rain Forecast",

                "Air Quality",

                "7-Day Forecast"
            ]
        }

    # ========================================================
    # PUBLIC
    # ========================================================

    if role == "Public":

        if rain_expected:

            outdoor_advice = (
                "Rain is possible today. "
                "Carry an umbrella or rain protection."
            )

        else:

            outdoor_advice = (
                "No significant rain is currently expected."
            )

        return {

            "role": "Public",

            "dashboard_title":
                "Daily Weather Dashboard",

            "location": location,

            "focus": {

                "temperature":
                    temperature,

                "weather":
                    current.get(
                        "weather_description"
                    ),

                "humidity":
                    humidity,

                "rain_probability":
                    today_rain_probability,

                "rain_status":
                    rain_status,

                "today_precipitation":
                    today_precipitation,

                "wind_speed":
                    wind_speed,

                "uv_index":
                    uv_index,

                "air_quality":
                    aqi,

                "aqi_category":
                    aqi_data.get(
                        "aqi_category"
                    )
            },

            "daily_information": {

                "sunrise":
                    daily[
                        "sunrise"
                    ][
                        "values"
                    ][0]
                    if daily[
                        "sunrise"
                    ][
                        "values"
                    ]
                    else None,

                "sunset":
                    daily[
                        "sunset"
                    ][
                        "values"
                    ][0]
                    if daily[
                        "sunset"
                    ][
                        "values"
                    ]
                    else None,

                "today_rain_probability":
                    today_rain_probability,

                "today_precipitation":
                    today_precipitation,

                "rain_status":
                    rain_status
            },

            "outdoor_conditions": {

                "rain_expected":
                    rain_expected,

                "rain_advice":
                    outdoor_advice,

                "uv_status":
                    analysis.get(
                        "uv_status"
                    ),

                "wind_status":
                    analysis.get(
                        "wind_status"
                    ),

                "aqi_status":
                    analysis.get(
                        "aqi_status"
                    )
            },

            "forecast_analysis":
                analysis,

            "current_weather":
                current,

            "forecast": {

                "hourly":
                    hourly,

                "daily":
                    daily
            },

            "features": [

                "Current Weather",

                "Temperature",

                "Humidity",

                "Today's Rain Probability",

                "Rain Forecast",

                "Today's Precipitation",

                "Wind Speed",

                "UV Index",

                "Air Quality",

                "7-Day Forecast",

                "Sunrise & Sunset"
            ]
        }

    raise HTTPException(
        status_code=400,
        detail="Unsupported role."
    )


# ============================================================
# ROOT
# ============================================================

@app.get("/")
async def root():

    return {

        "success": True,

        "application":
            "SIH Weather Web Application",

        "version":
            "2.1.0",

        "message":
            "Weather backend is running.",

        "roles": [

            "Traveler",

            "Farmer",

            "Athlete",

            "Public"
        ],

        "endpoints": {

            "login":
                "POST /api/login",

            "search_city":
                "GET /api/search-city?city=Mumbai",

            "weather":
                "GET /api/weather?lat=19.0760&lon=72.8777",

            "dashboard":
                "GET /api/dashboard"
                "?role=Farmer&lat=19.0760&lon=72.8777",

            "compare":
                "GET /api/compare"
                "?city1=Mumbai&city2=Pune",

            "health":
                "GET /health",

            "docs":
                "/docs"
        }
    }


# ============================================================
# HEALTH CHECK
# ============================================================

@app.get("/health")
async def health():

    return {

        "status":
            "healthy",

        "service":
            "SIH Weather API",

        "version":
            "2.1.0"
    }


# ============================================================
# LOGIN
# ============================================================

@app.post("/api/login")
async def login(
    request: LoginRequest
):

    username = (
        request.username
        .strip()
        .lower()
    )

    user = DEMO_USERS.get(
        username
    )

    if user is None:

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password."
        )

    if request.password != user["password"]:

        raise HTTPException(
            status_code=401,
            detail="Invalid username or password."
        )

    return {

        "success": True,

        "user": {

            "username":
                username,

            "name":
                user["name"],

            "role":
                user["role"]
        }
    }


# ============================================================
# SEARCH CITY
# ============================================================

@app.get("/api/search-city")
async def search_city(
    city: str = Query(
        ...,
        min_length=1,
        description="City name"
    )
):

    location = await geocode_city(
        city
    )

    return {

        "success": True,

        "location":
            location
    }


# ============================================================
# WEATHER
# ============================================================

@app.get("/api/weather")
async def weather(

    lat: float = Query(
        ...,
        description="Latitude"
    ),

    lon: float = Query(
        ...,
        description="Longitude"
    )
):

    if not -90 <= lat <= 90:

        raise HTTPException(
            status_code=400,
            detail="Invalid latitude."
        )

    if not -180 <= lon <= 180:

        raise HTTPException(
            status_code=400,
            detail="Invalid longitude."
        )

    data = await fetch_complete_weather(
        lat,
        lon
    )

    return {

        "success": True,

        "latitude":
            lat,

        "longitude":
            lon,

        **data
    }


# ============================================================
# ROLE-BASED DASHBOARD
# ============================================================

@app.get("/api/dashboard")
async def dashboard(

    role: str = Query(
        ...,
        description=(
            "Traveler, Farmer, Athlete or Public"
        )
    ),

    lat: float = Query(
        ...,
        description="Latitude"
    ),

    lon: float = Query(
        ...,
        description="Longitude"
    )
):

    if not -90 <= lat <= 90:

        raise HTTPException(
            status_code=400,
            detail="Invalid latitude."
        )

    if not -180 <= lon <= 180:

        raise HTTPException(
            status_code=400,
            detail="Invalid longitude."
        )

    normalized_role = normalize_role(
        role
    )

    complete_data = (
        await fetch_complete_weather(
            lat,
            lon
        )
    )

    location = {

        "latitude":
            lat,

        "longitude":
            lon
    }

    dashboard_data = (
        build_role_dashboard(
            normalized_role,
            location,
            complete_data
        )
    )

    return {

        "success": True,

        "dashboard":
            dashboard_data
    }


# ============================================================
# COMPARE TWO CITIES
# ============================================================

@app.get("/api/compare")
async def compare(

    city1: str = Query(
        ...,
        min_length=1,
        description="First city"
    ),

    city2: str = Query(
        ...,
        min_length=1,
        description="Second city"
    )
):

    # --------------------------------------------------------
    # GEOCODE BOTH CITIES IN PARALLEL
    # --------------------------------------------------------

    try:

        location1, location2 = await asyncio.gather(

            geocode_city(
                city1
            ),

            geocode_city(
                city2
            )
        )

    except HTTPException:

        raise

    # --------------------------------------------------------
    # FETCH BOTH WEATHER DATASETS IN PARALLEL
    # --------------------------------------------------------

    try:

        data1, data2 = await asyncio.gather(

            fetch_complete_weather(
                location1["latitude"],
                location1["longitude"]
            ),

            fetch_complete_weather(
                location2["latitude"],
                location2["longitude"]
            )
        )

    except HTTPException:

        raise

    # --------------------------------------------------------
    # RETURN COMPARISON
    # --------------------------------------------------------

    return {

        "success": True,

        "city1": {

            "location":
                location1,

            "weather":
                data1
        },

        "city2": {

            "location":
                location2,

            "weather":
                data2
        }
    }


# ============================================================
# RUN DIRECTLY
# ============================================================

if __name__ == "__main__":

    import os
    import uvicorn

    port = int(
        os.environ.get(
            "PORT",
            "8080"
        )
    )

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=port,
        reload=False
    )
