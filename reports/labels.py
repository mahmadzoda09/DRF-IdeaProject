"""Подписи отчётов на трёх языках (как и интерфейс): ключ -> (ru, tg, en)."""

LABELS = {
    "sales.title": ("Отчёт о продажах", "Ҳисоботи фурӯш", "Sales report"),
    "purchases.title": ("Отчёт о закупках", "Ҳисоботи харид", "Purchases report"),
    "period": ("Период", "Давра", "Period"),
    "allTime": ("за всё время", "дар тамоми давра", "all time"),
    "pharmacy": ("Аптека", "Дорухона", "Pharmacy"),
    "allPharmacies": ("Все аптеки", "Ҳамаи дорухонаҳо", "All pharmacies"),
    "seller": ("Продавец", "Фурӯшанда", "Seller"),
    "generated": ("Сформирован", "Тартиб дода шуд", "Generated"),
    "summary": ("Итоги", "Хулоса", "Summary"),
    "byMonth": ("По месяцам", "Аз рӯи моҳҳо", "By month"),
    "byMedicine": ("По лекарствам", "Аз рӯи доруҳо", "By medicine"),
    "bySeller": ("По продавцам", "Аз рӯи фурӯшандагон", "By seller"),
    "bySupplier": ("По поставщикам", "Аз рӯи таъминкунандагон", "By supplier"),
    "orders": ("Чеки", "Чекҳо", "Receipts"),
    "purchases": ("Закупки", "Харидҳо", "Purchases"),
    "month": ("Месяц", "Моҳ", "Month"),
    "medicine": ("Лекарство", "Дору", "Medicine"),
    "supplier": ("Поставщик", "Таъминкунанда", "Supplier"),
    "employee": ("Сотрудник", "Корманд", "Employee"),
    "number": ("Номер", "Рақам", "Number"),
    "date": ("Дата", "Сана", "Date"),
    "items": ("Позиций", "Мавқеъҳо", "Items"),
    "qty": ("Кол-во, шт.", "Миқдор, адад", "Qty"),
    "checks": ("Чеков", "Чекҳо", "Receipts"),
    "count": ("Закупок", "Харидҳо", "Purchases"),
    "sales": ("Продажи, сомони", "Фурӯш, сомонӣ", "Sales, TJS"),
    "cost": ("Себестоимость, сомони", "Арзиши аслӣ, сомонӣ", "Cost, TJS"),
    "profit": ("Прибыль, сомони", "Фоида, сомонӣ", "Profit, TJS"),
    "amount": ("Сумма, сомони", "Маблағ, сомонӣ", "Amount, TJS"),
    "avgPrice": ("Средняя цена, сомони", "Нархи миёна, сомонӣ", "Avg price, TJS"),
    "discount": ("Скидка, %", "Тахфиф, %", "Discount, %"),
    "total": ("Итого", "Ҳамагӣ", "Total"),
    "sum.checks": ("Чеков", "Шумораи чекҳо", "Receipts"),
    "sum.units": ("Продано упаковок", "Бастаҳои фурӯхташуда", "Units sold"),
    "sum.sales": ("Выручка, сомони", "Даромад, сомонӣ", "Revenue, TJS"),
    "sum.avg": ("Средний чек, сомони", "Чеки миёна, сомонӣ", "Average receipt, TJS"),
    "sum.cost": ("Себестоимость проданного, сомони", "Арзиши аслии фурӯхташуда, сомонӣ", "Cost of goods sold, TJS"),
    "sum.profit": ("Прибыль, сомони", "Фоида, сомонӣ", "Profit, TJS"),
    "sum.margin": ("Рентабельность, %", "Даромаднокӣ, %", "Margin, %"),
    "sum.cancelled": ("Отменено чеков", "Чекҳои бекоршуда", "Cancelled receipts"),
    "sum.purchases": ("Закупок", "Шумораи харидҳо", "Purchases"),
    "sum.units.in": ("Получено упаковок", "Бастаҳои воридшуда", "Units received"),
    "sum.spent": ("Потрачено, сомони", "Сарф шуд, сомонӣ", "Spent, TJS"),
    "sum.suppliers": ("Поставщиков", "Таъминкунандагон", "Suppliers"),
    "sign.confirm": (
        "Данные отчёта проверены. Подтверждаю.",
        "Маълумоти ҳисобот санҷида шуд. Тасдиқ мекунам.",
        "The report data has been checked. Approved.",
    ),
    "sign.director": ("Директор аптеки", "Директори дорухона", "Pharmacy director"),
    "sign.signature": ("подпись", "имзо", "signature"),
    "sign.name": ("Ф. И. О.", "ному насаб", "full name"),
    "sign.date": ("Дата", "Сана", "Date"),
    "sign.stamp": ("М. П.", "Ҷои мӯҳр", "Stamp"),
}

MONTHS = {
    "ru": ["Январь", "Февраль", "Март", "Апрель", "Май", "Июнь", "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь"],
    "tg": ["Январ", "Феврал", "Март", "Апрел", "Май", "Июн", "Июл", "Август", "Сентябр", "Октябр", "Ноябр", "Декабр"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
}

INDEX = {"ru": 0, "tg": 1, "en": 2}


class Labels:
    def __init__(self, language):
        self.language = language if language in INDEX else "ru"

    def __call__(self, key):
        return LABELS[key][INDEX[self.language]]

    def month(self, day):
        return f"{MONTHS[self.language][day.month - 1]} {day.year}"
