# Open OTC Trading

> **One assistant. Your whole desk.** — structured products, priced in real time.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)

![Open OTC agent desk](docs/screenshots/agent-desk.png)

---

## Overview

Open OTC Trading is an AI-native trading desk for structured equity derivatives. Talk to it the way you'd brief a junior trader — *"Quote a 12-month CSI 300 snowball, KO 103, KI 75, 8% coupon"* — and it pulls live spot, builds the product, and prices it with full Greeks. You approve; it books. From there the same assistant aggregates portfolio risk, solves and sizes hedges, runs stress tests and backtests, and writes the reports.

It pairs a **deterministic quant engine** ([QuantArk](https://github.com/deiiiiii93/quant-ark)) — so every price, Greek, and scenario is reproducible and audit-traced — with **LLM-powered agents** that handle the research and workflow around it. The numbers never come from a language model; the conversation does.

### The desk, end to end

The product walkthrough tells it as one continuous flow:

1. **Ask** — A trader describes a structured product (snowball, phoenix, autocall, sharkfin, Asian) in plain language, or a client submits an RFQ through the portal.
2. **Price** — The agent fetches market data, assembles the term sheet, and returns PV plus full Greeks (Δ, Γ, Vega, Theta) from QuantArk.
3. **Book** — Nothing is committed silently. The agent surfaces a **human-in-the-loop** confirmation card (`Approve` / `Reject`) before any position or hedge hits the book.
4. **Risk** — One pass aggregates portfolio Greeks — delta cash, gamma, vega — broken down by underlying.
5. **Hedge** — A solver proposes Δ-neutral legs (e.g. index futures), sizes them to a residual delta, and books them on approval.
6. **Operate** — The same assistant drives stress tests, batch pricing jobs, hedging backtests, and report generation across the whole position book.

### A look at the desk

| Build · Price · Book | Portfolio Risk |
|:---:|:---:|
| [![Booking and pricing with Greeks](docs/screenshots/booking.png)](docs/screenshots/booking.png) | [![Aggregated portfolio Greeks](docs/screenshots/risk.png)](docs/screenshots/risk.png) |
| Price a structured product with full Greeks, then confirm before it books. | Aggregated Δ/Γ/Vega/Theta in one pass, sliced by underlying. |

| Hedging | Scenario / Stress Tests |
|:---:|:---:|
| [![Delta-neutral hedge solver](docs/screenshots/hedging.png)](docs/screenshots/hedging.png) | [![Scenario stress testing](docs/screenshots/scenario.png)](docs/screenshots/scenario.png) |
| A solver proposes and sizes Δ-neutral hedge legs. | Shock spot, vol, and rates across the book. |

| Backtesting | Position Book |
|:---:|:---:|
| [![Hedging backtest](docs/screenshots/backtest.png)](docs/screenshots/backtest.png) | [![Positions overview](docs/screenshots/positions.png)](docs/screenshots/positions.png) |
| Replay hedging strategies over history. | Every booked position, live-valued. |

### Key Features

- **Conversational desk** — Brief an LLM agent in natural language; it calls deterministic tools for pricing, risk, hedging, and booking. Responses stream token-by-token with structured asset cards and charts.
- **Pricing engine** — Multi-engine Greeks (analytical, Monte Carlo, PDE) via QuantArk, across snowball, phoenix, autocall, sharkfin, Asian, digital, barrier, and vanilla families.
- **Human-in-the-loop booking** — Positions and hedges require explicit approval; the agent proposes, you commit.
- **Portfolio risk** — Aggregated Greeks, scenario analysis, and position monitoring in a single run, sliced by underlying.
- **Hedging** — A MILP solver that proposes and sizes Δ-neutral hedge strategies, with lifecycle backtesting.
- **RFQ workflow** — Client portal for quote requests with an internal approval pipeline.
- **Market data** — AKShare adapter with caching and fallback for A-share / HK markets.
- **Reproducible & audited** — Every pricing run, risk run, and agent trace is persisted; QuantArk keeps the math deterministic.

---

## Architecture

```
┌─────────────────────────────────────────────────────┐
│  Frontend (React 19 / Vite / TypeScript)            │
│  Radix UI · Recharts · "Warm Ledger" design system  │
└────────────────────────┬────────────────────────────┘
                         │ REST + SSE
┌────────────────────────▼────────────────────────────┐
│  Backend (FastAPI / Uvicorn)                         │
│  LangGraph agents · SQLAlchemy · Alembic migrations  │
└───┬────────────────┬───────────────────┬────────────┘
    │                │                   │
    ▼                ▼                   ▼
 QuantArk        SQLite DB         LLM Providers
 (pricing)     (positions,        (ZenMux, DeepSeek)
               traces, RFQs)
```

---

## Quick Start

### Prerequisites

- Python 3.11+
- Node.js 18+
- (Optional) [QuantArk](https://github.com/deiiiiii93/quant-ark) local checkout for development

### Backend

```bash
git clone https://github.com/deiiiiii93/open-otc-trading.git
cd open-otc-trading

python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
cp .env.example .env
cp config/agent_channels.example.yml config/agent_channels.yaml
mkdir -p data artifacts
.venv/bin/python -m alembic upgrade head

# Run tests
.venv/bin/python -m pytest

# Start dev server (port 8000)
uvicorn app.main:app --app-dir backend --reload --reload-dir backend --reload-dir config --port 8000
```

> **Note:** If developing against a local QuantArk checkout, install it first:
> `python -m pip install -e /path/to/quant-ark`

### Frontend

```bash
cd frontend
npm install
npm run dev
```

Open http://localhost:5173

### CLI

```bash
open-otc --help
```

---

## Configuration

```bash
cp .env.example .env
cp config/agent_channels.example.yml config/agent_channels.yaml
mkdir -p data artifacts
.venv/bin/python -m alembic upgrade head
```

| Variable | Description | Required |
|----------|-------------|----------|
| `OPEN_OTC_DATABASE_URL` | SQLite connection string | Yes (has default) |
| `ZENMUX_API_KEY` | ZenMux unified LLM gateway key | No |
| `DEEPSEEK_API_KEY` | DeepSeek API key | No |
| `LANGSMITH_API_KEY` | LangSmith observability | No |
| `OPEN_OTC_TRACING` | Tracing mode: `local` \| `langsmith` \| `both` \| `off` | No |

The platform works without LLM API keys — agents fall back to deterministic persona responses and QuantArk-backed tool outputs. The local `config/agent_channels.yaml` file is gitignored; keep provider keys in `.env` and adjust channel/model entries there when needed.

By default the app uses SQLite at `data/open_otc.sqlite3` via `OPEN_OTC_DATABASE_URL`. Run `.venv/bin/python -m alembic upgrade head` after changing the database URL or pulling schema migrations. Fresh app startup also creates missing local tables, but Alembic is the explicit setup and upgrade path for development databases.

See `.env.example` for the full variable list and `config/agent_channels.example.yml` for LLM model/channel configuration.

---

## Project Structure

```
open-otc-trading/
├── backend/
│   └── app/
│       ├── main.py          # FastAPI application
│       ├── routers/         # API endpoints
│       ├── services/        # Business logic
│       ├── skills/          # Agent skill definitions
│       ├── tools/           # LangGraph tool implementations
│       └── models.py        # SQLAlchemy models
├── frontend/
│   └── src/
│       ├── components/      # Reusable UI components
│       ├── routes/          # Page-level route components
│       ├── tokens/          # Design tokens (colors, typography)
│       ├── api/             # Backend API client
│       └── hooks/           # Custom React hooks
├── config/                  # Agent channel configuration
├── tests/                   # Backend test suite
└── docs/                    # Design specs & plans
```

---

## Development

### Running Tests

```bash
# Backend
.venv/bin/python -m pytest

# Frontend
cd frontend && npm test
```

## Tech Stack

**Backend:** FastAPI, SQLAlchemy, Alembic, LangGraph, LangChain, QuantArk, AKShare, Pandas

**Frontend:** React 19, TypeScript, Vite, Radix UI, Recharts, Lucide Icons

**AI/LLM:** LangGraph agents, ZenMux (Anthropic/OpenAI gateway), DeepSeek, LangSmith tracing

---

## License

[MIT](LICENSE)
