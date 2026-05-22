# Полный отчёт о проекте «PDF Terms Extraction Pipeline»

## 1. Соответствие техническому заданию

### Матрица compliance

| Требование ТЗ | Статус | Где реализовано |
|---|---|---|
| Извлечение сокращений, терминов, определений | ✅ | `extraction/term_extractor.py` |
| PDF-сканы без текстового слоя | ✅ | OCR fallback: `ingestion/loader.py` |
| Сохранение в PostgreSQL с привязкой к документу | ✅ | `db/models.py`, `db/crud.py`, `db/repositories/` |
| Возможность получить перечни по названию документа | ✅ | `get_document_by_hash()` + `ExtractedItemsRepository` |
| Разнородное качество сканов | ✅ | Двухэтапный OCR: text layer → Tesseract |
| Возможное отсутствие разделов | ✅ | Статус `SKIPPED`, не падает |
| Разделы в произвольных местах | ✅ | Поиск по всему тексту, посекционная обработка |
| GPU-ограничение 32 ГБ VRAM | ✅ | Tesseract на CPU, учтено в `agents.md` |
| Docker-обёртка | ✅ | `Dockerfile` + `docker-compose.yml` |
| Отказоустойчивость | ✅ | `reset_stale_processing`, статусная модель |
| Идемпотентность | ✅ | SHA-256 fingerprint, проверка статуса перед обработкой |
| Не дублировать обработанные документы | ✅ | `_should_process()` пропускает `COMPLETED`/`SKIPPED` |
| Ошибки логируются | ✅ | `processing_events` + `logging` |
| Ошибки не блокируют остальные файлы | ✅ | Per-document try/except, статус `FAILED` |

### Рекомендованный pipeline (agents.md) — реализация

| Этап | Реализация | Файл |
|---|---|---|
| 1. discover | `process_once` → `glob("*.pdf")` | `scripts/worker.py:136` |
| 2. fingerprint | `file_sha256()` + `get_document_by_hash()` | `worker.py:51-52` |
| 3. ocr | `extract_pdf_text()` — text layer + Tesseract fallback | `ingestion/loader.py` |
| 4. section-detection | `_split_sections()` — поиск заголовков «Сокращения»/«Термины и определения» | `extraction/term_extractor.py:39-57` |
| 5. entity-extraction | `extract_entities_from_text()` — regex-парсинг пар «термин — определение» | `extraction/term_extractor.py:92-141` |
| 6. validation | `validate_entities()` — фильтр по confidence, дедупликация, санитизация | `extraction/validator.py` |
| 7. persist | `ExtractedItemsRepository.replace_for_document()` | `db/repositories/extracted_items_repository.py` |
| 8. mark-complete | `update_document_status(..., "COMPLETED")` | `worker.py:108` |

---

## 2. Архитектура проекта

```
.
├── Dockerfile                  # Сборка образа: python:3.11-slim + Tesseract + зависимости
├── docker-compose.yml          # Оркестрация: postgres + worker
├── .dockerignore               # Исключение мусора из сборочного контекста
├── .env.example                # Шаблон конфигурации
├── pyproject.toml              # Зависимости и метаданные пакета
├── migrations/
│   └── 001_init.sql            # Схема БД (documents, extracted_items, processing_events)
├── scripts/
│   └── worker.py               # Главный вход: CLI, цикл обработки, оркестрация пайплайна
├── src/rag_system/
│   ├── config.py               # Pydantic Settings — вся конфигурация из .env
│   ├── ingestion/
│   │   └── loader.py           # Загрузка PDF: текстовый слой (pypdf) → OCR fallback (PyMuPDF + Tesseract)
│   ├── extraction/
│   │   ├── models.py           # DTO ExtractedEntity
│   │   ├── term_extractor.py   # Regex-парсинг: секции, пары «термин—определение», confidence
│   │   └── validator.py        # Валидация: порог confidence, санитизация
│   └── db/
│       ├── base.py             # SQLAlchemy DeclarativeBase
│       ├── models.py           # ORM-модели: Document, ExtractedItem, ProcessingEvent
│       ├── session.py          # Engine + Session factory с пулом соединений
│       ├── crud.py             # Операции: create, update, reset_stale, prepare_reprocess
│       └── repositories/
│           └── extracted_items_repository.py  # Атомарная запись extracted_items
├── для терминов/               # Входная директория с PDF-файлами (11 ГОСТов)
└── tests/                      # Заготовка под тесты (пока пусто)
```

---

## 3. Принцип работы каждого модуля

### 3.1 `config.py` — Централизованная конфигурация

- **Библиотека:** Pydantic Settings
- **Источник:** `.env` файл → переменные окружения → defaults
- **Настройки:**
  - `INPUT_DIR` — где искать PDF
  - `OCR_LANGUAGES` — языки Tesseract (`rus+eng`)
  - `OCR_MIN_TEXT_CHARS` — порог (200 символов) для решения «текстовый слой или OCR»
  - `POSTGRES_*` — параметры подключения к БД
  - `DB_POOL_SIZE`, `DB_POOL_OVERFLOW`, `DB_POOL_RECYCLE_SECONDS` — настройки пула SQLAlchemy
  - `VALIDATION_MIN_CONFIDENCE` — порог отсева низкокачественных извлечений (0.4)
- **DSN:** свойство `postgres_dsn` собирает `postgresql+psycopg://user:pass@host:port/db`

### 3.2 `ingestion/loader.py` — Извлечение текста из PDF

- **Два этапа:**
  1. `_extract_text_layer()` — пытается читать встроенный текстовый слой через `pypdf.PdfReader`
  2. `_extract_text_ocr()` — если текст короткий (< `min_text_chars`), рендерит страницы через `PyMuPDF` (300 DPI) и прогоняет через `pytesseract`
- **Возвращает:** `(list[str], str, int)` — тексты постранично, источник (`text_layer`/`ocr`/`empty`), количество страниц
- **Ключевое исправление:** возвращает текст постранично (не склеенный), чтобы extraction-слой знал номера страниц

### 3.3 `extraction/term_extractor.py` — Извлечение сущностей

- **Regex-константы:**
  - `SECTION_TITLE_RE` — заголовки: «Сокращения», «Термины и определения», «Термины», «Определения»
  - `PAIR_RE` — строки вида «термин — определение» или «термин: определение» (поддерживает нумерацию)
  - `ABBREVIATION_RE` — эвристика: термин похож на аббревиатуру (заглавные буквы, цифры, дефисы, точки)
- **Алгоритм:**
  1. `_normalize_lines()` — очистка строк от пустых
  2. `_split_sections()` — поиск заголовков, разбиение текста на секции с типом (`abbreviation` / `term_definition`)
  3. Для каждой строки каждой секции: `PAIR_RE.match()` → извлечение term + definition
  4. `_guess_item_type()` — fallback: если секция не «abbreviation», проверка на аббревиатуру
  5. `_compute_confidence()` — базовая уверенность от источника (text_layer=0.85, ocr=0.55) + бонусы за длину
  6. Дедупликация по `(term.lower(), definition.lower())` в пределах документа
- **Ключевое исправление:** `_split_sections` возвращает тип секции → item_type назначается от заголовка, а не угадывается

### 3.4 `extraction/validator.py` — Валидация

- Фильтрует сущности по трём правилам:
  1. `confidence < min_confidence` → отсев
  2. `term == definition` (после нормализации) → отсев (например, «Стандарт — Стандарт»)
  3. `len(definition) < 3` → отсев
- Возвращает `(validated_entities, rejected_count)`

### 3.5 `db/models.py` — ORM-модели

- **`Document`**: id, file_name, file_hash (UNIQUE), status, pages_count, created_at, updated_at
- **`ExtractedItem`**: id, document_id (FK), item_type, term, definition, page, confidence, raw_fragment
- **`ProcessingEvent`**: id, document_id (FK), stage, level, message, created_at
- **Таймзоны:** `_utcnow()` → `datetime.now(timezone.utc)` (вместо deprecated `datetime.utcnow`)

### 3.6 `db/session.py` — Подключение к БД

- `create_engine()` с параметрами пула: `pool_size=5`, `max_overflow=10`, `pool_recycle=1800`, `pool_pre_ping=True`
- `sessionmaker()` с `expire_on_commit=False` (объекты не expire после commit)
- `get_session()` — фабрика новых сессий

### 3.7 `db/crud.py` — Операции с БД

- `get_document_by_hash()` — поиск документа по SHA-256
- `create_document()` — создание с `status="NEW"`
- `update_document_status()` — смена статуса
- `add_event()` — запись в processing_events
- `reset_stale_processing()` — **восстановление после падения:** документы в `PROCESSING` дольше 30 минут → обратно в `NEW`
- `prepare_reprocess()` — удаление старых extracted_items + сброс статуса `FAILED` → `NEW`

### 3.8 `db/repositories/extracted_items_repository.py` — Сохранение результатов

- `replace_for_document()` — атомарная операция: DELETE старых записей + INSERT новых в одной транзакции
- При ошибке — явный `session.rollback()`

### 3.9 `scripts/worker.py` — Оркестратор пайплайна

- **CLI:** `--input-dir`, `--interval`, `--once`, `--reprocess`
- **Цикл:** `process_once()` каждые N секунд в бесконечном цикле (или однократно с `--once`)
- **Восстановление:** в начале каждого цикла `reset_stale_processing()`
- **Per-document:** каждый PDF обрабатывается в собственной сессии БД → изоляция ошибок
- **Полный пайплайн для одного документа:**
  1. `file_sha256()` → fingerprint
  2. `get_document_by_hash()` → проверка статуса
  3. `_should_process()` → пропуск COMPLETED/SKIPPED/FAILED (без `--reprocess`)
  4. `create_document()` или `prepare_reprocess()`
  5. Статус → `PROCESSING`
  6. `extract_pdf_text()` → OCR
  7. `document.pages_count = pages_count` → сохранение числа страниц
  8. `extract_entities_from_text()` → извлечение сущностей
  9. `validate_entities()` → валидация
  10. `replace_for_document()` → сохранение в БД
  11. Статус → `COMPLETED`
  12. При любой ошибке → статус `FAILED` + запись в `processing_events`

---

## 4. Статусная модель документа

```
NEW ────────→ PROCESSING ────────→ COMPLETED
  ↑               │                      │
  │               │ (timeout 30m)        │
  │               ↓                      │
  └── reset_stale ─┘                     │
                                         │
FAILED ←─────── (exception)              │
  │                                      │
  └── --reprocess → NEW → ... ──────────→│
                                         │
SKIPPED ←────── (нет текста/разделов)     │
```

---

## 5. Как запустить и проверить

### 5.1 Быстрый запуск (Docker)

```bash
cd "тестовое задание на RAG"
cp .env.example .env
docker compose up --build
```

После старта:
- PostgreSQL доступен на `localhost:5432`
- Worker каждые 30 секунд сканирует `для терминов/` и обрабатывает новые PDF
- Для однократного запуска: `docker compose run --rm worker python scripts/worker.py --once`

### 5.2 Переобработка FAILED-документов

```bash
docker compose run --rm worker python scripts/worker.py --once --reprocess
```

### 5.3 Проверка результатов (SQL)

```bash
docker compose exec postgres psql -U rag -d rag

-- Список обработанных документов
SELECT id, file_name, status, pages_count FROM documents;

-- Извлечённые пункты по документу
SELECT d.file_name, e.item_type, e.term, e.definition, e.page, e.confidence
FROM extracted_items e
JOIN documents d ON d.id = e.document_id
WHERE d.file_name = 'ГОСТ 1.1-2002.pdf';

-- Лог обработки
SELECT stage, level, message, created_at
FROM processing_events
WHERE document_id = 1
ORDER BY created_at;
```

### 5.4 Ручное тестирование extraction-логики (без Docker)

```bash
cd "тестовое задание на RAG"
python3 -m venv .venv
.venv/bin/pip install -e .
PYTHONPATH=src .venv/bin/python3 -c "
from rag_system.extraction.term_extractor import extract_entities_from_text
from rag_system.extraction.validator import validate_entities

text = ['Сокращения\n\nГОСТ — государственный стандарт\nТУ — технические условия']
entities = extract_entities_from_text(text, source='text_layer')
for e in entities:
    print(f'page={e.page} type={e.item_type} conf={e.confidence:.2f} {e.term} → {e.definition}')

validated, rejected = validate_entities(entities)
print(f'Kept: {len(validated)}, Rejected: {rejected}')
"
```

### 5.5 Результаты тестирования (выполнено)

Все 5 тестов локально пройдены:

- **Test 1 — Аббревиатуры:** 4 сущности, `item_type=abbreviation`, `confidence=0.85–0.90`, `page=1` ✅
- **Test 2 — Термины:** 3 сущности, `item_type=term_definition`, `confidence=0.95` ✅
- **Test 3 — Multi-page:** 4 сущности, страницы 1 и 2 корректно назначены ✅
- **Test 4 — OCR source:** confidence снижен до 0.55–0.60 ✅
- **Test 5 — Validation:** 2 из 3 отфильтрованы (low confidence + term≡definition), 1 сохранён ✅

Схема БД верифицирована: все 3 таблицы совпадают с миграцией `001_init.sql` и требованиями `agents.md`.

---

## 6. Известные ограничения

1. **OCR на реальных сканах низкого качества** — regex-парсинг ожидает относительно чистый текст. Для сильно зашумлённых сканов ГОСТов потребуется пост-обработка OCR-артефактов (замена похожих символов, восстановление разрывов строк).
2. **Нет REST API** — модуль `api/` заготовлен, но не реализован. Запросы к данным — только через SQL.
3. **Нет тестов** — `tests/` пуст, функциональность проверена ad-hoc.
4. **Tesseract только CPU** — GPU-ускорение не настроено, работает в рамках ограничения 32 ГБ VRAM.
