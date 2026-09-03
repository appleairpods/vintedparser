"""Тексты экранов. Отдельно от логики, чтобы правки формулировок были дешёвыми."""

from datetime import datetime

from app.config import settings
from app.domain.plans import FREE, PRO, Quota
from app.infrastructure.db.models import Filter

WELCOME = """👋 <b>Привет! Я слежу за новыми объявлениями на Vinted.</b>

Ты описываешь, что ищешь, — я присылаю подходящие лоты через несколько секунд
после публикации. На хороших вещах это решает: кто первый написал, тот и купил.

Кнопки внизу всегда под рукой. Начни с «Новый фильтр», а если объявлений станет
слишком много — нажми «Пауза», и я замолчу до следующего нажатия."""

MENU = """☰ <b>Меню</b>

Выбери раздел. Основные действия продублированы кнопками внизу экрана."""

HELP = f"""❓ <b>Как это работает</b>

Я каждые несколько секунд читаю ленту новых объявлений Vinted и сравниваю их
с твоими фильтрами. Совпало — сразу присылаю карточку со ссылкой.

<b>Фильтр</b> — это набор условий: страны, слова из названия, бренд, размер и
цена. Чем точнее фильтр, тем меньше лишнего.

<b>Страны</b> выбираются кнопками с флагами. У каждой своя лента и своя валюта,
поэтому цену в фильтре я понимаю в деньгах выбранных стран, без пересчёта.

<b>Тарифы</b>
• {FREE.title}: {FREE.daily_notifications} объявлений в сутки, {FREE.max_filters} фильтр
• {PRO.title}: без ограничений, до {PRO.max_filters} фильтров

Лимит обновляется каждый день в полночь по UTC.

<b>Слишком много сообщений?</b>
Нажми «Пауза» внизу экрана или прямо на карточке объявления — я замолчу, пока
не включишь обратно. Фильтры при этом сохраняются.

Если предпочитаешь команды, работают и они: /add, /list, /del, /pause."""


def _plan_line(quota: Quota, subscription_until: datetime | None) -> str:
    if not quota.is_pro:
        return f"Тариф: <b>{FREE.title}</b>"

    until = subscription_until.strftime("%d.%m.%Y") if subscription_until else "—"
    return f"Тариф: <b>{PRO.title}</b> до {until}"


def paused_notice(dropped: int) -> str:
    tail = f"\n\nИз очереди убрал {dropped} — они уже не придут." if dropped else ""
    return (
        "🔕 <b>Уведомления на паузе</b>\n\n"
        "Фильтры сохранены, я просто молчу. Пропущенные за это время объявления "
        "не накапливаются: приходить будут только те, что появятся после "
        f"включения.{tail}"
    )


def resumed_notice() -> str:
    return (
        "🔔 <b>Уведомления включены</b>\n\n"
        "Снова слежу за лентой и пришлю всё, что подойдёт под твои фильтры."
    )


def profile(
    quota: Quota,
    subscription_until: datetime | None,
    filters_count: int,
    *,
    paused: bool = False,
) -> str:
    plan = quota.plan
    if plan.is_unlimited:
        usage = f"Сегодня получено: <b>{quota.used_today}</b> (без ограничений)"
    else:
        usage = (
            f"Сегодня получено: <b>{quota.used_today} из "
            f"{plan.daily_notifications}</b>"
        )

    lines = [
        "👤 <b>Личный кабинет</b>",
        "",
        _plan_line(quota, subscription_until),
        usage,
        f"Фильтров: <b>{filters_count} из {plan.max_filters}</b>",
        f"Уведомления: <b>{'🔕 на паузе' if paused else '🔔 включены'}</b>",
    ]

    if not quota.is_pro:
        lines += [
            "",
            "💎 <b>Pro</b> снимает суточный лимит и открывает "
            f"до {PRO.max_filters} фильтров.",
        ]

    return "\n".join(lines)


def subscription() -> str:
    prices = "\n".join(
        f"• {offer.title} — <b>{offer.stars} ⭐</b>" for offer in settings.offers
    )
    return f"""💎 <b>Подписка Pro</b>

Что даёт:
• Уведомления без суточного лимита
• До {PRO.max_filters} фильтров вместо {FREE.max_filters}
• Приоритет в поддержке

<b>Стоимость</b>
{prices}

Оплата — звёздами Telegram, прямо в приложении.
Если продлеваешь досрочно, дни складываются с остатком."""


def limit_reached() -> str:
    return f"""⏳ <b>Дневной лимит исчерпан</b>

Ты получил все {FREE.daily_notifications} бесплатных объявлений за сегодня.
Остальные подходящие лоты сегодня не приду́т — а они появляются каждую минуту.

Лимит обновится в полночь по UTC. Либо сними его совсем."""


def renewal_reminder(until: datetime, days_left: int) -> str:
    when = "завтра" if days_left <= 1 else f"через {days_left} дн."
    return (
        f"⏰ <b>Подписка заканчивается {when}</b>\n\n"
        f"Pro действует до <b>{until.strftime('%d.%m.%Y')}</b>. "
        f"Потом вернётся бесплатный тариф: {FREE.daily_notifications} объявлений "
        f"в сутки и {FREE.max_filters} фильтр.\n\n"
        "Продлишь сейчас — дни добавятся к текущему сроку, ничего не сгорит."
    )


def subscription_expired() -> str:
    return (
        "😔 <b>Подписка закончилась</b>\n\n"
        f"Ты снова на бесплатном тарифе: {FREE.daily_notifications} объявлений "
        f"в сутки. Фильтры сохранились, но лишние сверх {FREE.max_filters} "
        "перестали работать.\n\n"
        "Вернуть безлимит можно в одно нажатие."
    )


def payment_success(until: datetime, days: int) -> str:
    return f"""✅ <b>Оплата прошла. Pro активирован.</b>

Подписка действует до <b>{until.strftime("%d.%m.%Y")}</b> (+{days} дней).
Суточный лимит снят, можно завести до {PRO.max_filters} фильтров.

Спасибо, что поддерживаешь проект."""


def filters_list(filters: list[Filter], describe) -> str:
    if not filters:
        return (
            "🔍 <b>Фильтров пока нет</b>\n\n"
            "Добавь первый — и я начну присылать объявления."
        )

    lines = ["🔍 <b>Твои фильтры</b>", ""]
    for index, flt in enumerate(filters, start=1):
        state = "" if flt.is_active else " <i>(выключен)</i>"
        lines.append(f"<b>{index}.</b> {describe(flt)}{state}")
    return "\n".join(lines)
