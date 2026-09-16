"""SQLite 資料庫建立、寫入與查詢。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pandas as pd

from config import DATABASE_PATH


class DatabaseError(RuntimeError):
    """資料庫操作失敗。"""


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS WeatherForecasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    regionName TEXT NOT NULL,
    dataDate TEXT NOT NULL,
    startTime TEXT NOT NULL,
    endTime TEXT NOT NULL,
    minTemp REAL,
    maxTemp REAL,
    weather TEXT,
    rainProbability REAL,
    humidity REAL,
    UNIQUE(regionName, startTime, endTime)
)
"""


def _connect(db_path: str | Path = DATABASE_PATH) -> sqlite3.Connection:
    connection = sqlite3.connect(str(db_path), timeout=10)
    connection.row_factory = sqlite3.Row
    return connection


def initialize_database(db_path: str | Path = DATABASE_PATH) -> None:
    """第一次執行時自動建立 data.db 與資料表。"""
    try:
        with _connect(db_path) as connection:
            connection.execute(CREATE_TABLE_SQL)
    except sqlite3.Error as exc:
        raise DatabaseError(f"無法建立資料庫：{exc}") from exc


def _clean_value(value: Any) -> Any:
    return None if pd.isna(value) else value


def insert_forecasts(frame: pd.DataFrame, db_path: str | Path = DATABASE_PATH) -> int:
    """以預報地區與時段為唯一鍵寫入；相同時段會更新而不會重複新增。"""
    if frame.empty:
        return 0
    required = {
        "regionName", "dataDate", "startTime", "endTime", "minTemp",
        "maxTemp", "weather", "rainProbability", "humidity",
    }
    missing = required.difference(frame.columns)
    if missing:
        raise DatabaseError(f"DataFrame 缺少必要欄位：{', '.join(sorted(missing))}")

    sql = """
    INSERT INTO WeatherForecasts (
        regionName, dataDate, startTime, endTime, minTemp, maxTemp,
        weather, rainProbability, humidity
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(regionName, startTime, endTime) DO UPDATE SET
        dataDate = excluded.dataDate,
        minTemp = excluded.minTemp,
        maxTemp = excluded.maxTemp,
        weather = excluded.weather,
        rainProbability = excluded.rainProbability,
        humidity = excluded.humidity
    """
    values = [
        tuple(_clean_value(row[column]) for column in [
            "regionName", "dataDate", "startTime", "endTime", "minTemp",
            "maxTemp", "weather", "rainProbability", "humidity",
        ])
        for _, row in frame.iterrows()
    ]
    try:
        initialize_database(db_path)
        with _connect(db_path) as connection:
            before = connection.total_changes
            connection.executemany(sql, values)
            return connection.total_changes - before
    except sqlite3.Error as exc:
        raise DatabaseError(f"寫入天氣資料失敗：{exc}") from exc


def get_regions(db_path: str | Path = DATABASE_PATH) -> list[str]:
    try:
        initialize_database(db_path)
        with _connect(db_path) as connection:
            rows = connection.execute(
                "SELECT DISTINCT regionName FROM WeatherForecasts ORDER BY regionName"
            ).fetchall()
        return [str(row["regionName"]) for row in rows]
    except sqlite3.Error as exc:
        raise DatabaseError(f"讀取地區清單失敗：{exc}") from exc


def get_weather_by_region(
    region_name: str, db_path: str | Path = DATABASE_PATH
) -> pd.DataFrame:
    """使用 parameterized query 查詢指定地區。"""
    sql = """
    SELECT *
    FROM WeatherForecasts
    WHERE regionName = ?
    ORDER BY startTime
    """
    try:
        initialize_database(db_path)
        with _connect(db_path) as connection:
            return pd.read_sql_query(sql, connection, params=(region_name,))
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DatabaseError(f"查詢地區天氣失敗：{exc}") from exc


def get_latest_weather_for_all_regions(
    db_path: str | Path = DATABASE_PATH,
) -> pd.DataFrame:
    """取得每個地區目前資料庫中最早的未過期預報，供地圖使用。"""
    sql = """
    SELECT w.*
    FROM WeatherForecasts AS w
    JOIN (
        SELECT regionName, MIN(startTime) AS firstStart
        FROM WeatherForecasts
        WHERE endTime >= datetime('now', 'localtime')
        GROUP BY regionName
    ) AS latest
      ON w.regionName = latest.regionName AND w.startTime = latest.firstStart
    ORDER BY w.regionName
    """
    fallback_sql = """
    SELECT w.*
    FROM WeatherForecasts AS w
    JOIN (
        SELECT regionName, MAX(startTime) AS firstStart
        FROM WeatherForecasts
        GROUP BY regionName
    ) AS latest
      ON w.regionName = latest.regionName AND w.startTime = latest.firstStart
    ORDER BY w.regionName
    """
    try:
        initialize_database(db_path)
        with _connect(db_path) as connection:
            frame = pd.read_sql_query(sql, connection)
            if frame.empty:
                frame = pd.read_sql_query(fallback_sql, connection)
            return frame
    except (sqlite3.Error, pd.errors.DatabaseError) as exc:
        raise DatabaseError(f"讀取地圖資料失敗：{exc}") from exc

