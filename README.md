# Taiwan Weather Forecast

這是一個可直接執行的台灣天氣預報 Dashboard。系統從中央氣象署（CWA）取得真實預報 JSON，整理成 Pandas DataFrame、寫入 SQLite，再以 Streamlit 顯示圖表、資料表、地圖與 Hugging Face AI 生活建議。

## 系統架構

```text
CWA Open Data (F-D0047-091)
          ↓ JSON
       cwa_api.py
          ↓ DataFrame
       database.py → data.db / SQL 查詢
          ↓
       app.py (Streamlit)
       ├─ Altair 溫度區間圖
       ├─ Folium 台灣地圖
       └─ ai_service.py → Hugging Face Inference Providers
```

## 1. 安裝 Python 與建立環境

建議使用 Python 3.10～3.12。先安裝 [Python](https://www.python.org/downloads/)，並在本專案目錄開啟 PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

若 PowerShell 阻擋啟用腳本，可在目前視窗執行：

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\.venv\Scripts\Activate.ps1
```

## 2. 設定 API Key

專案已附 `.env.example`。把它複製成 `.env`：

```powershell
Copy-Item .env.example .env
```

編輯 `.env`：

```dotenv
CWA_API_KEY=你的中央氣象署授權碼
HF_TOKEN=你的_Hugging_Face_Token
```

- CWA Key：至 [中央氣象署開放資料平臺](https://opendata.cwa.gov.tw/) 註冊並取得授權碼。
- HF Token：至 [Hugging Face Token 設定](https://huggingface.co/settings/tokens) 建立 fine-grained token，允許呼叫 Inference Providers。
- `.env` 與 `data.db` 已列入 `.gitignore`，不會被 Git 追蹤；`.env.example` 只放示意值。

## 3. 執行

```powershell
streamlit run app.py
```

第一次進入時資料庫是空的。確認 `.env` 已設定後，按畫面上的「更新天氣資料」，系統會建立 `data.db` 並下載預報。

## CWA Dataset 與 JSON 解析

使用 Dataset `F-D0047-091`：全臺縣市一週天氣預報。API 路徑：

```text
https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-091
```

解析流程：

1. 從 `records.locations[].location[]` 取得地區。
2. 依 `elementName` 建立查找表，不使用容易出錯的固定陣列索引。
3. 以 `MinT`／`MaxT` 的預報時段為基準，依相同時段或時間涵蓋關係配對 `Wx`、`PoP12h`（亦相容 `PoP`／`PoP6h`）與 `RH`。
4. `elementValue` 可為 list 或 dict；缺少的因子保留為 `NULL`。
5. 整理成 `regionName`、`dataDate`、`startTime`、`endTime`、`minTemp`、`maxTemp`、`weather`、`rainProbability`、`humidity`。

若 CWA 改變外層包裝、缺少欄位、回傳非 JSON、HTTP 失敗或 timeout，畫面會顯示錯誤，Dashboard 不會因單一 API 錯誤而崩潰。

## SQLite 資料庫

`data.db` 用來保留下載過的預報，即使 AI 暫時失敗仍可瀏覽天氣。第一次執行會自動建立：

```sql
CREATE TABLE WeatherForecasts (
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

重複更新使用 SQLite `ON CONFLICT ... DO UPDATE`，同一地區與預報時段只保留一筆並更新最新數值。查詢使用 `WHERE regionName = ?` parameterized query，不拼接 SQL。

## Hugging Face AI 功能

預設模型為 `Qwen/Qwen3.5-9B`，透過官方 `huggingface_hub.InferenceClient` 與自動 Provider 路由產生繁體中文摘要。程式會關閉 Qwen 的 thinking 模式，避免簡短天氣建議被推理 token 用完。

若目前啟用的 Provider 不支援指定模型，程式會依序嘗試：

1. `Qwen/Qwen3.5-9B`
2. `openai/gpt-oss-20b`
3. `google/gemma-3-27b-it`

若要改模型，可在 `.env` 額外加入：

```dotenv
HF_MODEL=其他可用的聊天模型名稱
```

Token 缺少、請求逾時、模型暫時不可用或回傳格式不同時，只會顯示「AI 天氣建議目前無法取得」，其他 Dashboard 功能仍可使用。部分 Inference Provider 可能需要額度。

## Dashboard 可以做什麼

- 在 sidebar 選擇地區、日期與日夜預報時段。
- 以帶有趨勢 sparkline 的 KPI 卡查看最低溫、最高溫、降雨機率與濕度。
- 動態顯示所選地區的 MaxT／MinT 折線與溫度區間。
- 顯示完整預報資料表。
- 在 Folium 台灣地圖顯示各縣市預報，Marker 顏色代表平均溫度。
- 依所選預報產生簡短繁體中文 AI 天氣與生活建議。
- 按一下按鈕重新下載、解析並更新 SQLite。
- 使用 `.streamlit/config.toml` 原生主題，不依賴脆弱的自訂 CSS。

## 測試

離線測試不需要 API Key：

```powershell
pytest -q
python -m compileall .
```

測試涵蓋 JSON 欄位順序變動、MinT／MaxT／PoP／RH 解析、SQLite 寫入、SQL 查詢、重複更新去重，以及缺少 Key／JSON 結構改變時的錯誤處理。

有真實 Token 後，建議再做端到端測試：

1. 啟動 Streamlit 並按「更新天氣資料」。
2. 切換地區與日期，確認指標、圖表與資料表一起變動。
3. 點地圖 Marker，確認地區資料正確。
4. 確認 AI 建議出現；暫時移除 `HF_TOKEN` 後確認其他功能仍正常。

## 常見錯誤

- **找不到 CWA_API_KEY**：確認檔名是 `.env`、變數名稱正確，修改後重新啟動 Streamlit。
- **401／403**：Key 無效、權限不足或貼入了多餘空白。
- **CWA timeout**：稍後再按更新；舊資料仍保留於 `data.db`。
- **資料庫無資料**：至少成功按一次「更新天氣資料」。
- **AI model unavailable／額度不足**：程式會先自動嘗試備援模型。若仍失敗，請確認 HF Token 的 Inference Providers 權限、已啟用的 Provider 與帳戶額度，或在 `.env` 設定另一個可用聊天模型。
- **ModuleNotFoundError**：確認已啟用正確虛擬環境並重新執行 `pip install -r requirements.txt`。
