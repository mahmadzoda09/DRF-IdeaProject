import random
from datetime import datetime, time, timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from accounts.models import User
from orders.models import Order
from orders.serializers import OrderSerializer
from pharmacies.models import Medicine, Pharmacy, Supplier
from purchases.models import Purchase
from purchases.serializers import PurchaseSerializer

DAYS = 14
SUPPLIERS = [
    ("Шифо-Фарм (демо)", "+992 37 221-10-01"),
    ("Дору-Импорт (демо)", "+992 37 221-10-02"),
    ("Саломатӣ (демо)", "+992 37 221-10-03"),
]


class Command(BaseCommand):
    help = (
        "Демо-данные для статистики: поставщики, закупки (с партиями, у которых скоро истекает срок) "
        f"и продажи за последние {DAYS} дней. Идёт через те же сериализаторы, что и API, "
        "поэтому остатки и суммы считаются как при обычной работе."
    )

    def add_arguments(self, parser):
        parser.add_argument("--pharmacy", type=int, help="id аптеки; без него — во все аптеки")
        parser.add_argument("--force", action="store_true", help="добавить, даже если продажи уже есть")
        parser.add_argument("--seed", type=int, default=2004, help="зерно случайных чисел")
        parser.add_argument("--purchases", type=int, default=2, help="на сколько закупок разбить приход (по умолчанию 2)")

    def handle(self, *args, **options):
        pharmacies = Pharmacy.objects.all()
        if options["pharmacy"]:
            pharmacies = pharmacies.filter(id=options["pharmacy"])
        if not pharmacies.exists():
            raise CommandError("Аптеки не найдены. Сначала создайте аптеку.")

        rng = random.Random(options["seed"])
        for pharmacy in pharmacies:
            staff = list(User.objects.filter(pharmacy=pharmacy, is_active=True))
            if not staff:
                self.stdout.write(self.style.WARNING(f"{pharmacy.name}: нет сотрудников — пропущено"))
                continue
            if pharmacy.orders.exists() and not options["force"]:
                self.stdout.write(
                    self.style.WARNING(f"{pharmacy.name}: продажи уже есть — пропущено (--force, чтобы добавить)")
                )
                continue

            call_command("seed_medicines", pharmacy=pharmacy.id, stdout=self.stdout)
            purchases = self.make_purchases(pharmacy, staff[0], rng, options["purchases"])
            orders = self.make_orders(pharmacy, staff, rng)
            self.stdout.write(self.style.SUCCESS(f"{pharmacy.name}: закупок {purchases}, продаж {orders}"))

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def context(user):
        # сериализаторам из request нужен только user
        return {"request": SimpleNamespace(user=user)}

    @staticmethod
    def moment(day, rng, latest_hour=21):
        at = time(rng.randint(8, max(8, latest_hour)), rng.randint(0, 59))
        return timezone.make_aware(datetime.combine(day, at))

    def make_purchases(self, pharmacy, user, rng, count):
        today = timezone.localdate()
        suppliers = [
            Supplier.objects.get_or_create(pharmacy=pharmacy, name=name, defaults={"phone_number": phone})[0]
            for name, phone in SUPPLIERS
        ]
        medicines = list(Medicine.objects.filter(pharmacy=pharmacy).order_by("id"))
        rng.shuffle(medicines)

        # у нескольких лекарств — маленькая партия (станут «мало на складе»),
        # у нескольких — срок годности в ближайшие 30 дней
        small = set(m.id for m in medicines[:5])
        expiring = set(m.id for m in medicines[5:9])

        # каждое лекарство попадает ровно в одну закупку; закупки разнесены по дням, самая поздняя — до периода продаж
        count = max(1, min(count, len(medicines)))
        batches = [medicines[i::count] for i in range(count)]
        for n, batch in enumerate(batches):
            items = []
            for m in batch:
                quantity = rng.randint(12, 18) if m.id in small else rng.randint(40, 120)
                if m.id in expiring:
                    expire = today + timedelta(days=rng.randint(5, 28))
                else:
                    expire = today + timedelta(days=rng.randint(240, 900))
                cost = (m.price * Decimal("0.7")).quantize(Decimal("0.01"))  # закупка ≈ 70% розницы
                items.append({"medicine": m.id, "quantity": quantity, "price": str(cost), "expire_date": expire})

            serializer = PurchaseSerializer(
                data={"supplier": suppliers[n % len(suppliers)].id, "items": items}, context=self.context(user)
            )
            serializer.is_valid(raise_exception=True)
            purchase = serializer.save(pharmacy=pharmacy, user=user)
            Purchase.objects.filter(pk=purchase.pk).update(
                created_at=self.moment(today - timedelta(days=DAYS + 2 + (count - 1 - n) * 2), rng)
            )
        return len(batches)

    def make_orders(self, pharmacy, staff, rng):
        today = timezone.localdate()
        now = timezone.localtime()
        created = 0

        for back in range(DAYS - 1, -1, -1):
            day = today - timedelta(days=back)
            # выходные чуть спокойнее, к концу периода продаж немного больше
            per_day = rng.randint(3, 6) if day.weekday() >= 5 else rng.randint(5, 10)
            per_day += (DAYS - back) // 5
            if back == 0:
                per_day = max(1, per_day * max(now.hour - 8, 1) // 13)  # сегодня — только до текущего часа

            for _ in range(per_day):
                in_stock = list(Medicine.objects.filter(pharmacy=pharmacy, quantity__gt=0))
                if not in_stock:
                    return created
                picks = rng.sample(in_stock, k=min(len(in_stock), rng.choice([1, 1, 2, 2, 3])))
                items = [{"medicine": m.id, "quantity": min(m.quantity, rng.choice([1, 1, 1, 2, 2, 3]))} for m in picks]
                discount = rng.choice(["0"] * 8 + ["5", "10"])

                seller = rng.choice(staff)
                serializer = OrderSerializer(data={"discount": discount, "items": items}, context=self.context(seller))
                if not serializer.is_valid():
                    continue
                order = serializer.save(pharmacy=pharmacy, user=seller)
                latest = now.hour if back == 0 else 21
                Order.objects.filter(pk=order.pk).update(created_at=self.moment(day, rng, latest))
                created += 1
        return created
