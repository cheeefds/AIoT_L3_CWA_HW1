# Taiwan Weather Forecast 系統設計規格書 (System Design Document)

本文件詳細記錄「台灣天氣預報儀表板 (Taiwan Weather Forecast Dashboard)」之軟體架構、設計決策、資料模型、容錯與安全機制以及模組技術規格。

---

## 1. 系統設計目標與架構原則 (Design Goals & Principles)

### 1.1 設計目標
1. **端到端管線自動化 (End-to-End Pipeline)**：串接政府開放資料（CWA Open Data）、本地資料庫持久化、互動式資料儀表板與生成式 AI 建議。
2. **高容錯性與高可用性 (Resilience & Availability)**：任何外部服務（氣象 API 逾時、SSL 憑證缺陷、Hugging Face 額度用罄）皆不可導致前端介面崩潰或未處理例外。
3. **輕量且易維護 (Simplicity & Maintainability)**：採用 Python 現代生態系（Requests, Pandas, SQLite, Streamlit, Altair, Folium），避免過度封裝或引入沉重的微服務架構，便於初中階開發者理解與擴充。
4. **資訊安全與隱私防護 (Security by Design)**：嚴格實施金鑰與資料庫本機隔離，杜絕 Token 外洩與 SQL Injection。

### 1.2 核心架構原則
* **職責分離 (Separation of Concerns, SoC)**：
  - `cwa_api.py` 僅負責網路請求與 JSON 資料清洗。
  - `database.py` 僅負責 SQLite CRUD、去重與交易管理。
  - `ai_service.py` 僅負責 Prompt 構造、推論呼叫與 Fallback。
  - `app.py` 專注於 Streamlit 佈局、元件渲染與狀態聯動。
* **資料操作冪等性 (Idempotence)**：資料庫寫入一律依據預報時段進行 Upsert，重複執行不增加冗餘紀錄。
* **優雅降級 (Graceful Degradation)**：AI 推論失敗時僅於卡片提示，主面板各項數值、折線圖與地圖仍保持 100% 正常可用。

---

## 2. 系統分層架構 (Layered Architecture)

系統邏輯劃分為五大層級：

```text
┌──────────────────────────────────────────────────────────┐
│ 1. 表現層 (Presentation Layer) - app.py                   │
│    - Streamlit 原生主題佈局與側邊欄篩選器                  │
│    - KPI 關鍵指標卡片、Altair 溫度區間圖、Folium 地圖      │
└────────────────────────────┬─────────────────────────────┘
                             ↓
┌────────────────────────────┴─────────────────────────────┐
│ 2. 商業邏輯與推論層 (Intelligence Layer) - ai_service.py │
│    - 提示詞工程 (Prompt Engineering)                     │
│    - Hugging Face InferenceClient 多模型 Fallback 路由   │
└────────────────────────────┬─────────────────────────────┘
                             ↓
┌────────────────────────────┴─────────────────────────────┐
│ 3. 資料持久層 (Persistence Layer) - database.py           │
│    - SQLite (data.db) 連線池與自動建表                    │
│    - ON CONFLICT DO UPDATE 冪等寫入                      │
│    - 參數化查詢 (Parameterized Queries)                  │
└────────────────────────────┬─────────────────────────────┘
                             ↓
┌────────────────────────────┴─────────────────────────────┐
│ 4. 資料擷取與轉換層 (ETL Layer) - cwa_api.py              │
│    - 中央氣象署 API (F-D0047-091) REST 通訊              │
│    - SSL/TLS 相容重試、巢狀 JSON 扁平化與時段對齊         │
│    - 標準化 Pandas DataFrame 輸出                        │
└────────────────────────────┬─────────────────────────────┘
                             ↓
┌────────────────────────────┴─────────────────────────────┐
│ 5. 設定與基礎建設層 (Infrastructure Layer) - config.py     │
│    - .env 環境變數載入、全域常數與安全驗證                │
└──────────────────────────────────────────────────────────┘
```

---

## 3. 資料模型與資料庫設計 (Data Modeling & Storage)

### 3.1 CWA 原始 JSON 到 DataFrame 的對映設計
CWA Dataset `F-D0047-091`（全臺縣市一週天氣預報）為深層巢狀結構。系統採取「元素字典查找 + 預報時段對齊」策略：

```text
records
 └─ locations[]
     └─ location[] (locationName: 臺北市, 臺中市...)
         └─ weatherElement[]
             ├─ elementName: MaxT / MaxTemperature / 最高溫度
             ├─ elementName: MinT / MinTemperature / 最低溫度
             ├─ elementName: Wx / Weather / 天氣現象
             ├─ elementName: PoP12h / PoP / 降雨機率
             └─ elementName: RH / RelativeHumidity / 相對濕度
```

* **轉換規則**：
  1. 擷取每個地區所有 `time` 區段。
  2. 以 `(startTime, endTime)` 為鍵對齊溫度、天氣現象與降雨機率。
  3. 缺值因子填入 `None`，不丟棄整筆預報。
  4. 最終轉化為具備 9 個強型態欄位的 Pandas DataFrame：
     `regionName`, `dataDate`, `startTime`, `endTime`, `minTemp`, `maxTemp`, `weather`, `rainProbability`, `humidity`。

---

### 3.2 SQLite 資料庫實體設計
資料表名稱固定為 `WeatherForecasts`，定義於 `database.py`：

```sql
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
);
```

#### 關鍵欄位約束與索引考量：
* **複合唯一鍵 (Composite Unique Constraint)**：`UNIQUE(regionName, startTime, endTime)`
  * **目的**：同一行政區在同一預報區間內只應存在一筆紀錄。
  * **去重策略**：使用 SQLite 原生語法：
    ```sql
    INSERT INTO WeatherForecasts (regionName, dataDate, startTime, endTime, minTemp, maxTemp, weather, rainProbability, humidity)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(regionName, startTime, endTime) DO UPDATE SET
        dataDate = excluded.dataDate,
        minTemp = excluded.minTemp,
        maxTemp = excluded.maxTemp,
        weather = excluded.weather,
        rainProbability = excluded.rainProbability,
        humidity = excluded.humidity;
    ```
* **查詢安全**：所有查詢一律使用 `connection.execute("SELECT ... WHERE regionName = ? ORDER BY startTime ASC", (region_name,))`，防止 SQL Injection。

---

## 4. 網路通訊與資訊安全設計 (Network & Security)

### 4.1 SSL/TLS 憑證相容設計
* **挑戰**：中央氣象署伺服器（`opendata.cwa.gov.tw`）之 X.509 憑證鏈中，中繼憑證缺少 RFC 5280 之 `Subject Key Identifier (SKI)`。在 Python 3.13 / OpenSSL 3.2+ 嚴格模式下會拋出 `[SSL: CERTIFICATE_VERIFY_FAILED]`。
* **解決方案**：
  ```python
  try:
      response = requests.get(CWA_API_URL, params=params, timeout=timeout)
  except requests.exceptions.SSLError:
      urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
      response = requests.get(CWA_API_URL, params=params, timeout=timeout, verify=False)
  ```
  系統預設先嘗試標準驗證；若遇憑證結構缺失引發之 `SSLError`，自動降級相容重試，兼顧安全性與環境相容性。

### 4.2 金鑰與隱私安全架構
1. **設定隔離**：`CWA_API_KEY` 與 `HF_TOKEN` 僅由根目錄 `.env` 讀取，禁止寫死於原始碼。
2. **版本控制防護**：`.env` 與 `data.db` 納入 `.gitignore`，確保不被提交至遠端儲存庫。
3. **除錯遮蔽**：在發生 `requests.RequestException` 時，透過字串替換將 URL 中的 `Authorization` 數值過濾為 `***`，避免於日誌或 Streamlit 介面中洩漏真實金鑰。

---

## 5. 生成式 AI 推論與多模型備援設計 (AI Architecture)

### 5.1 提示詞架構 (Prompt Engineering)
AI 建議模組採用單輪 Direct Prompting，明確規範輸出維度以抑制幻覺：
* **輸入變數**：`regionName`, `dataDate`, `minTemp`, `maxTemp`, `rainProbability`, `humidity`, `weather`。
* **系統約束**：
  1. 繁體中文輸出。
  2. 嚴格限縮在 100～200 字。
  3. 依序涵蓋：天氣摘要、穿著建議、是否攜帶雨具、戶外注意事項。
  4. 禁止自行補充非預報中提及之數字。

### 5.2 模型選擇與推理優化
* **首選模型**：`Qwen/Qwen3.5-9B`。該模型在繁體中文理解、生活情境推理具備優異表現。
* **推理加速與 Token 節省**：在請求時透過 `extra_body={"chat_template_kwargs": {"thinking": False}}` 關閉思考模式，防止簡短的生活建議被思考過程耗盡 token 與請求時間。

### 5.3 三階段 Fallback 容錯鏈
當 Hugging Face 伺服器忙碌、模型臨時離線或 Provider 額度受限時，系統依序嘗試備援：
1. `Qwen/Qwen3.5-9B`（預設模型）
2. `openai/gpt-oss-20b`（備援 1）
3. `google/gemma-3-27b-it`（備援 2）

若所有模型均無法調用，系統拋出 `WeatherAIError`，前端捕捉後轉化為非阻塞警告，絕不中斷儀表板核心運作。

---

## 6. 前端視覺化與互動架構 (UI/UX Architecture)

### 6.1 側邊欄與元件狀態聯動
* 透過 Streamlit 的 Session State 與元件選單（`st.selectbox`）：
  - 縣市清單動態來自資料庫 `SELECT DISTINCT regionName`。
  - 選定縣市後，日期清單過濾為該縣市實際擁有的預報日期。
  - 支援白天（06:00~18:00）與夜間（18:00~06:00）時段切換。

### 6.2 雙層 Altair 溫度區間圖 (Band & Line Layer)
* **底層（`mark_area`）**：將 `minTemp` 與 `maxTemp` 繪製為半透明色彩區間帶（Band），視覺化溫差範圍。
* **頂層（`mark_line` + `mark_point`）**：疊加最高溫（紅色系）與最低溫（藍色系）走勢折線與節點。

### 6.3 Folium 地圖溫度色彩對映
Marker 顏色根據各縣市即時平均氣溫計算（`temperature_color`）：
* 低於 20°C：**藍色 (Blue)** - 寒冷/偏涼
* 20°C ~ 25°C：**綠色 (Green)** - 舒適宜人
* 25°C ~ 30°C：**橘色 (Orange)** - 暖熱
* 高於 30°C：**紅色 (Red)** - 酷熱

---

## 7. 測試架構與品質保證規範 (Quality Assurance)

### 7.1 測試套件設計 (`tests/test_pipeline.py`)
* **離線與 Mock 原則**：單元測試全程不發送外部網路請求，不消耗真實 Token 額度。
* **核心測試面向**：
  1. `test_parse_expected_fields`：驗證複雜 JSON 結構扁平化為 DataFrame 的正確性。
  2. `test_parse_current_cwa_named_value_shape`：驗證大小寫與具名欄位相容性。
  3. `test_sqlite_insert_query_and_dedup`：驗證資料庫 Upsert 邏輯（寫入兩次不增加資料筆數）。
  4. `test_chart_and_map_follow_dataframe`：驗證 Altair 圖表規格與 Folium HTML 渲染。
  5. `test_hugging_face_falls_back_when_model_is_not_supported`：驗證多模型 Fallback 呼叫鏈。

### 7.2 驗證指令清單
在每次修改核心模組後，必須依序執行：
```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

---

## 8. 相關文件導航
* 快速開始與操作流程：[README.md](file:///c:/Users/user/Desktop/local_folder/AIoT_L3_CWA_HW1/README.md)
* 系統架構圖與時序圖：[myplane/workflow.md](file:///c:/Users/user/Desktop/local_folder/AIoT_L3_CWA_HW1/myplane/workflow.md)
