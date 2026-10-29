"""Draws the ClutchClip logo -> assets/logo.png and assets/clutchclip.ico. Run: python make_logo.py"""
import os

from PIL import Image, ImageDraw

S = 1024  # drawn at 1024 and downscaled, for smooth edges
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")


def logo():
    # purple gradient rounded square
    grad = Image.new("RGB", (S, S))
    px = grad.load()
    for y in range(S):
        for x in range(S):
            t = (x + y) / (2 * S)
            px[x, y] = (int(139 - 50 * t), int(92 - 40 * t), int(246 - 20 * t))
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, S - 1, S - 1), radius=S // 4, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, mask=mask)

    # white play triangle, cut in two by a diagonal gap = "clip"
    tri = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(tri)
    d.polygon([(330, 230), (330, 794), (790, 512)], fill=255)
    d.line([(250, 640), (700, 300)], fill=0, width=64)  # the cut
    img.paste(Image.new("RGBA", (S, S), "white"), mask=tri)

    # green "keep" dot on the cut
    d = ImageDraw.Draw(img)
    d.ellipse((730, 150, 870, 290), fill="#22c55e")
    return img


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    img = logo()
    img.resize((512, 512), Image.LANCZOS).save(os.path.join(OUT, "logo.png"))
    img.save(os.path.join(OUT, "clutchclip.ico"), sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("wrote", OUT)
