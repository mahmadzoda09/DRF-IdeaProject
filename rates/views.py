from drf_spectacular.utils import extend_schema
from rest_framework import serializers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .sources import get_rates


class PairSerializer(serializers.Serializer):
    buy = serializers.DecimalField(max_digits=12, decimal_places=4, help_text="банк покупает")
    sell = serializers.DecimalField(max_digits=12, decimal_places=4, help_text="банк продаёт")


class BankSerializer(serializers.Serializer):
    key = serializers.CharField()
    name = serializers.CharField()
    available = serializers.BooleanField(help_text="false — курсов банка ещё нет (сайт не ответил)")
    fetched_at = serializers.DateTimeField(allow_null=True)
    rates = serializers.DictField(child=PairSerializer())


class RatesSerializer(serializers.Serializer):
    updated_at = serializers.DateTimeField(allow_null=True)
    loading = serializers.BooleanField(help_text="часть курсов ещё загружается — спросите снова через полминуты")
    currencies = serializers.ListField(child=serializers.CharField())
    nbt = serializers.DictField(
        child=serializers.DecimalField(max_digits=12, decimal_places=4), help_text="официальный курс Нацбанка"
    )
    banks = BankSerializer(many=True)


@extend_schema(tags=["rates"], summary="Курсы валют: Нацбанк и крупные банки Таджикистана", responses=RatesSerializer)
class RatesView(APIView):
    """Курс сомони к USD, EUR, RUB. Доступно всем сотрудникам. Обновляется раз в 30 минут."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response(RatesSerializer(get_rates()).data)
