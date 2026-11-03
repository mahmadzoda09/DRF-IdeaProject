"""Общие расчёты по продажам — для статистики аптеки и личного отчёта сотрудника."""

from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from purchases.models import PurchaseItem

from .models import OrderItem


def cents(value):
    """Сумма с копейками: SQLite отдаёт Sum() как 12062.9000000000."""
    return (value or Decimal(0)).quantize(Decimal("0.01"))


def daily_sales(orders, first_day, last_day):
    """Выручка и число чеков по каждому дню с first_day по last_day включительно; дни без продаж — нули."""
    daily = {
        row["day"]: row
        for row in orders.filter(created_at__date__gte=first_day, created_at__date__lte=last_day)
        .annotate(day=TruncDate("created_at"))
        .order_by()
        .values("day")
        .annotate(total=Sum("total_price"), count=Count("id"))
    }
    result = []
    for i in range((last_day - first_day).days + 1):
        day = first_day + timedelta(days=i)
        row = daily.get(day, {})
        result.append({"date": day, "total": cents(row.get("total")), "count": row.get("count", 0)})
    return result


def sales_by_day(orders, days):
    """Выручка и число чеков по дням за последние `days` дней (включая сегодня)."""
    today = timezone.localdate()
    return daily_sales(orders, today - timedelta(days=days - 1), today)


def week_summary(orders, day):
    """
    Неделя (пн–вс), в которую попадает `day`: продажи по дням, лучший и худший день по выручке,
    итог недели и итог предыдущей. Будущие дни (future=True) в лучший/худший не идут.
    best — день с наибольшей выручкой (если продажи были); worst — с наименьшей среди прошедших, кроме best.
    При равенстве берётся более ранний день.
    """
    today = timezone.localdate()
    start = day - timedelta(days=day.weekday())
    end = start + timedelta(days=6)
    days = daily_sales(orders, start, end)
    for d in days:
        d["future"] = d["date"] > today

    past = [d for d in days if not d["future"]]
    best = max(past, key=lambda d: (d["total"], -d["date"].toordinal()), default=None)
    if best is not None and not best["total"]:
        best = None  # продаж не было — лучшего дня нет
    worst = None
    if best is not None:
        worst = min((d for d in past if d is not best), key=lambda d: (d["total"], d["date"]), default=None)

    previous = orders.filter(created_at__date__gte=start - timedelta(days=7), created_at__date__lt=start)
    return {
        "start": start,
        "end": end,
        "days": days,
        "best": best,
        "worst": worst,
        "total": cents(sum(d["total"] for d in days)),
        "count": sum(d["count"] for d in days),
        "prev_total": cents(previous.aggregate(s=Sum("total_price"))["s"]),
        "has_next": end < today,
    }


def average_costs(pharmacy):
    """Средняя закупочная цена каждого лекарства аптеки (по всем закупкам): {medicine_id: цена}."""
    return {
        r["medicine_id"]: r["cost"] / r["qty"]
        for r in PurchaseItem.objects.filter(purchase__pharmacy=pharmacy)
        .order_by()
        .values("medicine_id")
        .annotate(cost=Sum(F("price") * F("quantity")), qty=Sum("quantity"))
    }


def profit_split(orders, pharmacy):
    """Продажи = расходы (себестоимость проданного по средней закупочной цене) + прибыль."""
    costs = average_costs(pharmacy)
    items = OrderItem.objects.filter(order__in=orders).values("medicine_id", "packs")
    expenses = cents(sum((costs.get(i["medicine_id"], Decimal(0)) * i["packs"] for i in items), Decimal(0)))
    sales = cents(orders.aggregate(s=Sum("total_price"))["s"])
    return {"sales": sales, "expenses": expenses, "profit": sales - expenses}


def medicine_ranking(orders, medicines, limit=5):
    """
    Лучшие и худшие по продажам лекарства (по количеству упаковок) среди этих продаж.
    best — самые продаваемые (только проданные); worst — наименее продаваемые из всего ассортимента `medicines`,
    включая те, что не продавались вовсе (их и важнее всего заметить). Лекарство не попадает в оба списка.
    """
    sold = {
        row["medicine_id"]: row
        for row in OrderItem.objects.filter(order__in=orders)
        .order_by()
        .values("medicine_id")
        .annotate(sold=Sum("packs"), revenue=Sum(F("quantity") * F("price")))
    }
    rows = [
        {
            "medicine_id": m.id,
            "medicine__title": m.title,
            "quantity": m.quantity,
            "sold": sold.get(m.id, {}).get("sold") or 0,
            "revenue": cents(sold.get(m.id, {}).get("revenue")),
        }
        for m in medicines.only("id", "title", "quantity")
    ]
    best = sorted((r for r in rows if r["sold"]), key=lambda r: (-r["sold"], r["medicine__title"]))[:limit]
    taken = {r["medicine_id"] for r in best}
    worst = sorted((r for r in rows if r["medicine_id"] not in taken), key=lambda r: (r["sold"], r["medicine__title"]))[:limit]
    return {"best": best, "worst": worst}
