import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db.models import Max
from rest_framework.exceptions import ValidationError

from accounts.models import User
from accounts.rbac import Perm, has_permission
from pharmacies.models import Pharmacy

from .models import ChatRead, Message

logger = logging.getLogger(__name__)


def group_name(pharmacy_id):
    return f"chat_{pharmacy_id}"


def chat_pharmacy(user, pharmacy_id=None):
    """Чат какой аптеки: сотруднику — своей; админу — указанной (pharmacy_id обязателен)."""
    if not has_permission(user, Perm.ALL_PHARMACIES):
        if user.pharmacy_id is None:
            raise ValidationError({"detail": "Вы не привязаны к аптеке."})
        return user.pharmacy
    pharmacy = Pharmacy.objects.filter(pk=pharmacy_id).first() if str(pharmacy_id or "").isdigit() else None
    if pharmacy is None:
        raise ValidationError({"pharmacy": "Выберите аптеку."})
    return pharmacy


def _send(pharmacy_id, event):
    layer = get_channel_layer()
    if layer is None:
        return
    try:
        async_to_sync(layer.group_send)(group_name(pharmacy_id), event)
    except Exception:  # Redis недоступен: сообщение уже в базе, клиенты догрузят его опросом
        logger.exception("chat: не удалось разослать событие по WebSocket")


def broadcast(pharmacy_id, message):
    """Разослать новое сообщение всем, у кого открыт чат этой аптеки (WebSocket)."""
    _send(pharmacy_id, {"type": "chat.message", "message": message})


def broadcast_update(pharmacy_id, message):
    """Сообщение изменилось (текст, удаление, реакции, закрепление) — всем новые данные."""
    _send(pharmacy_id, {"type": "chat.update", "message": message})


def broadcast_pin(pharmacy_id, message):
    """Закреплённое сообщение аптеки (None — открепили)."""
    _send(pharmacy_id, {"type": "chat.pin", "message": message})


def broadcast_mute(pharmacy_id, user_id, muted):
    """Сотруднику запретили / разрешили писать — его чат сразу переключается."""
    _send(pharmacy_id, {"type": "chat.mute", "user": user_id, "muted": muted})


def broadcast_info(pharmacy):
    """Сменили фото чата — у всех обновится шапка."""
    _send(pharmacy.id, {"type": "chat.info", "avatar_url": pharmacy.chat_avatar.url if pharmacy.chat_avatar else None})


def broadcast_cleared(pharmacy_id):
    """Админ очистил историю чата."""
    _send(pharmacy_id, {"type": "chat.cleared"})


def read_up_to(pharmacy):
    """
    До какого id чат прочитали ВСЕ активные сотрудники аптеки (синие галочки).
    Сообщения, написанные до прихода сотрудника, от него не ждём: новичок не «снимает» синие галочки с истории.
    """
    reads = dict(ChatRead.objects.filter(pharmacy=pharmacy).values_list("user_id", "last_read_id"))
    messages = Message.objects.filter(pharmacy=pharmacy)
    result = None
    for user_id, joined in User.objects.filter(pharmacy=pharmacy, is_active=True).values_list("id", "date_joined"):
        seen = reads.get(user_id, 0)
        if result is not None and seen >= result:
            continue  # и так не меньше текущего минимума — лишний запрос не нужен
        before_joined = messages.filter(created_at__lt=joined).aggregate(m=Max("id"))["m"] or 0
        seen = max(seen, before_joined)
        result = seen if result is None else min(result, seen)
    return result or 0


def mark_read(user, pharmacy, last_id):
    """Сотрудник прочитал чат до last_id. Если от этого стало «прочитали все» дальше — всем уходит новое значение."""
    if user.pharmacy_id != pharmacy.id:  # админ проекта только наблюдает: его прочтение не считается
        return read_up_to(pharmacy)
    last_id = min(int(last_id), Message.objects.filter(pharmacy=pharmacy).aggregate(m=Max("id"))["m"] or 0)
    record, _ = ChatRead.objects.get_or_create(pharmacy=pharmacy, user=user)
    if last_id <= record.last_read_id:
        return read_up_to(pharmacy)
    before = read_up_to(pharmacy)
    record.last_read_id = last_id
    record.save(update_fields=["last_read_id"])
    after = read_up_to(pharmacy)
    if after != before:
        _send(pharmacy.id, {"type": "chat.read", "read_up_to": after})
    return after
