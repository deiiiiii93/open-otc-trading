#!/usr/bin/env python3
"""Generate Red Note artifacts for an OTC Desk Agent Arena run report.

Default workflow:
  1. Render the markdown report to HTML with docs/arena/render_report.py
  2. Screenshot the HTML with Chrome headless
  3. Crop three 1024x1536 leaderboard/insight cards from the screenshot
  4. Generate a clean iconic cover image via ZenMux gpt-image-2
  5. Write bilingual EN|CN post copy

Fallback (--use-cards):
  Render leaderboard cards with Pillow instead of HTML screenshots.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Tuple

from PIL import Image, ImageDraw, ImageFont


def find_font() -> Tuple[ImageFont.FreeTypeFont, ImageFont.FreeTypeFont, ImageFont.FreeTypeFont]:
    """Load a CJK-capable font family at three sizes."""
    env_font = os.environ.get("REDNOTES_FONT")
    candidates = []
    if env_font:
        candidates.append((env_font, 0))
    candidates.extend(
        [
            # macOS system Heiti has complete CJK glyphs
            ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
            ("/System/Library/Fonts/STHeiti Light.ttc", 0),
            # Fallback: Sarasa SuperTTC index 1 is Sarasa Gothic SC
            ("/Users/fuxinyao/Library/Fonts/Sarasa-SuperTTC.ttc", 1),
        ]
    )
    font_path = None
    font_index = 0
    for path, idx in candidates:
        if Path(path).exists():
            font_path = path
            font_index = idx
            break
    if font_path is None:
        raise RuntimeError("No CJK font found. Set REDNOTES_FONT to a .ttc/.ttf path.")

    def load(size: int) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(font_path, size, index=font_index)

    return load(64), load(42), load(30)


def hex_color(hex_str: str) -> Tuple[int, int, int]:
    hex_str = hex_str.lstrip("#")
    return tuple(int(hex_str[i : i + 2], 16) for i in (0, 2, 4))


# Palette for card fallback
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


def draw_bar(draw: ImageDraw.ImageDraw, x: int, y: int, w: int, h: int, pct: float, color: Tuple[int, int, int]) -> None:
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=hex_color("1F2947"))
    fill_w = int(w * max(0.0, min(1.0, pct / 100.0)))
    if fill_w > 0:
        draw.rounded_rectangle([x, y, x + fill_w, y + h], radius=h // 2, fill=color)


def parse_run_number(text: str) -> str:
    m = re.search(r"Run\s*#(\d+)", text)
    return m.group(1) if m else "XX"


def parse_markdown_table(lines: list[str]) -> list[dict[str, Any]]:
    """Parse a pipe-delimited markdown table into dict rows."""
    rows = []
    header: list[str] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.split("|")[1:-1]]
        if not cells:
            continue
        # Skip markdown header separator lines like |---|---|
        if all(set(c) <= {"-", ":", "|", " "} or c == "" for c in cells):
            continue
        if not header:
            header = cells
            continue
        if len(cells) != len(header):
            continue
        row = {header[i].lower(): cells[i] for i in range(len(header))}
        rows.append(row)
    return rows


def numeric(value: str) -> int:
    try:
        return int(float(value.strip().replace("**", "")))
    except ValueError:
        return 0


def clean_model(name: str) -> str:
    return re.sub(r"\*\*", "", name).strip()


def parse_leaderboard(text: str) -> list[dict[str, Any]]:
    """Find and parse the full Model Ability Card table."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if "| Model |" in line and "| OVR |" in line:
            table_lines = []
            for j in range(i, len(lines)):
                if not lines[j].strip().startswith("|"):
                    break
                table_lines.append(lines[j])
            rows = parse_markdown_table(table_lines)
            result = []
            for row in rows:
                model = row.get("model", "").strip()
                if not model or set(model) <= {"-", "|", " ", ":"}:
                    continue
                result.append(
                    {
                        "rank": row.get("rank", ""),
                        "model": clean_model(model),
                        "ovr": numeric(row.get("ovr", "0")),
                        "con": numeric(row.get("con", "0")),
                        "grd": numeric(row.get("grd", "0")),
                        "adh": numeric(row.get("adh", "0")),
                        "syn": numeric(row.get("syn", "0")),
                        "prc": numeric(row.get("prc", "0")),
                        "eff": numeric(row.get("eff", "0")),
                        "obj": numeric(row.get("obj (mean)", "0")),
                    }
                )
            return result
    return []


# ---------------------------------------------------------------------- cover ---

DEFAULT_REF_IMAGES = [
    Path(__file__).parent.parent / "assets" / "ref-layout.png",
    Path(__file__).parent.parent / "assets" / "ref-editorial.png",
    Path(__file__).parent.parent / "assets" / "ref-palette.jpg",
]


def generate_cover_prompt(run: str, leaderboard: list[dict[str, Any]]) -> str:
    """Build the Chinese Red Note cover prompt from parsed report data."""
    top = sorted(leaderboard, key=lambda r: (-r["ovr"], -r["eff"], -r["con"]))[:6]

    def row_text(idx: int, item: dict[str, Any], suffix: str = "") -> str:
        return f"{idx}. {item['model']} — {item['ovr']}{suffix}"

    board_lines = []
    if len(top) >= 1:
        board_lines.append(row_text(1, top[0], "（全场最佳）"))
    if len(top) >= 2:
        board_lines.append(row_text(2, top[1]))
    if len(top) >= 3:
        board_lines.append(row_text(3, top[2]))
    if len(top) >= 4 and top[3]["ovr"] == top[2]["ovr"]:
        board_lines.append(row_text(3, top[3]))
    elif len(top) >= 4:
        board_lines.append(row_text(4, top[3]))
    if len(top) >= 5:
        board_lines.append(row_text(5, top[4]))

    # Grok inversion candidate: high obj, low-ish ovr
    grok = next((r for r in leaderboard if "grok" in r["model"].lower()), None)
    if grok is None:
        grok = max(leaderboard, key=lambda r: (r["obj"], -r["ovr"])) if leaderboard else {"model": "Grok 4.5", "obj": 91, "ovr": 73, "eff": 4}

    board_lines.append(
        f"10. {grok['model']} — {grok['ovr']}（客观分{grok['obj']}并列第一，但工具调用开销大，效率极低）"
    )

    board_text = "\n".join(board_lines)

    return f"""根据报告内容，设计一张小红书封面。

报告：The OTC Desk Agent Arena — Methodology & Results (Run #{run})，评估{len(leaderboard)}个主流大语言模型在无人工干预下独立运营真实OTC衍生品交易台的能力。

主标题：{len(leaderboard)}大模型无人交易台真实对决
副标题：评估{len(leaderboard)}个主流大语言模型在无人工干预下独立运营真实OTC衍生品交易台的能力

OVR 综合得分排行榜（至少展示前5名及Grok）：
{board_text}

核心洞察：
大规模无人值守 AI Agent 部署，效率（EFF）与一致性（CON）比单纯客观能力更重要。

评估维度：
用六边形雷达图展示六个核心维度，OVR 综合评分放在雷达图中心：
- GRD 基础核实
- ADH 合规服从
- SYN 综合分析
- PRC 流程执行
- EFF 执行效率
- CON 一致性

底部链接栏：
- 详细报告：https://www.artena.one/arena/
- 评测项目 GitHub：https://github.com/deiiiiii93/open-otc-trading

视觉要求：
- Claude 官网式的 editorial 杂志主页风格
- 配色以淡蓝（#3A7BD5 附近）和淡白/米白（#F4F7FC 附近）为主
- 竖版 3:4 比例
- 排版留白充足、字体优雅、信息层级清晰
- 整体干净、高级、专业，适合作为小红书笔记首图"""


def generate_cover_image(
    run: str,
    output_dir: Path,
    leaderboard: list[dict[str, Any]],
    reference_images: list[Path] | None = None,
) -> Path:
    from openai import OpenAI

    api_key = os.environ.get("ZENMUX_API_KEY")
    if not api_key:
        raise SystemExit("ZENMUX_API_KEY is not set")

    client = OpenAI(base_url="https://zenmux.ai/api/v1", api_key=api_key)
    prompt = generate_cover_prompt(run, leaderboard)

    prompt_dir = Path(".context/prompts/zenmux-image-generation")
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / f"cover-rednote-run{run}.md"
    prompt_file.write_text(f"---\ntitle: OTC Desk Agent Arena Run #{run} Red Note Cover\n---\n{prompt}\n")

    refs = reference_images or DEFAULT_REF_IMAGES
    valid_refs = [str(p) for p in refs if p.exists()]
    if not valid_refs:
        # Fall back to text-only generation if reference assets are missing
        response = client.images.generate(
            model="openai/gpt-image-2",
            prompt=prompt,
            n=1,
            size="1024x1536",
            quality="high",
        )
        b64_data = response.data[0].b64_json
    else:
        # Use ZenMux image-edit endpoint with reference images for style steering
        import mimetypes

        def file_tuple(path: str) -> tuple[str, bytes, str]:
            data = Path(path).read_bytes()
            mime = mimetypes.guess_type(path)[0] or "image/png"
            return (Path(path).name, data, mime)

        image_files = [file_tuple(p) for p in valid_refs]
        response = client.images.edit(
            model="openai/gpt-image-2",
            image=image_files,
            prompt=prompt,
            n=1,
            size="1024x1536",
            quality="high",
        )
        b64_data = response.data[0].b64_json

    if not b64_data:
        raise RuntimeError("No image data returned from API")
    image_bytes = base64.b64decode(b64_data)
    out_path = output_dir / f"cover-run{run}.png"
    out_path.write_bytes(image_bytes)
    return out_path


# ---------------------------------------------------------- HTML screenshot ---

def render_html_report(source_doc: Path) -> Path:
    """Run docs/arena/render_report.py to produce the HTML report."""
    render_script = source_doc.parent / "render_report.py"
    if not render_script.exists():
        raise FileNotFoundError(f"Report renderer not found: {render_script}")
    subprocess.run(
        [sys.executable, str(render_script), str(source_doc)],
        check=True,
    )
    html_path = source_doc.with_suffix(".html")
    if not html_path.exists():
        raise FileNotFoundError(f"HTML report not generated: {html_path}")
    return html_path


def screenshot_html(html_path: Path, output_path: Path, window_height: int = 12000) -> None:
    """Use Chrome headless to capture a full-page PNG of the HTML report."""
    chrome = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    if not Path(chrome).exists():
        raise FileNotFoundError(f"Google Chrome not found at {chrome}")

    cmd = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--hide-scrollbars",
        f"--window-size=1200,{window_height}",
        f"--screenshot={output_path}",
        html_path.resolve().as_uri(),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def crop_report_screenshot(
    full_screenshot: Path,
    output_dir: Path,
    offsets: Tuple[int, int, int] = (5600, 7200, 8800),
) -> list[Path]:
    """Crop three 1024x1536 cards from the full-page screenshot.

    Defaults are tuned for the current OTC Desk Agent Arena HTML layout:
      1. section 5.1 leaderboard chart
      2. section 5.2 full card table + 5.3/5.4 insights
      3. section 5.5/6/7 deeper insights
    """
    img = Image.open(full_screenshot)
    w, h = img.size
    crop_w, crop_h = 1024, 1536
    left = (w - crop_w) // 2
    names = ["leaderboard-01-board", "leaderboard-02-full-card", "leaderboard-03-insights"]
    paths = []
    for name, top in zip(names, offsets):
        if top + crop_h > h:
            # If screenshot is shorter than expected, clamp to bottom
            top = max(0, h - crop_h)
        cropped = img.crop((left, top, left + crop_w, top + crop_h))
        out_path = output_dir / f"{name}.png"
        cropped.save(out_path, "PNG")
        paths.append(out_path)
    return paths


# ------------------------------------------------------- card fallback ---

def generate_image_top5(output_dir: Path, leaderboard: list[dict[str, Any]], run: str) -> Path:
    img, draw = new_image()
    title_font, subtitle_font, body_font = find_font()

    draw.text((512, 80), "OTC Desk Agent Arena", font=title_font, fill=WHITE, anchor="mm")
    draw.text((512, 160), f"Run #{run} Leaderboard | Run #{run} 排行榜", font=subtitle_font, fill=CYAN, anchor="mm")
    draw.text((512, 220), "Top 5 by OVR (overall rating) | OVR 前五名", font=body_font, fill=GRAY, anchor="mm")

    top5 = sorted(leaderboard, key=lambda r: (-r["ovr"], -r["eff"], -r["con"]))[:5]

    y = 300
    panel_w = 920
    panel_h = 210
    x = 52
    for item in top5:
        draw_panel(draw, x, y, panel_w, panel_h, 24)
        rank = item.get("rank", "")
        try:
            rank_int = int(rank.replace("T-", "").strip())
        except ValueError:
            rank_int = 0
        rank_color = GOLD if rank_int == 1 else (CYAN if rank_int <= 3 else WHITE)
        draw.rounded_rectangle([x + 24, y + 24, x + 96, y + 96], radius=16, fill=rank_color)
        draw.text((x + 60, y + 60), str(rank_int or rank), font=subtitle_font, fill=BG, anchor="mm")

        draw.text((x + 120, y + 40), item["model"], font=subtitle_font, fill=WHITE)
        draw.text((x + 120, y + 92), f"OVR {item['ovr']}", font=body_font, fill=GOLD)

        draw_bar(draw, x + 260, y + 55, 620, 28, item["ovr"], CYAN)

        stats = [
            f"CON {item['con']}",
            f"EFF {item['eff']}",
            f"GRD {item['grd']}",
            f"ADH {item['adh']}",
            f"SYN {item['syn']}",
            f"PRC {item['prc']}",
        ]
        sx = x + 120
        sy = y + 132
        for i, stat in enumerate(stats):
            draw.text((sx + i * 132, sy), stat, font=body_font, fill=GRAY)

        y += panel_h + 24

    draw.text((512, 1470), f"Full report: docs/arena/...run{run}...", font=body_font, fill=GRAY, anchor="mm")

    out_path = output_dir / "leaderboard-01-top5.png"
    img.save(out_path, "PNG")
    return out_path


def generate_image_inversion(output_dir: Path, leaderboard: list[dict[str, Any]], run: str) -> Path:
    img, draw = new_image()
    title_font, subtitle_font, body_font = find_font()

    draw.text((512, 80), "The Capability Inversion", font=title_font, fill=WHITE, anchor="mm")
    draw.text((512, 160), "Raw score ≠ Best operator | 能力强 ≠ 最适合上岗", font=subtitle_font, fill=CYAN, anchor="mm")

    if not leaderboard:
        out_path = output_dir / "leaderboard-02-inversion.png"
        img.save(out_path, "PNG")
        return out_path

    inversion = max(leaderboard, key=lambda r: (r["obj"], -r["ovr"]))
    winner = max(leaderboard, key=lambda r: r["ovr"])

    draw_panel(draw, 52, 260, 920, 420, 24)
    draw.text((512, 320), inversion["model"], font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 380), "Highest objective score in this run", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 430), "本场最高客观分", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 500), f"Obj {inversion['obj']}  →  OVR {inversion['ovr']}", font=subtitle_font, fill=YELLOW, anchor="mm")
    draw.text((512, 560), f"EFF {inversion['eff']} · heavy tool-call profile", font=body_font, fill=RED, anchor="mm")
    draw.text((512, 600), f"效率 {inversion['eff']} · 工具调用开销大", font=body_font, fill=RED, anchor="mm")

    draw_panel(draw, 52, 720, 920, 420, 24)
    draw.text((512, 780), winner["model"], font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 840), "Wins the board", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 890), "赢得总榜第一", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 960), f"Obj {winner['obj']}  →  OVR {winner['ovr']} (#1)", font=subtitle_font, fill=GOLD, anchor="mm")
    draw.text((512, 1020), f"EFF {winner['eff']} · lean and steady", font=body_font, fill=GREEN, anchor="mm")
    draw.text((512, 1060), f"效率 {winner['eff']} · 精简且稳定", font=body_font, fill=GREEN, anchor="mm")

    draw_panel(draw, 52, 1180, 920, 240, 24)
    draw.text((512, 1240), "Key Takeaway | 关键结论", font=subtitle_font, fill=CYAN, anchor="mm")
    draw.text((512, 1310), "For unattended deployment, read EFF + CON first.", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 1360), "无人值守任务，优先看效率与稳定性。", font=body_font, fill=WHITE, anchor="mm")

    out_path = output_dir / "leaderboard-02-inversion.png"
    img.save(out_path, "PNG")
    return out_path


def generate_image_eff_con(output_dir: Path, leaderboard: list[dict[str, Any]], run: str) -> Path:
    img, draw = new_image()
    title_font, subtitle_font, body_font = find_font()

    draw.text((512, 80), "What Separates Winners", font=title_font, fill=WHITE, anchor="mm")
    draw.text((512, 160), "EFF + CON decide deployability | 效率与稳定性决定能否上线", font=subtitle_font, fill=CYAN, anchor="mm")

    if not leaderboard:
        out_path = output_dir / "leaderboard-03-eff-con.png"
        img.save(out_path, "PNG")
        return out_path

    reliable = max(leaderboard, key=lambda r: (r["con"], r["eff"]))
    variable = min(leaderboard, key=lambda r: (r["con"], -r["ovr"]))

    draw_panel(draw, 52, 260, 920, 420, 24)
    draw.text((512, 320), reliable["model"], font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 380), "The reliability exemplar", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 430), "可靠性标杆", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 500), f"CON {reliable['con']} · EFF {reliable['eff']}", font=subtitle_font, fill=GREEN, anchor="mm")
    draw.text((512, 560), "Consistent, lean, deployable", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 600), "稳定、精简、可上线", font=body_font, fill=WHITE, anchor="mm")

    draw_panel(draw, 52, 720, 920, 420, 24)
    draw.text((512, 780), variable["model"], font=subtitle_font, fill=WHITE, anchor="mm")
    draw.text((512, 840), "High variance", font=body_font, fill=GRAY, anchor="mm")
    draw.text((512, 890), "波动大", font=body_font, fill=GRAY, anchor="mm")

    draw.text((512, 960), f"CON {variable['con']} · OVR {variable['ovr']}", font=subtitle_font, fill=YELLOW, anchor="mm")
    draw.text((512, 1020), "Needs supervision across runs", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 1060), "每次结果差异大，需要人工监督", font=body_font, fill=WHITE, anchor="mm")

    draw_panel(draw, 52, 1180, 920, 240, 24)
    draw.text((512, 1240), "Practitioner's Read | 实践者视角", font=subtitle_font, fill=CYAN, anchor="mm")
    winner = max(leaderboard, key=lambda r: r["ovr"])
    draw.text((512, 1310), f"Best all-round pick: {winner['model']}", font=body_font, fill=WHITE, anchor="mm")
    draw.text((512, 1360), f"最佳全能选择：{winner['model']}", font=body_font, fill=WHITE, anchor="mm")

    out_path = output_dir / "leaderboard-03-eff-con.png"
    img.save(out_path, "PNG")
    return out_path


# --------------------------------------------------------------- post copy ---

def generate_post_copy(output_dir: Path, leaderboard: list[dict[str, Any]], run: str) -> Path:
    winner = max(leaderboard, key=lambda r: r["ovr"]) if leaderboard else {"model": "TBD", "ovr": 0}
    inversion = max(leaderboard, key=lambda r: (r["obj"], -r["ovr"])) if leaderboard else {"model": "TBD", "obj": 0, "ovr": 0, "eff": 0}

    title = f"OTC Arena Run #{run}: {winner['model']} wins | 交易台竞技Run#{run} {winner['model']}夺冠"

    body = f"""---
title: {title}
---

# Red Note Post Copy

## Title (~50 chars)
{title}

## Body
🏆 Run #{run} answers the real question: which LLM can run an OTC derivatives desk unattended, day after day?

The new Model Ability Card separates capability, efficiency, and consistency — and the results flip the old leaderboard.

🔹 {winner['model']} wins overall (OVR {winner['ovr']}) — lean + steady.
🔹 {inversion['model']} ties the highest objective score ({inversion['obj']}) but ranks lower because it burns too many tool calls (EFF {inversion['eff']}).
🔹 Efficiency (EFF) and consistency (CON) are now the real separators.

👉 For unattended deployment, read EFF + CON first. Capability is no longer the separator.

---

🏆 Run #{run} 回答了一个更实际的问题：哪个大模型能真正无人值守地运营场外衍生品交易台？

全新的 Model Ability Card 把能力、效率和稳定性拆开了看，结果颠覆了传统排行榜。

🔹 {winner['model']} 综合第一（OVR {winner['ovr']}）—— 精简且稳定。
🔹 {inversion['model']} 客观分并列最高（{inversion['obj']}），却因工具调用开销过大（EFF {inversion['eff']}）排名下滑。
🔹 效率（EFF）与稳定性（CON）才是真正的分水岭。

👉 无人值守场景，优先看 EFF + CON。单纯的能力分数已经不够用了。

## Hashtags
#AI #LLM #FinTech #OTC #TradingDesk #AgentBenchmark #ZenMux #ModelAbilityCard #人工智能 #量化交易 #交易台 #大模型评测
"""

    out_path = output_dir / "post-copy.md"
    out_path.write_text(body)
    return out_path


def ensure_gitignored(output_dir: Path) -> None:
    gitignore = output_dir.parent.parent / ".gitignore"
    marker = str(output_dir.relative_to(output_dir.parent.parent)).replace("\\", "/")
    markers = {marker, f"{marker}/"}
    if not gitignore.exists():
        return
    content = gitignore.read_text()
    if any(m in content.splitlines() for m in markers):
        return
    with gitignore.open("a") as f:
        if not content.endswith("\n"):
            f.write("\n")
        f.write(f"\n# generated rednote artifacts\n{marker}/\n")


# --------------------------------------------------------------------- main ---

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate Red Note artifacts for an Arena run report")
    parser.add_argument("--source-doc", required=True, help="Path to the Arena run markdown report")
    parser.add_argument("--output-dir", default="docs/rednotes", help="Where to save generated artifacts")
    parser.add_argument("--use-cards", action="store_true", help="Use Pillow card fallback instead of HTML report screenshots")
    parser.add_argument(
        "--crop-offsets",
        type=lambda s: tuple(int(x) for x in s.split(",")),
        default=(5600, 7200, 8800),
        help="Comma-separated y-offsets for cropping three 1024x1536 report screenshots (default: 5600,7200,8800)",
    )
    parser.add_argument(
        "--reference-image",
        action="append",
        default=[],
        help="Override the bundled cover reference images (provide two paths; repeat flag)",
    )
    args = parser.parse_args(argv)

    source_path = Path(args.source_doc)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    text = source_path.read_text(encoding="utf-8")
    run = parse_run_number(text)
    leaderboard = parse_leaderboard(text)

    print(f"Run: #{run}")
    print(f"Leaderboard entries: {len(leaderboard)}")

    ref_images = [Path(p) for p in args.reference_image] if args.reference_image else None
    cover_path = generate_cover_image(run, output_dir, leaderboard, reference_images=ref_images)
    print(f"Cover: {cover_path}")

    if args.use_cards:
        img1 = generate_image_top5(output_dir, leaderboard, run)
        img2 = generate_image_inversion(output_dir, leaderboard, run)
        img3 = generate_image_eff_con(output_dir, leaderboard, run)
    else:
        html_path = render_html_report(source_path)
        print(f"HTML report: {html_path}")
        with tempfile.TemporaryDirectory() as tmpdir:
            full_shot = Path(tmpdir) / "report-full.png"
            screenshot_html(html_path, full_shot)
            print(f"Full screenshot: {full_shot} ({full_shot.stat().st_size} bytes)")
            img1, img2, img3 = crop_report_screenshot(full_shot, output_dir, args.crop_offsets)

    print(f"Leaderboard 1: {img1}")
    print(f"Leaderboard 2: {img2}")
    print(f"Leaderboard 3: {img3}")

    post_path = generate_post_copy(output_dir, leaderboard, run)
    print(f"Post copy: {post_path}")

    ensure_gitignored(output_dir)
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
