import pytest

from app.services.reporting.contracts import BlockContext, BlockResult, BlockShape
from app.services.reporting.registry import (
    UnknownBlockError,
    get_block,
    list_blocks,
    report_block,
    require_block,
    resolve_block,
)


@pytest.fixture(autouse=True)
def _isolated_registry(monkeypatch):
    """Each test gets a clean registry so fixtures cannot leak between tests."""
    from app.services.reporting import registry

    monkeypatch.setattr(registry, "_REGISTRY", {})
    yield


def test_decorator_registers_a_block_with_its_declared_shape():
    @report_block(key="test.scalar", title="Test scalar", shape=BlockShape.SCALARS,
                  domain="test", description="A test block.")
    def _produce(ctx):
        return BlockResult.ok(data={"value": 1.0})

    spec = get_block("test.scalar")
    assert spec is not None
    assert spec.key == "test.scalar"
    assert spec.shape is BlockShape.SCALARS
    assert spec.domain == "test"
    assert spec.description == "A test block."


def test_resolve_invokes_the_producer():
    @report_block(key="test.scalar", title="T", shape=BlockShape.SCALARS)
    def _produce(ctx):
        return BlockResult.ok(data={"portfolio_id": ctx.portfolio_id})

    result = resolve_block("test.scalar", BlockContext(portfolio_id=7))
    assert result.status == "ok"
    assert result.data == {"portfolio_id": 7}


def test_unknown_key_resolves_to_unavailable_rather_than_raising():
    result = resolve_block("test.nope", BlockContext(portfolio_id=1))
    assert result.status == "unavailable"
    assert "test.nope" in result.reason


def test_missing_required_param_resolves_to_unavailable_naming_the_param():
    @report_block(key="test.cmp", title="T", shape=BlockShape.SCALARS_WITH_PRIOR,
                  requires=("compare_to_run_id",))
    def _produce(ctx):
        return BlockResult.ok(data={})

    result = resolve_block("test.cmp", BlockContext(portfolio_id=1))
    assert result.status == "unavailable"
    assert "compare_to_run_id" in result.reason


def test_required_param_present_lets_the_producer_run():
    @report_block(key="test.cmp", title="T", shape=BlockShape.SCALARS_WITH_PRIOR,
                  requires=("compare_to_run_id",))
    def _produce(ctx):
        return BlockResult.ok(data={"compared": ctx.compare_to_run_id})

    result = resolve_block("test.cmp", BlockContext(portfolio_id=1, compare_to_run_id=35))
    assert result.status == "ok"
    assert result.data == {"compared": 35}


def test_producer_exception_becomes_unavailable_not_a_500():
    @report_block(key="test.boom", title="T", shape=BlockShape.SCALARS)
    def _produce(ctx):
        raise RuntimeError("upstream exploded")

    result = resolve_block("test.boom", BlockContext(portfolio_id=1))
    assert result.status == "unavailable"
    assert "upstream exploded" in result.reason


def test_duplicate_key_registration_is_rejected():
    @report_block(key="test.dupe", title="T", shape=BlockShape.SCALARS)
    def _first(ctx):
        return BlockResult.ok(data={})

    with pytest.raises(ValueError, match="test.dupe"):
        @report_block(key="test.dupe", title="T2", shape=BlockShape.SCALARS)
        def _second(ctx):
            return BlockResult.ok(data={})


def test_list_blocks_is_sorted_by_key():
    @report_block(key="test.b", title="B", shape=BlockShape.ROWS)
    def _b(ctx):
        return BlockResult.ok(data={})

    @report_block(key="test.a", title="A", shape=BlockShape.ROWS)
    def _a(ctx):
        return BlockResult.ok(data={})

    assert [spec.key for spec in list_blocks()] == ["test.a", "test.b"]


def test_require_block_raises_for_the_template_validator():
    with pytest.raises(UnknownBlockError, match="test.nope"):
        require_block("test.nope")
