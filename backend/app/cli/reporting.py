"""Reporting CLI commands (Typer).

Four commands mirror the agent tools: ``list``, ``show``, ``create``,
``batch-run``. Each calls ``services.domains.reporting`` and shapes output
through ``tools._shaping`` so the CLI and LLM see the same wire format.
"""
from __future__ import annotations

import typer

from app.services.domains import reporting as reporting_svc
from app.tools._shaping import shape_report_full, shape_report_row

from ._format import emit

app = typer.Typer(no_args_is_help=True)


@app.command("list")
def list_cmd(
    portfolio_id: int = typer.Option(None, "--portfolio-id"),
    report_type: str = typer.Option(None, "--report-type"),
    status: str = typer.Option(None, "--status"),
    limit: int = typer.Option(20, "--limit", min=1, max=100),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """List ReportJob rows newest-first with optional filters."""
    rows = reporting_svc.list_reports(
        portfolio_id=portfolio_id,
        report_type=report_type,  # type: ignore[arg-type]
        status=status,  # type: ignore[arg-type]
        limit=limit,
    )
    payload = {
        "reports": [shape_report_row(job) for job in rows],
        "total": len(rows),
    }
    emit(payload, as_json=json_output)


@app.command("show")
def show_cmd(
    report_id: int = typer.Option(..., "--report-id"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Show full ReportJob payload for one id."""
    job = reporting_svc.get_report(report_id=report_id)
    if job is None:
        raise typer.BadParameter(f"Report job not found: report_id={report_id}")
    emit(shape_report_full(job), as_json=json_output)


@app.command("create")
def create_cmd(
    portfolio_id: int = typer.Option(..., "--portfolio-id"),
    report_type: str = typer.Option("portfolio", "--report-type"),
    title: str = typer.Option("CLI Generated Desk Report", "--title"),
    pricing_profile_id: int = typer.Option(None, "--pricing-profile-id"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Queue a persisted report job for a portfolio."""
    if report_type not in ("portfolio", "risk", "rfq"):
        raise typer.BadParameter("--report-type must be portfolio, risk, or rfq")
    payload = reporting_svc.create_report(
        portfolio_id=portfolio_id,
        report_type=report_type,  # type: ignore[arg-type]
        title=title,
        pricing_profile_id=pricing_profile_id,
    )
    emit(payload, as_json=json_output)


@app.command("batch-run")
def batch_run_cmd(
    title: str = typer.Option("Desk Risk Report", "--title"),
    report_type: str = typer.Option("portfolio", "--report-type"),
    json_output: bool = typer.Option(True, "--json/--no-json"),
) -> None:
    """Print an ad-hoc batch envelope (no DB writes, no risk computation).

    Useful for previewing the batch shape expected by the agent. Risk
    computation is intentionally skipped — the CLI batch path is for
    quick inspection, not pricing runs.
    """
    payload = reporting_svc.run_batch(
        title=title,
        report_type=report_type,
        risk_summary={},
    )
    emit(payload, as_json=json_output)
