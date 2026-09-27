"""Sinh icon app (PNG, ICO cho Windows, ICNS cho macOS) từ mã, không cần file thiết kế."""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
TEAL = (15, 110, 106)
INK = (22, 32, 46)


def _font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf", "/System/Library/Fonts/Supplemental/Arial Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def draw(size: int = 1024) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    pad = size * 0.08
    d.rounded_rectangle([pad, pad, size - pad, size - pad], radius=size * 0.2, fill=TEAL)
    # Trang giấy cách điệu
    px0, py0, px1, py1 = size * 0.27, size * 0.22, size * 0.73, size * 0.78
    d.rounded_rectangle([px0, py0, px1, py1], radius=size * 0.035, fill=(247, 246, 242))
    font = _font(int(size * 0.2))
    text = "YK"
    box = d.textbbox((0, 0), text, font=font)
    tw, th = box[2] - box[0], box[3] - box[1]
    d.text(((size - tw) / 2 - box[0], py0 + size * 0.11 - box[1]), text, font=font, fill=INK)
    for i, width in enumerate((0.34, 0.28, 0.34)):
        y = py0 + size * (0.33 + i * 0.07)
        d.rounded_rectangle([px0 + size * 0.06, y, px0 + size * (0.06 + width), y + size * 0.03], radius=size * 0.015, fill=TEAL)
    return img


def main() -> None:
    img = draw()
    img.save(HERE / "icon.png")
    img.save(HERE / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    try:
        img.save(HERE / "icon.icns")
    except Exception as exc:  # Pillow cũ có thể không ghi được ICNS
        print("Bỏ qua icon.icns:", exc)
    print("Đã tạo icon trong", HERE)


if __name__ == "__main__":
    main()
