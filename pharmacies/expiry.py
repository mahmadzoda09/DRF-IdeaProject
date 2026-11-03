"""
Сроки годности лекарств на складе.

Срок хранится у партии (строки закупки), а не у лекарства, и остаток по партиям не ведётся.
Поэтому считаем по FIFO: продаётся сначала старый товар, значит текущий остаток — это последние
закупленные упаковки. Идём по партиям от новой к старой, пока не наберём остаток; срок лекарства —
ближайший среди этих партий.
"""

from collections import defaultdict
from datetime import timedelta

from django.utils import timezone

from purchases.models import PurchaseItem

SOON_DAYS = 30


def batches_on_hand(medicines):
    """
    {medicine_id: [{"item_id", "expire_date", "quantity", "cost"}, ...]} — партии, из которых состоит текущий остаток
    (по сроку). Списанное из партии (written_off) на складе уже не лежит.
    """
    stock = {m.id: m.stock_packs for m in medicines}
    items = defaultdict(list)
    for item in (
        PurchaseItem.objects.filter(medicine_id__in=[mid for mid, q in stock.items() if q > 0])
        .order_by("medicine_id", "-purchase__created_at", "-id")
        .values("id", "medicine_id", "expire_date", "quantity", "written_off", "price")
    ):
        items[item["medicine_id"]].append(item)

    result = {}
    for medicine_id, rows in items.items():
        left = stock[medicine_id]
        on_hand = []
        for row in rows:  # от новой партии к старой
            if left <= 0:
                break
            taken = min(left, row["quantity"] - row["written_off"])
            if taken <= 0:
                continue
            on_hand.append({"item_id": row["id"], "expire_date": row["expire_date"], "quantity": taken, "cost": row["price"]})
            left -= taken
        result[medicine_id] = sorted(on_hand, key=lambda b: b["expire_date"])
    return result


def status_of(expire_date, today=None):
    """expired — срок прошёл; soon — истекает в ближайшие 30 дней; ok — позже; None — срок неизвестен."""
    if expire_date is None:
        return None
    today = today or timezone.localdate()
    if expire_date < today:
        return "expired"
    if expire_date <= today + timedelta(days=SOON_DAYS):
        return "soon"
    return "ok"


def expiry_info(medicines):
    """{medicine_id: {"expire_date", "expire_status", "batches"}} для сериализатора и фильтра."""
    today = timezone.localdate()
    batches = batches_on_hand(medicines)
    info = {}
    for m in medicines:
        on_hand = batches.get(m.id, [])
        nearest = on_hand[0]["expire_date"] if on_hand else None
        info[m.id] = {"expire_date": nearest, "expire_status": status_of(nearest, today), "batches": on_hand}
    return info
