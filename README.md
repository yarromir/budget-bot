# Finance Bot

Отдельный Telegram-бот для личного финансового учета на Python, aiogram 3.x и SQLite.

## Что умеет

- записывает доходы и расходы в SQLite;
- ведет подписки и присылает напоминания об оплате;
- хранит месячные лимиты по категориям и предупреждает при 80% и 100%;
- строит HTML-отчеты за день, неделю и месяц;
- принимает простые русские фразы без строгих команд.

## Структура

```text
finance_bot/
  bot.py
  db.py
  parser.py
  reminders.py
  report.py
  requirements.txt
```

## Настройка

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r finance_bot/requirements.txt
```

Обязательные переменные:

```bash
export TELEGRAM_BOT_TOKEN="telegram_bot_token"
export ALLOWED_USER_IDS="123456789"
```

Опциональные переменные:

```bash
export FINANCE_DB_PATH="$HOME/finance_bot/finance.db"
export FINANCE_REPORT_DIR="$HOME/finance_bot/reports"
export FINANCE_BOT_TIMEZONE="Asia/Novosibirsk"
```

Если `ALLOWED_USER_IDS` пустой, бот никого не пустит.

## Запуск

```bash
python -m finance_bot.bot
```

## Фразы

```text
потратил 450 на такси
заплатил 450 за такси
пришла зп 100000
заработал 100000
добавь подписку YouTube 299 дата 12.03.2026
лимит на такси 5000
отчет за день
отчет за неделю
отчет за месяц
/balance
баланс
/last
последние операции
/paid 3
оплатил подписку YouTube
```

Если сумма не распознана, бот попросит уточнить. Если категория не указана, используется `прочее`.

Команда `/balance` или фраза `баланс` показывает доходы, расходы и остаток за всё время. Команда `/last` или фраза `последние операции` выводит последние 10 операций прямо в чате.

## База данных

По умолчанию база создается здесь:

```text
~/finance_bot/finance.db
```

Таблицы:

- `transactions`
- `subscriptions`
- `budgets`

Все изменения пишутся в SQLite реально, без имитации.

## Отчеты

HTML-отчет содержит:

- баланс за период;
- доходы, расходы и остаток;
- таблицу операций;
- активные подписки;
- круговую диаграмму расходов по категориям;
- график расходов по дням.

Отчет автономный: CSS и изображения встроены в HTML, внешних ресурсов нет.
