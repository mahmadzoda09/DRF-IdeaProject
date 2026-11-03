# Pharmacy API (Django REST Framework)

REST API для сети аптек: аптеки, сотрудники с ролями, лекарства, поставщики, закупки (приход товара) и продажи.

## Запуск

```bash
python -m venv venv
venv\Scripts\activate          # Windows
# source venv/bin/activate     # Linux / macOS

pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser   # это и есть АДМИН ПРОЕКТА
python manage.py runserver
```

- Фронтенд (панель управления): http://127.0.0.1:8000/
- Тесты: `python manage.py test`

### Бэкенд и фронтенд отдельно

Фронтенд можно запускать отдельным процессом (нужен только Node.js 18+, без `npm install`).
`frontend_server.js` отдаёт `frontend/` и пересылает `/api`, `/media` и WebSocket `/ws` на бэкенд,
поэтому фронтенд работает без изменений и без CORS.

```bash
# терминал 1 — бэкенд (API, медиа, WebSocket-чат)
python manage.py runserver 8000

# терминал 2 — фронтенд
node frontend_server.js
```

- Сайт: http://127.0.0.1:3000/
- Другой порт или адрес бэкенда: `PORT=5000 BACKEND=http://10.0.0.5:8000 node frontend_server.js`
  (в PowerShell: `$env:PORT=5000; $env:BACKEND="http://10.0.0.5:8000"; node frontend_server.js`)
- `HOST=0.0.0.0` — открыть фронтенд для других компьютеров в сети.

## Фронтенд

Одностраничное приложение на чистом JS без сборки (`frontend/`), отдаётся самим Django — npm и CORS не нужны.
Меню и кнопки показываются по правам пользователя (`permissions` из `/api/auth/me/`):

- **Статистика** — продажи, мало на складе, скоро истекает срок (админ выбирает аптеку)
- **Продажи** — список, новая продажа с корзиной и скидкой, отмена
- **Лекарства**, **Поставщики** — поиск, сортировка, CRUD
- **Закупки** — приход товара с несколькими позициями
- **Сотрудники**, **Аптеки** — управление пользователями и аптеками

## Роли

| Роль | Кто это | Как появляется |
|---|---|---|
| `admin` | Админ всего проекта | `createsuperuser` или создаёт другой админ |
| `director` | Директор аптеки | Создаёт **только админ** проекта, обязательно с `pharmacy` |
| `pharmacist` | Фармацевт / кассир | Создаёт админ или **директор своей аптеки** |

**Аптеку может создать только админ проекта.**

## Кто что может

| Действие | admin | director | pharmacist |
|---|---|---|---|
| Создать / изменить / удалить аптеку | ✅ | ❌ | ❌ |
| Смотреть аптеки | все | своя | своя |
| Создать директора | ✅ | ❌ | ❌ |
| Создать фармацевта | ✅ | ✅ (в своей аптеке) | ❌ |
| Лекарства, поставщики: смотреть | все | своя аптека | своя аптека |
| Лекарства, поставщики: создать / изменить / удалить | ❌ | ✅ | ❌ |
| Закупка (приход товара) | смотреть | ✅ | ❌ |
| Продажа (заказ) | смотреть | ✅ | ✅ |
| Отменить продажу | ❌ | ✅ | ❌ |
| Статистика аптеки | ✅ | ✅ | ❌ |

Каждый сотрудник видит **только данные своей аптеки**. Фармацевт видит только свои продажи.

## Эндпоинты

| Метод | URL | Описание |
|---|---|---|
| POST | `/api/auth/login/` | Логин → `access`, `refresh`, `user` |
| POST | `/api/auth/refresh/` | Новый access токен |
| GET | `/api/auth/me/` | Текущий пользователь |
| CRUD | `/api/pharmacies/` | Аптеки |
| GET | `/api/pharmacies/{id}/stats/` | Продажи, мало на складе, скоро истекает срок |
| CRUD | `/api/users/` | Пользователи (`?role=director` для админа) |
| CRUD | `/api/medicines/` | Лекарства (`?search=парацетамол`) |
| CRUD | `/api/suppliers/` | Поставщики |
| GET, POST | `/api/purchases/` | Закупки |
| GET, POST | `/api/orders/` | Продажи (`?status=completed`) |
| POST | `/api/orders/{id}/cancel/` | Отмена продажи |

## Как работает склад

- `quantity` у лекарства **нельзя менять вручную**.
- **Закупка** увеличивает остаток.
- **Продажа** уменьшает остаток. Если товара не хватает, вернётся ошибка 400, и ничего не спишется.
- **Отмена продажи** возвращает товар на склад.
- В `OrderItem.price` сохраняется цена на момент продажи.
- В `PurchaseItem` хранятся закупочная цена и срок годности (`expire_date`) партии.
- `discount` в заказе указывается в процентах (0–100).

## Пример сценария

**1. Админ создаёт аптеку и директора**

```json
POST /api/pharmacies/
{"name": "Аптека №1", "address": "Душанбе, Рудаки 10", "phone_number": "+992900000000"}

POST /api/users/
{"username": "director1", "password": "pass12345", "full_name": "Али Валиев",
 "role": "director", "pharmacy": 1}
```

**2. Директор создаёт фармацевта, лекарство, поставщика и закупку**

```json
POST /api/users/
{"username": "seller1", "password": "pass12345", "full_name": "Мадина"}

POST /api/medicines/
{"title": "Парацетамол 500мг", "price": "12.50"}

POST /api/suppliers/
{"name": "Фарм Снаб", "phone_number": "+992911111111"}

POST /api/purchases/
{"supplier": 1, "items": [
  {"medicine": 1, "quantity": 100, "price": "8.00", "expire_date": "2028-01-01"}
]}
```

**3. Фармацевт продаёт**

```json
POST /api/orders/
{"discount": "5", "items": [{"medicine": 1, "quantity": 2}]}
```

## Структура

```
config/      настройки, urls
accounts/    User + роли, permissions, mixins, логин
pharmacies/  Pharmacy, Medicine, Supplier, статистика
purchases/   Purchase, PurchaseItem (приход)
orders/      Order, OrderItem (продажа) + тесты
frontend/    templates/index.html, static/js (api.js, app.js), static/css
```
# DRF-IdeaProject
# DRF-IdeaProject
