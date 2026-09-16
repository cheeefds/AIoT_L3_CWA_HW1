from __future__ import annotations

import sqlite3

import pytest

import ai_service
from ai_service import build_weather_prompt, generate_weather_advice
from app import make_temperature_chart, make_weather_map, temperature_color
from cwa_api import CWAError, fetch_cwa_forecast, parse_cwa_forecast
from database import get_weather_by_region, initialize_database, insert_forecasts


@pytest.fixture
def cwa_payload() -> dict:
    """欄位順序刻意打亂，並混用 list/dict value，避免測試只通過固定索引。"""
    periods = [
        ("2026-09-17 06:00:00", "2026-09-17 18:00:00"),
        ("2026-09-17 18:00:00", "2026-09-18 06:00:00"),
    ]

    def element(name: str, values: list[object]) -> dict:
        return {
            "elementName": name,
            "time": [
                {"startTime": start, "endTime": end, "elementValue": value}
                for (start, end), value in zip(periods, values)
            ],
        }

    return {
        "success": "true",
        "records": {
            "locations": [{
                "datasetDescription": "一週縣市天氣預報",
                "location": [{
                    "locationName": "臺中市",
                    "weatherElement": [
                        element("Wx", [[{"value": "多雲短暫雨"}], [{"value": "多雲"}]]),
                        element("RH", [{"value": "78"}, {"value": "70"}]),
                        element("MaxT", [[{"value": "31"}], [{"value": "29"}]]),
                        element("PoP12h", [[{"value": "60"}], [{"value": "30"}]]),
                        element("MinT", [[{"value": "25"}], [{"value": "24"}]]),
                    ],
                }],
            }],
        },
    }


def test_parse_expected_fields(cwa_payload: dict) -> None:
    frame = parse_cwa_forecast(cwa_payload)
    assert list(frame["regionName"].unique()) == ["臺中市"]
    assert frame.loc[0, "minTemp"] == 25
    assert frame.loc[0, "maxTemp"] == 31
    assert frame.loc[0, "rainProbability"] == 60
    assert frame.loc[0, "humidity"] == 78
    assert frame.loc[0, "weather"] == "多雲短暫雨"


def test_sqlite_insert_query_and_dedup(tmp_path, cwa_payload: dict) -> None:
    db_path = tmp_path / "test.db"
    frame = parse_cwa_forecast(cwa_payload)
    initialize_database(db_path)
    insert_forecasts(frame, db_path)
    insert_forecasts(frame, db_path)

    result = get_weather_by_region("臺中市", db_path)
    assert len(result) == 2
    with sqlite3.connect(db_path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM WeatherForecasts").fetchone()[0]
    assert count == 2


def test_missing_cwa_key_is_friendly_error() -> None:
    with pytest.raises(CWAError, match="CWA_API_KEY"):
        fetch_cwa_forecast("")


def test_changed_json_shape_is_friendly_error() -> None:
    with pytest.raises(CWAError, match="location"):
        parse_cwa_forecast({"success": "true", "records": {}})


def test_chart_and_map_follow_dataframe(cwa_payload: dict) -> None:
    frame = parse_cwa_forecast(cwa_payload)
    chart = make_temperature_chart(frame)
    chart_spec = chart.to_dict()
    assert len(chart_spec["layer"]) == 3
    assert "最低溫" in str(chart_spec)
    assert "最高溫" in str(chart_spec)

    latest = frame.iloc[[0]].copy()
    weather_map = make_weather_map(latest)
    assert weather_map.get_root().render().find("臺中市") >= 0
    assert temperature_color(25, 31) == "orange"


def test_hugging_face_prompt_and_response_parsing(monkeypatch) -> None:
    weather = {
        "regionName": "臺中市", "dataDate": "2026-09-17", "minTemp": 25,
        "maxTemp": 31, "rainProbability": 60, "humidity": 78,
        "weather": "午後短暫雷陣雨",
    }
    assert "不要自行補充預報數字" in build_weather_prompt(weather)

    class FakeMessage:
        content = "天氣偏熱且可能下雨，建議穿著透氣衣物並攜帶雨具。"

    class FakeChoice:
        message = FakeMessage()

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            assert kwargs["model"] == "test/model"
            return type("Result", (), {"choices": [FakeChoice()]})()

    class FakeClient:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "hf_test"
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(ai_service, "InferenceClient", FakeClient)
    result = generate_weather_advice(weather, token="hf_test", model="test/model")
    assert "攜帶雨具" in result


def test_hugging_face_falls_back_when_model_is_not_supported(monkeypatch) -> None:
    called_models: list[str] = []

    class FakeMessage:
        content = "備援模型成功產生天氣建議。"

    class FakeCompletions:
        @staticmethod
        def create(**kwargs):
            called_models.append(kwargs["model"])
            if kwargs["model"] == "old/model":
                raise RuntimeError(
                    "model_not_supported: not supported by any provider you have enabled"
                )
            return type(
                "Result",
                (),
                {"choices": [type("Choice", (), {"message": FakeMessage()})()]},
            )()

    class FakeClient:
        def __init__(self, **kwargs):
            self.chat = type("Chat", (), {"completions": FakeCompletions()})()

    monkeypatch.setattr(ai_service, "InferenceClient", FakeClient)
    result = generate_weather_advice(
        {"regionName": "臺中市"},
        token="hf_test",
        model="old/model",
        fallback_models=("new/model",),
    )
    assert result == "備援模型成功產生天氣建議。"
    assert called_models == ["old/model", "new/model"]


def test_parse_current_cwa_named_value_shape() -> None:
    """官方新版範例使用大寫節點與 MaxTemperature 等具名值。"""
    start, end = "2026-09-17T06:00:00+08:00", "2026-09-17T18:00:00+08:00"

    def element(name: str, value_key: str, value: str) -> dict:
        return {
            "ElementName": name,
            "Time": [{
                "StartTime": start,
                "EndTime": end,
                "ElementValue": [{value_key: value, "Measures": "自動略過"}],
            }],
        }

    payload = {
        "success": "true",
        "records": {
            "Locations": [{
                "Location": [{
                    "LocationName": "臺北市",
                    "WeatherElement": [
                        element("最高溫度", "MaxTemperature", "30"),
                        element("最低溫度", "MinTemperature", "23"),
                        element("天氣現象", "Weather", "多雲"),
                        element("12小時降雨機率", "ProbabilityOfPrecipitation", "40"),
                        element("平均相對濕度", "RelativeHumidity", "72"),
                    ],
                }],
            }],
        },
    }
    row = parse_cwa_forecast(payload).iloc[0]
    assert (row["minTemp"], row["maxTemp"]) == (23, 30)
    assert (row["rainProbability"], row["humidity"]) == (40, 72)
    assert row["weather"] == "多雲"
