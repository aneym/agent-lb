from app.modules.accounts import reset_credit_cache


def setup_function() -> None:
    reset_credit_cache.reset()


def teardown_function() -> None:
    reset_credit_cache.reset()


def test_record_get_clear_and_reset() -> None:
    assert reset_credit_cache.get_count("account-1") is None

    reset_credit_cache.record_count("account-1", 2)
    reset_credit_cache.record_count("account-2", 0)

    assert reset_credit_cache.get_count("account-1") == 2
    assert reset_credit_cache.get_count("account-2") == 0

    reset_credit_cache.clear("account-1")
    assert reset_credit_cache.get_count("account-1") is None

    reset_credit_cache.reset()
    assert reset_credit_cache.get_count("account-2") is None
