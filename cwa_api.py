"""中央氣象署 API 存取與 JSON 正規化。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pandas as pd
import requests

from config import CWA_API_URL, REQUEST_TIMEOUT


COLUMNS = [
    "regionName",
    "dataDate",
    "startTime",
    "endTime",
    "minTemp",
    "maxTemp",
    "weather",
    "rainProbability",
    "humidity",
]

ELEMENT_ALIASES = {
    "最高溫度": "MaxT",
    "最低溫度": "MinT",
    "天氣現象": "Wx",
    "12小時降雨機率": "PoP12h",
    "降雨機率": "PoP",
    "平均相對濕度": "RH",
    "相對濕度": "RH",
}

VALUE_KEYS = {
    "MaxT": ("MaxTemperature",),
    "MinT": ("MinTemperature",),
    "Wx": ("Weather",),
    "PoP12h": ("ProbabilityOfPrecipitation",),
    "PoP": ("ProbabilityOfPrecipitation",),
    "PoP6h": ("ProbabilityOfPrecipitation",),
    "RH": ("RelativeHumidity",),
}


class CWAError(RuntimeError):
    """CWA 下載或資料解析失敗。"""


def fetch_cwa_forecast(api_key: str, timeout: int = REQUEST_TIMEOUT) -> dict[str, Any]:
    """下載 CWA 一週縣市預報 JSON。"""
    if not api_key or api_key.startswith("YOUR_") or api_key.startswith("your_"):
        raise CWAError("找不到有效的 CWA_API_KEY，請先設定 .env。")

    try:
        response = requests.get(
            CWA_API_URL,
            params={"Authorization": api_key, "format": "JSON"},
            timeout=timeout,
        )
        response.raise_for_status()
    except requests.Timeout as exc:
        raise CWAError(f"CWA API 連線逾時（{timeout} 秒）。") from exc
    except requests.RequestException as exc:
        raise CWAError(f"CWA API 請求失敗：{exc}") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise CWAError("CWA API 回傳內容不是有效的 JSON。") from exc

    if not isinstance(payload, dict):
        raise CWAError("CWA JSON 最外層格式不正確。")
    if payload.get("success") is False or str(payload.get("success", "")).lower() == "false":
        message = payload.get("message") or payload.get("result", {}).get("resource_id")
        raise CWAError(f"CWA API 回報失敗：{message or '請檢查 API Key 與 Dataset'}")
    return payload


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def _first(mapping: Any, *keys: str) -> Any:
    if not isinstance(mapping, dict):
        return None
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _extract_locations(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """相容 REST API 與下載檔常見的兩種 JSON 包裝。"""
    records = payload.get("records")
    candidates: list[Any] = []

    if isinstance(records, dict):
        for group in _as_list(records.get("locations") or records.get("Locations")):
            if isinstance(group, dict):
                candidates.extend(_as_list(group.get("location") or group.get("Location")))
        candidates.extend(_as_list(records.get("location") or records.get("Location")))

    dataset = payload.get("cwaopendata", {}).get("dataset", {})
    if isinstance(dataset, dict):
        for group in _as_list(dataset.get("locations")):
            if isinstance(group, dict):
                candidates.extend(_as_list(group.get("location")))

    locations = [item for item in candidates if isinstance(item, dict)]
    if not locations:
        raise CWAError("CWA JSON 中找不到 location；資料格式可能已變更。")
    return locations


def _extract_value(raw_value: Any, element_name: str = "") -> Any:
    """取出 elementValue/parameter 中真正的值，不依賴固定形狀。"""
    if isinstance(raw_value, list):
        for item in raw_value:
            value = _extract_value(item, element_name)
            if value not in (None, ""):
                return value
        return None
    if isinstance(raw_value, dict):
        preferred_keys = VALUE_KEYS.get(element_name, ())
        direct = _first(
            raw_value,
            *preferred_keys,
            "value",
            "Value",
            "parameterName",
            "ParameterName",
        )
        if direct is not None:
            return direct
        for key in ("elementValue", "ElementValue", "parameter", "Parameter"):
            if key in raw_value:
                return _extract_value(raw_value[key], element_name)
        # 新版 CWA ElementValue 常以 Temperature 等語意名稱存值。
        # 僅在沒有明確對應時，取第一個非單位欄位的純量值。
        for key, value in raw_value.items():
            if key.lower() not in {"measures", "unit", "description"} and not isinstance(value, (dict, list)):
                return value
        return None
    return raw_value


def _number(value: Any) -> float | None:
    try:
        if value in (None, "", "-", "None"):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _element_entries(location: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    output: dict[str, list[dict[str, Any]]] = {}
    elements = _first(location, "weatherElement", "WeatherElement")
    for element in _as_list(elements):
        name = _first(element, "elementName", "ElementName")
        if not name:
            continue
        canonical_name = ELEMENT_ALIASES.get(str(name), str(name))
        entries: list[dict[str, Any]] = []
        for period in _as_list(_first(element, "time", "Time")):
            if not isinstance(period, dict):
                continue
            start = _first(period, "startTime", "StartTime", "dataTime", "DataTime")
            end = _first(period, "endTime", "EndTime") or start
            raw = _first(period, "elementValue", "ElementValue", "parameter", "Parameter")
            entries.append({
                "start": start,
                "end": end,
                "value": _extract_value(raw, canonical_name),
            })
        output[canonical_name] = entries
    return output


def _matching_value(
    entries: list[dict[str, Any]], start: str, end: str
) -> Any:
    """先比對相同區間，再用時間中點找涵蓋該時段的資料。"""
    for entry in entries:
        if entry["start"] == start and entry["end"] == end:
            return entry["value"]

    start_dt, end_dt = _parse_time(start), _parse_time(end)
    if not start_dt:
        return None
    midpoint = start_dt if not end_dt else start_dt + (end_dt - start_dt) / 2
    for entry in entries:
        entry_start = _parse_time(entry["start"])
        entry_end = _parse_time(entry["end"])
        if entry_start and entry_end and entry_start <= midpoint <= entry_end:
            return entry["value"]
    return None


def parse_cwa_forecast(payload: dict[str, Any]) -> pd.DataFrame:
    """將 CWA JSON 整理成每個地區、每個預報時段一列的 DataFrame。"""
    rows: list[dict[str, Any]] = []
    for location in _extract_locations(payload):
        region = _first(location, "locationName", "LocationName", "regionName")
        if not region:
            continue
        elements = _element_entries(location)
        # CWA 一週預報中溫度為主要 12 小時區間；若缺少，改用其他因子的區間。
        anchors = elements.get("MinT") or elements.get("MaxT")
        if not anchors:
            anchors = next((elements.get(name) for name in ("Wx", "PoP12h", "PoP", "RH") if elements.get(name)), [])

        for anchor in anchors:
            start, end = anchor.get("start"), anchor.get("end")
            if not start:
                continue
            pop_entries = elements.get("PoP12h") or elements.get("PoP") or elements.get("PoP6h") or []
            rows.append(
                {
                    "regionName": str(region),
                    "dataDate": str(start)[:10],
                    "startTime": str(start),
                    "endTime": str(end or start),
                    "minTemp": _number(_matching_value(elements.get("MinT", []), start, end)),
                    "maxTemp": _number(_matching_value(elements.get("MaxT", []), start, end)),
                    "weather": _matching_value(elements.get("Wx", []), start, end),
                    "rainProbability": _number(_matching_value(pop_entries, start, end)),
                    "humidity": _number(_matching_value(elements.get("RH", []), start, end)),
                }
            )

    if not rows:
        raise CWAError("找得到 location，但沒有可用的預報時段或 weatherElement。")

    frame = pd.DataFrame(rows, columns=COLUMNS)
    frame = frame.drop_duplicates(subset=["regionName", "startTime", "endTime"], keep="last")
    return frame.sort_values(["regionName", "startTime"]).reset_index(drop=True)


def download_forecast_dataframe(api_key: str) -> pd.DataFrame:
    """下載並解析 CWA 預報。"""
    return parse_cwa_forecast(fetch_cwa_forecast(api_key))
