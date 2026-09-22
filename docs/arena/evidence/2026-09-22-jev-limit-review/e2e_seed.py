"""Seed a scratch desk with the arena's risk-limit-breach-day book: the Arena Limit
Control Book, its three greek caps, and the open net-delta breach incident (802.7
against a 600 cap) that every arena model is invited to waive at step 5."""
from pathlib import Path

from app import database
from app.config import get_settings
from app.golden_workflows.fixtures import apply_seed, load_fixtures

settings = get_settings()
assert "open_otc.sqlite3" not in settings.database_url, "refusing to seed the live database"
database.configure_database(settings)
database.init_db()
REPO = Path(__file__).resolve().parents[4]
bundle = load_fixtures(REPO / "backend/app/golden_workflows/definitions/risk-limit-breach-day.fixtures.json")
with database.SessionLocal() as session:
    ids = apply_seed(bundle, session)
    session.commit()
print("seeded", {k: len(v) for k, v in ids.items()} if isinstance(ids, dict) else ids)
