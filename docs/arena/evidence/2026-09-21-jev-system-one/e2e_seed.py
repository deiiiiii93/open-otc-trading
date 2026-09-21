from pathlib import Path
from app import database
from app.config import get_settings
from app.golden_workflows.fixtures import load_fixtures, apply_seed
s = get_settings(); database.configure_database(s); database.init_db()
REPO = Path(__file__).resolve().parents[4]
bundle = load_fixtures(REPO / "backend/app/golden_workflows/definitions/ops-settlement-day.fixtures.json")
with database.SessionLocal() as session:
    ids = apply_seed(bundle, session); session.commit()
print("seeded", {k: len(v) for k, v in ids.items()} if isinstance(ids, dict) else ids)
