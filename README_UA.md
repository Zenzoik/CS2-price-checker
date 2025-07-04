# Трекер цін кейсів CS2

<p align="center">
  <a href="README.md">English</a> •
  <strong>Українська</strong>
</p>

Python скрипт для автоматичного відстеження цін кейсів CS2 (Counter-Strike 2) з торгової площадки Steam та оновлення їх у Google Таблицях.

## Можливості

- 🔍 **Пошук та додавання кейсів**: Знаходьте кейси за назвою та додавайте їх до списку відстеження
- 📊 **Автоматичне оновлення цін**: Оновлює поточні ринкові ціни кожні 5 хвилин
- 📈 **Розрахунок прибутку**: Розраховує потенційний прибуток на основі цін купівлі/продажу
- 🛡️ **Захист від лімітів**: Вбудований захист від обмежень Steam API
- 🌍 **Універсальна сумісність**: Працює з Google Таблицями на будь-якій мові/локалі

## Вимоги

- Python 3.7+
- Акаунт Google
- Акаунт Google Cloud Platform (безкоштовного тарифу достатньо)

## Встановлення

1. **Клонуйте репозиторій**
   ```bash
   git clone https://github.com/Zenzoik/CS2-price-checker.git
   cd CS2-price-checker
   ```

2. **Встановіть необхідні пакети**
   ```bash
   pip install -r requirements.txt
   ```

## Інструкція з налаштування

### Крок 1: Скопіюйте шаблон Google Таблиць

1. Відкрийте [шаблон таблиці](https://docs.google.com/spreadsheets/d/1eShxZQ34gI8dir-6LISCNX-omjF8A2XQJb9vL1jh_bs/edit?usp=sharing)
2. Натисніть **Файл → Створити копію**
3. Перейменуйте таблицю на "Template_CS_2_cases"

### Крок 2: Створіть сервісний акаунт Google Cloud

1. Перейдіть до [Google Cloud Console](https://console.cloud.google.com/)
2. Створіть новий проект або виберіть існуючий
3. Увімкніть **Google Sheets API** та **Google Drive API**:
   - Перейдіть до **APIs & Services → Library**
   - Знайдіть "Google Sheets API" та увімкніть його
   - Знайдіть "Google Drive API" та увімкніть його

### Крок 3: Створіть облікові дані сервісного акаунта

1. Перейдіть до **APIs & Services → Credentials**
2. Натисніть **Create Credentials → Service Account**
3. Заповніть дані сервісного акаунта:
   - **Назва**: `cs2-tracker-bot`
   - **Опис**: `Сервісний акаунт для відстеження цін кейсів CS2`
4. Натисніть **Create and Continue**
5. Пропустіть призначення ролей (натисніть **Continue**)
6. Натисніть **Done**

### Крок 4: Створіть та завантажте JSON ключ

1. Знайдіть ваш новий сервісний акаунт у списку
2. Натисніть на email сервісного акаунта
3. Перейдіть на вкладку **Keys**
4. Натисніть **Add Key → Create new key**
5. Виберіть формат **JSON**
6. Натисніть **Create**
7. JSON файл завантажиться автоматично
8. **Перейменуйте файл на `service-account.json`**
9. **Перемістіть його в ту ж папку, що й скрипт `main.py`**

### Крок 5: Надайте доступ до таблиці

1. Відкрийте вашу скопійовану Google Таблицю
2. Натисніть кнопку **Надати доступ**
3. Скопіюйте email сервісного акаунта з JSON файлу або Google Cloud (формат: `name@project-id.iam.gserviceaccount.com`)
4. Вставте email у діалог надання доступу
5. Встановіть права **Редактор**
6. **Зніміть галочку "Повідомити користувачів"**
7. Натисніть **Поділитися**

## Використання

### Запуск скрипта

```bash
python main.py
```

### Опції меню

**Опція 1: Додати нові кейси для відстеження**
- Вводьте назви кейсів (наприклад, "breakout case", "chroma case")
- Скрипт знайде точний збіг у Steam Market
- Підтвердіть кожен кейс перед додаванням до списку відстеження

**Опція 2: Запустити відстеження цін**
- Автоматично оновлює поточні ринкові ціни кожні 5 хвилин
- Ціни оновлюються в колонці C (Поточна ціна)
- Використовуйте `Ctrl+C` для зупинки відстеження та повернення в меню

**Опція 3: Вихід**
- Безпечний вихід з програми

### Ручне введення даних

Після додавання кейсів та запуску відстеження цін, вручну заповніть:
- **Колонка B (Ціна купівлі)**: Ваша ціна купівлі за кейс
- **Колонка E (Кількість)**: Кількість кейсів, які у вас є

Таблиця автоматично розрахує:
- Загальні інвестиції
- Поточну загальну вартість
- Суми прибутку/збитку та відсотки

## Структура таблиці

| Колонка | Опис |
|---------|------|
| A | Hash name (заповнюється автоматично) |
| B | Ціна купівлі (ручне введення) |
| C | Поточна ціна (оновлюється автоматично) |
| D | Зміна ціни % |
| E | Кількість (ручне введення) |
| F | ЗАГАЛЬНА КУПІВЛЯ |
| G | ПОТОЧНА ЗАГАЛЬНА |
| H | Прибуток/Збиток |
| I | Результат % |

## Усунення неполадок

### Часті проблеми

**Помилка "No module named 'gspread'"**
```bash
pip install gspread oauth2client requests
```

**Помилка "service-account.json not found"**
- Переконайтеся, що JSON файл знаходиться в тій же директорії, що й `main.py`
- Перевірте, що ім'я файлу точно `service-account.json`

**Помилка "Permission denied"**
- Переконайтеся, що ви поділилися таблицею з email сервісного акаунта
- Перевірте, що у сервісного акаунта є права Редактора

**Повідомлення "Rate limited"**
- Це нормально - скрипт автоматично обробляє обмеження Steam API
- Час очікування буде показано в консолі

**Помилка "Could not find item_nameid"**
- Назва кейса може бути неправильною
- Спробуйте пошукати з точною назвою зі Steam Market

## Правові аспекти

Цей інструмент призначений лише для освітніх та особистих цілей. Будь ласка, дотримуйтеся Умов обслуговування Steam та обмежень швидкості запитів. Автори не несуть відповідальності за будь-які обмеження акаунта або інші наслідки використання цього інструменту.

## Ліцензія

Цей проект ліцензований під ліцензією MIT - дивіться файл [LICENSE](LICENSE) для деталей.

---

⭐ **Поставте зірку цьому репозиторію, якщо він вам допоміг!**
