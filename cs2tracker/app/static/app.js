"use strict";

(() => {
  const tg = window.Telegram && window.Telegram.WebApp;
  const initData = (tg && tg.initData) || "";
  // Outside Telegram (a plain browser during development) draw our own buttons.
  const nativeUi = !!initData && tg.platform !== "unknown";
  const ICON_BASE = "https://community.fastly.steamstatic.com/economy/image/";
  const MARKET_URL = "https://steamcommunity.com/market/listings/730/";
  // A CS2 seller gets the buyer's price minus 5% Steam + 10% game fee.
  const STEAM_FEE_PERCENT = 15;

  // -- i18n ---------------------------------------------------------------------

  const TEXTS = {
    en: {
      addItem: "Add item", add: "Add", save: "Save", boughtMore: "Buy more", remove: "Remove",
      overview: "Overview", portfolioTab: "Portfolio", topValue: "Most valuable",
      changeDay: "24h", changeWeek: "7d", allItems: "View all", historyGrowing: "History starts today",
      breakEven: "Break-even / item", sellListings: "For sale: {n}", buyOrders: "Buy orders: {n}", spread: "Spread: {pct}",
      historyUnavailable: "History unavailable. Tap to retry", compositionChanged: "Holdings changed",
      chartLabel: "Portfolio value by day", sections: "Sections", period: "Period", profitLabel: "Profit",
      range_7d: "Week", range_30d: "Month", range_all: "All",
      scope_7d: "7 days", scope_30d: "30 days", scope_all: "all time", marketOnly: "{period}, price moves only",
      notifications: "Notifications", notifyMe: "Notify me…", alertsHere: "Alerts",
      digestTitle: "Summary in the bot", digest_off: "Off", digest_daily: "Daily", digest_weekly: "Weekly",
      digestHint_daily: "Portfolio value, the day's change, and the best and worst item.",
      digestHint_weekly: "Every Monday: the week's change, and the best and worst item.",
      digestAt: "Send at", portfolioAlerts: "Portfolio", itemAlerts: "Items", alertsCount: "{n} of {max} alerts",
      noAlerts: "No alerts yet. Open an item and tap “Notify me…”, or add one for the whole portfolio.",
      newPortfolioAlert: "Add portfolio alert", alertItemTitle: "Notify me when", alertType: "Alert type", direction: "Direction",
      cond_price_above: "Price ≥ {v}", cond_price_below: "Price ≤ {v}",
      cond_profit_above: "Profit ≥ {v}", cond_profit_below: "Profit ≤ {v}",
      cond_value_above: "Value ≥ {v} · price moves since {date}", cond_value_below: "Value ≤ {v} · price moves since {date}",
      nowValue: "now {v}", firedAgo: "sent {ago}",
      metric_price: "Price", metric_profit: "Profit", metric_value_pct: "Percent", metric_value_amount: "Amount",
      dir_above: "Above", dir_below: "Below", dir_up: "Up", dir_down: "Down",
      fieldPrice: "Price each", fieldPercent: "Change, %", fieldAmount: "Change", fieldProfit: "Profit, %",
      nowPrice: "Now {v} on Steam", nowProfit: "Now {v} on the price paid",
      fromValue: "Counted from the value now, {v}. Buying or selling items doesn't count.",
      fromBaseline: "Price moves since {date}; buying or selling items doesn't count.",
      metNow: "Already true now: the message comes after the next price update.",
      alertOnce: "The bot writes once, and again only after the value moves back across the threshold.",
      deleteAlert: "Delete alert", deleteAlertConfirm: "Delete this alert?",
      noWriteAccess: "Allow the bot to message you (or press Start in the bot chat), or alerts won't arrive.", allow: "Allow",
      watch: "Watch price", unwatch: "Stop watching", watchingTitle: "Watching",
      offerTitle: "Daily summary in the bot?", offerYes: "Turn on", offerNo: "No thanks",
      offerText: "Every day at 10:00: portfolio value, the day's change, and the best and worst item. You can change it under 🔔.",
      emptyTitle: "No items yet",
      emptyText: "Add the cases, capsules or skins you bought — or paste your Steam profile link in search to import the inventory.",
      invested: "Invested", worth: "You'd get now", unpricedCost: "+ {cost} without a price", total: "Total",
      searchPlaceholder: "Item name or Steam profile link",
      searchHint: "Search the Steam market, or paste a link to your Steam profile or trade offer to import your inventory.",
      notSet: "not set", noBuyPrice: "{qty} pcs · price paid not set", withoutBuy: "{n} without a price paid",
      hasNoPrice: "You have {qty} without a price paid.", becomesNoPrice: "Will become {qty}, price paid not set.",
      importN: "Import {n}", selected: "{n} of {total} selected", selectAll: "Select all", selectNone: "Clear",
      mode_none: "No price", mode_market: "Today's", mode_manual: "Manual", importPriceTitle: "Price paid",
      modeHint_none: "Only the total value is shown. You can add prices later in each item.",
      modeHint_market: "Price paid = what selling today would bring, so profit starts from zero today.",
      modeHint_manual: "Enter the price paid for each item; leave empty if unknown.",
      room: "room for {n}", pnlScope: "on\u00a0{n}\u00a0of\u00a0{total}",
      noSellable: "No marketable CS2 items in this inventory.",
      storageNote: "Items inside storage units aren't visible to Steam's public inventory.",
      err_not_profile: "That doesn't look like a Steam profile link.",
      err_profile_not_found: "No Steam profile at this link.",
      err_inventory_private: "This inventory is private. In Steam: Profile → Edit Profile → Privacy Settings → Inventory: Public.",
      err_steam_rate: "Steam limits inventory lookups. Try again in a couple of minutes.",
      err_inventory_busy: "Many imports right now. Try again in a minute.",
      err_import_expired: "The inventory list was refreshed. Check it and tap Import again.",
      nothingFound: "Nothing found",
      recentProfiles: "Recent profiles", profileItems: "{n} items", forgetProfile: "Remove from recent",
      inPortfolio: "In portfolio",
      qty: "Quantity", buyPrice: "Price paid, each",
      now: "On Steam {price} · you'd get {net}", noPrice: "No price on the market right now",
      position: "{qty} at {price}",
      has: "You have {qty} at {price} each.",
      becomes: "Will become {qty} at {price} each.",
      becomesKeep: "Will become {qty} at {price} each: the new ones have no price paid, so the average stays.",
      removeFull: "Remove from portfolio",
      pricing: "Fetching prices: {n} of {total}",
      select: "Select",
      done: "Done",
      deleteN: "Remove {n}",
      deleteConfirm: "Remove the selected items ({n}) from the portfolio?",
      stats: "Statistics",
      st_users: "Users",
      st_active7: "Active, 7 days",
      st_new7: "New, 7 days",
      st_withPortfolio: "With a portfolio",
      st_more: "Active today: {d1} · 30 days: {d30} · only pressed /start: {bot}",
      st_chart: "Active users per day, 14 days",
      st_dayTip: "{day}: {active} active, {new} new",
      st_actions: "Actions, 7 days",
      act_open: "App opens",
      act_search: "Searches",
      act_add: "Items added",
      act_edit: "Edits",
      act_remove: "Removals",
      act_inventory: "Inventory lookups",
      act_import: "Items imported",
      act_bot: "Bot messages", act_watch: "Items watched", act_alert: "Alerts created", act_digest: "Digests turned on",
      st_top: "Most held items",
      st_holders: "{n} users · {qty} pcs",
      st_recent: "Recent users",
      st_userSub: "{items} items · seen {ago}",
      st_prices: "Prices",
      st_tracked: "Items tracked",
      st_pending: "Not priced yet",
      st_lastCheck: "Last Steam check",
      st_oldest: "Oldest price",
      st_none: "No data yet",
      sortBy: "Sort by", sortAsc: "Ascending", sortDesc: "Descending",
      sort_value: "Value", sort_profitPct: "Profit, %", sort_profit: "Profit, {cur}", sort_qty: "Quantity",
      sort_price: "Price each", sort_name: "Name", sort_added: "Recently added", sort_change24h: "Change, 24h",
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
      err_alerts_full: "You already have 20 alerts. Delete one to add another.",
      err_watch_full: "Your watchlist is full (50 items).",
      err_no_buy_price: "Enter the price you paid to get profit alerts.",
      sold: "Sold…", sellTitle: "Record a sale", sellQty: "Quantity sold", sellPrice: "Received, each",
      sellHint: "What one item brought you, after fees. Filled in with today's price after Steam's fee.",
      sellLeft: "{qty} will stay in the portfolio.", sellCloses: "This closes the position.",
      recordSale: "Record sale", received: "Received", realizedLabel: "Realized", soldFor: "Sold for {v}",
      realizedProfit: "Realized profit", sales: "Sales", saleSub: "{qty} × {price} · {date}",
      noSales: "No sales yet. Record one from an item's screen.",
      undoConfirm: "Undo this sale? {qty} × {name} go back to the portfolio at the price paid.",
      undoSale: "Undo",
      syncNew: "New in your Steam inventory: {n}", syncReview: "Review", syncGone: "Gone from your Steam inventory. Sold?",
      syncDismiss: "Dismiss", syncTitle: "Inventory check", sync_on: "On", sync_off: "Off",
      syncHint: "Once a day the bot looks at the Steam inventory you imported from and tells you about new items and ones that are gone. Nothing is added or removed by itself.",
      syncChecked: "Checked {ago}.", syncPrivate: "This inventory is private, so it can't be checked.",
      syncNotFound: "This Steam profile wasn't found.",
      syncNone: "Import your inventory once (paste your Steam profile link in search) to have it checked daily.",
      err_sell_qty: "You don't have that many.", err_no_sync: "Import your inventory first.",
      act_sell: "Sales recorded",
      folders: "Folders", allFolders: "All", noFolder: "No folder", folderLabel: "Folder", newFolder: "New folder…",
      newFolderName: "New folder", addFolder: "Add folder", deleteFolder: "Delete folder",
      deleteFolderConfirm: "Delete the folder “{name}”? Its items stay in the portfolio.",
      foldersHint: "Group items, for example by Steam account or plan. An item is in one folder at a time; rename a folder by editing its name.",
      folderCount: "{n} items", moveTo: "Move to…", emptyFolder: "No items in this folder yet. Open an item to move it here, or use Select in the Portfolio tab.",
      exportCsv: "Export to CSV", exported: "The bot sent you the file.",
      share: "Share", shareTitle: "Share your portfolio", showAmounts: "With amounts", hideAmounts: "Percent only",
      shareHint: "Amounts stay hidden unless you choose to show them.", shareChat: "Send to a chat", shareStory: "Share to story",
      shareSent: "The bot sent you the picture: forward it to anyone.", cardTitle: "CS2 portfolio", cardItems: "{n} items",
      cardFooter: "Track yours in Telegram", cardProfit: "Profit",
      broadcast: "Message to all users", broadcastPlaceholder: "Text of the message",
      broadcastAudience: "Goes to {n} users who allowed messages, with an “Open portfolio” button.",
      broadcastSend: "Send to {n}", broadcastConfirm: "Send this message to {n} users?",
      broadcastProgress: "Sending: {done} of {total}, {failed} failed", broadcastCancel: "Stop",
      broadcastDone: "Last one: {sent} delivered, {failed} failed · {ago}", broadcastCancelled: "Last one was stopped after {done} of {total}",
      err_folder_exists: "There is already a folder with this name.", err_folders_full: "At most 10 folders.",
      err_send_failed: "The bot couldn't send it. Press Start in the bot chat and try again.",
      err_no_write_access: "Allow the bot to message you (or press Start in the bot chat), then try again.",
      askWriteAccess: "The bot needs your permission to send you the file. Allow it?", exporting: "Sending…", err_unavailable: "Not available right now.",
      err_broadcast_running: "A broadcast is still going out.",
      act_export: "Exports", act_share: "Shares", act_inline: "Inline searches (users)", act_inline_sent: "Inline cards sent",
      inlineHint: "Tip: in any chat, type @{bot} and an item's name, and the bot sends its price card.",
    },
    ru: {
      addItem: "Добавить предмет", add: "Добавить", save: "Сохранить", boughtMore: "Докупить", remove: "Убрать",
      overview: "Главная", portfolioTab: "Портфель", topValue: "Самые ценные",
      changeDay: "24 ч", changeWeek: "7 д", allItems: "Все", historyGrowing: "История начинается сегодня",
      breakEven: "Безубыточность / шт.", sellListings: "В продаже: {n}", buyOrders: "Заявок на покупку: {n}", spread: "Спред: {pct}",
      historyUnavailable: "История недоступна. Нажмите, чтобы повторить", compositionChanged: "Состав портфеля изменился",
      chartLabel: "Стоимость портфеля по дням", sections: "Разделы", period: "Период", profitLabel: "Прибыль",
      range_7d: "Неделя", range_30d: "Месяц", range_all: "Всё",
      scope_7d: "7 дней", scope_30d: "30 дней", scope_all: "всё время", marketOnly: "{period}, только изменение цен",
      notifications: "Уведомления", notifyMe: "Уведомить меня…", alertsHere: "Уведомления",
      digestTitle: "Сводка в боте", digest_off: "Выкл.", digest_daily: "Каждый день", digest_weekly: "Раз в неделю",
      digestHint_daily: "Стоимость портфеля, изменение за день, лучший и худший предмет.",
      digestHint_weekly: "Каждый понедельник: изменение за неделю, лучший и худший предмет.",
      digestAt: "Время", portfolioAlerts: "Портфель", itemAlerts: "Предметы", alertsCount: "{n} из {max} уведомлений",
      noAlerts: "Уведомлений пока нет. Откройте предмет и нажмите «Уведомить меня…» или добавьте уведомление для всего портфеля.",
      newPortfolioAlert: "Добавить уведомление", alertItemTitle: "Уведомить, когда", alertType: "Тип уведомления", direction: "Направление",
      cond_price_above: "Цена ≥ {v}", cond_price_below: "Цена ≤ {v}",
      cond_profit_above: "Прибыль ≥ {v}", cond_profit_below: "Прибыль ≤ {v}",
      cond_value_above: "Стоимость ≥ {v} · движение цен с {date}", cond_value_below: "Стоимость ≤ {v} · движение цен с {date}",
      nowValue: "сейчас {v}", firedAgo: "отправлено {ago}",
      metric_price: "Цена", metric_profit: "Прибыль", metric_value_pct: "В процентах", metric_value_amount: "В деньгах",
      dir_above: "Выше", dir_below: "Ниже", dir_up: "Рост", dir_down: "Падение",
      fieldPrice: "Цена за шт.", fieldPercent: "Изменение, %", fieldAmount: "Изменение", fieldProfit: "Прибыль, %",
      nowPrice: "Сейчас {v} в Steam", nowProfit: "Сейчас {v} к цене покупки",
      fromValue: "Отсчёт от текущей стоимости, {v}. Покупки и продажи не учитываются.",
      fromBaseline: "Движение цен с {date}; покупки и продажи не учитываются.",
      metNow: "Условие уже выполнено: сообщение придёт после следующего обновления цен.",
      alertOnce: "Бот напишет один раз, а повторно — только когда значение вернётся за порог.",
      deleteAlert: "Удалить уведомление", deleteAlertConfirm: "Удалить это уведомление?",
      noWriteAccess: "Разрешите боту писать вам (или нажмите «Старт» в чате с ботом), иначе уведомления не придут.", allow: "Разрешить",
      watch: "Следить за ценой", unwatch: "Не следить", watchingTitle: "Слежу за ценой",
      offerTitle: "Присылать сводку в бот каждый день?", offerYes: "Включить", offerNo: "Не надо",
      offerText: "Каждый день в 10:00: стоимость портфеля, изменение за день, лучший и худший предмет. Изменить можно под 🔔.",
      emptyTitle: "Пока пусто",
      emptyText: "Добавьте кейсы, капсулы или скины, которые купили, — или вставьте в поиск ссылку на профиль Steam, чтобы импортировать инвентарь.",
      invested: "Вложено", worth: "Получите сейчас", unpricedCost: "+ {cost} без цены", total: "Итого",
      searchPlaceholder: "Название или ссылка на профиль Steam",
      searchHint: "Поиск по торговой площадке Steam. Или вставьте ссылку на свой профиль Steam или трейд-ссылку — импортируем инвентарь.",
      notSet: "не указана", noBuyPrice: "{qty} шт. · цена покупки не указана", withoutBuy: "без цены покупки: {n}",
      hasNoPrice: "У вас {qty} шт. без цены покупки.", becomesNoPrice: "Станет {qty} шт., цена покупки не указана.",
      importN: "Импортировать {n}", selected: "Выбрано {n} из {total}", selectAll: "Выбрать все", selectNone: "Снять все",
      mode_none: "Без цены", mode_market: "Текущая", mode_manual: "Вручную", importPriceTitle: "Цена покупки",
      modeHint_none: "Покажем только общую стоимость. Цену покупки можно указать позже в карточке предмета.",
      modeHint_market: "Цена покупки = сколько вы получили бы при продаже сегодня; прибыль считается с нуля.",
      modeHint_manual: "Укажите цену покупки за штуку; пустые останутся без цены.",
      room: "можно {n}", pnlScope: "по\u00a0{n}\u00a0из\u00a0{total}",
      noSellable: "В этом инвентаре нет предметов CS2, которые можно продать.",
      storageNote: "Предметы внутри хранилищ (Storage Unit) Steam не показывает.",
      err_not_profile: "Это не похоже на ссылку на профиль Steam.",
      err_profile_not_found: "Профиль Steam по этой ссылке не найден.",
      err_inventory_private: "Инвентарь скрыт. В Steam: Профиль → Редактировать профиль → Приватность → Инвентарь: Открытый.",
      err_steam_rate: "Steam ограничивает запросы инвентаря. Попробуйте через пару минут.",
      err_inventory_busy: "Сейчас много импортов. Попробуйте через минуту.",
      err_import_expired: "Список инвентаря обновлён. Проверьте его и нажмите «Импортировать» ещё раз.",
      nothingFound: "Ничего не найдено",
      recentProfiles: "Недавние профили", profileItems: "предметов: {n}", forgetProfile: "Убрать из недавних",
      inPortfolio: "В портфеле",
      qty: "Количество", buyPrice: "Цена покупки за шт.",
      now: "На Steam {price} · вы получите {net}", noPrice: "Сейчас на рынке нет цены",
      position: "{qty} шт. · по {price}",
      has: "У вас {qty} шт. по {price}.",
      becomes: "Станет {qty} шт. по {price}.",
      becomesKeep: "Станет {qty} шт. по {price}: у новых нет цены покупки, средняя не меняется.",
      removeFull: "Убрать из портфеля",
      pricing: "Получаем цены: {n} из {total}",
      select: "Выбрать",
      done: "Готово",
      deleteN: "Удалить {n}",
      deleteConfirm: "Убрать из портфеля выбранные предметы ({n})?",
      stats: "Статистика",
      st_users: "Пользователи",
      st_active7: "Активны за 7 дней",
      st_new7: "Новые за 7 дней",
      st_withPortfolio: "С портфелем",
      st_more: "Сегодня: {d1} · за 30 дней: {d30} · только нажали /start: {bot}",
      st_chart: "Активные пользователи по дням, 14 дней",
      st_dayTip: "{day}: активных {active}, новых {new}",
      st_actions: "Действия за 7 дней",
      act_open: "Открытия приложения",
      act_search: "Поиски",
      act_add: "Добавлено предметов",
      act_edit: "Изменения",
      act_remove: "Удаления",
      act_inventory: "Просмотры инвентаря",
      act_import: "Импортировано предметов",
      act_bot: "Сообщения боту", act_watch: "Добавлено в наблюдение", act_alert: "Создано уведомлений", act_digest: "Включено сводок",
      st_top: "Популярные предметы",
      st_holders: "у {n} польз. · {qty} шт.",
      st_recent: "Последние пользователи",
      st_userSub: "предметов: {items} · был(а) {ago}",
      st_prices: "Цены",
      st_tracked: "Отслеживается предметов",
      st_pending: "Ещё без цены",
      st_lastCheck: "Последняя проверка Steam",
      st_oldest: "Самая старая цена",
      st_none: "Пока нет данных",
      sortBy: "Сортировка", sortAsc: "По возрастанию", sortDesc: "По убыванию",
      sort_value: "Стоимость", sort_profitPct: "Прибыль, %", sort_profit: "Прибыль, {cur}", sort_qty: "Количество",
      sort_price: "Цена за шт.", sort_name: "Название", sort_added: "Недавно добавленные", sort_change24h: "Изменение, 24 ч",
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
      err_alerts_full: "У вас уже 20 уведомлений. Удалите одно, чтобы добавить новое.",
      err_watch_full: "Список наблюдения заполнен (50 предметов).",
      err_no_buy_price: "Укажите цену покупки, чтобы получать уведомления о прибыли.",
      sold: "Продано…", sellTitle: "Отметить продажу", sellQty: "Продано, шт.", sellPrice: "Получено за шт.",
      sellHint: "Сколько принёс один предмет после комиссий. Подставлена сегодняшняя цена за вычетом комиссии Steam.",
      sellLeft: "В портфеле останется {qty} шт.", sellCloses: "Позиция будет закрыта.",
      recordSale: "Отметить продажу", received: "Получено", realizedLabel: "Реализовано", soldFor: "Продано на {v}",
      realizedProfit: "Реализованная прибыль", sales: "Продажи", saleSub: "{qty} шт. × {price} · {date}",
      noSales: "Продаж пока нет. Отметить продажу можно в карточке предмета.",
      undoConfirm: "Отменить продажу? {name}, {qty} шт., вернутся в портфель по цене покупки.",
      undoSale: "Отменить",
      syncNew: "Новое в инвентаре Steam: {n}", syncReview: "Посмотреть", syncGone: "Пропало из инвентаря Steam. Продали?",
      syncDismiss: "Скрыть", syncTitle: "Проверка инвентаря", sync_on: "Вкл.", sync_off: "Выкл.",
      syncHint: "Раз в день бот смотрит инвентарь Steam, из которого вы импортировали предметы, и сообщает о новых и пропавших. Сам ничего не добавляет и не удаляет.",
      syncChecked: "Проверено {ago}.", syncPrivate: "Инвентарь скрыт, проверить его нельзя.",
      syncNotFound: "Профиль Steam не найден.",
      syncNone: "Импортируйте инвентарь один раз (вставьте ссылку на профиль Steam в поиск), и он будет проверяться каждый день.",
      err_sell_qty: "У вас нет столько.", err_no_sync: "Сначала импортируйте инвентарь.",
      act_sell: "Отмечено продаж",
      folders: "Папки", allFolders: "Все", noFolder: "Без папки", folderLabel: "Папка", newFolder: "Новая папка…",
      newFolderName: "Новая папка", addFolder: "Добавить папку", deleteFolder: "Удалить папку",
      deleteFolderConfirm: "Удалить папку «{name}»? Предметы останутся в портфеле.",
      foldersHint: "Группируйте предметы, например по аккаунтам Steam или планам. Предмет лежит в одной папке; чтобы переименовать папку, измените её название.",
      folderCount: "предметов: {n}", moveTo: "Переместить…", emptyFolder: "В этой папке пока пусто. Откройте предмет, чтобы переместить его сюда, или нажмите «Выбрать» во вкладке «Портфель».",
      exportCsv: "Экспорт в CSV", exported: "Бот прислал вам файл.",
      share: "Поделиться", shareTitle: "Поделиться портфелем", showAmounts: "С суммами", hideAmounts: "Только проценты",
      shareHint: "Суммы скрыты, пока вы сами не решите их показать.", shareChat: "Отправить в чат", shareStory: "В историю",
      shareSent: "Бот прислал вам картинку — перешлите её кому угодно.", cardTitle: "Портфель CS2", cardItems: "предметов: {n}",
      cardFooter: "Следите за своим в Telegram", cardProfit: "Прибыль",
      broadcast: "Сообщение всем пользователям", broadcastPlaceholder: "Текст сообщения",
      broadcastAudience: "Получат {n} пользователей, разрешивших сообщения, с кнопкой «Открыть портфель».",
      broadcastSend: "Отправить {n}", broadcastConfirm: "Отправить это сообщение {n} пользователям?",
      broadcastProgress: "Отправка: {done} из {total}, ошибок: {failed}", broadcastCancel: "Остановить",
      broadcastDone: "Последнее: доставлено {sent}, ошибок {failed} · {ago}", broadcastCancelled: "Последнее остановлено после {done} из {total}",
      err_folder_exists: "Папка с таким названием уже есть.", err_folders_full: "Не больше 10 папок.",
      err_send_failed: "Бот не смог отправить. Нажмите «Старт» в чате с ботом и попробуйте снова.",
      err_no_write_access: "Разрешите боту писать вам (или нажмите «Старт» в чате с ботом) и попробуйте снова.",
      askWriteAccess: "Боту нужно разрешение, чтобы прислать вам файл. Разрешить?", exporting: "Отправляем…", err_unavailable: "Сейчас недоступно.",
      err_broadcast_running: "Предыдущая рассылка ещё идёт.",
      act_export: "Экспорты", act_share: "Поделились", act_inline: "Инлайн-поиск (польз.)", act_inline_sent: "Отправлено карточек",
      inlineHint: "Совет: в любом чате напишите @{bot} и название предмета — бот отправит карточку с ценой.",
    },
    uk: {
      addItem: "Додати предмет", add: "Додати", save: "Зберегти", boughtMore: "Докупити", remove: "Прибрати",
      overview: "Головна", portfolioTab: "Портфель", topValue: "Найдорожчі",
      changeDay: "24 год", changeWeek: "7 д", allItems: "Усі", historyGrowing: "Історія починається сьогодні",
      breakEven: "Беззбитковість / шт.", sellListings: "У продажу: {n}", buyOrders: "Заявок на купівлю: {n}", spread: "Спред: {pct}",
      historyUnavailable: "Історія недоступна. Натисніть, щоб повторити", compositionChanged: "Склад портфеля змінився",
      chartLabel: "Вартість портфеля за днями", sections: "Розділи", period: "Період", profitLabel: "Прибуток",
      range_7d: "Тиждень", range_30d: "Місяць", range_all: "Усе",
      scope_7d: "7 днів", scope_30d: "30 днів", scope_all: "весь час", marketOnly: "{period}, лише зміна цін",
      notifications: "Сповіщення", notifyMe: "Сповістити мене…", alertsHere: "Сповіщення",
      digestTitle: "Зведення в боті", digest_off: "Вимк.", digest_daily: "Щодня", digest_weekly: "Щотижня",
      digestHint_daily: "Вартість портфеля, зміна за день, найкращий і найгірший предмет.",
      digestHint_weekly: "Щопонеділка: зміна за тиждень, найкращий і найгірший предмет.",
      digestAt: "Час", portfolioAlerts: "Портфель", itemAlerts: "Предмети", alertsCount: "{n} з {max} сповіщень",
      noAlerts: "Сповіщень поки немає. Відкрийте предмет і натисніть «Сповістити мене…» або додайте сповіщення для всього портфеля.",
      newPortfolioAlert: "Додати сповіщення", alertItemTitle: "Сповістити, коли", alertType: "Тип сповіщення", direction: "Напрямок",
      cond_price_above: "Ціна ≥ {v}", cond_price_below: "Ціна ≤ {v}",
      cond_profit_above: "Прибуток ≥ {v}", cond_profit_below: "Прибуток ≤ {v}",
      cond_value_above: "Вартість ≥ {v} · рух цін з {date}", cond_value_below: "Вартість ≤ {v} · рух цін з {date}",
      nowValue: "зараз {v}", firedAgo: "надіслано {ago}",
      metric_price: "Ціна", metric_profit: "Прибуток", metric_value_pct: "У відсотках", metric_value_amount: "У грошах",
      dir_above: "Вище", dir_below: "Нижче", dir_up: "Зростання", dir_down: "Падіння",
      fieldPrice: "Ціна за шт.", fieldPercent: "Зміна, %", fieldAmount: "Зміна", fieldProfit: "Прибуток, %",
      nowPrice: "Зараз {v} у Steam", nowProfit: "Зараз {v} до ціни купівлі",
      fromValue: "Відлік від поточної вартості, {v}. Купівлі та продажі не враховуються.",
      fromBaseline: "Рух цін з {date}; купівлі та продажі не враховуються.",
      metNow: "Умова вже виконується: повідомлення прийде після наступного оновлення цін.",
      alertOnce: "Бот напише один раз, а повторно — лише коли значення повернеться за поріг.",
      deleteAlert: "Видалити сповіщення", deleteAlertConfirm: "Видалити це сповіщення?",
      noWriteAccess: "Дозвольте боту писати вам (або натисніть «Старт» у чаті з ботом), інакше сповіщення не прийдуть.", allow: "Дозволити",
      watch: "Стежити за ціною", unwatch: "Не стежити", watchingTitle: "Стежу за ціною",
      offerTitle: "Надсилати зведення в бот щодня?", offerYes: "Увімкнути", offerNo: "Не треба",
      offerText: "Щодня о 10:00: вартість портфеля, зміна за день, найкращий і найгірший предмет. Змінити можна під 🔔.",
      emptyTitle: "Поки порожньо",
      emptyText: "Додайте кейси, капсули чи скіни, які купили, — або вставте в пошук посилання на профіль Steam, щоб імпортувати інвентар.",
      invested: "Вкладено", worth: "Отримаєте зараз", unpricedCost: "+ {cost} без ціни", total: "Разом",
      searchPlaceholder: "Назва або посилання на профіль Steam",
      searchHint: "Пошук на торговому майданчику Steam. Або вставте посилання на свій профіль Steam чи трейд-посилання — імпортуємо інвентар.",
      notSet: "не вказана", noBuyPrice: "{qty} шт. · ціна купівлі не вказана", withoutBuy: "без ціни купівлі: {n}",
      hasNoPrice: "У вас {qty} шт. без ціни купівлі.", becomesNoPrice: "Стане {qty} шт., ціна купівлі не вказана.",
      importN: "Імпортувати {n}", selected: "Вибрано {n} з {total}", selectAll: "Вибрати всі", selectNone: "Зняти всі",
      mode_none: "Без ціни", mode_market: "Поточна", mode_manual: "Вручну", importPriceTitle: "Ціна купівлі",
      modeHint_none: "Покажемо лише загальну вартість. Ціну купівлі можна вказати пізніше в картці предмета.",
      modeHint_market: "Ціна купівлі = скільки ви отримали б при продажу сьогодні; прибуток рахується з нуля.",
      modeHint_manual: "Вкажіть ціну купівлі за штуку; порожні залишаться без ціни.",
      room: "можна {n}", pnlScope: "за\u00a0{n}\u00a0з\u00a0{total}",
      noSellable: "У цьому інвентарі немає предметів CS2, які можна продати.",
      storageNote: "Предмети всередині сховищ (Storage Unit) Steam не показує.",
      err_not_profile: "Це не схоже на посилання на профіль Steam.",
      err_profile_not_found: "Профіль Steam за цим посиланням не знайдено.",
      err_inventory_private: "Інвентар приховано. У Steam: Профіль → Редагувати профіль → Приватність → Інвентар: Відкритий.",
      err_steam_rate: "Steam обмежує запити інвентарю. Спробуйте за кілька хвилин.",
      err_inventory_busy: "Зараз багато імпортів. Спробуйте за хвилину.",
      err_import_expired: "Список інвентарю оновлено. Перевірте його й натисніть «Імпортувати» ще раз.",
      nothingFound: "Нічого не знайдено",
      recentProfiles: "Нещодавні профілі", profileItems: "предметів: {n}", forgetProfile: "Прибрати з нещодавніх",
      inPortfolio: "У портфелі",
      qty: "Кількість", buyPrice: "Ціна купівлі за шт.",
      now: "У Steam {price} · ви отримаєте {net}", noPrice: "Зараз на ринку немає ціни",
      position: "{qty} шт. · по {price}",
      has: "У вас {qty} шт. по {price}.",
      becomes: "Стане {qty} шт. по {price}.",
      becomesKeep: "Стане {qty} шт. по {price}: у нових немає ціни купівлі, середня не змінюється.",
      removeFull: "Прибрати з портфеля",
      pricing: "Отримуємо ціни: {n} з {total}",
      select: "Вибрати",
      done: "Готово",
      deleteN: "Видалити {n}",
      deleteConfirm: "Прибрати з портфеля вибрані предмети ({n})?",
      stats: "Статистика",
      st_users: "Користувачі",
      st_active7: "Активні за 7 днів",
      st_new7: "Нові за 7 днів",
      st_withPortfolio: "З портфелем",
      st_more: "Сьогодні: {d1} · за 30 днів: {d30} · лише натиснули /start: {bot}",
      st_chart: "Активні користувачі по днях, 14 днів",
      st_dayTip: "{day}: активних {active}, нових {new}",
      st_actions: "Дії за 7 днів",
      act_open: "Відкриття застосунку",
      act_search: "Пошуки",
      act_add: "Додано предметів",
      act_edit: "Зміни",
      act_remove: "Видалення",
      act_inventory: "Перегляди інвентарю",
      act_import: "Імпортовано предметів",
      act_bot: "Повідомлення боту", act_watch: "Додано до спостереження", act_alert: "Створено сповіщень", act_digest: "Увімкнено зведень",
      st_top: "Популярні предмети",
      st_holders: "у {n} корист. · {qty} шт.",
      st_recent: "Останні користувачі",
      st_userSub: "предметів: {items} · був(ла) {ago}",
      st_prices: "Ціни",
      st_tracked: "Відстежується предметів",
      st_pending: "Ще без ціни",
      st_lastCheck: "Остання перевірка Steam",
      st_oldest: "Найстаріша ціна",
      st_none: "Поки немає даних",
      sortBy: "Сортування", sortAsc: "За зростанням", sortDesc: "За спаданням",
      sort_value: "Вартість", sort_profitPct: "Прибуток, %", sort_profit: "Прибуток, {cur}", sort_qty: "Кількість",
      sort_price: "Ціна за шт.", sort_name: "Назва", sort_added: "Нещодавно додані", sort_change24h: "Зміна, 24 год",
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
      err_alerts_full: "У вас уже 20 сповіщень. Видаліть одне, щоб додати нове.",
      err_watch_full: "Список спостереження заповнений (50 предметів).",
      err_no_buy_price: "Вкажіть ціну купівлі, щоб отримувати сповіщення про прибуток.",
      sold: "Продано…", sellTitle: "Позначити продаж", sellQty: "Продано, шт.", sellPrice: "Отримано за шт.",
      sellHint: "Скільки приніс один предмет після комісій. Підставлено сьогоднішню ціну за вирахуванням комісії Steam.",
      sellLeft: "У портфелі залишиться {qty} шт.", sellCloses: "Позицію буде закрито.",
      recordSale: "Позначити продаж", received: "Отримано", realizedLabel: "Реалізовано", soldFor: "Продано на {v}",
      realizedProfit: "Реалізований прибуток", sales: "Продажі", saleSub: "{qty} шт. × {price} · {date}",
      noSales: "Продажів поки немає. Позначити продаж можна в картці предмета.",
      undoConfirm: "Скасувати продаж? {name}, {qty} шт., повернуться до портфеля за ціною купівлі.",
      undoSale: "Скасувати",
      syncNew: "Нове в інвентарі Steam: {n}", syncReview: "Переглянути", syncGone: "Зникло з інвентарю Steam. Продали?",
      syncDismiss: "Приховати", syncTitle: "Перевірка інвентарю", sync_on: "Увімк.", sync_off: "Вимк.",
      syncHint: "Раз на день бот переглядає інвентар Steam, з якого ви імпортували предмети, і повідомляє про нові та зниклі. Сам нічого не додає і не видаляє.",
      syncChecked: "Перевірено {ago}.", syncPrivate: "Інвентар приховано, перевірити його неможливо.",
      syncNotFound: "Профіль Steam не знайдено.",
      syncNone: "Імпортуйте інвентар один раз (вставте посилання на профіль Steam у пошук), і він перевірятиметься щодня.",
      err_sell_qty: "У вас немає стільки.", err_no_sync: "Спочатку імпортуйте інвентар.",
      act_sell: "Позначено продажів",
      folders: "Папки", allFolders: "Усі", noFolder: "Без папки", folderLabel: "Папка", newFolder: "Нова папка…",
      newFolderName: "Нова папка", addFolder: "Додати папку", deleteFolder: "Видалити папку",
      deleteFolderConfirm: "Видалити папку «{name}»? Предмети залишаться в портфелі.",
      foldersHint: "Групуйте предмети, наприклад за акаунтами Steam або планами. Предмет лежить в одній папці; щоб перейменувати папку, змініть її назву.",
      folderCount: "предметів: {n}", moveTo: "Перемістити…", emptyFolder: "У цій папці поки порожньо. Відкрийте предмет, щоб перемістити його сюди, або натисніть «Вибрати» у вкладці «Портфель».",
      exportCsv: "Експорт у CSV", exported: "Бот надіслав вам файл.",
      share: "Поділитися", shareTitle: "Поділитися портфелем", showAmounts: "Із сумами", hideAmounts: "Лише відсотки",
      shareHint: "Суми приховані, доки ви самі не вирішите їх показати.", shareChat: "Надіслати в чат", shareStory: "В історію",
      shareSent: "Бот надіслав вам картинку — перешліть її будь-кому.", cardTitle: "Портфель CS2", cardItems: "предметів: {n}",
      cardFooter: "Стежте за своїм у Telegram", cardProfit: "Прибуток",
      broadcast: "Повідомлення всім користувачам", broadcastPlaceholder: "Текст повідомлення",
      broadcastAudience: "Отримають {n} користувачів, які дозволили повідомлення, з кнопкою «Відкрити портфель».",
      broadcastSend: "Надіслати {n}", broadcastConfirm: "Надіслати це повідомлення {n} користувачам?",
      broadcastProgress: "Надсилання: {done} з {total}, помилок: {failed}", broadcastCancel: "Зупинити",
      broadcastDone: "Останнє: доставлено {sent}, помилок {failed} · {ago}", broadcastCancelled: "Останнє зупинено після {done} з {total}",
      err_folder_exists: "Папка з такою назвою вже є.", err_folders_full: "Не більше 10 папок.",
      err_send_failed: "Бот не зміг надіслати. Натисніть «Старт» у чаті з ботом і спробуйте знову.",
      err_no_write_access: "Дозвольте боту писати вам (або натисніть «Старт» у чаті з ботом) і спробуйте знову.",
      askWriteAccess: "Боту потрібен дозвіл, щоб надіслати вам файл. Дозволити?", exporting: "Надсилаємо…", err_unavailable: "Зараз недоступно.",
      err_broadcast_running: "Попередня розсилка ще триває.",
      act_export: "Експорти", act_share: "Поділилися", act_inline: "Інлайн-пошук (корист.)", act_inline_sent: "Надіслано карток",
      inlineHint: "Порада: у будь-якому чаті напишіть @{bot} і назву предмета — бот надішле картку з ціною.",
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

  // "₴", "$": for labels such as "Profit, ₴".
  function currencySymbol() {
    const parts = numberFormat({ style: "currency", currency: state.currency, currencyDisplay: "narrowSymbol" }).formatToParts(0);
    return (parts.find((p) => p.type === "currency") || { value: state.currency }).value;
  }

  function percent(ratio) {
    return numberFormat({ style: "percent", maximumFractionDigits: 1, signDisplay: "exceptZero" }).format(ratio);
  }

  function trend(ratio) {
    return ratio > 0.0005 ? "up" : ratio < -0.0005 ? "down" : "hint";
  }

  // What selling one item would bring after Steam's fee.
  function net(price) {
    // Convert to integer minor units first; floating division can lose one cent.
    return price == null ? null : Math.floor(Math.round(price * 100) * 100 / (100 + STEAM_FEE_PERCENT)) / 100;
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

  // Accepts "464", "12,5", "1 234,50", "1,234.50", "1.234,50", "1.234.567"; rounds to cents.
  // Separators that can't be thousands ("12.5.3", "1,2,3") are a typo, not a number:
  // guessing would silently turn a price into one ten times bigger.
  function parseAmount(text) {
    const s = String(text).replace(/[\s']/g, "");
    if (!/^[\d.,]+$/.test(s) || !/\d/.test(s)) return null;
    const seps = s.replace(/\d/g, "");
    const last = Math.max(s.lastIndexOf("."), s.lastIndexOf(","));
    const mark = s[last];
    const head = s.slice(0, last), frac = s.slice(last + 1);
    let plain;
    if (!seps) {
      plain = s;
    } else if (seps.length === 1) {
      // "1,234" / "1.234": a lone separator with 3 digits after it groups thousands.
      plain = frac.length === 3 && mark !== decimalMark ? head + frac : `${head}.${frac}`;
    } else if (!seps.includes(mark === "." ? "," : ".")) {
      // One kind, several times: only thousands ("1.234.567").
      if (!new RegExp(`^\\d{1,3}(\\${mark}\\d{3})+$`).test(s)) return null;
      plain = s.split(mark).join("");
    } else {
      // Both: the last one is the decimal mark, the other groups thousands.
      const other = mark === "." ? "," : ".";
      if (head.includes(mark) || !new RegExp(`^\\d{1,3}(\\${other}\\d{3})*$`).test(head)) return null;
      plain = `${head.split(other).join("")}.${frac}`;
    }
    const n = Math.round(Number(plain) * 100) / 100;
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

  // The big number on top. Long amounts shrink (CSS reads --chars) instead of
  // running under the buttons beside them on narrow screens.
  function heroValue(text, extraClass = "") {
    const el = h("div", { class: `hero-value num${extraClass ? ` ${extraClass}` : ""}` }, text);
    el.style.setProperty("--chars", String(text.length));
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
    el.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      e.preventDefault();
      handler(e);
    });
    return el;
  }

  const app = document.getElementById("app");
  function mount(nodes, { keepScroll = false } = {}) {
    const y = window.scrollY;
    const shown = nodes.filter(Boolean);
    app.replaceChildren(...shown);
    document.body.classList.toggle("has-nav", shown.some((n) => n.classList && n.classList.contains("bottom-nav")));
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

  // raw: a Blob sent as is (the share picture), with its own type, or a FormData.
  async function api(path, { method = "GET", body, raw, signal } = {}) {
    let res;
    let data = null;
    try {
      res = await fetch(path, {
        method, signal,
        headers: Object.assign({ Authorization: `tma ${initData}` },
          raw instanceof FormData ? {} : raw ? { "Content-Type": raw.type }
            : body ? { "Content-Type": "application/json" } : {}),
        body: raw || (body ? JSON.stringify(body) : undefined),
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
    tab: "overview",
    period: "30d",
    history: null, // { period, points, change, change_ratio } of the last answer
    historyError: false,
    historyRequest: 0,
    historyAt: 0, // when history was last fetched; 0 = fetch on the next Overview render
    historyLoading: false,
    holdingsKey: "",
    chartDay: null, // the day the user picked on the chart, kept across redraws
    currency: "USD",
    portfolio: null,
    loadError: null, // first load failed: nothing to show
    stale: null, // a later refresh failed: data on screen may be old
    busy: false,
    back: null,
    selected: null, // Set of hash names while Home is in selection mode
    folder: (() => { // the folder Home shows (null: all), remembered per device
      try { return JSON.parse(localStorage.getItem("folder") || "null"); } catch (e) { return null; }
    })(),
  };

  function setPortfolio(p) {
    // Quantities changed (a save, import or removal): the chart is out of date.
    const key = p.items.map((i) => `${i.hash_name}:${i.qty}`).join("|");
    if (key !== state.holdingsKey) state.historyAt = 0;
    state.holdingsKey = key;
    state.portfolio = p;
    state.currency = p.currency;
    if (state.folder != null && !(p.folders || []).some((f) => f.id === state.folder)) setFolder(null);
    state.loadError = null;
    state.stale = null;
  }

  function heldItem(hashName) {
    return ((state.portfolio && state.portfolio.items) || []).find((i) => i.hash_name === hashName) || null;
  }

  let pendingPoll = 0;
  const appVersion = (document.querySelector('meta[name="app-version"]') || {}).content || "";

  // A deploy happened while the app was open: reload into the new version, but
  // only on Home and never twice for the same version (no reload loops).
  function reloadIfOutdated(version) {
    if (!version || !appVersion || version === appVersion || appVersion.includes("{")) return;
    if (state.screen !== "home" || state.busy || imp.busy) return;
    try {
      if (sessionStorage.getItem("reloadedFor") === version) return;
      sessionStorage.setItem("reloadedFor", version);
    } catch (e) { /* storage blocked: reload once anyway */ }
    window.location.reload();
  }

  let opened = false;

  // ?item=<hash name>: opened from a price card sent in inline mode (via the bot).
  let linkedItem = (() => {
    try { return new URLSearchParams(location.search).get("item"); } catch (e) { return null; }
  })();
  if (linkedItem) {
    // Opened once: a reload (e.g. into a new version) lands on Home.
    try { history.replaceState(null, "", location.pathname + location.hash); } catch (e) { /* cosmetic */ }
  }

  async function loadPortfolio() {
    try {
      const p = await api(opened ? "/api/portfolio" : "/api/portfolio?open=1");
      opened = true;
      setPortfolio(p);
      reloadIfOutdated(p.version);
      if (linkedItem && state.screen === "home") {
        const name = linkedItem;
        linkedItem = null;
        const held = heldItem(name);
        showItem(held || { hash_name: name, name, icon: null }, held ? "edit" : "add", null, { quiet: true });
        return;
      }
    } catch (e) {
      if (state.portfolio) state.stale = e;
      else state.loadError = e;
    }
    if (state.screen === "home") showHome({ keepScroll: true });
  }

  // Daily closes change slowly: Overview asks at most once a minute, plus right
  // after the holdings or the period change. Polls never touch it otherwise.
  const HISTORY_TTL = 60000;

  // History is per period and folder: an answer for another pair is shown dimmed.
  function historyKey() {
    return `${state.period}|${state.folder == null ? "" : state.folder}`;
  }

  function historyDue() {
    return !state.historyLoading && (!state.history || state.history.key !== historyKey()
      || Date.now() - state.historyAt > HISTORY_TTL);
  }

  async function loadHistory() {
    const request = ++state.historyRequest;
    const period = state.period;
    const key = historyKey();
    state.historyLoading = true;
    let redraw = true;
    try {
      const folder = state.folder == null ? "" : `&folder=${state.folder}`;
      const data = await api(`/api/portfolio/history?period=${period}${folder}`);
      if (request !== state.historyRequest) return;
      const next = { period, key, points: data.points, change: data.change, change_ratio: data.change_ratio };
      // Same answer as on screen: leave the chart (and the user's pick) alone.
      redraw = state.historyError || JSON.stringify(next) !== JSON.stringify(state.history);
      state.history = next;
      state.historyError = false;
    } catch (e) {
      if (request !== state.historyRequest) return;
      state.historyError = true;
    } finally {
      if (request === state.historyRequest) {
        state.historyLoading = false;
        state.historyAt = Date.now();
      }
    }
    if (redraw && state.screen === "home" && state.tab === "overview") showHome({ keepScroll: true });
  }

  // Right after an import prices arrive one by one: follow them closely.
  function followPendingPrices() {
    clearTimeout(pendingPoll);
    pendingPoll = setTimeout(() => {
      if (state.screen === "home" && document.visibilityState === "visible") loadPortfolio();
    }, 3000);
  }

  function back() {
    if (!state.busy && state.back) state.back();
  }

  // -- screen: portfolio -------------------------------------------------------

  // value: everything with a market price. P&L and "invested" only cover items
  // whose price paid is known; the rest is counted and named separately.
  function totals(items) {
    let value = 0, trackedValue = 0, pricedCost = 0, cost = 0, unpriced = 0, noBuy = 0, tracked = 0;
    for (const it of items) {
      const known = it.buy_price != null;
      if (known) cost += it.buy_price * it.qty;
      else noBuy += 1;
      if (it.price == null) { unpriced += 1; continue; }
      value += net(it.price) * it.qty;
      if (known) {
        tracked += 1;
        trackedValue += net(it.price) * it.qty;
        pricedCost += it.buy_price * it.qty;
      }
    }
    return { value, trackedValue, pricedCost, cost, unpriced, noBuy, tracked };
  }

  const SVG_NS = "http://www.w3.org/2000/svg";

  function svgEl(tag, attrs, parent) {
    const el = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs)) el.setAttribute(key, value);
    if (parent) parent.append(el);
    return el;
  }

  // Line icons drawn with the text colour, so they follow the theme and the active tab.
  const TAB_ICONS = {
    overview: "M4 20V10l8-6 8 6v10h-5v-6H9v6z",
    portfolio: "M4 6h16M4 12h16M4 18h16",
  };

  function tabIcon(tab) {
    const svg = svgEl("svg", { class: "tab-icon", viewBox: "0 0 24 24", "aria-hidden": "true" });
    svgEl("path", { d: TAB_ICONS[tab] }, svg);
    return svg;
  }

  function bottomNav() {
    return h("nav", { class: "bottom-nav", "aria-label": t("sections") },
      [["overview", t("overview")], ["portfolio", t("portfolioTab")]].map(([tab, label]) =>
        h("button", { type: "button", class: `tab${state.tab === tab ? " active" : ""}`,
          "aria-current": state.tab === tab ? "page" : null,
          onclick: () => {
            if (state.tab === tab) return;
            state.tab = tab;
            state.selected = null;
            haptic.tap();
            showHome();
          },
        }, tabIcon(tab), label)),
    );
  }

  function shortDay(day) {
    return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" })
      .format(new Date(`${day}T12:00:00Z`));
  }

  // One series, no legend: the value by day. The scale is labelled (high, low,
  // first and last day); the line is not filled, since the axis doesn't start at 0.
  // Dots are HTML so they stay round while the SVG stretches to the width.
  const W = 320, H = 140, PAD_X = 8, TOP = 18, BOTTOM = 122;

  function chart(points) {
    const values = points.map((p) => p.value);
    const low = Math.min(...values), high = Math.max(...values);
    const span = Math.max(high - low, Math.max(high, 1) * 0.04);
    const floor = low - (span - (high - low)) / 2;
    const coords = points.map((p, i) => ({
      x: points.length === 1 ? W / 2 : PAD_X + i * (W - 2 * PAD_X) / (points.length - 1),
      y: BOTTOM - (p.value - floor) * (BOTTOM - TOP) / span,
    }));
    const svg = svgEl("svg", { class: "value-chart", viewBox: `0 0 ${W} ${H}`, preserveAspectRatio: "none",
      "aria-hidden": "true" });
    if (coords.length > 1) {
      svgEl("path", { class: "chart-line",
        d: coords.map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ") }, svg);
    }
    const at = (el, c) => {
      el.style.left = `${(c.x / W) * 100}%`;
      el.style.top = `${(c.y / H) * 100}%`;
      return el;
    };
    const marks = points.map((p, i) => p.changed && at(h("span", { class: "chart-dot change" }), coords[i]));
    const focus = h("span", { class: "chart-dot focus" });
    const plot = h("div", {
      class: "chart-plot-area", role: "slider", tabindex: "0", "aria-label": t("chartLabel"),
      "aria-valuemin": "0", "aria-valuemax": String(points.length - 1),
    }, svg, marks, focus,
    points.length > 1 && high > low && [
      h("span", { class: "chart-label top hint num" }, money(high)),
      h("span", { class: "chart-label bottom hint num" }, money(low)),
    ]);
    const info = h("div", { class: "chart-info hint num" });
    const legend = points.some((p) => p.changed) && h("div", { class: "chart-legend hint" },
      h("span", { class: "chart-dot change static", "aria-hidden": "true" }), t("compositionChanged"));
    let selected = -1;
    const pick = (i) => {
      if (i === selected) return;
      selected = i;
      const point = points[i];
      at(focus, coords[i]);
      const text = `${shortDay(point.day)} · ${money(point.value)}${point.changed ? ` · ${t("compositionChanged")}` : ""}`;
      info.textContent = text;
      // Hidden, not removed: the chart must not jump while the finger moves.
      if (legend) legend.style.visibility = point.changed ? "hidden" : "";
      plot.setAttribute("aria-valuenow", String(i));
      plot.setAttribute("aria-valuetext", text);
    };
    const kept = points.findIndex((p) => p.day === state.chartDay);
    pick(kept >= 0 ? kept : points.length - 1);
    const choose = (i) => {
      if (i !== selected) haptic.tap();
      pick(i);
      // Today stays "today" on the next redraw; an older day stays picked.
      state.chartDay = i === points.length - 1 ? null : points[i].day;
    };
    const pointAt = (event) => {
      const rect = plot.getBoundingClientRect();
      const x = (event.clientX - rect.left) / rect.width * W;
      choose(points.length === 1 ? 0 : Math.max(0, Math.min(points.length - 1,
        Math.round((x - PAD_X) * (points.length - 1) / (W - 2 * PAD_X)))));
    };
    plot.addEventListener("pointermove", pointAt);
    plot.addEventListener("pointerdown", pointAt);
    plot.addEventListener("keydown", (event) => {
      const moves = { ArrowLeft: selected - 1, ArrowRight: selected + 1, Home: 0, End: points.length - 1 };
      if (!(event.key in moves)) return;
      event.preventDefault();
      choose(Math.max(0, Math.min(points.length - 1, moves[event.key])));
    });
    return h("div", { class: "chart-box" },
      plot,
      h("div", { class: "chart-axis hint num" },
        h("span", {}, shortDay(points[0].day)),
        points.length > 1 && h("span", {}, shortDay(points[points.length - 1].day))),
      info,
      legend);
  }

  function chartSection() {
    const history = state.history;
    const current = history && history.key === historyKey();
    // A single day is not a line yet: say so here once, rather than draw a lone dot.
    if (history && history.points.length > 1) {
      // While another period loads, the old line stays (dimmed) instead of jumping.
      const box = chart(history.points);
      if (!current) box.classList.add("loading");
      return box;
    }
    if (state.historyError) {
      return tappable(h("p", { class: "chart-box chart-empty hint" }, t("historyUnavailable")), () => {
        haptic.tap();
        loadHistory();
      });
    }
    if (!history || !current) return h("div", { class: "chart-box chart-empty", "aria-hidden": "true" }, h("div", { class: "sk sk-chart" }));
    return h("p", { class: "chart-box chart-empty hint" }, t("historyGrowing"));
  }

  // Above the chart: how much prices moved the portfolio over the period.
  // Items added or removed are left out, so a purchase never looks like a gain.
  function periodChange() {
    const history = state.history;
    if (!history || history.key !== historyKey()) {
      return h("div", { class: "overview-change" }, h("span", { class: "sk sk-line change" }));
    }
    if (history.change == null) return null; // the chart area says why
    const ratio = history.change_ratio;
    return h("div", { class: "overview-change num" },
      h("span", { class: trend(ratio != null ? ratio : history.change) },
        money(history.change, true), ratio != null && ` · ${percent(ratio)}`),
      h("div", { class: "overview-scope hint" }, t("marketOnly", { period: t(`scope_${state.period}`) })));
  }

  function showOverview({ keepScroll = false } = {}) {
    main.set(t("addItem"), showSearch);
    const p = state.portfolio;
    const items = visibleItems(p);
    if (!items.length) {
      mount([folderBar(p), h("p", { class: "message" }, t("emptyFolder")), bottomNav()], { keepScroll });
      return;
    }
    const { value, trackedValue, pricedCost, noBuy, tracked, unpriced } = totals(items);
    const pending = items.filter((it) => it.pending).length;
    if (pending) followPendingPrices();
    if (historyDue()) loadHistory();
    const pnl = trackedValue - pricedCost;
    const top = [...items].filter((it) => it.price != null)
      .sort((a, b) => net(b.price) * b.qty - net(a.price) * a.qty).slice(0, 5);
    const range = h("div", { class: "chart-ranges", role: "group", "aria-label": t("period") },
      ["7d", "30d", "all"].map((period) =>
        h("button", { type: "button", class: period === state.period ? "active" : "",
          "aria-pressed": String(period === state.period), onclick: () => {
            if (state.period === period) return;
            state.period = period;
            haptic.tap();
            showHome({ keepScroll: true });
          },
        }, t(`range_${period}`))));
    const foot = [`${t(`kind_${p.price_kind}`)}, ${t("afterFee")}`];
    if (p.updated_at) foot.push(t("updated", { ago: ago(p.updated_at) }));
    if (unpriced - pending > 0) foot.push(t("unpriced", { n: unpriced - pending }));
    const chartFocused = document.activeElement && document.activeElement.classList.contains("chart-plot-area");
    mount([
      folderBar(p),
      h("section", { class: "overview-hero" },
        shareButton(),
        bellButton(),
        heroValue(money(value)),
        periodChange(),
        pricedCost > 0 && h("div", { class: "hero-sub hint num" }, `${t("profitLabel")} `,
          h("span", { class: trend(pnl / pricedCost) }, `${money(pnl, true)} · ${percent(pnl / pricedCost)}`),
          noBuy > 0 && ` · ${t("pnlScope", { n: tracked, total: items.length })}`),
        state.folder == null && realizedLine(p.realized),
        pending > 0 && h("div", { class: "progress" },
          h("div", { class: "hero-sub hint num" }, t("pricing", { n: items.length - pending, total: items.length })),
          progressBar((items.length - pending) / items.length))),
      syncCard(p.sync),
      p.offer_digest && digestOffer(),
      chartSection(),
      range,
      h("section", { class: "top-section" },
        top.length > 0 && h("div", { class: "top-heading" }, h("span", {}, t("topValue")), h("span", { class: "hint" }, t("changeDay"))),
        top.length > 0 && h("ul", { class: "top-list" }, top.map((it) => tappable(h("li", { class: "top-row" },
          thumb(it.icon),
          h("div", { class: "top-name" }, it.name),
          h("div", { class: "top-values num" },
            h("div", {}, money(net(it.price) * it.qty)),
            h("div", { class: `top-change ${it.change_24h == null ? "hint" : trend(it.change_24h)}` },
              it.change_24h == null ? "—" : percent(it.change_24h))),
        ), () => { haptic.tap(); showItem(it, "edit"); }))),
        h("button", { type: "button", class: "view-all", onclick: () => {
          state.tab = "portfolio";
          haptic.tap();
          showHome();
        } }, t("allItems"))),
      ...(watchSection(p) || []),
      h("p", { class: "foot hint" }, foot.join(" · ")),
      state.stale && h("p", { class: "foot down" }, errorText(state.stale)),
      p.is_admin && h("p", { class: "foot" },
        h("button", { type: "button", class: "link-btn", onclick: showAdmin }, t("stats"))),
      bottomNav(),
    ], { keepScroll });
    if (chartFocused) {
      const plot = app.querySelector(".chart-plot-area");
      if (plot) plot.focus({ preventScroll: true });
    }
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
        bottomNav(),
      ]);
      return;
    }

    if (state.tab === "overview" && p.items.length) {
      showOverview({ keepScroll });
      return;
    }
    main.set(t("addItem"), showSearch);
    if (state.selected) {
      // Selection mode: back leaves it, the main button removes the selection.
      const held = new Set(p.items.map((i) => i.hash_name));
      state.selected = new Set([...state.selected].filter((n) => held.has(n)));
      if (!p.items.length) state.selected = null;
    }
    if (state.selected) {
      const n = state.selected.size;
      state.back = exitSelection;
      setBack(true);
      main.set(t("deleteN", { n }), removeSelected, { enabled: n > 0, busy: state.busy, danger: true });
    }
    if (!p.items.length) {
      mount([h("section", { class: "empty-state" },
        bellButton(),
        h("div", { class: "empty-icon", "aria-hidden": "true" }, "📦"),
        h("div", { class: "empty-title" }, t("emptyTitle")),
        h("p", { class: "empty-text" }, t("emptyText")),
        realizedLine(p.realized),
      ), syncCard(p.sync), ...(watchSection(p) || []), p.is_admin && h("p", { class: "foot" }, h("button", { type: "button", class: "link-btn", onclick: showAdmin }, t("stats"))),
      bottomNav()]);
      return;
    }

    const shown = visibleItems(p);
    if (!shown.length) {
      state.selected = null;
      mount([folderBar(p), h("p", { class: "message" }, t("emptyFolder")), portfolioLinks(p), bottomNav()], { keepScroll });
      return;
    }
    const { value, trackedValue, pricedCost, cost, unpriced, noBuy, tracked } = totals(shown);
    const pending = shown.filter((i) => i.pending).length;
    if (pending) followPendingPrices();
    const pnl = trackedValue - pricedCost;
    const items = sortItems(shown);

    const foot = [`${t(`kind_${p.price_kind}`)}, ${t("afterFee")}`];
    if (p.updated_at) foot.push(t("updated", { ago: ago(p.updated_at) }));
    if (unpriced - pending > 0) foot.push(t("unpriced", { n: unpriced - pending }));

    mount([
      !state.selected && folderBar(p),
      h("section", { class: "hero" },
        !state.selected && bellButton(),
        heroValue(money(value)),
        pricedCost > 0 && h("div", { class: `hero-pnl num ${trend(pnl / pricedCost)}` },
          `${money(pnl, true)} · ${percent(pnl / pricedCost)}`,
          // Say what the profit covers when some items have no price paid.
          noBuy > 0 && h("span", { class: "hint scope" }, ` · ${t("pnlScope", { n: tracked, total: shown.length })}`)),
        // Invested matches what value and P&L cover; unpriced items are listed apart.
        (pricedCost > 0 || cost > 0) && h("div", { class: "hero-sub hint num" }, `${t("invested")} ${money(pricedCost)}`,
          cost > pricedCost && ` ${t("unpricedCost", { cost: money(cost - pricedCost) })}`),
        noBuy > 0 && pricedCost === 0 && h("div", { class: "hero-sub hint" }, t("withoutBuy", { n: noBuy })),
        !state.selected && state.folder == null && realizedLine(p.realized),
        pending > 0 && h("div", { class: "progress" },
          h("div", { class: "hero-sub hint num" }, t("pricing", { n: shown.length - pending, total: shown.length })),
          progressBar((shown.length - pending) / shown.length)),
      ),
      !state.selected && syncCard(p.sync),
      toolbar(shown),
      h("ul", { class: "list" }, items.map(homeRow)),
      ...(!state.selected && watchSection(p) || []),
      h("p", { class: "foot hint" }, foot.join(" · ")),
      state.stale && h("p", { class: "foot down" }, errorText(state.stale)),
      !state.selected && portfolioLinks(p),
      !state.selected && bottomNav(),
    ], { keepScroll });
  }

  // -- sorting -------------------------------------------------------------------

  // key -> [value of an item (null = unknown, always listed last), default direction]
  const SORTS = {
    value: [(it) => (it.price == null ? null : net(it.price) * it.qty), "desc"],
    change24h: [(it) => it.change_24h, "desc"],
    profitPct: [(it) => (it.price == null || !(it.buy_price > 0) ? null : net(it.price) / it.buy_price - 1), "desc"],
    profit: [(it) => (it.price == null || it.buy_price == null ? null : (net(it.price) - it.buy_price) * it.qty), "desc"],
    qty: [(it) => it.qty, "desc"],
    price: [(it) => it.price, "desc"],
    name: [(it) => it.name.toLocaleLowerCase(locale), "asc"],
    added: [(it, i) => i, "desc"], // the server lists items oldest first
  };

  const sort = (() => {
    try {
      const saved = JSON.parse(localStorage.getItem("sort") || "null");
      if (saved && SORTS[saved.key] && (saved.dir === "asc" || saved.dir === "desc")) return saved;
    } catch (e) { /* storage unavailable: use the default */ }
    return { key: "value", dir: "desc" };
  })();

  function saveSort() {
    try { localStorage.setItem("sort", JSON.stringify(sort)); } catch (e) { /* per-device nicety only */ }
  }

  function sortItems(items) {
    const [get] = SORTS[sort.key];
    const sign = sort.dir === "asc" ? 1 : -1;
    return items
      .map((it, i) => ({ it, v: get(it, i), i }))
      .sort((a, b) => {
        if (a.v == null || b.v == null) return (a.v == null) - (b.v == null) || a.i - b.i;
        const c = typeof a.v === "string" ? a.v.localeCompare(b.v, locale) : a.v - b.v;
        return c * sign || a.i - b.i;
      })
      .map((x) => x.it);
  }

  // "Value ↓": the name opens the platform's own picker, the arrow flips direction.
  function sortControl() {
    const select = h("select", { class: "sort-select", "aria-label": t("sortBy") },
      Object.keys(SORTS).map((key) => h("option", { value: key, selected: key === sort.key },
        t(`sort_${key}`, { cur: currencySymbol() }))));
    select.addEventListener("change", () => {
      sort.key = select.value;
      sort.dir = SORTS[sort.key][1];
      saveSort();
      haptic.tap();
      showHome({ keepScroll: true });
    });
    const arrow = h("button", {
      type: "button", class: "sort-dir", "aria-label": t(sort.dir === "asc" ? "sortAsc" : "sortDesc"),
      onclick: () => {
        sort.dir = sort.dir === "asc" ? "desc" : "asc";
        saveSort();
        haptic.tap();
        showHome({ keepScroll: true });
      },
    }, sort.dir === "asc" ? "↑" : "↓");
    return h("div", { class: "sort" }, select, arrow);
  }

  // Above the list: "Select" + sort, or, while selecting, "Select all" + "Done".
  function toolbar(items) {
    const link = (text, onclick) => h("button", { type: "button", class: "link-btn", onclick }, text);
    if (state.selected) {
      const all = state.selected.size === items.length;
      return h("div", { class: "toolbar" },
        link(all ? t("selectNone") : t("selectAll"), () => {
          state.selected = all ? new Set() : new Set(items.map((i) => i.hash_name));
          haptic.tap();
          showHome({ keepScroll: true });
        }),
        state.selected.size > 0 && moveSelect(),
        link(t("done"), exitSelection));
    }
    return h("div", { class: "toolbar" },
      link(t("select"), () => {
        state.selected = new Set();
        haptic.tap();
        showHome({ keepScroll: true });
      }),
      items.length > 1 ? sortControl() : h("span", {}));
  }

  function exitSelection() {
    if (state.busy) return;
    state.selected = null;
    showHome({ keepScroll: true });
  }

  async function removeSelected() {
    const names = [...(state.selected || [])];
    if (state.busy || !names.length || !(await confirmUser(t("deleteConfirm", { n: names.length })))) return;
    state.busy = true;
    showHome({ keepScroll: true });
    try {
      setPortfolio(await api("/api/holdings/delete", { method: "POST", body: { hash_names: names } }));
      haptic.ok();
      state.selected = null;
    } catch (e) {
      haptic.fail();
      alertUser(errorText(e));
    }
    state.busy = false;
    if (state.screen === "home") showHome({ keepScroll: true });
  }

  function progressBar(ratio) {
    const bar = h("div", { class: "bar", role: "progressbar", "aria-valuenow": String(Math.round(ratio * 100)) },
      h("span", {}));
    bar.style.setProperty("--done", `${Math.round(ratio * 100)}%`);
    return bar;
  }

  function homeRow(it) {
    const priced = it.price != null;
    const value = priced ? net(it.price) * it.qty : null;
    const ratio = priced && it.buy_price > 0 ? net(it.price) / it.buy_price - 1 : null;
    const sub = it.buy_price == null ? t("noBuyPrice", { qty: it.qty })
      : t("position", { qty: it.qty, price: money(it.buy_price) });
    const selecting = !!state.selected;
    const on = selecting && state.selected.has(it.hash_name);
    const row = h("li", { class: `row${selecting ? " pick" : ""}${on ? " selected" : ""}` },
      selecting && h("span", { class: "check", "aria-hidden": "true" }),
      thumb(it.icon),
      h("div", { class: "row-main" },
        h("div", { class: "row-title" }, it.name),
        h("div", { class: "row-sub hint num" }, sub),
      ),
      h("div", { class: "row-side num" },
        it.pending ? h("div", { class: "sk sk-price", "aria-hidden": "true" }) : h("div", { class: "row-value" }, money(value)),
        (ratio != null || it.change_24h != null) && h("div", { class: "row-metrics" },
          ratio != null && h("span", { class: `row-pnl ${trend(ratio)}` }, percent(ratio)),
          it.change_24h != null && h("span", { class: `row-change ${trend(it.change_24h)}` },
            `${t("changeDay")} ${percent(it.change_24h)}`),
        ),
      ),
    );
    if (!selecting) return tappable(row, () => { haptic.tap(); showItem(it, "edit"); });
    row.setAttribute("aria-checked", String(on));
    return tappable(row, () => {
      if (on) state.selected.delete(it.hash_name);
      else state.selected.add(it.hash_name);
      haptic.tap();
      showHome({ keepScroll: true });
    });
  }

  // -- screen: search ----------------------------------------------------------

  const search = { query: "", results: null, error: null, loading: false, cache: new Map(), timer: 0, ctrl: null };

  function showSearch() {
    state.screen = "search";
    state.back = () => { if (!imp.busy) showHome(); };
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
    loadRecents();
    // Only a fresh search pops the keyboard; coming back keeps the list readable.
    if (!search.query) setTimeout(() => input.focus(), 50);
  }

  function onQuery(value) {
    search.query = value;
    clearTimeout(search.timer);
    if (search.ctrl) search.ctrl.abort();
    search.ctrl = null;
    const q = value.trim();
    if (looksLikeProfile(q)) {
      loadInventory(q);
      return;
    }
    resetImport();
    main.set(null);
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
    if (imp.loading || imp.data || imp.error) {
      renderImport(box);
      return;
    }
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
      content = [h("p", { class: "message" }, t("searchHint")), recentProfiles()];
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
    box.replaceChildren(...[].concat(content).filter(Boolean));
  }

  // -- recent Steam profiles ----------------------------------------------------
  // Kept on the server (so every device has them): each profile whose inventory
  // was opened, newest first. Tapping one opens that inventory again.

  const recents = { list: null };

  async function loadRecents() {
    try {
      recents.list = (await api("/api/profiles")).profiles;
    } catch (e) { /* the hint alone is fine */ }
    if (state.screen === "search") renderResults();
  }

  function profileLabel(p) {
    return p.name || p.vanity || `Steam ID …${p.steamid.slice(-4)}`;
  }

  function recentProfiles() {
    const list = recents.list;
    if (!list || !list.length) return null;
    return h("section", { class: "recents" },
      h("div", { class: "section-title hint" }, t("recentProfiles")),
      h("ul", { class: "list" }, list.map((p) => {
        const label = profileLabel(p);
        const picture = p.avatar
          ? h("img", { class: "thumb avatar", alt: "", loading: "lazy", src: p.avatar })
          : h("div", { class: "thumb avatar initial", "aria-hidden": "true" }, label.slice(0, 1).toUpperCase());
        const forget = h("button", {
          type: "button", class: "folder-delete", "aria-label": `${t("forgetProfile")}: ${label}`,
          onkeydown: (e) => e.stopPropagation(), // Enter here must not open the profile
          onclick: async (e) => {
            e.stopPropagation();
            haptic.tap();
            try {
              recents.list = (await api("/api/profiles/delete", { method: "POST", body: { steamid: p.steamid } })).profiles;
            } catch (err) {
              alertUser(errorText(err));
            }
            if (state.screen === "search") renderResults();
          },
        }, "✕");
        return tappable(h("li", { class: "row" },
          picture,
          h("div", { class: "row-main" },
            h("div", { class: "row-title" }, label),
            h("div", { class: "row-sub hint num" }, `${t("profileItems", { n: p.items })} · ${ago(p.used_at)}`)),
          forget,
        ), () => {
          haptic.tap();
          const link = `https://steamcommunity.com/profiles/${p.steamid}`;
          const input = document.querySelector(".search input");
          if (input) input.value = link;
          onQuery(link);
        });
      })));
  }

  // -- import from a Steam inventory ------------------------------------------
  // A profile / trade link pasted into search turns the result list into this.

  const imp = { key: "", loading: false, data: null, error: null, ctrl: null,
                selected: new Set(), mode: "none", prices: new Map(), busy: false };

  function looksLikeProfile(q) {
    return /^7656119\d{10}$/.test(q) || /steamcommunity\.com\/(id|profiles|tradeoffer)\//i.test(q);
  }

  function resetImport() {
    if (imp.ctrl) imp.ctrl.abort();
    Object.assign(imp, { key: "", loading: false, data: null, error: null, ctrl: null });
  }

  // keep: after "import expired", carry the user's choices over to the fresh list.
  // preselect: hash names to tick (what the daily inventory check found).
  async function loadInventory(q, { keep = false, preselect = null } = {}) {
    if (!keep && !preselect && imp.key === q && (imp.loading || imp.data)) { renderResults(); return; }
    const kept = keep ? { selected: imp.selected, prices: imp.prices, mode: imp.mode } : null;
    resetImport();
    const ctrl = new AbortController();
    Object.assign(imp, { key: q, loading: true, ctrl });
    Object.assign(search, { loading: false, results: null, error: null });
    renderResults();
    let data = null;
    let error = null;
    try {
      data = await api(`/api/inventory?profile=${encodeURIComponent(q)}`, { signal: ctrl.signal });
    } catch (e) {
      if (e.name === "AbortError") return;
      error = e;
    }
    if (imp.ctrl !== ctrl) return; // the query changed meanwhile
    Object.assign(imp, { ctrl: null, loading: false, data, error, prices: new Map() });
    if (data) loadRecents(); // it is the newest recent profile now
    if (data) {
      const free = data.items.filter((i) => !i.held);
      if (kept) {
        const names = new Set(free.map((i) => i.hash_name));
        imp.selected = new Set([...kept.selected].filter((n) => names.has(n)));
        imp.prices = kept.prices;
        imp.mode = kept.mode;
      } else if (preselect) {
        const names = new Set(preselect);
        imp.selected = new Set(free.filter((i) => names.has(i.hash_name)).slice(0, data.room).map((i) => i.hash_name));
      } else {
        // Cases and capsules are what people invest in; take everything if there are none.
        const containers = free.filter((i) => i.container);
        const pick = (containers.length ? containers : free).slice(0, data.room);
        imp.selected = new Set(pick.map((i) => i.hash_name));
      }
    }
    if (state.screen === "search") renderResults();
  }

  function importPrices() {
    // Manual mode: every filled-in price must parse; empty ones stay unknown.
    const out = new Map();
    for (const name of imp.selected) {
      const text = (imp.prices.get(name) || "").trim();
      if (imp.mode !== "manual" || !text) { out.set(name, null); continue; }
      const v = parseAmount(text);
      if (v == null) return null;
      out.set(name, v);
    }
    return out;
  }

  function updateImportButton() {
    const n = imp.selected.size;
    const fits = imp.data && n <= imp.data.room;
    main.set(t("importN", { n }), doImport, { enabled: n > 0 && fits && importPrices() != null, busy: imp.busy });
  }

  function renderImport(box) {
    if (imp.loading) {
      main.set(null);
      box.replaceChildren(h("ul", { class: "list" }, [0, 1, 2, 3].map(() => h("li", { class: "row" },
        h("div", { class: "thumb placeholder" }),
        h("div", { class: "row-main" }, h("div", { class: "sk sk-line" })),
      ))));
      return;
    }
    if (imp.error) {
      main.set(null);
      box.replaceChildren(tappable(h("p", { class: "message tappable" }, errorText(imp.error), h("br"), t("retry")), () => {
        const q = imp.key;
        resetImport();
        loadInventory(q);
      }));
      return;
    }
    const data = imp.data;
    if (!data.items.length) {
      main.set(null);
      box.replaceChildren(h("p", { class: "message" }, t("noSellable"), h("br"), t("storageNote")));
      return;
    }

    const free = data.items.filter((i) => !i.held);
    const modeHint = h("p", { class: "note" });
    const count = h("span", {});
    const toggleAll = h("button", { type: "button", class: "link-btn" });
    const sides = new Map(); // hash name -> function that refreshes the row's right side

    const segmented = h("div", { class: "segmented", role: "radiogroup", "aria-label": t("importPriceTitle") });
    const modes = ["none", "market", "manual"].map((m) => h("button", {
      type: "button", role: "radio",
      onclick: () => {
        if (imp.mode === m) return;
        imp.mode = m;
        haptic.tap();
        sync();
      },
    }, t(`mode_${m}`)));
    segmented.append(...modes);

    // Updates everything that depends on the selection or mode, in place, so
    // scroll position and a focused price field survive.
    function sync() {
      modes.forEach((b, i) => {
        const on = ["none", "market", "manual"][i] === imp.mode;
        b.classList.toggle("active", on);
        b.setAttribute("aria-checked", String(on));
      });
      modeHint.textContent = t(`modeHint_${imp.mode}`);
      const over = imp.selected.size > data.room;
      count.className = over ? "down" : "hint";
      count.textContent = t("selected", { n: imp.selected.size, total: free.length })
        + (over || data.room < free.length ? ` · ${t("room", { n: data.room })}` : "");
      const full = imp.selected.size >= Math.min(free.length, data.room);
      toggleAll.textContent = full ? t("selectNone") : t("selectAll");
      toggleAll.hidden = free.length === 0;
      sides.forEach((refresh) => refresh());
      updateImportButton();
    }

    toggleAll.addEventListener("click", () => {
      const full = imp.selected.size >= Math.min(free.length, data.room);
      imp.selected = full ? new Set() : new Set(free.slice(0, data.room).map((i) => i.hash_name));
      haptic.tap();
      rows.forEach((r) => r.sync());
      sync();
    });

    const rows = data.items.map((it) => {
      const side = h("div", { class: "pick-side" });
      const sub = h("div", { class: "row-sub hint num" });
      const row = h("li", { class: `row pick${it.held ? " disabled" : ""}` },
        h("span", { class: "check", "aria-hidden": "true" }),
        thumb(it.icon),
        h("div", { class: "row-main" }, h("div", { class: "row-title" }, it.name), sub),
        side,
      );
      let input = null;
      const refreshSide = () => {
        const on = imp.selected.has(it.hash_name);
        const known = it.price != null ? money(it.price) : null;
        sub.textContent = on && known && imp.mode !== "none" ? `× ${it.qty} · ${known}` : `× ${it.qty}`;
        if (it.held) { side.replaceChildren(h("span", { class: "badge" }, t("inPortfolio"))); return; }
        if (on && imp.mode === "manual") {
          if (!input) {
            input = h("input", {
              class: "price-input num", type: "text", inputmode: "decimal", enterkeyhint: "done",
              autocomplete: "off", placeholder: "—", // empty = unknown; today's price is in the row
              "aria-label": t("buyPrice"),
              onclick: (e) => e.stopPropagation(),
              oninput: (e) => {
                imp.prices.set(it.hash_name, e.target.value);
                e.target.classList.toggle("invalid", e.target.value.trim() !== "" && parseAmount(e.target.value) == null);
                updateImportButton();
              },
              onkeydown: (e) => {
                e.stopPropagation(); // Enter must not toggle the row
                if (e.key === "Enter") e.target.blur();
              },
            });
            input.value = imp.prices.get(it.hash_name) || "";
          }
          if (side.firstChild !== input) side.replaceChildren(input);
        } else {
          side.replaceChildren();
        }
      };
      sides.set(it.hash_name, refreshSide);
      row.sync = () => {
        const on = imp.selected.has(it.hash_name);
        row.classList.toggle("selected", on);
        if (!it.held) row.setAttribute("aria-checked", String(on));
      };
      row.sync();
      if (it.held) return row;
      tappable(row, () => {
        if (imp.selected.has(it.hash_name)) imp.selected.delete(it.hash_name);
        else imp.selected.add(it.hash_name);
        haptic.tap();
        row.sync();
        sync();
      });
      return row;
    });

    box.replaceChildren(
      h("div", { class: "section-title hint import-title" }, t("importPriceTitle")),
      segmented,
      modeHint,
      h("div", { class: "import-head" }, count, toggleAll),
      h("ul", { class: "list" }, rows),
      h("p", { class: "foot hint" }, t("storageNote")),
    );
    sync();
  }

  async function doImport() {
    const prices = importPrices();
    if (imp.busy || !prices || !imp.data) return;
    imp.busy = true;
    updateImportButton();
    try {
      const p = await api("/api/import", {
        method: "POST",
        body: {
          steamid: imp.data.steamid,
          price_mode: imp.mode,
          folder: state.folder,
          items: [...imp.selected].map((name) => ({ hash_name: name, buy_price: prices.get(name) })),
        },
      });
      setPortfolio(p);
      haptic.ok();
      imp.busy = false;
      resetImport();
      search.query = "";
      showHome();
    } catch (e) {
      imp.busy = false;
      haptic.fail();
      if (e.code === "import_expired") {
        loadInventory(imp.key, { keep: true });
      } else {
        updateImportButton();
      }
      alertUser(errorText(e));
    }
  }

  // -- notifications: alerts, digest, watchlist ------------------------------
  // The bot sends these; the app only sets them up. Alerts are checked on the
  // server after each price refresh, so there is nothing to poll here.

  const notif = { alerts: null };
  const MAX_ALERTS = 20;

  async function loadAlerts() {
    notif.alerts = (await api("/api/alerts")).alerts;
    return notif.alerts;
  }

  function localZone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch (e) { return null; }
  }

  // The bot may only write to users who allowed it. Asked when it matters:
  // the first alert or digest. true / false, or null when the client can't ask.
  function ensureWriteAccess() {
    if (state.portfolio && state.portfolio.can_notify) return Promise.resolve(true);
    return new Promise((resolve) => {
      if (!nativeUi || !tg.isVersionAtLeast("6.9")) { resolve(null); return; }
      try {
        tg.requestWriteAccess((ok) => {
          if (ok && state.portfolio) state.portfolio.can_notify = true;
          resolve(!!ok);
        });
      } catch (e) { resolve(null); }
    });
  }

  function localDate(sec) {
    return new Intl.DateTimeFormat(locale, { day: "numeric", month: "short" }).format(new Date(sec * 1000));
  }

  function alertValue(metric, v) {
    if (v == null) return "—";
    if (metric === "price") return money(v);
    if (metric === "value_amount") return money(v, true);
    return percent(v);
  }

  function describeAlert(a) {
    const side = a.above ? "above" : "below";
    if (a.hash_name == null) return t(`cond_value_${side}`, { v: alertValue(a.metric, a.threshold), date: localDate(a.created_at) });
    return t(`cond_${a.metric}_${side}`, { v: alertValue(a.metric, a.threshold) });
  }

  function alertStatus(a) {
    if (!a.armed && a.fired_at) return t("firedAgo", { ago: ago(a.fired_at) });
    return a.current == null ? "" : t("nowValue", { v: alertValue(a.metric, a.current) });
  }

  // "−12,5 %", "+3", "10%": a signed number; null when it isn't one.
  function parseSigned(text) {
    const s = String(text).trim().replace(/\s*%$/, "");
    const sign = /^[-−–]/.test(s) ? -1 : 1;
    const amount = parseAmount(s.replace(/^[-−–+]\s*/, ""));
    return amount == null ? null : sign * amount;
  }

  function alertsFull() {
    return (notif.alerts || []).length >= MAX_ALERTS;
  }

  function warnIfUnreachable(granted) {
    if (granted !== true && !(state.portfolio && state.portfolio.can_notify)) alertUser(t("noWriteAccess"));
  }

  function segmentedControl(options, value, onChange, label) {
    let current = value;
    const buttons = options.map(([v, text]) => h("button", {
      type: "button", role: "radio",
      onclick: () => {
        if (v === current) return;
        current = v;
        haptic.tap();
        sync();
        onChange(v);
      },
    }, text));
    const sync = () => buttons.forEach((b, i) => {
      const on = options[i][0] === current;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", String(on));
    });
    sync();
    return h("div", { class: "segmented", role: "radiogroup", "aria-label": label }, buttons);
  }

  function bellButton() {
    const svg = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
    svgEl("path", { d: "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0" }, svg);
    return h("button", { type: "button", class: "bell", "aria-label": t("notifications"), onclick: () => {
      haptic.tap();
      showNotifications();
    } }, svg);
  }

  // `named`: show which item (not needed on that item's own screen).
  function alertRow(a, onTap, named = a.hash_name != null) {
    return tappable(h("li", { class: `row${named ? "" : " compact"}${a.armed ? "" : " sent"}` },
      named && thumb(a.icon),
      h("div", { class: "row-main" },
        named && h("div", { class: "row-title" }, a.name),
        h("div", { class: named ? "row-sub num" : "row-title num" }, describeAlert(a)),
        h("div", { class: "row-sub hint num" }, alertStatus(a))),
    ), () => { haptic.tap(); onTap(); });
  }

  function showNotifications() {
    state.screen = "notifications";
    state.back = () => showHome();
    setBack(true);
    secondary.set(null);
    main.set(null);
    const box = h("div", {}, h("p", { class: "message" }, h("span", { class: "sk sk-line short" })));
    mount([h("h1", { class: "screen-title" }, t("notifications")), box]);
    Promise.all([api("/api/prefs"), loadAlerts()]).then(([prefs, alerts]) => {
      if (state.screen !== "notifications" || !app.contains(box)) return;
      const render = () => box.replaceChildren(...notificationsView(prefs, alerts, render));
      render();
      // A portfolio alert needs a priced portfolio to count from, and room.
      const priced = state.portfolio && totals(state.portfolio.items).value > 0;
      main.set(priced ? t("newPortfolioAlert") : null, () => showAlertEditor(null, null, showNotifications),
        { enabled: alerts.length < MAX_ALERTS });
    }).catch((e) => {
      if (state.screen === "notifications" && app.contains(box)) {
        box.replaceChildren(tappable(h("p", { class: "message tappable" }, errorText(e), h("br"), t("retry")), showNotifications));
      }
    });
  }

  function notificationsView(prefs, alerts, rerender) {
    const section = (title, ...body) => [h("div", { class: "section-title hint" }, title), ...body];
    const portfolioAlerts = alerts.filter((a) => a.hash_name == null);
    const itemAlerts = alerts.filter((a) => a.hash_name != null);
    if (state.portfolio) state.portfolio.can_notify = prefs.can_notify || state.portfolio.can_notify;

    const hint = h("p", { class: "note" });
    const hour = h("select", { class: "hour-select", "aria-label": t("digestAt") },
      Array.from({ length: 24 }, (_, i) => h("option", { value: String(i), selected: i === prefs.digest_hour },
        new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(new Date(2024, 0, 1, i)))));
    const hourRow = h("label", { class: "field" }, h("span", {}, t("digestAt")), hour);
    const save = async (body) => {
      try {
        const next = await api("/api/prefs", { method: "POST", body: Object.assign({ tz: localZone() || undefined }, body) });
        const couldNotify = prefs.can_notify;
        Object.assign(prefs, next);
        if (state.portfolio) state.portfolio.offer_digest = false;
        haptic.ok();
        if (prefs.can_notify !== couldNotify && app.contains(hour)) rerender();
      } catch (e) {
        haptic.fail();
        alertUser(errorText(e));
      }
    };
    const syncDigest = () => {
      hourRow.hidden = prefs.digest === "off";
      hint.textContent = prefs.digest === "off" ? "" : t(`digestHint_${prefs.digest}`);
      hint.hidden = prefs.digest === "off";
    };
    const digest = segmentedControl(["off", "daily", "weekly"].map((d) => [d, t(`digest_${d}`)]), prefs.digest, async (d) => {
      prefs.digest = d;
      syncDigest();
      const granted = d === "off" ? null : await ensureWriteAccess();
      await save({ digest: d, digest_hour: Number(hour.value), write_access: granted === true ? true : undefined });
      if (d !== "off") warnIfUnreachable(granted);
    }, t("digestTitle"));
    hour.addEventListener("change", () => save({ digest_hour: Number(hour.value) }));
    syncDigest();

    const permission = !prefs.can_notify && h("div", { class: "card notice" },
      h("p", {}, t("noWriteAccess")),
      h("button", { type: "button", class: "link-btn", onclick: async () => {
        const granted = await ensureWriteAccess();
        if (granted) await save({ write_access: true });
        else alertUser(t("noWriteAccess"));
      } }, t("allow")));

    // The daily inventory check: only once there is a profile to read.
    const sync = prefs.sync;
    const syncStatus = sync && (sync.error === "private" ? t("syncPrivate")
      : sync.error === "not_found" ? t("syncNotFound") : t("syncChecked", { ago: ago(sync.checked_at) }));
    const syncBody = sync ? [
      segmentedControl([[true, t("sync_on")], [false, t("sync_off")]], sync.enabled, async (on) => {
        try {
          Object.assign(prefs, await api("/api/prefs", { method: "POST", body: { sync_enabled: on } }));
          haptic.ok();
        } catch (e) {
          haptic.fail();
          alertUser(errorText(e));
          if (app.contains(hour)) rerender(); // back to what the server has
        }
      }, t("syncTitle")),
      h("p", { class: "note" }, t("syncHint")),
      h("p", { class: `note${sync.error ? " down" : ""}` }, syncStatus),
    ] : [h("p", { class: "note" }, t("syncNone"))];

    return [
      permission,
      ...section(t("digestTitle"), digest, h("div", { class: "form spaced" }, hourRow), hint),
      ...section(t("syncTitle"), ...syncBody),
      ...(portfolioAlerts.length ? section(t("portfolioAlerts"), h("ul", { class: "list" },
        portfolioAlerts.map((a) => alertRow(a, () => showAlertEditor(null, a, showNotifications))))) : []),
      ...(itemAlerts.length ? section(t("itemAlerts"), h("ul", { class: "list" }, itemAlerts.map((a) => alertRow(a, () =>
        showAlertEditor({ hash_name: a.hash_name, name: a.name, icon: a.icon, price: a.price }, a, showNotifications))))) : []),
      !alerts.length && h("p", { class: "note spaced" }, t("noAlerts")),
      alerts.length > 0 && h("p", { class: `foot ${alerts.length >= MAX_ALERTS ? "down" : "hint"}` },
        alerts.length >= MAX_ALERTS ? t("err_alerts_full") : t("alertsCount", { n: alerts.length, max: MAX_ALERTS })),
    ].filter(Boolean);
  }

  // item: { hash_name, name, icon, price? } or null for the whole portfolio.
  function showAlertEditor(item, alert, backTo) {
    state.screen = "alert";
    state.back = backTo;
    setBack(true);
    secondary.set(null);
    const forPortfolio = !item;
    const held = item ? heldItem(item.hash_name) : null;
    const canProfit = !!(held && held.buy_price > 0);
    const metrics = forPortfolio ? ["value_pct", "value_amount"] : canProfit ? ["price", "profit"] : ["price"];
    let metric = alert ? alert.metric : metrics[0];
    // Owners usually wait to sell higher, watchers to buy lower.
    let above = alert ? alert.above : forPortfolio || metric !== "price" || !!held;
    let price = item ? (held ? held.price : item.price) : null;
    const portfolioValue = state.portfolio ? totals(state.portfolio.items).value : 0;

    const input = numberInput("decimal", "", "done");
    input.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      input.blur();
      main.tap();
    });
    const label = h("span", {});
    const field = h("label", { class: "field" }, label, input);
    const error = h("p", { class: "note down", "aria-live": "polite" });
    const now = h("p", { class: "note num" });
    const met = h("p", { class: "note" });
    const directionBox = h("div", {});

    if (alert) {
      const shown = metric === "price" || metric === "value_amount" ? Math.abs(alert.threshold)
        : metric === "profit" ? alert.threshold * 100 : Math.abs(alert.threshold) * 100;
      input.value = (shown < 0 ? "−" : "") + plainAmount(Math.abs(shown));
    }

    function currentValue() {
      if (metric === "price") return price;
      if (metric === "profit") return price == null || !canProfit ? null : net(price) / held.buy_price - 1;
      if (!alert || alert.hash_name != null || alert.baseline == null) return 0; // a new one starts at "no change"
      if (metric === "value_amount") return portfolioValue - alert.baseline;
      return alert.baseline > 0 ? portfolioValue / alert.baseline - 1 : null;
    }

    // The threshold as the server takes it, or null with the reason in `error`.
    function threshold() {
      if (input.value.trim() === "") return null;
      const typed = parseSigned(input.value);
      if (typed == null) return null;
      if (metric === "profit") return typed > -100 && typed <= 10000 ? typed / 100 : null;
      // Price is a level; portfolio moves are a size, the direction gives the sign.
      const size = metric === "price" ? typed : Math.abs(typed);
      if (!(size > 0)) return null;
      if (metric === "price") return size;
      const sign = above ? 1 : -1;
      if (metric === "value_amount") return sign * size;
      return !above && size >= 100 ? null : sign * size / 100;
    }

    function renderDirection() {
      const labels = metric === "price" || metric === "profit" ? ["dir_above", "dir_below"] : ["dir_up", "dir_down"];
      directionBox.replaceChildren(segmentedControl([[true, t(labels[0])], [false, t(labels[1])]], above,
        (v) => { above = v; refresh(); }, t("direction")));
    }

    function refresh() {
      label.textContent = t({ price: "fieldPrice", profit: "fieldProfit", value_amount: "fieldAmount" }[metric] || "fieldPercent");
      const value = currentValue();
      if (metric === "price") now.textContent = price == null ? "" : t("nowPrice", { v: money(price) });
      else if (metric === "profit") now.textContent = value == null ? "" : t("nowProfit", { v: percent(value) });
      else if (alert && alert.hash_name == null) now.textContent = t("fromBaseline", { date: localDate(alert.created_at) });
      else now.textContent = t("fromValue", { v: money(portfolioValue) });
      now.hidden = !now.textContent;
      const limit = threshold();
      const invalid = input.value.trim() !== "" && limit == null;
      field.classList.toggle("invalid", invalid);
      error.textContent = invalid ? t("err_invalid") : "";
      error.hidden = !invalid;
      const isMet = limit != null && value != null && (above ? value >= limit : value <= limit);
      met.textContent = isMet ? t("metNow") : t("alertOnce");
      const unchanged = alert && limit != null && alert.metric === metric && alert.above === above
        && Math.abs(alert.threshold - limit) < 1e-9;
      main.set(t("save"), save, { enabled: limit != null && !unchanged, busy: state.busy });
    }
    input.addEventListener("input", refresh);

    async function save() {
      const limit = threshold();
      if (state.busy || limit == null) return;
      state.busy = true;
      refresh();
      const granted = await ensureWriteAccess();
      try {
        const data = await api("/api/alerts", { method: "POST", body: {
          id: alert ? alert.id : undefined, hash_name: item ? item.hash_name : null, metric, above, threshold: limit,
          write_access: granted === true ? true : undefined,
        } });
        notif.alerts = data.alerts;
        haptic.ok();
        state.busy = false;
        warnIfUnreachable(granted);
        backTo();
      } catch (e) {
        state.busy = false;
        haptic.fail();
        if (app.contains(input)) refresh();
        alertUser(errorText(e));
      }
    }

    async function remove() {
      if (state.busy || !(await confirmUser(t("deleteAlertConfirm")))) return;
      state.busy = true;
      refresh();
      try {
        notif.alerts = (await api("/api/alerts/delete", { method: "POST", body: { id: alert.id } })).alerts;
        haptic.ok();
        state.busy = false;
        backTo();
      } catch (e) {
        state.busy = false;
        haptic.fail();
        if (app.contains(input)) refresh();
        alertUser(errorText(e));
      }
    }

    const metricControl = metrics.length > 1 && segmentedControl(metrics.map((m) => [m, t(`metric_${m}`)]), metric, (m) => {
      metric = m;
      input.value = "";
      renderDirection();
      refresh();
    }, t("alertType"));
    renderDirection();
    mount([
      h("section", { class: "item-head compact" },
        item && thumb(item.icon, 128),
        h("div", { class: "item-name" }, item ? item.name : t("portfolioTab")),
        h("div", { class: "hint" }, t("alertItemTitle"))),
      metricControl,
      h("div", { class: "spaced" }, directionBox),
      h("div", { class: "form spaced" }, field),
      error,
      now,
      met,
      alert && h("button", { class: "danger-link", type: "button", onclick: remove }, t("deleteAlert")),
    ]);
    refresh();
    if (!alert) setTimeout(() => input.focus(), 50);
    if (item && price === undefined) {
      api(`/api/quote?hash_name=${encodeURIComponent(item.hash_name)}`).then((data) => {
        price = data.price;
        if (app.contains(input)) refresh();
      }).catch(() => {});
    }
  }

  // On the item screen: "Notify me…", "Sold…" for items owned and, for items not
  // owned, following the price (all in the head, where they are seen), plus this
  // item's alerts.
  function itemNotifications(item, reopen) {
    const actions = h("div", { class: "item-actions" });
    const list = h("section", { class: "item-alerts" });
    const render = () => {
      const mine = (notif.alerts || []).filter((a) => a.hash_name === item.hash_name);
      const watched = ((state.portfolio && state.portfolio.watching) || []).some((w) => w.hash_name === item.hash_name);
      const held = heldItem(item.hash_name);
      const snapshot = Object.assign({}, item, { price: held ? held.price : item.price });
      actions.replaceChildren(...[
        h("button", { type: "button", class: "chip", onclick: () => {
          haptic.tap();
          if (alertsFull()) alertUser(t("err_alerts_full"));
          else showAlertEditor(snapshot, null, reopen);
        } }, h("span", { "aria-hidden": "true" }, "🔔 "), t("notifyMe")),
        held && h("button", { type: "button", class: "chip", onclick: () => {
          haptic.tap();
          showSell(held, null, reopen);
        } }, h("span", { "aria-hidden": "true" }, "💰 "), t("sold")),
        canShareInline() && h("button", { type: "button", class: "chip", onclick: () => {
          haptic.tap();
          // Closes the app and lets the user pick a chat, with "@bot <item>" typed in.
          call(() => tg.switchInlineQuery(item.hash_name, ["users", "groups", "channels"]), "6.7");
        } }, h("span", { "aria-hidden": "true" }, "📤 "), t("share")),
        !held && h("button", { type: "button", class: `chip${watched ? " on" : ""}`, "aria-pressed": String(watched), onclick: async () => {
          haptic.tap();
          try {
            setPortfolio(await api("/api/watch", { method: "POST", body: { hash_name: item.hash_name, on: !watched } }));
            if (app.contains(actions)) render();
          } catch (e) {
            haptic.fail();
            alertUser(errorText(e));
          }
        } }, h("span", { "aria-hidden": "true" }, watched ? "★ " : "☆ "), watched ? t("unwatch") : t("watch")),
      ].filter(Boolean));
      list.replaceChildren(...[
        mine.length > 0 && h("div", { class: "section-title hint" }, t("alertsHere")),
        mine.length > 0 && h("ul", { class: "list" }, mine.map((a) => alertRow(a, () => showAlertEditor(snapshot, a, reopen), false))),
      ].filter(Boolean));
    };
    render();
    // Fresh each time: an alert may have fired, or "now" moved, since the last look.
    loadAlerts().then(() => { if (app.contains(actions)) render(); }).catch(() => {});
    return { actions, list };
  }

  function canShareInline() {
    return !!(state.portfolio && state.portfolio.inline) && nativeUi && tg.isVersionAtLeast("6.7");
  }

  // Offered once, after the first import or the third item.
  function digestOffer() {
    const answer = async (on) => {
      haptic.tap();
      state.portfolio.offer_digest = false;
      showHome({ keepScroll: true });
      const granted = on ? await ensureWriteAccess() : null;
      try {
        await api("/api/prefs", { method: "POST", body: on
          ? { digest: "daily", digest_hour: 10, tz: localZone() || undefined, write_access: granted === true ? true : undefined }
          : { digest_offered: true } });
        if (on) {
          haptic.ok();
          warnIfUnreachable(granted);
        }
      } catch (e) { /* offered again next time */ }
    };
    return h("section", { class: "card offer" },
      h("div", { class: "offer-title" }, t("offerTitle")),
      h("p", { class: "hint" }, t("offerText")),
      h("div", { class: "offer-actions" },
        h("button", { type: "button", class: "offer-yes", onclick: () => answer(true) }, t("offerYes")),
        h("button", { type: "button", class: "link-btn", onclick: () => answer(false) }, t("offerNo"))));
  }

  function watchRow(w) {
    return tappable(h("li", { class: "row" },
      thumb(w.icon),
      h("div", { class: "row-main" }, h("div", { class: "row-title" }, w.name)),
      h("div", { class: "row-side num" },
        w.pending ? h("div", { class: "sk sk-price", "aria-hidden": "true" }) : h("div", { class: "row-value" }, money(w.price)),
        w.change_24h != null && h("div", { class: `row-change ${trend(w.change_24h)}` }, `${t("changeDay")} ${percent(w.change_24h)}`)),
    ), () => {
      haptic.tap();
      // A look at the price, not a purchase: no keyboard popping up.
      showItem({ hash_name: w.hash_name, name: w.name, icon: w.icon, price: w.price }, "add", () => showHome(), { quiet: true });
    });
  }

  function watchSection(p) {
    return p.watching && p.watching.length > 0 && [
      h("div", { class: "section-title hint" }, t("watchingTitle")),
      h("ul", { class: "list" }, p.watching.map(watchRow)),
    ];
  }

  // -- sales and the inventory check ------------------------------------------
  // A sale reduces the position and keeps its result: realized profit, measured
  // against the average price paid, like the profit on paper.

  // Under the portfolio's profit; opens the list of sales.
  function realizedLine(r) {
    if (!r || !r.count) return null;
    const ratio = r.profit != null && r.cost > 0 ? r.profit / r.cost : null;
    return tappable(h("div", { class: "hero-sub hint num realized" },
      r.profit == null ? t("soldFor", { v: money(r.proceeds) }) : [`${t("realizedLabel")} `,
        h("span", { class: trend(ratio != null ? ratio : r.profit) },
          money(r.profit, true), ratio != null && ` · ${percent(ratio)}`)],
      r.profit != null && r.unknown > 0 && ` · ${t("pnlScope", { n: r.count - r.unknown, total: r.count })}`,
      h("span", { class: "more" }, ` · ${t("sales")} ›`),
    ), () => { haptic.tap(); showSales(); });
  }

  // item: the held position; qty: how many to start with (all of them by default).
  function showSell(item, qtyStart, backTo) {
    state.screen = "sell";
    state.back = backTo;
    setBack(true);
    secondary.set(null);
    const held = heldItem(item.hash_name) || item;
    const qty = numberInput("numeric", String(Math.min(qtyStart || held.qty, held.qty)), "next");
    const price = numberInput("decimal", held.price != null ? plainAmount(net(held.price)) : "", "done");
    qty.addEventListener("keydown", (e) => { if (e.key === "Enter") price.focus(); });
    price.addEventListener("keydown", (e) => {
      if (e.key !== "Enter") return;
      price.blur();
      main.tap();
    });
    const qtyField = h("label", { class: "field" }, h("span", {}, t("sellQty")), qty);
    const priceField = h("label", { class: "field" }, h("span", {}, t("sellPrice")), price);
    const note = h("p", { class: "note" });
    const summary = h("div", {});

    function values() {
      const q = parseQty(qty.value);
      const p = price.value.trim() === "" ? null : parseAmount(price.value);
      return { q: q != null && q > 0 && q <= held.qty ? q : null, p };
    }

    function refresh() {
      const { q, p } = values();
      qtyField.classList.toggle("invalid", qty.value !== "" && q == null);
      priceField.classList.toggle("invalid", price.value.trim() !== "" && p == null);
      note.textContent = q == null
        ? (held.buy_price == null ? t("hasNoPrice", { qty: held.qty }) : t("has", { qty: held.qty, price: money(held.buy_price) }))
        : q === held.qty ? t("sellCloses") : t("sellLeft", { qty: held.qty - q });
      const rows = [];
      if (q != null && p != null) {
        rows.push(summaryRow(t("received"), money(q * p)));
        if (held.buy_price != null) {
          const r = held.buy_price > 0 ? p / held.buy_price - 1 : null;
          rows.push(summaryRow(t("realizedProfit"), money(q * (p - held.buy_price), true), r != null && [` ${percent(r)}`, trend(r)]));
        }
      }
      summary.replaceChildren(...rows);
      main.set(t("recordSale"), save, { enabled: q != null && p != null, busy: state.busy });
    }
    qty.addEventListener("input", refresh);
    price.addEventListener("input", refresh);

    async function save() {
      const { q, p } = values();
      if (state.busy || q == null || p == null) return;
      state.busy = true;
      refresh();
      try {
        setPortfolio(await api("/api/sales", { method: "POST", body: { hash_name: held.hash_name, qty: q, price: p } }));
        haptic.ok();
        state.busy = false;
        showHome();
      } catch (e) {
        state.busy = false;
        haptic.fail();
        if (app.contains(qty)) refresh();
        alertUser(errorText(e));
      }
    }

    mount([
      h("section", { class: "item-head compact" },
        thumb(held.icon, 128),
        h("div", { class: "item-name" }, held.name),
        h("div", { class: "hint" }, t("sellTitle"))),
      h("div", { class: "form" }, qtyField, priceField),
      h("p", { class: "note" }, t("sellHint")),
      note,
      summary,
    ]);
    refresh();
    setTimeout(() => price.focus(), 50);
  }

  function showSales() {
    state.screen = "sales";
    state.back = () => showHome();
    setBack(true);
    main.set(null);
    secondary.set(null);
    const box = h("div", {}, h("section", { class: "hero" }, h("div", { class: "sk sk-hero" })));
    mount([h("h1", { class: "screen-title" }, t("sales")), box]);
    const render = (sales, realized) => box.replaceChildren(...salesView(sales, realized, render));
    api("/api/sales").then((data) => {
      if (state.screen === "sales" && app.contains(box)) render(data.sales, data.realized);
    }).catch((e) => {
      if (state.screen === "sales" && app.contains(box)) {
        box.replaceChildren(tappable(h("p", { class: "message tappable" }, errorText(e), h("br"), t("retry")), showSales));
      }
    });
  }

  function salesView(sales, r, rerender) {
    if (!sales.length) return [h("p", { class: "message" }, t("noSales"))];
    const ratio = r.profit != null && r.cost > 0 ? r.profit / r.cost : null;
    const undo = async (sale, button) => {
      if (state.busy || !(await confirmUser(t("undoConfirm", { qty: sale.qty, name: sale.name })))) return;
      state.busy = true;
      button.disabled = true;
      button.textContent = "…";
      try {
        const data = await api("/api/sales/delete", { method: "POST", body: { id: sale.id } });
        setPortfolio(data.portfolio);
        haptic.ok();
        if (state.screen === "sales") rerender(data.sales, data.portfolio.realized);
      } catch (e) {
        haptic.fail();
        alertUser(errorText(e));
        button.disabled = false;
        button.textContent = t("undoSale");
      }
      state.busy = false;
    };
    return [
      h("section", { class: "hero" },
        r.profit != null
          ? heroValue(money(r.profit, true), trend(ratio != null ? ratio : r.profit))
          : heroValue(money(r.proceeds)),
        h("div", { class: "hero-sub hint num" }, r.profit != null ? t("realizedProfit") : t("received"),
          ratio != null && ` · ${percent(ratio)}`,
          r.profit != null && r.unknown > 0 && ` · ${t("pnlScope", { n: r.count - r.unknown, total: r.count })}`),
        r.profit != null && h("div", { class: "hero-sub hint num" }, `${t("received")} ${money(r.proceeds)}`)),
      h("ul", { class: "list" }, sales.map((sale) => {
        const gain = sale.buy_price > 0 ? sale.price / sale.buy_price - 1 : null;
        // Undoing puts items back: its own button, not a tap anywhere on the row.
        return h("li", { class: "row plain" },
          thumb(sale.icon),
          h("div", { class: "row-main" },
            h("div", { class: "row-title" }, sale.name),
            h("div", { class: "row-sub hint num" },
              t("saleSub", { qty: sale.qty, price: money(sale.price), date: localDate(sale.sold_at) })),
            h("button", { type: "button", class: "link-btn undo-sale", onclick: (e) => {
              haptic.tap();
              undo(sale, e.currentTarget);
            } }, t("undoSale"))),
          h("div", { class: "row-side num" },
            h("div", { class: "row-value" }, money(sale.qty * sale.price)),
            sale.profit != null && h("div", { class: `row-pnl ${trend(gain != null ? gain : sale.profit)}` },
              money(sale.profit, true))));
      })),
    ];
  }

  // What the daily inventory check found. Nothing changes until the user acts:
  // new items open the import with them ticked, gone ones the sale screen.
  function syncCard(sync) {
    if (!sync) return null;
    const dismiss = async () => {
      haptic.tap();
      try {
        setPortfolio(await api("/api/sync/dismiss", { method: "POST", body: {} }));
      } catch (e) {
        alertUser(errorText(e));
      }
      if (state.screen === "home") showHome({ keepScroll: true });
    };
    const names = (list) => list.slice(0, 3).map((i) => i.name).join(", ") + (list.length > 3 ? ` +${list.length - 3}` : "");
    return h("section", { class: "card sync-card" },
      sync.new.length > 0 && h("div", { class: "sync-part" },
        h("div", { class: "offer-title" }, t("syncNew", { n: sync.new.length })),
        h("p", { class: "hint" }, names(sync.new)),
        h("button", { type: "button", class: "offer-yes", onclick: () => {
          haptic.tap();
          search.query = sync.steamid;
          showSearch();
          loadInventory(sync.steamid, { preselect: sync.new.map((i) => i.hash_name) });
        } }, t("syncReview"))),
      sync.gone.length > 0 && h("div", { class: "sync-part" },
        h("div", { class: "offer-title" }, t("syncGone")),
        h("ul", { class: "top-list" }, sync.gone.map((g) => tappable(h("li", { class: "top-row" },
          thumb(g.icon),
          h("div", { class: "top-name" }, g.name),
          h("div", { class: "top-values num" }, `−${g.qty}`),
        ), () => {
          const held = heldItem(g.hash_name);
          if (!held) return;
          haptic.tap();
          showSell(held, g.qty, () => showHome());
        })))),
      h("button", { type: "button", class: "link-btn sync-dismiss", onclick: dismiss }, t("syncDismiss")));
  }

  // -- folders ------------------------------------------------------------------
  // A folder is a label on a position ("Main", "Alt", "Long-term"). Home and the
  // Portfolio tab show one folder at a time; totals, the chart and the top list
  // follow it. Realized profit and alerts stay whole-portfolio.

  function setFolder(id) {
    state.folder = id;
    state.chartDay = null;
    try { localStorage.setItem("folder", JSON.stringify(id)); } catch (e) { /* per-device nicety only */ }
  }

  function visibleItems(p) {
    return state.folder == null ? p.items : p.items.filter((i) => i.folder === state.folder);
  }

  function folderBar(p) {
    if (!p.folders || !p.folders.length) return null;
    const chip = (id, label) => h("button", {
      type: "button", class: `chip${state.folder === id ? " on" : ""}`, "aria-pressed": String(state.folder === id),
      onclick: () => {
        if (state.folder === id) return;
        setFolder(id);
        state.selected = null;
        haptic.tap();
        showHome();
      },
    }, label);
    return h("nav", { class: "folder-bar", "aria-label": t("folders") },
      chip(null, t("allFolders")),
      p.folders.map((f) => chip(f.id, f.name)),
      h("button", { type: "button", class: "chip ghost", "aria-label": t("folders"), onclick: () => {
        haptic.tap();
        showFolders(() => showHome());
      } }, "⋯"));
  }

  // Under the Portfolio list: folders, export and (for admins) statistics.
  function portfolioLinks(p) {
    const link = (text, onclick) => h("button", { type: "button", class: "link-btn", onclick }, text);
    return h("p", { class: "foot links" },
      link(t("folders"), () => { haptic.tap(); showFolders(() => showHome()); }),
      p.items.length > 0 && link(t("exportCsv"), exportCsv),
      p.is_admin && link(t("stats"), showAdmin));
  }

  function folderOptions(current, { placeholder = null, create = true } = {}) {
    const folders = (state.portfolio && state.portfolio.folders) || [];
    return [
      placeholder && h("option", { value: "", selected: true, disabled: true }, placeholder),
      h("option", { value: "none", selected: !placeholder && current == null }, t("noFolder")),
      ...folders.map((f) => h("option", { value: String(f.id), selected: !placeholder && current === f.id }, f.name)),
      create && h("option", { value: "new" }, t("newFolder")),
    ];
  }

  async function moveTo(names, folder) {
    setPortfolio(await api("/api/holdings/folder", { method: "POST", body: { hash_names: names, folder } }));
  }

  // On an item's screen: which folder it is in; saved as soon as it changes.
  function folderField(item, reopen) {
    const select = h("select", { class: "hour-select", "aria-label": t("folderLabel") }, folderOptions(item.folder));
    select.addEventListener("change", async () => {
      if (select.value === "new") { showFolders(reopen); return; }
      try {
        await moveTo([item.hash_name], select.value === "none" ? null : Number(select.value));
        haptic.ok();
      } catch (e) {
        haptic.fail();
        alertUser(errorText(e));
        if (app.contains(select)) reopen();
      }
    });
    return h("div", { class: "form spaced" }, h("label", { class: "field" }, h("span", {}, t("folderLabel")), select));
  }

  // In selection mode: move what is selected.
  function moveSelect() {
    const select = h("select", { class: "sort-select", "aria-label": t("moveTo") },
      folderOptions(null, { placeholder: t("moveTo") }));
    select.addEventListener("change", async () => {
      const names = [...(state.selected || [])];
      if (select.value === "new") { showFolders(() => showHome()); return; }
      if (!names.length || state.busy) return;
      state.busy = true;
      select.disabled = true;
      try {
        await moveTo(names, select.value === "none" ? null : Number(select.value));
        haptic.ok();
        state.selected = null;
      } catch (e) {
        haptic.fail();
        alertUser(errorText(e));
      }
      state.busy = false;
      if (state.screen === "home") showHome({ keepScroll: true });
    });
    return select;
  }

  function showFolders(backTo) {
    state.screen = "folders";
    state.back = backTo;
    setBack(true);
    secondary.set(null);
    const fresh = h("input", { type: "text", class: "folder-input", maxlength: "32", enterkeyhint: "done",
      autocomplete: "off", placeholder: t("newFolderName"), "aria-label": t("newFolderName") });

    async function send(path, body) {
      if (state.busy) return false;
      state.busy = true;
      try {
        setPortfolio(await api(path, { method: "POST", body }));
        haptic.ok();
        return true;
      } catch (e) {
        haptic.fail();
        alertUser(errorText(e));
        return false;
      } finally {
        state.busy = false;
      }
    }

    function render() {
      const folders = state.portfolio.folders || [];
      const rows = folders.map((f) => {
        const input = h("input", { type: "text", class: "folder-input", maxlength: "32", enterkeyhint: "done",
          autocomplete: "off", "aria-label": t("folderLabel") });
        input.value = f.name;
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") input.blur(); });
        input.addEventListener("change", async () => {
          const name = input.value.trim();
          if (!name || name === f.name) { input.value = f.name; return; }
          if (!(await send("/api/folders", { id: f.id, name }))) input.value = f.name;
        });
        return h("div", { class: "field folder-row" }, input,
          h("span", { class: "hint num folder-count" }, t("folderCount", { n: f.count })),
          h("button", { type: "button", class: "folder-delete", "aria-label": t("deleteFolder"), onclick: async () => {
            if (!(await confirmUser(t("deleteFolderConfirm", { name: f.name })))) return;
            if (state.folder === f.id) setFolder(null);
            if (await send("/api/folders/delete", { id: f.id }) && state.screen === "folders") render();
          } }, "✕"));
      });
      mount([
        h("h1", { class: "screen-title" }, t("folders")),
        h("p", { class: "note" }, t("foldersHint")),
        rows.length > 0 && h("div", { class: "form spaced" }, rows),
        h("div", { class: "form spaced" }, h("div", { class: "field" }, fresh)),
      ]);
      update();
    }

    async function add() {
      const name = fresh.value.trim();
      if (!name) return;
      if (await send("/api/folders", { name })) {
        fresh.value = "";
        if (state.screen === "folders") render();
      }
    }
    function update() {
      main.set(t("addFolder"), add, { enabled: fresh.value.trim() !== "" && (state.portfolio.folders || []).length < 10 });
    }
    fresh.addEventListener("input", update);
    fresh.addEventListener("keydown", (e) => { if (e.key === "Enter") { fresh.blur(); add(); } });
    render();
    if (!(state.portfolio.folders || []).length) setTimeout(() => fresh.focus(), 50);
  }

  // -- export ---------------------------------------------------------------------
  // The bot sends the CSV files to the chat: that works in every Telegram client.

  async function exportCsv(event) {
    if (state.busy) return;
    const link = event && event.currentTarget;
    const label = link && link.textContent;
    haptic.tap();
    let granted = await ensureWriteAccess();
    state.busy = true;
    if (link) {
      link.disabled = true;
      link.textContent = t("exporting");
    }
    try {
      for (let asked = false; ; asked = true) {
        try {
          await api("/api/export", { method: "POST", body: { write_access: granted === true ? true : undefined } });
          break;
        } catch (e) {
          // Declined before: ask once more, now that it is clearly what the file needs.
          if (e.code !== "no_write_access" || asked || !nativeUi || !tg.isVersionAtLeast("6.9")
              || !(await confirmUser(t("askWriteAccess")))) throw e;
          granted = await ensureWriteAccess();
          if (granted !== true) throw e;
        }
      }
      haptic.ok();
      alertUser(t("exported"));
    } catch (e) {
      haptic.fail();
      alertUser(errorText(e));
    }
    state.busy = false;
    if (link && link.isConnected) {
      link.disabled = false;
      link.textContent = label;
    }
  }

  // -- share card -----------------------------------------------------------------
  // Drawn here on a canvas (so it matches the app, with no image library on the
  // server), uploaded as a JPEG, and shared as a message or to a story. Amounts
  // are only on it when the user asks for them.

  function shareButton() {
    const svg = svgEl("svg", { viewBox: "0 0 24 24", "aria-hidden": "true" });
    svgEl("path", { d: "M12 15V4M7.5 8.5 12 4l4.5 4.5M5 13v6h14v-6" }, svg);
    return h("button", { type: "button", class: "bell share", "aria-label": t("share"), onclick: () => {
      haptic.tap();
      showShare();
    } }, svg);
  }

  const canStory = () => nativeUi && tg.isVersionAtLeast("7.8") && ["android", "ios"].includes(tg.platform);

  // Steam's CDN sends no CORS header, and a picture from it would taint the canvas
  // (no JPEG export then), so our server passes the icon on from its own origin.
  // The CSP allows data: but not blob: images, hence the data URL fallback.
  async function loadIcon(icon) {
    if (!icon) return null;
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 4000);
    try {
      const res = await fetch(`/api/icon?name=${encodeURIComponent(icon)}`,
        { headers: { Authorization: `tma ${initData}` }, signal: ctrl.signal });
      if (!res.ok) return null;
      const blob = await res.blob();
      if (window.createImageBitmap) return await createImageBitmap(blob);
      const url = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
      return await new Promise((resolve) => {
        const img = new Image();
        img.onload = () => resolve(img);
        img.onerror = () => resolve(null);
        img.src = url;
      });
    } catch (e) {
      return null;
    } finally {
      clearTimeout(timer);
    }
  }

  // Two formats. Wide (1600×800) for messages: Telegram shows a photo across the
  // bubble, so a landscape card fills it and its text comes out large. Tall
  // (1080×1350) for stories, whose screen is portrait.
  async function drawCard(amounts, wide) {
    const W = wide ? 1600 : 1080, H = wide ? 800 : 1350, X = wide ? 72 : 80;
    const canvas = document.createElement("canvas");
    canvas.width = W;
    canvas.height = H;
    const g = canvas.getContext("2d");
    const font = (weight, size) => `${weight} ${size}px -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif`;
    const colors = { up: "#4cd07d", down: "#ff6b61", hint: "#9aa0aa", fg: "#ffffff" };
    const tone = (ratio) => (ratio > 0.0005 ? colors.up : ratio < -0.0005 ? colors.down : colors.hint);
    const fit = (text, max) => {
      if (g.measureText(text).width <= max) return text;
      let s = text;
      while (s.length > 1 && g.measureText(`${s}…`).width > max) s = s.slice(0, -1);
      return `${s.trimEnd()}…`;
    };
    // The headline shrinks to fit rather than lose digits.
    const fitted = (text, max, weight, size) => {
      let px = size;
      g.font = font(weight, px);
      while (px > 48 && g.measureText(text).width > max) g.font = font(weight, px -= 6);
      return text;
    };
    // Up to two lines; the second ends in "…" when it still doesn't fit.
    const wrap = (text, max) => {
      if (g.measureText(text).width <= max) return [text];
      const words = text.split(" ");
      let first = "";
      while (words.length && g.measureText(first ? `${first} ${words[0]}` : words[0]).width <= max) {
        first = first ? `${first} ${words.shift()}` : words.shift();
      }
      if (!first) return [fit(text, max)];
      return [first, fit(words.join(" "), max)];
    };
    const box = (x, y, w, h, r, fill) => {
      g.fillStyle = fill;
      g.beginPath();
      if (g.roundRect) g.roundRect(x, y, w, h, r); else g.rect(x, y, w, h);
      g.fill();
    };

    const bg = g.createLinearGradient(0, 0, W, H);
    bg.addColorStop(0, "#1a1d26");
    bg.addColorStop(1, "#0b0c10");
    g.fillStyle = bg;
    g.fillRect(0, 0, W, H);
    g.fillStyle = themeColor("button_color", "#2481cc");
    g.fillRect(0, 0, W, 14);

    const p = state.portfolio;
    const items = visibleItems(p);
    const folder = state.folder != null && (p.folders || []).find((f) => f.id === state.folder);
    const { value, trackedValue, pricedCost } = totals(items);
    const pnl = trackedValue - pricedCost;
    const ratio = pricedCost > 0 ? pnl / pricedCost : null;
    const top = [...items].filter((it) => it.price != null)
      .sort((a, b) => net(b.price) * b.qty - net(a.price) * a.qty).slice(0, wide ? 4 : 5);
    const icons = await Promise.all(top.map((it) => loadIcon(it.icon)));

    // The headline block: left column (wide) or top (tall).
    const blockW = wide ? 640 : W - 2 * X;
    const big = wide ? 150 : 128, mid = wide ? 54 : 52, small = wide ? 46 : 44;
    g.textBaseline = "alphabetic";
    g.fillStyle = colors.hint;
    g.font = font(600, wide ? 44 : 40);
    g.fillText(fit(t("cardTitle") + (folder ? ` · ${folder.name}` : ""), blockW), X, wide ? 120 : 130);

    // The value when amounts are on, else the profit in percent.
    let y = wide ? 290 : 290;
    if (amounts) {
      g.fillStyle = colors.fg;
      g.fillText(fitted(money(value), blockW, 800, big), X, y);
      if (ratio != null) {
        y += wide ? 90 : 80;
        g.font = font(600, mid);
        g.fillStyle = tone(ratio);
        g.fillText(fit(`${t("cardProfit")} ${money(pnl, true)}`, blockW), X, y);
        y += wide ? 70 : 64;
        g.fillText(percent(ratio), X, y);
      }
    } else if (ratio != null) {
      g.fillStyle = tone(ratio);
      g.fillText(fitted(percent(ratio), blockW, 800, big), X, y);
      y += wide ? 80 : 70;
      g.font = font(500, small);
      g.fillStyle = colors.hint;
      g.fillText(t("cardProfit"), X, y);
    } else {
      g.fillStyle = colors.fg;
      g.fillText(fitted(t("cardItems", { n: items.length }), blockW, 800, big), X, y);
    }
    const history = state.history;
    if (history && history.key === historyKey() && history.change_ratio != null) {
      y += wide ? 70 : 70;
      g.font = font(500, small);
      g.fillStyle = tone(history.change_ratio);
      g.fillText(`${t(`range_${state.period}`)}: ${percent(history.change_ratio)}`, X, y);
    }

    // The top items by value: name, and the profit on the price paid (or the day's move).
    // Wide: a right column filling the height; tall: rows under the headline.
    const rx = wide ? 760 : X, rw = wide ? W - X - 760 : W - 2 * X;
    const rowTop = wide ? 64 : Math.max(y + 80, 520);
    const ROW = wide ? (H - 2 * 64) / 4 : 120, rowH = ROW - 16;
    const icon = wide ? 116 : 92, nameSize = wide ? 44 : 42, sideSize = wide ? 52 : 44;
    top.forEach((it, i) => {
      const rowY = rowTop + i * ROW;
      box(rx, rowY, rw, rowH, 24, "rgba(255, 255, 255, 0.06)");
      const iy = rowY + (rowH - icon) / 2;
      if (icons[i]) g.drawImage(icons[i], rx + 18, iy, icon, icon);
      else box(rx + 22, iy + 4, icon - 8, icon - 8, 16, "rgba(255, 255, 255, 0.08)");
      const mid = rowY + rowH / 2;
      const gain = it.buy_price > 0 ? net(it.price) / it.buy_price - 1 : it.change_24h;
      const side = gain == null ? "" : percent(gain);
      g.font = font(700, sideSize);
      const sideWidth = side ? g.measureText(side).width : 0;
      const worth = amounts ? money(net(it.price) * it.qty) : "";
      g.font = font(500, sideSize * 0.7);
      const worthWidth = worth ? g.measureText(worth).width : 0;
      const right = rx + rw - 28;
      if (side) {
        g.font = font(700, sideSize);
        g.fillStyle = gain == null ? colors.hint : tone(gain);
        g.fillText(side, right - sideWidth, worth ? mid - 4 : mid + sideSize * 0.36);
      }
      if (worth) {
        g.font = font(500, sideSize * 0.7);
        g.fillStyle = colors.hint;
        g.fillText(worth, right - worthWidth, mid + sideSize * 0.75);
      }
      const nameX = rx + 18 + icon + 22;
      const nameW = right - Math.max(sideWidth, worthWidth, 120) - 24 - nameX;
      g.font = font(500, nameSize);
      g.fillStyle = colors.fg;
      const lines = wrap(it.name, nameW);
      if (lines.length === 1) g.fillText(lines[0], nameX, mid + nameSize * 0.36);
      else {
        g.font = font(500, nameSize * 0.86);
        const lines2 = wrap(it.name, nameW);
        g.fillText(lines2[0], nameX, mid - 6);
        if (lines2[1]) g.fillText(lines2[1], nameX, mid + nameSize * 0.86);
      }
    });

    g.font = font(600, wide ? 42 : 38);
    g.fillStyle = colors.fg;
    g.fillText(fit(t("cardFooter"), blockW), X, H - (wide ? 110 : 96));
    g.font = font(500, wide ? 42 : 38);
    g.fillStyle = themeColor("button_color", "#2481cc");
    g.fillText(fit(p.bot ? `t.me/${p.bot}` : location.host, blockW), X, H - (wide ? 56 : 48));
    return canvas;
  }

  // The server takes up to 256 KB per request.
  function jpeg(canvas, limit = 240 * 1024) {
    const at = (quality) => new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
    return (async () => {
      for (const quality of [0.88, 0.75, 0.6, 0.45]) {
        const blob = await at(quality);
        if (blob && blob.size <= limit) return blob;
      }
      return null;
    })();
  }

  // Telegram for iOS shows the sender their shared photo from its thumbnail,
  // and keeps whatever part of it had arrived when the message went out: a
  // small one has arrived long before. 800 px wide still looks sharp in a bubble.
  function thumbnail(canvas) {
    const small = document.createElement("canvas");
    small.width = 800;
    small.height = Math.round(canvas.height * 800 / canvas.width);
    const g = small.getContext("2d");
    g.imageSmoothingQuality = "high";
    g.drawImage(canvas, 0, 0, small.width, small.height);
    return jpeg(small, 48 * 1024);
  }

  function showShare() {
    state.screen = "share";
    state.back = () => showHome();
    setBack(true);
    let amounts = false;
    let canvas = null;
    let drawing = 0;
    const preview = h("img", { class: "share-preview", alt: t("shareTitle") });
    const box = h("div", { class: "share-box" }, preview);

    async function redraw() {
      const run = ++drawing;
      const next = await drawCard(amounts, true);
      if (run !== drawing || !app.contains(preview)) return;
      canvas = next;
      preview.src = canvas.toDataURL("image/jpeg", 0.8);
      buttons();
    }

    function buttons() {
      const ready = !!canvas && !state.busy;
      main.set(t("shareChat"), () => send(nativeUi && tg.isVersionAtLeast("8.0") ? "message" : "chat"),
        { enabled: ready, busy: state.busy });
      secondary.set(canStory() ? t("shareStory") : null, () => send("story"), { enabled: ready });
    }

    async function send(mode) {
      if (state.busy || !canvas) return;
      state.busy = true;
      buttons();
      try {
        let raw;
        if (mode === "message") {
          // Photo and thumbnail in one request, together under the 256 KB limit.
          const [photo, thumb] = await Promise.all([jpeg(canvas, 196 * 1024), thumbnail(canvas)]);
          if (!photo || !thumb) throw Object.assign(new Error("too big"), { code: "generic" });
          raw = new FormData();
          raw.append("photo", photo, "card.jpg");
          raw.append("thumb", thumb, "thumb.jpg");
        } else {
          raw = await jpeg(mode === "story" ? await drawCard(amounts, false) : canvas);
          if (!raw) throw Object.assign(new Error("too big"), { code: "generic" });
        }
        const data = await api(`/api/share?mode=${mode}`, { method: "POST", raw });
        haptic.ok();
        if (data.prepared) call(() => tg.shareMessage(data.prepared), "8.0");
        else if (data.url) call(() => tg.shareToStory(data.url, { text: data.text.slice(0, 200) }), "7.8");
        else if (data.sent) alertUser(t("shareSent"));
      } catch (e) {
        haptic.fail();
        alertUser(errorText(e));
      }
      state.busy = false;
      if (app.contains(preview)) buttons();
    }

    mount([
      h("h1", { class: "screen-title" }, t("shareTitle")),
      segmentedControl([[false, t("hideAmounts")], [true, t("showAmounts")]], amounts, (v) => { amounts = v; redraw(); },
        t("shareTitle")),
      h("p", { class: "note" }, t("shareHint")),
      box,
      state.portfolio.inline && state.portfolio.bot && h("p", { class: "note spaced" }, t("inlineHint", { bot: state.portfolio.bot })),
    ]);
    buttons();
    redraw();
  }

  // -- admin: broadcast -----------------------------------------------------------

  function broadcastSection() {
    const box = h("div", {});
    let text = "";
    const load = () => api("/api/admin/broadcast").then((d) => {
      if (state.screen === "admin" && box.isConnected) render(d);
    }).catch(() => {});

    function render(d) {
      const last = d.last;
      const running = !!last && !last.finished_at;
      const done = last ? last.sent + last.failed : 0;
      const input = h("textarea", { class: "broadcast-text", rows: "5", maxlength: "4000",
        placeholder: t("broadcastPlaceholder"), "aria-label": t("broadcast"), disabled: running });
      input.value = text;
      const send = h("button", { type: "button", class: "offer-yes", disabled: running || !text.trim() || !d.audience,
        onclick: async () => {
          if (!text.trim() || !(await confirmUser(t("broadcastConfirm", { n: d.audience })))) return;
          try {
            render(await api("/api/admin/broadcast", { method: "POST", body: { text } }));
            text = "";
            haptic.ok();
          } catch (e) {
            haptic.fail();
            alertUser(errorText(e));
          }
        } }, t("broadcastSend", { n: d.audience }));
      input.addEventListener("input", () => {
        text = input.value;
        send.disabled = running || !text.trim() || !d.audience;
      });
      const status = last && (running ? t("broadcastProgress", { done, total: last.total, failed: last.failed })
        : last.cancelled ? t("broadcastCancelled", { done, total: last.total })
        : t("broadcastDone", { sent: last.sent, failed: last.failed, ago: ago(last.finished_at) }));
      box.replaceChildren(
        h("div", { class: "section-title hint" }, t("broadcast")),
        h("div", { class: "card broadcast" },
          input,
          h("p", { class: "note" }, t("broadcastAudience", { n: d.audience })),
          h("div", { class: "offer-actions" }, send,
            running && h("button", { type: "button", class: "link-btn", onclick: async () => {
              try { render(await api("/api/admin/broadcast/cancel", { method: "POST", body: {} })); } catch (e) { alertUser(errorText(e)); }
            } }, t("broadcastCancel"))),
          status && h("p", { class: "note num" }, status)));
      if (running) setTimeout(load, 3000);
    }
    load();
    return box;
  }

  // -- screen: admin statistics ---------------------------------------------
  // Only reachable for ids in CS2BOT_ADMINS; the server checks it again.

  function showAdmin() {
    state.screen = "admin";
    state.back = () => showHome();
    setBack(true);
    main.set(null);
    secondary.set(null);
    const box = h("div", {}, h("section", { class: "hero" }, h("div", { class: "sk sk-hero" })));
    mount([h("h1", { class: "screen-title" }, t("stats")), box]);
    api("/api/admin/stats").then((data) => {
      if (state.screen === "admin" && app.contains(box)) box.replaceChildren(...adminView(data), broadcastSection());
    }).catch((e) => {
      if (state.screen === "admin" && app.contains(box)) box.replaceChildren(h("p", { class: "message" }, errorText(e)));
    });
  }

  const compact = (n) => numberFormat({ notation: "compact", maximumFractionDigits: 1 }).format(n || 0);
  const utcDay = (sec) => new Intl.DateTimeFormat(locale, { day: "numeric", month: "short", timeZone: "UTC" })
    .format(new Date(sec * 1000));

  function adminView(d) {
    const u = d.users;
    const tile = (value, label) => h("div", { class: "tile" },
      h("div", { class: "tile-value" }, compact(value)), h("div", { class: "tile-label hint" }, label));
    const section = (title, ...body) => [h("div", { class: "section-title hint" }, title), ...body];
    const row = (label, value) => h("li", { class: "row plain" },
      h("div", { class: "row-main" }, label), h("div", { class: "row-side num" }, value));

    const actions = ["open", "search", "inventory", "import", "add", "edit", "remove", "sell", "watch", "alert", "digest",
      "export", "share", "inline", "inline_sent", "bot"]
      .filter((k) => d.actions_7d[k]);
    const p = d.prices;

    return [
      h("div", { class: "tiles" },
        tile(u.total, t("st_users")), tile(u.active_7d, t("st_active7")),
        tile(u.new_7d, t("st_new7")), tile(u.with_portfolio, t("st_withPortfolio"))),
      h("p", { class: "note" }, t("st_more", { d1: u.active_1d, d30: u.active_30d, bot: u.bot_only })),
      ...section(t("st_chart"), activityChart(d.daily)),
      ...section(t("st_actions"), actions.length
        ? h("ul", { class: "list" }, actions.map((k) => row(t(`act_${k}`), compact(d.actions_7d[k]))))
        : h("p", { class: "message" }, t("st_none"))),
      ...section(t("st_top"), d.top_items.length
        ? h("ul", { class: "list" }, d.top_items.map((it) => h("li", { class: "row plain" },
          h("div", { class: "row-main" }, h("div", { class: "row-title" }, it.name),
            h("div", { class: "row-sub hint num" }, t("st_holders", { n: it.holders, qty: it.qty }))))))
        : h("p", { class: "message" }, t("st_none"))),
      ...section(t("st_recent"), d.recent_users.length
        ? h("ul", { class: "list" }, d.recent_users.map((r) => h("li", { class: "row plain" },
          h("div", { class: "row-main" },
            h("div", { class: "row-title" }, [r.first_name, r.username && `@${r.username}`].filter(Boolean).join(" ") || `id ${r.id}`),
            h("div", { class: "row-sub hint num" }, t("st_userSub", { items: r.items, ago: ago(r.last_seen) }))),
          h("div", { class: "row-side hint num small" }, String(r.id)))))
        : h("p", { class: "message" }, t("st_none"))),
      ...section(t("st_prices"), h("ul", { class: "list" },
        row(t("st_tracked"), compact(p.tracked)),
        row(t("st_pending"), compact(p.pending)),
        row(t("st_lastCheck"), p.last_check ? ago(p.last_check) : "—"),
        row(t("st_oldest"), p.oldest_price ? ago(p.oldest_price) : "—"))),
    ];
  }

  // One series (active users per day): no legend, the section title names it.
  // Columns grow from one baseline with 4px rounded tops; tap or hover shows the day.
  function activityChart(days) {
    const max = Math.max(1, ...days.map((x) => x.active));
    const caption = h("div", { class: "chart-caption hint num" });
    const describe = (x) => t("st_dayTip", { day: utcDay(x.day), active: x.active, new: x.new });
    const last = days[days.length - 1];
    caption.textContent = describe(last);
    let selected = null;
    const cols = days.map((x) => {
      const bar = h("span", { class: "chart-bar" });
      bar.style.setProperty("--h", `${(x.active / max) * 100}%`);
      const col = h("button", { type: "button", class: "chart-col", "aria-label": describe(x) }, bar);
      const pick = () => {
        if (selected) selected.classList.remove("on");
        selected = col;
        col.classList.add("on");
        caption.textContent = describe(x);
      };
      col.addEventListener("click", () => { haptic.tap(); pick(); });
      col.addEventListener("mouseenter", pick);
      if (x === last) { selected = col; col.classList.add("on"); }
      return col;
    });
    return h("div", { class: "chart card" },
      caption,
      h("div", { class: "chart-plot" },
        h("div", { class: "chart-max hint num" }, compact(max)),
        h("div", { class: "chart-cols" }, cols)),
      h("div", { class: "chart-axis hint num" }, h("span", {}, utcDay(days[0].day)), h("span", {}, utcDay(last.day))));
  }

  // -- screen: item ------------------------------------------------------------

  // mode "edit": change or remove a position; mode "add": record a purchase,
  // averaged into the position if the item is already held.
  function showItem(item, mode, backTo, { quiet = false } = {}) {
    state.screen = "item";
    state.back = backTo || (() => showHome());
    setBack(true);
    const editing = mode === "edit";
    const held = heldItem(item.hash_name);
    let price = held ? held.price : undefined; // undefined: still loading
    let liquidity = held ? held.liquidity : null;
    let priceError = null;

    const now = h("div", { class: "item-now hint num" });
    const picture = thumb(item.icon, 256);
    const insights = h("div", { class: "item-insights num" });
    const marketDepth = h("div", { class: "market-depth num" });
    const steamLink = h("a", {
      href: MARKET_URL + encodeURIComponent(item.hash_name), target: "_blank", rel: "noopener",
      onclick: (e) => { if (initData) { e.preventDefault(); tg.openLink(e.currentTarget.href); } },
    }, item.name, h("span", { class: "external", "aria-hidden": "true" }, " ↗"));

    const qty = numberInput("numeric", editing ? String(item.qty) : "1", "next");
    const buy = numberInput("decimal", editing && item.buy_price != null ? plainAmount(item.buy_price) : "", "done");
    buy.placeholder = t("notSet");
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
    }

    function renderInsights() {
      const changes = [];
      for (const [key, value] of [["changeDay", held?.change_24h], ["changeWeek", held?.change_7d]]) {
        if (value != null) changes.push(h("span", { class: trend(value) }, `${t(key)} ${percent(value)}`));
      }
      insights.replaceChildren(...changes);
      const depth = [];
      if (liquidity) {
        const count = (n) => numberFormat({ maximumFractionDigits: 0 }).format(n);
        if (liquidity.sell_listings != null) depth.push(t("sellListings", { n: count(liquidity.sell_listings) }));
        if (liquidity.buy_orders != null) depth.push(t("buyOrders", { n: count(liquidity.buy_orders) }));
        // A gap, not a move: no sign.
        if (liquidity.spread != null) {
          depth.push(t("spread", { pct: numberFormat({ style: "percent", maximumFractionDigits: 1 }).format(liquidity.spread) }));
        }
      }
      marketDepth.textContent = depth.join(" · ");
      marketDepth.hidden = !depth.length;
    }

    function refresh() {
      const q = parseQty(qty.value);
      // An empty price paid is allowed and means "unknown".
      const bEmpty = buy.value.trim() === "";
      const b = bEmpty ? null : parseAmount(buy.value);
      const bValid = bEmpty || b != null;
      const removing = editing && q === 0;
      qtyField.classList.toggle("invalid", qty.value !== "" && (q == null || (!editing && q === 0)));
      buyField.classList.toggle("invalid", !bValid);

      // Note: what adding does to the position, or how to remove.
      let noteText = "";
      let perItem = b; // the price paid each that the break-even covers
      if (!editing && held && q) {
        // A lot without a price paid leaves a known average as it is (so does the server).
        perItem = held.buy_price == null ? null
          : b == null ? held.buy_price : (held.qty * held.buy_price + q * b) / (held.qty + q);
      }
      if (editing) noteText = "";
      else if (held && q && bValid) {
        const total = held.qty + q;
        if (held.buy_price == null) noteText = t("becomesNoPrice", { qty: total });
        else if (b == null) noteText = t("becomesKeep", { qty: total, price: money(held.buy_price) });
        else {
          const avg = Math.round(((held.qty * held.buy_price + q * b) / total) * 100) / 100;
          noteText = t("becomes", { qty: total, price: money(avg) });
        }
      } else if (held) {
        noteText = held.buy_price == null ? t("hasNoPrice", { qty: held.qty })
          : t("has", { qty: held.qty, price: money(held.buy_price) });
      }
      note.textContent = noteText;
      note.hidden = !noteText;

      const rows = [];
      if (q && bValid && !removing) {
        const cost = b == null ? null : q * b;
        if (cost != null && (editing || q > 1)) rows.push(summaryRow(t(editing ? "invested" : "total"), money(cost)));
        if (price != null) {
          const worth = q * net(price);
          const r = cost > 0 ? worth / cost - 1 : null;
          rows.push(summaryRow(t("worth"), money(worth), r != null && [` ${percent(r)}`, trend(r)]));
        }
        if (perItem > 0) {
          // The lowest listing price whose payout (after the fee) covers the price paid.
          const breakEvenCents = Math.ceil(Math.round(perItem * 100) * (100 + STEAM_FEE_PERCENT) / 100);
          rows.push(summaryRow(t("breakEven"), money(breakEvenCents / 100)));
        }
      }
      summary.replaceChildren(...rows);

      if (removing) {
        main.set(t("remove"), remove, { busy: state.busy, danger: true });
      } else {
        const valid = q != null && q > 0 && bValid;
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

    // Not while buying more of a held item: that screen is about the purchase.
    const alerts = (editing || !held) && itemNotifications(item,
      () => showItem(heldItem(item.hash_name) || item, editing ? "edit" : "add", backTo, { quiet: true }));
    mount([
      h("section", { class: "item-head" },
        picture,
        h("div", { class: "item-name" }, steamLink),
        now,
        insights,
        alerts && alerts.actions,
      ),
      h("div", { class: "form" }, qtyField, buyField),
      note,
      summary,
      editing && held && folderField(held, () => showItem(heldItem(item.hash_name) || item, "edit", backTo, { quiet: true })),
      marketDepth,
      alerts && alerts.list,
      secondary.inline,
      editing && h("button", { class: "danger-link", type: "button", onclick: () => remove() }, t("removeFull")),
    ]);
    renderNow();
    renderInsights();
    refresh();
    // The purchase price is the one number only the user knows: start there.
    if (!editing && !quiet) setTimeout(() => buy.focus(), 50);

    if (price === undefined) {
      api(`/api/quote?hash_name=${encodeURIComponent(item.hash_name)}`).then((data) => {
        price = data.price;
        liquidity = data.liquidity;
        // Opened from a link: only the hash name was known.
        if (!item.icon && data.icon && app.contains(picture)) picture.replaceWith(thumb(data.icon, 256));
        if (data.name && data.name !== item.name && steamLink.firstChild) steamLink.firstChild.textContent = data.name;
      }).catch((e) => {
        price = null;
        priceError = e;
      }).finally(() => {
        if (!app.contains(now)) return; // user moved on
        renderNow();
        renderInsights();
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
      submit({ path: "/api/holdings", json: { hash_name: item.hash_name, qty: q, buy_price: b, mode: editing ? "set" : "add",
        folder: editing ? undefined : state.folder } });
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
  // Home keeps itself current: prices change in the background on the server.
  // The request only reads the database, so polling is cheap.
  setInterval(() => {
    if (state.screen === "home" && document.visibilityState === "visible") loadPortfolio();
  }, 30000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible" && state.screen === "home") loadPortfolio();
  });

  showHome();
  tg.ready();
  tg.expand();
  loadPortfolio();
})();
