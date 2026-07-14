#!/usr/bin/env python3
"""Generate Red Note leaderboard text images for OTC Desk Agent Arena Run #20."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Tuple

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = Path(__file__).parent

def find_font() -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """Load a CJK-capable font family at three sizes."""
    candidates = [
        # macOS system Heiti has complete CJK glyphs
        ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
        ("/System/Library/Fonts/STHeiti Light.ttc", 0),
        # Fallback: Sarasa SuperTTC index 1 is Sarasa Gothic SC
        ("/Users/fuxinyao/Library/Fonts/Sarasa-SuperTTC.ttc", 1),
    ]
    font_path = None
    font_index = 0
    for path, idx in candidates:
        if Path(path).exists():
            font_path = path
            font_index = idx
            break
    if font_path is None:
        raise RuntimeError("No CJK font found")

    def load(size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(font_path, size, index=font_index)

    return load(64), load(42), load(30)


def hex_color(hex_str: str) -> Tuple[int, int, int]:
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i : i + 2], 16) for i in (0, 2, 4))


# Palette
BG = hex_color("0B1026")
PANEL = hex_color("141C3A")
CYAN = hex_color("00E5FF")
GOLD = hex_color("FFD700")
WHITE = hex_color("F5F7FF")
GRAY = hex_color("8A94A8")
GREEN = hex_color("00D68F")
YELLOW = hex_color("FFC857")
RED = hex_color("FF5C5C")


def new_image() -> Tuple[Image.Image, ImageDraw.ImageDraw]:
    img = Image.new("RGB", (1024, 1536), BG)
    draw = ImageDraw.Draw(img)
    return img, draw


def draw_panel(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, radius: int = 20) -> None:
    draw.rounded_rectangle([x, y, x + w, y + h], radius=radius, fill=PANEL)


def text_size(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont) -> Tuple[int, int]:
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]


def draw_bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct: float, color: Tuple[int, int, int]) -> None:
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=hex_color("1F2947"))
    fill_w = int(w * max(0.0, min(1.0, pct / 100.0)))
    if fill_w > 0:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=h // 2, fill=color)


LEADERBOARD = [
    {"rank": 1, "model": "GPT-5.6 Terra", "ovr": 86, "con": 92, "grd": 99, "adh": 68, "syn": 99, "prc": 92, "eff": 76},
    {"rank": 2, "model": "GLM 5.2", "ovr": 85, "con": 96, "grd": 99, "adh": 74, "syn": 99, "prc": 92, "eff": 60},
    {"rank": 3, "model": "DeepSeek V4 Pro", "ovr": 84, "con": 96, "grd": 99, "adh": 68, "syn": 86, "prc": 86, "eff": 82},
    {"rank": 3, "model": "GPT-5.6 Luna", "ovr": 84, "con": 96, "grd": 99, "adh": 74, "syn": 99, "prc": 92, "eff": 55},
    {"rank": 5, "model": "MiMo V2.5 Pro", "ovr": 82, "con": 92, "grd": 99, "adh": 68, "syn": 99, "prc": 90, "eff": 54},
]


def generate_image_1() -> Path:
    img, draw = new_image()
    title_font, subtitle_font, body_font = find_font()

    # Header
    draw.text((512, 80), "OTC Desk Agent Arena", font=title_font, fill=WHITE, anchor="mm")
    draw.text((512, 160), "Run #20 Leaderboard | Run #20 排行榜", font=subtitle_font, fill=CYAN, anchor="mm")
    draw.text((512, 220), "Top 5 by OVR (overall rating) | OVR 前五名", font=body_font, fill=GRAY, anchor="mm")

    y = 300
    panel_w = 920
    panel_h = 210
    x = 52
    for item in LEADERBOARD:
        draw_panel(draw, x, y, panel_w, panel_h, 24)
        # Rank badge
        rank_color = GOLD if item["rank"] == 1 else (CYAN if item["rank"] <= 3 else WHITE)
        draw.rounded_rectangle([x + 24, y + 24, x + 96, y + 96], radius=16, fill=rank_color)
        draw.text((x + 60, y + 60), str(item["rank"]), font=subtitle_font, fill=BG, anchor="mm")

        draw.text((x + 120, y + 40), item["model"], font=subtitle_font, fill=WHITE)
        draw.text((x + 120, y + 92), f"OVR {item['ovr']}", font=body_font, fill=GOLD)

        # OVR bar
        draw_bar(draw, x + 260, y + 55, 620, 28, item["ovr"], CYAN)

        # Card stats
        stats = [f"CON {item['con']}", f"EFF {item['eff']}", f"GRD {item['grd']}", f"ADH {item['adh']}", f"SYN {item['syn']}", f"PRC {item['prc']}"]
        sx = x + 120
        sy = y + 132
        for i, stat in enumerate(stats):
            draw.text((sx + i * 132, sy), stat, font=body_font, fill=GRAY)

        y += panel_h + 24

    # Footer
    draw.text((512, 1470), "Full report: docs/arena/2026-07-13-run20-otc-desk-agent-arena.md", font=body_font, fill=GRAY, anchor="mm")

    out_path = OUT_DIR / "leaderboard-01-top5.png"
    img.save(out_path, "PNG")
    return out_path


def generate_image_2() -> Path:
    img, draw = new_image()
    title_font, subtitle_font, body_font = find_font()

    draw.text((512, 80), "The Grok Inversion", font=title_font, fill=WHITE, anchor="mm")
    draw.text((512, 160), "Capability ≠ Best Operator | 能力强 ≠ 最适合上岗", font=subtitle_font, fill=CYAN, anchor="mm")

    # Grok panel
    draw_panel(draw, 52, 260, 920, 420, 24)
    draw.text((512, 320), "Grok 4.5", font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 380), "Highest objective score in the field", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 430), "全场最高客观分", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 500), "Obj 91.0  →  OVR 73  (T-10)", font=subtitle_font, fill=YELLOW, anchor="mm")
    draw.text((512, 560), "EFF only 4 · ~52 tool calls per run", font=body_font, fill=RED, anchor="mm")
    draw.text((512, 600), "效率仅 4 · 每次约 52 次工具调用", font=body_font, fill=RED, anchor="mm")

    # Terra panel
    draw_panel(draw, 52, 720, 920, 420, 24)
    draw.text((512, 780), "GPT-5.6 Terra", font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 840), "Wins the board", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 890), "赢得总榜第一", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 960), "Obj 89.8  →  OVR 86  (#1)", font=subtitle_font, fill=GOLD, anchor="mm")
    draw.text((512, 1020), "EFF 76 · lean and steady", font=body_font, fill=GREEN, anchor="mm")
    draw.text((512, 1060), "效率 76 · 精简且稳定", font=body_font, fill=GREEN, anchor="mm")

    # Takeaway
    draw_panel(draw, 52, 1180, 920, 240, 24)
    draw.text((512, 1240), "Key Takeaway | 关键结论", font=subtitle_font, fill=CYAN, anchor="mm")
    draw.text((512, 1310), "For unattended cron jobs, read EFF + CON first.", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 1360), "无人值守任务，优先看效率与稳定性。", font=body_font, fill=WHITE, anchor="mm")

    out_path = OUT_DIR / "leaderboard-02-grok-inversion.png"
    img.save(out_path, "PNG")
    return out_path


def generate_image_3() -> Path:
    img, draw = new_image()
    title_font, subtitle_font, body_font = find_font()

    draw.text((512, 80), "What Separates Winners", font=title_font, fill=WHITE, anchor="mm")
    draw.text((512, 160), "EFF + CON decide deployability | 效率与稳定性决定能否上线", font=subtitle_font, fill=CYAN, anchor="mm")

    # DeepSeek panel
    draw_panel(draw, 52, 260, 920, 420, 24)
    draw.text((512, 320), "DeepSeek V4 Pro", font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 380), "The reliability exemplar", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 430), "可靠性标杆", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 500), "Two trials: byte-identical 84.6 / 84.6", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 550), "两次试验：结果完全一致", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 610), "CON 96  ·  EFF 82  (highest)", font=subtitle_font, fill=GREEN, anchor="mm")

    # Sonnet panel
    draw_panel(draw, 52, 720, 920, 420, 24)
    draw.text((512, 780), "Claude Sonnet 5", font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 840), "High ceiling, high variance", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 890), "上限高，波动大", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 960), "Trial 1: OVR 86  ·  Trial 2: OVR 77", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 1010), "两次试验分差 9 分", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 1070), "CON 69  →  final OVR 77", font=subtitle_font, fill=YELLOW, anchor="mm")

    # Bottom insight
    draw_panel(draw, 52, 1180, 920, 240, 24)
    draw.text((512, 1240), "Practitioner's Read | 实践者视角", font=subtitle_font, fill=CYAN, anchor="mm")
    draw.text((512, 1310), "Best all-round pick: GPT-5.6 Terra", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 1360), "最佳全能选择：GPT-5.6 Terra", font=body_font, fill=WHITE, anchor="mm")

    out_path = OUT_DIR / "leaderboard-03-eff-con.png"
    img.save(out_path, "PNG")
    return out_path


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    paths = [generate_image_1(), generate_image_2(), generate_image_3()]
    result = {"ok": True, "image_paths": [str(p) for p in paths]}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
