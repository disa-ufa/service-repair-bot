# Telegram-бот (aiogram 3) — текущая версия

Сейчас реализовано:
- Конфиг из `config.yml` (YAML) через `bot/config.py`
- SQLite база через SQLAlchemy (async) и сидирование тестовых услуг (offers)
- Регистрация мастера (/start → ФИО → процент 40/50/60)
- Роль `super_admin` для TG ID из `admins` в конфиге
- Меню (кнопки) и раздел «Мастера» с выбором услуг/навыков
- Заявки (MVP): создание, просмотр списка, «Доступные заказы» для мастеров, принятие заявки
- Статусы заявки (мастер): Принята → В работе → Модернизация → Закрыта (с расчетом «к сдаче»)
- «Требуется сдача»: мастер отправляет скрин перевода, админ подтверждает оплату (статус «Оплачено»)
- Статистика (кнопки): для админа (общая), для мастера (личная)

## Быстрый старт (Windows)

1) Установите Python 3.12+ (рекомендую 3.12).

2) В корне проекта создайте виртуальное окружение:
```powershell
cd C:\root
python -m venv .venv
```

3) Активируйте venv и поставьте зависимости:
```powershell
.\.venv\Scripts\activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

4) Создайте `config.yml` на основе примера:
```powershell
copy config.example.yml config.yml
```
Откройте `config.yml` и укажите:
- `bot_token` — токен от BotFather
- `admins` — ваш Telegram ID (можно узнать через /start или /stats)

5) Запуск:
```powershell
python main.py
```

## Важно про перенос на другой компьютер

Папку `.venv` **не переносим**. На новом ПК:
- удалите `.venv`
- создайте её заново (`python -m venv .venv`)
- переустановите зависимости

Иначе будет ошибка вида: `No Python at 'C:\Program Files\Python312\python.exe'`.

## Частые ошибки

### 1) `TelegramConflictError: terminated by other getUpdates request`
Запущены **2 копии бота**. Остановите предыдущий запуск (закройте консоль/процесс) и запустите снова.

### 2) `ModuleNotFoundError: No module named 'yaml'`
Вы запустили не из venv. Активируйте `.venv` и ставьте зависимости через `python -m pip ...`.

### 3) Ошибки схемы SQLite (`no such column ...`)
Это значит, что база старая и не соответствует текущим моделям. Проще всего переименовать/удалить `app.db` и запустить заново.
