# Open OTC Trading

> AI-powered OTC derivatives research, pricing, and RFQ workflow platform.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![React 19](https://img.shields.io/badge/React-19-61DAFB.svg)](https://react.dev/)

---

## Overview

Open OTC Trading is a single-desk web platform for structured derivatives workflows — from client RFQ intake through pricing, risk management, and reporting. It combines a deterministic quant engine ([QuantArk](https://github.com/deiiiiii93/quant-ark)) with LLM-powered agents for research and workflow automation.

### Key Features

- **Pricing Engine** — Multi-engine Greeks computation (analytical, Monte Carlo, PDE) via QuantArk
- **RFQ Workflow** — Client portal for quote requests with internal approval pipeline
- **AI Agents** — LangGraph-based agents with tool-calling for research, pricing, and report generation
- **Risk Dashboard** — Portfolio-level Greeks, scenario analysis, and real-time position monitoring
- **Market Data** — AKShare adapter with caching and fallback for A-share / HK markets
- **Streaming Chat** — Token-by-token agent responses with structured asset cards and charts

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
pip install -e ".[dev]"

# Run tests
python -m pytest

# Start dev server (port 8000)
uvicorn app.main:app --app-dir backend --reload --reload-dir backend --reload-dir config --port 8000
```

> **Note:** If developing against a local QuantArk checkout, install it first:
> `pip install -e /path/to/quant-ark`

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
```

| Variable | Description | Required |
|----------|-------------|----------|
| `OPEN_OTC_DATABASE_URL` | SQLite connection string | Yes (has default) |
| `QUANTARK_PATH` | Path to QuantArk library | Yes |
| `ZENMUX_API_KEY` | ZenMux unified LLM gateway key | No |
| `DEEPSEEK_API_KEY` | DeepSeek API key | No |
| `LANGSMITH_API_KEY` | LangSmith observability | No |
| `OPEN_OTC_TRACING` | Tracing mode: `local` \| `langsmith` \| `both` \| `off` | No |

The platform works without LLM API keys — agents fall back to deterministic persona responses and QuantArk-backed tool outputs.

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
python -m pytest

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
