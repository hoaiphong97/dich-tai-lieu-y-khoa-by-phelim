"""Nguồn dịch: Ollama (native API) và mọi API tương thích OpenAI (LM Studio, Gemini, ...).

Chế độ "agent" không có engine: pipeline xuất file cho agent dịch rồi ghép lại.
"""

from __future__ import annotations

import re
import time

import httpx

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
TAG_RE = re.compile(r"</?(SOURCE|CONTEXT|TERMS|TRANSLATION)>", re.IGNORECASE)
PREFIX_RE = re.compile(r"^\s*(bản dịch|translation|dịch|vietnamese)\s*:\s*", re.IGNORECASE)


class EngineError(RuntimeError):
    pass


def clean_output(text: str) -> str:
    text = THINK_RE.sub("", text or "")
    text = TAG_RE.sub("", text)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```\w*\n?|```$", "", text).strip()
    text = PREFIX_RE.sub("", text)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'“”":
        text = text[1:-1].strip()
    return text


class Engine:
    name = "base"

    def __init__(self, base_url: str, model: str, api_key: str = "", temperature: float = 0.2, timeout: float = 300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.temperature = temperature
        self.client = httpx.Client(timeout=httpx.Timeout(timeout, connect=10))

    @property
    def identity(self) -> str:
        return f"{self.name}|{self.base_url}|{self.model}"

    def translate(self, system: str, user: str, temperature: float | None = None) -> str:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                raw = self._chat(system, user, self.temperature if temperature is None else temperature)
                text = clean_output(raw)
                if not text:
                    raise EngineError("Model trả về bản dịch rỗng")
                return text
            except httpx.ConnectError as exc:
                raise EngineError(f"Không kết nối được tới {self.base_url}. Máy dịch đã được bật chưa?") from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                detail = exc.response.text[:300]
                if status in (401, 403):
                    raise EngineError(f"API từ chối truy cập ({status}). Kiểm tra API key.") from exc
                if status == 404:
                    raise EngineError(f"Không tìm thấy model '{self.model}' hoặc sai địa chỉ API. {detail}") from exc
                last_error = EngineError(f"Lỗi API {status}: {detail}")
                if status == 429:
                    time.sleep(min(60, 10 * (attempt + 1)))
                    continue
            except (httpx.TimeoutException, httpx.RemoteProtocolError, EngineError) as exc:
                last_error = exc
            time.sleep(2 * (attempt + 1))
        raise EngineError(str(last_error) if last_error else "Dịch thất bại")

    def list_models(self) -> list[str]:
        raise NotImplementedError

    def _chat(self, system: str, user: str, temperature: float) -> str:
        raise NotImplementedError

    def close(self) -> None:
        self.client.close()


class OllamaEngine(Engine):
    name = "ollama"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.base_url = re.sub(r"/v1$", "", self.base_url)
        self._send_think = True

    def _chat(self, system: str, user: str, temperature: float) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": 8192},
        }
        if self._send_think:
            payload["think"] = False  # tắt suy luận của Qwen3... cho nhanh
        response = self.client.post(f"{self.base_url}/api/chat", json=payload)
        if response.status_code == 400 and "think" in response.text.lower() and self._send_think:
            self._send_think = False
            payload.pop("think")
            response = self.client.post(f"{self.base_url}/api/chat", json=payload)
        response.raise_for_status()
        return response.json().get("message", {}).get("content", "")

    def list_models(self) -> list[str]:
        response = self.client.get(f"{self.base_url}/api/tags")
        response.raise_for_status()
        return sorted(m["name"] for m in response.json().get("models", []))


class OpenAICompatEngine(Engine):
    name = "openai"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}

    def _chat(self, system: str, user: str, temperature: float) -> str:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": temperature,
        }
        response = self.client.post(f"{self.base_url}/chat/completions", json=payload, headers=self._headers())
        response.raise_for_status()
        choices = response.json().get("choices") or []
        if not choices:
            raise EngineError("API không trả về kết quả")
        return choices[0].get("message", {}).get("content") or ""

    def list_models(self) -> list[str]:
        response = self.client.get(f"{self.base_url}/models", headers=self._headers())
        response.raise_for_status()
        data = response.json().get("data") or response.json().get("models") or []
        names = [m.get("id") or m.get("name", "") for m in data]
        return sorted(n.removeprefix("models/") for n in names if n)


def make_engine(settings: dict) -> Engine:
    kind = settings.get("engine")
    if kind == "agent":
        raise EngineError("Chế độ agent không gọi model trực tiếp")
    if not settings.get("model"):
        raise EngineError("Chưa chọn model dịch. Vào Cài đặt để chọn.")
    cls = OllamaEngine if kind == "ollama" else OpenAICompatEngine
    if not settings.get("base_url"):
        raise EngineError("Chưa nhập địa chỉ API. Vào Cài đặt để nhập.")
    return cls(
        settings["base_url"],
        settings["model"],
        api_key=settings.get("api_key", ""),
        temperature=float(settings.get("temperature", 0.2)),
        timeout=float(settings.get("timeout", 300)),
    )
