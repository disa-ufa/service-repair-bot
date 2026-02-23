# Деплой на Timeweb Cloud (VPS/VDS) — Docker Compose

Ниже — практичный сценарий: сервер Ubuntu + Docker Compose + polling (без домена и webhook).

## 1) На сервере: базовая подготовка

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install git ufw
sudo ufw allow OpenSSH
sudo ufw enable
```

## 2) Установка Docker

```bash
sudo apt -y install docker.io docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker
```

## 3) Клонирование проекта

```bash
git clone <URL_ВАШЕГО_REPO> bot
cd bot
```

## 4) Конфиг и папка данных

Создайте `config.yml` на основе `config.example.yml`:

```bash
cp config.example.yml config.yml
nano config.yml
```

Рекомендуется:
- `db_url: sqlite+aiosqlite:///./data/app.db`
- `bot_token` — токен BotFather
- `admins` — список TG ID админов (super_admin)

Создайте папку данных (SQLite и возможные файлы в будущем):

```bash
mkdir -p data
```

## 5) Запуск

```bash
docker compose up -d --build
docker compose logs -f
```

Проверка:
```bash
docker compose ps
```

## 6) Обновление

```bash
git pull
docker compose up -d --build
docker compose logs -f
```

## 7) Бэкап SQLite (минимум)

SQLite — это файл. Делайте резервные копии `data/app.db`.

Простой ручной бэкап:
```bash
cp data/app.db data/app.db.backup_$(date +%F_%H%M)
```

Рекомендуется дополнительно включить **бэкапы/снапшоты** в панели Timeweb Cloud.
