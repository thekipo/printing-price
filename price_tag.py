"""
Печать ценников на термопринтере Xprinter XP-365B (TSPL).

Этикетка: 60 x 40 мм, 203 DPI (8 точек/мм) => 480 x 320 точек.
Кириллица печатается через рендер картинки (Pillow) + команда BITMAP,
т.к. встроенные шрифты TSPL кириллицу не тянут.

Печать кроссплатформенная:
- Linux: device — путь к устройству (например /dev/usb/lp0), пишем в него сырые байты.
- Windows: device — имя принтера из "Устройства и принтеры", байты уходят через
  спулер Windows как задание типа RAW (см. list_printers/_send_to_printer).
"""

import glob
import os
import sys

from PIL import Image, ImageDraw, ImageFont

# ---------- Параметры этикетки ----------
DPI_PER_MM = 8          # 203 DPI
LABEL_W_MM = 60
LABEL_H_MM = 40
GAP_MM = 2

WIDTH = LABEL_W_MM * DPI_PER_MM    # 480
HEIGHT = LABEL_H_MM * DPI_PER_MM   # 320

SHOP_NAME = "Step Up"

IS_WINDOWS = sys.platform == "win32"


def list_printers() -> list[str]:
    """Принтеры, установленные в Windows. На Linux всегда пустой список —
    там печать идёт по пути устройства, а не по имени принтера."""
    if not IS_WINDOWS:
        return []
    import win32print
    flags = win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS
    return [p[2] for p in win32print.EnumPrinters(flags)]


def _default_device() -> str:
    if IS_WINDOWS:
        try:
            import win32print
            return win32print.GetDefaultPrinter()
        except Exception:
            return ""
    return "/dev/usb/lp0"


DEVICE = _default_device()


def _send_to_printer(payload: bytes, device: str) -> None:
    if IS_WINDOWS:
        import win32print
        h = win32print.OpenPrinter(device)
        try:
            win32print.StartDocPrinter(h, 1, ("Ценник", None, "RAW"))
            try:
                win32print.StartPagePrinter(h)
                win32print.WritePrinter(h, payload)
                win32print.EndPagePrinter(h)
            finally:
                win32print.EndDocPrinter(h)
        finally:
            win32print.ClosePrinter(h)
    else:
        with open(device, "wb") as f:
            f.write(payload)


# ---------- Шрифты ----------
# Ищем в системе шрифт с кириллицей. Порядок = приоритет.

if IS_WINDOWS:
    _FONT_DIRS = [os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")]
else:
    _FONT_DIRS = ["/usr/share/fonts", os.path.expanduser("~/.fonts")]


def _find_font(*names: str) -> str:
    for base in _FONT_DIRS:
        for name in names:
            hits = glob.glob(os.path.join(base, "**", name), recursive=True)
            if hits:
                return hits[0]
    raise FileNotFoundError(
        f"Не найден ни один из шрифтов: {names}\n"
        + ("Установи Arial или другой шрифт с кириллицей в Windows."
           if IS_WINDOWS else
           "Установи: sudo dnf install liberation-sans-fonts dejavu-sans-fonts")
    )


F_BOLD = _find_font(
    "LiberationSans-Bold.ttf",
    "DejaVuSans-Bold.ttf",
    "FreeSansBold.ttf",
    "arialbd.ttf",
)
F_REG = _find_font(
    "LiberationSans-Regular.ttf",
    "DejaVuSans.ttf",
    "FreeSans.ttf",
    "arial.ttf",
)


def _has_rouble(font_path: str) -> bool:
    """Есть ли в шрифте знак ₽ (U+20BD). В Liberation Sans его нет."""
    try:
        from fontTools.ttLib import TTFont
        return 0x20BD in TTFont(font_path).getBestCmap()
    except Exception:
        return False


# Если ₽ в шрифте нет — печатаем "руб.", иначе вылезет пустой квадрат.
CURRENCY = "\u20BD" if _has_rouble(F_BOLD) else "руб."


def _font(path, size):
    return ImageFont.truetype(path, size)


def _center(draw, text, font, y, img_w=WIDTH, fill=0):
    """Рисует текст по центру по горизонтали. Возвращает высоту строки."""
    bbox = draw.textbbox((0, 0), text, font=font)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (img_w - w) // 2
    draw.text((x - bbox[0], y - bbox[1]), text, font=font, fill=fill)
    return h


def _fit_font(draw, text, path, max_size, max_width, min_size=12):
    """Подбирает размер шрифта, чтобы текст влез в max_width."""
    size = max_size
    while size > min_size:
        f = _font(path, size)
        bbox = draw.textbbox((0, 0), text, font=f)
        if bbox[2] - bbox[0] <= max_width:
            return f
        size -= 2
    return _font(path, min_size)


def _wrap(draw, text, font, max_width):
    """Переносит текст по словам."""
    words = text.split()
    lines, cur = [], ""
    for w in words:
        test = f"{cur} {w}".strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        if bbox[2] - bbox[0] <= max_width or not cur:
            cur = test
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


SHELF_AREA_H = 46  # место под номер полки внизу этикетки


def render_tag(model: str, price: int, old_price: int | None = None,
               size: str | None = None, shelf: str | int | None = None) -> Image.Image:
    """Рисует ценник как ч/б картинку."""
    img = Image.new("1", (WIDTH, HEIGHT), 1)  # 1 = белый
    d = ImageDraw.Draw(img)

    PAD = 14
    inner_w = WIDTH - 2 * PAD

    # если указана полка — резервируем место под неё внизу, цена поднимается выше
    price_bottom = HEIGHT - (SHELF_AREA_H if shelf else 0) - 14

    # --- рамка ---
    d.rectangle([4, 4, WIDTH - 5, HEIGHT - 5], outline=0, width=3)

    # --- шапка: название магазина ---
    f_shop = _font(F_BOLD, 40)
    y = 18
    h = _center(d, SHOP_NAME, f_shop, y)
    y += h + 12

    # линия под шапкой
    d.line([PAD + 10, y, WIDTH - PAD - 10, y], fill=0, width=2)
    y += 14

    # --- модель (с переносом) ---
    f_model = _font(F_REG, 30)
    lines = _wrap(d, model, f_model, inner_w)
    if len(lines) > 2:                      # если не влезло — уменьшаем
        f_model = _font(F_REG, 25)
        lines = _wrap(d, model, f_model, inner_w)[:2]
    for ln in lines:
        h = _center(d, ln, f_model, y)
        y += h + 8

    # --- размер (опционально) ---
    if size:
        f_size = _font(F_REG, 24)
        h = _center(d, f"Размер: {size}", f_size, y)
        y += h + 6

    # --- блок цен ---
    price_txt = f"{price:,}".replace(",", " ") + " " + CURRENCY

    if old_price:
        old_txt = f"{old_price:,}".replace(",", " ")
        f_old = _font(F_REG, 26)
        bbox = d.textbbox((0, 0), old_txt, font=f_old)
        ow = bbox[2] - bbox[0]

        # новая цена — крупно, подбираем размер под ширину
        f_new = _fit_font(d, price_txt, F_BOLD, 62, inner_w - ow - 30)
        bbox_n = d.textbbox((0, 0), price_txt, font=f_new)
        nw = bbox_n[2] - bbox_n[0]
        nh = bbox_n[3] - bbox_n[1]

        total = ow + 24 + nw
        x = (WIDTH - total) // 2
        base_y = price_bottom - nh

        # старая цена + зачёркивание
        oy = base_y + nh // 2 - (bbox[3] - bbox[1]) // 2
        d.text((x - bbox[0], oy - bbox[1]), old_txt, font=f_old, fill=0)
        strike_y = oy + (bbox[3] - bbox[1]) // 2
        d.line([x - 4, strike_y, x + ow + 4, strike_y], fill=0, width=3)

        # новая цена
        d.text((x + ow + 24 - bbox_n[0], base_y - bbox_n[1]),
               price_txt, font=f_new, fill=0)
    else:
        f_new = _fit_font(d, price_txt, F_BOLD, 68, inner_w)
        bbox_n = d.textbbox((0, 0), price_txt, font=f_new)
        nh = bbox_n[3] - bbox_n[1]
        _center(d, price_txt, f_new, price_bottom - nh)

    # --- номер полки (внизу, под ценой) ---
    if shelf:
        line_y = HEIGHT - SHELF_AREA_H
        d.line([PAD + 10, line_y, WIDTH - PAD - 10, line_y], fill=0, width=2)
        f_shelf = _font(F_BOLD, 30)
        _center(d, str(shelf), f_shelf, line_y + 4)

    return img


def _to_tspl_bitmap(img: Image.Image) -> bytes:
    """Конвертирует картинку в байты для команды BITMAP (режим 0, 1bpp).
    В TSPL: бит 0 = чёрная точка, бит 1 = белая."""
    img = img.convert("1")
    w, h = img.size
    width_bytes = (w + 7) // 8
    px = img.load()

    out = bytearray()
    for y in range(h):
        for bx in range(width_bytes):
            byte = 0
            for bit in range(8):
                x = bx * 8 + bit
                # белый (255) -> 1, чёрный (0) -> 0
                v = 1 if (x >= w or px[x, y]) else 0
                byte = (byte << 1) | v
            out.append(byte)
    return bytes(out), width_bytes, h


def build_tspl(img: Image.Image, copies: int = 1) -> bytes:
    """Собирает полный TSPL-пакет для печати картинки."""
    data, width_bytes, h = _to_tspl_bitmap(img)

    header = (
        f"SIZE {LABEL_W_MM} mm,{LABEL_H_MM} mm\r\n"
        f"GAP {GAP_MM} mm,0\r\n"
        f"DIRECTION 1\r\n"
        f"DENSITY 10\r\n"
        f"SPEED 4\r\n"
        f"CLS\r\n"
        f"BITMAP 0,0,{width_bytes},{h},0,"
    ).encode("ascii")

    footer = f"\r\nPRINT 1,{copies}\r\n".encode("ascii")
    return header + data + footer


def print_tag(model: str, price: int, old_price: int | None = None,
              size: str | None = None, shelf: str | int | None = None,
              copies: int = 1, device: str = DEVICE) -> None:
    """Печатает ценник на принтере."""
    img = render_tag(model, price, old_price, size, shelf)
    payload = build_tspl(img, copies)
    _send_to_printer(payload, device)


def print_batch(items: list[dict], device: str = DEVICE) -> None:
    """Печатает пачку ценников.
    items: [{"model": ..., "price": ..., "old_price": ..., "size": ..., "shelf": ..., "copies": ...}, ...]
    """
    for it in items:
        print_tag(
            model=it["model"],
            price=it["price"],
            old_price=it.get("old_price"),
            size=it.get("size"),
            shelf=it.get("shelf"),
            copies=it.get("copies", 1),
            device=device,
        )


if __name__ == "__main__":
    # превью без печати — сохраняется рядом со скриптом
    import os

    out = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "preview_tag.png")
    img = render_tag("Nike Air Zoom Superrep 3", 2990, old_price=8150, shelf=3)
    # увеличиваем x2, чтобы удобнее было разглядывать на экране
    img.convert("RGB").resize((WIDTH * 2, HEIGHT * 2)).save(out)
    print(f"Превью сохранено: {out}")
    print(f"Шрифт: {F_BOLD}")
    print(f"Валюта: {CURRENCY}")
