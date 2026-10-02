"""Cửa sổ app: js_api đưa cho pywebview chỉ được lộ ra phương thức, không lộ đối tượng cửa sổ."""

from __future__ import annotations

import inspect
import sys
import types

from dichyk import __main__ as entry


def _walk_like_pywebview(obj, seen=None, found=None):
    """Bắt chước webview.util.inject_pywebview.get_functions: lần vào mọi thuộc tính công khai."""
    seen = seen if seen is not None else set()
    found = found if found is not None else []
    if id(obj) in seen:
        return found
    seen.add(id(obj))
    for name in dir(obj):
        if name.startswith("_"):
            continue
        attr = getattr(obj, name)
        if inspect.ismethod(attr) or inspect.isfunction(attr):
            found.append(name)
        elif inspect.isclass(attr) or (not callable(attr) and hasattr(attr, "__module__")):
            _walk_like_pywebview(attr, seen, found)
    return found


class _FakeWindow:
    @property
    def native(self):  # cửa sổ thật: cây đối tượng WinForms/Cocoa khổng lồ, chạm vào là treo
        raise AssertionError("pywebview đã lần vào đối tượng cửa sổ")


def test_main_does_not_expose_window_to_js(monkeypatch):
    calls = {}

    fake = types.ModuleType("webview")

    def create_window(title, url, js_api=None, **kwargs):
        calls["api"] = js_api
        return _FakeWindow()

    def start(*args, **kwargs):  # lúc trang tải xong pywebview mới duyệt js_api
        try:
            calls["functions"] = _walk_like_pywebview(calls["api"])
        except AssertionError as exc:  # main() nuốt lỗi rồi mở trình duyệt và chờ mãi: giữ lỗi lại
            calls["functions"] = str(exc)

    fake.create_window = create_window
    fake.start = start
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(entry, "_start_server", lambda port: None)
    monkeypatch.setattr(entry, "_free_port", lambda port: port)

    assert entry.main([]) == 0
    assert calls["functions"] == ["pick_pdf"]
    assert isinstance(calls["api"]._window, _FakeWindow)  # pick_pdf vẫn có cửa sổ để mở hộp thoại
