// Отдельный сервер фронтенда: отдаёт frontend/ и пересылает остальное на бэкенд (Django).
// Фронтенд обращается к тому же адресу (/api, /media, WebSocket /ws) — поэтому CORS не нужен.
//
//   node frontend_server.js                         → http://127.0.0.1:3000, бэкенд http://127.0.0.1:8000
//   PORT=5000 BACKEND=http://10.0.0.5:8000 node frontend_server.js
//
// Без зависимостей: только встроенные модули Node.js (18+).

const http = require("node:http");
const net = require("node:net");
const fs = require("node:fs");
const path = require("node:path");

const PORT = Number(process.env.PORT) || 3000;
const HOST = process.env.HOST || "127.0.0.1";
const BACKEND = new URL(process.env.BACKEND || "http://127.0.0.1:8000");
const ROOT = path.join(__dirname, "frontend");
const STATIC = path.join(ROOT, "static");
const TEMPLATE = path.join(ROOT, "templates", "index.html");

const TYPES = {
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json",
  ".svg": "image/svg+xml",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".gif": "image/gif",
  ".webp": "image/webp",
  ".ico": "image/x-icon",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".ttf": "font/ttf",
};

// ?v= у css/js — время последней правки (как FrontendView в config/urls.py), чтобы браузер не брал старое из кэша
function version(dir = STATIC) {
  let latest = 0;
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    latest = Math.max(latest, entry.isDirectory() ? version(full) : fs.statSync(full).mtimeMs);
  }
  return Math.floor(latest / 1000);
}

// index.html — шаблон Django: {% static '…' %} → /static/…, {{ v }} → версия файлов
function renderIndex() {
  return fs
    .readFileSync(TEMPLATE, "utf8")
    .replace(/{%\s*load static\s*%}/g, "")
    .replace(/{%\s*static\s+['"]([^'"]+)['"]\s*%}/g, (_, file) => `/static/${file}`)
    .replace(/{{\s*v\s*}}/g, String(version()));
}

// файл из frontend/static или null (в том числе при попытке выйти за папку через ../)
function staticFile(urlPath) {
  const relative = decodeURIComponent(urlPath.slice("/static/".length));
  const file = path.join(STATIC, relative);
  if (!file.startsWith(STATIC + path.sep)) return null;
  try {
    return fs.statSync(file).isFile() ? file : null;
  } catch {
    return null;
  }
}

function backendDown(res, err) {
  console.error(`[frontend] бэкенд ${BACKEND.origin} недоступен: ${err.message}`);
  if (res.headersSent) return res.destroy();
  res.writeHead(502, { "Content-Type": "application/json; charset=utf-8" });
  res.end(JSON.stringify({ detail: `Бэкенд недоступен (${BACKEND.origin}). Запустите: python manage.py runserver` }));
}

// всё, что не фронтенд (/api, /media, /admin, …), — как есть на бэкенд; Host сохраняем
function proxy(req, res) {
  const upstream = http.request(
    {
      host: BACKEND.hostname,
      port: BACKEND.port || 80,
      method: req.method,
      path: req.url,
      headers: { ...req.headers, "x-forwarded-for": req.socket.remoteAddress, "x-forwarded-host": req.headers.host },
    },
    (answer) => {
      res.writeHead(answer.statusCode, answer.headers);
      answer.pipe(res);
    }
  );
  upstream.on("error", (err) => backendDown(res, err));
  req.pipe(upstream);
}

const server = http.createServer((req, res) => {
  // «../» в адресе — попытка выйти за папку: не отдаём и не пересылаем
  if (decodeURIComponent(req.url.split("?")[0]).split(/[\\/]/).includes("..")) {
    res.writeHead(400, { "Content-Type": "text/plain; charset=utf-8" });
    return res.end("Bad request");
  }
  const { pathname } = new URL(req.url, "http://localhost");

  if (req.method === "GET" && (pathname === "/" || pathname === "/index.html")) {
    res.writeHead(200, { "Content-Type": TYPES[".html"], "Cache-Control": "no-cache" });
    return res.end(renderIndex());
  }
  if (req.method === "GET" && pathname.startsWith("/static/")) {
    const file = staticFile(pathname);
    // нет во frontend/static — может быть статикой бэкенда (админка Django, Swagger)
    if (file) {
      res.writeHead(200, { "Content-Type": TYPES[path.extname(file).toLowerCase()] || "application/octet-stream", "Cache-Control": "no-cache" });
      return fs.createReadStream(file).pipe(res);
    }
  }
  proxy(req, res);
});

// WebSocket (живой чат /ws/chat/): поднимаем TCP до бэкенда, повторяем рукопожатие и соединяем потоки
server.on("upgrade", (req, socket, head) => {
  const upstream = net.connect(BACKEND.port || 80, BACKEND.hostname, () => {
    const headers = Object.entries(req.headers).map(([k, v]) => `${k}: ${v}`);
    upstream.write([`${req.method} ${req.url} HTTP/${req.httpVersion}`, ...headers, "", ""].join("\r\n"));
    if (head?.length) upstream.write(head);
    upstream.pipe(socket);
    socket.pipe(upstream);
  });
  const close = () => {
    upstream.destroy();
    socket.destroy();
  };
  upstream.on("error", (err) => {
    console.error(`[frontend] WebSocket: бэкенд недоступен: ${err.message}`);
    close();
  });
  socket.on("error", close);
});

server.listen(PORT, HOST, () => {
  console.log(`Фронтенд: http://${HOST}:${PORT}/`);
  console.log(`Бэкенд (API, медиа, WebSocket): ${BACKEND.origin}`);
});
