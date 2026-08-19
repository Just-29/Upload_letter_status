import logging

from config import (
    POSTGRES_CONNECT_TIMEOUT,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    STUB_TRACKING_NUMBERS,
    TRACKING_NUMBERS_SQL,
    USE_DATABASE,
)

log = logging.getLogger(__name__)


def _normalize_number(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def fetch_from_database() -> list[str]:
    if not TRACKING_NUMBERS_SQL:
        raise ValueError(
            "USE_DATABASE=true, но TRACKING_NUMBERS_SQL пустой. "
            "Пропишите запрос в .env (берётся первая колонка)."
        )

    try:
        import psycopg2
    except ImportError as exc:
        raise RuntimeError(
            "Для работы с базой установите psycopg2-binary: pip install psycopg2-binary"
        ) from exc

    log.info(
        "Подключение к PostgreSQL %s:%s/%s пользователь=%s",
        POSTGRES_HOST,
        POSTGRES_PORT,
        POSTGRES_DB,
        POSTGRES_USER,
    )
    log.info("SQL:\n%s", TRACKING_NUMBERS_SQL)

    conn = psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
        connect_timeout=POSTGRES_CONNECT_TIMEOUT,
    )
    try:
        with conn.cursor() as cur:
            cur.execute(TRACKING_NUMBERS_SQL)
            rows = cur.fetchall()
            colnames = [desc[0] for desc in cur.description] if cur.description else []
            log.debug("Колонки результата: %s, строк: %s", colnames, len(rows))
    finally:
        conn.close()

    numbers: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if not row:
            continue
        number = _normalize_number(row[0])
        if not number:
            continue
        if number in seen:
            log.debug("Пропуск дубля из БД: %s", number)
            continue
        seen.add(number)
        numbers.append(number)

    log.info("Из базы получено %s трек-номеров: %s", len(numbers), numbers)
    return numbers


def get_tracking_numbers() -> list[str]:
    """Список трек-номеров: из БД или из заглушки STUB_TRACKING_NUMBERS."""
    if USE_DATABASE:
        numbers = fetch_from_database()
        if not numbers:
            raise RuntimeError("Запрос к базе вернул 0 трек-номеров. Проверьте TRACKING_NUMBERS_SQL.")
        return numbers

    log.warning(
        "USE_DATABASE=false — работаем на заглушке STUB_TRACKING_NUMBERS (%s шт.): %s",
        len(STUB_TRACKING_NUMBERS),
        STUB_TRACKING_NUMBERS,
    )
    return list(STUB_TRACKING_NUMBERS)
