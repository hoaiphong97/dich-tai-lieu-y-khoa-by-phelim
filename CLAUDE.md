# Hướng dẫn cho agent

## Dự án
App desktop (Windows/macOS) dịch PDF y khoa Anh → Việt ra Markdown. Python + FastAPI + giao diện web thuần, đóng gói bằng PyInstaller + pywebview. Xem README.md.

## Lệnh thường dùng
- Chạy test: `python -m pytest -q tests`
- Chạy app để phát triển: `python -m dichyk --browser` (hoặc `--server-only`)
- Build thử: `pyinstaller packaging/dichyk.spec --noconfirm`

## Quy ước
- Chuỗi hiển thị cho người dùng viết bằng tiếng Việt có dấu, lưu UTF-8 (NFC).
- Mọi file app ghi ra phải nằm trong `paths.data_dir()` hoặc thư mục kết quả người dùng chọn. Thư mục cài app là chỉ-đọc.
- Đổi nội dung prompt thì tăng `PROMPT_VERSION` trong `dichyk/prompts.py`, để bộ nhớ đệm không trả bản dịch cũ.
- Thuật ngữ đi kèm app nằm ở `kit/termbase/*.tsv`. Chỉ đổi `status` sang `approved` khi đã có người có chuyên môn duyệt.

## Khi được nhờ dịch một job (chế độ agent)
Người dùng sẽ đưa đường dẫn tới `AGENT_TASK.md` trong thư mục job. Đọc file đó và làm đúng theo hướng dẫn:
đọc `segments.jsonl`, ghi `translations.jsonl` (`{"id", "dst"}` mỗi dòng), dịch theo lô và ghi nối tiếp.
Giữ nguyên số liệu, đơn vị, viết tắt. Dùng đúng thuật ngữ trong trường `terms`. Không thêm hay bớt nội dung.
Nếu task có mục "Chữ trong hình": dịch xong các đoạn thì mở từng ảnh trong `hinh-agent/` (danh sách ở `figures*.jsonl`),
đọc chữ trong hình (bỏ chữ đã có trong `known`, chữ cái/số đánh dấu, số đo) và ghi `figure_texts*.jsonl`:
`{"figure": "fig-0012", "items": [{"en": "...", "vi": "..."}]}` mỗi hình một dòng; hình không có chữ ghi `"items": []`.
