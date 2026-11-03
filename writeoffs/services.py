from collections import defaultdict
from decimal import Decimal

from django.db import transaction
from django.db.models import F
from django.utils import timezone

from pharmacies.expiry import batches_on_hand
from pharmacies.models import IN_STOCK, Medicine
from purchases.models import PurchaseItem

from .models import WriteOff, WriteOffItem


def expired_batches(medicines, month=None):
    """Просроченные партии на складе (срок < сегодня); month — date 1-го числа: только партии с этим месяцем срока."""
    today = timezone.localdate()
    medicines = [m for m in medicines if m.stock_packs > 0]
    by_id = {m.id: m for m in medicines}
    batches = []
    for medicine_id, on_hand in batches_on_hand(medicines).items():
        m = by_id[medicine_id]
        for b in on_hand:
            expire = b["expire_date"]
            if expire >= today or (month and (expire.year, expire.month) != (month.year, month.month)):
                continue
            batches.append({**b, "medicine": m})
    batches.sort(key=lambda b: (b["expire_date"], b["medicine"].title))
    return batches


def pending_by_month(medicines):
    """Просроченное по месяцам истечения срока — то, что можно списать."""
    today = timezone.localdate()
    months = defaultdict(list)
    for b in expired_batches(medicines):
        months[b["expire_date"].strftime("%Y-%m")].append(b)

    def totals(batches):
        return {
            "batches": len(batches),
            "quantity": sum(b["quantity"] for b in batches),
            "cost": sum((b["cost"] * b["quantity"] for b in batches), Decimal("0")),
            "price": sum((b["medicine"].price * b["quantity"] for b in batches), Decimal("0")),
        }

    rows = [
        {
            "month": month,
            **totals(batches),
            "items": [
                {
                    "medicine_id": b["medicine"].id,
                    "title": b["medicine"].title,
                    "barcode": b["medicine"].barcode,
                    "pharmacy": b["medicine"].pharmacy_id,
                    "quantity": b["quantity"],
                    "cost": b["cost"],
                    "price": b["medicine"].price,
                    "expire_date": b["expire_date"],
                    "days_overdue": (today - b["expire_date"]).days,
                }
                for b in batches
            ],
        }
        for month, batches in sorted(months.items())
    ]
    every = [b for batches in months.values() for b in batches]
    return {"today": today, "total": totals(every), "months": rows}


def write_off_expired(pharmacy, user, month=None):
    """Списывает просроченные партии аптеки (за месяц срока или все). None — списывать нечего."""
    with transaction.atomic():
        # блокируем лекарства: продажа не должна поменять остаток между расчётом партий и списанием
        medicines = list(Medicine.objects.select_for_update().filter(IN_STOCK, pharmacy=pharmacy))
        batches = expired_batches(medicines, month)
        if not batches:
            return None
        write_off = WriteOff.objects.create(pharmacy=pharmacy, user=user, expire_month=month)
        total_cost = total_price = Decimal("0")
        for b in batches:
            m, qty = b["medicine"], b["quantity"]
            WriteOffItem.objects.create(
                write_off=write_off,
                medicine=m,
                purchase_item_id=b["item_id"],
                quantity=qty,
                cost=b["cost"],
                price=m.price,
                expire_date=b["expire_date"],
            )
            PurchaseItem.objects.filter(pk=b["item_id"]).update(written_off=F("written_off") + qty)
            # по FIFO вскрытая упаковка — самая старая: если списание до неё дошло, её блистеры тоже уходят
            whole = min(qty, m.quantity)
            m.quantity -= whole
            if qty > whole:
                m.open_blisters = 0
            m.save(update_fields=["quantity", "open_blisters"])
            total_cost += b["cost"] * qty
            total_price += m.price * qty
        write_off.total_cost = total_cost
        write_off.total_price = total_price
        write_off.save(update_fields=["total_cost", "total_price"])
    return write_off
