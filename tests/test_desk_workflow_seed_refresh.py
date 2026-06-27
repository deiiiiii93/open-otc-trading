"""The boot-seed refreshes an already-seeded flagship (so existing DBs adopt
the parameterized script) but never clobbers a user-authored workflow."""
from sqlalchemy import create_engine, text

from app.database import _ensure_incremental_schema
from app.models import Base

_OLD_FLAGSHIP = (
    'meta = {"name":"risk-manager-control-day","title":"Risk Manager Control Day",'
    '"persona":"risk_manager","mode":"yolo","scope":"shared"}\n'
    'await step("What does the latest risk say for the control portfolio?")\n'
)


def _engine(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/seed.db")
    Base.metadata.create_all(bind=eng)
    return eng


def test_boot_refreshes_seed_flagship(tmp_path):
    eng = _engine(tmp_path)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO desk_workflows "
                "(slug, title, persona, description, scope, default_mode, script, source, created_at, updated_at) "
                "VALUES ('risk-manager-control-day','Risk Manager Control Day','risk_manager',"
                "'','shared','yolo',:script,'seed',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"script": _OLD_FLAGSHIP},
        )
    _ensure_incremental_schema(eng)
    with eng.connect() as conn:
        script = conn.execute(
            text("SELECT script FROM desk_workflows WHERE slug = 'risk-manager-control-day'")
        ).scalar_one()
    assert '"params"' in script and "args.portfolio" in script


def test_boot_leaves_user_workflow_untouched(tmp_path):
    eng = _engine(tmp_path)
    user_script = (
        'meta = {"name":"risk-manager-control-day","title":"Mine","persona":"trader",'
        '"mode":"auto","scope":"local"}\n'
        'await step("my own thing")\n'
    )
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO desk_workflows "
                "(slug, title, persona, description, scope, default_mode, script, source, created_at, updated_at) "
                "VALUES ('risk-manager-control-day','Mine','trader','','local','auto',:script,'user',CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)"
            ),
            {"script": user_script},
        )
    _ensure_incremental_schema(eng)
    with eng.connect() as conn:
        script = conn.execute(
            text("SELECT script FROM desk_workflows WHERE slug = 'risk-manager-control-day'")
        ).scalar_one()
    assert script == user_script  # source='user' row untouched
