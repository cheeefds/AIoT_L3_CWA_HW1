# Taiwan Weather Forecast 系統架構與工作流程圖 (System Workflow)

本文件詳細展示 Taiwan Weather Forecast Dashboard 的系統架構、資料處理管線（ETL）、模組互動時序與各階段的核心工作流程。

> 📚 **系統技術設計規格書請參閱：[../design.md](../design.md)**  
> 📖 **快速上手指南與流程總覽請參閱：[../README.md](../README.md)**

---

## 1. 總體系統架構圖 (Architecture Overview)

### 1.1 系統管線階層文字圖

```text
┌─────────────────────────────────────────────────────────────────┐
│ Part 2: CWA 氣象資料擷取與 JSON 解析 (cwa_api.py)                 │
│ 中央氣象署開放資料 API (Dataset: F-D0047-091 一週預報)             │
│      ↓ HTTP GET (JSON 格式)                                     │
│ 欄位對齊、缺失值容錯、大小寫相容 → 標準化 Pandas DataFrame         │
└───────────────────────────────┬─────────────────────────────────┘
                                ↓
┌───────────────────────────────┴─────────────────────────────────┐
│ Part 3: SQLite 資料庫持久化與去重 (database.py)                  │
│ 自動初始化 data.db / WeatherForecasts 資料表                    │
│ 依 (regionName, startTime, endTime) 唯一約束                    │
│ 執行 Upsert 冪等更新 (ON CONFLICT ... DO UPDATE)                │
└───────────────────────────────┬─────────────────────────────────┘
                                ↓
┌───────────────────────────────┴─────────────────────────────────┐
│ Part 4: Streamlit 互動儀表板與視覺化 (app.py)                   │
│ ├─ 側邊欄篩選：全台縣市、預報日期、日夜時段切換                  │
│ ├─ KPI 關鍵指標：最高溫、最低溫、降雨機率、相對濕度              │
│ ├─ Altair 趨勢圖：溫度區間帶 (Band) 與高低溫折線                │
│ └─ Folium 地圖：各縣市平均溫度顏色標記 (藍/綠/橘/紅)             │
└───────────────────────────────┬─────────────────────────────────┘
                                ↓
┌───────────────────────────────┴─────────────────────────────────┐
│ Part 5: Hugging Face AI 智慧天氣建議 (ai_service.py)            │
│ 結構化 Prompt 模板工程 → 呼叫 InferenceClient                   │
│ 預設模型 Qwen/Qwen3.5-9B (關閉 thinking 節省 token)             │
│ 支援 3 階段 Fallback 備援 (gpt-oss-20b / gemma-3-27b)           │
│ 輸出：穿著建議、雨具提示、戶外活動指南 (繁體中文 100~200 字)     │
└─────────────────────────────────────────────────────────────────┘
```

---

### 1.2 系統架構 Mermaid 流程圖

```mermaid
flowchart TD
    subgraph CWA_Source ["中央氣象署 Open Data API"]
        API["CWA 氣象開放資料 API<br/>(Dataset: F-D0047-091)"]
    end

    subgraph Data_Pipeline ["資料擷取與清洗 (cwa_api.py)"]
        Fetch["fetch_cwa_forecast(api_key)<br/>發送 HTTP GET 請求"]
        Parse["parse_cwa_forecast(payload)<br/>動態欄位比對、大小寫相容<br/>時段對齊、缺失值補 None"]
        DF["標準化 Pandas DataFrame<br/>(9 大統一欄位)"]
    end

    subgraph Database_Layer ["資料庫持久化 (database.py)"]
        InitDB["initialize_database()<br/>自動建立 WeatherForecasts 資料表"]
        Upsert["insert_forecasts(frame)<br/>依 (regionName, startTime, endTime)<br/>執行 ON CONFLICT DO UPDATE 冪等去重"]
        DB[(SQLite 本機資料庫<br/>data.db)]
    end

    subgraph Presentation_Layer ["前端展示與視覺化 (app.py)"]
        UI_Filter["側邊欄互動篩選器<br/>(縣市 / 日期 / 預報時段)"]
        KPI["KPI 關鍵指標卡片<br/>(高溫/低溫/降雨率/濕度)"]
        Chart["Altair 溫度趨勢圖<br/>(高低溫折線與半透明區間帶)"]
        Map["Folium 氣象地圖<br/>(依縣市氣溫著色標記)"]
    end

    subgraph AI_Layer ["生成式 AI 建議 (ai_service.py)"]
        Prompt["build_weather_prompt()<br/>組裝繁體中文結構化 Prompt"]
        HF_Call{"InferenceClient 呼叫<br/>(預設: Qwen/Qwen3.5-9B)"}
        FB1["Fallback 1: openai/gpt-oss-20b"]
        FB2["Fallback 2: google/gemma-3-27b-it"]
        Advice["生活穿著與出行建議<br/>(100~200 字繁中)"]
        AI_Err["安全提示：AI 建議暫時無法取得<br/>(不中斷儀表板主要運作)"]
    end

    %% 連線關係
    API -->|JSON Payload| Fetch
    Fetch --> Parse
    Parse --> DF
    DF --> Upsert
    InitDB -.-> DB
    Upsert -->|寫入與更新| DB

    DB -->|SQL 參數化查詢| UI_Filter
    UI_Filter --> KPI
    UI_Filter --> Chart
    UI_Filter --> Map
    UI_Filter --> Prompt

    Prompt --> HF_Call
    HF_Call -->|成功| Advice
    HF_Call -->|模型不可用或逾時| FB1
    FB1 -->|成功| Advice
    FB1 -->|仍不可用| FB2
    FB2 -->|成功| Advice
    FB2 -->|全部失敗| AI_Err
```

---

## 2. 核心工作流程詳細分解

### 2.1 資料擷取與清理流程 (ETL Flow)

```mermaid
sequenceDiagram
    autonumber
    actor User as 使用者
    participant App as app.py (Streamlit)
    participant API as cwa_api.py
    participant CWA as 中央氣象署 API (F-D0047-091)
    participant DB as database.py (SQLite)

    User->>App: 點擊「更新天氣資料」按鈕
    App->>API: download_forecast_dataframe(api_key)
    API->>CWA: GET /F-D0047-091 (Authorization=Key, format=JSON)
    alt CWA 回應正常
        CWA-->>API: 200 OK (巢狀預報 JSON)
        API->>API: 遍歷 locations[].location[] 建立元素索引
        API->>API: 對齊 MinT, MaxT, Wx, PoP12h, RH 時段
        API->>API: 缺失值填補 None 並輸出 DataFrame
        API-->>App: 回傳清理後 DataFrame (22 縣市 * 預報時段)
        App->>DB: insert_forecasts(frame, db_path)
        DB->>DB: 執行 INSERT INTO ... ON CONFLICT DO UPDATE
        DB-->>App: 回傳寫入/更新筆數
        App-->>User: 顯示「更新成功！共寫入/更新 N 筆」
    else 連線失敗或金鑰無效
        CWA-->>API: 401 / 403 / Timeout / 非 JSON
        API-->>App: 拋出 CWAError
        App-->>User: 顯示友善錯誤提示（不中斷系統）
    end
```

---

### 2.2 儀表板互動與 AI 生活建議時序流程 (User Interaction & AI Flow)

```mermaid
sequenceDiagram
    autonumber
    actor User as 使用者
    participant App as app.py
    participant DB as database.py
    participant AI as ai_service.py
    participant HF as Hugging Face Inference API

    User->>App: 在側邊欄切換「地區 (如：臺中市)」與「日期」
    App->>DB: get_weather_by_region(regionName, db_path)
    DB-->>App: 回傳該縣市所有時段預報 (ORDER BY startTime)
    
    par 畫面並行渲染
        App->>App: 計算並渲染 KPI 指標卡片
        App->>App: 繪製 Altair 最高溫/最低溫與區間帶折線圖
        App->>App: 繪製 Folium 台灣縣市氣溫標記地圖
        App->>App: 渲染預報明細資料表
    end

    opt 當前時段有氣象資料
        App->>AI: generate_weather_advice(weather_data, token)
        AI->>AI: build_weather_prompt(weather_data)
        AI->>HF: chat.completions.create(model="Qwen/Qwen3.5-9B")
        alt 預設模型推論成功
            HF-->>AI: 回傳繁體中文天氣建議
            AI-->>App: 回傳建議內容
            App-->>User: 於專屬卡片區展示生活建議
        else 預設模型失敗 (不支援或逾時)
            AI->>HF: 依序切換 Fallback 模型 (gpt-oss-20b / gemma-3-27b)
            alt 備援成功
                HF-->>AI: 回傳備援推論建議
                AI-->>App: 回傳建議內容
                App-->>User: 於專屬卡片區展示生活建議
            else 所有備援皆失敗或無 Token
                AI-->>App: 拋出 WeatherAIError / 返回警告
                App-->>User: 顯示「AI 天氣建議目前無法取得」提示
            end
        end
    end
```

---

## 3. 資料庫 Schema 與資料表關聯

本專案採用輕量級本機資料庫 `data.db`，核心預報表 `WeatherForecasts` 的設計如下：

| 欄位名稱 | 型態 | 限制條件 | 說明 |
| :--- | :--- | :--- | :--- |
| `id` | INTEGER | PRIMARY KEY AUTOINCREMENT | 主鍵自動遞增流水號 |
| `regionName` | TEXT | NOT NULL | 縣市名稱（如：臺中市、新北市） |
| `dataDate` | TEXT | NOT NULL | 預報日期（格式：YYYY-MM-DD） |
| `startTime` | TEXT | NOT NULL | 預報時段起始時間 |
| `endTime` | TEXT | NOT NULL | 預報時段結束時間 |
| `minTemp` | REAL | 可為 NULL | 預報最低溫度 (°C) |
| `maxTemp` | REAL | 可為 NULL | 預報最高溫度 (°C) |
| `weather` | TEXT | 可為 NULL | 天氣現象描述（如：多雲短暫雨） |
| `rainProbability` | REAL | 可為 NULL | 降雨機率 (%) |
| `humidity` | REAL | 可為 NULL | 相對濕度 (%) |

> **唯一性鍵值 (Composite Unique Key)**：
> `UNIQUE(regionName, startTime, endTime)` 保證同一縣市在相同時間區間內只有一筆紀錄，重複下載時執行 Upsert 更新，不造成資料膨脹。

---

## 4. 模組職責對照表

| 模組檔案 | 核心函式 / 元件 | 工作流程職責 |
| :--- | :--- | :--- |
| **`config.py`** | `BASE_DIR`, `CWA_API_KEY`, `HF_TOKEN`, `HF_FALLBACK_MODELS` | 集中環境變數管理，載入 `.env` 與全域配置參數 |
| **`cwa_api.py`** | `fetch_cwa_forecast()`, `parse_cwa_forecast()`, `download_forecast_dataframe()` | 呼叫 CWA API、JSON 格式與大小寫相容、時段對齊、輸出 Pandas DataFrame |
| **`database.py`** | `initialize_database()`, `insert_forecasts()`, `get_weather_by_region()`, `get_regions()` | SQLite 資料庫初始化、Upsert 冪等寫入與防重、參數化安全查詢 |
| **`ai_service.py`** | `build_weather_prompt()`, `generate_weather_advice()` | 提示詞組裝、Hugging Face InferenceClient 連線、3 階段 Fallback 容錯 |
| **`app.py`** | `make_temperature_chart()`, `make_weather_map()`, `main()` | Streamlit 側邊欄篩選、KPI 呈現、Altair 區間圖表、Folium 地圖整合 |
| **`tests/test_pipeline.py`** | `test_parse_expected_fields()`, `test_sqlite_insert_query_and_dedup()`, `test_hugging_face_falls_back_when_model_is_not_supported()` | 離線自動化單元測試，驗證解析、去重、圖表與 AI Fallback 正確性 |
