import shutil
import tempfile

from asgiref.sync import async_to_sync
from channels.testing import WebsocketCommunicator
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TransactionTestCase, override_settings
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from config.asgi import application

from accounts.models import User
from pharmacies.models import Pharmacy


class ChatTest(APITestCase):
    """Сотрудники пишут в чат своей аптеки и не видят чужой; админ читает и пишет в любой."""

    def setUp(self):
        self.mine, self.other = Pharmacy.objects.create(name="Моя"), Pharmacy.objects.create(name="Чужая")
        self.director = User.objects.create_user("d", password="x", pharmacy=self.mine, role=User.Role.DIRECTOR, full_name="Али")
        self.pharmacist = User.objects.create_user("p", password="x", pharmacy=self.mine, role=User.Role.PHARMACIST)
        self.stranger = User.objects.create_user("s", password="x", pharmacy=self.other, role=User.Role.PHARMACIST)
        self.admin = User.objects.create_superuser("a", password="x")

    def say(self, user, text, **extra):
        self.client.force_authenticate(user)
        return self.client.post("/api/chat/", {"text": text, **extra})

    def test_pharmacy_chat(self):
        r = self.say(self.director, "  Салом  ", pharmacy=self.other.id)  # чужую аптеку указать нельзя — пишется в свою
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["text"], r.data["author"], r.data["pharmacy"]), ("Салом", "Али", self.mine.id))
        first = r.data["id"]
        self.say(self.pharmacist, "Ассалому алейкум")
        self.assertEqual(self.say(self.pharmacist, "   ").status_code, 400)

        self.client.force_authenticate(self.pharmacist)
        self.assertEqual([m["text"] for m in self.client.get("/api/chat/").data], ["Салом", "Ассалому алейкум"])
        self.assertEqual([m["text"] for m in self.client.get("/api/chat/", {"after": first}).data], ["Ассалому алейкум"])

        self.client.force_authenticate(self.stranger)
        self.assertEqual(self.client.get("/api/chat/").data, [])

    def test_admin_chooses_pharmacy(self):
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.client.get("/api/chat/").status_code, 400)
        self.assertEqual(self.say(self.admin, "Привет").status_code, 400)
        self.assertEqual(self.say(self.admin, "Привет", pharmacy=self.mine.id).status_code, 201)
        self.client.force_authenticate(self.director)
        self.assertEqual([m["role"] for m in self.client.get("/api/chat/").data], ["admin"])



class ChatAttachmentTest(APITestCase):
    """Фото и видео в чате: тип — по расширению (svg/html под видом картинки не пройдут), лимит размера."""

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        pharmacy = Pharmacy.objects.create(name="Моя")
        self.client.force_authenticate(User.objects.create_user("d", password="x", pharmacy=pharmacy, role=User.Role.DIRECTOR))

    def send(self, name, content_type, text="", size=10):
        upload = SimpleUploadedFile(name, b"x" * size, content_type=content_type)
        return self.client.post("/api/chat/", {"text": text, "file": upload}, format="multipart")

    def test_photo_and_video(self):
        r = self.send("Фото.JPG", "image/jpeg")
        self.assertEqual(r.status_code, 201, r.data)
        self.assertEqual((r.data["kind"], r.data["text"]), ("image", ""))
        self.assertRegex(r.data["file_url"], r"^/media/chat/\d+/[0-9a-f]{32}\.jpg$")
        self.assertNotIn("file", r.data)
        r = self.send("clip.mp4", "video/mp4", text="Смотрите")
        self.assertEqual((r.status_code, r.data["kind"], r.data["text"]), (201, "video", "Смотрите"))
        self.assertEqual(self.client.get("/api/chat/").data[0]["file_url"].startswith("/media/chat/"), True)

    def test_voice(self):
        upload = SimpleUploadedFile("voice.webm", b"x" * 10, content_type="audio/webm")
        r = self.client.post("/api/chat/", {"file": upload, "voice": "true", "duration": 7}, format="multipart")
        self.assertEqual((r.status_code, r.data["kind"], r.data["duration"]), (201, "voice", 7))
        # без voice тот же webm — это видео; длительность у видео не хранится
        upload = SimpleUploadedFile("clip.webm", b"x" * 10, content_type="video/webm")
        r = self.client.post("/api/chat/", {"file": upload, "duration": 7}, format="multipart")
        self.assertEqual((r.data["kind"], r.data["duration"]), ("video", None))
        upload = SimpleUploadedFile("voice.html", b"x", content_type="audio/webm")
        self.assertEqual(self.client.post("/api/chat/", {"file": upload, "voice": "true"}, format="multipart").status_code, 400)

    def test_rejected(self):
        self.assertEqual(self.send("evil.svg", "image/svg+xml").status_code, 400)
        self.assertEqual(self.send("evil.html", "image/png").status_code, 400)  # content_type от браузера не верим
        self.assertEqual(self.send("doc.pdf", "application/pdf").status_code, 400)
        with self.settings(DATA_UPLOAD_MAX_MEMORY_SIZE=None):
            from chat import serializers

            old, serializers.MAX_FILE_MB = serializers.MAX_FILE_MB, 0
            try:
                self.assertEqual(self.send("big.png", "image/png").status_code, 400)
            finally:
                serializers.MAX_FILE_MB = old


class ChatWebSocketTest(TransactionTestCase):
    """Живой чат: новое сообщение приходит по WebSocket только в чат своей аптеки; без токена не пускает."""

    def setUp(self):
        self.mine, self.other = Pharmacy.objects.create(name="Моя"), Pharmacy.objects.create(name="Чужая")
        self.director = User.objects.create_user("d", password="x", pharmacy=self.mine, role=User.Role.DIRECTOR)
        self.pharmacist = User.objects.create_user("p", password="x", pharmacy=self.mine, role=User.Role.PHARMACIST)
        self.stranger = User.objects.create_user("s", password="x", pharmacy=self.other, role=User.Role.PHARMACIST)
        self.admin = User.objects.create_superuser("a", password="x")

    def socket(self, user=None, query=""):
        token = f"token={AccessToken.for_user(user)}" if user else "token=bad"
        # Origin нужен AllowedHostsOriginValidator
        return WebsocketCommunicator(application, f"/ws/chat/?{token}{query}", headers=[(b"origin", b"http://testserver")])

    def test_live_messages(self):
        async def scenario():
            colleague, stranger, admin = self.socket(self.pharmacist), self.socket(self.stranger), self.socket(self.admin, f"&pharmacy={self.mine.id}")
            for ws in (colleague, stranger, admin):
                self.assertTrue((await ws.connect())[0])
            await database_post(self.director, "Салом")
            self.assertEqual((await colleague.receive_json_from())["message"]["text"], "Салом")
            self.assertEqual((await admin.receive_json_from())["message"]["text"], "Салом")
            # фармацевт прочитал — теперь прочитали все сотрудники: всем уходит «read»
            await database_read(self.pharmacist, (await self.last_id()))
            self.assertEqual((await colleague.receive_json_from())["event"], "read")
            self.assertEqual((await admin.receive_json_from())["read_up_to"], await self.last_id())
            self.assertTrue(await stranger.receive_nothing())
            for ws in (colleague, stranger, admin):
                await ws.disconnect()

            for ws in (self.socket(), self.socket(self.admin)):  # плохой токен; админ без аптеки
                connected, code = await ws.connect()
                self.assertEqual((connected, code), (False, 4401))

        async_to_sync(scenario)()

    async def last_id(self):
        from asgiref.sync import sync_to_async

        from chat.models import Message

        return await sync_to_async(lambda: Message.objects.latest("id").id)()


async def database_post(user, text):
    from asgiref.sync import sync_to_async
    from rest_framework.test import APIClient

    def post():
        client = APIClient()
        client.force_authenticate(user)
        assert client.post("/api/chat/", {"text": text}).status_code == 201

    await sync_to_async(post)()


async def database_read(user, last_id):
    from asgiref.sync import sync_to_async
    from rest_framework.test import APIClient

    def post():
        client = APIClient()
        client.force_authenticate(user)
        assert client.post("/api/chat/read/", {"last_id": last_id}).status_code == 200

    await sync_to_async(post)()


class ChatReadTest(APITestCase):
    """Синие галочки: сообщение прочитано, когда его прочитали все активные сотрудники аптеки (кроме тех, кто пришёл позже)."""

    def setUp(self):
        self.pharmacy = Pharmacy.objects.create(name="Моя")
        self.director = User.objects.create_user("d", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.p1 = User.objects.create_user("p1", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.p2 = User.objects.create_user("p2", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.admin = User.objects.create_superuser("a", password="x")

    def read(self, user, last_id, **extra):
        self.client.force_authenticate(user)
        r = self.client.post("/api/chat/read/", {"last_id": last_id, **extra})
        self.assertEqual(r.status_code, 200, r.data)
        return r.data["read_up_to"]

    def test_all_must_read(self):
        self.client.force_authenticate(self.director)
        first = self.client.post("/api/chat/", {"text": "Салом"}).data["id"]
        self.assertEqual(self.read(self.director, first), 0)  # автор прочитал, остальные — нет
        self.assertEqual(self.read(self.p1, first), 0)  # p2 ещё не видел
        self.assertEqual(self.read(self.admin, first, pharmacy=self.pharmacy.id), 0)  # админ не участник
        self.assertEqual(self.read(self.p2, first + 100), first)  # больше последнего id не записывается

        # новый сотрудник не делает старые сообщения снова непрочитанными; уволенный — не ждём
        User.objects.create_user("new", password="x", pharmacy=self.pharmacy)
        self.assertEqual(self.read(self.p1, first), first)
        self.client.force_authenticate(self.p1)
        second = self.client.post("/api/chat/", {"text": "Ок"}).data["id"]
        self.assertEqual(self.read(self.director, second), first)
        self.p2.is_active = False
        self.p2.save()
        new = User.objects.get(username="new")
        self.assertEqual(self.read(new, second), second)


class ChatActionsTest(APITestCase):
    """Ответ, изменение, удаление, реакции, закрепление — и кто что может."""

    def setUp(self):
        self.pharmacy, other = Pharmacy.objects.create(name="Моя"), Pharmacy.objects.create(name="Чужая")
        self.director = User.objects.create_user("d", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.p1 = User.objects.create_user("p1", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.p2 = User.objects.create_user("p2", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.stranger = User.objects.create_user("s", password="x", pharmacy=other, role=User.Role.PHARMACIST)

    def as_(self, user):
        self.client.force_authenticate(user)
        return self.client

    def say(self, user, text, **extra):
        r = self.as_(user).post("/api/chat/", {"text": text, **extra})
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def test_reply(self):
        first = self.say(self.p1, "Есть парацетамол?")
        answer = self.say(self.p2, "Да", reply_to=first["id"])
        self.assertEqual((answer["reply"]["id"], answer["reply"]["text"]), (first["id"], "Есть парацетамол?"))
        foreign = self.say(self.stranger, "чужое")
        self.assertEqual(self.as_(self.p1).post("/api/chat/", {"text": "x", "reply_to": foreign["id"]}).status_code, 400)

    def test_edit_only_own(self):
        m = self.say(self.p1, "Привет")
        self.assertEqual(self.as_(self.p2).patch(f"/api/chat/{m['id']}/", {"text": "Взлом"}).status_code, 403)
        self.assertEqual(self.as_(self.director).patch(f"/api/chat/{m['id']}/", {"text": "Взлом"}).status_code, 403)
        r = self.as_(self.p1).patch(f"/api/chat/{m['id']}/", {"text": " Привет всем "})
        self.assertEqual((r.status_code, r.data["text"], r.data["edited"]), (200, "Привет всем", True))
        self.assertEqual(self.as_(self.p1).patch(f"/api/chat/{m['id']}/", {"text": ""}).status_code, 400)
        self.assertEqual(self.as_(self.stranger).patch(f"/api/chat/{m['id']}/", {"text": "x"}).status_code, 404)

    def test_delete(self):
        m = self.say(self.p1, "Удали меня")
        reply = self.say(self.p2, "Ответ", reply_to=m["id"])
        self.assertEqual(self.as_(self.p2).delete(f"/api/chat/{m['id']}/").status_code, 403)  # чужое — нельзя
        r = self.as_(self.p1).delete(f"/api/chat/{m['id']}/")
        self.assertEqual((r.status_code, r.data["deleted"], r.data["text"]), (200, True, ""))
        quoted = next(x for x in self.as_(self.p2).get("/api/chat/").data if x["id"] == reply["id"])
        self.assertTrue(quoted["reply"]["deleted"])
        self.assertEqual(self.as_(self.p1).post("/api/chat/", {"text": "x", "reply_to": m["id"]}).status_code, 400)
        # директор удаляет любое
        other = self.say(self.p2, "Спам")
        self.assertEqual(self.as_(self.director).delete(f"/api/chat/{other['id']}/").status_code, 200)

    def test_reactions(self):
        m = self.say(self.p1, "Готово")
        url = f"/api/chat/{m['id']}/react/"
        self.as_(self.p1).post(url, {"emoji": "👍"})
        r = self.as_(self.p2).post(url, {"emoji": "👍"})
        self.assertEqual(r.data["reactions"], [{"emoji": "👍", "users": [self.p1.id, self.p2.id]}])
        r = self.as_(self.p2).post(url, {"emoji": "❤️"})  # другой — заменяет
        self.assertEqual(r.data["reactions"], [{"emoji": "👍", "users": [self.p1.id]}, {"emoji": "❤️", "users": [self.p2.id]}])
        r = self.as_(self.p1).post(url, {"emoji": "👍"})  # тот же — снимает
        self.assertEqual(r.data["reactions"], [{"emoji": "❤️", "users": [self.p2.id]}])
        self.assertEqual(self.as_(self.p1).post(url, {"emoji": "💩"}).status_code, 400)

    def test_pin(self):
        a, b = self.say(self.p1, "Важно"), self.say(self.p1, "Ещё важнее")
        self.assertEqual(self.as_(self.p1).post(f"/api/chat/{a['id']}/pin/").status_code, 403)
        self.assertTrue(self.as_(self.director).post(f"/api/chat/{a['id']}/pin/").data["pinned"])
        self.as_(self.director).post(f"/api/chat/{b['id']}/pin/")  # закреплённое одно
        self.assertEqual(self.as_(self.p1).get("/api/chat/pinned/").data["id"], b["id"])
        self.assertFalse(next(x for x in self.client.get("/api/chat/").data if x["id"] == a["id"])["pinned"])
        self.as_(self.director).post(f"/api/chat/{b['id']}/pin/")  # повторно — открепить
        self.assertIsNone(self.as_(self.p1).get("/api/chat/pinned/").data)



class ChatAdminTest(APITestCase):
    """Управление чатом: участники, запрет писать, очистка истории, обзор всех чатов у админа."""

    def setUp(self):
        self.pharmacy, self.other = Pharmacy.objects.create(name="Моя"), Pharmacy.objects.create(name="Пустая")
        self.director = User.objects.create_user("d", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR, full_name="Али")
        self.p1 = User.objects.create_user("p1", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.admin = User.objects.create_superuser("a", password="x")

    def as_(self, user):
        user.refresh_from_db()  # как в настоящем запросе: пользователь читается из базы (запрет мог измениться)
        self.client.force_authenticate(user)
        return self.client

    def test_mute(self):
        url = f"/api/chat/members/{self.p1.id}/mute/"
        self.assertEqual(self.as_(self.p1).post(f"/api/chat/members/{self.director.id}/mute/", {"muted": True}).status_code, 403)
        r = self.as_(self.director).post(url, {"muted": True})
        self.assertEqual((r.status_code, r.data["chat_muted"]), (200, True))
        self.assertEqual(self.as_(self.p1).post("/api/chat/", {"text": "x"}).status_code, 403)
        self.assertEqual(self.as_(self.p1).get("/api/chat/").status_code, 200)  # читать можно
        self.assertEqual(self.as_(self.director).post(f"/api/chat/members/{self.director.id}/mute/", {"muted": True}).status_code, 400)
        # директора — только админ
        self.assertEqual(self.as_(self.admin).post(f"/api/chat/members/{self.director.id}/mute/?pharmacy={self.pharmacy.id}", {"muted": True}).status_code, 200)
        self.as_(self.admin).post(f"/api/chat/members/{self.director.id}/mute/?pharmacy={self.pharmacy.id}", {"muted": False})
        self.as_(self.director).post(url, {"muted": False})
        self.assertEqual(self.as_(self.p1).post("/api/chat/", {"text": "снова могу"}).status_code, 201)
        members = self.as_(self.p1).get("/api/chat/members/").data
        self.assertEqual({m["username"] for m in members}, {"d", "p1"})
        self.assertEqual(next(m for m in members if m["username"] == "d")["name"], "Али")

    def test_clear_and_overview(self):
        self.as_(self.p1).post("/api/chat/", {"text": "Салом"})
        self.as_(self.director).post("/api/chat/", {"text": "Последнее"})
        self.assertEqual(self.as_(self.director).post("/api/chat/clear/").status_code, 403)  # только админ
        self.assertEqual(self.as_(self.director).get("/api/chat/overview/").status_code, 403)

        rows = self.as_(self.admin).get("/api/chat/overview/").data
        self.assertEqual([r["name"] for r in rows], ["Моя", "Пустая"])  # с сообщениями — выше
        self.assertEqual((rows[0]["messages"], rows[0]["members"], rows[0]["last"]["text"], rows[0]["last"]["author"]), (2, 2, "Последнее", "Али"))
        self.assertIsNone(rows[1]["last"])

        self.assertEqual(self.client.post(f"/api/chat/clear/?pharmacy={self.pharmacy.id}").status_code, 204)
        self.assertEqual(self.client.get("/api/chat/", {"pharmacy": self.pharmacy.id}).data, [])



class ChatMediaTest(APITestCase):
    """Сведения о чате: счётчики и списки фото / видео / голосовых / ссылок; фото чата ставят директор и админ."""

    def setUp(self):
        self.media = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.media, ignore_errors=True)
        override = override_settings(MEDIA_ROOT=self.media)
        override.enable()
        self.addCleanup(override.disable)
        self.pharmacy = Pharmacy.objects.create(name="Моя")
        self.director = User.objects.create_user("d", password="x", pharmacy=self.pharmacy, role=User.Role.DIRECTOR)
        self.p1 = User.objects.create_user("p1", password="x", pharmacy=self.pharmacy, role=User.Role.PHARMACIST)
        self.client.force_authenticate(self.p1)

    def upload(self, name, **extra):
        f = SimpleUploadedFile(name, b"x" * 10)
        r = self.client.post("/api/chat/", {"file": f, **extra}, format="multipart")
        self.assertEqual(r.status_code, 201, r.data)
        return r.data

    def test_counts_and_lists(self):
        photo = self.upload("a.jpg")
        self.upload("b.png")
        video = self.upload("c.mp4")
        self.upload("v.webm", voice="true", duration=4)
        self.client.post("/api/chat/", {"text": "Смотри https://example.com"})
        self.client.post("/api/chat/", {"text": "без ссылки"})
        self.client.delete(f"/api/chat/{self.upload('gone.jpg')['id']}/")  # удалённое не считается

        info = self.client.get("/api/chat/info/").data
        self.assertEqual(info["counts"], {"image": 2, "video": 1, "voice": 1, "link": 1})
        self.assertEqual((info["name"], info["members"], info["avatar_url"]), ("Моя", 2, None))

        media = self.client.get("/api/chat/media/").data  # по умолчанию — фото и видео, новые первыми
        self.assertEqual([m["id"] for m in media], [video["id"], photo["id"] + 1, photo["id"]])
        self.assertEqual([m["kind"] for m in self.client.get("/api/chat/media/", {"kind": "image"}).data], ["image", "image"])
        self.assertEqual(self.client.get("/api/chat/media/", {"kind": "voice"}).data[0]["duration"], 4)
        self.assertIn("https://example.com", self.client.get("/api/chat/media/", {"kind": "link"}).data[0]["text"])
        self.assertEqual(self.client.get("/api/chat/media/", {"kind": "pdf"}).status_code, 400)

    def test_chat_avatar(self):
        from io import BytesIO

        from PIL import Image

        out = BytesIO()
        Image.new("RGB", (300, 500), (0, 120, 80)).save(out, "PNG")
        photo = lambda: SimpleUploadedFile("chat.png", out.getvalue())  # noqa: E731
        self.assertEqual(self.client.post("/api/chat/avatar/", {"file": photo()}, format="multipart").status_code, 403)
        self.client.force_authenticate(self.director)
        r = self.client.post("/api/chat/avatar/", {"file": photo()}, format="multipart")
        self.assertEqual(r.status_code, 200, r.data)
        self.assertRegex(r.data["avatar_url"], r"^/media/chat-avatars/[0-9a-f]{32}\.jpg$")
        admin = User.objects.create_superuser("a", password="x")
        self.client.force_authenticate(admin)
        self.assertEqual(self.client.get("/api/chat/overview/").data[0]["avatar_url"], r.data["avatar_url"])
        self.assertIsNone(self.client.delete(f"/api/chat/avatar/?pharmacy={self.pharmacy.id}").data["avatar_url"])
