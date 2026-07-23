from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont


FONT_PATHS = (
    Path("C:/Windows/Fonts/arial.ttf"),
    Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        Path("C:/Windows/Fonts/arialbd.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ) if bold else FONT_PATHS
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def _format_value(value: float, value_format: str) -> str:
    if value_format == "currency":
        return f"{value:,.0f} ₽".replace(",", " ")
    if value_format == "percent":
        return f"{value:g}%"
    return f"{value:,.0f}".replace(",", " ")


def render_chart_png(presentation: dict[str, Any]) -> bytes | None:
    chart = presentation.get("chart")
    if not chart:
        return None
    labels = [str(label) for label in chart.get("labels", [])]
    values = [float(value or 0) for value in chart.get("values", [])]
    if not labels or not values:
        return None

    width, height = 1200, 700
    image = Image.new("RGB", (width, height), "#f7f8fa")
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((35, 35, width - 35, height - 35), 26, fill="white")
    draw.text(
        (80, 72),
        presentation.get("title", "Отчёт"),
        fill="#171717",
        font=_font(34, bold=True),
    )
    draw.text(
        (80, 120),
        chart.get("title", ""),
        fill="#737373",
        font=_font(22),
    )

    chart_left, chart_top = 95, 190
    chart_width, chart_height = 1030, 360
    max_value = max(values) or 1
    count = min(len(labels), 12)
    slot = chart_width / count
    bar_width = max(18, min(64, slot * 0.58))
    colors = ("#10a37f", "#4f7cff", "#8b5cf6", "#f59e0b")

    for index, (label, value) in enumerate(zip(labels[:count], values[:count])):
        x1 = chart_left + index * slot + (slot - bar_width) / 2
        bar_height = max(4, value / max_value * chart_height)
        y1 = chart_top + chart_height - bar_height
        draw.rounded_rectangle(
            (x1, y1, x1 + bar_width, chart_top + chart_height),
            8,
            fill=colors[index % len(colors)],
        )
        value_text = _format_value(value, chart.get("format", "number"))
        value_box = draw.textbbox((0, 0), value_text, font=_font(16, bold=True))
        draw.text(
            (x1 + bar_width / 2 - (value_box[2] - value_box[0]) / 2, y1 - 28),
            value_text,
            fill="#333333",
            font=_font(16, bold=True),
        )
        short_label = label if len(label) <= 16 else f"{label[:15]}…"
        label_box = draw.textbbox((0, 0), short_label, font=_font(15))
        draw.text(
            (
                x1 + bar_width / 2 - (label_box[2] - label_box[0]) / 2,
                chart_top + chart_height + 18,
            ),
            short_label,
            fill="#555555",
            font=_font(15),
        )

    output = io.BytesIO()
    image.save(output, format="PNG", optimize=True)
    return output.getvalue()
