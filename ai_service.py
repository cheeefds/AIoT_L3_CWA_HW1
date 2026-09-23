"""Hugging Face 天氣摘要服務。"""

from __future__ import annotations

from typing import Any, Mapping

from huggingface_hub import InferenceClient

from config import HF_FALLBACK_MODELS, HF_MODEL, REQUEST_TIMEOUT


class WeatherAIError(RuntimeError):
    """AI API 無法產生建議。"""


def _display(value: Any, suffix: str = "") -> str:
    if value is None:
        return "無資料"
    try:
        if value != value:  # NaN
            return "無資料"
    except TypeError:
        pass
    return f"{value}{suffix}"


def build_weather_prompt(weather_data: Mapping[str, Any]) -> str:
    """把單筆預報整理成繁體中文提示。"""
    return f"""請根據以下台灣天氣預報，以繁體中文產生簡短、容易理解的天氣摘要與生活建議。

地區：{_display(weather_data.get('regionName'))}
日期：{_display(weather_data.get('dataDate'))}
最低溫：{_display(weather_data.get('minTemp'), '°C')}
最高溫：{_display(weather_data.get('maxTemp'), '°C')}
降雨機率：{_display(weather_data.get('rainProbability'), '%')}
濕度：{_display(weather_data.get('humidity'), '%')}
天氣：{_display(weather_data.get('weather'))}

請依序包含：天氣摘要、穿著建議、是否攜帶雨具、戶外活動注意事項。
請只使用以上資料，不要自行補充預報數字，回答控制在 100～200 個中文字。"""


def generate_weather_advice(
    weather_data: Mapping[str, Any],
    token: str,
    model: str = HF_MODEL,
    timeout: int = REQUEST_TIMEOUT,
    fallback_models: tuple[str, ...] = HF_FALLBACK_MODELS,
) -> str:
    """呼叫 Hugging Face Chat Completion；模型不可用時自動嘗試備援。"""
    if not token or token.startswith("YOUR_") or token.startswith("your_"):
        raise WeatherAIError("找不到有效的 HF_TOKEN，請先在 .env 或 Streamlit Secrets 中設定。")

    models = list(dict.fromkeys((model, *fallback_models)))
    unavailable_models: list[str] = []
    try:
        client = InferenceClient(api_key=token, timeout=timeout, provider="auto")
        for candidate in models:
            request_options: dict[str, Any] = {
                "model": candidate,
                "messages": [
                    {"role": "system", "content": "你是台灣生活天氣助理，回答務必簡潔、實用。"},
                    {"role": "user", "content": build_weather_prompt(weather_data)},
                ],
                "max_tokens": 350,
                "temperature": 0.4,
            }
            # Qwen 3 預設可能先輸出大量思考 token；此任務只需要精簡生活建議。
            if candidate.startswith("Qwen/"):
                request_options["extra_body"] = {
                    "chat_template_kwargs": {"enable_thinking": False}
                }

            try:
                completion = client.chat.completions.create(**request_options)
            except Exception as exc:
                error_text = str(exc).lower()
                model_unavailable = any(marker in error_text for marker in (
                    "model_not_supported",
                    "not supported by any provider",
                    "model is not supported",
                    "model unavailable",
                ))
                if model_unavailable:
                    unavailable_models.append(candidate)
                    continue
                raise

            content = completion.choices[0].message.content
            if isinstance(content, str) and content.strip():
                return content.strip()
            raise WeatherAIError(
                f"Hugging Face 模型 {candidate} 回傳格式中沒有可用文字。"
            )

        attempted = "、".join(unavailable_models)
        raise WeatherAIError(
            f"目前啟用的 Provider 不支援嘗試過的模型：{attempted}。"
            "請至 Hugging Face Inference Provider 設定啟用可用 Provider，"
            "或在 .env 設定 HF_MODEL。"
        )
    except WeatherAIError:
        raise
    except Exception as exc:
        name = type(exc).__name__
        raise WeatherAIError(f"Hugging Face API 呼叫失敗（{name}）：{exc}") from exc
