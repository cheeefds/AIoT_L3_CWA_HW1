"""專案設定：集中讀取環境變數與共用常數。"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def get_secret(key: str, default: str = "") -> str:
    """優先自系統環境變數或 .env 取得，若無則自 Streamlit Secrets (st.secrets) 取得。"""
    val = os.getenv(key)
    if val:
        return val.strip()
    try:
        import streamlit as st
        # 兼容 Streamlit Community Cloud 的 st.secrets 管理機制
        if hasattr(st, "secrets") and key in st.secrets:
            return str(st.secrets[key]).strip()
    except Exception:
        pass
    return default


def get_cwa_api_key() -> str:
    return get_secret("CWA_API_KEY", "")


def get_hf_token() -> str:
    return get_secret("HF_TOKEN", "")


def get_hf_model() -> str:
    return get_secret("HF_MODEL", "Qwen/Qwen3.5-9B")


CWA_API_KEY = get_cwa_api_key()
HF_TOKEN = get_hf_token()
HF_MODEL = get_hf_model()

CWA_DATASET_ID = "F-D0047-091"
CWA_API_URL = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{CWA_DATASET_ID}"
HF_FALLBACK_MODELS = (
    "Qwen/Qwen3.5-9B",
    "openai/gpt-oss-20b",
    "google/gemma-3-27b-it",
)
DATABASE_PATH = BASE_DIR / "data.db"
REQUEST_TIMEOUT = 30
