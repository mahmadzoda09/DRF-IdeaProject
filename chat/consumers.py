from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from rest_framework.exceptions import APIException
from rest_framework_simplejwt.authentication import JWTAuthentication

from .services import chat_pharmacy, group_name


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """
    Живой чат аптеки: ws/chat/?token=<access JWT>&pharmacy=<ID, только админу>.
    Сервер только присылает события: {"event": "message", "message": {...}} — новое сообщение,
    {"event": "update", "message": {...}} — сообщение изменили, удалили, отреагировали, закрепили;
    {"event": "pin", "message": {...} | null} — закреплённое сообщение аптеки;
    {"event": "mute", "user": id, "muted": bool} — сотруднику запретили / разрешили писать;
    {"event": "cleared"} — админ очистил историю;
    {"event": "info", "avatar_url": url | null} — сменили фото чата;
    {"event": "read", "read_up_to": id} — все сотрудники прочитали до этого id (синие галочки).
    Отправка и отметка «прочитано» — обычными POST /api/chat/ и /api/chat/read/ (там проверки).
    """

    group = None

    async def connect(self):
        self.group = await self.resolve_group()
        if self.group is None:
            await self.close(code=4401)
            return
        await self.channel_layer.group_add(self.group, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if self.group:
            await self.channel_layer.group_discard(self.group, self.channel_name)

    async def chat_message(self, event):
        await self.send_json({"event": "message", "message": event["message"]})

    async def chat_update(self, event):
        await self.send_json({"event": "update", "message": event["message"]})

    async def chat_pin(self, event):
        await self.send_json({"event": "pin", "message": event["message"]})

    async def chat_mute(self, event):
        await self.send_json({"event": "mute", "user": event["user"], "muted": event["muted"]})

    async def chat_info(self, event):
        await self.send_json({"event": "info", "avatar_url": event["avatar_url"]})

    async def chat_cleared(self, event):
        await self.send_json({"event": "cleared"})

    async def chat_read(self, event):
        await self.send_json({"event": "read", "read_up_to": event["read_up_to"]})

    @database_sync_to_async
    def resolve_group(self):
        params = parse_qs(self.scope["query_string"].decode())
        token = params.get("token", [""])[0]
        try:
            auth = JWTAuthentication()
            user = auth.get_user(auth.get_validated_token(token))
            pharmacy = chat_pharmacy(user, params.get("pharmacy", [None])[0])
        except APIException:  # просроченный токен, нет аптеки, чужая аптека
            return None
        return group_name(pharmacy.id)
