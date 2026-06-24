#!/usr/bin/env python3
"""Generate the app icon assets."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ASSET_DIR = Path(__file__).resolve().parent / "assets"


def draw_icon(size: int) -> Image.Image:
    scale = size / 256
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)

    def box(values: tuple[int, int, int, int]) -> tuple[int, int, int, int]:
        return tuple(round(value * scale) for value in values)

    radius = round(52 * scale)
    draw.rounded_rectangle(box((16, 16, 240, 240)), radius=radius, fill="#1f7aec")
    draw.rounded_rectangle(box((16, 16, 240, 240)), radius=radius, outline="#1258b7", width=max(1, round(4 * scale)))

    draw.rounded_rectangle(box((54, 148, 202, 196)), radius=round(18 * scale), fill="#ffffff")
    draw.polygon([box((92, 76, 164, 76))[0:2], box((164, 76, 164, 76))[2:4], box((128, 150, 128, 150))[0:2]], fill="#ffffff")
    draw.rectangle(box((110, 68, 146, 150)), fill="#ffffff")

    draw.polygon([box((165, 70, 165, 70))[0:2], box((165, 126, 165, 126))[0:2], box((210, 98, 210, 98))[0:2]], fill="#29d3a6")
    draw.arc(box((54, 48, 210, 204)), start=25, end=320, fill="#b9fff0", width=max(3, round(9 * scale)))

    return image


def main() -> None:
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    png = draw_icon(64)
    png.save(ASSET_DIR / "app_icon.png")

    ico_images = [draw_icon(size) for size in (16, 24, 32, 48, 64, 128, 256)]
    ico_images[-1].save(ASSET_DIR / "app.ico", sizes=[(image.width, image.height) for image in ico_images])


if __name__ == "__main__":
    main()
