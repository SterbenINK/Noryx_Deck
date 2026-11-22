"""Логотип Noryx — «дек 2×2»: тусклая плитка, светящаяся (фиолетовая), белая и круглая ручка.
Рисует PIL для трея (Windows / Linux) и файла-значка ярлыка. Тот же рисунок в SVG — packaging/public/overlay/logo.svg
(README), значок вкладки — в client/index.html; координаты — в сетке 512."""

BG = ((0x1B, 0x11, 0x30), (0x0A, 0x07, 0x11))     # фон — диагональный градиент
LIT = ((0xC8, 0x96, 0xFF), (0x7C, 0x3A, 0xED))    # светящаяся плитка
DIM = (0x2A, 0x21, 0x40)
INK = (0xEE, 0xEA, 0xF8)


def _grad(size, a, b):
    """Квадрат size×size с диагональным градиентом a (левый верх) → b (правый низ)."""
    from PIL import Image
    n = 128
    g = Image.new("L", (n, n))
    g.putdata([(x + y) * 255 // (2 * (n - 1)) for y in range(n) for x in range(n)])
    g = g.resize((size, size), Image.BILINEAR)
    return Image.composite(Image.new("RGB", (size, size), b), Image.new("RGB", (size, size), a), g)


def image(size=64):
    """RGBA size×size. Рисуем вчетверо крупнее и уменьшаем — сглаженные края и на 16 px."""
    from PIL import Image, ImageDraw
    big = max(size * 4, 256)
    k = big / 512
    im = Image.new("RGBA", (big, big), (0, 0, 0, 0))

    def box(x0, y0, x1, y1):
        return tuple(round(v * k) for v in (x0, y0, x1, y1))

    def paste(fill, rect, r):                # градиент / цвет по маске скруглённого прямоугольника
        m = Image.new("L", (big, big), 0)
        ImageDraw.Draw(m).rounded_rectangle(box(*rect), radius=round(r * k), fill=255)
        if isinstance(fill[0], tuple):      # градиент — по самой фигуре, как в SVG
            x0, y0, x1, y1 = box(*rect)
            src = Image.new("RGB", (big, big))
            src.paste(_grad(max(x1 - x0, y1 - y0), *fill), (x0, y0))
        else:
            src = Image.new("RGB", (big, big), fill)
        im.paste(src, (0, 0), m)

    paste(BG, (16, 16, 496, 496), 124)
    paste(DIM, (104, 104, 242, 242), 38)
    paste(LIT, (270, 104, 408, 242), 38)
    paste(INK, (104, 270, 242, 408), 38)
    d = ImageDraw.Draw(im)
    d.ellipse(box(339 - 80, 339 - 80, 339 + 80, 339 + 80), outline=INK, width=round(22 * k))   # ручка
    d.ellipse(box(339 - 11, 300 - 11, 339 + 11, 300 + 11), fill=INK)
    return im.resize((size, size), Image.LANCZOS)


if __name__ == "__main__":                   # python server/logo.py out.png 512
    import sys
    image(int(sys.argv[2]) if len(sys.argv) > 2 else 512).save(sys.argv[1] if len(sys.argv) > 1 else "noryx.png")
