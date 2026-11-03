// Панель управления сетью аптек. Разделы и кнопки показываются по правам пользователя (RBAC).

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

const esc = (value) =>
  String(value ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const money = (value) =>
  Number(value || 0).toLocaleString(i18n.locale, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
const dateTime = (value) => (value ? new Date(value).toLocaleString(i18n.dateLocale, { dateStyle: "short", timeStyle: "short" }) : "");
const dateOnly = (value) => (value ? new Date(value).toLocaleDateString(i18n.dateLocale) : "");
const today = () => new Date().toISOString().slice(0, 10);

const ROLES = ["admin", "director", "pharmacist"];
const roleLabel = (role) => (ROLES.includes(role) ? t(`role.${role}`) : role);
const roleOptions = () => ROLES.map((r) => [r, roleLabel(r)]);

let me = null;
let pharmacyNames = {}; // id -> название (нужно админу, он видит все аптеки)

const can = (perm) => !!me && me.permissions.includes(perm);
const isAdmin = () => can("pharmacy.all");

// ---------------------------------------------------------------- Язык и тема

function setupLangSwitches() {
  $$(".lang-switch").forEach((box) => {
    box.innerHTML = Object.entries(LANGS)
      .map(([code, label]) => `<button type="button" data-lang="${code}">${esc(label)}</button>`)
      .join("");
    box.addEventListener("click", (e) => e.target.dataset.lang && changeLang(e.target.dataset.lang));
  });
}

function changeLang(code) {
  i18n.set(code);
  applyStaticI18n();
  if (me) {
    closeModal();
    renderShell();
    route();
  }
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  try { localStorage.setItem("theme", theme); } catch {}
  $$(".theme-icon").forEach((el) => (el.textContent = theme === "dark" ? "dark_mode" : "light_mode"));
}

// Иконки Material Symbols (имя иконки — лигатура)
const icon = (name) => `<span class="ms" aria-hidden="true">${name}</span>`;

const reducedMotion = () => window.matchMedia("(prefers-reduced-motion: reduce)").matches;

// Перезапуск CSS-анимации: снять класс, заставить пересчитать стили, вернуть
function replay(el, cls) {
  el.classList.remove(cls);
  void el.offsetWidth;
  el.classList.add(cls);
}

// Смена темы с круговым раскрытием от кнопки; без View Transitions — плавный переход цветов
function toggleTheme(button) {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  const apply = () => {
    setTheme(next);
    $$(".theme-icon").forEach((el) => replay(el, "spin"));
  };

  if (reducedMotion()) return apply();
  if (!document.startViewTransition) {
    document.documentElement.classList.add("theme-anim");
    apply();
    setTimeout(() => document.documentElement.classList.remove("theme-anim"), 450);
    return;
  }

  const rect = button.getBoundingClientRect();
  const x = rect.left + rect.width / 2;
  const y = rect.top + rect.height / 2;
  const radius = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  document.startViewTransition(apply).ready.then(() => {
    document.documentElement.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${radius}px at ${x}px ${y}px)`] },
      { duration: 550, easing: "cubic-bezier(.4, 0, .2, 1)", pseudoElement: "::view-transition-new(root)" }
    );
  });
}

$$(".theme-toggle").forEach((el) => el.addEventListener("click", () => toggleTheme(el)));

// ---------------------------------------------------------------- UI-помощники

function toast(text, isError = false) {
  const el = $("#toast");
  el.textContent = text;
  el.className = `toast${isError ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => el.classList.add("hidden"), 3500);
}

function openModal(title, html) {
  $("#modal-title").textContent = title;
  $("#modal-body").innerHTML = html;
  $("#modal").classList.remove("hidden");
  return $("#modal-body");
}

function closeModal() {
  $("#modal").classList.add("hidden");
  $("#modal-body").innerHTML = "";
}

function confirmAction(text) {
  return window.confirm(text);
}

function badge(text, kind = "") {
  return `<span class="badge ${kind}">${esc(text)}</span>`;
}

function stockBadge(quantity) {
  if (quantity === 0) return badge(t("outOfStock"), "bad");
  if (quantity < 10) return badge(quantity, "warn");
  return badge(quantity, "ok");
}

function options(list, selected, { empty } = {}) {
  const head = empty !== undefined ? `<option value="">${esc(empty)}</option>` : "";
  return (
    head +
    list
      .map(([value, label]) => `<option value="${esc(value)}"${String(value) === String(selected ?? "") ? " selected" : ""}>${esc(label)}</option>`)
      .join("")
  );
}

// ---------------------------------------------------------------- Форма в модалке

/**
 * fields: [{ name, label, type: text|password|number|date|select|checkbox, options, required, step, hint }]
 * submit(data) -> Promise; ошибки API показываются внутри формы.
 */
function formModal({ title, fields, initial = {}, submit, submitText = t("save") }) {
  const html = `
    <form class="form">
      ${fields.map((f) => fieldHtml(f, initial[f.name])).join("")}
      <div class="error"></div>
      <div class="form-actions">
        <button type="button" class="btn" data-close>${esc(t("cancel"))}</button>
        <button type="submit" class="btn primary">${esc(submitText)}</button>
      </div>
    </form>`;
  const body = openModal(title, html);
  const form = $("form", body);

  // сканер штрих-кода в конце нажимает Enter — в поле штрих-кода это не отправка формы, а переход дальше
  form.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" || !e.target.matches("[data-scan]")) return;
    e.preventDefault();
    const inputs = $$("input, select", form);
    inputs[inputs.indexOf(e.target) + 1]?.focus();
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const data = {};
    fields.forEach((f) => {
      const input = form.elements[f.name];
      if (f.type === "checkbox") data[f.name] = input.checked;
      else data[f.name] = input.value.trim();
    });
    const button = $("button[type=submit]", form);
    button.disabled = true;
    try {
      await submit(data);
      closeModal();
    } catch (err) {
      $(".error", form).textContent = err.message;
    } finally {
      button.disabled = false;
    }
  });
}

function fieldHtml(f, value) {
  value = value ?? f.default ?? "";
  const req = f.required ? " required" : "";
  const hint = f.hint ? `<span class="muted small">${esc(f.hint)}</span>` : "";
  if (f.type === "checkbox") {
    return `<label class="check"><input type="checkbox" name="${f.name}"${value ? " checked" : ""}> ${esc(f.label)}</label>`;
  }
  if (f.type === "select") {
    return `<label>${esc(f.label)}<select name="${f.name}"${req}>${options(f.options, value, { empty: f.empty })}</select>${hint}</label>`;
  }
  const step = f.step ? ` step="${f.step}"` : "";
  const min = f.min !== undefined ? ` min="${f.min}"` : "";
  const max = f.max !== undefined ? ` max="${f.max}"` : "";
  const scan = f.scan ? ' data-scan autocomplete="off"' : "";
  return `<label>${esc(f.label)}<input name="${f.name}" type="${f.type || "text"}" value="${esc(value)}"${req}${step}${min}${max}${scan}>${hint}</label>`;
}

// ---------------------------------------------------------------- Список с фильтрами и пагинацией

/**
 * columns: [{ label, html: row => string (уже экранированный), cls }]
 * filters: [{ name, type: search|select, placeholder, options }]
 * actions: row => html кнопок; кнопки с data-act обрабатываются в onAction(act, row)
 */
function listView({ endpoint, columns, filters = [], baseParams = {}, actions, onAction, onRowClick }) {
  const view = $("#view");
  const state = { page: 1, params: {}, rows: [] };

  view.innerHTML = `
    <div class="toolbar">
      ${filters
        .map((f) =>
          f.type === "months"
            ? monthPickerHtml()
            : f.type === "select"
            ? `<select data-filter="${f.name}">${options(f.options, "", { empty: f.placeholder })}</select>`
            : `<input type="search" data-filter="${f.name}" placeholder="${esc(f.placeholder || t("search"))}">`
        )
        .join("")}
    </div>
    <div class="card table-wrap" style="padding:0">
      <table>
        <thead><tr>${columns.map((c) => `<th class="${c.cls || ""}">${esc(c.label)}</th>`).join("")}${actions ? "<th></th>" : ""}</tr></thead>
        <tbody><tr><td class="empty" colspan="99">${esc(t("loading"))}</td></tr></tbody>
      </table>
    </div>
    <div class="pager"></div>`;

  $$("[data-month-picker]", view).forEach(setupMonthPicker);

  let debounce;
  $$("[data-filter]", view).forEach((el) => {
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
      clearTimeout(debounce);
      debounce = setTimeout(() => {
        // читаем все фильтры сразу: выбор периода меняет два поля (month_from и month_to) одновременно
        $$("[data-filter]", view).forEach((f) => (state.params[f.dataset.filter] = f.value));
        state.page = 1;
        load();
      }, 250);
    });
  });

  const tbody = $("tbody", view);

  tbody.addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-i]");
    if (!tr) return;
    const row = state.rows[tr.dataset.i];
    const btn = e.target.closest("[data-act]");
    if (btn) onAction?.(btn.dataset.act, row);
    else onRowClick?.(row);
  });

  async function load() {
    try {
      const base = typeof baseParams === "function" ? baseParams(state.params) : baseParams;
      const data = await api.get(endpoint, { ...base, ...state.params, page: state.page });
      state.rows = data.results;
      tbody.innerHTML = data.results.length
        ? data.results
            .map(
              (row, i) => `<tr data-i="${i}"${onRowClick ? ' class="clickable"' : ""}>
                ${columns.map((c) => `<td class="${c.cls || ""}">${c.html(row)}</td>`).join("")}
                ${actions ? `<td class="actions">${actions(row)}</td>` : ""}
              </tr>`
            )
            .join("")
        : `<tr><td class="empty" colspan="99">${esc(t("nothingFound"))}</td></tr>`;

      $(".pager", view).innerHTML = pagerHtml(state.page, data.count);
    } catch (err) {
      // удалили последнюю запись на последней странице — такой страницы больше нет, идём на предыдущую
      if (err.status === 404 && state.page > 1) {
        state.page -= 1;
        return load();
      }
      tbody.innerHTML = `<tr><td class="empty error" colspan="99">${esc(err.message)}</td></tr>`;
    }
  }

  $(".pager", view).addEventListener("click", (e) => {
    const button = e.target.closest("[data-page]");
    if (!button || button.disabled) return;
    state.page = Number(button.dataset.page);
    load();
    view.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "start" });
  });

  load();
  return { reload: load };
}

// ---------------------------------------------------------------- Пагинация (по PAGE_SIZE на страницу)

const PAGE_SIZE = 20; // как PAGE_SIZE в настройках DRF

// Номера страниц: первая, последняя и соседи текущей; пропуски — «…» (или сама страница, если пропущена одна)
function pageNumbers(page, pages) {
  const nums = [...new Set([1, page - 1, page, page + 1, pages])].filter((p) => p >= 1 && p <= pages).sort((a, b) => a - b);
  const out = [];
  nums.forEach((p, i) => {
    const gap = i ? p - nums[i - 1] : 0;
    if (gap === 2) out.push(p - 1);
    else if (gap > 2) out.push("…");
    out.push(p);
  });
  return out;
}

function pagerHtml(page, count) {
  const pages = Math.max(1, Math.ceil(count / PAGE_SIZE));
  const from = count ? (page - 1) * PAGE_SIZE + 1 : 0;
  const to = Math.min(page * PAGE_SIZE, count);
  const nav =
    pages > 1
      ? `<nav class="pages">
          <button type="button" class="page-btn" data-page="${page - 1}" title="${esc(t("pager.prev"))}"${page <= 1 ? " disabled" : ""}>${icon("chevron_left")}</button>
          ${pageNumbers(page, pages)
            .map((p) =>
              p === "…"
                ? `<span class="page-gap">…</span>`
                : `<button type="button" class="page-btn${p === page ? " active" : ""}" data-page="${p}"${p === page ? ' aria-current="page"' : ""}>${p}</button>`
            )
            .join("")}
          <button type="button" class="page-btn" data-page="${page + 1}" title="${esc(t("pager.next"))}"${page >= pages ? " disabled" : ""}>${icon("chevron_right")}</button>
        </nav>`
      : "";
  return `<span class="muted">${esc(t("pager.shown", { from, to, count }))}</span>${nav}`;
}

// Таблица с пагинацией на клиенте — для списков, которые приходят целиком (например, в статистике)
function mountPagedTable(box, head, rows) {
  let page = 1;
  const render = () => {
    const slice = rows.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);
    box.innerHTML = simpleTable(head, slice) + (rows.length > PAGE_SIZE ? `<div class="pager">${pagerHtml(page, rows.length)}</div>` : "");
  };
  box.addEventListener("click", (e) => {
    const button = e.target.closest("[data-page]");
    if (!button || button.disabled) return;
    page = Number(button.dataset.page);
    render();
  });
  render();
}

// ---------------------------------------------------------------- Фильтр по периоду месяцев (?month_from=…&month_to=…)

const monthFilter = () => ({ type: "months" });

const ymOf = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const shiftMonth = (ym, n) => {
  const [y, m] = ym.split("-").map(Number);
  return ymOf(new Date(y, m - 1 + n, 1));
};
// «авг 2026»
const shortMonth = (ym) => {
  const [y, m] = ym.split("-").map(Number);
  return `${new Date(y, m - 1, 1).toLocaleDateString(i18n.dateLocale, { month: "short" }).replace(".", "")} ${y}`;
};

// «Октябрь 2026 г.» или «авг 2026 — окт 2026»; пустая строка — период не задан
function periodLabel(from, to) {
  if (!from) return "";
  if (from === to) return monthName(`${from}-01`);
  return `${shortMonth(from)} — ${shortMonth(to)}`;
}

// [‹] [📅 Авг 2026 — Окт 2026 ▾] [›] [×]; значения — в скрытых полях month_from / month_to, listView слушает их «input»
function monthPickerHtml() {
  const presets = [
    ["this", t("filter.thisMonth")],
    ["3", t("filter.last3")],
    ["6", t("filter.last6")],
    ["year", t("filter.thisYear")],
  ];
  return `
    <div class="month-picker" data-month-picker>
      <input type="hidden" data-filter="month_from" value="">
      <input type="hidden" data-filter="month_to" value="">
      <button type="button" class="mp-step" data-step="-1" title="${esc(t("filter.prevMonth"))}">${icon("chevron_left")}</button>
      <button type="button" class="mp-toggle" aria-haspopup="true">${icon("date_range")}<span class="mp-label"></span>${icon("expand_more")}</button>
      <button type="button" class="mp-step" data-step="1" title="${esc(t("filter.nextMonth"))}">${icon("chevron_right")}</button>
      <button type="button" class="mp-clear hidden" title="${esc(t("filter.allMonths"))}">${icon("close")}</button>
      <div class="mp-pop hidden">
        <div class="mp-presets">${presets.map(([key, label]) => `<button type="button" data-preset="${key}">${esc(label)}</button>`).join("")}</div>
        <div class="mp-year">
          <button type="button" class="mp-step" data-year="-1">${icon("chevron_left")}</button>
          <b></b>
          <button type="button" class="mp-step" data-year="1">${icon("chevron_right")}</button>
        </div>
        <div class="mp-grid"></div>
        <p class="mp-hint"></p>
        <button type="button" class="mp-all">${esc(t("filter.allMonths"))}</button>
      </div>
    </div>`;
}

/**
 * Период выбирается двумя кликами в сетке: начало, затем конец (порядок не важен, при наведении виден будущий период).
 * Один клик и закрытие окна — один месяц. ‹ › сдвигают весь период на месяц. Будущие месяцы недоступны.
 */
function setupMonthPicker(root) {
  const fromInput = $('[data-filter="month_from"]', root);
  const toInput = $('[data-filter="month_to"]', root);
  const pop = $(".mp-pop", root);
  const current = ymOf(new Date());
  let year = new Date().getFullYear(); // год, показанный в сетке
  let anchor = null; // первый клик, ждём второй
  let hover = null;

  const apply = (from, to) => {
    if (from && to && from > to) [from, to] = [to, from];
    fromInput.value = from || "";
    toInput.value = to || "";
    fromInput.dispatchEvent(new Event("input"));
    anchor = hover = null;
    pop.classList.add("hidden");
    render();
  };
  const close = () => {
    if (pop.classList.contains("hidden")) return;
    if (anchor) return apply(anchor, anchor); // выбрали только начало — это один месяц
    pop.classList.add("hidden");
  };
  root.close = close;

  const range = () => {
    if (anchor) {
      const other = hover || anchor;
      return anchor <= other ? [anchor, other] : [other, anchor];
    }
    return [fromInput.value, toInput.value];
  };

  const render = () => {
    const from = fromInput.value;
    const to = toInput.value;
    root.classList.toggle("active", !!from);
    $(".mp-label", root).textContent = periodLabel(from, to) || t("filter.allMonths");
    $(".mp-clear", root).classList.toggle("hidden", !from);
    $('[data-step="1"]', root).disabled = !to || to >= current; // будущих месяцев нет

    $(".mp-year b", root).textContent = year;
    $('[data-year="1"]', root).disabled = year >= new Date().getFullYear();
    $(".mp-hint", root).textContent = anchor ? t("filter.pickEnd") : t("filter.pickStart");

    const [start, end] = range();
    $(".mp-grid", root).innerHTML = Array.from({ length: 12 }, (_, i) => {
      const ym = `${year}-${String(i + 1).padStart(2, "0")}`;
      const name = new Date(year, i, 1).toLocaleDateString(i18n.dateLocale, { month: "short" }).replace(".", "");
      const cls = [
        start && ym >= start && ym <= end && "in-range",
        ym === start && "range-start",
        ym === end && "range-end",
        ym === current && "today",
      ].filter(Boolean);
      return `<button type="button" class="mp-month ${cls.join(" ")}" data-ym="${ym}"${ym > current ? " disabled" : ""}>${esc(name)}</button>`;
    }).join("");
  };

  const preset = (key) => {
    if (key === "this") return apply(current, current);
    if (key === "year") return apply(`${current.slice(0, 4)}-01`, current);
    apply(shiftMonth(current, 1 - Number(key)), current); // «3 месяца» = текущий и два предыдущих
  };

  root.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn || btn.disabled) return;
    if (btn.matches(".mp-toggle")) {
      if (!pop.classList.contains("hidden")) return close();
      year = Number((toInput.value || current).slice(0, 4));
      anchor = hover = null;
      render();
      pop.classList.remove("hidden");
    } else if (btn.dataset.step) {
      const step = Number(btn.dataset.step);
      if (!fromInput.value) return apply(current, current); // из «все месяцы» — текущий месяц
      apply(shiftMonth(fromInput.value, step), shiftMonth(toInput.value, step));
    } else if (btn.dataset.year) {
      year += Number(btn.dataset.year);
      render();
    } else if (btn.dataset.ym) {
      if (!anchor) {
        anchor = btn.dataset.ym;
        render();
      } else {
        apply(anchor, btn.dataset.ym);
      }
    } else if (btn.dataset.preset) {
      preset(btn.dataset.preset);
    } else if (btn.matches(".mp-clear, .mp-all")) {
      apply("", "");
    }
  });
  // предпросмотр периода при наведении после первого клика
  root.addEventListener("mouseover", (e) => {
    const ym = e.target.closest("[data-ym]")?.dataset.ym;
    if (anchor && ym && ym !== hover) {
      hover = ym;
      render();
    }
  });
  root.addEventListener("keydown", (e) => e.key === "Escape" && close());
  render();
}

// клик вне открытого выбора периода закрывает его (и применяет начатый выбор).
// composedPath, а не contains(target): сетка перерисовывается в обработчике клика, и target к этому моменту уже вне DOM
document.addEventListener("click", (e) => {
  const path = e.composedPath();
  $$("[data-month-picker]").forEach((picker) => {
    if (!path.includes(picker)) picker.close?.();
  });
});

function setActions(html, handlers = {}) {
  const box = $("#page-actions");
  box.innerHTML = html;
  Object.entries(handlers).forEach(([id, fn]) => $(`#${id}`, box)?.addEventListener("click", fn));
}

async function remove(path, label, list) {
  if (!confirmAction(t("confirmDelete", { name: label }))) return;
  try {
    await api.del(path);
    toast(t("deleted"));
    list.reload();
  } catch (err) {
    toast(err.message, true);
  }
}

// аватарка: фото, если загружено, иначе инициалы; cls — классы обёртки (avatar, avatar big, pc-avatar c3 …)
const avatarHtml = (name, url, cls = "avatar") =>
  url
    ? `<span class="${cls} has-photo"><img src="${esc(url)}" alt="${esc(name || "")}" loading="lazy"></span>`
    : `<span class="${cls}">${esc(initialsOf(name || "?"))}</span>`;

// выбрать фото, выбрать в редакторе, какая часть попадёт в кружок, и загрузить на url (multipart, поле file);
// вернёт ответ сервера или null, если передумали
function pickAvatar(url, title = t("avatar.cropTitle")) {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = "image/*";
    input.addEventListener("change", async () => {
      const file = input.files[0];
      if (!file) return resolve(null);
      const blob = await cropAvatar(file, title);
      if (!blob) return resolve(null);
      const body = new FormData();
      body.append("file", blob, "avatar.jpg");
      try {
        resolve(await api.post(url, body));
      } catch (err) {
        toast(err.message, true);
        resolve(null);
      }
    });
    input.click();
  });
}

const CROP_OUT = 640; // как SIZE в accounts/avatars.py
const CROP_MAX_ZOOM = 5;

// Редактор аватарки: фото под круглой рамкой — двигать мышью или пальцем, приближать ползунком или колёсиком.
// Вернёт квадрат CROP_OUT×CROP_OUT (jpg) — ровно то, что видно в круге; null — отмена или не картинка
function cropAvatar(file, title) {
  return new Promise((resolve) => {
    const src = URL.createObjectURL(file);
    const img = new Image();
    img.onerror = () => {
      URL.revokeObjectURL(src);
      toast(t("avatar.notImage"), true);
      resolve(null);
    };
    img.onload = () => {
      const body = openModal(
        title,
        `<div class="cropper">
          <div class="crop-stage" data-stage><img src="${src}" alt="" draggable="false"><div class="crop-ring"></div></div>
          <label class="crop-zoom">${icon("zoom_out")}<input type="range" min="1" max="${CROP_MAX_ZOOM}" step="0.01" value="1" data-zoom>${icon("zoom_in")}</label>
          <p class="muted">${esc(t("avatar.cropHint"))}</p>
          <div class="form-actions">
            <button type="button" class="btn" data-crop-cancel>${esc(t("cancel"))}</button>
            <button type="button" class="btn primary" data-crop-save>${icon("check")}${esc(t("save"))}</button>
          </div>
        </div>`
      );
      const stage = $("[data-stage]", body);
      const view = $("img", stage);
      const zoomInput = $("[data-zoom]", body);
      const size = () => stage.clientWidth; // квадрат; на телефоне меньше
      // x, y — левый верхний угол фото относительно рамки; scale — пикселей экрана на пиксель фото
      let zoom = 1;
      let x = 0;
      let y = 0;
      const base = () => size() / Math.min(img.naturalWidth, img.naturalHeight); // zoom 1 — фото закрывает круг
      const clamp = () => {
        const s = base() * zoom;
        x = Math.min(0, Math.max(size() - img.naturalWidth * s, x));
        y = Math.min(0, Math.max(size() - img.naturalHeight * s, y));
      };
      const draw = () => {
        clamp();
        const s = base() * zoom;
        Object.assign(view.style, { width: `${img.naturalWidth * s}px`, height: `${img.naturalHeight * s}px`, transform: `translate(${x}px, ${y}px)` });
      };
      // приблизить, оставив точку (cx, cy) рамки на месте
      const zoomTo = (next, cx = size() / 2, cy = size() / 2) => {
        next = Math.min(CROP_MAX_ZOOM, Math.max(1, next));
        const k = next / zoom;
        x = cx - (cx - x) * k;
        y = cy - (cy - y) * k;
        zoom = next;
        zoomInput.value = zoom;
        draw();
      };
      // сначала — по центру
      const s0 = base();
      x = (size() - img.naturalWidth * s0) / 2;
      y = (size() - img.naturalHeight * s0) / 2;
      draw();

      zoomInput.addEventListener("input", () => zoomTo(Number(zoomInput.value)));
      stage.addEventListener(
        "wheel",
        (e) => {
          e.preventDefault();
          const r = stage.getBoundingClientRect();
          zoomTo(zoom * (e.deltaY < 0 ? 1.1 : 1 / 1.1), e.clientX - r.left, e.clientY - r.top);
        },
        { passive: false }
      );
      // перетаскивание; двумя пальцами — щипок для приближения
      const pointers = new Map();
      let pinch = null;
      stage.addEventListener("pointerdown", (e) => {
        stage.setPointerCapture(e.pointerId);
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        stage.classList.add("grabbing");
      });
      stage.addEventListener("pointermove", (e) => {
        const prev = pointers.get(e.pointerId);
        if (!prev) return;
        pointers.set(e.pointerId, { x: e.clientX, y: e.clientY });
        if (pointers.size === 2) {
          const [a, b] = [...pointers.values()];
          const dist = Math.hypot(a.x - b.x, a.y - b.y);
          const r = stage.getBoundingClientRect();
          if (pinch) zoomTo(zoom * (dist / pinch), (a.x + b.x) / 2 - r.left, (a.y + b.y) / 2 - r.top);
          pinch = dist;
          return;
        }
        x += e.clientX - prev.x;
        y += e.clientY - prev.y;
        draw();
      });
      const release = (e) => {
        pointers.delete(e.pointerId);
        pinch = null;
        if (!pointers.size) stage.classList.remove("grabbing");
      };
      stage.addEventListener("pointerup", release);
      stage.addEventListener("pointercancel", release);

      let done = false;
      const finish = (blob) => {
        if (done) return;
        done = true;
        observer.disconnect();
        URL.revokeObjectURL(src);
        resolve(blob);
      };
      // окно закрыли крестиком, фоном или Esc — это отмена
      const observer = new MutationObserver(() => !body.contains(stage) && finish(null));
      observer.observe(body, { childList: true });
      $("[data-crop-cancel]", body).addEventListener("click", () => {
        closeModal();
        finish(null);
      });
      $("[data-crop-save]", body).addEventListener("click", () => {
        const s = base() * zoom;
        const canvas = document.createElement("canvas");
        canvas.width = canvas.height = CROP_OUT;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff"; // прозрачный png — на белом, а не на чёрном
        ctx.fillRect(0, 0, CROP_OUT, CROP_OUT);
        ctx.imageSmoothingQuality = "high";
        ctx.drawImage(img, -x / s, -y / s, size() / s, size() / s, 0, 0, CROP_OUT, CROP_OUT);
        canvas.toBlob(
          (blob) => {
            closeModal();
            finish(blob);
          },
          "image/jpeg",
          0.92
        );
      });
    };
    img.src = src;
  });
}

const editButtons = () =>
  `<button class="btn sm" data-act="edit">${esc(t("edit"))}</button> <button class="btn sm danger" data-act="delete">${esc(t("delete"))}</button>`;

const addButton = (label) => `<button class="btn primary" id="add">${esc(label)}</button>`;

// ---------------------------------------------------------------- Статистика

async function renderDashboard() {
  const view = $("#view");
  let pharmacyId = me.pharmacy;

  if (isAdmin()) {
    const list = Object.entries(pharmacyNames);
    if (!list.length) {
      view.innerHTML = `<div class="card empty">${esc(t("dash.noPharmacies"))}</div>`;
      return;
    }
    pharmacyId = pharmacyId || list[0][0];
    setActions(`<select id="stats-pharmacy">${options(list, pharmacyId)}</select>`);
    $("#stats-pharmacy").addEventListener("change", (e) => loadStats(e.target.value));
  }
  loadStats(pharmacyId);

  async function loadStats(id) {
    view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
    try {
      const s = await api.get(`/pharmacies/${id}/stats/`);
      const avgCheck = s.orders_count ? Number(s.total_sales) / s.orders_count : 0;
      const currency = esc(t("currency"));
      const cur = s.monthly.at(-1);

      view.innerHTML = `
        <div class="dash">
          <div class="stats">
            ${statCard({
              eyebrow: t("dash.ordersEyebrow"),
              title: t("dash.ordersCount"),
              icon: "receipt_long",
              value: Number(s.orders_count).toLocaleString(i18n.locale),
            })}
            ${statCard({
              accent: "secondary",
              eyebrow: t("dash.revenueEyebrow"),
              title: t("dash.totalSales"),
              icon: "account_balance_wallet",
              value: money(s.total_sales),
              unit: currency,
              foot: `${icon("analytics")}<span>${esc(t("dash.avgCheck"))}</span><b>${money(avgCheck)} ${currency}</b>`,
            })}
            ${statCard({
              eyebrow: t("dash.todayEyebrow"),
              title: t("dash.todaySales"),
              icon: "point_of_sale",
              value: money(s.today_sales),
              unit: currency,
            })}
          </div>
          ${ratesPanel()}
          <div class="charts">
            ${panel({
              icon: "calendar_month",
              iconCls: "primary",
              title: t("dash.byMonth"),
              hint: t("dash.byMonthHint"),
              count: "",
              table: simpleTable(
                [
                  t("dash.month"),
                  { label: t("dash.sales"), cls: "num" },
                  { label: t("dash.expenses"), cls: "num" },
                  { label: t("dash.profit"), cls: "num" },
                  { label: t("dash.purchases"), cls: "num" },
                ],
                [...s.monthly].reverse().map((m) => [
                  `<b>${esc(monthName(m.month))}</b>`,
                  money(m.sales),
                  money(m.expenses),
                  `<span class="${Number(m.profit) < 0 ? "neg" : "pos"}">${money(m.profit)}</span>`,
                  money(m.purchases),
                ])
              ),
            })}
            ${panel({
              icon: "donut_large",
              iconCls: "primary",
              title: t("dash.donut"),
              hint: t("dash.donutHint"),
              count: `<select id="donut-month">${options(
                [...s.monthly].reverse().map((m) => [m.month, monthName(m.month)]),
                cur.month
              )}</select>`,
              table: `<div class="donut-box" id="donut"></div>`,
            })}
          </div>
          <div class="charts">
            ${panel({
              icon: "bar_chart",
              iconCls: "primary",
              title: t("dash.salesChart"),
              hint: t("dash.salesChartHint"),
              count: `<button type="button" class="icon-btn" id="sales-toggle" title="${esc(t("dash.showTable"))}">${icon("table_chart")}</button>`,
              table: `<div class="chart-box" id="sales-chart"></div>
                <div class="hidden" id="sales-table">${simpleTable(
                  [t("date"), { label: t("dash.revenue"), cls: "num" }, { label: t("qty"), cls: "num" }],
                  s.sales_by_day.map((d) => [dateOnly(d.date), money(d.total), d.count])
                )}</div>`,
            })}
            ${rankingPanel(s.medicine_ranking, t("rank.title"), t("dash.topChartHint"))}
          </div>
          ${weekPanel(t("week.title"))}
          ${panel({
            icon: "event_busy",
            iconCls: "error",
            title: t("dash.expiring"),
            hint: t("dash.expiringHint"),
            count: s.expiring_soon.length ? badge(s.expiring_soon.length, "bad") : "",
            table: `<div id="expiring-table"></div>`,
            link: can("purchase.view") ? ["#/purchases", t("dash.toPurchases")] : null,
          })}
        </div>`;
      setupSalesChart(s.sales_by_day);
      mountRates($("[data-rates]", view));
      mountWeek($("[data-week]", view), s.week, (date) => api.get(`/pharmacies/${id}/week/`, { date }));
      mountPagedTable(
        $("#expiring-table"),
        [t("medicine"), { label: t("qty"), cls: "center" }, t("expireDate"), { label: t("dash.daysLeft"), cls: "num" }],
        s.expiring_soon.map((p) => [`<b>${esc(p.medicine__title)}</b>`, esc(p.quantity), dateOnly(p.expire_date), daysLeftBadge(p.expire_date)])
      );
      const donut = $("#donut");
      const byMonth = Object.fromEntries(s.monthly.map((m) => [m.month, m]));
      drawDonut(donut, cur);
      $("#donut-month").addEventListener("change", (e) => drawDonut(donut, byMonth[e.target.value]));
    } catch (err) {
      view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    }
  }
}

// ---------------------------------------------------------------- Курсы валют (статистика и профиль)

const RATES_POLL_MS = 15000; // пока сервер догружает курсы банков
let ratesCurrency = (() => {
  try {
    return localStorage.getItem("ratesCurrency") || "USD";
  } catch {
    return "USD";
  }
})();

const rateNumber = (v) =>
  Number(v).toLocaleString(i18n.locale, { minimumFractionDigits: Number(v) < 1 ? 4 : 2, maximumFractionDigits: 4 });

function ratesPanel() {
  return `
    <div class="card panel rates" data-rates>
      <div class="panel-head">
        <div class="panel-title">
          <div class="panel-icon primary">${icon("currency_exchange")}</div>
          <div><h3>${esc(t("rates.title"))} <span class="rates-info" title="${esc(t("rates.note"))}">${icon("info")}</span></h3><div class="panel-hint" data-rates-hint>${esc(t("rates.hint"))}</div></div>
        </div>
        <div class="rates-seg" role="tablist" data-rates-tabs></div>
      </div>
      <div data-rates-body><p class="muted empty">${esc(t("loading"))}</p></div>
    </div>`;
}

// Заполняет панель ratesPanel(); сам переспрашивает сервер, пока часть банков ещё грузится
async function mountRates(root, attempt = 0) {
  if (!root?.isConnected) return;
  let data;
  try {
    data = await api.get("/rates/");
  } catch (err) {
    $("[data-rates-body]", root).innerHTML = `<p class="muted empty error">${esc(err.message)}</p>`;
    return;
  }
  if (!root.isConnected) return;
  if (!data.currencies.includes(ratesCurrency)) ratesCurrency = data.currencies[0];

  const draw = () => {
    const cur = ratesCurrency;
    $("[data-rates-tabs]", root).innerHTML = data.currencies
      .map((c) => `<button type="button" data-rate-cur="${c}" class="${c === cur ? "active" : ""}">${c}</button>`)
      .join("");
    const rows = data.banks.map((b) => ({ ...b, pair: b.rates[cur] }));
    const priced = rows.filter((r) => r.pair);
    // выгоднее всего: продать валюту туда, где банк покупает дороже; купить — где продаёт дешевле
    const bestBuy = Math.max(...priced.map((r) => Number(r.pair.buy)));
    const bestSell = Math.min(...priced.map((r) => Number(r.pair.sell)));
    const line = (label, value, best) => {
      const top = priced.length > 1 && Number(value) === best;
      return `<div class="rate-line${top ? " best" : ""}"><span>${esc(label)}</span><b>${rateNumber(value)}</b></div>`;
    };

    const official = data.nbt[cur];
    $("[data-rates-body]", root).innerHTML = `<div class="rates-grid">
      ${
        official
          ? `<div class="rate-card official">
              <span class="rate-bank">${icon("account_balance")}${esc(t("rates.nbt"))}</span>
              <b class="rate-big">${rateNumber(official)}</b>
              <span class="rate-sub">${esc(t("rates.officialShort", { cur }))}</span>
            </div>`
          : ""
      }
      ${rows
        .map(
          (r) => `<div class="rate-card">
            <span class="rate-bank">${esc(r.name)}</span>
            ${
              r.pair
                ? line(t("rates.buy"), r.pair.buy, bestBuy) + line(t("rates.sell"), r.pair.sell, bestSell)
                : `<span class="rate-sub">${esc(t(r.available ? "rates.none" : "rates.loading"))}</span>`
            }
          </div>`
        )
        .join("")}
    </div>`;
    if (data.updated_at) $("[data-rates-hint]", root).textContent = `${t("rates.hint")} · ${t("rates.updated", { time: dateTime(data.updated_at) })}`;
  };

  root.onclick = (e) => {
    const btn = e.target.closest("[data-rate-cur]");
    if (!btn) return;
    ratesCurrency = btn.dataset.rateCur;
    try {
      localStorage.setItem("ratesCurrency", ratesCurrency);
    } catch {}
    draw();
  };
  draw();
  if (data.loading && attempt < 8) setTimeout(() => mountRates(root, attempt + 1), RATES_POLL_MS);
}

function statCard({ accent = "", eyebrow, title, icon: iconName, value, unit = "", foot = "" }) {
  return `
    <div class="card stat ${accent}">
      <div class="stat-head">
        <div><span class="stat-eyebrow">${esc(eyebrow)}</span><span class="stat-title">${esc(title)}</span></div>
        <div class="stat-icon">${icon(iconName)}</div>
      </div>
      <div class="stat-value"><b>${value}</b>${unit ? `<span>${unit}</span>` : ""}</div>
      <div class="stat-foot">${foot}</div>
    </div>`;
}

function panel({ icon: iconName, iconCls, title, hint, count, table, link }) {
  return `
    <div class="card panel">
      <div>
        <div class="panel-head">
          <div class="panel-title">
            <div class="panel-icon ${iconCls}">${icon(iconName)}</div>
            <div><h3>${esc(title)}</h3><div class="panel-hint">${esc(hint)}</div></div>
          </div>
          ${count}
        </div>
        ${table}
      </div>
      ${link ? `<div class="panel-foot"><a class="link-arrow" href="${link[0]}">${esc(link[1])}${icon("arrow_forward")}</a></div>` : ""}
    </div>`;
}

// ---------------------------------------------------------------- Диаграммы

// «Красивый» шаг шкалы: 1, 2, 2.5, 5 × 10^n
function niceStep(raw) {
  const mag = 10 ** Math.floor(Math.log10(raw));
  return [1, 2, 2.5, 5, 10].map((m) => m * mag).find((s) => s >= raw);
}

// Столбик: скругление 4px сверху, прямой угол у базовой линии
function barPath(x, y, w, h) {
  const r = Math.min(4, h, w / 2);
  return `M${x},${y + h}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + h}Z`;
}

// marks = { best: "ГГГГ-ММ-ДД", worst: "ГГГГ-ММ-ДД" } — эти дни выделяются цветом, остальные приглушаются
function drawSalesChart(box, days, animate, marks = null) {
  const markOf = (d) => (!marks ? "" : d.date === marks.best ? "best" : d.date === marks.worst ? "worst" : "dim");
  const width = box.clientWidth;
  if (!width) return;
  const height = 220, top = 22, right = 4, bottom = 26;
  const values = days.map((d) => Number(d.total));
  const max = Math.max(...values);
  const step = niceStep(max / 4 || 1);
  const yMax = Math.max(step * Math.ceil(max / step), step);
  const ticks = [];
  for (let v = 0; v <= yMax + step / 2; v += step) ticks.push(v);

  const compact = new Intl.NumberFormat(i18n.locale, { notation: "compact", maximumFractionDigits: 1 });
  const left = Math.max(...ticks.map((v) => compact.format(v).length)) * 7 + 10;
  const plotW = width - left - right;
  const plotH = height - top - bottom;
  const band = plotW / days.length;
  const barW = Math.min(24, Math.max(4, band * 0.62));
  const y = (v) => top + plotH - (v / yMax) * plotH;
  const labelEvery = band < 36 ? 2 : 1;
  const last = days.length - 1;
  const maxIdx = max > 0 ? values.indexOf(max) : -1;
  const shortDate = (d) => new Date(`${d}T00:00:00`).toLocaleDateString(i18n.dateLocale, { day: "2-digit", month: "2-digit" });

  let grid = "", bars = "", hovers = "", hits = "", labels = "";
  ticks.forEach((v) => {
    grid += `<line class="chart-grid" x1="${left}" x2="${width - right}" y1="${y(v)}" y2="${y(v)}"/>
      <text class="chart-axis" x="${left - 8}" y="${y(v) + 4}" text-anchor="end">${compact.format(v)}</text>`;
  });
  days.forEach((d, i) => {
    const x0 = left + i * band;
    const cx = x0 + band / 2;
    const h = (values[i] / yMax) * plotH;
    hovers += `<rect class="chart-hover" data-hover="${i}" x="${x0 + 1}" y="${top}" width="${band - 2}" height="${plotH}" rx="6"/>`;
    if (h > 0) bars += `<path class="chart-bar ${markOf(d)}" data-bar="${i}" d="${barPath(cx - barW / 2, y(values[i]), barW, h)}" style="animation-delay:${i * 30}ms"/>`;
    if ((last - i) % labelEvery === 0) labels += `<text class="chart-axis" x="${cx}" y="${height - 6}" text-anchor="middle">${shortDate(d.date)}</text>`;
    hits += `<rect class="chart-hit" data-i="${i}" x="${x0}" y="${top}" width="${band}" height="${plotH + bottom}"/>`;
  });
  if (maxIdx >= 0) {
    const cx = left + maxIdx * band + band / 2;
    labels += `<text class="chart-value" x="${cx}" y="${y(max) - 7}" text-anchor="middle">${compact.format(max)}</text>`;
  }

  box.classList.toggle("animate", animate);
  box.classList.toggle("marked", !!marks);
  box.innerHTML = `
    <svg width="${width}" height="${height}" role="img" aria-label="${esc(t("dash.salesChart"))}">
      ${grid}${hovers}${bars}${labels}${hits}
    </svg>
    ${max === 0 ? `<div class="chart-empty">${esc(t("dash.noSales"))}</div>` : ""}
    <div class="chart-tip hidden"></div>`;

  const svg = $("svg", box);
  const tip = $(".chart-tip", box);
  const hide = () => {
    tip.classList.add("hidden");
    box.classList.remove("hovering");
    $$(".on", svg).forEach((el) => el.classList.remove("on"));
  };
  const show = (e) => {
    const hit = e.target.closest("[data-i]");
    if (!hit) return hide();
    const i = Number(hit.dataset.i);
    const d = days[i];
    $$(".on", svg).forEach((el) => el.classList.remove("on"));
    $(`[data-hover="${i}"]`, svg).classList.add("on");
    $(`[data-bar="${i}"]`, svg)?.classList.add("on");
    box.classList.add("hovering");
    const date = new Date(`${d.date}T00:00:00`).toLocaleDateString(i18n.dateLocale, { weekday: "short", day: "numeric", month: "long" });
    tip.innerHTML = `<b>${esc(date)}</b><i class="key ${markOf(d)}"></i>${money(d.total)} ${esc(t("currency"))}<br><span>${esc(t("dash.receipts", { n: d.count }))}</span>`;
    tip.classList.remove("hidden");
    const cx = left + i * band + band / 2;
    tip.style.left = `${Math.min(Math.max(cx, 80), width - 80)}px`;
    tip.style.top = `${Math.max(y(values[i]), top + 40)}px`;
  };
  svg.addEventListener("pointermove", show);
  svg.addEventListener("pointerdown", show);
  svg.addEventListener("pointerleave", hide);
}

// перерисовка по ширине карточки (без повторной анимации); после ухода со страницы наблюдатель отключается
function redrawOnResize(box, draw) {
  let lastWidth = box.clientWidth;
  const observer = new ResizeObserver(() => {
    if (!box.isConnected) return observer.disconnect();
    if (box.clientWidth && box.clientWidth !== lastWidth) {
      lastWidth = box.clientWidth;
      draw();
    }
  });
  observer.observe(box);
}

// ---------------------------------------------------------------- Лучший и худший день недели

const ymd = (d) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
const dayDate = (s) => new Date(`${s}T00:00:00`);
const addDays = (s, n) => {
  const d = dayDate(s);
  d.setDate(d.getDate() + n);
  return ymd(d);
};
const mondayOf = (d) => {
  const x = new Date(d.getFullYear(), d.getMonth(), d.getDate());
  x.setDate(x.getDate() - ((x.getDay() + 6) % 7));
  return ymd(x);
};

// «26 окт – 1 ноя 2026»
function weekLabel(monday) {
  const fmt = (s, opts) => dayDate(s).toLocaleDateString(i18n.dateLocale, opts);
  return `${fmt(monday, { day: "numeric", month: "short" })} – ${fmt(addDays(monday, 6), { day: "numeric", month: "short", year: "numeric" })}`;
}

// [‹] [📅 26 окт – 1 ноя 2026 ▾] [›] — как выбор месяца; в окне — календарь, строка = неделя (пн–вс)
function weekPickerHtml() {
  return `
    <div class="month-picker week-picker" data-month-picker>
      <button type="button" class="mp-step" data-wstep="-7" title="${esc(t("week.prev"))}">${icon("chevron_left")}</button>
      <button type="button" class="mp-toggle" aria-haspopup="true">${icon("date_range")}<span class="mp-label"></span>${icon("expand_more")}</button>
      <button type="button" class="mp-step" data-wstep="7" title="${esc(t("week.next"))}">${icon("chevron_right")}</button>
      <div class="mp-pop hidden">
        <div class="mp-presets">
          <button type="button" data-wpreset="0">${esc(t("week.thisWeek"))}</button>
          <button type="button" data-wpreset="-7">${esc(t("week.lastWeek"))}</button>
        </div>
        <div class="mp-year">
          <button type="button" class="mp-step" data-wmonth="-1">${icon("chevron_left")}</button>
          <b></b>
          <button type="button" class="mp-step" data-wmonth="1">${icon("chevron_right")}</button>
        </div>
        <div class="wk-head"></div>
        <div class="wk-grid"></div>
      </div>
    </div>`;
}

/** Выбор недели; onPick(понедельник ГГГГ-ММ-ДД). Будущие недели недоступны. Возвращает { set(понедельник) }. */
function setupWeekPicker(root, onPick) {
  const pop = $(".mp-pop", root);
  const today = ymd(new Date());
  const thisMonday = mondayOf(new Date());
  let value = thisMonday; // понедельник выбранной недели
  let shown = value.slice(0, 7); // месяц в календаре, ГГГГ-ММ

  const render = () => {
    root.classList.toggle("active", value !== thisMonday);
    $(".mp-label", root).textContent = weekLabel(value);
    $('[data-wstep="7"]', root).disabled = value >= thisMonday;
    $(".mp-year b", root).textContent = monthName(`${shown}-01`);
    $('[data-wmonth="1"]', root).disabled = shown >= today.slice(0, 7); // будущих месяцев нет

    // шапка: пн … вс (1 января 2024 — понедельник)
    $(".wk-head", root).innerHTML = Array.from({ length: 7 }, (_, i) =>
      `<span>${esc(new Date(2024, 0, 1 + i).toLocaleDateString(i18n.dateLocale, { weekday: "short" }).replace(".", ""))}</span>`
    ).join("");

    // строки — недели, задевающие показанный месяц
    const rows = [];
    for (let monday = mondayOf(dayDate(`${shown}-01`)); monday.slice(0, 7) <= shown; monday = addDays(monday, 7)) {
      const cells = Array.from({ length: 7 }, (_, i) => {
        const day = addDays(monday, i);
        const cls = [day.slice(0, 7) !== shown && "out", day === today && "today", day > today && "future"].filter(Boolean).join(" ");
        return `<span class="${cls}">${Number(day.slice(8))}</span>`;
      }).join("");
      const cls = [monday === value && "selected", monday === thisMonday && "this-week"].filter(Boolean).join(" ");
      rows.push(`<button type="button" class="wk-row ${cls}" data-week="${monday}"${monday > thisMonday ? " disabled" : ""}>${cells}</button>`);
    }
    $(".wk-grid", root).innerHTML = rows.join("");
  };

  const pick = (monday) => {
    value = monday;
    pop.classList.add("hidden");
    render();
    onPick(monday);
  };

  root.addEventListener("click", (e) => {
    const btn = e.target.closest("button");
    if (!btn || btn.disabled) return;
    if (btn.matches(".mp-toggle")) {
      shown = value.slice(0, 7);
      render();
      pop.classList.toggle("hidden");
    } else if (btn.dataset.wstep) {
      pick(addDays(value, Number(btn.dataset.wstep)));
    } else if (btn.dataset.wmonth) {
      const [y, m] = shown.split("-").map(Number);
      shown = ymOf(new Date(y, m - 1 + Number(btn.dataset.wmonth), 1));
      render();
    } else if (btn.dataset.week) {
      pick(btn.dataset.week);
    } else if (btn.dataset.wpreset) {
      pick(addDays(thisMonday, Number(btn.dataset.wpreset)));
    }
  });
  root.addEventListener("keydown", (e) => e.key === "Escape" && pop.classList.add("hidden"));
  root.close = () => pop.classList.add("hidden"); // закрытие кликом вне — общий обработчик выбора периода
  render();

  return {
    set(monday) {
      value = monday;
      render();
    },
  };
}

function weekPanel(title) {
  return panel({
    icon: "date_range",
    iconCls: "primary",
    title,
    hint: "",
    count: weekPickerHtml(),
    table: `<div class="week" data-week>
        <div class="week-days"></div>
        <div class="chart-box" data-week-chart></div>
      </div>`,
  });
}

/**
 * Неделя пн–вс: плитки «лучший / худший день» + столбики 7 дней (эти два дня выделены цветом).
 * load(date) — загрузить неделю, в которую попадает date (ГГГГ-ММ-ДД).
 */
function mountWeek(body, week, load) {
  const card = body.closest(".panel");
  const chart = $("[data-week-chart]", body);
  const today = ymd(new Date());
  let current = week;

  const tile = (kind, d) => {
    if (!d) {
      const text = kind === "best" ? t("week.noSales") : current.best ? t("week.noWorst") : "";
      return `<div class="day-tile empty">${esc(text)}</div>`;
    }
    const date = dayDate(d.date).toLocaleDateString(i18n.dateLocale, { weekday: "long", day: "numeric", month: "long" });
    const note = d.date === today ? ` · ${t("week.todayNote")}` : "";
    return `
      <div class="day-tile ${kind}">
        <span class="day-kind"><i class="key ${kind === "best" ? "s1" : "s2"}"></i>${esc(t(`week.${kind}`))}</span>
        <b>${esc(date.charAt(0).toUpperCase() + date.slice(1))}</b>
        <div class="day-sum">${money(d.total)} <span>${esc(t("currency"))}</span></div>
        <span class="muted small">${esc(t("dash.receipts", { n: d.count }) + note)}</span>
      </div>`;
  };

  const render = (w, animate) => {
    current = w;
    picker.set(w.start);

    const prev = Number(w.prev_total);
    const pct = prev ? ((Number(w.total) - prev) / prev) * 100 : null;
    $(".week-days", body).innerHTML = `
      ${tile("best", w.best)}
      ${tile("worst", w.worst)}
      <div class="week-total">
        <span>${esc(t("week.total"))} <b>${money(w.total)} ${esc(t("currency"))}</b> · ${esc(t("dash.receipts", { n: w.count }))}</span>
        ${pct === null ? "" : `<span>${badge(`${pct >= 0 ? "+" : ""}${pct.toFixed(1)}%`, pct >= 0 ? "ok" : "bad")} ${esc(t("week.vsPrev"))}</span>`}
      </div>`;
    drawSalesChart(chart, w.days, animate, { best: w.best?.date, worst: w.worst?.date });
  };

  const picker = setupWeekPicker($(".week-picker", card), async (monday) => {
    body.classList.add("loading");
    try {
      render(await load(monday), true);
    } catch (err) {
      toast(err.message, true);
    } finally {
      body.classList.remove("loading");
    }
  });
  redrawOnResize(chart, () => render(current, false));
  render(week, true);
}

function setupSalesChart(days) {
  const box = $("#sales-chart");
  const table = $("#sales-table");
  const toggle = $("#sales-toggle");
  drawSalesChart(box, days, true);
  redrawOnResize(box, () => drawSalesChart(box, days, false));

  toggle.addEventListener("click", () => {
    const showTable = table.classList.contains("hidden");
    table.classList.toggle("hidden", !showTable);
    box.classList.toggle("hidden", showTable);
    toggle.innerHTML = icon(showTable ? "bar_chart" : "table_chart");
    toggle.title = t(showTable ? "dash.showChart" : "dash.showTable");
    if (!showTable) drawSalesChart(box, days, false);
  });
}

// Лучшие / худшие по продажам лекарства: вкладки; полосы обоих списков в одном масштабе, чтобы их можно было сравнить
function rankingPanel(ranking, title, hint) {
  const max = Math.max(1, ...ranking.best.map((m) => m.sold), ...ranking.worst.map((m) => m.sold));
  const bars = (items, kind) => {
    if (!items.length) return `<p class="muted empty">${esc(t(kind === "best" ? "dash.noSales" : "noData"))}</p>`;
    return `<div class="hbars">${items
      .map((m, i) => {
        const value = m.sold
          ? `${t("dash.sold", { n: m.sold })} · ${money(m.revenue)}`
          : `${t("rank.never")} · ${t("rank.stock", { q: m.quantity })}`;
        return `
        <div>
          <div class="hbar-label"><span>${esc(m.medicine__title)}</span><b class="${m.sold ? "" : "never"}">${esc(value)}</b></div>
          <div class="hbar-track"><div class="hbar-fill ${kind}" style="width:${(m.sold / max) * 100}%;animation-delay:${i * 60}ms"></div></div>
        </div>`;
      })
      .join("")}</div>`;
  };
  return panel({
    icon: "leaderboard",
    iconCls: "primary",
    title,
    hint,
    count: `<div class="seg" role="tablist">
        <button type="button" role="tab" class="active" data-rank="best">${icon("trending_up")}${esc(t("rank.best"))}</button>
        <button type="button" role="tab" data-rank="worst">${icon("trending_down")}${esc(t("rank.worst"))}</button>
      </div>`,
    table: `<div class="rank" data-rank-list="best">${bars(ranking.best, "best")}</div>
      <div class="rank hidden" data-rank-list="worst">${bars(ranking.worst, "worst")}</div>`,
  });
}

// вкладки «Лучшие / Худшие» (панель перерисовывается при каждом заходе — слушаем на документе)
document.addEventListener("click", (e) => {
  const tab = e.target.closest("[data-rank]");
  if (!tab) return;
  const card = tab.closest(".panel");
  $$("[data-rank]", card).forEach((b) => b.classList.toggle("active", b === tab));
  // показ из display:none сам перезапускает анимацию — полосы вырастают заново
  $$("[data-rank-list]", card).forEach((list) => list.classList.toggle("hidden", list.dataset.rankList !== tab.dataset.rank));
});

// «Октябрь 2026 г.» — с заглавной буквы
function monthName(month) {
  const name = new Date(`${month}T00:00:00`).toLocaleDateString(i18n.dateLocale, { month: "long", year: "numeric" });
  return name.charAt(0).toUpperCase() + name.slice(1);
}

// Сектор кольца между углами a0 и a1 (радианы), радиусы r0 (внутр.) и r1 (внешн.)
function arcPath(cx, cy, r0, r1, a0, a1) {
  a1 = Math.min(a1, a0 + 2 * Math.PI - 1e-4); // полный круг SVG-дугой не рисуется
  const large = a1 - a0 > Math.PI ? 1 : 0;
  const p = (r, a) => `${cx + r * Math.cos(a)},${cy + r * Math.sin(a)}`;
  return `M${p(r1, a0)}A${r1},${r1} 0 ${large} 1 ${p(r1, a1)}L${p(r0, a1)}A${r0},${r0} 0 ${large} 0 ${p(r0, a0)}Z`;
}

// Кольцо «продажи = расходы + прибыль» за месяц, с легендой и подсказкой при наведении
function drawDonut(box, month) {
  const sales = Number(month.sales);
  const expenses = Number(month.expenses);
  const profit = Number(month.profit);
  const currency = esc(t("currency"));
  const slices = [
    { key: "expenses", label: t("dash.expenses"), value: expenses, cls: "s2" },
    { key: "profit", label: t("dash.profit"), value: profit, cls: "s1" },
  ];
  const pct = (v) => (sales ? `${((v / sales) * 100).toFixed(1)}%` : "—");

  let ring = "";
  let note = "";
  const size = 200, c = size / 2, r1 = 92, r0 = 64;
  if (!sales) {
    ring = `<circle class="donut-track" cx="${c}" cy="${c}" r="${(r0 + r1) / 2}" stroke-width="${r1 - r0}"/>`;
    note = t("dash.noSales");
  } else if (profit < 0) {
    // убыток: кольцо «продажи = расходы + прибыль» не складывается — показываем трек и пояснение
    ring = `<circle class="donut-track" cx="${c}" cy="${c}" r="${(r0 + r1) / 2}" stroke-width="${r1 - r0}"/>`;
    note = t("dash.lossNote", { sum: `${money(-profit)} ${t("currency")}` });
  } else {
    let a = -Math.PI / 2;
    slices.forEach((sl, i) => {
      if (sl.value <= 0) return;
      const a1 = a + (sl.value / sales) * 2 * Math.PI;
      ring += `<path class="donut-slice ${sl.cls}" data-s="${i}" d="${arcPath(c, c, r0, r1, a, a1)}"/>`;
      sl.mid = (a + a1) / 2;
      a = a1;
    });
  }

  box.innerHTML = `
    <div class="donut-chart">
      <svg viewBox="0 0 ${size} ${size}" role="img" aria-label="${esc(t("dash.donut"))}">
        <g class="donut-ring">${ring}</g>
        <text class="donut-value" x="${c}" y="${c - 2}" text-anchor="middle">${money(sales)}</text>
        <text class="donut-label" x="${c}" y="${c + 18}" text-anchor="middle">${esc(t("dash.sales"))}, ${currency}</text>
      </svg>
      <div class="chart-tip hidden"></div>
    </div>
    ${note ? `<p class="donut-note">${esc(note)}</p>` : ""}
    <div class="legend">
      ${slices
        .map(
          (sl, i) => `
        <div class="legend-row" data-s="${i}">
          <i class="key ${sl.cls}"></i><span>${esc(sl.label)}</span>
          <b>${money(sl.value)} ${currency}</b><em>${pct(Math.max(sl.value, 0))}</em>
        </div>`
        )
        .join("")}
    </div>`;

  const chart = $(".donut-chart", box);
  const tip = $(".chart-tip", box);
  const hide = () => {
    box.classList.remove("hovering");
    tip.classList.add("hidden");
    $$(".on", box).forEach((el) => el.classList.remove("on"));
  };
  const show = (e) => {
    const target = e.target.closest("[data-s]");
    const sl = target && slices[target.dataset.s];
    if (!sl || sl.mid === undefined) return hide();
    $$(".on", box).forEach((el) => el.classList.remove("on"));
    $$(`[data-s="${target.dataset.s}"]`, box).forEach((el) => el.classList.add("on"));
    box.classList.add("hovering");
    tip.innerHTML = `<b><i class="key ${sl.cls}"></i>${esc(sl.label)}</b>${money(sl.value)} ${currency}<br><span>${pct(sl.value)}</span>`;
    tip.classList.remove("hidden");
    // подсказка — у середины сектора
    const scale = chart.clientWidth / size;
    const rm = (r0 + r1) / 2;
    tip.style.left = `${(c + rm * Math.cos(sl.mid)) * scale}px`;
    tip.style.top = `${(c + rm * Math.sin(sl.mid)) * scale}px`;
  };
  box.onpointermove = show;
  box.onpointerdown = show;
  box.onpointerleave = hide;
}

function daysLeftBadge(date) {
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0);
  const days = Math.round((new Date(`${date}T00:00:00`) - midnight) / 86400000);
  return badge(t("dash.days", { n: days }), days <= 10 ? "bad" : days <= 20 ? "warn" : "");
}

// head: строка или { label, cls }
function simpleTable(head, rows) {
  if (!rows.length) return `<p class="muted empty">${esc(t("noData"))}</p>`;
  const cols = head.map((h) => (typeof h === "string" ? { label: h, cls: "" } : h));
  return `<div class="table-wrap"><table>
    <thead><tr>${cols.map((c) => `<th class="${c.cls}">${esc(c.label)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((r) => `<tr>${r.map((cell, i) => `<td class="${cols[i]?.cls || ""}">${cell}</td>`).join("")}</tr>`).join("")}</tbody>
  </table></div>`;
}

// ---------------------------------------------------------------- Аптеки

function renderPharmacies() {
  const fields = [
    { name: "name", label: t("name"), required: true },
    { name: "address", label: t("address") },
    { name: "phone_number", label: t("phone") },
  ];
  const save = (row) => async (data) => {
    if (row) await api.patch(`/pharmacies/${row.id}/`, data);
    else await api.post("/pharmacies/", data);
    toast(t("saved"));
    await loadPharmacyNames();
    list.reload();
  };

  if (can("pharmacy.manage")) {
    setActions(addButton(t("ph.add")), {
      add: () => formModal({ title: t("ph.new"), fields, submit: save(null) }),
    });
  }

  const list = listView({
    endpoint: "/pharmacies/",
    filters: [{ name: "search", placeholder: t("ph.search") }],
    columns: [
      { label: t("name"), html: (r) => `<b>${esc(r.name)}</b>` },
      { label: t("address"), html: (r) => esc(r.address) },
      { label: t("phone"), html: (r) => esc(r.phone_number) },
      { label: t("ph.created"), html: (r) => dateOnly(r.created_at) },
    ],
    actions: (r) =>
      [
        can("user.manage") ? `<button class="btn sm" data-act="staff">${esc(t("ph.staff"))}</button>` : "",
        can("pharmacy.manage") ? editButtons() : "",
      ].join(" "),
    onAction: (act, row) => {
      if (act === "edit") formModal({ title: t("pharmacy"), fields, initial: row, submit: save(row) });
      if (act === "delete") remove(`/pharmacies/${row.id}/`, row.name, list);
      if (act === "staff") location.hash = staffLink(row.id);
    },
  });
}

// ---------------------------------------------------------------- Сотрудники

// Админ проекта (#/users): не все сотрудники сразу, а аптеки со счётчиком; клик — сотрудники этой аптеки
function renderStaffPharmacies() {
  listView({
    endpoint: "/pharmacies/",
    filters: [{ name: "search", placeholder: t("ph.search") }],
    columns: [
      { label: t("pharmacy"), html: (r) => `<b>${esc(r.name)}</b>` },
      { label: t("address"), html: (r) => esc(r.address) },
      { label: t("ph.staffCount"), html: (r) => badge(t("ph.staffN", { n: r.staff_count })), cls: "num" },
    ],
    actions: () => `<button class="btn sm" data-act="staff">${icon("badge")}${esc(t("ph.staff"))}</button>`,
    onAction: (act, row) => (location.hash = staffLink(row.id)),
    onRowClick: (row) => (location.hash = staffLink(row.id)),
  });
}

const staffLink = (pharmacyId) => `#/users?pharmacy=${pharmacyId}`;

function renderUsers(params) {
  const admin = can("user.manage");
  const pharmacy = params.get("pharmacy");
  if (admin && !pharmacy) return renderStaffPharmacies();
  if (admin) {
    $("#page-title").textContent = t("user.staffOf", { name: pharmacyNames[pharmacy] || "" });
  }

  const fields = (row) => [
    { name: "username", label: t("login.username"), required: true },
    {
      name: "password",
      label: t("login.password"),
      type: "password",
      required: !row,
      hint: row ? t("user.passwordKeep") : t("user.passwordMin"),
    },
    { name: "full_name", label: t("user.fullName") },
    { name: "phone_number", label: t("phone") },
    ...(admin
      ? [
          { name: "role", label: t("user.role"), type: "select", options: roleOptions(), required: true },
          { name: "pharmacy", label: t("pharmacy"), type: "select", options: Object.entries(pharmacyNames), empty: t("user.noPharmacy") },
        ]
      : []),
    { name: "is_active", label: t("user.active"), type: "checkbox" },
  ];

  const save = (row) => async (data) => {
    if (!data.password) delete data.password;
    data.phone_number = data.phone_number || null;
    if ("pharmacy" in data) data.pharmacy = data.pharmacy || null;
    if (row) await api.patch(`/users/${row.id}/`, data);
    else await api.post("/users/", data);
    toast(t("saved"));
    list.reload();
  };

  const back = admin ? `<a class="btn" href="#/users">${icon("arrow_back")}${esc(t("user.toPharmacies"))}</a>` : "";
  setActions(back + addButton(admin ? t("user.addEmployee") : t("user.addPharmacist")), {
    add: () =>
      formModal({
        title: admin ? t("user.newEmployee") : t("user.newPharmacist"),
        fields: fields(null),
        initial: { is_active: true, role: "director", pharmacy },
        submit: save(null),
      }),
  });

  const filters = [{ name: "search", placeholder: t("user.search") }];
  if (admin) filters.push({ name: "role", type: "select", placeholder: t("user.allRoles"), options: roleOptions() });

  const list = listView({
    endpoint: "/users/",
    baseParams: admin ? { pharmacy } : {},
    filters,
    columns: [
      { label: t("login.username"), html: (r) => `<span class="user-cell">${avatarHtml(r.full_name || r.username, r.avatar_url)}<b>${esc(r.username)}</b></span>` },
      { label: t("user.fullName"), html: (r) => esc(r.full_name) },
      { label: t("phone"), html: (r) => esc(r.phone_number) },
      { label: t("user.role"), html: (r) => badge(roleLabel(r.role)) },
      { label: t("status"), html: (r) => (r.is_active ? badge(t("user.isActive"), "ok") : badge(t("user.disabled"), "bad")) },
    ],
    // фармацевту — кнопка «Доступ»: страница, где видно и меняется, что он может
    actions: (r) =>
      r.id === me.id
        ? ""
        : `<button class="btn sm" data-act="photo" title="${esc(t("avatar.title"))}">${icon("photo_camera")}</button> ${
            r.role === "pharmacist" ? `<button class="btn sm" data-act="access">${icon("key")}${esc(t("access.button"))}</button> ` : ""
          }${editButtons()}`,
    onAction: (act, row) => {
      if (act === "photo") userPhoto(row, list);
      if (act === "access") location.hash = `#/access?user=${row.id}`;
      if (act === "edit") formModal({ title: row.username, fields: fields(row), initial: row, submit: save(row) });
      if (act === "delete") remove(`/users/${row.id}/`, row.username, list);
    },
  });
}

// Фото сотрудника (админ — любому, директор — своим фармацевтам): посмотреть, загрузить, убрать
function userPhoto(user, list) {
  const name = user.full_name || user.username;
  const body = openModal(
    t("avatar.titleOf", { name }),
    `<div class="avatar-modal">
      ${avatarHtml(name, user.avatar_url, "avatar huge")}
      <p class="muted">${esc(t("avatar.hint"))}</p>
      <div class="form-actions">
        ${user.avatar_url ? `<button type="button" class="btn danger" data-remove-photo>${icon("delete")}${esc(t("avatar.remove"))}</button>` : ""}
        <button type="button" class="btn primary" data-pick-photo>${icon("photo_camera")}${esc(t(user.avatar_url ? "avatar.change" : "avatar.upload"))}</button>
      </div>
    </div>`
  );
  const done = (data) => {
    if (!data) return;
    closeModal();
    toast(t("saved"));
    list.reload();
  };
  $("[data-pick-photo]", body).addEventListener("click", async () => done(await pickAvatar(`/users/${user.id}/avatar/`)));
  $("[data-remove-photo]", body)?.addEventListener("click", async () =>
    done(await api.del(`/users/${user.id}/avatar/`).catch((err) => toast(err.message, true)))
  );
}

// Доступ фармацевта (#/access?user=5): что он может и что нет; директор или админ включает и выключает права
const ACCESS_GROUPS = { sales: "point_of_sale", medicines: "medication", purchases: "shopping_cart", other: "tune" };
const ACCESS_ICONS = {
  "order.view": "receipt_long",
  "order.create": "add_shopping_cart",
  "order.view_all": "groups",
  "order.cancel": "undo",
  "medicine.view": "medication",
  "medicine.manage": "edit_note",
  "writeoff.view": "event_busy",
  "writeoff.create": "delete_sweep",
  "purchase.view": "inventory",
  "purchase.create": "add_box",
  "supplier.view": "local_shipping",
  "supplier.manage": "edit",
  "pharmacy.stats": "monitoring",
  "pharmacy.view": "store",
  "assistant.use": "smart_toy",
};

async function renderAccess(params) {
  const view = $("#view");
  const id = Number(params.get("user")) || 0;
  setActions(`<a class="btn" href="#/users">${icon("arrow_back")}${esc(t("access.back"))}</a>`);
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
  let data;
  try {
    data = await api.get(`/users/${id}/access/`);
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  const items = data.groups.flatMap((g) => g.items);
  const defaults = new Set(items.filter((i) => i.default).map((i) => i.perm));
  let saved = new Set(items.filter((i) => i.granted).map((i) => i.perm));
  let granted = new Set(saved);

  // включили право — включаем и то, без чего оно не работает; выключили — выключаем то, что от него зависит
  const withRequired = (perm, set) => {
    set.add(perm);
    (data.requires[perm] || []).forEach((p) => set.has(p) || withRequired(p, set));
  };
  const withoutDependents = (perm, set) => {
    set.delete(perm);
    Object.entries(data.requires).forEach(([p, needs]) => needs.includes(perm) && set.has(p) && withoutDependents(p, set));
  };
  const same = (a, b) => a.size === b.size && [...a].every((p) => b.has(p));

  const name = data.user.full_name || data.user.username;
  $("#page-title").textContent = t("access.title", { name });
  view.innerHTML = `<div class="access-page"></div>`;
  const page = $(".access-page", view); // обработчики — на элементе страницы, а не на общем #view

  const draw = () => {
    const dirty = !same(granted, saved);
    const row = (i) => {
      const on = granted.has(i.perm);
      const tag = on && !i.default ? badge(t("access.extra"), "ok") : !on && i.default ? badge(t("access.revoked"), "bad") : "";
      const needs = (data.requires[i.perm] || []).map((p) => t(`perm.${p}`)).join(", ");
      return `
        <label class="access-row${on ? " on" : ""}">
          <span class="access-icon">${icon(ACCESS_ICONS[i.perm] || "check")}</span>
          <span class="access-text">
            <b>${esc(t(`perm.${i.perm}`))} ${tag}</b>
            <small>${esc(t(`perm.${i.perm}.hint`))}${needs ? ` · ${esc(t("access.needs", { list: needs }))}` : ""}</small>
          </span>
          <span class="access-state">${esc(t(on ? "access.can" : "access.cannot"))}</span>
          <input type="checkbox" class="switch" data-perm="${i.perm}"${on ? " checked" : ""}>
        </label>`;
    };

    page.innerHTML = `
      <div class="card access-head">
        ${avatarHtml(name, data.user.avatar_url, "avatar big")}
        <div class="access-who">
          <h2>${esc(name)}</h2>
          <span class="muted">@${esc(data.user.username)} · ${esc(roleLabel("pharmacist"))}${data.user.pharmacy_name ? ` · ${esc(data.user.pharmacy_name)}` : ""}</span>
        </div>
        <div class="access-count"><b>${granted.size}</b><span>${esc(t("access.count", { total: items.length }))}</span></div>
      </div>
      ${data.groups
        .map(
          (g) => `
        <div class="card panel">
          <div class="panel-head">
            <div class="panel-title">
              <div class="panel-icon primary">${icon(ACCESS_GROUPS[g.key] || "tune")}</div>
              <div><h3>${esc(t(`access.group.${g.key}`))}</h3><div class="panel-hint">${esc(
                t("access.groupCount", { n: g.items.filter((i) => granted.has(i.perm)).length, total: g.items.length })
              )}</div></div>
            </div>
          </div>
          <div class="access-list">${g.items.map(row).join("")}</div>
        </div>`
        )
        .join("")}
      <div class="access-bar${dirty ? " dirty" : ""}">
        <button type="button" class="btn" data-access-default${same(granted, defaults) ? " disabled" : ""}>${icon("restart_alt")}${esc(t("access.default"))}</button>
        <span class="access-bar-note">${esc(t(dirty ? "access.unsaved" : "access.saved"))}</span>
        <button type="button" class="btn" data-access-undo${dirty ? "" : " disabled"}>${esc(t("cancel"))}</button>
        <button type="button" class="btn primary" data-access-save${dirty ? "" : " disabled"}>${icon("save")}${esc(t("save"))}</button>
      </div>`;
  };

  page.addEventListener("change", (e) => {
    const box = e.target.closest("[data-perm]");
    if (!box) return;
    if (box.checked) withRequired(box.dataset.perm, granted);
    else withoutDependents(box.dataset.perm, granted);
    draw();
  });
  page.addEventListener("click", async (e) => {
    if (e.target.closest("[data-access-default]")) {
      granted = new Set(defaults);
      draw();
    } else if (e.target.closest("[data-access-undo]")) {
      granted = new Set(saved);
      draw();
    } else if (e.target.closest("[data-access-save]")) {
      const button = e.target.closest("[data-access-save]");
      button.disabled = true;
      try {
        data = await api.put(`/users/${id}/access/`, { permissions: [...granted] });
        saved = new Set(data.groups.flatMap((g) => g.items).filter((i) => i.granted).map((i) => i.perm));
        granted = new Set(saved);
        toast(t("access.done", { name }));
        draw();
      } catch (err) {
        toast(err.message, true);
        button.disabled = false;
      }
    }
  });
  draw();
}

// ---------------------------------------------------------------- Лекарства

// Срок годности остатка: дата и метка (истёк / сколько дней осталось); в подсказке — партии на складе
function expiryCell(m) {
  if (!m.expire_date) return `<span class="muted">—</span>`;
  const midnight = new Date();
  midnight.setHours(0, 0, 0, 0); // местная полночь: today() даёт дату по UTC, в Душанбе до 5 утра это ещё вчера
  const days = Math.round((new Date(`${m.expire_date}T00:00`) - midnight) / 86400000);
  const title = (m.batches || []).map((b) => t("exp.batch", { date: dateOnly(b.expire_date), q: b.quantity })).join("\n");
  const tag =
    m.expire_status === "expired"
      ? badge(t("exp.expired"), "bad")
      : m.expire_status === "soon"
        ? badge(days === 0 ? t("exp.today") : t("exp.days", { n: days }), "warn")
        : "";
  return `<span class="expiry" title="${esc(title)}">${dateOnly(m.expire_date)} ${tag}</span>`;
}

// Страница сроков годности (#/expiry): каждая партия на складе, ближайший срок — первым
const EXPIRY_FILTERS = {
  attention: (b) => b.days_left <= 30,
  expired: (b) => b.days_left < 0,
  soon: (b) => b.days_left >= 0 && b.days_left <= 30,
  quarter: (b) => b.days_left > 30 && b.days_left <= 90,
  all: () => true,
};
let expiryFilter = "attention";

async function renderExpiry() {
  const view = $("#view");
  setActions(
    `<a class="btn" href="#/medicines">${icon("arrow_back")}${esc(t("exp.back"))}</a>${
      can("writeoff.view") ? `<a class="btn danger" href="#/writeoff">${icon("delete_sweep")}${esc(t("nav.writeoff"))}</a>` : ""
    }`
  );
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
  let data;
  try {
    data = await api.get("/medicines/expiry/");
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  const { summary, batches } = data;
  const tile = (key, cls, ic, count) => `
    <button type="button" class="card exp-tile ${cls}" data-exp="${key}">
      <span class="exp-tile-icon">${icon(ic)}</span>
      <span><small>${esc(t(`exp.tile.${key}`))}</small><b>${count.batches}</b><em>${esc(t("exp.units", { q: count.quantity }))}</em></span>
    </button>`;

  view.innerHTML = `<div class="exp-page">
    <div class="exp-tiles">
      ${tile("expired", "bad", "dangerous", summary.expired)}
      ${tile("soon", "warn", "hourglass_bottom", summary.soon)}
      ${tile("quarter", "", "event_upcoming", summary.quarter)}
      ${tile("all", "", "inventory_2", summary.total)}
    </div>
    <div class="card">
      <div class="toolbar">
        <input type="search" data-exp-search placeholder="${esc(t("med.search"))}">
        <div class="exp-filter" role="tablist">${["attention", "expired", "soon", "quarter", "all"]
          .map((k) => `<button type="button" role="tab" data-exp="${k}">${esc(t(`exp.f.${k}`))}<span>${batches.filter(EXPIRY_FILTERS[k]).length}</span></button>`)
          .join("")}</div>
      </div>
      <div data-exp-table></div>
    </div>
  </div>`;
  const page = $(".exp-page", view); // обработчики — на элементе страницы, а не на общем #view

  // срок и «осталось» — сразу после названия: на телефоне они видны без прокрутки таблицы
  const head = [
    t("name"),
    t("med.expiry"),
    t("exp.left"),
    { label: t("exp.batchQty"), cls: "num" },
    { label: t("exp.value"), cls: "num" },
    t("barcode"),
    ...(isAdmin() ? [t("pharmacy")] : []),
  ];
  const left = (b) =>
    b.days_left < 0
      ? badge(t("exp.overdue", { n: -b.days_left }), "bad")
      : b.days_left <= 30
        ? badge(b.days_left === 0 ? t("exp.today") : t("exp.days", { n: b.days_left }), "warn")
        : esc(t("exp.days", { n: b.days_left }));

  const draw = () => {
    $$("[data-exp]", page).forEach((el) => el.classList.toggle("active", el.dataset.exp === expiryFilter));
    const q = normalize($("[data-exp-search]", page).value.trim());
    const rows = batches
      .filter(EXPIRY_FILTERS[expiryFilter])
      .filter((b) => !q || normalize(b.title).includes(q) || (b.barcode || "").includes(q))
      .map((b) => [
        `<b>${esc(b.title)}</b>`,
        dateOnly(b.expire_date),
        left(b),
        b.quantity,
        money(b.price * b.quantity),
        b.barcode ? `<code class="barcode">${esc(b.barcode)}</code>` : "—",
        ...(isAdmin() ? [esc(pharmacyNames[b.pharmacy])] : []),
      ]);
    // новый контейнер на каждую перерисовку — у mountPagedTable свой обработчик кликов
    const box = $("[data-exp-table]", page);
    box.replaceChildren(document.createElement("div"));
    if (!rows.length) box.firstChild.innerHTML = `<p class="muted empty">${esc(t(expiryFilter === "all" ? "noData" : "exp.none"))}</p>`;
    else mountPagedTable(box.firstChild, head, rows);
  };

  page.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-exp]");
    if (!btn) return;
    expiryFilter = btn.dataset.exp;
    draw();
  });
  $("[data-exp-search]", page).addEventListener("input", draw);
  draw();
}

// Списание просроченного (#/writeoff): кнопка на каждый месяц срока (потери и сколько лекарств) → страница месяца; ниже — история
const woMonthLabel = (ym) => monthName(`${ym}-01`);
const woMedicineCount = (m) => new Set(m.items.map((i) => i.medicine_id)).size;

const woDocLink = (w) => `#/writeoff-doc?id=${w.id}`;

// Один документ списания (#/writeoff-doc?id=5): кто, когда, за какой месяц и что списано
async function renderWriteOffDoc(params) {
  const view = $("#view");
  setActions(`<a class="btn" href="#/writeoff">${icon("arrow_back")}${esc(t("wo.toMonths"))}</a>`);
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
  let w;
  try {
    w = await api.get(`/write-offs/${Number(params.get("id")) || 0}/`);
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  const month = w.expire_month ? woMonthLabel(w.expire_month.slice(0, 7)) : t("wo.allMonths");
  const quantity = w.items.reduce((s, i) => s + i.quantity, 0);
  $("#page-title").textContent = t("wo.title", { n: w.write_off_number });

  view.innerHTML = `<div class="exp-page wo-page">
    <div class="exp-tiles wo-tiles">
      ${woTile("bad", "medication", t("wo.tile.written"), new Set(w.items.map((i) => i.medicine)).size, t("exp.units", { q: quantity }))}
      ${woTile("warn", "payments", t("wo.tile.cost"), money(w.total_cost), t("currency"))}
      ${woTile("", "sell", t("wo.tile.price"), money(w.total_price), t("currency"))}
    </div>
    <div class="card panel wo-month">
      <div class="panel-head">
        <div class="panel-title">
          <div class="panel-icon error">${icon("receipt_long")}</div>
          <div><h3>${esc(w.write_off_number)}</h3><div class="panel-hint">${esc(`${t("wo.forMonth")}: ${month}`)}</div></div>
        </div>
      </div>
      <dl class="wo-meta">
        <div><dt>${esc(t("date"))}</dt><dd>${dateTime(w.created_at)}</dd></div>
        <div><dt>${esc(t("wo.user"))}</dt><dd>${esc(w.user_name || "—")}</dd></div>
        <div><dt>${esc(t("wo.forMonth"))}</dt><dd>${esc(month)}</dd></div>
        ${isAdmin() ? `<div><dt>${esc(t("pharmacy"))}</dt><dd>${esc(pharmacyNames[w.pharmacy])}</dd></div>` : ""}
      </dl>
      ${simpleTable(
        [
          t("medicine"),
          t("med.expiry"),
          { label: t("qty"), cls: "num" },
          { label: t("pur.price"), cls: "num" },
          { label: t("wo.cost"), cls: "num" },
        ],
        w.items.map((i) => [`<b>${esc(i.medicine_title)}</b>`, dateOnly(i.expire_date), i.quantity, money(i.cost), money(i.cost * i.quantity)])
      )}
      <p class="total">${esc(t("wo.total", { sum: money(w.total_cost) }))}</p>
    </div>
  </div>`;
}

// Списать месяц (или всё — month пустой) после подтверждения; true — списано
async function writeOffMonth(month, target) {
  const text = month
    ? t("wo.confirmMonth", { month: woMonthLabel(month), q: target.quantity, sum: money(target.cost) })
    : t("wo.confirmAll", { q: target.quantity, sum: money(target.cost) });
  if (!confirmAction(text)) return null;
  const w = await api.post("/write-offs/", { month: month || null });
  toast(t("wo.done", { n: w.write_off_number, q: w.items.reduce((s, i) => s + i.quantity, 0) }));
  return w;
}

const woTile = (cls, ic, label, value, foot) => `
  <div class="card exp-tile static ${cls}">
    <span class="exp-tile-icon">${icon(ic)}</span>
    <span><small>${esc(label)}</small><b>${value}</b><em>${esc(foot)}</em></span>
  </div>`;

async function renderWriteOff() {
  const view = $("#view");
  setActions(`<a class="btn" href="#/expiry">${icon("arrow_back")}${esc(t("wo.back"))}</a>`);
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
  let pending, history;
  try {
    [pending, history] = await Promise.all([api.get("/write-offs/pending/"), api.getAll("/write-offs/")]);
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  const manage = can("writeoff.create");
  const { months } = pending;

  view.innerHTML = `<div class="exp-page wo-page">
    <div class="exp-tiles wo-tiles" data-wo-tiles></div>
    <div class="card">
      <div class="panel-head wo-head">
        <div class="panel-title">
          <div class="panel-icon error">${icon("event_busy")}</div>
          <div><h3>${esc(t("wo.months"))}</h3><div class="panel-hint">${esc(t("wo.monthsHint"))}</div></div>
        </div>
        ${monthPickerHtml()}
      </div>
      <div data-wo-months></div>
    </div>
    <div class="card panel">
      <div class="panel-head">
        <div class="panel-title">
          <div class="panel-icon primary">${icon("history")}</div>
          <div><h3>${esc(t("wo.history"))}</h3><div class="panel-hint">${esc(t("wo.historyHint"))}</div></div>
        </div>
      </div>
      <div data-wo-history></div>
    </div>
  </div>`;
  const page = $(".wo-page", view);
  const picker = $("[data-month-picker]", page);
  setupMonthPicker(picker);
  const draw = () => {
    const from = $('[data-filter="month_from"]', picker).value;
    const to = $('[data-filter="month_to"]', picker).value;
    const inRange = (ym) => !from || (ym >= from && ym <= to);
    const shown = months.filter((m) => inRange(m.month));

    const sum = (key) => shown.reduce((s, m) => s + Number(m[key]), 0);
    $("[data-wo-tiles]", page).innerHTML = `
      ${woTile("bad", "dangerous", t("wo.tile.medicines"), shown.reduce((s, m) => s + woMedicineCount(m), 0), t("exp.units", { q: sum("quantity") }))}
      ${woTile("warn", "payments", t("wo.tile.cost"), money(sum("cost")), t("currency"))}
      ${woTile("", "sell", t("wo.tile.price"), money(sum("price")), t("currency"))}`;

    $("[data-wo-months]", page).innerHTML = shown.length
      ? `<div class="report-tiles wo-months">${shown
          .map(
            (m, i) => `
            <a class="card report-tile wo-month-tile" href="#/writeoff-month?month=${m.month}" style="animation-delay:${i * 40}ms">
              <span class="report-tile-icon">${icon("calendar_month")}</span>
              <span class="report-tile-text">
                <b>${esc(woMonthLabel(m.month))}</b>
                <span>${esc(t("wo.monthMeta", { n: woMedicineCount(m), q: m.quantity }))}</span>
                <strong class="wo-loss">${esc(t("wo.loss", { sum: money(m.cost) }))}</strong>
              </span>
              ${icon("chevron_right")}
            </a>`
          )
          .join("")}</div>
          ${
            manage && !from
              ? `<div class="wo-all"><span class="muted">${esc(t("wo.allHint"))}</span><button type="button" class="btn primary danger-fill" data-wo-all>${icon("delete_forever")}${esc(t("wo.all"))}</button></div>`
              : ""
          }`
      : `<p class="muted empty">${esc(t(from ? "wo.nothingPeriod" : "wo.nothing"))}</p>`;

    // история: списания, чей месяц срока попал в период («всё сразу» — только без фильтра)
    const rows = history.filter((w) => (w.expire_month ? inRange(w.expire_month.slice(0, 7)) : !from));
    const box = $("[data-wo-history]", page);
    box.replaceChildren(document.createElement("div"));
    if (!rows.length) box.firstChild.innerHTML = `<p class="muted empty">${esc(t("wo.noHistory"))}</p>`;
    else
      mountPagedTable(
        box.firstChild,
        [
          t("number"),
          t("date"),
          t("wo.forMonth"),
          t("wo.user"),
          { label: t("pur.items"), cls: "num" },
          { label: t("wo.cost"), cls: "num" },
          ...(isAdmin() ? [t("pharmacy")] : []),
          "",
        ],
        rows.map((w) => [
          `<b>${esc(w.write_off_number)}</b>`,
          dateTime(w.created_at),
          esc(w.expire_month ? woMonthLabel(w.expire_month.slice(0, 7)) : t("wo.allMonths")),
          esc(w.user_name || "—"),
          w.items.length,
          money(w.total_cost),
          ...(isAdmin() ? [esc(pharmacyNames[w.pharmacy])] : []),
          `<a class="btn sm" href="${woDocLink(w)}">${esc(t("wo.open"))}${icon("chevron_right")}</a>`,
        ])
      );
  };

  $('[data-filter="month_from"]', picker).addEventListener("input", draw);
  page.addEventListener("click", async (e) => {
    const all = e.target.closest("[data-wo-all]");
    if (!all) return;
    all.disabled = true;
    try {
      const w = await writeOffMonth("", pending.total);
      if (!w) return (all.disabled = false);
      location.hash = woDocLink(w); // сразу открываем созданный документ
    } catch (err) {
      toast(err.message, true);
      all.disabled = false;
    }
  });
  draw();
}

// Один месяц срока (#/writeoff-month?month=2026-09): лекарства и кнопка «Списать»
async function renderWriteOffMonth(params) {
  const view = $("#view");
  const month = params.get("month") || "";
  setActions(`<a class="btn" href="#/writeoff">${icon("arrow_back")}${esc(t("wo.toMonths"))}</a>`);
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
  let pending;
  try {
    pending = await api.get("/write-offs/pending/");
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  if (/^\d{4}-\d{2}$/.test(month)) $("#page-title").textContent = `${t("nav.writeoff")}: ${woMonthLabel(month)}`;
  const m = pending.months.find((x) => x.month === month);
  if (!m) {
    view.innerHTML = `<div class="card empty">${esc(t("wo.monthEmpty"))}</div>`;
    return;
  }
  const manage = can("writeoff.create");

  view.innerHTML = `<div class="exp-page wo-page">
    <div class="exp-tiles wo-tiles">
      ${woTile("bad", "medication", t("wo.tile.medicines"), woMedicineCount(m), t("exp.units", { q: m.quantity }))}
      ${woTile("warn", "payments", t("wo.tile.cost"), money(m.cost), t("currency"))}
      ${woTile("", "sell", t("wo.tile.price"), money(m.price), t("currency"))}
    </div>
    <div class="card panel wo-month">
      <div class="panel-head">
        <div class="panel-title">
          <div class="panel-icon error">${icon("event_busy")}</div>
          <div><h3>${esc(woMonthLabel(m.month))}</h3><div class="panel-hint">${esc(t("wo.monthMeta", { n: woMedicineCount(m), q: m.quantity }))}</div></div>
        </div>
        ${manage ? `<button type="button" class="btn primary danger-fill" data-wo-month>${icon("delete_sweep")}${esc(t("wo.month"))}</button>` : ""}
      </div>
      ${simpleTable(
        [
          t("name"),
          t("med.expiry"),
          t("exp.left"),
          { label: t("qty"), cls: "num" },
          { label: t("pur.price"), cls: "num" },
          { label: t("wo.cost"), cls: "num" },
          t("barcode"),
          ...(isAdmin() ? [t("pharmacy")] : []),
        ],
        m.items.map((i) => [
          `<b>${esc(i.title)}</b>`,
          dateOnly(i.expire_date),
          badge(t("exp.overdue", { n: i.days_overdue }), "bad"),
          i.quantity,
          money(i.cost),
          money(i.cost * i.quantity),
          i.barcode ? `<code class="barcode">${esc(i.barcode)}</code>` : "—",
          ...(isAdmin() ? [esc(pharmacyNames[i.pharmacy])] : []),
        ])
      )}
      <p class="total">${esc(t("wo.total", { sum: money(m.cost) }))}</p>
    </div>
  </div>`;

  $("[data-wo-month]", view)?.addEventListener("click", async (e) => {
    const btn = e.currentTarget;
    btn.disabled = true;
    try {
      const w = await writeOffMonth(m.month, m);
      if (!w) return (btn.disabled = false);
      location.hash = woDocLink(w); // сразу открываем созданный документ
    } catch (err) {
      toast(err.message, true);
      btn.disabled = false;
    }
  });
}

function renderMedicines() {
  const manage = can("medicine.manage");
  const fields = medicineFields();
  const save = (row) => async (data) => {
    if (row) await api.patch(`/medicines/${row.id}/`, data);
    else await api.post("/medicines/", data);
    toast(t("saved"));
    list.reload();
  };

  // кнопка страницы сроков годности — всем сотрудникам; «+ Лекарство» — директору
  setActions(
    `<a class="btn" href="#/expiry">${icon("event_busy")}${esc(t("nav.expiry"))}</a>${
      can("writeoff.view") ? `<a class="btn danger" href="#/writeoff">${icon("delete_sweep")}${esc(t("nav.writeoff"))}</a>` : ""
    }${manage ? addButton(t("med.add")) : ""}`,
    manage ? { add: () => formModal({ title: t("med.new"), fields, submit: save(null) }) } : {}
  );

  const columns = [
    {
      label: t("name"),
      html: (r) =>
        `<b>${esc(r.title)}</b>${r.blisters > 1 ? `<span class="med-blisters">${esc(t("med.perPack", { n: r.blisters, price: money(r.blister_price) }))}</span>` : ""}`,
    },
    { label: t("barcode"), html: (r) => (r.barcode ? `<code class="barcode">${esc(r.barcode)}</code>` : "—") },
    { label: t("price"), html: (r) => money(r.price), cls: "num" },
    { label: t("stock"), html: stockCell, cls: "num" },
    { label: t("med.expiry"), html: expiryCell },
    { label: t("med.added"), html: (r) => dateOnly(r.created_at) },
  ];
  if (isAdmin()) columns.splice(1, 0, { label: t("pharmacy"), html: (r) => esc(pharmacyNames[r.pharmacy]) });

  const list = listView({
    endpoint: "/medicines/",
    // без поиска — только то, что есть на складе; при поиске находятся и закончившиеся
    baseParams: (params) => (params.search?.trim() ? {} : { in_stock: 1 }),
    filters: [
      { name: "search", placeholder: t("med.search") },
      {
        name: "ordering",
        type: "select",
        placeholder: t("med.sortNew"),
        options: [
          ["title", t("med.sortTitle")],
          ["price", t("med.priceAsc")],
          ["-price", t("med.priceDesc")],
          ["quantity", t("med.stockAsc")],
          ["-quantity", t("med.stockDesc")],
          ["expiry", t("med.sortExpiry")],
        ],
      },
      {
        name: "expiry",
        type: "select",
        placeholder: t("exp.all"),
        options: [
          ["attention", t("exp.attention")],
          ["soon", t("exp.soon")],
          ["expired", t("exp.expired")],
        ],
      },
    ],
    columns,
    actions: manage ? editButtons : null,
    onAction: (act, row) => {
      if (act === "edit") formModal({ title: row.title, fields, initial: row, submit: save(row) });
      if (act === "delete") remove(`/medicines/${row.id}/`, row.title, list);
    },
  });
}

// ---------------------------------------------------------------- Поставщики

function renderSuppliers() {
  const manage = can("supplier.manage");
  const fields = [
    { name: "name", label: t("name"), required: true },
    { name: "phone_number", label: t("phone") },
  ];
  const save = (row) => async (data) => {
    if (row) await api.patch(`/suppliers/${row.id}/`, data);
    else await api.post("/suppliers/", data);
    toast(t("saved"));
    list.reload();
  };

  if (manage) {
    setActions(addButton(t("sup.add")), {
      add: () => formModal({ title: t("sup.new"), fields, submit: save(null) }),
    });
  }

  const columns = [
    { label: t("name"), html: (r) => `<b>${esc(r.name)}</b>` },
    { label: t("phone"), html: (r) => esc(r.phone_number) },
  ];
  if (isAdmin()) columns.push({ label: t("pharmacy"), html: (r) => esc(pharmacyNames[r.pharmacy]) });

  const list = listView({
    endpoint: "/suppliers/",
    filters: [{ name: "search", placeholder: t("sup.search") }],
    columns,
    actions: manage ? editButtons : null,
    onAction: (act, row) => {
      if (act === "edit") formModal({ title: row.name, fields, initial: row, submit: save(row) });
      if (act === "delete") remove(`/suppliers/${row.id}/`, row.name, list);
    },
  });
}

// ---------------------------------------------------------------- Закупки

function renderPurchases() {
  if (can("purchase.create")) {
    setActions(addButton(t("pur.add")), { add: () => (location.hash = "#/purchase") });
  }

  const columns = [
    { label: t("number"), html: (r) => `<b>${esc(r.purchase_number)}</b>` },
    { label: t("date"), html: (r) => dateTime(r.created_at) },
    { label: t("supplier"), html: (r) => esc(r.supplier_name) },
    { label: t("pur.items"), html: (r) => r.items.length, cls: "num" },
    { label: t("amount"), html: (r) => money(r.total_price), cls: "num" },
  ];
  if (isAdmin()) columns.splice(2, 0, { label: t("pharmacy"), html: (r) => esc(pharmacyNames[r.pharmacy]) });

  const list = listView({
    endpoint: "/purchases/",
    filters: [{ name: "search", placeholder: t("pur.search") }, monthFilter()],
    columns,
    onRowClick: (r) =>
      openModal(
        t("pur.title", { n: r.purchase_number }),
        `<p class="muted">${dateTime(r.created_at)} · ${esc(t("pur.supplierLine", { name: r.supplier_name }))}</p>
         ${simpleTable(
           [t("medicine"), t("qty"), t("pur.price"), t("expireDate"), t("amount")],
           r.items.map((i) => [esc(i.medicine_title), i.quantity, money(i.price), dateOnly(i.expire_date), money(i.price * i.quantity)])
         )}
         <p class="total">${esc(t("grandTotal", { sum: money(r.total_price) }))}</p>`
      ),
  });
}

// ---------------------------------------------------------------- Поиск лекарства и корзина (продажа и закупка)

const medicineFields = () => [
  { name: "title", label: t("name"), required: true },
  { name: "barcode", label: t("barcode"), hint: t("barcode.hint"), scan: true },
  { name: "price", label: t("med.salePrice"), type: "number", step: "0.01", min: "0.01", required: true },
  { name: "blisters", label: t("med.blisters"), hint: t("med.blisters.hint"), type: "number", step: "1", min: "1", max: "100", default: 1, required: true },
];

// Блистеры (пластинки): упаковка №30 = 3 блистера, продавать можно по одному
const blisterTotal = (m) => m.quantity * m.blisters + (m.open_blisters || 0); // всего блистеров на складе
const inStock = (m) => m.quantity > 0 || m.open_blisters > 0;
// «12 уп. + 2 бл.» — остаток с блистерами вскрытой упаковки
function stockText(m) {
  const packs = t("unit.packN", { n: m.quantity });
  return m.open_blisters ? `${packs} + ${t("unit.blisterN", { n: m.open_blisters })}` : packs;
}
function stockCell(m) {
  if (!m.open_blisters) return stockBadge(m.quantity);
  return `${m.quantity ? stockBadge(m.quantity) : ""} ${badge(`+${t("unit.blisterN", { n: m.open_blisters })}`, "warn")}`;
}

const normalize = (s) => String(s).toLowerCase().replace(/ё/g, "е");

// Совпавшая с запросом часть названия подсвечивается
function highlight(text, query) {
  const i = normalize(text).indexOf(normalize(query));
  if (i < 0 || !query) return esc(text);
  return `${esc(text.slice(0, i))}<mark>${esc(text.slice(i, i + query.length))}</mark>${esc(text.slice(i + query.length))}`;
}

const pickerHtml = () => `
  <div class="picker">
    <label class="scan">${icon("search")}<input data-picker placeholder="${esc(t("picker.placeholder"))}" autocomplete="off"></label>
    <div class="picker-list hidden"></div>
  </div>`;

/**
 * Поиск лекарства по названию или штрих-коду с выпадающим списком похожих.
 * Сканер вводит код и жмёт Enter — лекарство с точным штрих-кодом добавляется сразу.
 * describe(m) — подпись под названием (экранированный текст); isDisabled(m) — нельзя выбрать;
 * onCreate(query) — если задан и точного совпадения нет, в списке есть «Создать лекарство».
 * actions(m) — доп. кнопки в пункте ([data-pick-unit="…"]): выбор сразу с единицей, onPick(m, unit).
 */
function setupPicker(
  root,
  {
    medicines,
    describe,
    isDisabled = () => false,
    onPick,
    onCreate,
    // для других списков (клиенты): что показывать, где искать, текст «создать», ставить ли фокус сразу
    label = (m) => m.title,
    matches = (m, q, query) => normalize(m.title).includes(q) || (m.barcode && m.barcode.startsWith(query)),
    createText = (q) => t("picker.create", { q }),
    autofocus = true,
    actions = () => "",
  }
) {
  const input = $("[data-picker]", root);
  const list = $(".picker-list", root);
  let found = [];
  let active = -1;

  const close = () => {
    list.classList.add("hidden");
    active = -1;
  };
  const choose = (m, unit) => {
    if (!m || isDisabled(m)) return;
    onPick(m, unit);
    input.value = "";
    close();
    input.focus();
  };
  const create = () => {
    const query = input.value.trim();
    input.value = "";
    close();
    onCreate(query);
  };

  const render = () => {
    const query = input.value.trim();
    if (!query) return close();
    const q = normalize(query);
    found = medicines.filter((m) => matches(m, q, query)).slice(0, 8);
    active = found.findIndex((m) => !isDisabled(m));
    const exact = medicines.some((m) => normalize(label(m)) === q || (m.barcode && m.barcode === query));
    list.innerHTML =
      found
        .map(
          (m, i) => `
        <button type="button" class="picker-item${i === active ? " active" : ""}" data-i="${i}"${isDisabled(m) ? " disabled" : ""}>
          <span class="picker-title">${highlight(label(m), query)}</span>
          <span class="picker-meta">${describe(m)}</span>
          ${isDisabled(m) ? "" : actions(m)}
        </button>`
        )
        .join("") +
      (found.length ? "" : `<div class="picker-empty">${esc(t("picker.none", { q: query }))}</div>`) +
      (onCreate && !exact
        ? `<button type="button" class="picker-create" data-create>${icon("add_circle")}<span>${esc(createText(query))}</span></button>`
        : "");
    list.classList.remove("hidden");
  };

  const move = (step) => {
    const enabled = $$(".picker-item:not([disabled])", list);
    if (!enabled.length) return;
    const pos = enabled.findIndex((el) => Number(el.dataset.i) === active);
    const next = pos < 0 ? enabled[step > 0 ? 0 : enabled.length - 1] : enabled[(pos + step + enabled.length) % enabled.length];
    active = Number(next.dataset.i);
    $$(".picker-item", list).forEach((el) => el.classList.toggle("active", Number(el.dataset.i) === active));
    next.scrollIntoView({ block: "nearest" });
  };

  input.addEventListener("input", render);
  input.addEventListener("focus", render);
  input.addEventListener("blur", close);
  input.addEventListener("keydown", (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      return move(e.key === "ArrowDown" ? 1 : -1);
    }
    if (e.key === "Escape") return close();
    if (e.key !== "Enter") return;
    e.preventDefault();
    const query = input.value.trim();
    if (!query) return;

    // сканер: точный штрих-код, даже если список ещё не обновился
    const byBarcode = medicines.find((m) => m.barcode && m.barcode === query);
    if (byBarcode) {
      if (isDisabled(byBarcode)) toast(`${label(byBarcode)}: ${t("picker.outOfStock")}`, true);
      else choose(byBarcode);
      return;
    }
    if (list.classList.contains("hidden")) render(); // иначе сохраняем выбор, сделанный стрелками
    if (found[active]) choose(found[active]);
    else if (onCreate) create();
    else toast(t("picker.none", { q: query }), true);
  });
  // mousedown, а не click: иначе поле теряет фокус и список закрывается раньше клика
  list.addEventListener("mousedown", (e) => {
    e.preventDefault();
    const item = e.target.closest("[data-i]");
    if (item) choose(found[item.dataset.i], e.target.closest("[data-pick-unit]")?.dataset.pickUnit);
    else if (e.target.closest("[data-create]")) create();
  });
  if (autofocus) setTimeout(() => input.focus(), 50);
}

/**
 * Корзина: одно лекарство — одна строка (rowHtml(m)); повторное добавление увеличивает количество.
 * maxQty(m, row) — сколько можно добавить (для продажи — остаток в выбранных единицах).
 */
function setupCart(form, { rowHtml, onChange, maxQty = () => Infinity }) {
  const items = $(".items", form);
  const empty = $(".cart-empty", form);
  const sync = () => {
    empty.classList.toggle("hidden", items.children.length > 0);
    onChange();
  };
  items.addEventListener("click", (e) => {
    if (!e.target.closest("[data-remove]")) return;
    e.target.closest(".item-row").remove();
    sync();
  });

  return {
    add(m, extra) {
      let row = $(`.item-row[data-id="${m.id}"]`, items);
      if (row) {
        const quantity = $("[name=quantity]", row);
        if (Number(quantity.value) + 1 > maxQty(m, row)) toast(t("cart.onlyInStock", { q: maxQty(m, row) }), true);
        else quantity.value = Number(quantity.value || 0) + 1;
      } else {
        items.insertAdjacentHTML("beforeend", rowHtml(m, extra));
        row = items.lastElementChild;
      }
      replay(row, "flash");
      sync();
      return row;
    },
    rows: () => $$(".item-row", items),
  };
}

const cartHtml = () => `
  <div class="items"></div>
  <div class="cart-empty">${icon("manage_search")}<span>${esc(t("cart.empty"))}</span></div>`;

const removeButton = () =>
  `<button type="button" class="icon-btn remove" data-remove title="${esc(t("delete"))}">${icon("close")}</button>`;

// Отдельная страница закупки (#/purchase): поиск → строки с ценой и сроком годности → итог справа
async function renderNewPurchase() {
  const view = $("#view");
  setActions(`<a class="btn" href="#/purchases">${icon("arrow_back")}${esc(t("pur.back"))}</a>`);
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;

  let suppliers, medicines;
  try {
    [suppliers, medicines] = await Promise.all([api.getAll("/suppliers/"), api.getAll("/medicines/", { ordering: "title" })]);
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  if (!suppliers.length) {
    view.innerHTML = `<div class="card empty">${esc(t("pur.needSupplier"))}</div>`;
    return;
  }

  const currency = esc(t("currency"));
  const describe = (m) =>
    esc([t("pur.retail", { p: money(m.price) }), t("picker.inStock", { q: m.quantity }), m.barcode].filter(Boolean).join(" · "));
  const rowHtml = (m) => `
    <div class="item-row purchase" data-id="${m.id}">
      <div class="item-name"><b>${esc(m.title)}</b><span>${describe(m)}</span></div>
      <label>${esc(t("qty"))}<input name="quantity" type="number" min="1" value="1" required></label>
      <label>${esc(t("pur.price"))}<input name="price" type="number" min="0" step="0.01" required></label>
      <label>${esc(t("med.salePrice"))}<input name="sale_price" type="number" min="0.01" step="0.01" value="${esc(m.price)}" required></label>
      <label>${esc(t("expireDate"))}<input name="expire_date" type="date" min="${today()}" required></label>
      <div class="line-sum" data-line>${money(0)}</div>
      ${removeButton()}
      <div class="row-note" data-note></div>
    </div>`;

  view.innerHTML = `
    <form class="pos">
      <div class="card pos-main">
        ${pickerHtml()}
        ${cartHtml()}
      </div>
      <aside class="card pos-summary">
        <h3>${esc(t("order.summary"))}</h3>
        <label>${esc(t("supplier"))}<select name="supplier" required>${options(suppliers.map((s) => [s.id, s.name]), "", { empty: "—" })}</select></label>
        <div class="sum-row"><span>${esc(t("pur.items"))}</span><b data-count>0</b></div>
        <div class="sum-total"><span>${esc(t("pur.total"))}</span><b><span data-total>${money(0)}</span> ${currency}</b></div>
        <div class="error"></div>
        <button type="submit" class="btn primary big">${icon("inventory")}${esc(t("pur.submit"))}</button>
        <a class="btn" href="#/purchases">${esc(t("cancel"))}</a>
      </aside>
    </form>`;
  const form = $("form", view);

  const recalc = () => {
    const rows = cart.rows();
    const total = rows.reduce((sum, row) => {
      const cost = Number($("[name=price]", row).value || 0);
      const sale = Number($("[name=sale_price]", row).value || 0);
      const line = Number($("[name=quantity]", row).value || 0) * cost;
      $("[data-line]", row).textContent = money(line);

      // наценка к цене закупки; продажа дешевле закупки — предупреждение
      const note = $("[data-note]", row);
      const loss = cost > 0 && sale > 0 && sale < cost;
      note.textContent = !cost || !sale ? "" : loss ? t("pur.belowCost") : t("pur.markup", { p: Math.round(((sale - cost) / cost) * 100) });
      note.classList.toggle("bad", loss);
      return sum + line;
    }, 0);
    $("[data-count]", form).textContent = rows.length;
    $("[data-total]", form).textContent = money(total);
  };

  const cart = setupCart(form, { rowHtml, onChange: recalc });
  const addAndFocus = (m) => $("[name=price]", cart.add(m))?.focus();

  setupPicker(form, {
    medicines,
    describe,
    onPick: (m) => cart.add(m),
    // нового лекарства нет в списке — создаём его тут же (директор) и сразу добавляем в закупку
    onCreate: can("medicine.manage")
      ? (query) =>
          formModal({
            title: t("med.new"),
            fields: medicineFields(),
            initial: /^\d{6,}$/.test(query) ? { barcode: query } : { title: query },
            submit: async (data) => {
              const medicine = await api.post("/medicines/", data);
              medicines.push(medicine);
              toast(t("med.created", { title: medicine.title }));
              setTimeout(() => addAndFocus(medicine));
            },
          })
      : null,
  });
  form.addEventListener("input", recalc);

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const rows = cart.rows();
    if (!rows.length) return ($(".error", form).textContent = t("cart.required"));
    const payload = {
      supplier: Number(form.elements.supplier.value),
      items: rows.map((row) => ({
        medicine: Number(row.dataset.id),
        quantity: Number($("[name=quantity]", row).value),
        price: $("[name=price]", row).value,
        sale_price: $("[name=sale_price]", row).value, // новая цена продажи лекарства
        expire_date: $("[name=expire_date]", row).value,
      })),
    };
    const button = $("button[type=submit]", form);
    button.disabled = true;
    try {
      await api.post("/purchases/", payload);
      toast(t("pur.done"));
      location.hash = "#/purchases";
    } catch (err) {
      $(".error", form).textContent = err.message;
      button.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- Продажи

const ORDER_STATUS = { completed: "ok", cancelled: "bad" };
const statusBadge = (status) => badge(t(`order.${status}`), ORDER_STATUS[status]);

function renderOrders() {
  if (can("order.create")) {
    setActions(addButton(t("order.add")), { add: () => (location.hash = "#/sale") });
  }

  const columns = [
    { label: t("number"), html: (r) => `<b>${esc(r.order_number)}</b>` },
    { label: t("date"), html: (r) => dateTime(r.created_at) },
    { label: t("order.seller"), html: (r) => esc(r.seller) },
    { label: t("order.discount"), html: (r) => `${Number(r.discount)}%`, cls: "num" },
    { label: t("amount"), html: (r) => money(r.total_price), cls: "num" },
    { label: t("status"), html: (r) => statusBadge(r.status) },
  ];
  if (isAdmin()) columns.splice(2, 0, { label: t("pharmacy"), html: (r) => esc(pharmacyNames[r.pharmacy]) });

  listView({
    endpoint: "/orders/",
    filters: [
      { name: "search", placeholder: t("order.search") },
      monthFilter(),
      { name: "status", type: "select", placeholder: t("order.allStatuses"), options: Object.keys(ORDER_STATUS).map((k) => [k, t(`order.${k}`)]) },
    ],
    columns,
    onRowClick: (r) => (location.hash = orderLink(r)),
  });
}

const orderLink = (o) => `#/order?id=${o.id}`;

// Одна продажа (#/order?id=5): что продано, кем и когда; директор может изменить или отменить
async function renderOrderDoc(params) {
  const view = $("#view");
  const back = `<a class="btn" href="#/orders">${icon("arrow_back")}${esc(t("order.back"))}</a>`;
  setActions(back);
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
  let order;
  try {
    order = await api.get(`/orders/${Number(params.get("id")) || 0}/`);
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  if (can("order.cancel") && order.status === "completed") {
    setActions(
      `${back}
       <a class="btn primary" href="#/sale?id=${order.id}">${icon("edit")}${esc(t("edit"))}</a>
       <button class="btn danger" id="cancel-order">${esc(t("order.cancel"))}</button>`,
      {
        "cancel-order": async () => {
          if (!confirmAction(t("order.confirmCancel"))) return;
          try {
            await api.post(`/orders/${order.id}/cancel/`);
            toast(t("order.cancelledToast"));
            route();
          } catch (err) {
            toast(err.message, true);
          }
        },
      }
    );
  }
  $("#page-title").textContent = t("order.title", { n: order.order_number });

  view.innerHTML = `<div class="card panel wo-month">
    <div class="panel-head">
      <div class="panel-title">
        <div class="panel-icon">${icon("receipt_long")}</div>
        <div><h3>${esc(order.order_number)}</h3><div class="panel-hint">${statusBadge(order.status)}</div></div>
      </div>
    </div>
    <dl class="wo-meta">
      <div><dt>${esc(t("date"))}</dt><dd>${dateTime(order.created_at)}</dd></div>
      <div><dt>${esc(t("order.seller"))}</dt><dd>${esc(order.seller || "—")}</dd></div>
      <div><dt>${esc(t("order.discount"))}</dt><dd>${Number(order.discount)}%</dd></div>
      ${isAdmin() ? `<div><dt>${esc(t("pharmacy"))}</dt><dd>${esc(pharmacyNames[order.pharmacy])}</dd></div>` : ""}
    </dl>
    ${simpleTable(
      [t("medicine"), { label: t("qty"), cls: "num" }, { label: t("price"), cls: "num" }, { label: t("amount"), cls: "num" }],
      order.items.map((i) => [
        `<b>${esc(i.medicine_title)}</b>`,
        i.unit === "blister" ? esc(t("unit.blisterN", { n: i.quantity })) : i.quantity,
        money(i.price),
        money(i.price * i.quantity),
      ])
    )}
    <p class="total">${esc(t("order.discountTotal", { d: Number(order.discount), sum: money(order.total_price) }))}</p>
  </div>`;
}

// Отдельная страница кассы (#/sale): поиск → строки → итог и кнопка «Продать» справа.
// #/sale?id=5 — та же касса для изменения продажи 5
async function renderNewSale(params) {
  const view = $("#view");
  const editId = Number(params.get("id")) || 0;
  const backHref = editId ? `#/order?id=${editId}` : "#/orders";
  setActions(`<a class="btn" href="${backHref}">${icon("arrow_back")}${esc(t(editId ? "order.backToSale" : "order.back"))}</a>`);
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;

  let medicines, editing;
  try {
    [medicines, editing] = await Promise.all([
      api.getAll("/medicines/", { ordering: "title" }),
      editId ? api.get(`/orders/${editId}/`) : null,
    ]);
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  if (editing) {
    $("#page-title").textContent = t("order.editTitle", { n: editing.order_number });
    // проданное в этой продаже снова доступно: возвращаем на склад так же, как сервер (give_back)
    editing.items.forEach((i) => {
      const m = medicines.find((x) => x.id === i.medicine);
      if (!m) return;
      if (i.unit === "pack") m.quantity += i.quantity;
      else {
        const open = (m.open_blisters || 0) + i.quantity;
        m.quantity += Math.floor(open / m.blisters);
        m.open_blisters = open % m.blisters;
      }
    });
  }
  if (!medicines.some(inStock)) {
    view.innerHTML = `<div class="card empty">${esc(t("order.noStock"))}</div>`;
    return;
  }

  const currency = esc(t("currency"));
  const byId = Object.fromEntries(medicines.map((m) => [m.id, m]));
  const describe = (m) =>
    esc(
      [
        `${money(m.price)} ${t("currency")}`,
        m.blisters > 1 && t("order.blisterPrice", { price: money(m.blister_price) }),
        inStock(m) ? t("picker.inStock", { q: stockText(m) }) : t("picker.outOfStock"),
        m.barcode,
      ]
        .filter(Boolean)
        .join(" · ")
    );
  // единица строки: упаковка или блистер (если упаковку делят); целых упаковок нет — сразу блистеры
  const unitOf = (row) => $("[name=unit]", row)?.value || "pack";
  const perUnit = (m, unit) => t("order.perUnit", { price: money(unitPrice(m, unit)), unit: t(`unit.${unit}`).toLowerCase() });
  const unitSeg = (unit) => `
    <span class="unit-seg" role="radiogroup" title="${esc(t("order.unit"))}">
      <input type="hidden" name="unit" value="${unit}">
      ${["pack", "blister"]
        .map((u) => `<button type="button" role="radio" data-unit="${u}" class="${u === unit ? "active" : ""}" aria-checked="${u === unit}">${esc(t(`unit.${u}`))}</button>`)
        .join("")}
    </span>`;
  const setUnit = (row, unit) => {
    const m = byId[row.dataset.id];
    $("[name=unit]", row).value = unit;
    $$("[data-unit]", row).forEach((b) => {
      b.classList.toggle("active", b.dataset.unit === unit);
      b.setAttribute("aria-checked", b.dataset.unit === unit);
    });
    const quantity = $("[name=quantity]", row);
    quantity.max = maxFor(m, unit);
    if (Number(quantity.value) > maxFor(m, unit)) quantity.value = maxFor(m, unit);
    $(".item-unit-price", row).textContent = perUnit(m, unit);
  };
  const unitPrice = (m, unit) => Number(unit === "blister" ? m.blister_price : m.price);
  const maxFor = (m, unit) => (unit === "blister" ? blisterTotal(m) : m.quantity);
  const rowHtml = (m, wanted) => {
    const blisters = m.blisters > 1;
    const unit = blisters && (wanted === "blister" || m.quantity === 0) ? "blister" : "pack";
    return `
    <div class="item-row order${blisters ? " has-units" : ""}" data-id="${m.id}">
      <div class="item-name"><b>${esc(m.title)}</b><span>${describe(m)}</span>${
        blisters ? `<span class="item-unit-price">${esc(perUnit(m, unit))}</span>${unitSeg(unit)}` : ""
      }</div>
      <label>${esc(t("qty"))}<input name="quantity" type="number" min="1" max="${maxFor(m, unit)}" value="1" required></label>
      <div class="line-sum" data-line>${money(unitPrice(m, unit))}</div>
      ${removeButton()}
    </div>`;
  };

  view.innerHTML = `
    <form class="pos">
      <div class="card pos-main">
        ${pickerHtml()}
        ${cartHtml()}
      </div>
      <aside class="card pos-summary">
        <h3>${esc(t("order.summary"))}</h3>
        <div class="sum-row"><span>${esc(t("order.subtotal"))}</span><b data-subtotal>${money(0)}</b></div>
        <label>${esc(t("order.discountPct"))}<input name="discount" type="number" min="0" max="100" step="0.01" value="0"></label>
        <div class="sum-row"><span>${esc(t("order.discount"))}</span><b data-discount>${money(0)}</b></div>
        <div class="sum-total"><span>${esc(t("order.toPay"))}</span><b><span data-total>${money(0)}</span> ${currency}</b></div>
        <div class="error"></div>
        <button type="submit" class="btn primary big">${icon(editing ? "save" : "point_of_sale")}${esc(t(editing ? "save" : "order.sell"))}</button>
        <a class="btn" href="${backHref}">${esc(t("cancel"))}</a>
      </aside>
    </form>`;
  const form = $("form", view);

  const recalc = () => {
    const subtotal = cart.rows().reduce((sum, row) => {
      const line = unitPrice(byId[row.dataset.id], unitOf(row)) * Number($("[name=quantity]", row).value || 0);
      $("[data-line]", row).textContent = money(line);
      return sum + line;
    }, 0);
    const discount = Number(form.elements.discount.value || 0);
    const discountSum = (subtotal * discount) / 100;
    $("[data-subtotal]", form).textContent = money(subtotal);
    $("[data-discount]", form).textContent = money(discountSum ? -discountSum : 0); // без «-0,00»
    $("[data-total]", form).textContent = money(subtotal - discountSum);
  };

  const cart = setupCart(form, { rowHtml, onChange: recalc, maxQty: (m, row) => maxFor(m, unitOf(row)) });
  setupPicker(form, {
    medicines,
    describe,
    isDisabled: (m) => !inStock(m),
    // упаковку делят — в списке сразу две кнопки: добавить упаковкой или блистером
    actions: (m) =>
      m.blisters > 1
        ? `<span class="pick-units">${m.quantity > 0 ? `<span data-pick-unit="pack">+ ${esc(t("unit.pack"))} · ${money(m.price)}</span>` : ""}<span data-pick-unit="blister">+ ${esc(t("unit.blister"))} · ${money(m.blister_price)}</span></span>`
        : "",
    onPick: (m, unit) => {
      const row = $(`.item-row[data-id="${m.id}"]`, form);
      if (row && unit && unit !== unitOf(row)) {
        // уже в корзине другой единицей — переключаем строку
        setUnit(row, unit);
        $("[name=quantity]", row).value = 1;
        replay(row, "flash");
        recalc();
      } else cart.add(m, unit);
    },
  });
  form.addEventListener("input", recalc);
  if (editing) {
    form.elements.discount.value = Number(editing.discount);
    editing.items.forEach((i) => {
      const m = byId[i.medicine];
      if (m) $("[name=quantity]", cart.add(m, i.unit)).value = i.quantity;
    });
    recalc();
  }
  // переключили упаковку/блистер — новый максимум, лишнее урезаем
  form.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-unit]");
    if (!btn) return;
    setUnit(btn.closest(".item-row"), btn.dataset.unit);
    recalc();
  });
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const rows = cart.rows();
    if (!rows.length) return ($(".error", form).textContent = t("cart.required"));
    const payload = {
      discount: form.elements.discount.value || "0",
      items: rows.map((row) => ({ medicine: Number(row.dataset.id), unit: unitOf(row), quantity: Number($("[name=quantity]", row).value) })),
    };
    const button = $("button[type=submit]", form);
    button.disabled = true;
    try {
      if (editing) {
        const order = await api.put(`/orders/${editing.id}/`, payload);
        toast(t("order.saved", { n: order.order_number, sum: money(order.total_price) }));
        location.hash = orderLink(order);
        return;
      }
      const order = await api.post("/orders/", payload);
      toast(t("order.done", { n: order.order_number, sum: money(order.total_price) }));
      route(); // чистая касса для следующего покупателя, с обновлёнными остатками
    } catch (err) {
      $(".error", form).textContent = err.message;
      button.disabled = false;
    }
  });
}

// ---------------------------------------------------------------- Чат аптеки

const CHAT_POLL_MS = 4000;
const CHAT_MAX_MB = 25; // как MAX_FILE_MB на сервере
const CHAT_ACCEPT = ".jpg,.jpeg,.png,.gif,.webp,.mp4,.webm,.mov,.m4v"; // как EXTENSIONS на сервере
const CHAT_GROUP_MS = 5 * 60 * 1000; // сообщения одного автора подряд в пределах 5 минут — одной группой
const CHAT_EMOJI = "😀 😁 😂 🤣 😊 😇 😍 😘 😎 🤔 🙄 😴 😢 😭 😡 🤒 👍 👎 👏 🙏 💪 👌 ✌️ 🤝 👋 ❤️ 💚 🔥 ⭐ 🎉 ✅ ❌ ⚠️ ❓ 💊 💉 🩺 🌡️ 🩹 🧴 🧾 📦 🚚 💰 📞 ⏰ ☕ 🌙".split(" ");
const CHAT_REACTIONS = ["👍", "❤️", "😂", "😮", "😢", "🙏", "🔥", "✅"]; // как Reaction.EMOJI на сервере
const CHAT_VOICE_MAX_S = 600; // как MAX_VOICE_SECONDS на сервере
const CHAT_WAVE_BARS = 32;
// формат записи: что умеет браузер (Chrome/Firefox — webm/ogg, Safari — mp4)
const VOICE_TYPES = [["audio/webm;codecs=opus", "webm"], ["audio/ogg;codecs=opus", "ogg"], ["audio/mp4", "m4a"], ["audio/webm", "webm"]];
const clock = (s) => `${Math.floor(s / 60)}:${String(Math.floor(s % 60)).padStart(2, "0")}`;
// «волна» голосового: высоты столбиков из id — у каждого сообщения своя, но всегда одинаковая
const waveBars = (seed) =>
  Array.from({ length: CHAT_WAVE_BARS }, (_, i) => {
    const x = Math.sin(seed * 9301 + i * 49297) * 233280;
    return 25 + Math.round((x - Math.floor(x)) * 75);
  });
const EMOJI_ONLY = /^(?:\p{Extended_Pictographic}|\p{Emoji_Modifier}|️|‍|\s)+$/u;

const chatTime = (iso) => new Date(iso).toLocaleTimeString(i18n.dateLocale, { hour: "2-digit", minute: "2-digit" });
function chatDay(iso) {
  const d = new Date(iso);
  const today = new Date();
  const yesterday = new Date(today);
  yesterday.setDate(today.getDate() - 1);
  if (d.toDateString() === today.toDateString()) return t("chat.today");
  if (d.toDateString() === yesterday.toDateString()) return t("chat.yesterday");
  const year = d.getFullYear() === today.getFullYear() ? undefined : "numeric";
  return d.toLocaleDateString(i18n.dateLocale, { day: "numeric", month: "long", year });
}
// ссылки в тексте кликабельны; текст уже экранирован
const linkify = (html) => html.replace(/https?:\/\/[^\s<]+/g, (url) => `<a href="${url}" target="_blank" rel="noopener">${url}</a>`);
const fileSize = (bytes) => (bytes < 1024 * 1024 ? `${Math.ceil(bytes / 1024)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`);

// Чат аптеки (#/chat): директор и фармацевты переписываются; админ выбирает аптеку (#/chat?pharmacy=3).
// Новые сообщения приходят сразу по WebSocket (ws/chat/); если связь оборвалась — опросом, пока она не вернётся
// фото чата аптеки: загруженное или значок аптеки
const chatAvatarHtml = (url, cls = "pc-avatar pc-brand", name = "") =>
  url ? `<span class="${cls} has-photo"><img src="${esc(url)}" alt="${esc(name)}"></span>` : `<span class="${cls}">${icon("local_pharmacy")}</span>`;

// Просмотр фото и видео на весь экран: ← → (и клавиши) листают, Esc или клик по фону — закрыть.
// items: [{ kind: "image" | "video", url, caption }]
function openMediaViewer(items, start = 0) {
  let index = start;
  const box = document.createElement("div");
  box.className = "media-viewer";
  const show = () => {
    const item = items[index];
    box.innerHTML = `
      <div class="mv-top"><span>${index + 1} / ${items.length}</span><small>${esc(item.caption || "")}</small>
        <a class="icon-btn" href="${esc(item.url)}" download title="${esc(t("chat.download"))}">${icon("download")}</a>
        <button type="button" class="icon-btn" data-mv-close title="${esc(t("close"))}">${icon("close")}</button></div>
      ${items.length > 1 ? `<button type="button" class="mv-nav prev" data-mv="-1">${icon("chevron_left")}</button><button type="button" class="mv-nav next" data-mv="1">${icon("chevron_right")}</button>` : ""}
      <div class="mv-stage">${
        item.kind === "video" ? `<video src="${esc(item.url)}" controls autoplay playsinline></video>` : `<img src="${esc(item.url)}" alt="">`
      }</div>`;
  };
  const go = (step) => {
    index = (index + step + items.length) % items.length;
    show();
  };
  const close = () => {
    box.remove();
    document.removeEventListener("keydown", onKey, true);
  };
  const onKey = (e) => {
    if (e.key === "Escape") close();
    else if (e.key === "ArrowLeft" && items.length > 1) go(-1);
    else if (e.key === "ArrowRight" && items.length > 1) go(1);
    else return;
    e.stopPropagation();
    e.preventDefault();
  };
  box.addEventListener("click", (e) => {
    const nav = e.target.closest("[data-mv]");
    if (nav) return go(Number(nav.dataset.mv));
    if (e.target.closest("[data-mv-close]") || e.target === box || e.target.classList.contains("mv-stage")) close();
  });
  document.addEventListener("keydown", onKey, true);
  show();
  document.body.append(box);
}

// Админ проекта (#/chat): все чаты аптек — последнее сообщение, участники, у скольких запрет; клик — открыть чат
async function renderChatOverview() {
  const view = $("#view");
  view.innerHTML = `<div class="card empty">${esc(t("loading"))}</div>`;
  let rows;
  try {
    rows = await api.get("/chat/overview/");
  } catch (err) {
    view.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    return;
  }
  if (!rows.length) {
    view.innerHTML = `<div class="card empty">${esc(t("dash.noPharmacies"))}</div>`;
    return;
  }
  const when = (iso) => (chatDay(iso) === t("chat.today") ? chatTime(iso) : chatDay(iso));
  const last = (l) => {
    if (!l) return `<em>${esc(t("chat.noMessages"))}</em>`;
    const kind = { image: `📷 ${t("chat.photo")}`, video: `🎬 ${t("chat.video")}`, voice: `🎤 ${t("chat.voice")}` }[l.kind];
    const text = l.deleted ? t("chat.deletedMsg") : l.text || kind || "";
    return `<b>${esc(l.author || t("chat.deleted"))}:</b> ${esc(text)}`;
  };
  view.innerHTML = `<div class="chat-overview">${rows
    .map(
      (r, i) => `
      <a class="card co-item" href="#/chat?pharmacy=${r.pharmacy}" style="animation-delay:${i * 30}ms">
        ${chatAvatarHtml(r.avatar_url, undefined, r.name)}
        <span class="co-main">
          <span class="co-top"><b>${esc(r.name)}</b>${r.last ? `<time>${esc(when(r.last.created_at))}</time>` : ""}</span>
          <span class="co-last">${last(r.last)}</span>
          <span class="co-meta">
            <span>${icon("group")}${esc(t("chat.membersN", { n: r.members }))}</span>
            <span>${icon("chat")}${esc(t("chat.messagesN", { n: r.messages }))}</span>
            ${r.muted ? `<span class="warn">${icon("speaker_notes_off")}${esc(t("chat.mutedN", { n: r.muted }))}</span>` : ""}
          </span>
        </span>
        ${icon("chevron_right")}
      </a>`
    )
    .join("")}</div>`;
}

async function renderChat(params) {
  const view = $("#view");
  const admin = isAdmin();
  if (admin && !params.get("pharmacy")) return renderChatOverview();
  const pharmacy = admin ? params.get("pharmacy") : me.pharmacy;
  if (!pharmacy) {
    view.innerHTML = `<div class="card empty">${esc(t(admin ? "dash.noPharmacies" : "chat.noPharmacy"))}</div>`;
    return;
  }
  const scope = admin ? { pharmacy } : {};
  const name = admin ? pharmacyNames[pharmacy] : me.pharmacy_name;

  view.innerHTML = `
    <div class="card pc">
      <header class="pc-head">
        <button type="button" class="pc-head-info" data-info title="${esc(t("chat.info"))}">
          <span data-chat-avatar>${chatAvatarHtml(null)}</span>
          <span class="pc-title"><b>${esc(name || t("nav.chat"))}</b><span data-status>${esc(t("chat.connecting"))}</span></span>
        </button>
        <button type="button" class="pc-icon" data-members title="${esc(t("chat.members"))}">${icon("group")}</button>
        ${admin ? `<button type="button" class="pc-icon danger" data-clear title="${esc(t("chat.clear"))}">${icon("delete_sweep")}</button>` : ""}
        ${admin ? `<a class="btn sm" href="#/chat">${icon("arrow_back")}${esc(t("chat.allChats"))}</a>` : ""}
      </header>
      <button type="button" class="pc-pin hidden" data-pin></button>
      <div class="pc-log" data-log><div class="pc-empty">${esc(t("loading"))}</div></div>
      <div class="pc-drop">${icon("upload")}<span>${esc(t("chat.dropHere"))}</span></div>
      <section class="pc-info hidden" data-info-panel></section>
      <div class="pc-context hidden" data-context></div>
      <div class="pc-preview hidden" data-preview></div>
      <div class="pc-muted-note">${icon("speaker_notes_off")}<span>${esc(t("chat.youAreMuted"))}</span></div>
      <form class="pc-composer">
        <div class="pc-emoji hidden" data-emoji>${CHAT_EMOJI.map((e) => `<button type="button" data-e="${e}">${e}</button>`).join("")}</div>
        <button type="button" class="pc-icon" data-emoji-toggle title="${esc(t("chat.emoji"))}">${icon("sentiment_satisfied")}</button>
        <button type="button" class="pc-icon" data-attach title="${esc(t("chat.attach"))}">${icon("attach_file")}</button>
        <input type="file" accept="${CHAT_ACCEPT}" hidden>
        <textarea name="text" rows="1" maxlength="2000" placeholder="${esc(t("chat.placeholder"))}"></textarea>
        <div class="pc-rec">
          <button type="button" class="pc-icon" data-rec-cancel title="${esc(t("chat.recCancel"))}">${icon("delete")}</button>
          <span class="pc-rec-dot"></span><b data-rec-time>0:00</b><span class="pc-rec-hint">${esc(t("chat.recording"))}</span>
        </div>
        <button type="button" class="pc-send pc-mic" data-mic title="${esc(t("chat.voice"))}">${icon("mic")}</button>
        <button type="submit" class="pc-send" data-send title="${esc(t("ai.send"))}">${icon("send")}</button>
      </form>
    </div>`;

  const card = $(".pc", view);
  const log = $("[data-log]", view);
  const form = $("form", view);
  const input = form.elements.text;
  const fileInput = $("input[type=file]", form);
  const emojiPanel = $("[data-emoji]", form);
  const preview = $("[data-preview]", view);
  const status = $("[data-status]", view);
  let lastId = 0;
  let prev = null; // последнее показанное сообщение — для группировки и разделителей дней
  let file = null;
  let readUpTo = 0; // все сотрудники прочитали до этого id — мои галочки синие
  let reportedRead = 0;
  const messages = new Map(); // id → данные сообщения (для меню: ответить, изменить, …)
  const moderator = admin || me.role === "director"; // удаляет чужое и закрепляет (сервер проверяет сам)
  const q = api.query(scope);
  let mode = null; // { type: "reply" | "edit", m } — полоса над полем ввода
  const pinBar = $("[data-pin]", view);
  const contextBar = $("[data-context]", view);

  // ---- сообщения

  const media = (m) => {
    if (m.kind === "image") return `<button type="button" class="pc-media" data-view="${esc(m.file_url)}"><img src="${esc(m.file_url)}" alt="" loading="lazy"></button>`;
    if (m.kind === "video") return `<video class="pc-media" src="${esc(m.file_url)}" controls preload="metadata" playsinline></video>`;
    if (m.kind === "voice")
      return `<div class="pc-voice" data-voice data-duration="${m.duration || ""}">
        <button type="button" class="pc-play" data-play title="${esc(t("chat.voice"))}">${icon("play_arrow")}</button>
        <div class="pc-wave" data-seek>${waveBars(m.id).map((h) => `<i style="height:${h}%"></i>`).join("")}</div>
        <span class="pc-dur" data-dur>${m.duration ? clock(m.duration) : "0:00"}</span>
        <audio src="${esc(m.file_url)}" preload="metadata"></audio>
      </div>`;
    return "";
  };
  const ticks = (m) =>
    `<span class="pc-ticks${m.id <= readUpTo ? " read" : ""}" title="${esc(t(m.id <= readUpTo ? "chat.readAll" : "chat.sent"))}">${icon("done_all")}</span>`;
  const kindLabel = (m) => ({ image: `📷 ${t("chat.photo")}`, video: `🎬 ${t("chat.video")}`, voice: `🎤 ${t("chat.voice")}` })[m.kind] || "";
  const snippet = (m) => (m.deleted ? t("chat.deletedMsg") : m.text || kindLabel(m));
  const quote = (r) =>
    `<button type="button" class="pc-quote c${(r.user || 0) % 7}" data-jump="${r.id}"><b>${esc(r.author || t("chat.deleted"))}</b><span>${esc(r.deleted ? t("chat.deletedMsg") : r.text || kindLabel(r))}</span></button>`;
  const reactions = (m) =>
    m.reactions.length
      ? `<div class="pc-reacts">${m.reactions
          .map((r) => `<button type="button" class="pc-react${r.users.includes(me.id) ? " mine" : ""}" data-react="${r.emoji}">${r.emoji}<b>${r.users.length}</b></button>`)
          .join("")}</div>`
      : "";
  const bubble = (m, cont) => {
    const mine = m.user === me.id;
    const color = `c${(m.user || 0) % 7}`;
    const big = !m.deleted && !m.file_url && !m.reply && EMOJI_ONLY.test(m.text) && [...m.text.replace(/\s/g, "")].length <= 6;
    const meta = `<span class="pc-meta">${m.pinned ? icon("push_pin") : ""}${m.edited && !m.deleted ? `<em>${esc(t("chat.edited"))}</em>` : ""}${chatTime(m.created_at)}${mine && !m.deleted ? ticks(m) : ""}</span>`;
    const visual = m.kind === "image" || m.kind === "video";
    const author = m.author || t("chat.deleted");
    const body = m.deleted
      ? `<div class="pc-text pc-deleted">${icon("block")}${esc(t("chat.deletedMsg"))}${meta}</div>`
      : `${m.reply ? quote(m.reply) : ""}${m.file_url ? media(m) : ""}${m.text ? `<div class="pc-text">${linkify(esc(m.text))}${meta}</div>` : meta}${reactions(m)}`;
    return `
      <div class="pc-msg ${mine ? "out" : "in"}${cont ? " cont" : ""}${big ? " big" : ""}${visual && !m.text && !m.reply && !m.deleted ? " only-media" : ""}" data-id="${m.id}">
        ${mine ? "" : cont ? `<span class="pc-avatar-gap"></span>` : avatarHtml(author, m.author_avatar, `pc-avatar ${color}`)}
        <div class="pc-bubble">
          ${mine || cont ? "" : `<div class="pc-author ${color}">${esc(author)}${m.role ? ` <small>${esc(roleLabel(m.role))}</small>` : ""}</div>`}
          ${body}
        </div>
      </div>`;
  };
  // новое сообщение: разделитель дня и «группа» (тот же автор подряд в пределах 5 минут)
  const place = (m) => {
    const cont = prev && prev.user === m.user && chatDay(prev.created_at) === chatDay(m.created_at) && new Date(m.created_at) - new Date(prev.created_at) < CHAT_GROUP_MS;
    const day = !prev || chatDay(prev.created_at) !== chatDay(m.created_at) ? `<div class="pc-day"><span>${esc(chatDay(m.created_at))}</span></div>` : "";
    prev = m;
    messages.set(m.id, m);
    return day + bubble(m, cont);
  };
  // изменённое (текст, удаление, реакции, закрепление) — перерисовать на месте и обновить цитаты на него
  const replace = (m) => {
    messages.set(m.id, m);
    const el = $(`.pc-msg[data-id="${m.id}"]`, log);
    if (el) el.outerHTML = bubble(m, el.classList.contains("cont"));
    $$(`[data-jump="${m.id}"] span`, log).forEach((span) => (span.textContent = snippet(m)));
    if (mode?.m.id === m.id && m.deleted) setMode(null);
  };
  const append = (list) => {
    const fresh = list.filter((m) => m.id > lastId);
    if (!fresh.length) return;
    const atBottom = log.scrollHeight - log.scrollTop - log.clientHeight < 80;
    $(".pc-empty", log)?.remove();
    log.insertAdjacentHTML("beforeend", fresh.map(place).join(""));
    lastId = Math.max(lastId, ...fresh.map((m) => m.id));
    if (atBottom || fresh.some((m) => m.user === me.id)) log.scrollTop = log.scrollHeight;
    reportRead();
    // картинка догрузилась и выросла — остаёмся внизу
    $$("img, video", log).forEach((el) => {
      if (el.dataset.watched) return;
      el.dataset.watched = 1;
      el.addEventListener(el.tagName === "IMG" ? "load" : "loadedmetadata", () => atBottom && (log.scrollTop = log.scrollHeight), { once: true });
    });
  };

  const applyRead = (value) => {
    readUpTo = Math.max(readUpTo, value);
    $$(".pc-msg.out[data-id]", log).forEach((el) => {
      if (Number(el.dataset.id) > readUpTo) return;
      const tick = $(".pc-ticks", el);
      if (!tick || tick.classList.contains("read")) return;
      tick.classList.add("read");
      tick.title = t("chat.readAll");
    });
  };
  // прочитал — только если вкладка на экране; force — заодно узнать, кто ещё прочитал (когда нет WebSocket)
  let readTimer;
  const reportRead = (force = false) => {
    if (document.hidden || (!force && lastId <= reportedRead)) return;
    clearTimeout(readTimer);
    readTimer = setTimeout(async () => {
      reportedRead = Math.max(reportedRead, lastId);
      try {
        applyRead((await api.post("/chat/read/", { ...scope, last_id: lastId })).read_up_to);
      } catch {
        reportedRead = 0; // повторим при следующем сообщении
      }
    }, 300);
  };
  const onVisible = () => {
    if (!log.isConnected) return document.removeEventListener("visibilitychange", onVisible);
    if (!document.hidden) reportRead();
  };
  document.addEventListener("visibilitychange", onVisible);

  const load = async () => {
    append(await api.get("/chat/", { ...scope, ...(lastId ? { after: lastId } : {}) }));
    if (!lastId) log.innerHTML = `<div class="pc-empty">${icon("forum")}<span>${esc(t("chat.empty"))}</span></div>`;
  };
  try {
    await load();
  } catch (err) {
    log.innerHTML = `<div class="pc-empty error">${esc(err.message)}</div>`;
    return;
  }
  log.scrollTop = log.scrollHeight;
  reportRead(true);

  const setPinned = (m) => {
    pinBar.classList.toggle("hidden", !m);
    if (!m) return;
    pinBar.dataset.jump = m.id;
    pinBar.innerHTML = `${icon("push_pin")}<span><b>${esc(t("chat.pinned"))}</b><small>${esc(snippet(m))}</small></span>`;
  };
  api.get(`/chat/pinned/${q}`).then(setPinned, () => {});

  // переход к сообщению (цитата, закреплённое) с подсветкой
  const jump = (id) => {
    const el = $(`.pc-msg[data-id="${id}"]`, log);
    if (!el) return toast(t("chat.notLoaded"), true);
    el.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "center" });
    replay(el, "flash");
  };
  pinBar.addEventListener("click", () => jump(pinBar.dataset.jump));
  log.addEventListener("click", (e) => {
    const quoted = e.target.closest("[data-jump]");
    if (quoted) return jump(quoted.dataset.jump);
    const chip = e.target.closest("[data-react]");
    if (chip) react(Number(chip.closest(".pc-msg").dataset.id), chip.dataset.react);
  });

  // голосовые: одно играет за раз, клик по волне — перемотка
  const voiceDuration = (box) => {
    const audio = $("audio", box);
    return Number.isFinite(audio.duration) && audio.duration > 0 ? audio.duration : Number(box.dataset.duration) || 0;
  };
  const voiceProgress = (box) => {
    const audio = $("audio", box);
    const total = voiceDuration(box);
    const on = total ? Math.round((audio.currentTime / total) * CHAT_WAVE_BARS) : 0;
    $$(".pc-wave i", box).forEach((bar, i) => bar.classList.toggle("on", i < on));
    $("[data-dur]", box).textContent = clock(audio.paused && !audio.currentTime ? total : audio.currentTime);
  };
  const wireVoice = (box) => {
    if (box.dataset.wired) return;
    box.dataset.wired = 1;
    const audio = $("audio", box);
    const button = $("[data-play]", box);
    audio.addEventListener("timeupdate", () => voiceProgress(box));
    audio.addEventListener("play", () => (button.innerHTML = icon("pause")));
    audio.addEventListener("pause", () => (button.innerHTML = icon("play_arrow")));
    audio.addEventListener("ended", () => {
      audio.currentTime = 0;
      voiceProgress(box);
    });
  };
  log.addEventListener("click", (e) => {
    const box = e.target.closest("[data-voice]");
    if (!box) return;
    wireVoice(box);
    const audio = $("audio", box);
    if (e.target.closest("[data-play]")) {
      $$("[data-voice] audio", log).forEach((other) => other !== audio && other.pause());
      if (audio.paused) audio.play().catch((err) => toast(err.message, true));
      else audio.pause();
    } else if (e.target.closest("[data-seek]")) {
      const wave = $("[data-seek]", box).getBoundingClientRect();
      const total = voiceDuration(box);
      if (total) audio.currentTime = ((e.clientX - wave.left) / wave.width) * total;
      voiceProgress(box);
    }
  });

  // фото — на весь экран; стрелками листаются все фото ленты
  log.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-view]");
    if (!btn) return;
    const photos = [...messages.values()].filter((m) => m.kind === "image" && !m.deleted && m.file_url);
    const items = photos.map((m) => ({ kind: "image", url: m.file_url, caption: `${m.author || ""} · ${dateTime(m.created_at)}` }));
    const start = Math.max(0, photos.findIndex((m) => m.file_url === btn.dataset.view));
    openMediaViewer(items.length ? items : [{ kind: "image", url: btn.dataset.view }], start);
  });

  // ---- сведения о чате: фото, участники, медиа / голосовые / ссылки (как в мессенджерах)

  const infoPanel = $("[data-info-panel]", view);
  let info = null;
  let infoTab = "media";
  let mediaFilter = "image,video";
  const applyInfo = (data) => {
    info = data;
    $("[data-chat-avatar]", view).innerHTML = chatAvatarHtml(data.avatar_url, undefined, data.name);
    if (!infoPanel.classList.contains("hidden")) renderInfo();
  };
  api.get(`/chat/info/${q}`).then(applyInfo, () => {});

  const monthTitle = (iso) => {
    const d = new Date(iso);
    const now = new Date();
    if (d.getFullYear() === now.getFullYear() && d.getMonth() === now.getMonth()) return t("chat.thisMonth");
    const label = d.toLocaleDateString(i18n.dateLocale, { month: "long", ...(d.getFullYear() === now.getFullYear() ? {} : { year: "numeric" }) });
    return label.charAt(0).toUpperCase() + label.slice(1);
  };
  const byMonth = (items, render) => {
    const groups = [];
    items.forEach((m, i) => {
      const title = monthTitle(m.created_at);
      if (groups.at(-1)?.title !== title) groups.push({ title, html: [] });
      groups.at(-1).html.push(render(m, i));
    });
    return groups;
  };
  const jumpButton = (m) => `<button type="button" class="icon-btn" data-goto="${m.id}" title="${esc(t("chat.toMessage"))}">${icon("forum")}</button>`;
  let mediaItems = [];

  async function renderInfo() {
    const c = info.counts;
    const tab = (key, label, n) => `<button type="button" class="${infoTab === key ? "active" : ""}" data-tab="${key}">${esc(label)}<small>${n}</small></button>`;
    const chip = (value, label, n) => `<button type="button" class="chip${mediaFilter === value ? " active" : ""}" data-filter-media="${value}">${esc(label)} · ${n}</button>`;
    infoPanel.innerHTML = `
      <header class="pci-head">
        <button type="button" class="pc-icon" data-info-close title="${esc(t("close"))}">${icon("arrow_back")}</button>
        <b>${esc(t("chat.info"))}</b>
      </header>
      <div class="pci-scroll">
        <div class="pci-hero">
          <div class="avatar-edit">
            ${chatAvatarHtml(info.avatar_url, "pc-avatar pc-brand pci-avatar", info.name)}
            ${moderator ? `<button type="button" class="avatar-btn" data-chat-photo title="${esc(t("avatar.change"))}">${icon("photo_camera")}</button>` : ""}
          </div>
          <h3>${esc(info.name)}</h3>
          <button type="button" class="link-btn neutral" data-info-members>${icon("group")}${esc(t("chat.membersN", { n: info.members }))}</button>
          ${moderator && info.avatar_url ? `<button type="button" class="link-btn" data-chat-photo-remove>${esc(t("avatar.remove"))}</button>` : ""}
        </div>
        <div class="pci-tabs">
          ${tab("media", t("chat.tabMedia"), c.image + c.video)}
          ${tab("voice", t("chat.tabVoice"), c.voice)}
          ${tab("link", t("chat.tabLinks"), c.link)}
        </div>
        ${infoTab === "media" ? `<div class="pci-filter">${chip("image,video", t("chat.all"), c.image + c.video)}${chip("image", t("chat.photos"), c.image)}${chip("video", t("chat.videos"), c.video)}</div>` : ""}
        <div class="pci-body" data-info-body><div class="pc-empty">${esc(t("loading"))}</div></div>
      </div>`;
    const body = $("[data-info-body]", infoPanel);
    const kind = infoTab === "media" ? mediaFilter : infoTab;
    let items;
    try {
      items = await api.get(`/chat/media/${api.query({ ...scope, kind })}`);
    } catch (err) {
      body.innerHTML = `<div class="pc-empty error">${esc(err.message)}</div>`;
      return;
    }
    if (!body.isConnected) return;
    mediaItems = items;
    if (!items.length) {
      body.innerHTML = `<div class="pci-none">${icon(infoTab === "voice" ? "mic_off" : infoTab === "link" ? "link_off" : "hide_image")}<span>${esc(t("chat.nothingHere"))}</span></div>`;
      return;
    }
    const groups =
      infoTab === "media"
        ? byMonth(
            items,
            (m, i) => `
            <button type="button" class="pci-tile" data-media-index="${i}">
              ${m.kind === "video" ? `<video src="${esc(m.file_url)}#t=0.1" preload="metadata" muted playsinline></video><span class="pci-badge">${icon("videocam")}<b data-video-dur></b></span>` : `<img src="${esc(m.file_url)}" alt="" loading="lazy">`}
            </button>`
          )
        : byMonth(items, (m) =>
            infoTab === "voice"
              ? `<div class="pci-row">${icon("mic")}<span class="pci-row-main"><b>${esc(m.author || t("chat.deleted"))}</b><small>${dateTime(m.created_at)}${m.duration ? ` · ${clock(m.duration)}` : ""}</small><audio src="${esc(m.file_url)}" controls preload="none"></audio></span>${jumpButton(m)}</div>`
              : `<div class="pci-row">${icon("link")}<span class="pci-row-main">${(m.text.match(/https?:\/\/[^\s<]+/g) || [])
                  .map((url) => `<a href="${esc(url)}" target="_blank" rel="noopener">${esc(url)}</a>`)
                  .join("")}<small>${esc(m.author || "")} · ${dateTime(m.created_at)}</small></span>${jumpButton(m)}</div>`
          );
    body.innerHTML = groups
      .map((g) => `<h4 class="pci-month">${esc(g.title)}</h4><div class="${infoTab === "media" ? "pci-grid" : "pci-list"}">${g.html.join("")}</div>`)
      .join("");
    // длительность видео — из самого файла, когда загрузятся его данные
    $$("video", body).forEach((video) =>
      video.addEventListener("loadedmetadata", () => {
        const label = $("[data-video-dur]", video.parentElement);
        if (label && Number.isFinite(video.duration)) label.textContent = clock(video.duration);
      })
    );
  }

  const openInfo = async () => {
    infoPanel.classList.remove("hidden");
    infoPanel.innerHTML = `<div class="pc-empty">${esc(t("loading"))}</div>`;
    try {
      info = await api.get(`/chat/info/${q}`); // счётчики — свежие
    } catch (err) {
      infoPanel.innerHTML = `<div class="pc-empty error">${esc(err.message)}</div>`;
      return;
    }
    renderInfo();
  };
  const closeInfo = () => {
    infoPanel.classList.add("hidden");
    infoPanel.innerHTML = "";
  };
  $("[data-info]", view).addEventListener("click", openInfo);
  infoPanel.addEventListener("click", async (e) => {
    const el = (sel) => e.target.closest(sel);
    if (el("[data-info-close]")) return closeInfo();
    if (el("[data-info-members]")) return showMembers();
    if (el("[data-tab]")) {
      infoTab = el("[data-tab]").dataset.tab;
      return renderInfo();
    }
    if (el("[data-filter-media]")) {
      mediaFilter = el("[data-filter-media]").dataset.filterMedia;
      return renderInfo();
    }
    if (el("[data-media-index]")) {
      const items = mediaItems.map((m) => ({ kind: m.kind, url: m.file_url, caption: `${m.author || ""} · ${dateTime(m.created_at)}` }));
      return openMediaViewer(items, Number(el("[data-media-index]").dataset.mediaIndex));
    }
    if (el("[data-goto]")) {
      const id = el("[data-goto]").dataset.goto;
      closeInfo();
      return jump(id);
    }
    if (el("[data-chat-photo]")) {
      const data = await pickAvatar(`/chat/avatar/${q}`, t("avatar.cropChat"));
      if (data) applyInfo(data);
      return;
    }
    if (el("[data-chat-photo-remove]")) {
      try {
        applyInfo(await api.del(`/chat/avatar/${q}`));
      } catch (err) {
        toast(err.message, true);
      }
    }
  });

  // ---- управление: участники (директор и админ запрещают писать), очистка (админ)

  const setMuted = (muted) => {
    me.chat_muted = muted;
    card.classList.toggle("muted", muted);
  };
  setMuted(Boolean(me.chat_muted));
  const showMembers = async () => {
    let members;
    try {
      members = await api.get(`/chat/members/${q}`);
    } catch (err) {
      return toast(err.message, true);
    }
    const canMute = (u) => moderator && u.id !== me.id && (admin || u.role !== "director");
    const row = (u) => `
      <div class="member">
        ${avatarHtml(u.name, u.avatar_url, `pc-avatar c${u.id % 7}`)}
        <span class="member-who"><b>${esc(u.name)}${u.id === me.id ? ` <small>(${esc(t("chat.you"))})</small>` : ""}</b>
          <small>${esc(roleLabel(u.role))}${u.chat_muted ? ` · <span class="bad">${esc(t("chat.muted"))}</span>` : ""}</small></span>
        ${canMute(u) ? `<button type="button" class="btn sm${u.chat_muted ? "" : " danger"}" data-mute="${u.id}" data-to="${!u.chat_muted}">${icon(u.chat_muted ? "chat" : "speaker_notes_off")}${esc(t(u.chat_muted ? "chat.unmute" : "chat.mute"))}</button>` : ""}
      </div>`;
    const body = openModal(t("chat.membersOf", { name: name || "" }), `<div class="members">${members.map(row).join("") || esc(t("noData"))}</div>`);
    body.addEventListener("click", async (e) => {
      const btn = e.target.closest("[data-mute]");
      if (!btn) return;
      btn.disabled = true;
      try {
        const u = await api.post(`/chat/members/${btn.dataset.mute}/mute/${q}`, { muted: btn.dataset.to === "true" });
        members = members.map((x) => (x.id === u.id ? u : x));
        body.innerHTML = `<div class="members">${members.map(row).join("")}</div>`;
        toast(t(u.chat_muted ? "chat.mutedToast" : "chat.unmutedToast", { name: u.name }));
      } catch (err) {
        toast(err.message, true);
        btn.disabled = false;
      }
    });
  };
  $("[data-members]", view).addEventListener("click", showMembers);
  $("[data-clear]", view)?.addEventListener("click", async () => {
    if (!confirmAction(t("chat.confirmClear", { name: name || "" }))) return;
    try {
      await api.post(`/chat/clear/${q}`);
      toast(t("chat.clearedToast"));
      route();
    } catch (err) {
      toast(err.message, true);
    }
  });

  // ---- живая связь: WebSocket; пока связи нет — опрос и переподключение с паузой

  let socket = null;
  let retryMs = 1000;
  const setOnline = (online) => {
    status.textContent = t(online ? "chat.online" : "chat.connecting");
    status.classList.toggle("online", online);
  };
  const connect = () => {
    if (!log.isConnected) return;
    const proto = location.protocol === "https:" ? "wss" : "ws";
    socket = new WebSocket(`${proto}://${location.host}/ws/chat/${api.query({ token: api.tokens.access, ...scope })}`);
    socket.onopen = () => {
      retryMs = 1000;
      setOnline(true);
      load().catch(() => {}); // догружаем то, что пришло, пока связи не было
    };
    socket.onmessage = (e) => {
      const data = JSON.parse(e.data);
      if (data.event === "message") append([data.message]);
      if (data.event === "update") replace(data.message);
      if (data.event === "pin") setPinned(data.message);
      if (data.event === "mute" && data.user === me.id) {
        setMuted(data.muted);
        toast(t(data.muted ? "chat.youAreMuted" : "chat.youCanWrite"), data.muted);
      }
      if (data.event === "info" && info) applyInfo({ ...info, avatar_url: data.avatar_url });
      if (data.event === "cleared") {
        toast(t("chat.clearedToast"));
        route();
      }
      if (data.event === "read") applyRead(data.read_up_to);
    };
    socket.onclose = () => {
      socket = null;
      setOnline(false);
      if (!log.isConnected) return;
      // токен мог истечь: запрос к API обновит его перед новой попыткой
      setTimeout(() => load().catch(() => {}).finally(connect), retryMs);
      retryMs = Math.min(retryMs * 2, 30000);
    };
  };
  connect();

  // ушли со страницы (или она перерисована) — сокет закрывается, опрос останавливается
  const timer = setInterval(() => {
    if (!log.isConnected) {
      clearInterval(timer);
      return socket?.close();
    }
    if (!document.hidden && socket?.readyState !== WebSocket.OPEN) load().then(() => reportRead(true), () => {});
  }, CHAT_POLL_MS);
  window.addEventListener("hashchange", () => !log.isConnected && socket?.close(), { once: true });

  const syncComposer = () => form.classList.toggle("empty", !input.value.trim() && !file && mode?.type !== "edit");

  // ---- ответ / изменение: полоса над полем ввода

  function setMode(next) {
    const wasEdit = mode?.type === "edit";
    mode = next;
    form.classList.toggle("editing", mode?.type === "edit");
    contextBar.classList.toggle("hidden", !mode);
    if (mode) {
      const { m } = mode;
      const title = mode.type === "edit" ? t("chat.editing") : t("chat.replyTo", { name: m.author || t("chat.deleted") });
      contextBar.innerHTML = `${icon(mode.type === "edit" ? "edit" : "reply")}
        <span><b>${esc(title)}</b><small>${esc(snippet(m))}</small></span>
        <button type="button" class="icon-btn" data-unmode title="${esc(t("cancel"))}">${icon("close")}</button>`;
      if (mode.type === "edit") {
        setFile(null);
        input.value = m.text;
        input.dispatchEvent(new Event("input"));
      }
      input.focus();
    } else if (wasEdit) {
      input.value = "";
      input.dispatchEvent(new Event("input"));
    }
    syncComposer();
  }
  contextBar.addEventListener("click", (e) => e.target.closest("[data-unmode]") && setMode(null));

  // ---- действия с сообщением

  const run = async (request) => {
    try {
      replace(await request);
    } catch (err) {
      toast(err.message, true);
    }
  };
  const react = (id, emoji) => run(api.post(`/chat/${id}/react/${q}`, { emoji }));
  const actions = {
    reply: (m) => setMode({ type: "reply", m }),
    copy: async (m) => {
      try {
        await navigator.clipboard.writeText(m.text);
        toast(t("chat.copied"));
      } catch {
        toast(t("chat.copyFailed"), true);
      }
    },
    edit: (m) => setMode({ type: "edit", m }),
    pin: async (m) => {
      try {
        const data = await api.post(`/chat/${m.id}/pin/${q}`);
        replace(data);
        setPinned(data.pinned ? data : null);
      } catch (err) {
        toast(err.message, true);
      }
    },
    download: (m) => {
      const link = document.createElement("a");
      link.href = m.file_url;
      link.download = m.file_url.split("/").pop();
      link.click();
    },
    delete: (m) => confirmAction(t("chat.confirmDelete")) && run(api.del(`/chat/${m.id}/${q}`)),
  };

  // правый клик (на телефоне — долгое нажатие) по сообщению: реакции и действия
  let menu = null;
  const closeMenu = () => {
    menu?.remove();
    menu = null;
  };
  const openMenu = (el, x, y) => {
    const m = messages.get(Number(el.dataset.id));
    if (!m || m.deleted) return;
    closeMenu();
    const mine = m.user === me.id;
    const items = [
      ["reply", "reply", t("chat.reply")],
      m.text && ["copy", "content_copy", t("chat.copy")],
      mine && ["edit", "edit", t("edit")],
      moderator && ["pin", "push_pin", t(m.pinned ? "chat.unpin" : "chat.pin")],
      m.file_url && ["download", "download", t("chat.download")],
      (mine || moderator) && ["delete", "delete", t("delete"), "danger"],
    ].filter(Boolean);
    const myReaction = m.reactions.find((r) => r.users.includes(me.id))?.emoji;
    menu = document.createElement("div");
    menu.className = "pc-menu";
    menu.innerHTML = `
      <div class="pc-menu-reacts">${CHAT_REACTIONS.map((e) => `<button type="button" data-menu-react="${e}" class="${e === myReaction ? "mine" : ""}">${e}</button>`).join("")}</div>
      ${items.map(([act, ic, label, cls]) => `<button type="button" data-menu-act="${act}" class="${cls || ""}">${icon(ic)}${esc(label)}</button>`).join("")}`;
    document.body.append(menu);
    const { offsetWidth: w, offsetHeight: h } = menu;
    menu.style.left = `${Math.min(x, window.innerWidth - w - 8)}px`;
    menu.style.top = `${y + h > window.innerHeight - 8 ? Math.max(8, y - h) : y}px`;
    replay(el, "flash");
    menu.addEventListener("click", (e) => {
      const emoji = e.target.closest("[data-menu-react]")?.dataset.menuReact;
      const act = e.target.closest("[data-menu-act]")?.dataset.menuAct;
      if (!emoji && !act) return;
      closeMenu();
      if (emoji) react(m.id, emoji);
      else actions[act](m);
    });
  };
  log.addEventListener("contextmenu", (e) => {
    const el = e.target.closest(".pc-msg");
    if (!el || e.target.closest("a, video, audio")) return; // у ссылок и плееров — меню браузера
    e.preventDefault();
    openMenu(el, e.clientX, e.clientY);
  });
  let press;
  let pressAt = [0, 0];
  log.addEventListener("pointerdown", (e) => {
    if (e.pointerType !== "touch") return;
    const el = e.target.closest(".pc-msg");
    if (!el) return;
    pressAt = [e.clientX, e.clientY];
    press = setTimeout(() => openMenu(el, e.clientX, e.clientY), 500);
  });
  ["pointerup", "pointercancel"].forEach((type) => log.addEventListener(type, () => clearTimeout(press)));
  // палец дрогнул — ещё долгое нажатие; поехал (прокрутка) — нет
  log.addEventListener("pointermove", (e) => Math.hypot(e.clientX - pressAt[0], e.clientY - pressAt[1]) > 10 && clearTimeout(press));
  log.addEventListener("scroll", closeMenu);
  document.addEventListener("pointerdown", function outside(e) {
    if (!log.isConnected) return document.removeEventListener("pointerdown", outside);
    if (menu && !menu.contains(e.target)) closeMenu();
  });
  document.addEventListener(
    "keydown",
    function onEsc(e) {
      if (!log.isConnected) return document.removeEventListener("keydown", onEsc, true);
      const infoOpen = !infoPanel.classList.contains("hidden");
      if (e.key !== "Escape" || (!menu && !mode && !infoOpen) || document.querySelector(".media-viewer") || !$("#modal").classList.contains("hidden")) return;
      e.stopPropagation(); // не закрывать заодно окно ассистента
      if (menu) closeMenu();
      else if (infoOpen) closeInfo();
      else setMode(null);
    },
    true
  );

  // ---- вложение: кнопка, вставка из буфера, перетаскивание

  const setFile = (f) => {
    if (preview.dataset.url) URL.revokeObjectURL(preview.dataset.url);
    file = null;
    preview.classList.add("hidden");
    preview.innerHTML = "";
    delete preview.dataset.url;
    syncComposer();
    if (!f) return;
    const ext = `.${f.name.split(".").pop().toLowerCase()}`;
    if (!CHAT_ACCEPT.split(",").includes(ext)) return toast(t("chat.badType"), true);
    if (f.size > CHAT_MAX_MB * 1024 * 1024) return toast(t("chat.tooBig", { mb: CHAT_MAX_MB }), true);
    file = f;
    const url = URL.createObjectURL(f);
    const video = f.type.startsWith("video/") || [".mp4", ".webm", ".mov", ".m4v"].includes(ext);
    preview.dataset.url = url;
    preview.innerHTML = `
      ${video ? `<video src="${url}" muted></video>` : `<img src="${url}" alt="">`}
      <div><b>${esc(t(video ? "chat.video" : "chat.photo"))}</b><span>${esc(f.name)} · ${fileSize(f.size)}</span></div>
      <button type="button" class="icon-btn" data-unfile title="${esc(t("delete"))}">${icon("close")}</button>`;
    preview.classList.remove("hidden");
    syncComposer();
    input.focus();
  };
  $("[data-attach]", form).addEventListener("click", () => fileInput.click());
  fileInput.addEventListener("change", () => {
    setFile(fileInput.files[0]);
    fileInput.value = "";
  });
  preview.addEventListener("click", (e) => e.target.closest("[data-unfile]") && setFile(null));
  input.addEventListener("paste", (e) => {
    const pasted = e.clipboardData?.files?.[0];
    if (!pasted) return;
    e.preventDefault();
    setFile(pasted);
  });
  card.addEventListener("dragover", (e) => {
    if (![...e.dataTransfer.types].includes("Files")) return;
    e.preventDefault();
    card.classList.add("dragging");
  });
  card.addEventListener("dragleave", (e) => !card.contains(e.relatedTarget) && card.classList.remove("dragging"));
  card.addEventListener("drop", (e) => {
    e.preventDefault();
    card.classList.remove("dragging");
    setFile(e.dataTransfer.files[0]);
  });

  // ---- эмодзи: вставляются в место курсора

  $("[data-emoji-toggle]", form).addEventListener("click", () => emojiPanel.classList.toggle("hidden"));
  emojiPanel.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-e]");
    if (!btn) return;
    const { selectionStart: from, selectionEnd: to, value } = input;
    input.value = value.slice(0, from) + btn.dataset.e + value.slice(to);
    input.selectionStart = input.selectionEnd = from + btn.dataset.e.length;
    syncComposer();
    input.focus();
  });
  document.addEventListener("click", function hideEmoji(e) {
    if (!form.isConnected) return document.removeEventListener("click", hideEmoji);
    if (!e.target.closest("[data-emoji], [data-emoji-toggle]")) emojiPanel.classList.add("hidden");
  });

  // ---- отправка

  const post = async (fields) => {
    const body = new FormData();
    Object.entries({ ...scope, ...fields }).forEach(([k, v]) => body.append(k, v));
    append([await api.post("/chat/", body)]);
  };
  const send = async () => {
    const text = input.value.trim();
    if (mode?.type === "edit") {
      if (!text && !mode.m.file_url) return;
      if (text !== mode.m.text) await run(api.patch(`/chat/${mode.m.id}/${q}`, { text }));
      return setMode(null);
    }
    if (!text && !file) return;
    const button = $("[data-send]", form);
    button.disabled = true;
    card.classList.add("sending");
    try {
      const reply = mode?.type === "reply" ? { reply_to: mode.m.id } : {};
      await post(file ? { text, file, ...reply } : { text, ...reply });
      input.value = "";
      input.style.height = "";
      setFile(null);
      setMode(null);
      emojiPanel.classList.add("hidden");
    } catch (err) {
      toast(err.message, true);
    } finally {
      button.disabled = false;
      card.classList.remove("sending");
      syncComposer();
      input.focus();
    }
  };

  // ---- голосовое: кнопка микрофона — запись; «отправить» — стоп и отправка, корзина — отмена

  let recorder = null;
  const recTime = $("[data-rec-time]", form);
  const recDot = $(".pc-rec-dot", form);
  const stopTracks = () => recorder?.stream.getTracks().forEach((track) => track.stop());
  const startRecording = async () => {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) return toast(t("chat.micUnsupported"), true);
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch {
      return toast(t("chat.micDenied"), true);
    }
    if (!form.isConnected) return stream.getTracks().forEach((track) => track.stop());
    const [mimeType, ext] = VOICE_TYPES.find(([type]) => MediaRecorder.isTypeSupported(type)) || ["", "webm"];
    const chunks = [];
    const started = Date.now();
    // уровень звука: точка «дышит» в такт голосу; если микрофон всё время молчит — не отправляем тишину
    const audioCtx = new AudioContext();
    const analyser = audioCtx.createAnalyser();
    analyser.fftSize = 512;
    audioCtx.createMediaStreamSource(stream).connect(analyser);
    const samples = new Float32Array(analyser.fftSize);
    let loudest = 0;
    const level = () => {
      analyser.getFloatTimeDomainData(samples);
      const peak = samples.reduce((max, v) => Math.max(max, Math.abs(v)), 0);
      loudest = Math.max(loudest, peak);
      recDot.style.transform = `scale(${1 + Math.min(peak * 4, 1.2)})`;
    };
    recorder = new MediaRecorder(stream, mimeType ? { mimeType } : {});
    recorder.ext = ext;
    recorder.addEventListener("dataavailable", (e) => e.data.size && chunks.push(e.data));
    recorder.addEventListener("stop", async () => {
      clearInterval(recorder.timer);
      stopTracks();
      audioCtx.close();
      recDot.style.transform = "";
      const shouldSend = recorder.send;
      const duration = Math.max(1, Math.round((Date.now() - started) / 1000));
      recorder = null;
      form.classList.remove("recording");
      syncComposer();
      if (!shouldSend || !chunks.length || !form.isConnected) return;
      if (loudest < 0.01) return toast(t("chat.micSilent"), true);
      const blob = new Blob(chunks, { type: chunks[0].type || mimeType });
      card.classList.add("sending");
      try {
        const reply = mode?.type === "reply" ? { reply_to: mode.m.id } : {};
        await post({ voice: "true", duration: Math.min(duration, CHAT_VOICE_MAX_S), file: new File([blob], `voice.${ext}`, { type: blob.type }), ...reply });
        setMode(null);
      } catch (err) {
        toast(err.message, true);
      } finally {
        card.classList.remove("sending");
      }
    });
    recorder.timer = setInterval(() => {
      level();
      const seconds = (Date.now() - started) / 1000;
      recTime.textContent = clock(seconds);
      if (seconds >= CHAT_VOICE_MAX_S || !form.isConnected) stopRecording(form.isConnected);
    }, 100);
    recTime.textContent = "0:00";
    form.classList.add("recording");
    emojiPanel.classList.add("hidden");
    recorder.start();
  };
  const stopRecording = (sendIt) => {
    if (!recorder || recorder.state === "inactive") return;
    recorder.send = sendIt;
    recorder.stop();
  };
  $("[data-mic]", form).addEventListener("click", startRecording);
  $("[data-rec-cancel]", form).addEventListener("click", () => stopRecording(false));
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (recorder) stopRecording(true);
    else send();
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      send();
    }
    if (e.key === "ArrowUp" && !input.value && !mode && !file) {
      const last = [...messages.values()].reverse().find((m) => m.user === me.id && !m.deleted && m.text);
      if (!last) return;
      e.preventDefault();
      setMode({ type: "edit", m: last });
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "";
    input.style.height = `${Math.min(input.scrollHeight, 140)}px`;
    syncComposer();
  });
  syncComposer();
  if (matchMedia("(min-width: 601px)").matches) input.focus();

  // кнопка ассистента (её можно перетаскивать) не должна закрывать микрофон и «отправить»: сдвигаем их из-под неё
  const fab = $("#ai-fab");
  const avoidFab = () => {
    if (!form.isConnected) {
      window.removeEventListener("resize", avoidFab);
      return document.removeEventListener("pointerup", afterDrag);
    }
    form.style.paddingLeft = form.style.paddingRight = "";
    const f = fab.getBoundingClientRect();
    if (!f.width) return; // скрыта (нет права или открыт ассистент на телефоне)
    const c = form.getBoundingClientRect();
    if (f.left >= c.right || f.right <= c.left || f.top >= c.bottom || f.bottom <= c.top) return;
    if (f.left + f.width / 2 > c.left + c.width / 2) form.style.paddingRight = `${c.right - f.left + 8}px`;
    else form.style.paddingLeft = `${f.right - c.left + 8}px`;
  };
  const afterDrag = () => setTimeout(avoidFab, 50);
  window.addEventListener("resize", avoidFab);
  document.addEventListener("pointerup", afterDrag);
  avoidFab();
}

// ---------------------------------------------------------------- Мой профиль и личный отчёт

const initialsOf = (name) =>
  name
    .split(/\s+/)
    .slice(0, 2)
    .map((w) => w[0])
    .join("")
    .toUpperCase();

function renderProfile() {
  const view = $("#view");
  const name = me.full_name || me.username;
  const fact = (label, value) => `<div><dt>${esc(label)}</dt><dd>${esc(value || "—")}</dd></div>`;
  const profile = `
    <div class="card profile">
      <div class="avatar-edit">
        ${avatarHtml(name, me.avatar_url, "avatar big")}
        <button type="button" class="avatar-btn" data-avatar-pick title="${esc(t("avatar.change"))}">${icon("photo_camera")}</button>
      </div>
      <div class="profile-main">
        <h2>${esc(name)}</h2>
        <span class="role-pill">${esc(roleLabel(me.role))}</span>
        ${me.avatar_url ? `<button type="button" class="link-btn" data-avatar-remove>${esc(t("avatar.remove"))}</button>` : ""}
      </div>
      <dl class="profile-facts">
        ${fact(t("login.username"), me.username)}
        ${fact(t("phone"), me.phone_number)}
        ${fact(t("pharmacy"), me.pharmacy_name || t("user.allPharmacies"))}
        ${fact(t("profile.since"), dateOnly(me.created_at))}
      </dl>
    </div>`;

  // своё фото: после смены — обновить шапку и страницу
  const avatarChanged = (data) => {
    if (!data) return;
    Object.assign(me, data);
    renderShell();
    route();
    toast(t("saved"));
  };
  view.addEventListener("click", async (e) => {
    if (e.target.closest("[data-avatar-pick]")) avatarChanged(await pickAvatar("/auth/me/avatar/"));
    if (e.target.closest("[data-avatar-remove]")) avatarChanged(await api.del("/auth/me/avatar/").catch((err) => toast(err.message, true)));
  });

  // админ проекта не продаёт и не закупает — отчёта нет
  if (!me.pharmacy) {
    view.innerHTML = `<div class="dash">${profile}${ratesPanel()}<div class="card empty">${esc(t("profile.noReport"))}</div></div>`;
    mountRates($("[data-rates]", view));
    return;
  }

  view.innerHTML = `
    <div class="dash">
      ${profile}
      ${ratesPanel()}
      <div class="report-head"><h3>${icon("analytics")}${esc(t("profile.report"))}</h3>${monthPickerHtml()}</div>
      <div id="report"><div class="card empty">${esc(t("loading"))}</div></div>
    </div>`;
  const picker = $("[data-month-picker]", view);
  setupMonthPicker(picker);
  mountRates($("[data-rates]", view));
  const from = $('[data-filter="month_from"]', picker);
  const to = $('[data-filter="month_to"]', picker);

  const load = async () => {
    const box = $("#report");
    try {
      const r = await api.get("/auth/me/report/", { month_from: from.value, month_to: to.value });
      if (!box.isConnected) return; // ушли со страницы, пока грузилось
      renderReport(box, r, periodLabel(from.value, to.value) || t("report.periodAll"));
    } catch (err) {
      box.innerHTML = `<div class="card empty error">${esc(err.message)}</div>`;
    }
  };
  from.addEventListener("input", load);
  load();
}

function renderReport(box, r, period) {
  const currency = esc(t("currency"));
  const cards = [
    statCard({
      eyebrow: period,
      title: t("report.sales"),
      icon: "receipt_long",
      value: Number(r.sales.count).toLocaleString(i18n.locale),
      foot: `${icon("inventory_2")}<span>${esc(t("report.itemsSold"))}</span><b>${r.sales.items_sold}</b>`,
    }),
    statCard({
      accent: "secondary",
      eyebrow: period,
      title: t("report.revenue"),
      icon: "account_balance_wallet",
      value: money(r.sales.total),
      unit: currency,
      foot: `${icon("analytics")}<span>${esc(t("dash.avgCheck"))}</span><b>${money(r.sales.avg_check)} ${currency}</b>`,
    }),
    statCard({
      eyebrow: t("dash.todayEyebrow"),
      title: t("report.today"),
      icon: "point_of_sale",
      value: money(r.today.total),
      unit: currency,
      foot: `${icon("receipt")}<span>${esc(t("dash.receipts", { n: r.today.count }))}</span>`,
    }),
  ];
  if (r.purchases) {
    cards.push(
      statCard({
        accent: "secondary",
        eyebrow: period,
        title: t("report.purchases"),
        icon: "inventory",
        value: money(r.purchases.total),
        unit: currency,
        foot: `${icon("shopping_cart")}<span>${esc(t("report.purchasesCount", { n: r.purchases.count }))}</span>`,
      })
    );
  }

  box.innerHTML = `
    <div class="dash">
      <div class="stats${cards.length === 4 ? " four" : ""}">${cards.join("")}</div>
      <div class="charts">
        ${panel({
          icon: "bar_chart",
          iconCls: "primary",
          title: t("report.chart"),
          hint: t("dash.salesChartHint"),
          count: `<button type="button" class="icon-btn" id="sales-toggle" title="${esc(t("dash.showTable"))}">${icon("table_chart")}</button>`,
          table: `<div class="chart-box" id="sales-chart"></div>
            <div class="hidden" id="sales-table">${simpleTable(
              [t("date"), { label: t("dash.revenue"), cls: "num" }, { label: t("qty"), cls: "num" }],
              r.sales_by_day.map((d) => [dateOnly(d.date), money(d.total), d.count])
            )}</div>`,
        })}
        ${rankingPanel(r.medicine_ranking, t("rank.myTitle"), period)}
      </div>
      <div class="charts">
        ${weekPanel(t("week.myTitle"))}
        ${panel({
          icon: "donut_large",
          iconCls: "primary",
          title: t("dash.donut"),
          hint: `${period} · ${t("dash.donutHint")}`,
          count: "",
          table: `<div class="donut-box" data-donut></div>`,
        })}
      </div>
      ${panel({
        icon: "history",
        iconCls: "primary",
        title: t("report.recent"),
        hint: t("report.recentHint"),
        count: r.sales.cancelled ? badge(t("report.cancelled", { n: r.sales.cancelled }), "bad") : "",
        table: simpleTable(
          [t("number"), t("date"), { label: t("pur.items"), cls: "num" }, { label: t("amount"), cls: "num" }, t("status")],
          r.recent_orders.map((o) => [`<b>${esc(o.order_number)}</b>`, dateTime(o.created_at), o.items_count, money(o.total_price), statusBadge(o.status)])
        ),
        link: can("order.view") ? ["#/orders", t("report.allSales")] : null,
      })}
    </div>`;
  setupSalesChart(r.sales_by_day);
  mountWeek($("[data-week]", box), r.week, (date) => api.get("/auth/me/week/", { date }));
  drawDonut($("[data-donut]", box), r.finance); // мои продажи = расходы + прибыль
}

// ---------------------------------------------------------------- Отчёты

const REPORT_LIST_ROWS = 50; // в полном отчёте длинные списки обрезаем, в файлах и на отдельной странице — полностью
let reportPeriod = null; // выбранные месяцы общие для всех отчётов: {from, to}

// Кнопки страницы «Отчёты»: вид отчёта -> части (section API); all — полный отчёт
const REPORT_KINDS = [
  {
    kind: "sales",
    perm: "order.view",
    icon: "point_of_sale",
    sections: [
      ["all", "summarize"],
      ["months", "calendar_month"],
      ["medicines", "medication"],
      ["sellers", "badge", "order.view_all"], // фармацевт видит только свои продажи — разбивки по продавцам нет
      ["list", "receipt_long"],
    ],
  },
  {
    kind: "purchases",
    perm: "purchase.view",
    icon: "shopping_cart",
    sections: [
      ["all", "summarize"],
      ["months", "calendar_month"],
      ["suppliers", "local_shipping"],
      ["medicines", "medication"],
      ["list", "inventory"],
    ],
  },
];

const reportLabel = (kind, section) =>
  section === "all" ? t("rep.all") : section === "list" ? t(`rep.${kind}.list`) : t(`rep.${section}`);
const reportHint = (kind, section) => (section === "all" ? t("rep.all.hint") : t(`rep.${kind}.${section}.hint`));


function reportCell(value, type) {
  if (value === "" || value === null || value === undefined) return "";
  if (type === "money") return money(value);
  if (type === "int") return Number(value).toLocaleString(i18n.locale);
  if (type === "pct") return `${Number(value).toLocaleString(i18n.locale, { maximumFractionDigits: 1 })} %`;
  if (type === "datetime") return dateTime(value);
  return esc(value);
}

function reportHtml(r, limit = REPORT_LIST_ROWS) {
  const meta = `<div class="report-meta">${r.meta.map(([k, v]) => `<span><b>${esc(k)}:</b> ${esc(v)}</span>`).join("")}</div>`;
  const sections = r.sections.map((s) => {
    if (s.type === "summary") {
      return `<div class="report-kpis">${s.rows
        .map(([label, value, type]) => `<div class="card report-kpi"><span>${esc(label)}</span><b>${reportCell(value, type)}</b></div>`)
        .join("")}</div>`;
    }
    const cols = s.columns.map((c) => ({ label: c.label, cls: c.type === "text" || c.type === "datetime" ? "" : "num" }));
    const shown = s.rows.slice(0, limit);
    const rows = shown.map((row) => row.map((v, i) => reportCell(v, s.columns[i].type)));
    let table = simpleTable(cols, rows);
    if (s.rows.length && s.total) {
      // строка «Итого» — последней строкой той же таблицы
      table = table.replace("</tbody>", `<tr class="total-row">${s.total.map((v, i) => `<td class="${cols[i].cls}">${reportCell(v, s.columns[i].type)}</td>`).join("")}</tr></tbody>`);
    }
    const more = s.rows.length > limit ? `<p class="muted report-more">${esc(t("rep.more", { n: s.rows.length - limit }))}</p>` : "";
    return `<div class="card report-section"><h3>${esc(s.title)}</h3>${table}${more}</div>`;
  });
  return meta + sections.join("");
}

function renderReports() {
  const groups = REPORT_KINDS.filter((g) => can(g.perm));
  $("#view").innerHTML = `<div class="report-groups">${groups
    .map(
      (g) => `
      <section class="report-group">
        <h3>${icon(g.icon)}${esc(t(`rep.${g.kind}`))}</h3>
        <div class="report-tiles">${g.sections
          .filter(([, , perm]) => !perm || can(perm))
          .map(
            ([section, ic], i) => `
            <a class="card report-tile${section === "all" ? " main" : ""}" href="#/report?kind=${g.kind}&section=${section}" style="animation-delay:${i * 40}ms">
              <span class="report-tile-icon">${icon(ic)}</span>
              <span class="report-tile-text"><b>${esc(reportLabel(g.kind, section))}</b><span>${esc(reportHint(g.kind, section))}</span></span>
              ${icon("chevron_right")}
            </a>`
          )
          .join("")}</div>
      </section>`
    )
    .join("")}</div>`;
}

function renderReportPage(params) {
  const view = $("#view");
  const group = REPORT_KINDS.find((g) => g.kind === params.get("kind") && can(g.perm));
  const entry = group?.sections.find(([s, , perm]) => s === params.get("section") && (!perm || can(perm)));
  if (!group || !entry) {
    location.hash = "#/reports";
    return;
  }
  const { kind } = group;
  const section = entry[0];
  $("#page-title").textContent = `${t(`rep.${kind}`)}: ${reportLabel(kind, section)}`;
  $("#page-subtitle").textContent = reportHint(kind, section);
  setActions(`<a class="btn" href="#/reports">${icon("arrow_back")}${esc(t("rep.back"))}</a>`);

  const current = ymOf(new Date());
  reportPeriod ||= { from: current, to: current }; // по умолчанию — текущий месяц
  view.innerHTML = `
    <div class="card report-bar">
      ${monthPickerHtml()}
      ${isAdmin() ? `<select data-pharmacy><option value="">${esc(t("user.allPharmacies"))}</option>${Object.entries(pharmacyNames)
        .map(([id, name]) => `<option value="${id}">${esc(name)}</option>`).join("")}</select>` : ""}
      <div class="report-export">
        <span class="muted">${esc(t("rep.download"))}:</span>
        <button type="button" class="btn sm" data-export="xlsx">${icon("table_view")}Excel</button>
        <button type="button" class="btn sm" data-export="docx">${icon("description")}Word</button>
        <button type="button" class="btn sm" data-export="pdf">${icon("picture_as_pdf")}PDF</button>
      </div>
    </div>
    <div class="report" data-report></div>`;

  const picker = $("[data-month-picker]", view);
  const fromInput = $('[data-filter="month_from"]', picker);
  const toInput = $('[data-filter="month_to"]', picker);
  fromInput.value = reportPeriod.from;
  toInput.value = reportPeriod.to;
  setupMonthPicker(picker);

  const box = $("[data-report]", view);
  const filters = () => ({
    month_from: fromInput.value,
    month_to: toInput.value,
    pharmacy: $("[data-pharmacy]", view)?.value || "",
    section: section === "all" ? "" : section,
  });

  let seq = 0;
  async function load() {
    const my = ++seq;
    box.classList.add("loading");
    try {
      const r = await api.get(`/reports/${kind}/`, filters());
      if (my !== seq) return; // пришёл ответ на устаревший запрос
      box.innerHTML = reportHtml(r, section === "all" ? REPORT_LIST_ROWS : Infinity);
    } catch (e) {
      if (my === seq) box.innerHTML = `<div class="card empty">${esc(e.message)}</div>`;
    } finally {
      if (my === seq) box.classList.remove("loading");
    }
  }

  fromInput.addEventListener("input", () => {
    reportPeriod = { from: fromInput.value, to: toInput.value };
    load();
  });
  $("[data-pharmacy]", view)?.addEventListener("change", load);
  $(".report-export", view).addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-export]");
    if (!btn || btn.disabled) return;
    btn.disabled = true;
    try {
      const name = await api.download(`/reports/${kind}/`, { ...filters(), export: btn.dataset.export });
      toast(t("rep.downloaded", { name }));
    } catch (err) {
      toast(err.message, true);
    } finally {
      btn.disabled = false;
    }
  });

  load();
}

// ---------------------------------------------------------------- ИИ-ассистент

let chatHistory = []; // [{role: "user"|"assistant", content}] — живёт до выхода из аккаунта

// Небольшой markdown ответа модели: заголовки, **жирный**, `код`, списки, таблицы. Сначала всё экранируется.
function renderMarkdown(text) {
  const inline = (s) =>
    esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<b>$1</b>")
      .replace(/`([^`]+)`/g, "<code>$1</code>");
  const cells = (row) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
  const lines = text.replace(/\r/g, "").split("\n");
  const out = [];
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*\|.*\|\s*$/.test(line) && /^\s*\|?[\s:|-]+\|?\s*$/.test(lines[i + 1] || "")) {
      const head = cells(line);
      const body = [];
      for (i += 2; i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i]); i++) body.push(cells(lines[i]));
      i--;
      out.push(`<div class="table-wrap"><table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join("")}</tr></thead>
        <tbody>${body.map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join("")}</tr>`).join("")}</tbody></table></div>`);
    } else if (/^\s*([-*•]|\d+[.)])\s+/.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items = [];
      for (; i < lines.length && /^\s*([-*•]|\d+[.)])\s+/.test(lines[i]); i++) items.push(lines[i].replace(/^\s*([-*•]|\d+[.)])\s+/, ""));
      i--;
      const tag = ordered ? "ol" : "ul";
      out.push(`<${tag}>${items.map((it) => `<li>${inline(it)}</li>`).join("")}</${tag}>`);
    } else if (/^#{1,6}\s/.test(line)) {
      out.push(`<h4>${inline(line.replace(/^#+\s*/, ""))}</h4>`);
    } else if (line.trim()) {
      out.push(`<p>${inline(line)}</p>`);
    }
  }
  return out.join("");
}

// Окно чата открывается кнопкой в правом нижнем углу поверх любой страницы.
// Строится заново в renderShell (вход, смена языка); история и ожидание ответа — в chat, поэтому не теряются.
const chat = { pending: false, error: null };

function renderAssistant() {
  const panel = $("#ai-panel");
  const suggestions = [
    can("pharmacy.stats") && "ai.q.week",
    can("medicine.view") && "ai.q.lowStock",
    can("medicine.view") && "ai.q.expiring",
    me.pharmacy && "ai.q.mySales", // у админа проекта личных продаж нет
    "ai.q.analog",
  ].filter(Boolean);

  $("#ai-fab").title = t("nav.assistant");
  panel.innerHTML = `
    <div class="ai-head">
      <div class="msg-avatar">${icon("smart_toy")}</div>
      <div class="ai-title"><b>${esc(t("nav.assistant"))}</b><span>${esc(t("ai.status"))}</span></div>
      <button type="button" class="icon-btn" data-clear title="${esc(t("ai.clear"))}">${icon("edit_square")}</button>
      <button type="button" class="icon-btn" data-ai-close title="${esc(t("close"))}">${icon("close")}</button>
    </div>
    <div class="chat-log" data-log></div>
    <form class="chat-input" data-form>
      <textarea name="q" rows="1" maxlength="4000" placeholder="${esc(t("ai.placeholder"))}" required></textarea>
      <button type="submit" class="btn primary" title="${esc(t("ai.send"))}">${icon("send")}</button>
    </form>
    <p class="chat-note">${icon("info")}${esc(t("ai.note"))}</p>`;

  const form = $("[data-form]", panel);
  const input = form.elements.q;

  const bubble = (m) => `
    <div class="msg ${m.role}${m.error ? " error" : ""}">
      ${m.role === "assistant" ? `<div class="msg-avatar">${icon("smart_toy")}</div>` : ""}
      <div class="msg-body">${m.role === "assistant" && !m.error ? renderMarkdown(m.content) : `<p>${esc(m.content)}</p>`}${
        m.open ? `<a class="btn sm msg-open" href="#/${m.open}" data-open>${icon("open_in_new")}${esc(t(`nav.${m.open}`))}</a>` : ""
      }</div>
    </div>`;

  // страницу, которую ассистент открыл по просьбе, показываем сразу; на телефоне окно чата закрываем, чтобы её было видно
  function openPage(path) {
    if (!allowedRoutes().some((r) => r.path === path)) return;
    if (location.hash === `#/${path}`) route();
    else location.hash = `#/${path}`;
    if (!matchMedia("(min-width: 601px)").matches) toggleAssistant(false);
  }

  function draw() {
    const log = $("#ai-panel [data-log]");
    log.innerHTML = chatHistory.length
      ? chatHistory.map(bubble).join("") +
        (chat.error ? bubble({ role: "assistant", content: chat.error, error: true }) : "") +
        (chat.pending ? `<div class="msg assistant"><div class="msg-avatar">${icon("smart_toy")}</div><div class="msg-body typing"><span></span><span></span><span></span></div></div>` : "")
      : `<div class="chat-welcome">
          <div class="chat-hello">${icon("smart_toy")}</div>
          <h3>${esc(t("ai.hello"))}</h3>
          <p>${esc(t("ai.intro"))}</p>
          <div class="chat-chips">${suggestions.map((k) => `<button type="button" class="chip" data-ask="${esc(t(k))}">${esc(t(k))}</button>`).join("")}</div>
        </div>`;
    log.scrollTop = log.scrollHeight;
    $("#ai-panel [data-form] button").disabled = chat.pending;
  }

  async function ask(text) {
    text = text.trim();
    if (!text || chat.pending) return;
    chatHistory.push({ role: "user", content: text });
    Object.assign(chat, { pending: true, error: null });
    input.value = "";
    input.style.height = "";
    draw();
    const history = chatHistory; // «Новый чат» или выход во время ожидания — поздний ответ уже не нужен
    try {
      const messages = history.map(({ role, content }) => ({ role, content }));
      const { reply, open } = await api.post("/assistant/", { messages });
      history.push({ role: "assistant", content: reply || "…", open });
      if (open && history === chatHistory) openPage(open);
    } catch (e) {
      if (history === chatHistory) chat.error = e.message;
    } finally {
      if (history === chatHistory) {
        chat.pending = false;
        draw();
      }
    }
  }

  form.addEventListener("submit", (e) => {
    e.preventDefault();
    ask(input.value);
  });
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      ask(input.value);
    }
  });
  input.addEventListener("input", () => {
    input.style.height = "";
    input.style.height = `${Math.min(input.scrollHeight, 120)}px`;
  });
  $("[data-log]", panel).addEventListener("click", (e) => {
    const chip = e.target.closest("[data-ask]");
    if (chip) ask(chip.dataset.ask);
    const link = e.target.closest("[data-open]");
    if (link) {
      e.preventDefault();
      openPage(link.getAttribute("href").slice(2));
    }
  });
  $("[data-clear]", panel).addEventListener("click", () => {
    chatHistory = [];
    Object.assign(chat, { pending: false, error: null });
    draw();
    input.focus();
  });
  $("[data-ai-close]", panel).addEventListener("click", () => toggleAssistant(false));

  draw();
}

function toggleAssistant(open = $("#ai-panel").classList.contains("hidden")) {
  $("#ai-panel").classList.toggle("hidden", !open);
  $("#ai-fab").classList.toggle("open", open);
  document.body.classList.toggle("ai-open", open); // на телефоне окно на весь экран — прокрутку страницы гасим
  if (open) {
    aiDrag.placePanel();
    const log = $("#ai-panel [data-log]");
    log.scrollTop = log.scrollHeight;
    if (matchMedia("(min-width: 601px)").matches) $("#ai-panel textarea").focus(); // на телефоне не открываем клавиатуру сразу
  }
}

// Ассистента можно перетащить: кнопку — куда угодно, окно — за шапку. Двойной клик по шапке — вернуть на место.
// Положение — доля от размеров окна браузера (не «уезжает» при смене размера), запоминается в этом браузере.
const aiDrag = (() => {
  const MARGIN = 8;
  const GAP = 12;
  const fab = $("#ai-fab");
  const panel = $("#ai-panel");
  const phone = () => matchMedia("(max-width: 600px)").matches; // на телефоне окно на весь экран
  const store = {
    get(key) {
      try {
        return JSON.parse(localStorage.getItem(key));
      } catch {
        return null;
      }
    },
    set(key, value) {
      try {
        if (value) localStorage.setItem(key, JSON.stringify(value));
        else localStorage.removeItem(key);
      } catch {
        // приватный режим и т.п. — просто не запоминаем
      }
    },
  };
  const clamp = (el, x, y) => [
    Math.min(Math.max(MARGIN, x), window.innerWidth - el.offsetWidth - MARGIN),
    Math.min(Math.max(MARGIN, y), window.innerHeight - el.offsetHeight - MARGIN),
  ];
  const put = (el, x, y) => {
    [x, y] = clamp(el, x, y);
    Object.assign(el.style, { left: `${x}px`, top: `${y}px`, right: "auto", bottom: "auto" });
    return [x, y];
  };
  const save = (key, el) => {
    const r = el.getBoundingClientRect();
    store.set(key, { fx: r.left / window.innerWidth, fy: r.top / window.innerHeight });
  };
  const restore = (key, el) => {
    const p = store.get(key);
    if (!p) return false;
    put(el, p.fx * window.innerWidth, p.fy * window.innerHeight);
    return true;
  };
  const reset = (el) => ["left", "top", "right", "bottom"].forEach((side) => (el.style[side] = ""));

  // окно — там, куда его перетащили; иначе рядом с кнопкой: над ней (или под, если сверху мало места)
  function placePanel() {
    if (phone()) return reset(panel);
    if (restore("ai.panel", panel)) return;
    if (!fab.style.left) return reset(panel); // кнопка на своём месте — окно тоже (по CSS)
    const f = fab.getBoundingClientRect();
    const w = panel.offsetWidth;
    const h = panel.offsetHeight;
    const y = f.top - GAP - h >= MARGIN ? f.top - GAP - h : f.bottom + GAP;
    const x = f.left + f.width / 2 > window.innerWidth / 2 ? f.right - w : f.left;
    put(panel, x, y);
  }

  // общий перетаскиватель: мышь и палец; «клик» — если сдвинули меньше чем на 5px
  function draggable(handle, el, { canStart = () => true, onMove = () => {}, onEnd }) {
    let suppressClickUntil = 0;
    // перетащили кнопку — отпускание не должно открывать/закрывать окно
    handle.addEventListener("click", (ev) => Date.now() < suppressClickUntil && ev.stopImmediatePropagation(), true);
    handle.addEventListener("pointerdown", (e) => {
      if (e.button !== 0 || !canStart(e)) return;
      const start = el.getBoundingClientRect();
      const [sx, sy] = [e.clientX, e.clientY];
      let moved = false;
      const move = (ev) => {
        const dx = ev.clientX - sx;
        const dy = ev.clientY - sy;
        if (!moved && Math.hypot(dx, dy) < 5) return;
        if (!moved) {
          moved = true;
          handle.setPointerCapture(e.pointerId);
          el.classList.add("dragging");
        }
        put(el, start.left + dx, start.top + dy);
        onMove();
      };
      const up = () => {
        handle.removeEventListener("pointermove", move);
        handle.removeEventListener("pointerup", up);
        handle.removeEventListener("pointercancel", up);
        if (!moved) return;
        el.classList.remove("dragging");
        suppressClickUntil = Date.now() + 300; // клик сразу после перетаскивания — не клик
        onEnd();
      };
      handle.addEventListener("pointermove", move);
      handle.addEventListener("pointerup", up);
      handle.addEventListener("pointercancel", up);
    });
  }

  draggable(fab, fab, {
    onMove: () => {
      store.set("ai.panel", null); // окно снова следует за кнопкой
      if (!panel.classList.contains("hidden")) placePanel();
    },
    onEnd: () => save("ai.fab", fab),
  });
  // шапка окна (её содержимое перерисовывается — поэтому слушаем само окно)
  draggable(panel, panel, {
    canStart: (e) => !phone() && e.target.closest(".ai-head") && !e.target.closest("button, a, input, select"),
    onEnd: () => save("ai.panel", panel),
  });
  panel.addEventListener("dblclick", (e) => {
    if (!e.target.closest(".ai-head") || e.target.closest("button")) return;
    store.set("ai.panel", null);
    store.set("ai.fab", null);
    reset(fab);
    placePanel();
  });

  restore("ai.fab", fab);
  window.addEventListener("resize", () => {
    if (fab.style.left) put(fab, fab.offsetLeft, fab.offsetTop);
    if (!panel.classList.contains("hidden")) placePanel();
  });
  return { placePanel };
})();

// ---------------------------------------------------------------- Роутинг

const ROUTES = [
  { path: "dashboard", icon: "dashboard", perm: "pharmacy.stats", render: renderDashboard },
  { path: "orders", icon: "point_of_sale", perm: "order.view", render: renderOrders },
  { path: "sale", parent: "orders", perm: "order.create", render: renderNewSale }, // не в меню
  { path: "order", parent: "orders", perm: "order.view", render: renderOrderDoc }, // одна продажа
  { path: "medicines", icon: "medication", perm: "medicine.view", render: renderMedicines },
  { path: "expiry", parent: "medicines", perm: "medicine.view", render: renderExpiry }, // не в меню: кнопка на странице лекарств
  { path: "writeoff", parent: "medicines", perm: "writeoff.view", render: renderWriteOff }, // не в меню: кнопка на странице сроков
  { path: "writeoff-month", parent: "medicines", perm: "writeoff.view", render: renderWriteOffMonth }, // один месяц срока
  { path: "writeoff-doc", parent: "medicines", perm: "writeoff.view", render: renderWriteOffDoc }, // один документ списания
  { path: "purchases", icon: "shopping_cart", perm: "purchase.view", render: renderPurchases },
  { path: "purchase", parent: "purchases", perm: "purchase.create", render: renderNewPurchase }, // не в меню
  { path: "suppliers", icon: "local_shipping", perm: "supplier.view", render: renderSuppliers },
  { path: "users", icon: "badge", perm: "user.view", render: renderUsers },
  { path: "access", parent: "users", perm: "user.view", render: renderAccess }, // не в меню: кнопка «Доступ» у фармацевта
  { path: "pharmacies", icon: "store", perm: "pharmacy.view", render: renderPharmacies },
  { path: "reports", icon: "summarize", perm: "order.view", render: renderReports }, // кнопки «Закупки» — по purchase.view
  { path: "report", parent: "reports", perm: "order.view", render: renderReportPage }, // не в меню: один отчёт
  { path: "chat", icon: "forum", render: renderChat }, // доступен всем: своя аптека, админ — любая
  { path: "profile", icon: "person", render: renderProfile }, // доступен всем
];

const allowedRoutes = () => ROUTES.filter((r) => !r.perm || can(r.perm));

function route() {
  if (!me) return;
  const [path, queryString] = location.hash.replace(/^#\/?/, "").split("?");
  const routes = allowedRoutes();
  const current = routes.find((r) => r.path === path) || routes.find((r) => !r.parent);

  if (!current) {
    $("#page-title").textContent = "";
    $("#page-subtitle").textContent = "";
    $("#view").innerHTML = `<div class="card empty">${esc(t("noAccess"))}</div>`;
    return;
  }

  $$("#nav a").forEach((a) => a.classList.toggle("active", a.dataset.path === (current.parent || current.path)));
  $("#page-eyebrow").textContent = isAdmin() ? t("user.allPharmacies") : me.pharmacy_name || "";
  $("#page-title").textContent = t(`nav.${current.path}`);
  $("#page-subtitle").textContent = t(`sub.${current.path}`);
  $("#page-actions").innerHTML = "";
  $(".sidebar").classList.remove("open");
  replay($(".page"), "enter");
  current.render(new URLSearchParams(queryString));
}

// ---------------------------------------------------------------- Вход / выход

async function loadPharmacyNames() {
  if (!isAdmin()) {
    pharmacyNames = me.pharmacy ? { [me.pharmacy]: me.pharmacy_name } : {};
    return;
  }
  const list = await api.getAll("/pharmacies/");
  pharmacyNames = Object.fromEntries(list.map((p) => [p.id, p.name]));
}

function renderShell() {
  const name = me.full_name || me.username;
  const place = me.pharmacy_name || t("user.allPharmacies");

  $("#me").innerHTML = `
    <div class="me-top"><span class="me-name">${esc(name)}</span><span class="role-pill">${esc(roleLabel(me.role))}</span></div>
    <span class="me-sub">${esc(place)}</span>`;
  $("#topbar-user").innerHTML = `
    <div class="who"><b>${esc(name)}</b><span>${esc(roleLabel(me.role))}</span></div>
    ${avatarHtml(name, me.avatar_url)}`;
  $("#today").textContent = new Date().toLocaleDateString(i18n.dateLocale, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  $("#nav").innerHTML = allowedRoutes()
    .filter((r) => !r.parent)
    .map((r) => `<a href="#/${r.path}" data-path="${r.path}">${icon(r.icon)}<span>${esc(t(`nav.${r.path}`))}</span></a>`)
    .join("");
  // ассистент есть у всех ролей (право assistant.use); без права — кнопку прячем
  $("#ai-fab").classList.toggle("hidden", !can("assistant.use"));
  if (can("assistant.use")) renderAssistant();
  else toggleAssistant(false);
}

async function startApp(user) {
  me = user;
  await loadPharmacyNames();
  renderShell();

  $("#login-screen").classList.add("hidden");
  $("#app").classList.remove("hidden");
  route();
  api.get("/rates/").catch(() => {}); // сервер начинает подтягивать курсы банков заранее — к открытию профиля они готовы
}

function showLogin() {
  me = null;
  chatHistory = [];
  Object.assign(chat, { pending: false, error: null });
  toggleAssistant(false);
  closeModal();
  $("#app").classList.add("hidden");
  $("#login-screen").classList.remove("hidden");
}

$("#login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  $("#login-error").textContent = "";
  try {
    const user = await api.login(form.username.value.trim(), form.password.value);
    form.reset();
    await startApp(user);
  } catch (err) {
    $("#login-error").textContent = err.status === 401 ? t("login.invalid") : err.message;
  }
});

$("#logout").addEventListener("click", () => {
  api.tokens.clear();
  showLogin();
});

$("#menu-btn").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
// на телефоне меню закрывается кнопкой «назад» в нём или нажатием на затемнённый фон
$("#sidebar-close").addEventListener("click", () => $(".sidebar").classList.remove("open"));
$("#sidebar-backdrop").addEventListener("click", () => $(".sidebar").classList.remove("open"));
$("#ai-fab").addEventListener("click", () => toggleAssistant());

$("#modal").addEventListener("click", (e) => {
  if (e.target.id === "modal" || e.target.closest("[data-close]")) closeModal();
});
document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  if (!$("#modal").classList.contains("hidden")) closeModal();
  else if (!$("#ai-panel").classList.contains("hidden")) toggleAssistant(false);
});

// аватарка с фото (сотрудника или чата) — по клику крупно; раньше остальных обработчиков:
// клик по фото в шапке чата или в ссылке не открывает сведения и не уводит со страницы
document.addEventListener(
  "click",
  (e) => {
    const img = e.target.closest(".has-photo img");
    if (!img) return;
    e.preventDefault();
    e.stopPropagation();
    openMediaViewer([{ kind: "image", url: img.src, caption: img.alt }]);
  },
  true
);

window.addEventListener("hashchange", route);
api.onUnauthorized = showLogin;

setupLangSwitches();
applyStaticI18n();
setTheme(document.documentElement.dataset.theme);

// Автовход, если токен уже сохранён
(async () => {
  if (!api.tokens.access) return showLogin();
  try {
    await startApp(await api.get("/auth/me/"));
  } catch {
    showLogin();
  }
})();
