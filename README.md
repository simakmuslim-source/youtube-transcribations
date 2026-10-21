# youtube-transcribations

Пакетная дословная транскрибация YouTube-плейлистов (в том числе больших — 50–500+ видео) в отдельные текстовые файлы.

## Возможности

- Обрабатывает видео в порядке плейлиста.
- Сначала пытается получить доступные субтитры YouTube; при отсутствии субтитров может распознать аудио через Whisper.
- Сохраняет текст без редакторской правки. Если источник — субтитры, сохраняется их формулировка после технического удаления таймкодов и разметки. Если используется ASR, точность зависит от модели и качества звука.
- Возобновляет обработку: существующие транскрипции пропускаются.
- Сохраняет по одному `.txt` на видео, метаданные и отчёт `report.csv`.
- Собирает результат в ZIP-архив.
- Не публикует транскрипции в GitHub автоматически. В GitHub Actions результаты доступны как временный артефакт.

## Требования

- Python 3.10+
- FFmpeg (нужен для Whisper fallback)
- Доступ к YouTube из сети, где запускается программа

## Установка

### Windows

Установи Python с https://www.python.org/downloads/ и FFmpeg с https://ffmpeg.org/download.html. Затем в PowerShell из папки проекта:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

FFmpeg можно установить через пакетный менеджер системы (например, `brew install ffmpeg` на macOS или `sudo apt install ffmpeg` на Ubuntu/Debian).

## Запуск для нужного плейлиста

```bash
python main.py --playlist "https://youtube.com/playlist?list=PLsb6UHPckO4tUqnS53ef529-7neQ6btEz"
```

Субтитры — более быстрый вариант по умолчанию. Для обязательного распознавания аудио Whisper:

```bash
python main.py --playlist "URL_ПЛЕЙЛИСТА" --mode whisper
```

Чтобы использовать только уже опубликованные субтитры и не скачивать аудио:

```bash
python main.py --playlist "URL_ПЛЕЙЛИСТА" --mode captions
```

Если нужно ограничить количество роликов для теста:

```bash
python main.py --playlist "URL_ПЛЕЙЛИСТА" --limit 3
```

## Где результаты

По умолчанию программа создаёт каталог `output/`:

- `output/transcripts/` — отдельные текстовые файлы;
- `output/report.csv` — статус, название, URL и ошибка для каждого видео;
- `output/metadata.json` — метаданные плейлиста и видео;
- `output/youtube-transcripts.zip` — архив транскрипций.

Повторный запуск пропускает уже созданные непустые TXT-файлы. Чтобы пересоздать результаты, удали соответствующие файлы из `output/transcripts/` или используй `--overwrite`.

## Запуск через GitHub Actions (без установки на компьютер)

В репозитории добавлен workflow `.github/workflows/transcribe.yml`. После добавления workflow он запускается автоматически; последующие запуски можно делать вручную:

1. Открой вкладку [Actions](https://github.com/simakmuslim-source/youtube-transcribations/actions).
2. Выбери **Transcribe YouTube playlist**.
3. Нажми **Run workflow**, укажи лимит (например, `500`) и режим `auto`.
4. Дождись завершения запуска. Открой конкретный запуск и скачай артефакт **youtube-transcripts** в разделе Artifacts.
5. Распакуй скачанный артефакт: внутри будет `youtube-transcripts.zip` и отчёты. В самом ZIP находятся тексты и метаданные.

Режим `auto` сначала использует субтитры, а если их нет — пытается распознать аудио Whisper. Запуск может занять много времени; GitHub Actions ограничивает длительность одного задания. Артефакт хранится 14 дней. Если запуск завершился ошибкой, открой его и проверь `report.csv`.

### Если YouTube пишет «Sign in to confirm you’re not a bot»

GitHub Actions может быть заблокирован YouTube, даже если плейлист публичный. Скрипт поддерживает cookies через секрет репозитория:

1. В браузере на компьютере, где вы вошли в YouTube, экспортируйте cookies именно в формате Netscape (`cookies.txt`) с помощью доверенного локального инструмента.
2. Закодируйте файл в Base64 локально, не отправляя его в чат и не добавляя в репозиторий. Например, в Linux/macOS: `base64 -w 0 cookies.txt` (на macOS без `-w 0`).
3. В GitHub откройте **Settings → Secrets and variables → Actions → New repository secret**.
4. Имя секрета: `YOUTUBE_COOKIES_B64`. В значение вставьте Base64-строку.
5. Запустите workflow повторно через **Actions → Transcribe YouTube playlist → Run workflow**.

Cookies дают доступ к вашей сессии YouTube: никогда не публикуйте их, не помещайте в код и удалите/обновите их, если они случайно раскрылись. Даже с cookies YouTube может блокировать запросы; тогда потребуется запуск с компьютера/сети, где YouTube доступен.

## Важные замечания

1. Если GitHub Actions недоступен или запуск остановлен лимитом времени, запусти программу локально на компьютере или сервере с доступом к YouTube.
2. У YouTube могут быть ограничения, блокировки, CAPTCHA и региональные ограничения. Скрипт записывает ошибки в отчёт и продолжает обработку следующих видео.
3. Whisper fallback требует загрузки аудио и может занимать значительное время и место на диске. По умолчанию используется модель `small`; можно выбрать другую через `--whisper-model`.
4. Автоматическое распознавание не гарантирует абсолютную дословность. Для юридически значимого использования сверяй текст с оригинальной записью.
5. Соблюдай авторские права, условия платформы и права на распространение транскрипций. В публичный репозиторий добавлен только код, не содержимое видео.

## Командная строка

```
python main.py --playlist URL
  --mode auto|captions|whisper
  --output output
  --limit N
  --overwrite
  --whisper-model small
  --language ru
```

Лицензия: MIT.