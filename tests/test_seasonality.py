from datetime import date

from award_hunter.seasonality import AVOID, CAUTION, GOOD, easter, month_summary, rating_for, thanksgiving


def test_moving_holidays():
    assert easter(2026) == date(2026, 4, 5) and easter(2027) == date(2027, 3, 28)
    assert thanksgiving(2026) == date(2026, 11, 26) and thanksgiving(2027) == date(2027, 11, 25)


def test_ratings():
    assert rating_for(date(2026, 12, 24))[0] == AVOID
    assert rating_for(date(2027, 1, 2))[0] == AVOID
    assert rating_for(date(2027, 1, 20))[0] == GOOD
    assert rating_for(date(2027, 7, 15))[0] == AVOID
    assert rating_for(date(2027, 10, 5))[0] == GOOD
    assert rating_for(date(2026, 11, 25))[0] == CAUTION      # Thanksgiving week
    assert rating_for(date(2027, 3, 26))[0] == CAUTION       # Easter 2027 window
    assert rating_for(date(2026, 12, 5))[0] == GOOD


def test_month_summary():
    assert month_summary(2027, 7)[0] == AVOID and month_summary(2027, 2)[0] == GOOD
