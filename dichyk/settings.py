"""Cài đặt của người dùng, lưu ở <thư mục dữ liệu>/settings.json."""

from __future__ import annotations

import json
import threading

from . import paths

# Cấu hình sẵn cho các nguồn dịch phổ biến; giao diện dùng để điền nhanh.
PRESETS = {
    "ollama": {
        "label": "Ollama (chạy trên máy)",
        "engine": "ollama",
        "base_url": "http://localhost:11434",
        "needs_key": False,
    },
    "lmstudio": {
        "label": "LM Studio (chạy trên máy)",
        "engine": "openai",
        "base_url": "http://localhost:1234/v1",
        "needs_key": False,
    },
    "gemini": {
        "label": "Google Gemini API (có gói miễn phí)",
        "engine": "openai",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "needs_key": True,
    },
    "openai": {
        "label": "API tương thích OpenAI (tùy chỉnh)",
        "engine": "openai",
        "base_url": "",
        "needs_key": True,
    },
    "agent": {
        "label": "Agent (Claude Code…) — dịch qua file",
        "engine": "agent",
        "base_url": "",
        "needs_key": False,
    },
}

DEFAULTS = {
    "preset": "ollama",
    "engine": "ollama",
    "base_url": "http://localhost:11434",
    "api_key": "",
    "model": "",
    "temperature": 0.2,
    "workers": 1,
    "timeout": 300,
    "bilingual_terms": True,  # lần đầu gặp thuật ngữ trong mục: "xương hàm dưới (mandible)"
    "keep_source": False,  # kèm bản gốc tiếng Anh dưới mỗi đoạn
    "detect_tables": True,
    "output_pdf": True,  # PDF giữ bố cục, có hình
    "output_md": True,  # Markdown kèm thư mục hình
    "pdf_interleave": False,  # PDF xen kẽ trang gốc và trang dịch
    "output_dir": "",
}

_lock = threading.Lock()


def _file():
    return paths.data_dir() / "settings.json"


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(_file(), encoding="utf-8") as stream:
            stored = json.load(stream)
        data.update({k: v for k, v in stored.items() if k in DEFAULTS})
    except (OSError, ValueError):
        pass
    if not data["output_dir"]:
        data["output_dir"] = str(paths.default_output_dir())
    return data


def save(update: dict) -> dict:
    with _lock:
        data = load()
        for key, value in update.items():
            if key not in DEFAULTS:
                continue
            default = DEFAULTS[key]
            if isinstance(default, bool):
                value = bool(value)
            elif isinstance(default, int):
                value = max(1, min(8, int(value))) if key == "workers" else int(value)
            elif isinstance(default, float):
                value = max(0.0, min(1.5, float(value)))
            else:
                value = str(value).strip()
            data[key] = value
        with open(_file(), "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, indent=2)
        return data


def public(data: dict) -> dict:
    """Bản gửi cho giao diện: không lộ API key, chỉ báo đã có hay chưa."""
    out = dict(data)
    out["api_key_set"] = bool(data.get("api_key"))
    out["api_key"] = ""
    return out
