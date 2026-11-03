"""
Снимок данных для ассистента — только то, что пользователь и так видит в системе по своим правам (RBAC).
Отдаётся модели текстом в системном сообщении; модель ничего не меняет в базе.
"""

from datetime import timedelta

from django.db.models import Sum
from django.utils import timezone

from accounts.rbac import Perm, has_permission
from orders.models import Order
from orders.reports import cents, medicine_ranking, profit_split, week_summary
from pharmacies.models import Medicine, Pharmacy
from purchases.models import PurchaseItem

MAX_MEDICINES = 300
EXPIRY_DAYS = 60
LOW_STOCK = 20


def _sum(orders):
    return cents(orders.aggregate(s=Sum("total_price"))["s"])


def _sales_block(title, orders, today, pharmacy=None):
    month = orders.filter(created_at__date__gte=today.replace(day=1))
    week = week_summary(orders, today)
    lines = [
        f"## {title}",
        f"Сегодня (день ещё не закончился): {_sum(orders.filter(created_at__date=today))} сомони, "
        f"чеков {orders.filter(created_at__date=today).count()}",
        f"Эта неделя ({week['start']} – {week['end']}): {week['total']} сомони, чеков {week['count']}; "
        f"прошлая неделя: {week['prev_total']} сомони",
        "По дням недели: " + ", ".join(f"{d['date']}: {d['total']}" for d in week["days"] if not d["future"]),
    ]
    if week["best"]:
        lines.append(f"Лучший день недели: {week['best']['date']} ({week['best']['total']})")
    if week["worst"]:
        lines.append(f"Худший день недели: {week['worst']['date']} ({week['worst']['total']})")
    if pharmacy is not None:
        split = profit_split(month, pharmacy)
        lines.append(
            f"Этот месяц: продажи {split['sales']}, себестоимость проданного {split['expenses']}, "
            f"прибыль {split['profit']} сомони, чеков {month.count()}"
        )
    else:
        lines.append(f"Этот месяц: продажи {_sum(month)} сомони, чеков {month.count()}")
    lines.append(f"Всего за всё время: {_sum(orders)} сомони, чеков {orders.count()}")
    return lines


def build_context(user):
    today = timezone.localdate()
    all_pharmacies = has_permission(user, Perm.ALL_PHARMACIES)
    pharmacies = Pharmacy.objects.all() if all_pharmacies else Pharmacy.objects.filter(id=user.pharmacy_id)
    completed = Order.objects.filter(status=Order.Status.COMPLETED)

    lines = [
        f"Сегодня: {today} ({today.strftime('%A')}). Валюта: сомони (TJS).",
        f"Пользователь: {user.full_name or user.username}, роль: {user.get_role_display()}, "
        f"аптека: {user.pharmacy.name if user.pharmacy_id else 'все аптеки сети (администратор)'}",
    ]

    if has_permission(user, Perm.MEDICINE_VIEW):
        medicines = Medicine.objects.filter(pharmacy__in=pharmacies).select_related("pharmacy").order_by("pharmacy__name", "title")
        total = medicines.count()
        lines.append(f"\n## Лекарства на складе ({total}{', показаны первые ' + str(MAX_MEDICINES) if total > MAX_MEDICINES else ''})")
        lines.append(("аптека | " if all_pharmacies else "") + "название | цена | остаток | штрих-код")
        for m in medicines[:MAX_MEDICINES]:
            lines.append(
                (f"{m.pharmacy.name} | " if all_pharmacies else "")
                + f"{m.title} | {m.price} | {m.quantity} | {m.barcode or '-'}"
            )

        low = medicines.filter(quantity__lt=LOW_STOCK).order_by("quantity")
        lines.append(f"\n## Заканчиваются (остаток меньше {LOW_STOCK} шт.)")
        lines += [
            (f"{m.pharmacy.name} | " if all_pharmacies else "") + f"{m.title}: {m.quantity} шт." for m in low
        ] or ["нет"]

        expiring = (
            PurchaseItem.objects.filter(
                purchase__pharmacy__in=pharmacies,
                expire_date__lte=today + timedelta(days=EXPIRY_DAYS),
            )
            .select_related("medicine")
            .order_by("expire_date")[:50]
        )
        if expiring:
            lines.append(f"\n## Партии со сроком годности до {today + timedelta(days=EXPIRY_DAYS)} (в т.ч. уже истёкшие)")
            lines += [f"{p.medicine.title}: {p.quantity} шт., годен до {p.expire_date}" for p in expiring]

    if has_permission(user, Perm.PHARMACY_STATS):
        for pharmacy in pharmacies:
            orders = completed.filter(pharmacy=pharmacy)
            lines.append("")
            lines += _sales_block(f"Продажи аптеки «{pharmacy.name}»", orders, today, pharmacy)
            ranking = medicine_ranking(orders, Medicine.objects.filter(pharmacy=pharmacy))
            lines.append("Самые продаваемые: " + (", ".join(f"{r['medicine__title']} ({r['sold']} шт.)" for r in ranking["best"]) or "нет"))
            lines.append("Хуже всего продаются: " + ", ".join(f"{r['medicine__title']} ({r['sold']} шт.)" for r in ranking["worst"]))

    if user.pharmacy_id:
        lines.append("")
        lines += _sales_block("Мои личные продажи", completed.filter(user=user), today)

    return "\n".join(lines)
