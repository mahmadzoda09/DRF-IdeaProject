"""
Данные отчётов о продажах и закупках в общем виде: заголовок, шапка и секции (итоги и таблицы).
Один и тот же отчёт показывается на странице и выгружается в Excel / Word / PDF (см. export.py).
Видимость — по тем же правам, что и в списках: аптека пользователя (админ — все или выбранная),
фармацевт видит только свои продажи; себестоимость и прибыль — только с правом статистики аптеки.

Типы колонок: text, int, money, pct, datetime.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

from django.db.models import Count, F, Sum
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from accounts.mixins import _parse_month, filter_by_months
from accounts.models import User
from accounts.rbac import Perm, has_permission
from orders.models import Order, OrderItem
from orders.reports import cents
from pharmacies.models import Pharmacy
from purchases.models import Purchase, PurchaseItem

from .labels import Labels

ZERO = Decimal(0)


def packs(value):
    """Упаковки: целое — как есть (5), доли от блистеров — до 2 знаков (1.67)."""
    value = Decimal(value)
    return int(value) if value == value.to_integral_value() else float(round(value, 2))


def _col(label, type_="text"):
    return {"label": label, "type": type_}


def _month_of(moment):
    return timezone.localtime(moment).date().replace(day=1)


def _pharmacy(user, params):
    """Аптека отчёта: своя; админ — выбранная (?pharmacy=) или None = все аптеки."""
    if not has_permission(user, Perm.ALL_PHARMACIES):
        return user.pharmacy
    pharmacy_id = params.get("pharmacy")
    if not pharmacy_id:
        return None
    try:
        return Pharmacy.objects.get(pk=int(pharmacy_id))
    except (ValueError, Pharmacy.DoesNotExist):
        raise ValidationError({"pharmacy": "Аптека не найдена."})


def _scoped(queryset, pharmacy, params):
    if pharmacy is not None:
        queryset = queryset.filter(pharmacy=pharmacy)
    return filter_by_months(queryset, params)


def _period(params, L):
    start = params.get("month_from") or params.get("month")
    end = params.get("month_to") or params.get("month")
    if not start and not end:
        return L("allTime")
    start = L.month(_parse_month("month_from", start)) if start else "…"
    end = L.month(_parse_month("month_to", end)) if end else "…"
    return start if start == end else f"{start} – {end}"


def _person(user):
    if user is None:
        return "—"
    return user.full_name or user.get_full_name() or user.username


def _header(kind, pharmacy, params, L, extra=()):
    return {
        "kind": kind,
        "title": L(f"{kind}.title"),
        "meta": [
            [L("pharmacy"), pharmacy.name if pharmacy else L("allPharmacies")],
            [L("period"), _period(params, L)],
            *extra,
            [L("generated"), timezone.localtime().strftime("%d.%m.%Y %H:%M")],
        ],
        "sections": [],
        "signature": _signature(pharmacy, params, L),
    }


def _sign_date(params):
    """Дата подписи — последний день последнего месяца отчёта (октябрь -> 31.10.2026); без периода — пусто."""
    end = params.get("month_to") or params.get("month")
    if not end:
        return ""
    first = _parse_month("month_to", end)
    next_month = date(first.year + first.month // 12, first.month % 12 + 1, 1)
    return f"{next_month - timedelta(days=1):%d.%m.%Y}"


def _signature(pharmacy, params, L):
    """Место для подписи директора аптеки в PDF: директор подтверждает цифры отчёта."""
    director = pharmacy.users.filter(role=User.Role.DIRECTOR).order_by("id").first() if pharmacy else None
    return {
        "confirm": L("sign.confirm"),
        "role": L("sign.director"),
        "name": _person(director) if director else "",
        "signature": L("sign.signature"),
        "full_name": L("sign.name"),
        "date": L("sign.date"),
        "date_value": _sign_date(params),
        "stamp": L("sign.stamp"),
    }


def _average_costs(medicine_ids):
    """Средняя закупочная цена каждого лекарства по всем его закупкам."""
    return {
        r["medicine_id"]: r["cost"] / r["qty"]
        for r in PurchaseItem.objects.filter(medicine_id__in=medicine_ids)
        .order_by()
        .values("medicine_id")
        .annotate(cost=Sum(F("price") * F("quantity")), qty=Sum("quantity"))
    }


def sales_report(user, params, language):
    L = Labels(language)
    pharmacy = _pharmacy(user, params)
    orders = _scoped(Order.objects.all(), pharmacy, params)
    only_own = not has_permission(user, Perm.ORDER_VIEW_ALL)
    if only_own:
        orders = orders.filter(user=user)
    finance = has_permission(user, Perm.PHARMACY_STATS)

    completed = orders.filter(status=Order.Status.COMPLETED).select_related("user").order_by("created_at")
    cancelled = orders.filter(status=Order.Status.CANCELLED).count()
    items = list(
        OrderItem.objects.filter(order__in=completed).values(
            "medicine_id", "medicine__title", "quantity", "packs", "price", "order__created_at"
        )
    )
    costs = _average_costs({i["medicine_id"] for i in items}) if finance else {}
    item_counts = dict(
        OrderItem.objects.filter(order__in=completed).order_by().values_list("order_id").annotate(n=Count("id"))
    )

    # по месяцам: выручка — по суммам чеков (со скидкой), себестоимость — по проданным позициям
    month_sales = defaultdict(lambda: [0, ZERO, ZERO])  # чеков, выручка, себестоимость
    sellers = defaultdict(lambda: [0, ZERO])
    order_rows = []
    for o in completed:
        month = month_sales[_month_of(o.created_at)]
        month[0] += 1
        month[1] += o.total_price
        seller = sellers[_person(o.user)]
        seller[0] += 1
        seller[1] += o.total_price
        order_rows.append(
            [
                o.order_number,
                timezone.localtime(o.created_at).isoformat(),
                _person(o.user),
                item_counts.get(o.id, 0),
                o.discount,
                o.total_price,
            ]
        )

    medicines = defaultdict(lambda: [0, ZERO, ZERO])  # кол-во, сумма, себестоимость
    for i in items:
        cost = costs.get(i["medicine_id"], ZERO) * i["packs"]
        month_sales[_month_of(i["order__created_at"])][2] += cost
        row = medicines[i["medicine__title"]]
        row[0] += i["packs"]  # блистеры — долями упаковки
        row[1] += i["price"] * i["quantity"]
        row[2] += cost

    revenue = cents(sum((m[1] for m in month_sales.values()), ZERO))
    cost_total = cents(sum((m[2] for m in month_sales.values()), ZERO))
    checks = len(order_rows)
    units = packs(sum((m[0] for m in medicines.values()), ZERO))

    report = _header("sales", pharmacy, params, L, [[L("seller"), _person(user)]] if only_own else [])
    summary = [
        [L("sum.checks"), checks, "int"],
        [L("sum.units"), units, "int"],
        [L("sum.sales"), revenue, "money"],
        [L("sum.avg"), cents(revenue / checks) if checks else ZERO, "money"],
    ]
    if finance:
        summary += [
            [L("sum.cost"), cost_total, "money"],
            [L("sum.profit"), revenue - cost_total, "money"],
            [L("sum.margin"), round((revenue - cost_total) / revenue * 100, 1) if revenue else ZERO, "pct"],
        ]
    summary.append([L("sum.cancelled"), cancelled, "int"])
    report["sections"].append({"id": "summary", "title": L("summary"), "type": "summary", "rows": summary})

    money_cols = [_col(L("sales"), "money")]
    if finance:
        money_cols += [_col(L("cost"), "money"), _col(L("profit"), "money")]

    def money_cells(sales, cost):
        sales, cost = cents(sales), cents(cost)
        return [sales, cost, sales - cost] if finance else [sales]

    report["sections"].append(
        {
            "id": "months",
            "title": L("byMonth"),
            "type": "table",
            "columns": [_col(L("month")), _col(L("checks"), "int"), *money_cols],
            "rows": [[L.month(m), v[0], *money_cells(v[1], v[2])] for m, v in sorted(month_sales.items())],
            "total": [L("total"), checks, *money_cells(revenue, cost_total)],
        }
    )
    report["sections"].append(
        {
            "id": "medicines",
            "title": L("byMedicine"),
            "type": "table",
            "columns": [_col(L("medicine")), _col(L("qty"), "int"), *money_cols],
            "rows": [
                [title, packs(v[0]), *money_cells(v[1], v[2])]
                for title, v in sorted(medicines.items(), key=lambda kv: (-kv[1][1], kv[0]))
            ],
            "total": [L("total"), units, *money_cells(sum((v[1] for v in medicines.values()), ZERO), cost_total)],
        }
    )
    if not only_own:
        report["sections"].append(
            {
                "id": "sellers",
                "title": L("bySeller"),
                "type": "table",
                "columns": [_col(L("seller")), _col(L("checks"), "int"), _col(L("sales"), "money")],
                "rows": [[name, v[0], cents(v[1])] for name, v in sorted(sellers.items(), key=lambda kv: -kv[1][1])],
                "total": [L("total"), checks, revenue],
            }
        )
    report["sections"].append(
        {
            "id": "list",
            "title": L("orders"),
            "type": "table",
            "columns": [
                _col(L("number")),
                _col(L("date"), "datetime"),
                _col(L("seller")),
                _col(L("items"), "int"),
                _col(L("discount"), "pct"),
                _col(L("amount"), "money"),
            ],
            "rows": order_rows,
            "total": [L("total"), "", "", sum(r[3] for r in order_rows), "", revenue],
        }
    )
    return report


def purchases_report(user, params, language):
    L = Labels(language)
    pharmacy = _pharmacy(user, params)
    purchases = list(
        _scoped(Purchase.objects.all(), pharmacy, params).select_related("supplier", "user").order_by("created_at")
    )
    items = PurchaseItem.objects.filter(purchase__in=[p.id for p in purchases]).values(
        "purchase_id", "medicine__title", "quantity", "price"
    )

    months = defaultdict(lambda: [0, ZERO])
    suppliers = defaultdict(lambda: [0, ZERO])
    for p in purchases:
        for bucket in (months[_month_of(p.created_at)], suppliers[p.supplier.name]):
            bucket[0] += 1
            bucket[1] += p.total_price

    medicines = defaultdict(lambda: [0, ZERO])
    item_counts = defaultdict(int)
    for i in items:
        medicines[i["medicine__title"]][0] += i["quantity"]
        medicines[i["medicine__title"]][1] += i["price"] * i["quantity"]
        item_counts[i["purchase_id"]] += 1

    spent = cents(sum((p.total_price for p in purchases), ZERO))
    units = sum(v[0] for v in medicines.values())

    report = _header("purchases", pharmacy, params, L)
    report["sections"].append(
        {
            "id": "summary",
            "title": L("summary"),
            "type": "summary",
            "rows": [
                [L("sum.purchases"), len(purchases), "int"],
                [L("sum.units.in"), units, "int"],
                [L("sum.spent"), spent, "money"],
                [L("sum.suppliers"), len(suppliers), "int"],
            ],
        }
    )
    report["sections"].append(
        {
            "id": "months",
            "title": L("byMonth"),
            "type": "table",
            "columns": [_col(L("month")), _col(L("count"), "int"), _col(L("amount"), "money")],
            "rows": [[L.month(m), v[0], cents(v[1])] for m, v in sorted(months.items())],
            "total": [L("total"), len(purchases), spent],
        }
    )
    report["sections"].append(
        {
            "id": "suppliers",
            "title": L("bySupplier"),
            "type": "table",
            "columns": [_col(L("supplier")), _col(L("count"), "int"), _col(L("amount"), "money")],
            "rows": [[name, v[0], cents(v[1])] for name, v in sorted(suppliers.items(), key=lambda kv: -kv[1][1])],
            "total": [L("total"), len(purchases), spent],
        }
    )
    report["sections"].append(
        {
            "id": "medicines",
            "title": L("byMedicine"),
            "type": "table",
            "columns": [
                _col(L("medicine")),
                _col(L("qty"), "int"),
                _col(L("amount"), "money"),
                _col(L("avgPrice"), "money"),
            ],
            "rows": [
                [title, v[0], cents(v[1]), cents(v[1] / v[0])]
                for title, v in sorted(medicines.items(), key=lambda kv: (-kv[1][1], kv[0]))
            ],
            "total": [L("total"), units, cents(sum((v[1] for v in medicines.values()), ZERO)), ""],
        }
    )
    report["sections"].append(
        {
            "id": "list",
            "title": L("purchases"),
            "type": "table",
            "columns": [
                _col(L("number")),
                _col(L("date"), "datetime"),
                _col(L("supplier")),
                _col(L("employee")),
                _col(L("items"), "int"),
                _col(L("amount"), "money"),
            ],
            "rows": [
                [
                    p.purchase_number,
                    timezone.localtime(p.created_at).isoformat(),
                    p.supplier.name,
                    _person(p.user),
                    item_counts[p.id],
                    p.total_price,
                ]
                for p in purchases
            ],
            "total": [L("total"), "", "", "", sum(item_counts.values()), spent],
        }
    )
    return report


def file_name(report, params):
    """sales_months_report_2026-08_2026-10_20261031 (без расширения; секция — если отчёт по одной части)."""
    start = params.get("month_from") or params.get("month") or ""
    end = params.get("month_to") or params.get("month") or ""
    period = start if start == end else "_".join(filter(None, [start, end]))
    return "_".join(filter(None, [report["kind"], report.get("section"), "report", period, f"{date.today():%Y%m%d}"]))
