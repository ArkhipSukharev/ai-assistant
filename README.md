# F5 Assistant

AI-ассистент для amoCRM с двумя интерфейсами: **Telegram-бот** и **веб-чат**. Система использует [F5AI API](https://f5ai.ru/docs/quickstart) с function calling для получения данных из amoCRM и формирования отчётов на естественном языке.

## Возможности

- Отчёты по сделкам, воронке и менеджерам
- Запросы лидов, контактов и задач
- Telegram-бот и веб-интерфейс с общей логикой агента
- Админ-панель: аккаунты, роли, Telegram ID и дневные лимиты
- Авторизация через серверные HttpOnly cookie и Argon2-хеши паролей
- Журнал административных действий
- Экспорт CSV для отчётов
- Redis-сессии, rate limiting, OAuth refresh для amoCRM
- Проверка агрегатов отчёта перед ответом
- Ссылки на исходные сделки и отметка времени получения данных amoCRM
- Ограничение повторных tool-call и размера контекста модели
- Сохранённые шаблоны и расписание отчётов по московскому времени
- Автоматическая отправка отчётов и файлов в Telegram
- Экспорт отчётов в XLSX и PDF
- Сравнение периодов и показатели план/факт
- Webhooks amoCRM для оперативного повторного анализа изменённых сделок
- Анализ звонков, SMS, сообщений чатов и текстовых примечаний
- Автоматический поиск забытых сделок с оценкой риска и рекомендациями

## Архитектура

```
Telegram Bot ──┐
               ├──> FastAPI Agent ──> F5AI (gpt-4.1-mini)
Web Chat UI ───┘              └──> amoCRM API v4
```

## Быстрый старт

### 1. Настройка окружения

```bash
cp backend/.env.example backend/.env
```

Заполните переменные:

| Переменная | Описание |
|------------|----------|
| `F5AI_API_KEY` | Ключ из [app.f5ai.ru/keys](https://app.f5ai.ru/keys/) |
| `AMOCRM_DOMAIN` | Домен аккаунта, например `company.amocrm.ru` |
| `AMOCRM_ACCESS_TOKEN` | Долгоживущий токен или access token |
| `TELEGRAM_BOT_TOKEN` | Токен от @BotFather |
| `ADMIN_EMAIL` | Email первого администратора |
| `ADMIN_PASSWORD` | Пароль первого администратора (минимум 10 символов) |
| `DATABASE_URL` | SQLite локально, PostgreSQL в production |

### 2. Локальный запуск (без Docker)

```bash
cd backend
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

```bash
cd frontend/web-chat
npm install
npm run dev
```

Откройте http://localhost:5173

### 3. Запуск через Docker Compose

```bash
cp .env.example .env
# Задайте уникальный POSTGRES_PASSWORD в .env
docker compose up --build -d
```

Compose запускает PostgreSQL, Redis с AOF, backend, production-сборку frontend и
ежедневное резервное копирование PostgreSQL. Миграции Alembic применяются перед
стартом backend. Все сервисы имеют healthcheck и автоматически перезапускаются.

- Backend: http://localhost:8000
- Frontend: http://localhost:5173
- Health: http://localhost:8000/health
- Readiness: http://localhost:8000/ready

Проверка состояния:

```bash
docker compose ps
docker compose logs --tail=100 backend
```

Перенос существующих данных SQLite выполняется один раз после запуска PostgreSQL:

```bash
cd backend
python scripts/migrate_sqlite_to_postgres.py \
  --postgres-url "postgresql+asyncpg://f5_assistant:PASSWORD@localhost:5432/f5_assistant"
```

Передача `--force` разрешает заменить уже существующие прикладные данные.
Резервные копии хранятся семь дней в Docker volume `postgres_backups`.

Новые миграции схемы создаются командой:

```bash
cd backend
alembic revision --autogenerate -m "description"
alembic upgrade head
```

## API

### POST /api/chat

```json
{
  "session_id": "optional-uuid",
  "message": "Сделай отчёт по сделкам за март"
}
```

Ответ:

```json
{
  "session_id": "uuid",
  "reply": "Текст ответа",
  "attachments": []
}
```

Веб-API требует вход через `/api/auth/login`. Сессионный токен хранится только
в защищённой HttpOnly cookie.

### GET /api/balance

Баланс F5AI аккаунта.

### DELETE /api/chat/{session_id}

Очистка истории диалога.

## Telegram

Команды:

- `/start` — приветствие
- `/help` — справка
- `/clear` — сброс контекста

Доступ управляется в админ-панели: создайте аккаунт и укажите его Telegram ID.
При `TELEGRAM_REQUIRE_ACCOUNT=true` бот отвечает только активным аккаунтам.

## Админ-панель

После первого запуска откройте http://localhost:5173 и войдите с
`ADMIN_EMAIL` / `ADMIN_PASSWORD` из `.env`.

Администратор может:

- создавать и редактировать аккаунты;
- назначать роли `admin` и `user`;
- привязывать Telegram ID;
- настраивать HTTP/SOCKS5-прокси Telegram и проверять соединение;
- блокировать доступ;
- устанавливать дневной лимит запросов;
- выбирать общую модель F5AI для сайта и Telegram-бота;
- просматривать журнал действий.

Самостоятельная регистрация отключена.

## amoCRM

MVP использует долгоживущий токен. Для production настройте OAuth refresh:

```
AMOCRM_CLIENT_ID=...
AMOCRM_CLIENT_SECRET=...
AMOCRM_REFRESH_TOKEN=...
AMOCRM_REDIRECT_URI=...
```

При 401 клиент автоматически обновит access token.

### Webhooks и рекомендации

Для регистрации webhook сервер должен быть доступен amoCRM по публичному HTTPS:

```env
PUBLIC_BASE_URL=https://assistant.example.com
AMOCRM_WEBHOOK_SECRET=long-random-secret
FORGOTTEN_DEAL_DAYS=7
INSIGHT_SCAN_INTERVAL_MINUTES=60
INSIGHT_SCAN_MAX_LEADS=100
```

После запуска откройте в админ-панели раздел **amoCRM** и нажмите
«Подключить». Без публичного адреса доступна ручная проверка сделок; после
регистрации изменения сделок, задач, примечаний и сообщений обрабатываются
оперативно. Аудиозапись звонка не расшифровывается: анализируются доступные
amoCRM метаданные, длительность, результат и ссылка на запись.

## Модель F5AI

- Основная: `gpt-4.1-mini` — function calling, отчёты
- Сложные запросы (>500 символов): `gpt-4o`

## Тесты

```bash
cd backend
pip install -r requirements-dev.txt
pytest
```

## Примеры запросов

- «Отчёт по сделкам за март 2026»
- «Сколько новых лидов за неделю?»
- «Конверсия по воронке Продажи»
- «Топ-3 менеджера по сумме сделок»
- «Покажи задачи на сегодня»

## Структура проекта

```
backend/
  app/
    api/          # REST endpoints
    bot/          # Telegram bot
    services/     # F5AI, amoCRM, agent, reports
    tools/        # Function calling schemas
frontend/web-chat/  # React UI
docker-compose.yml
```
