from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Case, Count, F, Sum, When
from django.db.models.functions import TruncMonth
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status, viewsets
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import action
from rest_framework.response import Response

from accounts.mixins import PharmacyScopedMixin, ProtectedDeleteMixin, parse_day
from accounts.permissions import HasRolePermission
from accounts.rbac import Perm, has_permission
from accounts.serializers import UserSerializer
from orders.models import Order, OrderItem
from orders.reports import average_costs, medicine_ranking, sales_by_day, week_summary
from purchases.models import Purchase, PurchaseItem

from .expiry import SOON_DAYS, batches_on_hand, expiry_info, status_of
from .models import IN_STOCK, Medicine, Pharmacy, Supplier
from .serializers import (
    MedicineSerializer,
    PharmacySerializer,
    PharmacyStatsSerializer,
    SupplierSerializer,
    WeekSerializer,
    ExpiryReportSerializer,
)


@extend_schema(tags=["pharmacies"])
class PharmacyViewSet(ProtectedDeleteMixin, viewsets.ModelViewSet):
    queryset = Pharmacy.objects.annotate(staff_count=Count("users"))
    serializer_class = PharmacySerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "list": Perm.PHARMACY_VIEW,
        "retrieve": Perm.PHARMACY_VIEW,
        "stats": Perm.PHARMACY_STATS,
        "week": Perm.PHARMACY_STATS,
        "users": Perm.USER_MANAGE,
        "*": Perm.PHARMACY_MANAGE,
    }
    search_fields = ["name", "address"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset.none()

        user = self.request.user
        if has_permission(user, Perm.ALL_PHARMACIES):
            return queryset
        return queryset.filter(id=user.pharmacy_id)

    @extend_schema(
        methods=["GET"],
        summary="Сотрудники аптеки",
        parameters=[OpenApiParameter("role", OpenApiTypes.STR, enum=["director", "pharmacist"])],
        responses=UserSerializer(many=True),
    )
    @extend_schema(methods=["POST"], summary="Создать сотрудника в аптеке", responses={201: UserSerializer})
    @action(detail=True, methods=["get", "post"], serializer_class=UserSerializer)
    def users(self, request, pk=None):
        """
        GET  — сотрудники аптеки.
        POST — создать сотрудника (директора или фармацевта) в этой аптеке.
        """
        pharmacy = self.get_object()

        if request.method == "GET":
            queryset = pharmacy.users.all()
            role = request.query_params.get("role")
            if role:
                queryset = queryset.filter(role=role)
            page = self.paginate_queryset(queryset)
            serializer = UserSerializer(page, many=True, context=self.get_serializer_context())
            return self.get_paginated_response(serializer.data)

        data = request.data.copy()
        data["pharmacy"] = pharmacy.id
        data.setdefault("role", "pharmacist")
        serializer = UserSerializer(data=data, context=self.get_serializer_context())
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    SALES_DAYS = 14
    MONTHS = 6

    @extend_schema(summary="Статистика аптеки", responses=PharmacyStatsSerializer)
    @action(detail=True, methods=["get"])
    def stats(self, request, pk=None):
        """Статистика аптеки: продажи (в т.ч. по дням и топ лекарств), мало на складе, скоро истекает срок."""
        pharmacy = self.get_object()
        today = timezone.localdate()
        orders = Order.objects.filter(pharmacy=pharmacy, status=Order.Status.COMPLETED)

        # по месяцам: продажи, расходы = себестоимость проданного (средняя закупочная цена × кол-во),
        # прибыль = продажи − расходы; отдельно — сколько потрачено на закупки
        months = [today.replace(day=1)]
        for _ in range(self.MONTHS - 1):
            months.insert(0, (months[0] - timedelta(days=1)).replace(day=1))

        def sum_by_month(queryset):
            rows = (
                queryset.filter(created_at__date__gte=months[0])
                .annotate(month=TruncMonth("created_at"))
                .order_by()
                .values("month")
                .annotate(total=Sum("total_price"))
            )
            return {timezone.localtime(r["month"]).date(): r["total"] for r in rows}

        avg_cost = average_costs(pharmacy)
        cost_of_sold = defaultdict(Decimal)
        for item in OrderItem.objects.filter(
            order__in=orders, order__created_at__date__gte=months[0]
        ).values("order__created_at", "medicine_id", "packs"):
            month = timezone.localtime(item["order__created_at"]).date().replace(day=1)
            cost_of_sold[month] += avg_cost.get(item["medicine_id"], Decimal(0)) * item["packs"]

        sales = sum_by_month(orders)
        purchases = sum_by_month(Purchase.objects.filter(pharmacy=pharmacy))
        monthly = []
        for month in months:
            month_sales = sales.get(month) or Decimal(0)
            month_expenses = cost_of_sold[month].quantize(Decimal("0.01"))
            monthly.append(
                {
                    "month": month,
                    "sales": month_sales,
                    "expenses": month_expenses,
                    "profit": month_sales - month_expenses,
                    "purchases": purchases.get(month) or Decimal(0),
                }
            )

        data = {
            "pharmacy": pharmacy.name,
            "orders_count": orders.count(),
            "total_sales": orders.aggregate(s=Sum("total_price"))["s"] or 0,
            "today_sales": orders.filter(created_at__date=today).aggregate(s=Sum("total_price"))["s"]
            or 0,
            "low_stock": list(
                Medicine.objects.filter(pharmacy=pharmacy, quantity__lt=10).values(
                    "id", "title", "quantity"
                )
            ),
            "expiring_soon": list(
                PurchaseItem.objects.filter(
                    purchase__pharmacy=pharmacy,
                    expire_date__gte=today,
                    expire_date__lte=today + timedelta(days=30),
                ).values("medicine_id", "medicine__title", "quantity", "expire_date")
            ),
            "sales_by_day": sales_by_day(orders, self.SALES_DAYS),
            "week": week_summary(orders, today),
            "monthly": monthly,
            "medicine_ranking": medicine_ranking(orders, Medicine.objects.filter(pharmacy=pharmacy)),
        }
        return Response(data)

    @extend_schema(
        summary="Неделя: продажи по дням, лучший и худший день",
        parameters=[OpenApiParameter("date", OpenApiTypes.DATE, description="любой день недели; по умолчанию сегодня")],
        responses=WeekSerializer,
    )
    @action(detail=True, methods=["get"])
    def week(self, request, pk=None):
        pharmacy = self.get_object()
        orders = Order.objects.filter(pharmacy=pharmacy, status=Order.Status.COMPLETED)
        return Response(week_summary(orders, parse_day(request.query_params.get("date"))))


@extend_schema(tags=["suppliers"])
class SupplierViewSet(PharmacyScopedMixin, ProtectedDeleteMixin, viewsets.ModelViewSet):
    """Читать: админ и сотрудники. Создавать/менять/удалять: директор."""

    queryset = Supplier.objects.all()
    serializer_class = SupplierSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "list": Perm.SUPPLIER_VIEW,
        "retrieve": Perm.SUPPLIER_VIEW,
        "*": Perm.SUPPLIER_MANAGE,
    }
    search_fields = ["name", "phone_number"]


@extend_schema(tags=["medicines"])
class MedicineViewSet(PharmacyScopedMixin, ProtectedDeleteMixin, viewsets.ModelViewSet):
    """Читать: админ и сотрудники. Создавать/менять/удалять: директор."""

    queryset = Medicine.objects.all()
    serializer_class = MedicineSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "list": Perm.MEDICINE_VIEW,
        "retrieve": Perm.MEDICINE_VIEW,
        "expiry": Perm.MEDICINE_VIEW,
        "*": Perm.MEDICINE_MANAGE,
    }
    search_fields = ["title", "barcode"]
    ordering_fields = ["title", "price", "quantity", "created_at"]

    @extend_schema(
        parameters=[
            OpenApiParameter("barcode", OpenApiTypes.STR, description="точное совпадение штрих-кода (сканер)"),
            OpenApiParameter("in_stock", OpenApiTypes.BOOL, description="только лекарства с остатком на складе"),
            OpenApiParameter(
                "expiry",
                OpenApiTypes.STR,
                enum=["soon", "expired", "attention"],
                description="soon — срок истекает за 30 дней, expired — истёк, attention — то и другое",
            ),
            OpenApiParameter("ordering", OpenApiTypes.STR, description="title, price, quantity, created_at (с «-» — по убыванию) или expiry — ближайший срок первым"),
        ]
    )
    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        items = page if page is not None else list(queryset)
        # сроки считаем одним запросом на всю страницу
        serializer = self.get_serializer(items, many=True, context={**self.get_serializer_context(), "expiry": expiry_info(items)})
        return self.get_paginated_response(serializer.data) if page is not None else Response(serializer.data)

    def get_queryset(self):
        queryset = super().get_queryset()
        if self.request.query_params.get("in_stock"):
            queryset = queryset.filter(IN_STOCK)
        barcode = self.request.query_params.get("barcode")
        if barcode:
            queryset = queryset.filter(barcode=barcode.strip())
        expiry = self.request.query_params.get("expiry")
        if expiry:
            wanted = {"soon": {"soon"}, "expired": {"expired"}, "attention": {"soon", "expired"}}.get(expiry)
            if wanted is None:
                raise ValidationError({"expiry": "Допустимо: soon, expired, attention."})
            # срок считается в Python (FIFO по партиям) — отбираем id, пагинация остаётся в базе
            stocked = list(queryset.filter(IN_STOCK).only("id", "quantity", "open_blisters"))
            info = expiry_info(stocked)
            queryset = queryset.filter(id__in=[mid for mid, i in info.items() if i["expire_status"] in wanted])
        if self.request.query_params.get("ordering") == "expiry":
            # ближайший срок — первым; без срока (нет остатка или закупок) — в конце
            info = expiry_info(list(queryset.only("id", "quantity", "open_blisters")))
            dated = sorted((i["expire_date"], mid) for mid, i in info.items() if i["expire_date"])
            position = Case(*[When(id=mid, then=n) for n, (_, mid) in enumerate(dated)], default=len(dated))
            queryset = queryset.order_by(position, "title")
        return queryset

    @extend_schema(
        summary="Сроки годности: партии на складе",
        description=(
            "Каждая партия, из которой состоит текущий остаток (FIFO: продаётся сначала старое), с её сроком. "
            "Сначала ближайший срок. summary — сколько партий и упаковок с истёкшим сроком, истекающих за 30 и за 90 дней."
        ),
        responses=ExpiryReportSerializer,
    )
    @action(detail=False, methods=["get"])
    def expiry(self, request):
        today = timezone.localdate()
        medicines = list(self.get_queryset().filter(IN_STOCK).select_related("pharmacy"))
        by_id = {m.id: m for m in medicines}
        batches = []
        for medicine_id, on_hand in batches_on_hand(medicines).items():
            m = by_id[medicine_id]
            for b in on_hand:
                batches.append(
                    {
                        "medicine_id": m.id,
                        "title": m.title,
                        "barcode": m.barcode,
                        "pharmacy": m.pharmacy_id,
                        "price": m.price,
                        "quantity": b["quantity"],
                        "expire_date": b["expire_date"],
                        "days_left": (b["expire_date"] - today).days,
                        "status": status_of(b["expire_date"], today),
                    }
                )
        batches.sort(key=lambda b: (b["expire_date"], b["title"]))

        def count(test):
            chosen = [b for b in batches if test(b)]
            return {"batches": len(chosen), "quantity": sum(b["quantity"] for b in chosen)}

        summary = {
            "expired": count(lambda b: b["days_left"] < 0),
            "soon": count(lambda b: 0 <= b["days_left"] <= SOON_DAYS),
            "quarter": count(lambda b: SOON_DAYS < b["days_left"] <= 90),
            "total": count(lambda b: True),
        }
        return Response({"today": today, "summary": summary, "batches": batches})
