from app.domain.plans import FREE, PRO, Quota, plan_for, remaining_notifications


def test_free_plan_has_daily_limit():
    assert remaining_notifications(FREE, used_today=0) == FREE.daily_notifications
    assert remaining_notifications(FREE, used_today=4) == FREE.daily_notifications - 4


def test_limit_never_goes_negative():
    assert remaining_notifications(FREE, used_today=999) == 0


def test_pro_plan_is_unlimited():
    assert PRO.is_unlimited
    assert remaining_notifications(PRO, used_today=10_000) is None


def test_plan_depends_on_subscription():
    assert plan_for(is_pro=False) is FREE
    assert plan_for(is_pro=True) is PRO


def test_quota_spends_until_limit():
    quota = Quota(is_pro=False)

    taken = sum(quota.take() for _ in range(FREE.daily_notifications + 5))

    assert taken == FREE.daily_notifications
    assert quota.spent == FREE.daily_notifications
    assert quota.is_exhausted


def test_quota_accounts_for_earlier_usage():
    quota = Quota(is_pro=False, used_today=FREE.daily_notifications - 1)

    assert quota.take() is True
    assert quota.take() is False
    # Списываем только то, что потратили в этом цикле: суточный итог
    # уже лежит в базе.
    assert quota.spent == 1


def test_pro_quota_never_exhausts():
    quota = Quota(is_pro=True, used_today=100_000)

    assert quota.remaining is None
    assert quota.is_exhausted is False
    assert quota.take() is True
