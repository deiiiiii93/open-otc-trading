"""open-otc CLI entry point.

This package replaces the legacy ``backend/app/cli.py`` module. ``main(argv)``
keeps the same external signature (returns an int exit code) so the
``open-otc`` console script wired in ``pyproject.toml`` still works.

Subcommand routing:

* ``portfolios <op>`` is handled by the Typer app in ``app/cli/portfolios.py``.
* ``positions <op>`` (except ``positions price``) is handled by the Typer app
  in ``app/cli/positions.py``.
* The remaining subcommands (``agent``, ``positions price``) still use the
  legacy argparse implementation, kept verbatim in ``app/cli/_legacy.py``.
  ``positions price`` will migrate to Typer in the pricing PR.
"""
from __future__ import annotations

import sys

import typer

from . import market_data as market_data_cmd
from . import portfolios as portfolios_cmd
from . import positions as positions_cmd
from . import pricing as pricing_cmd
from . import reporting as reporting_cmd
from . import rfq as rfq_cmd
from . import risk as risk_cmd

app = typer.Typer(no_args_is_help=True, name="otc")
app.add_typer(portfolios_cmd.app, name="portfolios", help="Portfolio operations")
app.add_typer(positions_cmd.app, name="positions", help="Position operations")
app.add_typer(market_data_cmd.app, name="market-data", help="Market data operations")
app.add_typer(pricing_cmd.app, name="pricing", help="Pricing operations")
app.add_typer(risk_cmd.app, name="risk", help="Risk operations")
app.add_typer(rfq_cmd.app, name="rfq", help="RFQ operations")
app.add_typer(reporting_cmd.app, name="reporting", help="Reporting operations")


def _route_to_typer(argv: list[str]) -> int:
    try:
        result = app(args=argv, prog_name="otc", standalone_mode=False)
    except typer.Exit as exc:
        return int(exc.exit_code or 0)
    except SystemExit as exc:
        code = exc.code
        return int(code) if code is not None else 0
    # With standalone_mode=False Click returns the exit code rather than
    # re-raising. Treat ints as the exit code; treat None as success.
    if isinstance(result, int):
        return result
    return 0


def main(argv: list[str] | None = None) -> int:
    """Compatibility entry point; mirrors the old app/cli.py signature.

    When invoked via the ``open-otc`` console script wrapper, ``argv`` is
    ``None``; fall back to ``sys.argv[1:]`` so the wrapper-provided args
    reach the dispatcher.
    """
    if argv is None:
        argv = list(sys.argv[1:])
    else:
        argv = list(argv)
    if argv and argv[0] == "portfolios":
        return _route_to_typer(argv)
    if argv and argv[0] == "positions" and (len(argv) < 2 or argv[1] != "price"):
        return _route_to_typer(argv)
    if argv and argv[0] == "market-data":
        return _route_to_typer(argv)
    if argv and argv[0] == "pricing":
        return _route_to_typer(argv)
    if argv and argv[0] == "risk":
        return _route_to_typer(argv)
    if argv and argv[0] == "rfq":
        return _route_to_typer(argv)
    if argv and argv[0] == "reporting":
        return _route_to_typer(argv)
    from . import _legacy

    return _legacy.run(argv)


if __name__ == "__main__":
    raise SystemExit(main())
