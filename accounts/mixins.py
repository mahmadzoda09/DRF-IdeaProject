import re
from datetime import date

from django.db.models import ProtectedError
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from .rbac import Perm, has_permission


class PharmacyScopedMixin:
    def get_queryset(self):
        queryset = super().get_queryset()
        if getattr(self, "swagger_fake_view", False):
            return queryset.none()

        user = self.request.user
        if has_permission(user, Perm.ALL_PHARMACIES):
            return queryset
        return queryset.filter(pharmacy=user.pharmacy)

    def perform_create(self, serializer):
        serializer.save(pharmacy=self.request.user.pharmacy)


def _parse_month(name, value):
    match = re.fullmatch(r"(\d{4})-(\d{2})", value)
    if not match or not 1 <= int(match[2]) <= 12:
        raise ValidationError({name: "Укажите месяц в формате ГГГГ-ММ, например 2026-10."})
    return date(int(match[1]), int(match[2]), 1)


def filter_by_months(queryset, params, field="created_at"):
    """
    Фильтр по месяцам (по дате `field` в часовом поясе проекта), формат ГГГГ-ММ:
    month=2026-10 — один месяц; month_from=2026-08&month_to=2026-10 — период включительно (любая граница может отсутствовать).
    """
    start = end = None
    if params.get("month"):
        start = end = _parse_month("month", params["month"])
    if params.get("month_from"):
        start = _parse_month("month_from", params["month_from"])
    if params.get("month_to"):
        end = _parse_month("month_to", params["month_to"])
    if start and end and start > end:
        raise ValidationError({"month_to": "Конечный месяц раньше начального."})

    if start:
        queryset = queryset.filter(**{f"{field}__date__gte": start})
    if end:
        next_month = date(end.year + end.month // 12, end.month % 12 + 1, 1)
        queryset = queryset.filter(**{f"{field}__date__lt": next_month})
    return queryset


def parse_day(value):
    """?date=ГГГГ-ММ-ДД; без значения — сегодня (в часовом поясе проекта)."""
    if not value:
        return timezone.localdate()
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise ValidationError({"date": "Укажите дату в формате ГГГГ-ММ-ДД, например 2026-10-30."})


class MonthFilterMixin:
    """?month= / ?month_from= / ?month_to= в списке (см. filter_by_months)."""

    def get_queryset(self):
        return filter_by_months(super().get_queryset(), self.request.query_params)


class ProtectedDeleteMixin:
    def perform_destroy(self, instance):
        try:
            instance.delete()
        except ProtectedError:
            raise ValidationError({"detail": "Нельзя удалить: объект используется в заказах или закупках."})
