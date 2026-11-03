"""Поиск без учёта регистра для кириллицы на SQLite.

Встроенный LIKE в SQLite не различает регистр только у латиницы: «нурофен» не находит «Нурофен».
Django строит icontains/contains/istartswith… через LIKE, поэтому подменяем LIKE своей функцией
с Unicode-сравнением (casefold, «ё» = «е»). Для PostgreSQL это не нужно — там ILIKE.
"""

import re
from functools import lru_cache


def _fold(value):
    return value.casefold().replace("ё", "е")


@lru_cache(maxsize=256)
def _compile(pattern, escape):
    # LIKE-шаблон -> регулярное выражение: % — любая строка, _ — один символ, escape — следующий символ буквально
    parts = []
    chars = iter(_fold(pattern))
    for ch in chars:
        if escape and ch == escape:
            parts.append(re.escape(next(chars, "")))
        elif ch == "%":
            parts.append(".*")
        elif ch == "_":
            parts.append(".")
        else:
            parts.append(re.escape(ch))
    return re.compile("".join(parts), re.DOTALL)


def unicode_like(pattern, value, escape=None):
    # SQLite вызывает like(X, Y[, Z]) для выражения «Y LIKE X [ESCAPE Z]»
    if pattern is None or value is None:
        return None
    return _compile(pattern, escape).fullmatch(_fold(str(value))) is not None


def register(sender, connection, **kwargs):
    """Обработчик connection_created: ставит Unicode-LIKE на каждое новое SQLite-соединение."""
    if connection.vendor != "sqlite":
        return
    connection.connection.create_function("like", 2, unicode_like, deterministic=True)
    connection.connection.create_function("like", 3, unicode_like, deterministic=True)
