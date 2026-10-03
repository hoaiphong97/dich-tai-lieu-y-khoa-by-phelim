<p align="center"><img src="packaging/icon.png" width="96" alt=""></p>

<h1 align="center">Dịch Tài Liệu Y Khoa</h1>

<p align="center">App cài trên Windows và macOS: dịch tài liệu y khoa PDF tiếng Anh sang tiếng Việt — ra <b>PDF giữ nguyên bố cục và hình</b>, kèm <b>Markdown có hình</b> để đọc gọn.<br>
Chạy được hoàn toàn miễn phí trên máy (Ollama, LM Studio), hoặc qua Gemini API, hoặc giao cho agent như Claude Code.</p>

---

## Tính năng

- **3 cách dịch**
  - **Toàn bộ tài liệu**
  - **Khoảng trang**: `từ trang 10 đến 25`, hoặc nhiều khoảng `1-10, 15, 20-25`. Có thể **chọn theo chương** nếu PDF có mục lục.
  - **Dịch nhanh**: dán một đoạn văn, dịch ngay.
- **PDF giữ bố cục, có hình**: chữ tiếng Việt được đặt vào đúng chỗ chữ gốc (tự co cỡ chữ cho vừa); hình ảnh, sơ đồ, nền màu, khung bảng giữ nguyên. Nhãn chữ trong sơ đồ cũng được dịch tại chỗ. Tuỳ chọn xen kẽ trang gốc để đối chiếu.
- **Markdown kèm hình**: giữ tiêu đề, danh sách, chú thích và bảng; hình được cắt ra thư mục riêng (`hinh-<tên>/`, nhãn trong hình đã dịch). Bỏ header/footer và số trang; nối lại câu bị cắt qua cột hoặc qua trang.
- **Chữ trong hình**: dưới mỗi hình có danh sách chữ trong hình kèm bản dịch (Anh → Việt). Trong Markdown, danh sách nằm ngay dưới ảnh; trong PDF, bấm biểu tượng ghi chú ở góc hình để xem. Nhãn mà PDF lưu dạng ký tự được app tự đọc; chữ nằm trong ảnh (hình vẽ, ảnh chụp) được **agent đọc** ở chế độ agent — app xuất ảnh từng hình để agent xem.
- **So sánh trang**: xem trang gốc và trang dịch cạnh nhau ngay trong app — kể cả **trong lúc đang dịch**: lô trang nào dịch xong là xem được ngay.
- **Sách dày, file nặng**: dịch và đặt chữ vào PDF chạy chồng lên nhau theo lô 25 trang, nên RAM thấp (đo trên file 258 MB / 400 trang: cả app dùng tối đa ~580 MB).
- **Chạy song song**: nhiều luồng dịch trong cùng một job (ngữ cảnh và thuật ngữ được tính trước nên không bị lệch); chế độ agent chia việc cho nhiều agent dịch cùng lúc.
- **Dịch có ngữ cảnh**: mỗi đoạn được gửi kèm tên mục và đoạn trước đó, để model hiểu đúng "it", "this"…
- **Kho thuật ngữ**: thuật ngữ trong kho được đưa cho model dùng đúng. Tuỳ chọn ghi kèm tiếng Anh ở lần đầu xuất hiện trong mỗi mục, ví dụ `xương hàm dưới (mandible)`. Bạn tự thêm, sửa và duyệt thuật ngữ trong app.
- **Tự kiểm lỗi**: đánh dấu đoạn thiếu số liệu, sót câu tiếng Anh, dịch ngắn hoặc dài bất thường, sai thuật ngữ đã duyệt, để bạn xem lại.
- **Không mất công khi bị ngắt**: bản dịch được lưu đệm; tắt app giữa chừng thì bấm "Tiếp tục" để dịch nốt phần còn lại.

## Cài đặt (người dùng)

Tải bộ cài ở mục **[Releases](../../releases)**:

| Hệ điều hành | File |
|---|---|
| Windows 10/11 (64-bit) | `DichYKhoa-Setup-x.y.z.exe` |
| macOS, chip Apple (M1–M4) | `DichYKhoa-x.y.z-macos-arm64.dmg` |
| macOS, chip Intel | `DichYKhoa-x.y.z-macos-x86_64.dmg` |

> **Lần đầu mở app:** bộ cài chưa được ký số (cần chứng chỉ trả phí), nên hệ điều hành có thể cảnh báo.
> - **Windows**: SmartScreen báo "Windows protected your PC" → bấm **More info** → **Run anyway**.
> - **macOS**: chuột phải vào app → **Open** → **Open**. Hoặc chạy lệnh `xattr -cr /Applications/DichYKhoa.app`.

### Chọn nguồn dịch (trong app: Cài đặt)

| Nguồn | Chi phí | Ghi chú |
|---|---|---|
| **Ollama** (khuyên dùng) | Miễn phí | Cài [Ollama](https://ollama.com), chạy `ollama pull qwen3:8b` (hoặc model khác), rồi chọn trong app. |
| **LM Studio** | Miễn phí | Bật Local Server trong LM Studio. |
| **Gemini API** | Có gói miễn phí | Cần API key. Gói miễn phí có giới hạn lượt gọi, và Google có thể dùng nội dung gửi lên để cải thiện sản phẩm. |
| **API tương thích OpenAI** | Tuỳ dịch vụ | Mọi dịch vụ có `/chat/completions`. |
| **Agent** (Claude Code…) | Theo gói agent của bạn | App tách đoạn và tạo `AGENT_TASK.md` (hoặc `AGENT_TASK_1.md`, `_2.md`… khi chia cho nhiều agent); agent dịch xong thì bấm "Ghép bản dịch". |

Chọn model theo phần cứng: GPU 6–8 GB thì dùng model 4–8B; GPU 12–16 GB hoặc Mac 16 GB thì 12–14B; GPU 24 GB hoặc Mac 32 GB+ thì 27–32B.
Cách chọn tốt nhất là dán cùng một đoạn vào **Dịch nhanh**, thử vài model rồi so sánh.

**Dịch nhanh hơn với Ollama:** nếu GPU còn dư bộ nhớ, đặt biến môi trường `OLLAMA_NUM_PARALLEL=2` (hoặc 3–4), khởi động lại Ollama, rồi đặt "Số luồng song song" trong app bằng đúng số đó.

**File lớn:** nên chọn file bằng nút **Chọn file PDF** (mở thẳng file gốc); kéo thả sẽ chép thêm một bản vào thư mục dữ liệu. File vài trăm MB thường là sách scan — app sẽ báo số trang scan ngay khi mở.

### Dữ liệu được lưu ở đâu

- File kết quả `.vi.pdf`, `.vi.md` và thư mục hình `hinh-…/`: mặc định trong `Documents/DichYKhoa` (đổi được trong Cài đặt).
- Cài đặt, bộ nhớ đệm, lịch sử, thuật ngữ của bạn:
  - Windows: `%APPDATA%\DichYKhoa`
  - macOS: `~/Library/Application Support/DichYKhoa`

## Giới hạn hiện tại

- **PDF scan (ảnh chụp)** chưa đọc được chữ, vì chưa có OCR. App sẽ báo các trang này.
- Chữ nằm trong ảnh không được dịch tại chỗ (ảnh giữ nguyên); chỉ được liệt kê kèm bản dịch dưới hình, và chỉ ở chế độ agent.
- Bố cục phức tạp (3 cột, hộp chữ đặt chồng lên nhau, bảng không có đường kẻ) có thể bị xếp sai thứ tự đoạn.
- Trong PDF, chữ dùng font Noto Sans (không giữ font gốc của sách). Tiếng Việt thường dài hơn tiếng Anh nên chữ có thể nhỏ hơn bản gốc; khi quá chật, chữ được nới xuống khoảng trống bên dưới.
- Header/footer lặp lại (tên sách, số trang) được giữ nguyên tiếng Anh trong PDF.
- **Thuật ngữ đi kèm app đang ở trạng thái "Nháp"** và cần người có chuyên môn duyệt. Bản dịch máy luôn cần đối chiếu bản gốc trước khi dùng cho mục đích chuyên môn.

---

## Phát triển

Yêu cầu Python 3.10+.

```bash
# Windows
run-dev.bat
# macOS / Linux
./run-dev.sh
```

Hoặc làm thủ công:

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
python -m dichyk                  # mở cửa sổ app
python -m dichyk --browser        # mở bằng trình duyệt
python -m pytest -q tests         # chạy test
```

### Cấu trúc

```
dichyk/
  extract.py     PDF → khối (tiêu đề, đoạn, danh sách, chú thích, bảng), thứ tự đọc 2 cột, bỏ header/footer
  pipeline.py    khối → đoạn cần dịch (ngữ cảnh, thuật ngữ) → bản dịch → Markdown; chế độ agent
  pdfout.py      đặt bản dịch vào PDF gốc (giữ hình), cắt hình cho Markdown, ảnh trang để so sánh
  figtext.py     chữ trong hình: gom nhãn + chữ agent đọc từ ảnh → danh sách Anh → Việt dưới hình
  glossary.py    kho thuật ngữ TSV + bộ khớp (cụm dài nhất, số nhiều, viết tắt)
  engines.py     Ollama, API tương thích OpenAI
  prompts.py     prompt dịch
  validate.py    kiểm lỗi bản dịch bằng quy tắc
  cache.py       bộ nhớ đệm SQLite
  jobs.py        chạy nền, tiến độ, huỷ, tiếp tục, lịch sử
  server.py      API (FastAPI) cho giao diện
  web/           giao diện (HTML/CSS/JS thuần, không cần build)
  __main__.py    khởi động server + cửa sổ app (pywebview)
kit/termbase/    thuật ngữ đi kèm app (TSV)
packaging/       PyInstaller spec, bộ cài Windows (Inno Setup), icon
.github/workflows/build.yml   build bộ cài Windows + macOS
```

### Phát hành bộ cài

Bộ cài được build tự động trên GitHub Actions, vì bản Windows phải build trên Windows và bản macOS phải build trên macOS.

1. Tăng `APP_VERSION` trong `dichyk/paths.py` (ví dụ `0.1.0` → `0.2.0`).
2. Commit và push lên `main`.

Nếu phiên bản đó chưa có Release, workflow sẽ chạy test, build `.exe` (Windows) và `.dmg` (macOS Apple Silicon + Intel), rồi tự tạo tag `vX.Y.Z` và Release kèm các file.
Push mà không đổi phiên bản thì workflow chỉ chạy test.
Muốn build thử mà không phát hành: vào tab **Actions** → **Build bộ cài** → **Run workflow**, rồi tải file trong mục Artifacts.

### Giấy phép

App dùng [PyMuPDF](https://github.com/pymupdf/PyMuPDF), vốn theo giấy phép **AGPL-3.0**. Nếu phát hành app cho người khác, mã nguồn app cũng cần công khai theo AGPL-3.0 (hoặc mua giấy phép thương mại của PyMuPDF).
