# AGENTS.md

## 1. 專案目的

此專案是可直接執行的「Taiwan Weather Forecast」Dashboard，主要資料流程如下：

`CWA Open Data API → JSON → Pandas DataFrame → SQLite → Streamlit → Chart / Map / Hugging Face AI`

修改程式時，優先維持流程簡單、容易閱讀，讓 Python 初中階使用者也能理解與除錯。

## 2. 回覆與說明方式

- 除非使用者指定其他語言，使用繁體中文回覆。
- 先說明結果，再補充必要細節。
- 修改程式後，簡短說明「改了什麼、為什麼、如何驗證」。
- 不要為了抽象化而增加不必要的 class、設計模式或額外框架。
- 若受限於 API Token、網路或外部服務而無法測試，必須清楚指出未測試項目。

## 3. 專案檔案職責

- `app.py`：Streamlit UI、篩選器、指標、圖表、地圖及錯誤提示。
- `cwa_api.py`：CWA API 呼叫與 JSON 解析。
- `database.py`：SQLite schema、寫入、去重與查詢。
- `ai_service.py`：Hugging Face Inference API 與模型 fallback。
- `config.py`：環境變數與共用設定。
- `tests/`：資料解析、資料庫及關鍵流程測試。
- `.streamlit/config.toml`：Streamlit 原生主題設定。

不要把所有邏輯移入 `app.py`。新增功能時，放在最符合上述責任的模組。

## 4. 修改原則

- 先閱讀相關檔案與現有測試，再進行修改。
- 優先採用最小幅度修改，保留既有函式名稱、資料流程與程式風格。
- 函式使用清楚名稱與適當 type hints。
- 只在邏輯不容易理解時加入簡短註解。
- 不使用 Flask、React 或 Vue；Web UI 固定使用 Streamlit。
- 所有 Streamlit 建立、修改、除錯與美化工作，先遵循可用的 `developing-with-streamlit` skill。
- 優先使用 Streamlit 原生元件與 `.streamlit/config.toml`，避免注入大量自訂 CSS。

## 5. API 與機密資料

- 禁止將 CWA API Key、Hugging Face Token 或其他秘密寫死在程式碼、測試、README 或 Git 紀錄中。
- 機密資料只能由 `.env` 或執行環境讀取。
- `.env` 與 `data.db` 必須維持在 `.gitignore` 中。
- `.env.example` 只能保留假的範例值。
- 除錯輸出不得顯示完整 Token。
- 所有 HTTP request 必須設定 timeout，並處理連線、HTTP、逾時與回傳格式錯誤。

## 6. CWA 資料規則

- 目前主要 Dataset 為 `F-D0047-091`。
- 不可假設 JSON 欄位必定存在；解析前要確認型別與 key。
- 解析器需兼容目前已支援的大小寫與欄位形式，例如：
  - `weatherElement` / `WeatherElement`
  - `elementValue.value`
  - `MaxTemperature`、`MinTemperature`、`RelativeHumidity`
  - `ProbabilityOfPrecipitation`、`Weather`
- DataFrame 應維持以下欄位：
  - `regionName`
  - `dataDate`
  - `startTime`
  - `endTime`
  - `minTemp`
  - `maxTemp`
  - `weather`
  - `rainProbability`
  - `humidity`
- API 未提供的值可以是 `NULL` / `None`，不可因單一缺值讓整批資料解析失敗。

## 7. SQLite 規則

- 資料庫固定為 `data.db`，不存在時自動建立。
- 資料表固定為 `WeatherForecasts`。
- 使用 `(regionName, startTime, endTime)` 的唯一性規則避免重複資料。
- 重複更新應使用既有 upsert 行為，不可產生大量 duplicate records。
- 所有 SQL 查詢必須使用 parameterized query，不可拼接使用者輸入。
- 資料庫錯誤需轉成可理解的訊息，Streamlit 頁面不可直接 crash。

## 8. Hugging Face 規則

- Token 只能由 `HF_TOKEN` 環境變數取得。
- 目前預設模型為 `Qwen/Qwen3.5-9B`，並保留 fallback 模型機制。
- 模型不可用、provider 不支援、逾時或回傳格式改變時，Dashboard 仍需正常顯示。
- AI 失敗時顯示「AI 天氣建議目前無法取得」及安全、簡短的錯誤資訊。
- 不要在自動化測試中消耗真實 Hugging Face API 額度；使用 mock 或清空 `HF_TOKEN`。

## 9. Streamlit UI 規則

- 保留地區、日期與預報時段的互動篩選功能。
- 圖表、指標、表格、地圖與 AI 建議必須隨選擇內容更新。
- 慢速 API 呼叫不能阻止其他天氣資訊顯示；AI 區域需獨立處理錯誤。
- 更新資料按鈕應提供處理中、成功或失敗的明確回饋。
- 找不到地區或資料庫沒有資料時，顯示提示，不要拋出未處理例外。
- 地圖座標集中在乾淨的 dictionary 管理，不要散落在 UI 程式碼內。

## 10. 測試與完成條件

修改程式後，至少執行：

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pip check
```

若修改 `app.py` 或互動邏輯，還要使用 Streamlit `AppTest` 檢查：

- 頁面沒有未處理 exception。
- Selectbox 可以切換地區、日期與預報時段。
- 切換地區後，指標與資料會更新。
- 圖表、資料表與地圖可以建立。
- 沒有 `HF_TOKEN` 時，其他 Dashboard 功能仍正常。

涉及 CWA parser 或 SQLite 時，另外確認：

- MinT / MaxT 欄位解析正確。
- DataFrame 欄位完整。
- SQLite 確實寫入資料。
- 相同資料寫入兩次不會增加重複紀錄。
- SQL 查詢依 `startTime` 排序。

不得在未實際執行測試時宣稱測試成功。

## 11. Git 提交前檢查

完成修改後執行：

```powershell
git status --short
git check-ignore .env data.db
```

確認：

- `.env` 沒有被追蹤。
- `data.db` 沒有被追蹤。
- 沒有意外加入 Token、快取或 virtual environment。
- 只包含與目前任務相關的修改。
