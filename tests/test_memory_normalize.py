from app.services.deep_agent.memory.normalize import normalize_content


def test_nfkc_casefold_whitespace():
    assert normalize_content("  Books   IN\tUSD\n") == "books in usd"
    assert normalize_content("ＵＳＤ") == "usd"


def test_punctuation_preserved():
    assert normalize_content("ACT/365, daily.") == "act/365, daily."


def test_empty_results():
    assert normalize_content("   \n\t ") == ""
    assert normalize_content("") == ""
