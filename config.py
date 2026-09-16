"""專案設定：集中讀取環境變數與共用常數。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

CWA_API_KEY = os.getenv("CWA_API_KEY", "").strip()
HF_TOKEN = os.getenv("HF_TOKEN", "").strip()

CWA_DATASET_ID = "F-D0047-091"
CWA_API_URL = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{CWA_DATASET_ID}"
HF_MODEL = os.getenv("HF_MODEL", "Qwen/Qwen3.5-9B").strip()
HF_FALLBACK_MODELS = (
    "Qwen/Qwen3.5-9B",
    "openai/gpt-oss-20b",
    "google/gemma-3-27b-it",
)
DATABASE_PATH = BASE_DIR / "data.db"
REQUEST_TIMEOUT = 30
