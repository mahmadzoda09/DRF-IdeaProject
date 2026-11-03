from django.db.models import Count, Sum
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, viewsets
from rest_framework.parsers import MultiPartParser
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.views import TokenObtainPairView

from orders.models import Order, OrderItem
from orders.reports import cents, medicine_ranking, profit_split, sales_by_day, week_summary
from pharmacies.models import Medicine
from pharmacies.serializers import WeekSerializer
from purchases.models import Purchase

from .avatars import delete_avatar, save_avatar
from .mixins import filter_by_months, parse_day
from .models import User
from .permissions import HasRolePermission
from .rbac import GRANTABLE, REQUIRES, ROLE_PERMISSIONS, Perm, get_permissions, has_permission, set_access
from .serializers import (
    AccessSerializer,
    AccessUpdateSerializer,
    AvatarUploadSerializer,
    LoginResponseSerializer,
    LoginSerializer,
    MyReportSerializer,
    UserSerializer,
)


@extend_schema_view(
    post=extend_schema(
        tags=["auth"],
        summary="Логин",
        auth=[],
        responses={200: LoginResponseSerializer},
    )
)
class LoginView(TokenObtainPairView):
    """POST username + password -> access, refresh, user"""

    serializer_class = LoginSerializer


@extend_schema_view(get=extend_schema(tags=["auth"], summary="Текущий пользователь"))
class MeView(generics.RetrieveAPIView):
    """Данные текущего пользователя (вместе со списком прав)."""

    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


@extend_schema(tags=["auth"])
class MyAvatarView(APIView):
    """Моя аватарка: POST — загрузить фото (multipart, поле file), DELETE — убрать."""

    parser_classes = [MultiPartParser]

    @extend_schema(summary="Загрузить свою аватарку", request={"multipart/form-data": AvatarUploadSerializer}, responses=UserSerializer)
    def post(self, request):
        return Response(UserSerializer(save_avatar(request.user, request.FILES.get("file"))).data)

    @extend_schema(summary="Убрать свою аватарку", responses=UserSerializer)
    def delete(self, request):
        return Response(UserSerializer(delete_avatar(request.user)).data)


@extend_schema(
    tags=["auth"],
    summary="Мой отчёт",
    parameters=[
        OpenApiParameter("month", OpenApiTypes.STR, description="один месяц ГГГГ-ММ"),
        OpenApiParameter("month_from", OpenApiTypes.STR, description="с месяца ГГГГ-ММ (включительно)"),
        OpenApiParameter("month_to", OpenApiTypes.STR, description="по месяц ГГГГ-ММ (включительно)"),
    ],
    responses=MyReportSerializer,
)
class MyReportView(APIView):
    """
    Личный отчёт текущего пользователя за период: его продажи, расходы и прибыль (finance), лучшие и худшие лекарства,
    последние чеки; закупки — если он их оформляет. «Сегодня», график за 14 дней и неделя от периода не зависят.
    """

    SALES_DAYS = 14
    RECENT = 10

    def get(self, request):
        user = request.user
        params = request.query_params
        mine = Order.objects.filter(user=user)
        completed_all = mine.filter(status=Order.Status.COMPLETED)
        in_period = filter_by_months(mine, params)
        completed = in_period.filter(status=Order.Status.COMPLETED)

        totals = completed.aggregate(count=Count("id"), total=Sum("total_price"))
        count, total = totals["count"], cents(totals["total"])
        today = completed_all.filter(created_at__date=timezone.localdate()).aggregate(
            count=Count("id"), total=Sum("total_price")
        )

        purchases = None
        if has_permission(user, Perm.PURCHASE_CREATE):
            p = filter_by_months(Purchase.objects.filter(user=user), params).aggregate(
                count=Count("id"), total=Sum("total_price")
            )
            purchases = {"count": p["count"], "total": cents(p["total"])}

        return Response(
            {
                "sales": {
                    "count": count,
                    "total": total,
                    "avg_check": cents(total / count if count else None),
                    "items_sold": OrderItem.objects.filter(order__in=completed).aggregate(s=Sum("packs"))["s"] or 0,
                    "cancelled": in_period.filter(status=Order.Status.CANCELLED).count(),
                },
                "today": {"count": today["count"], "total": cents(today["total"])},
                "sales_by_day": sales_by_day(completed_all, self.SALES_DAYS),
                "week": week_summary(completed_all, timezone.localdate()),
                "finance": profit_split(completed, user.pharmacy),
                "medicine_ranking": medicine_ranking(completed, Medicine.objects.filter(pharmacy=user.pharmacy)),
                "recent_orders": list(
                    in_period.order_by("-created_at")
                    .annotate(items_count=Count("items"))
                    .values("id", "order_number", "created_at", "total_price", "status", "items_count")[: self.RECENT]
                ),
                "purchases": purchases,
            }
        )



@extend_schema(
    tags=["auth"],
    summary="Моя неделя: мои продажи по дням, лучший и худший день",
    parameters=[OpenApiParameter("date", OpenApiTypes.DATE, description="любой день недели; по умолчанию сегодня")],
    responses=WeekSerializer,
)
class MyWeekView(APIView):
    def get(self, request):
        orders = Order.objects.filter(user=request.user, status=Order.Status.COMPLETED)
        return Response(week_summary(orders, parse_day(request.query_params.get("date"))))

@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter("role", OpenApiTypes.STR, enum=User.Role.values, description="Только для админа"),
            OpenApiParameter("pharmacy", OpenApiTypes.INT, description="ID аптеки. Только для админа"),
        ]
    )
)
@extend_schema(tags=["users"])
class UserViewSet(viewsets.ModelViewSet):
    """
    Админ проекта (user.manage): видит и создаёт всех пользователей в любой аптеке.
    Директор (user.manage_pharmacist): видит и создаёт только фармацевтов своей аптеки.
    Фармацевт: доступа нет (только /api/auth/me/).
    """

    queryset = User.objects.select_related("pharmacy").all()
    serializer_class = UserSerializer
    permission_classes = [HasRolePermission]
    required_permissions = {
        "list": Perm.USER_VIEW,
        "retrieve": Perm.USER_VIEW,
        "*": (Perm.USER_MANAGE, Perm.PHARMACIST_MANAGE),
    }
    search_fields = ["username", "full_name", "phone_number"]

    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset.none()

        user = self.request.user
        if has_permission(user, Perm.USER_MANAGE):
            role = self.request.query_params.get("role")
            pharmacy = self.request.query_params.get("pharmacy")
            if role:
                queryset = queryset.filter(role=role)
            if pharmacy:
                queryset = queryset.filter(pharmacy_id=pharmacy)
            return queryset
        return queryset.filter(pharmacy=user.pharmacy, role=User.Role.PHARMACIST)

    def perform_destroy(self, instance):
        if instance == self.request.user:
            raise ValidationError({"detail": "Нельзя удалить самого себя."})
        instance.delete()

    @extend_schema(summary="Аватарка сотрудника: POST — загрузить (file), DELETE — убрать", request={"multipart/form-data": AvatarUploadSerializer})
    @action(detail=True, methods=["post", "delete"], parser_classes=[MultiPartParser])
    def avatar(self, request, pk=None):
        user = self.get_object()  # админ — любой сотрудник, директор — свои фармацевты
        if request.method == "DELETE":
            return Response(self.get_serializer(delete_avatar(user)).data)
        return Response(self.get_serializer(save_avatar(user, request.FILES.get("file"))).data)

    @extend_schema(
        summary="Доступ фармацевта: что он может и что нет (PUT — сохранить)",
        request=AccessUpdateSerializer,
        responses=AccessSerializer,
    )
    @action(detail=True, methods=["get", "put"])
    def access(self, request, pk=None):
        """Директор настраивает своих фармацевтов, админ — любых. Права роли меняются только у этого сотрудника."""
        target = self.get_object()
        if target.role != User.Role.PHARMACIST:
            raise ValidationError({"detail": "Доступ настраивается только фармацевтам — у директора и админа он полный по роли."})
        if request.method == "PUT":
            data = AccessUpdateSerializer(data=request.data)
            data.is_valid(raise_exception=True)
            set_access(target, set(data.validated_data["permissions"]))
        granted = get_permissions(target)
        defaults = ROLE_PERMISSIONS[User.Role.PHARMACIST]
        return Response(
            {
                "user": {
                    "id": target.id,
                    "username": target.username,
                    "full_name": target.full_name,
                    "avatar_url": target.avatar.url if target.avatar else None,
                    "pharmacy_name": target.pharmacy.name if target.pharmacy else None,
                    "is_active": target.is_active,
                },
                "groups": [
                    {"key": key, "items": [{"perm": p, "granted": p in granted, "default": p in defaults} for p in perms]}
                    for key, perms in GRANTABLE.items()
                ],
                "requires": REQUIRES,
            }
        )
