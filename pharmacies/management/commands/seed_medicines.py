from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError

from pharmacies.models import Medicine, Pharmacy

# Популярные лекарства и ориентировочная розничная цена в сомони (TJS).
# Цены примерные — реальные зависят от аптеки и поставки, их можно поправить в интерфейсе.
MEDICINES = [
    ("Парацетамол 500 мг №10", "5.00"),
    ("Ибупрофен 400 мг №20", "18.00"),
    ("Нурофен 200 мг №10", "35.00"),
    ("Анальгин 500 мг №10", "6.00"),
    ("Цитрамон П №10", "5.00"),
    ("Но-шпа 40 мг №24", "38.00"),
    ("Аспирин Кардио 100 мг №28", "55.00"),
    ("Амоксициллин 500 мг №20", "25.00"),
    ("Аугментин 875/125 мг №14", "120.00"),
    ("Азитромицин 500 мг №3", "25.00"),
    ("Цетиризин 10 мг №10", "12.00"),
    ("Супрастин 25 мг №20", "40.00"),
    ("Лоратадин 10 мг №10", "8.00"),
    ("Омепразол 20 мг №30", "15.00"),
    ("Мезим форте №20", "40.00"),
    ("Смекта 3 г №10", "45.00"),
    ("Активированный уголь 250 мг №10", "3.00"),
    ("Лоперамид 2 мг №20", "10.00"),
    ("Каптоприл 25 мг №40", "12.00"),
    ("Валидол 60 мг №10", "8.00"),
    ("Метформин 500 мг №60", "30.00"),
    ("Амлодипин 5 мг №30", "15.00"),
    ("Эналаприл 10 мг №20", "8.00"),
    ("Диклофенак 50 мг №20", "10.00"),
    ("Цефтриаксон 1 г №1", "12.00"),
    ("Аскорбиновая кислота 50 мг №200", "10.00"),
    ("Глицин 100 мг №50", "14.00"),
    ("Корвалол 25 мл", "12.00"),
    ("Фурацилин 20 мг №10", "6.00"),
    ("Бисептол 480 мг №20", "18.00"),
]


def demo_barcode(n):
    """EAN-13 из диапазона 200–299 (внутренние коды магазина), чтобы не совпасть с реальными товарами."""
    digits = f"200{n:09d}"
    checksum = sum(int(d) * (3 if i % 2 else 1) for i, d in enumerate(digits))
    return digits + str((10 - checksum % 10) % 10)


class Command(BaseCommand):
    help = "Добавляет популярные лекарства (30 шт.) с ценами и демо-штрих-кодами (по умолчанию — во все аптеки)."

    def add_arguments(self, parser):
        parser.add_argument("--pharmacy", type=int, help="id аптеки; без него — во все аптеки")

    def handle(self, *args, **options):
        pharmacies = Pharmacy.objects.all()
        if options["pharmacy"]:
            pharmacies = pharmacies.filter(id=options["pharmacy"])
        if not pharmacies.exists():
            raise CommandError("Аптеки не найдены. Сначала создайте аптеку.")

        for pharmacy in pharmacies:
            created = barcoded = 0
            for n, (title, price) in enumerate(MEDICINES, start=1):
                # у существующих цену не трогаем (могли изменить вручную), только дописываем пустой штрих-код
                medicine, is_new = Medicine.objects.get_or_create(
                    pharmacy=pharmacy, title=title, defaults={"price": Decimal(price), "barcode": demo_barcode(n)}
                )
                created += is_new
                taken = Medicine.objects.filter(pharmacy=pharmacy, barcode=demo_barcode(n)).exists()
                if not medicine.barcode and not taken:
                    medicine.barcode = demo_barcode(n)
                    medicine.save(update_fields=["barcode"])
                    barcoded += 1
            self.stdout.write(
                self.style.SUCCESS(
                    f"{pharmacy.name}: добавлено {created} из {len(MEDICINES)}, штрих-кодов дописано {barcoded}"
                )
            )
