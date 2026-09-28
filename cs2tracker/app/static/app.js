"use strict";

(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || "";
  // Outside Telegram (a plain browser during development) draw our own buttons.
  const nativeUi = !!initData && tg.platform !== "unknown";
  const ICON_BASE = "https://community.fastly.steamstatic.com/economy/image/";
  const MARKET_URL = "https://steamcommunity.com/market/listings/730/";
  // A CS2 seller gets the buyer's price minus 5% Steam + 10% game fee.
  const STEAM_FEE = 1.15;

  // -- i18n ---------------------------------------------------------------------

  const TEXTS = {
    en: {
      addItem: "Add item", add: "Add", save: "Save", boughtMore: "Buy more", remove: "Remove",
      emptyTitle: "No items yet",
      emptyText: "Add the cases, capsules or skins you bought — you'll see what they're worth now.",
      invested: "Invested", worth: "You'd get now", unpricedCost: "+ {cost} without a price", total: "Total",
      searchPlaceholder: "Case, capsule, skin…",
      searchHint: "Search the Steam market",
      nothingFound: "Nothing found",
      inPortfolio: "In portfolio",
      qty: "Quantity", buyPrice: "Price paid, each",
      now: "On Steam {price} · you'd get {net}", noPrice: "No price on the market right now",
      position: "{qty} at {price}",
      has: "You have {qty} at {price} each.",
      becomes: "Will become {qty} at {price} each.",
      zeroHint: "Set quantity to 0 to remove the item.",
      removeConfirm: "Remove {name} from the portfolio?",
      justNow: "just now", updated: "updated {ago}", unpriced: "{n} without a price",
      kind_sell: "Lowest Steam listing", kind_buy: "Highest Steam buy order",
      afterFee: "after the 15% fee",
      retry: "Tap to try again",
      notInTelegram: "Open this app from the Telegram bot.",
      err_network: "No connection.", err_steam: "Steam isn't responding. Try again in a minute.",
      err_busy: "Steam is busy. Try again in a few seconds.",
      err_auth: "Session expired — close and reopen the app.", err_private: "This bot is private.",
      err_rate: "Too many requests — wait a minute.", err_not_found: "Steam doesn't know this item.",
      err_full: "Your portfolio is full.", err_gone: "This item is no longer in your portfolio.",
      err_too_many: "That's more than 1,000,000 of one item.",
      err_invalid: "Check the numbers.", err_generic: "Something went wrong.",
    },
    ru: {
      addItem: "Добавить предмет", add: "Добавить", save: "Сохранить", boughtMore: "Докупить", remove: "Убрать",
      emptyTitle: "Пока пусто",
      emptyText: "Добавьте кейсы, капсулы или скины, которые купили, — увидите, сколько они стоят сейчас.",
      invested: "Вложено", worth: "Получите сейчас", unpricedCost: "+ {cost} без цены", total: "Итого",
      searchPlaceholder: "Кейс, капсула, скин…",
      searchHint: "Поиск по торговой площадке Steam",
      nothingFound: "Ничего не найдено",
      inPortfolio: "В портфеле",
      qty: "Количество", buyPrice: "Цена покупки за шт.",
      now: "На Steam {price} · вы получите {net}", noPrice: "Сейчас на рынке нет цены",
      position: "{qty} шт. · по {price}",
      has: "У вас {qty} шт. по {price}.",
      becomes: "Станет {qty} шт. по {price}.",
      zeroHint: "Чтобы убрать предмет, поставьте количество 0.",
      removeConfirm: "Убрать {name} из портфеля?",
      justNow: "только что", updated: "обновлено {ago}", unpriced: "без цены: {n}",
      kind_sell: "Мин. цена продажи Steam", kind_buy: "Макс. заявка на покупку Steam",
      afterFee: "за вычетом комиссии 15%",
      retry: "Нажмите, чтобы повторить",
      notInTelegram: "Откройте приложение из Telegram-бота.",
      err_network: "Нет соединения.", err_steam: "Steam не отвечает. Попробуйте через минуту.",
      err_busy: "Steam занят. Попробуйте через пару секунд.",
      err_auth: "Сессия истекла — закройте и откройте приложение снова.", err_private: "Это приватный бот.",
      err_rate: "Слишком много запросов — подождите минуту.", err_not_found: "Steam не знает такой предмет.",
      err_full: "Портфель заполнен.", err_gone: "Этого предмета уже нет в портфеле.",
      err_too_many: "Больше 1 000 000 штук одного предмета.",
      err_invalid: "Проверьте числа.", err_generic: "Что-то пошло не так.",
    },
    uk: {
      addItem: "Додати предмет", add: "Додати", save: "Зберегти", boughtMore: "Докупити", remove: "Прибрати",
      emptyTitle: "Поки порожньо",
      emptyText: "Додайте кейси, капсули чи скіни, які купили, — побачите, скільки вони коштують зараз.",
      invested: "Вкладено", worth: "Отримаєте зараз", unpricedCost: "+ {cost} без ціни", total: "Разом",
      searchPlaceholder: "Кейс, капсула, скін…",
      searchHint: "Пошук на торговому майданчику Steam",
      nothingFound: "Нічого не знайдено",
      inPortfolio: "У портфелі",
      qty: "Кількість", buyPrice: "Ціна купівлі за шт.",
      now: "У Steam {price} · ви отримаєте {net}", noPrice: "Зараз на ринку немає ціни",
      position: "{qty} шт. · по {price}",
      has: "У вас {qty} шт. по {price}.",
      becomes: "Стане {qty} шт. по {price}.",
      zeroHint: "Щоб прибрати предмет, поставте кількість 0.",
      removeConfirm: "Прибрати {name} з портфеля?",
      justNow: "щойно", updated: "оновлено {ago}", unpriced: "без ціни: {n}",
      kind_sell: "Мін. ціна продажу Steam", kind_buy: "Макс. заявка на купівлю Steam",
      afterFee: "за вирахуванням комісії 15%",
      retry: "Натисніть, щоб повторити",
      notInTelegram: "Відкрийте застосунок з Telegram-бота.",
      err_network: "Немає з'єднання.", err_steam: "Steam не відповідає. Спробуйте за хвилину.",
      err_busy: "Steam зайнятий. Спробуйте за кілька секунд.",
      err_auth: "Сесія завершилася — закрийте й відкрийте застосунок знову.", err_private: "Це приватний бот.",
      err_rate: "Забагато запитів — зачекайте хвилину.", err_not_found: "Steam не знає такого предмета.",
      err_full: "Портфель заповнений.", err_gone: "Цього предмета вже немає в портфелі.",
      err_too_many: "Більше 1 000 000 штук одного предмета.",
      err_invalid: "Перевірте числа.", err_generic: "Щось пішло не так.",
    },
  };
  const userLang = ((tg && tg.initDataUnsafe && tg.initDataUnsafe.user && tg.initDataUnsafe.user.language_code)
    || navigator.language || "en").slice(0, 2).toLowerCase();
  const lang = TEXTS[userLang] ? userLang : "en";
  const locale = { en: "en-US", ru: "ru-RU", uk: "uk-UA" }[lang];
  document.documentElement.lang = lang;

  function t(key, vars) {
    const s = TEXTS[lang][key] || TEXTS.en[key] || key;
    return vars ? s.replace(/\{(\w+)\}/g, (_, k) => vars[k]) : s;
  }

  // -- money --------------------------------------------------------------------

  const formats = new Map();
  function numberFormat(options) {
    const key = JSON.stringify(options);
    if (!formats.has(key)) formats.set(key, new Intl.NumberFormat(locale, options));
    return formats.get(key);
  }

  function money(value, sign = false) {
    if (value == null) return "—";
    const digits = Math.abs(value) >= 1000 ? 0 : 2;
    return numberFormat({
      style: "currency", currency: state.currency, currencyDisplay: "narrowSymbol",
      minimumFractionDigits: digits, maximumFractionDigits: digits,
      signDisplay: sign ? "exceptZero" : "auto",
    }).format(value);
  }

  function percent(ratio) {
    return numberFormat({ style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(ratio);
  }

  function trend(ratio) {
    return ratio > 0.0005 ? "up" : ratio < -0.0005 ? "down" : "hint";
  }

  // What selling one item would bring after Steam's fee.
  function net(price) {
    return price == null ? null : Math.floor((price / STEAM_FEE) * 100) / 100;
  }

  function ago(seconds) {
    const s = Math.max(0, Date.now() / 1000 - seconds);
    if (s < 60) return t("justNow");
    const rtf = new Intl.RelativeTimeFormat(locale, { numeric: "auto" });
    if (s < 3600) return rtf.format(-Math.floor(s / 60), "minute");
    if (s < 86400) return rtf.format(-Math.floor(s / 3600), "hour");
    return rtf.format(-Math.floor(s / 86400), "day");
  }

  const decimalMark = numberFormat({}).formatToParts(1.5).find((p) => p.type === "decimal").value;

  // Accepts "464", "12,5", "1 234,50", "1,234.50", "1.234,50"; rounds to cents.
  function parseAmount(text) {
    let s = String(text).replace(/[\s']/g, "");
    if (!/^[\d.,]+$/.test(s)) return null;
    const last = Math.max(s.lastIndexOf("."), s.lastIndexOf(","));
    if (last !== -1) {
      const frac = s.slice(last + 1);
      const head = s.slice(0, last).replace(/[.,]/g, "");
      // "1,234" / "1.234": a lone separator with 3 digits after it groups thousands.
      const grouping = frac.length === 3 && !/[.,]/.test(s.slice(0, last)) && s[last] !== decimalMark;
      s = grouping ? head + frac : `${head}.${frac}`;
    }
    const n = Math.round(Number(s) * 100) / 100;
    return Number.isFinite(n) && n <= 1e8 ? n : null;
  }

  function parseQty(text) {
    const s = String(text).replace(/\s/g, "");
    if (!/^\d{1,7}$/.test(s)) return null;
    const n = Number(s);
    return n <= 1e6 ? n : null;
  }

  function plainAmount(value) {
    // What a user would type: "464" or "12,5", no grouping or currency.
    return String(Math.round(value * 100) / 100).replace(".", decimalMark);
  }

  // -- DOM helpers --------------------------------------------------------------

  function h(tag, attrs, ...children) {
    const el = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v == null || v === false) continue;
      if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
      else if (k === "class") el.className = v;
      else el.setAttribute(k, v === true ? "" : v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }

  function thumb(icon, size = 96) {
    if (!icon) return h("div", { class: "thumb placeholder", "aria-hidden": "true" });
    return h("img", {
      class: "thumb", alt: "", loading: "lazy", decoding: "async",
      src: `${ICON_BASE}${encodeURIComponent(icon)}/${size}fx${size}f`,
    });
  }

  function tappable(el, handler) {
    el.setAttribute("role", "button");
    el.tabIndex = 0;
    el.addEventListener("click", handler);
    el.addEventListener("keydown", (e) => { if (e.key === "Enter") handler(e); });
    return el;
  }

  const app = document.getElementById("app");
  function mount(nodes, { keepScroll = false } = {}) {
    const y = window.scrollY;
    app.replaceChildren(...nodes.filter(Boolean));
    window.scrollTo(0, keepScroll ? y : 0);
  }

  // -- Telegram glue ------------------------------------------------------------

  function call(fn, version) {
    if (!initData || (version && !tg.isVersionAtLeast(version))) return undefined;
    try { return fn(); } catch (e) { return undefined; }
  }

  const haptic = {
    tap: () => call(() => tg.HapticFeedback.selectionChanged(), "6.1"),
    ok: () => call(() => tg.HapticFeedback.notificationOccurred("success"), "6.1"),
    fail: () => call(() => tg.HapticFeedback.notificationOccurred("error"), "6.1"),
  };

  function alertUser(text) {
    if (nativeUi && tg.isVersionAtLeast("6.2")) tg.showAlert(text);
    else window.alert(text);
  }

  function confirmUser(text) {
    return new Promise((resolve) => {
      if (nativeUi && tg.isVersionAtLeast("6.2")) tg.showConfirm(text, (ok) => resolve(!!ok));
      else resolve(window.confirm(text));
    });
  }

  function themeColor(key, fallback) {
    return (tg && tg.themeParams && tg.themeParams[key]) || fallback;
  }

  // One handler per bottom button for the app's lifetime; screens only swap the
  // action, so no stale handlers pile up.
  function bottomButton(native, fallbackEl, { primary = true } = {}) {
    let action = null;
    let active = false;
    const button = {
      set(text, onTap, { enabled = true, busy = false, danger = false } = {}) {
        action = text ? onTap : null;
        active = !!text && enabled && !busy;
        if (native) {
          // hideProgress() re-activates the button, so it must come before setParams.
          if (!busy) native.hideProgress();
          if (!text) { native.hide(); return; }
          const params = { text, is_active: active, is_visible: true };
          if (primary) {
            // Some clients barely dim an inactive button, so recolour it.
            params.color = !active && !busy ? themeColor("hint_color", "#8e8e93")
              : danger ? themeColor("destructive_text_color", "#e53935")
              : themeColor("button_color", "#2481cc");
            params.text_color = themeColor("button_text_color", "#ffffff");
          }
          native.setParams(params);
          if (busy) native.showProgress(false);
        } else if (fallbackEl) {
          fallbackEl.hidden = !text;
          fallbackEl.textContent = busy ? "…" : text || "";
          fallbackEl.disabled = !active;
          fallbackEl.classList.toggle("danger", danger);
        }
      },
      tap() {
        if (action && active) action();
        else if (action) haptic.fail();
      },
    };
    if (native) native.onClick(button.tap);
    if (fallbackEl) fallbackEl.addEventListener("click", button.tap);
    return button;
  }

  function setBack(visible) {
    if (nativeUi) visible ? tg.BackButton.show() : tg.BackButton.hide();
  }

  function applySafeArea() {
    const top = ((tg.safeAreaInset && tg.safeAreaInset.top) || 0)
      + ((tg.contentSafeAreaInset && tg.contentSafeAreaInset.top) || 0);
    const bottom = ((tg.safeAreaInset && tg.safeAreaInset.bottom) || 0)
      + ((tg.contentSafeAreaInset && tg.contentSafeAreaInset.bottom) || 0);
    document.documentElement.style.setProperty("--safe-top", `${top}px`);
    document.documentElement.style.setProperty("--safe-bottom", `${bottom}px`);
  }

  function applyTheme() {
    document.documentElement.dataset.scheme = (tg && tg.colorScheme) || "light";
    call(() => tg.setHeaderColor("secondary_bg_color"), "6.1");
    call(() => tg.setBackgroundColor("secondary_bg_color"), "6.1");
    call(() => tg.setBottomBarColor("secondary_bg_color"), "7.10");
  }

  // -- API ----------------------------------------------------------------------

  async function api(path, { method = "GET", body, signal } = {}) {
    let res;
    let data = null;
    try {
      res = await fetch(path, {
        method, signal,
        headers: Object.assign({ Authorization: `tma ${initData}` },
          body ? { "Content-Type": "application/json" } : {}),
        body: body ? JSON.stringify(body) : undefined,
      });
      data = await res.json().catch(() => null);
    } catch (e) {
      if (e.name === "AbortError") throw e;
      throw Object.assign(new Error("network"), { code: "network" });
    }
    if (!res.ok || data == null) {
      const code = (data && data.error) || (res.status >= 500 ? "steam" : "generic");
      throw Object.assign(new Error((data && data.message) || res.statusText), { code });
    }
    return data;
  }

  function errorText(err) {
    const key = `err_${err.code}`;
    return TEXTS.en[key] ? t(key) : t("err_generic");
  }

  // -- state --------------------------------------------------------------------

  const state = {
    screen: "home",
    currency: "USD",
    portfolio: null,
    loadError: null, // first load failed: nothing to show
    stale: null, // a later refresh failed: data on screen may be old
    busy: false,
    back: null,
  };

  function setPortfolio(p) {
    state.portfolio = p;
    state.currency = p.currency;
    state.loadError = null;
    state.stale = null;
  }

  function heldItem(hashName) {
    return ((state.portfolio && state.portfolio.items) || []).find((i) => i.hash_name === hashName) || null;
  }

  async function loadPortfolio() {
    try {
      setPortfolio(await api("/api/portfolio"));
    } catch (e) {
      if (state.portfolio) state.stale = e;
      else state.loadError = e;
    }
    if (state.screen === "home") showHome({ keepScroll: true });
  }

  function back() {
    if (!state.busy && state.back) state.back();
  }

  // -- screen: portfolio -------------------------------------------------------

  function totals(items) {
    let value = 0, pricedCost = 0, cost = 0, unpriced = 0;
    for (const it of items) {
      cost += it.buy_price * it.qty;
      if (it.price == null) { unpriced += 1; continue; }
      value += net(it.price) * it.qty;
      pricedCost += it.buy_price * it.qty;
    }
    return { value, pricedCost, cost, unpriced };
  }

  function showHome({ keepScroll = false } = {}) {
    state.screen = "home";
    state.back = null;
    setBack(false);
    secondary.set(null);
    const p = state.portfolio;

    if (!p && state.loadError) {
      main.set(null);
      mount([tappable(h("p", { class: "message tappable" }, errorText(state.loadError), h("br"), t("retry")), () => {
        state.loadError = null;
        showHome();
        loadPortfolio();
      })]);
      return;
    }
    if (!p) {
      main.set(null);
      mount([
        h("section", { class: "hero" }, h("div", { class: "sk sk-hero" })),
        h("ul", { class: "list" }, [0, 1, 2].map(() => h("li", { class: "row" },
          h("div", { class: "thumb placeholder" }),
          h("div", { class: "row-main" }, h("div", { class: "sk sk-line" }), h("div", { class: "sk sk-line short" })),
        ))),
      ]);
      return;
    }

    main.set(t("addItem"), showSearch);
    if (!p.items.length) {
      mount([h("section", { class: "empty-state" },
        h("div", { class: "empty-icon", "aria-hidden": "true" }, "📦"),
        h("div", { class: "empty-title" }, t("emptyTitle")),
        h("p", { class: "empty-text" }, t("emptyText")),
      )]);
      return;
    }

    const { value, pricedCost, cost, unpriced } = totals(p.items);
    const pnl = value - pricedCost;
    const worth = (it) => (it.price == null ? -1 : net(it.price) * it.qty);
    const items = [...p.items].sort((a, b) => worth(b) - worth(a));

    const foot = [`${t(`kind_${p.price_kind}`)}, ${t("afterFee")}`];
    if (p.updated_at) foot.push(t("updated", { ago: ago(p.updated_at) }));
    if (unpriced) foot.push(t("unpriced", { n: unpriced }));

    mount([
      h("section", { class: "hero" },
        h("div", { class: "hero-value num" }, money(value)),
        pricedCost > 0 && h("div", { class: `hero-pnl num ${trend(pnl / pricedCost)}` },
          `${money(pnl, true)} · ${percent(pnl / pricedCost)}`),
        // Invested matches what value and P&L cover; unpriced items are listed apart.
        h("div", { class: "hero-sub hint num" }, `${t("invested")} ${money(pricedCost)}`,
          cost > pricedCost && ` ${t("unpricedCost", { cost: money(cost - pricedCost) })}`),
      ),
      h("ul", { class: "list" }, items.map(homeRow)),
      h("p", { class: "foot hint" }, foot.join(" · ")),
      state.stale && h("p", { class: "foot down" }, errorText(state.stale)),
    ], { keepScroll });
  }

  function homeRow(it) {
    const priced = it.price != null;
    const value = priced ? net(it.price) * it.qty : null;
    const ratio = priced && it.buy_price > 0 ? net(it.price) / it.buy_price - 1 : null;
    return tappable(h("li", { class: "row" },
      thumb(it.icon),
      h("div", { class: "row-main" },
        h("div", { class: "row-title" }, it.name),
        h("div", { class: "row-sub hint num" }, t("position", { qty: it.qty, price: money(it.buy_price) })),
      ),
      h("div", { class: "row-side num" },
        h("div", { class: "row-value" }, money(value)),
        ratio != null && h("div", { class: `row-pnl ${trend(ratio)}` }, percent(ratio)),
      ),
    ), () => { haptic.tap(); showItem(it, "edit"); });
  }

  // -- screen: search ----------------------------------------------------------

  const search = { query: "", results: null, error: null, loading: false, cache: new Map(), timer: 0, ctrl: null };

  function showSearch() {
    state.screen = "search";
    state.back = () => showHome();
    setBack(true);
    main.set(null);
    secondary.set(null);
    const input = h("input", {
      type: "search", placeholder: t("searchPlaceholder"), enterkeyhint: "search",
      autocomplete: "off", autocapitalize: "off", spellcheck: "false", "aria-label": t("searchHint"),
    });
    input.value = search.query;
    input.addEventListener("input", () => onQuery(input.value));
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
    mount([h("div", { class: "search" }, input), h("div", { id: "results" })]);
    renderResults();
    // Only a fresh search pops the keyboard; coming back keeps the list readable.
    if (!search.query) setTimeout(() => input.focus(), 50);
  }

  function onQuery(value) {
    search.query = value;
    clearTimeout(search.timer);
    if (search.ctrl) search.ctrl.abort();
    search.ctrl = null;
    const q = value.trim();
    if (q.length < 2) {
      Object.assign(search, { results: null, error: null, loading: false });
      renderResults();
      return;
    }
    const key = q.toLowerCase().replace(/\s+/g, " ");
    if (search.cache.has(key)) {
      Object.assign(search, { results: search.cache.get(key), error: null, loading: false });
      renderResults();
      return;
    }
    search.loading = true;
    renderResults();
    search.timer = setTimeout(() => runSearch(q, key), 400);
  }

  async function runSearch(q, key) {
    const ctrl = new AbortController();
    search.ctrl = ctrl;
    let results = null;
    let error = null;
    try {
      results = (await api(`/api/search?q=${encodeURIComponent(q)}`, { signal: ctrl.signal })).results;
      search.cache.set(key, results);
    } catch (e) {
      error = e;
    }
    if (search.ctrl !== ctrl) return; // a newer query took over
    search.ctrl = null;
    Object.assign(search, { results, error, loading: false });
    if (state.screen === "search") renderResults();
  }

  function renderResults() {
    const box = document.getElementById("results");
    if (!box) return;
    let content;
    if (search.loading) {
      content = h("ul", { class: "list" }, [0, 1, 2, 3].map(() => h("li", { class: "row" },
        h("div", { class: "thumb placeholder" }),
        h("div", { class: "row-main" }, h("div", { class: "sk sk-line" })),
      )));
    } else if (search.error) {
      content = tappable(h("p", { class: "message tappable" }, errorText(search.error), h("br"), t("retry")),
        () => onQuery(search.query));
    } else if (!search.results) {
      content = h("p", { class: "message" }, t("searchHint"));
    } else if (!search.results.length) {
      content = h("p", { class: "message" }, t("nothingFound"));
    } else {
      content = h("ul", { class: "list" }, search.results.map((r) => tappable(h("li", { class: "row" },
        thumb(r.icon),
        h("div", { class: "row-main" }, h("div", { class: "row-title" }, r.name)),
        heldItem(r.hash_name) && h("span", { class: "badge" }, t("inPortfolio")),
      ), () => {
        haptic.tap();
        if (document.activeElement) document.activeElement.blur();
        showItem({ hash_name: r.hash_name, name: r.name, icon: r.icon }, "add", showSearch);
      })));
    }
    box.replaceChildren(content);
  }

  // -- screen: item ------------------------------------------------------------

  // mode "edit": change or remove a position; mode "add": record a purchase,
  // averaged into the position if the item is already held.
  function showItem(item, mode, backTo) {
    state.screen = "item";
    state.back = backTo || (() => showHome());
    setBack(true);
    const editing = mode === "edit";
    const held = heldItem(item.hash_name);
    let price = held ? held.price : undefined; // undefined: still loading
    let priceError = null;

    const now = h("div", { class: "item-now hint num" });
    const steamLink = h("a", {
      href: MARKET_URL + encodeURIComponent(item.hash_name), target: "_blank", rel: "noopener",
      onclick: (e) => { if (initData) { e.preventDefault(); tg.openLink(e.currentTarget.href); } },
    }, item.name, h("span", { class: "external", "aria-hidden": "true" }, " ↗"));

    const qty = numberInput("numeric", editing ? String(item.qty) : "1", "next");
    const buy = numberInput("decimal", editing ? plainAmount(item.buy_price) : "", "done");
    qty.addEventListener("keydown", (e) => { if (e.key === "Enter") buy.focus(); });
    buy.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      buy.blur();
      main.tap(); // Android shows a Done key: let it submit
    });
    const qtyField = h("label", { class: "field" }, h("span", {}, t("qty")), qty);
    const buyField = h("label", { class: "field" }, h("span", {}, t("buyPrice")), buy);
    const note = h("p", { class: "note" });
    const summary = h("div", {});

    function renderNow() {
      now.replaceChildren(price === undefined ? h("span", { class: "sk sk-line short" })
        : priceError ? errorText(priceError)
        : price == null ? t("noPrice")
        : t("now", { price: money(price), net: money(net(price)) }));
      if (!editing && price != null) buy.placeholder = plainAmount(price);
    }

    function refresh() {
      const q = parseQty(qty.value);
      const b = parseAmount(buy.value);
      const removing = editing && q === 0;
      qtyField.classList.toggle("invalid", qty.value !== "" && (q == null || (!editing && q === 0)));
      buyField.classList.toggle("invalid", buy.value !== "" && b == null);

      // Note: what adding does to the position, or how to remove.
      let noteText = "";
      if (editing) noteText = t("zeroHint");
      else if (held && q && b != null) {
        const total = held.qty + q;
        const avg = Math.round(((held.qty * held.buy_price + q * b) / total) * 100) / 100;
        noteText = t("becomes", { qty: total, price: money(avg) });
      } else if (held) noteText = t("has", { qty: held.qty, price: money(held.buy_price) });
      note.textContent = noteText;
      note.hidden = !noteText;

      const rows = [];
      if (q && b != null && !removing) {
        const cost = q * b;
        if (editing || q > 1) rows.push(summaryRow(t(editing ? "invested" : "total"), money(cost)));
        if (price != null && cost > 0) {
          const worth = q * net(price);
          const r = worth / cost - 1;
          rows.push(summaryRow(t("worth"), money(worth), [` ${percent(r)}`, trend(r)]));
        }
      }
      summary.replaceChildren(...rows);

      if (removing) {
        main.set(t("remove"), remove, { busy: state.busy, danger: true });
      } else {
        const valid = q != null && q > 0 && b != null;
        const changed = !editing || q !== item.qty || b !== item.buy_price;
        main.set(t(editing ? "save" : "add"), () => save(q, b), { enabled: valid && changed, busy: state.busy });
      }
      if (editing && !removing && !state.busy) {
        secondary.set(t("boughtMore"), () => showItem(item, "add", () => showItem(heldItem(item.hash_name) || item, "edit")));
      } else {
        secondary.set(null);
      }
    }
    qty.addEventListener("input", refresh);
    buy.addEventListener("input", refresh);

    mount([
      h("section", { class: "item-head" },
        thumb(item.icon, 256),
        h("div", { class: "item-name" }, steamLink),
        now,
      ),
      h("div", { class: "form" }, qtyField, buyField),
      note,
      summary,
      secondary.inline,
    ]);
    renderNow();
    refresh();
    // The purchase price is the one number only the user knows: start there.
    if (!editing) setTimeout(() => buy.focus(), 50);

    if (price === undefined) {
      api(`/api/quote?hash_name=${encodeURIComponent(item.hash_name)}`).then((data) => {
        price = data.price;
      }).catch((e) => {
        price = null;
        priceError = e;
      }).finally(() => {
        if (!app.contains(now)) return; // user moved on
        renderNow();
        refresh();
      });
    }

    async function submit(body) {
      state.busy = true;
      refresh();
      try {
        setPortfolio(await api(body.path, { method: "POST", body: body.json }));
        haptic.ok();
        state.busy = false;
        search.query = "";
        search.results = null;
        showHome();
      } catch (e) {
        state.busy = false;
        haptic.fail();
        if (app.contains(now)) refresh();
        alertUser(errorText(e));
      }
    }

    function save(q, b) {
      if (state.busy) return;
      submit({ path: "/api/holdings", json: { hash_name: item.hash_name, qty: q, buy_price: b, mode: editing ? "set" : "add" } });
    }

    async function remove() {
      if (state.busy || !(await confirmUser(t("removeConfirm", { name: item.name })))) return;
      submit({ path: "/api/holdings/delete", json: { hash_name: item.hash_name } });
    }
  }

  function numberInput(mode, value, enterHint) {
    const input = h("input", {
      type: "text", inputmode: mode, enterkeyhint: enterHint, autocomplete: "off", placeholder: "0",
    });
    input.value = value;
    input.addEventListener("focus", () => input.select());
    return input;
  }

  function summaryRow(label, value, extra) {
    return h("div", { class: "summary" },
      h("span", { class: "hint" }, label),
      h("span", { class: "num" }, value, extra && h("span", { class: extra[1] }, extra[0])),
    );
  }

  // -- boot ---------------------------------------------------------------------

  if (!initData) {
    app.replaceChildren(h("p", { class: "message" }, t("notInTelegram")));
    if (tg) tg.ready();
    return;
  }

  const main = bottomButton(nativeUi ? tg.MainButton : null, document.getElementById("fallback-main"));
  // SecondaryButton needs Bot API 7.10; older clients get the same action inline.
  const nativeSecondary = nativeUi && tg.isVersionAtLeast("7.10") ? tg.SecondaryButton : null;
  const inlineSecondary = h("button", { class: "secondary-inline", type: "button", hidden: true });
  // Left in the SDK's default secondary style so it never competes with MainButton.
  const secondary = bottomButton(nativeSecondary, nativeSecondary ? null : inlineSecondary, { primary: false });
  secondary.inline = nativeSecondary ? null : inlineSecondary;
  if (nativeSecondary) call(() => tg.SecondaryButton.setParams({ position: "top" }), "7.10");

  applyTheme();
  applySafeArea();
  tg.onEvent("themeChanged", applyTheme);
  tg.onEvent("safeAreaChanged", applySafeArea);
  tg.onEvent("contentSafeAreaChanged", applySafeArea);
  tg.onEvent("activated", () => { if (state.screen === "home") loadPortfolio(); });
  if (nativeUi) tg.BackButton.onClick(back);
  // Pulling the list down should scroll it, not minimise the app.
  call(() => tg.disableVerticalSwipes(), "7.7");
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") back(); });
  setInterval(() => {
    if (state.screen === "home" && document.visibilityState === "visible") loadPortfolio();
  }, 120000);

  showHome();
  tg.ready();
  tg.expand();
  loadPortfolio();
})();
