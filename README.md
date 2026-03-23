# KSB Music Mini App

Отдельный новый проект под Telegram Mini App. Текущий рабочий бот `music_src` этот проект не затрагивает.

## Что уже умеет MVP

- поиск треков в Яндекс.Музыке и YouTube
- поиск текстов песен через Яндекс.Музыку и fallback на Genius
- синхронизация `Мне понравилось` из Яндекс.Музыки в отдельную SQLite-библиотеку Mini App
- сохранение найденных треков в локальную библиотеку Mini App
- Telegram WebApp bootstrap без привязки к старому боту

## Запуск локально

```powershell
cd C:\WER\music_mini_app
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m app.main
```

Откройте `http://127.0.0.1:8000`.

## Переменные окружения

- `BOT_TOKEN` - токен Telegram-бота, нужен для валидации `initData`
- `YANDEX_MUSIC_TOKEN` - токен Яндекс.Музыки
- `DATA_DIR` - папка для SQLite и runtime-данных
- `DATABASE_PATH` - явный путь к базе, если нужен
- `SEARCH_RESULTS_PER_SOURCE` - сколько результатов брать с каждого источника
- `LYRICS_RESULTS_LIMIT` - сколько кандидатов проверять при поиске текста

## Railway

1. Создайте новый отдельный репозиторий из `C:\WER\music_mini_app`
2. Подключите его в Railway как новый проект
3. Добавьте `BOT_TOKEN`, `YANDEX_MUSIC_TOKEN`, `DATA_DIR=/data`, `DATABASE_PATH=/data/mini_app.db`
4. Подключите volume в `/data`

## Следующий этап

Следующим слоем можно добавить:

- полноценную авторизацию пользователя через Telegram init data
- скачивание треков прямо из Mini App
- очереди фоновой синхронизации
- отображение прогресса по лайкам и поиску
