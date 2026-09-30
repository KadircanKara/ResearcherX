from datetime import datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.services.usage_limits import day_start, is_over, next_reset


def test_zero_means_no_limit():
    assert is_over(10_000, 0) is False


def test_limit_is_reached_at_n_not_after():
    assert is_over(19, 20) is False
    assert is_over(20, 20) is True


def test_day_window_is_utc_midnight_even_for_other_offsets():
    now = datetime(2026, 9, 30, 1, 30, tzinfo=timezone(timedelta(hours=3)))  # 22:30Z on 29th
    assert day_start(now) == datetime(2026, 9, 29, tzinfo=timezone.utc)
    assert next_reset(now) == datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize(
    "field",
    [
        "user_max_papers",
        "user_chat_turns_per_day",
        "user_llm_assists_per_day",
        "global_chat_turns_per_day",
    ],
)
def test_a_negative_limit_is_refused_not_treated_as_on(field):
    with pytest.raises(ValidationError):
        Settings(**{field: -1})
