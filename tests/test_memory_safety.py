from app.services.deep_agent.memory.safety import is_memorable


def test_positive_pass_through():
    ok, reason = is_memorable("books all trades in USD")
    assert ok is True and reason is None
    ok, _ = is_memorable("prefers net-delta hedging by underlying")
    assert ok is True


def test_secret_pattern_blocked():
    ok, reason = is_memorable("api_key: sk-ABCDEF0123456789ABCD")
    assert ok is False and reason


def test_price_and_position_blocked():
    assert is_memorable("sold at 1200.50 USD")[0] is False
    assert is_memorable("holds 5000 shares of the name")[0] is False


def test_empty_denylist_passes():
    assert is_memorable("api_key: sk-whatever", denylist=())[0] is True
