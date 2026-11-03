"""
RBAC: роль -> набор прав.
Вьюхи и сериализаторы проверяют права (Perm), а не конкретные роли.
Чтобы поменять доступ роли — правим только ROLE_PERMISSIONS.
"""

from .models import User


class Perm:
    PHARMACY_VIEW = "pharmacy.view"
    PHARMACY_MANAGE = "pharmacy.manage"
    PHARMACY_STATS = "pharmacy.stats"
    ALL_PHARMACIES = "pharmacy.all"  

    USER_VIEW = "user.view"
    USER_MANAGE = "user.manage"
    PHARMACIST_MANAGE = "user.manage_pharmacist"
    
    MEDICINE_VIEW = "medicine.view"
    MEDICINE_MANAGE = "medicine.manage"
    SUPPLIER_VIEW = "supplier.view"
    SUPPLIER_MANAGE = "supplier.manage"

    # закупки
    PURCHASE_VIEW = "purchase.view"
    PURCHASE_CREATE = "purchase.create"

    # продажи
    ORDER_VIEW = "order.view"
    ORDER_VIEW_ALL = "order.view_all"  # все продажи, а не только свои
    ORDER_CREATE = "order.create"
    ORDER_CANCEL = "order.cancel"

    # списание просроченных лекарств
    WRITEOFF_VIEW = "writeoff.view"
    WRITEOFF_CREATE = "writeoff.create"

    # ИИ-ассистент
    ASSISTANT_USE = "assistant.use"


ROLE_PERMISSIONS = {
    User.Role.ADMIN: {
        Perm.PHARMACY_VIEW,
        Perm.PHARMACY_MANAGE,
        Perm.PHARMACY_STATS,
        Perm.ALL_PHARMACIES,
        Perm.USER_VIEW,
        Perm.USER_MANAGE,
        Perm.MEDICINE_VIEW,
        Perm.SUPPLIER_VIEW,
        Perm.PURCHASE_VIEW,
        Perm.ORDER_VIEW,
        Perm.ORDER_VIEW_ALL,
        Perm.WRITEOFF_VIEW,
        Perm.ASSISTANT_USE,
    },
    User.Role.DIRECTOR: {
        Perm.PHARMACY_VIEW,
        Perm.PHARMACY_STATS,
        Perm.USER_VIEW,
        Perm.PHARMACIST_MANAGE,
        Perm.MEDICINE_VIEW,
        Perm.MEDICINE_MANAGE,
        Perm.SUPPLIER_VIEW,
        Perm.SUPPLIER_MANAGE,
        Perm.PURCHASE_VIEW,
        Perm.PURCHASE_CREATE,
        Perm.ORDER_VIEW,
        Perm.ORDER_VIEW_ALL,
        Perm.ORDER_CREATE,
        Perm.ORDER_CANCEL,
        Perm.WRITEOFF_VIEW,
        Perm.WRITEOFF_CREATE,
        Perm.ASSISTANT_USE,
    },
    User.Role.PHARMACIST: {
        # страницы «Аптеки» у фармацевта нет: название своей аптеки он получает из профиля (/auth/me/)
        Perm.MEDICINE_VIEW,
        Perm.SUPPLIER_VIEW,
        Perm.ORDER_VIEW,
        Perm.ORDER_CREATE,
        Perm.ASSISTANT_USE,
    },
}


# Права, которые директор (или админ) может дать или отнять у фармацевта — работа внутри аптеки.
# Порядок и группы — как на странице «Доступ»; управление сотрудниками и аптеками сюда не входит.
GRANTABLE = {
    "sales": [Perm.ORDER_VIEW, Perm.ORDER_CREATE, Perm.ORDER_VIEW_ALL, Perm.ORDER_CANCEL],
    "medicines": [Perm.MEDICINE_VIEW, Perm.MEDICINE_MANAGE, Perm.WRITEOFF_VIEW, Perm.WRITEOFF_CREATE],
    "purchases": [Perm.PURCHASE_VIEW, Perm.PURCHASE_CREATE, Perm.SUPPLIER_VIEW, Perm.SUPPLIER_MANAGE],
    "other": [Perm.PHARMACY_STATS, Perm.PHARMACY_VIEW, Perm.ASSISTANT_USE],
}
GRANTABLE_ALL = [perm for perms in GRANTABLE.values() for perm in perms]

# без чего право бессмысленно: создавать закупку, не видя закупок и лекарств, нельзя
REQUIRES = {
    Perm.ORDER_CREATE: [Perm.ORDER_VIEW, Perm.MEDICINE_VIEW],
    Perm.ORDER_VIEW_ALL: [Perm.ORDER_VIEW],
    Perm.ORDER_CANCEL: [Perm.ORDER_VIEW],
    Perm.MEDICINE_MANAGE: [Perm.MEDICINE_VIEW],
    Perm.WRITEOFF_VIEW: [Perm.MEDICINE_VIEW],
    Perm.WRITEOFF_CREATE: [Perm.WRITEOFF_VIEW],
    Perm.PURCHASE_VIEW: [Perm.SUPPLIER_VIEW],
    Perm.PURCHASE_CREATE: [Perm.PURCHASE_VIEW, Perm.MEDICINE_VIEW],
    Perm.SUPPLIER_MANAGE: [Perm.SUPPLIER_VIEW],
}


def with_requirements(perms):
    """Добавляет права, без которых выбранные не работают (рекурсивно)."""
    result = set(perms)
    stack = list(result)
    while stack:
        for needed in REQUIRES.get(stack.pop(), []):
            if needed not in result:
                result.add(needed)
                stack.append(needed)
    return result


def get_permissions(user):
    if not (user and user.is_authenticated):
        return set()
    # сотрудник без аптеки ничего не может
    if user.role != User.Role.ADMIN and user.pharmacy_id is None:
        return set()
    perms = ROLE_PERMISSIONS.get(user.role, set())
    if user.role == User.Role.PHARMACIST and (user.extra_permissions or user.revoked_permissions):
        perms = (perms - set(user.revoked_permissions)) | (set(user.extra_permissions) & set(GRANTABLE_ALL))
    return perms


def set_access(user, granted):
    """Сохраняет доступ фармацевта как отличия от прав роли; granted — итоговый набор из GRANTABLE."""
    granted = with_requirements(granted) & set(GRANTABLE_ALL)
    defaults = ROLE_PERMISSIONS[user.role] & set(GRANTABLE_ALL)
    user.extra_permissions = sorted(granted - defaults)
    user.revoked_permissions = sorted(defaults - granted)
    user.save(update_fields=["extra_permissions", "revoked_permissions"])


def has_permission(user, perm):
    return perm in get_permissions(user)
