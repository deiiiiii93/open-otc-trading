---
name: rednotes-release
description: >-
  Generate Red Note (Xiaohongshu / 小红书) social media artifacts for an OTC Desk
  Agent Arena run report. Produces a Claude-style editorial cover image via
  ZenMux gpt-image-2 with style-reference assets, three exact screenshots of the
  rendered HTML report, and bilingual EN|CN post title/body. Saves everything to
  docs/rednotes/ and ensures the directory is gitignored. Trigger on "rednote",
  "red note", "xiaohongshu", "小红书", "social media post", or when the user wants
  to turn an arena run report into shareable images.
---

# rednotes-release

Generate Red Note (Xiaohongshu / 小红书) social media artifacts for an OTC Desk
Agent Arena run report.

## What it produces

1. **Cover image** — Claude-style editorial cover via `openai/gpt-image-2`
   through ZenMux, using two bundled style references (editorial layout +
   light-blue palette). Saved as `cover-run<N>.png`.
2. **Three report screenshots** — exact crops of the rendered HTML report:
   - `leaderboard-01-board.png` — section 5.1 OVR leaderboard chart
   - `leaderboard-02-full-card.png` — full card table + §5.3/§5.4 insights
   - `leaderboard-03-insights.png` — §5.5 consistency axis + §6/§7 takeaways
3. **Post copy** — bilingual EN|CN title (~50 chars) and body with hashtags in
   `post-copy.md`.

All outputs land in `docs/rednotes/` and the directory is added to `.gitignore`.

## Cover prompt pattern

The skill builds a Chinese prompt in this shape (data is parsed from the
markdown):

```
根据报告内容，设计一张小红书封面。

报告：The OTC Desk Agent Arena — Methodology & Results (Run #N) ...

主标题：N大模型无人交易台真实对决
副标题：评估N个主流大语言模型在无人工干预下独立运营真实OTC衍生品交易台的能力

OVR 综合得分排行榜（至少展示前5名及Grok）：
1. <Winner> — <OVR>（全场最佳）
2. <Second> — <OVR>
3. <Third> — <OVR>
3. <Tied third> — <OVR>
5. <Fifth> — <OVR>
10. Grok 4.5 — <OVR>（客观分...并列第一，但工具调用...次，效率极低）

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
- 整体干净、高级、专业，适合作为小红书笔记首图
```

Three local reference images in `assets/` steer the layout, editorial style,
and palette:
- `assets/ref-layout.png`
- `assets/ref-editorial.png`
- `assets/ref-palette.jpg`

## Prerequisites

- `ZENMUX_API_KEY` exported in the environment.
- Google Chrome installed at `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`
  (used for headless HTML screenshots).
- `docs/arena/render_report.py` present (it renders the markdown to HTML and PDF).
- Python packages: `Pillow`, `openai>=1.0`. The skill uses the project virtual
  environment by default (`.venv/bin/python`).

## Usage

Run from the project root:

```bash
.venv/bin/python .agents/skills/rednotes-release/scripts/rednotes_release.py \
  --source-doc docs/arena/2026-07-13-run20-otc-desk-agent-arena.md \
  --output-dir docs/rednotes
```

For a different run:

```bash
.venv/bin/python .agents/skills/rednotes-release/scripts/rednotes_release.py \
  --source-doc docs/arena/<run-report>.md \
  --output-dir docs/rednotes
```

The script:

1. Parses the source markdown for run number and leaderboard data.
2. Renders the markdown to HTML with `docs/arena/render_report.py`.
3. Captures a full-page screenshot with Chrome headless.
4. Crops three 1024×1536 cards from the screenshot.
5. Generates the cover image through ZenMux using the reference assets.
6. Writes `post-copy.md` with bilingual title and body.
7. Adds `docs/rednotes/` to `.gitignore` if missing.

## Adjusting crop offsets

If a future report has a different layout, pass custom y-offsets:

```bash
.venv/bin/python .agents/skills/rednotes-release/scripts/rednotes_release.py \
  --source-doc docs/arena/<run-report>.md \
  --crop-offsets 5000,6500,8000
```

Each offset is the top y-coordinate of one 1024×1536 crop, taken from the
1200px-wide full-page screenshot.

## Custom reference images

Override the bundled style references with `--reference-image`:

```bash
.venv/bin/python .agents/skills/rednotes-release/scripts/rednotes_release.py \
  --source-doc docs/arena/<run-report>.md \
  --reference-image /path/to/editorial.png \
  --reference-image /path/to/palette.jpg
```

## Card fallback

If Chrome or the HTML renderer is unavailable, use the Pillow-based card
fallback:

```bash
.venv/bin/python .agents/skills/rednotes-release/scripts/rednotes_release.py \
  --source-doc docs/arena/<run-report>.md \
  --use-cards
```

## Output layout

```
docs/rednotes/
  cover-<run>.png
  leaderboard-01-board.png
  leaderboard-02-full-card.png
  leaderboard-03-insights.png
  post-copy.md
```

## Workflow notes

- The cover image is generative; re-running will produce a different image.
- Report screenshots are deterministic for a given HTML render.
- If `docs/rednotes/` already contains files, new files overwrite matching names.
- Review `post-copy.md` before posting; tweak title/body to match the account tone.
