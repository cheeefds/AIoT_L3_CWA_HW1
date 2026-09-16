"""Taiwan Weather Forecast Streamlit Dashboard。"""

from __future__ import annotations

from datetime import datetime
from html import escape
from typing import Any

import altair as alt
import folium
import pandas as pd
import streamlit as st
from streamlit_folium import st_folium

from ai_service import WeatherAIError, generate_weather_advice
from config import CWA_API_KEY, CWA_DATASET_ID, DATABASE_PATH, HF_MODEL, HF_TOKEN
from cwa_api import CWAError, download_forecast_dataframe
from database import (
    DatabaseError,
    get_latest_weather_for_all_regions,
    get_regions,
    get_weather_by_region,
    initialize_database,
    insert_forecasts,
)


REGION_COORDINATES: dict[str, tuple[float, float]] = {
    "基隆市": (25.1276, 121.7392), "臺北市": (25.0375, 121.5637),
    "新北市": (25.0169, 121.4628), "桃園市": (24.9937, 121.3010),
    "新竹市": (24.8138, 120.9675), "新竹縣": (24.8387, 121.0177),
    "苗栗縣": (24.5602, 120.8214), "臺中市": (24.1477, 120.6736),
    "彰化縣": (24.0756, 120.5440), "南投縣": (23.9609, 120.9719),
    "雲林縣": (23.7092, 120.4313), "嘉義市": (23.4801, 120.4491),
    "嘉義縣": (23.4518, 120.2555), "臺南市": (22.9999, 120.2269),
    "高雄市": (22.6273, 120.3014), "屏東縣": (22.5519, 120.5487),
    "宜蘭縣": (24.7021, 121.7378), "花蓮縣": (23.9911, 121.6112),
    "臺東縣": (22.7554, 121.1500), "澎湖縣": (23.5711, 119.5793),
    "金門縣": (24.4494, 118.3767), "連江縣": (26.1605, 119.9517),
}


def format_metric(value: Any, suffix: str) -> str:
    if value is None or pd.isna(value):
        return "—"
    number = float(value)
    shown = str(int(number)) if number.is_integer() else f"{number:.1f}"
    return f"{shown}{suffix}"


def temperature_color(min_temp: Any, max_temp: Any) -> str:
    values = [float(value) for value in (min_temp, max_temp) if value is not None and not pd.isna(value)]
    if not values:
        return "gray"
    average = sum(values) / len(values)
    if average < 20:
        return "blue"
    if average < 25:
        return "green"
    if average <= 30:
        return "orange"
    return "red"


def make_temperature_chart(frame: pd.DataFrame) -> alt.LayerChart:
    """建立最低／最高溫折線與溫度區間帶。"""
    chart_data = frame.copy()
    chart_data["time"] = pd.to_datetime(chart_data["startTime"], errors="coerce")
    chart_data = chart_data.dropna(subset=["time"])
    long_data = chart_data.melt(
        id_vars=["time", "weather", "rainProbability"],
        value_vars=["minTemp", "maxTemp"],
        var_name="temperatureType",
        value_name="temperature",
    )
    long_data["temperatureType"] = long_data["temperatureType"].map(
        {"minTemp": "最低溫", "maxTemp": "最高溫"}
    )

    time_axis = alt.X("time:T", title="日期與時間", axis=alt.Axis(format="%m/%d %H:%M"))
    temperature_axis = alt.Y(
        "temperature:Q", title="溫度（°C）", scale=alt.Scale(zero=False)
    )
    tooltip = [
        alt.Tooltip("time:T", title="時間", format="%Y/%m/%d %H:%M"),
        alt.Tooltip("temperatureType:N", title="項目"),
        alt.Tooltip("temperature:Q", title="溫度", format=".0f"),
        alt.Tooltip("weather:N", title="天氣"),
        alt.Tooltip("rainProbability:Q", title="降雨機率", format=".0f"),
    ]

    band = alt.Chart(chart_data).mark_area(
        color="#0078D4", opacity=0.10
    ).encode(
        x=time_axis,
        y=alt.Y("minTemp:Q", title="溫度（°C）", scale=alt.Scale(zero=False)),
        y2="maxTemp:Q",
    )
    lines = alt.Chart(long_data).mark_line(strokeWidth=3).encode(
        x=time_axis,
        y=temperature_axis,
        color=alt.Color(
            "temperatureType:N",
            title=None,
            scale=alt.Scale(
                domain=["最低溫", "最高溫"], range=["#0078D4", "#D83B01"]
            ),
        ),
        tooltip=tooltip,
    )
    points = lines.mark_point(size=70, filled=True)
    return (
        (band + lines + points)
        .properties(height=320)
        .configure_legend(orient="bottom", direction="horizontal")
        .interactive()
    )


def make_weather_map(frame: pd.DataFrame) -> folium.Map:
    weather_map = folium.Map(location=[23.7, 121.0], zoom_start=7, tiles="OpenStreetMap")
    for _, row in frame.iterrows():
        region = str(row["regionName"])
        coordinates = REGION_COORDINATES.get(region)
        if not coordinates:
            continue
        popup = (
            f"<b>{escape(region)}</b><br>"
            f"最低溫：{format_metric(row['minTemp'], '°C')}<br>"
            f"最高溫：{format_metric(row['maxTemp'], '°C')}<br>"
            f"降雨機率：{format_metric(row['rainProbability'], '%')}<br>"
            f"天氣：{escape(str(row['weather'] or '無資料'))}"
        )
        folium.CircleMarker(
            location=coordinates,
            radius=8,
            color=temperature_color(row["minTemp"], row["maxTemp"]),
            fill=True,
            fill_opacity=0.8,
            tooltip=region,
            popup=folium.Popup(popup, max_width=260),
        ).add_to(weather_map)
    return weather_map


def format_period(start_time: Any, end_time: Any) -> str:
    """將 API 時間轉成適合 Selectbox 顯示的預報時段。"""
    start = pd.to_datetime(start_time, errors="coerce")
    end = pd.to_datetime(end_time, errors="coerce")
    if pd.isna(start):
        return "時段未提供"
    if pd.isna(end):
        return start.strftime("%H:%M")
    return f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}"


def metric_series(frame: pd.DataFrame, column: str) -> list[float]:
    """清除空值後提供 st.metric sparkline 使用。"""
    return [float(value) for value in frame[column].dropna().tolist()]


def database_updated_at() -> str:
    if not DATABASE_PATH.exists():
        return "尚未更新"
    modified = datetime.fromtimestamp(DATABASE_PATH.stat().st_mtime)
    return modified.strftime("%Y/%m/%d %H:%M")


@st.cache_data(ttl=1800, show_spinner=False)
def cached_ai_advice(weather_items: tuple[tuple[str, Any], ...], token: str, model: str) -> str:
    return generate_weather_advice(dict(weather_items), token=token, model=model)


def main() -> None:
    st.set_page_config(
        page_title="Taiwan Weather Forecast",
        page_icon=":material/cloud:",
        layout="wide",
    )
    st.title("Taiwan Weather Forecast", icon=":material/cloud:")
    st.caption("台灣一週天氣預報、互動地圖與 AI 生活建議")

    with st.container(horizontal=True, gap="xsmall"):
        st.badge(f"CWA {CWA_DATASET_ID}", icon=":material/cloud_download:", color="blue")
        st.badge("SQLite", icon=":material/database:", color="gray")
        st.badge(HF_MODEL, icon=":material/auto_awesome:", color="violet")

    try:
        initialize_database()
    except DatabaseError as exc:
        st.error(str(exc))
        st.stop()

    regions = get_regions()

    with st.sidebar:
        st.header("查詢條件", icon=":material/filter_alt:")
        update_clicked = st.button(
            "更新天氣資料",
            icon=":material/refresh:",
            type="primary",
            width="stretch",
        )
        st.caption(f"資料庫更新時間：{database_updated_at()}")

    if update_clicked:
        try:
            with st.status(
                "正在更新中央氣象署預報…",
                expanded=True,
            ) as update_status:
                fresh_data = download_forecast_dataframe(CWA_API_KEY)
                insert_forecasts(fresh_data)
                update_status.update(
                    label=f"更新完成，共處理 {len(fresh_data):,} 筆預報",
                    state="complete",
                    expanded=False,
                )
            cached_ai_advice.clear()
            st.toast("天氣資料已更新", icon=":material/check_circle:")
            regions = get_regions()
        except (CWAError, DatabaseError) as exc:
            st.error(str(exc), icon=":material/error:")

    if not regions:
        st.info(
            "資料庫目前沒有天氣資料。請先在 `.env` 設定 CWA_API_KEY，"
            "再按左側的「更新天氣資料」。",
            icon=":material/info:",
        )
        if not CWA_API_KEY or CWA_API_KEY.lower().startswith(("your_", "your")):
            st.warning("尚未偵測到有效的 CWA_API_KEY。", icon=":material/key_off:")
        st.stop()

    with st.sidebar:
        selected_region = st.selectbox("地區", regions, key="region")

    try:
        region_data = get_weather_by_region(selected_region)
    except DatabaseError as exc:
        st.error(str(exc), icon=":material/error:")
        st.stop()
    if region_data.empty:
        st.warning(
            "查不到所選地區的預報資料，請重新更新資料。",
            icon=":material/warning:",
        )
        st.stop()

    dates = region_data["dataDate"].dropna().astype(str).unique().tolist()
    with st.sidebar:
        selected_date = st.selectbox("日期", dates, key="forecast_date")
    selected_rows = region_data[
        region_data["dataDate"].astype(str) == selected_date
    ].reset_index(drop=True)
    if selected_rows.empty:
        st.warning("查不到所選日期的預報資料。", icon=":material/warning:")
        st.stop()
    period_labels = [
        format_period(row["startTime"], row["endTime"])
        for _, row in selected_rows.iterrows()
    ]
    with st.sidebar:
        selected_period = st.selectbox("預報時段", period_labels, key="forecast_period")
        with st.expander("資料來源與設定", icon=":material/settings:"):
            st.caption(f"Dataset：{CWA_DATASET_ID}")
            st.caption(f"AI 模型：{HF_MODEL}")
            st.caption("API Key 僅從本機 `.env` 讀取。")

    current = selected_rows.iloc[period_labels.index(selected_period)]
    weather_text = current["weather"] if pd.notna(current["weather"]) else "無資料"

    st.header(f"{selected_region} · {selected_date}", icon=":material/location_on:")
    st.caption(f"預報時段 {selected_period}　·　{weather_text}")

    with st.container(horizontal=True, gap="small"):
        st.metric(
            "最低溫",
            format_metric(current["minTemp"], "°C"),
            icon=":material/ac_unit:",
            border=True,
            chart_data=metric_series(region_data, "minTemp"),
            chart_type="line",
            delta_color="blue",
        )
        st.metric(
            "最高溫",
            format_metric(current["maxTemp"], "°C"),
            icon=":material/thermostat:",
            border=True,
            chart_data=metric_series(region_data, "maxTemp"),
            chart_type="line",
            delta_color="orange",
        )
        st.metric(
            "降雨機率",
            format_metric(current["rainProbability"], "%"),
            icon=":material/rainy:",
            border=True,
            chart_data=metric_series(region_data, "rainProbability"),
            chart_type="bar",
            delta_color="blue",
        )
        st.metric(
            "相對濕度",
            format_metric(current["humidity"], "%"),
            icon=":material/humidity_percentage:",
            border=True,
            chart_data=metric_series(region_data, "humidity"),
            chart_type="area",
            delta_color="violet",
        )

    chart_column, ai_column = st.columns([2, 1], gap="medium")
    with chart_column.container(border=True, height="stretch"):
        st.subheader("溫度趨勢", icon=":material/show_chart:")
        st.altair_chart(make_temperature_chart(region_data), width="stretch")
    with ai_column:
        ai_card = st.container(border=True, height="stretch")

    with st.container(border=True):
        st.subheader("台灣天氣地圖", icon=":material/map:")
        try:
            map_data = get_latest_weather_for_all_regions()
            map_column, legend_column = st.columns([4, 1], vertical_alignment="top")
            with map_column:
                st_folium(make_weather_map(map_data), width=900, height=500)
            with legend_column:
                st.markdown("**平均溫度圖例**")
                st.markdown(
                    ":blue-badge[低於 20°C]  \n"
                    ":green-badge[20～25°C]  \n"
                    ":orange-badge[25～30°C]  \n"
                    ":red-badge[高於 30°C]  \n"
                    ":gray-badge[無溫度資料]"
                )
                st.caption(f"顯示 {len(map_data)} 個縣市的最近預報")
        except DatabaseError as exc:
            st.warning(f"地圖資料目前無法取得：{exc}", icon=":material/warning:")

    table = region_data[[
        "dataDate", "startTime", "endTime", "minTemp", "maxTemp",
        "rainProbability", "humidity", "weather",
    ]].rename(columns={
        "dataDate": "日期", "startTime": "開始時間", "endTime": "結束時間",
        "minTemp": "最低溫 (°C)", "maxTemp": "最高溫 (°C)",
        "rainProbability": "降雨機率 (%)", "humidity": "濕度 (%)", "weather": "天氣",
    })
    table["開始時間"] = pd.to_datetime(table["開始時間"], errors="coerce")
    table["結束時間"] = pd.to_datetime(table["結束時間"], errors="coerce")
    with st.container(border=True):
        st.subheader("完整預報資料", icon=":material/table_chart:")
        st.caption(f"共 {len(table)} 個預報時段，可排序或放大查看。")
        st.dataframe(
            table,
            width="stretch",
            hide_index=True,
            column_config={
                "日期": st.column_config.TextColumn("日期", pinned=True),
                "開始時間": st.column_config.DatetimeColumn(
                    "開始時間", format="MM/DD HH:mm"
                ),
                "結束時間": st.column_config.DatetimeColumn(
                    "結束時間", format="MM/DD HH:mm"
                ),
                "最低溫 (°C)": st.column_config.NumberColumn(format="%.0f °C"),
                "最高溫 (°C)": st.column_config.NumberColumn(format="%.0f °C"),
                "降雨機率 (%)": st.column_config.NumberColumn(format="%.0f%%"),
                "濕度 (%)": st.column_config.NumberColumn(format="%.0f%%"),
            },
        )

    # AI 可能需要較久，因此先建立位置，等其他區塊完成後再填入內容。
    with ai_card:
        st.subheader("AI 天氣建議", icon=":material/auto_awesome:")
        st.caption(f"根據 {selected_region} {selected_date} {selected_period} 的預報產生")
        if not HF_TOKEN or HF_TOKEN.lower().startswith(("your_", "your")):
            st.warning(
                "尚未設定有效的 HF_TOKEN。",
                icon=":material/key_off:",
            )
        else:
            weather_items = tuple((key, current[key]) for key in [
                "regionName", "dataDate", "minTemp", "maxTemp",
                "rainProbability", "humidity", "weather",
            ])
            try:
                with st.skeleton(height=180):
                    advice = cached_ai_advice(weather_items, HF_TOKEN, HF_MODEL)
                st.markdown(advice)
            except WeatherAIError as exc:
                st.warning(
                    "AI 天氣建議目前無法取得",
                    icon=":material/warning:",
                )
                st.caption(str(exc))


if __name__ == "__main__":
    main()
