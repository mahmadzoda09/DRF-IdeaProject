from rest_framework.permissions import BasePermission

from .rbac import has_permission


class HasRolePermission(BasePermission):
    """
    RBAC-проверка. Во вьюхе задаётся:
        required_permissions = {"list": Perm.X, "create": Perm.Y, "*": Perm.Z}
    "*" — право по умолчанию для остальных действий.
    Кортеж прав (Perm.A, Perm.B) — достаточно любого из них.
    """

    message = "Недостаточно прав."

    def has_permission(self, request, view):
        required = getattr(view, "required_permissions", {})
        # у обычного APIView нет action — действует право "*"
        perms = required.get(getattr(view, "action", None), required.get("*"))
        if perms is None:
            return False
        if isinstance(perms, str):
            perms = (perms,)
        return any(has_permission(request.user, perm) for perm in perms)
