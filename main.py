import logging
import traceback

from selenium.common.exceptions import InvalidSessionIdException
from urllib3.exceptions import MaxRetryError, ProtocolError

from browser import create_driver
from config import KEEP_BROWSER_OPEN_ON_ERROR, USE_DATABASE
from db import get_tracking_numbers
from logging_setup import dump_debug, setup_logging
from pochta import LetterProcessingError, PochtaClient, close_extra_tabs

log = logging.getLogger(__name__)


def _driver_dead(exc: BaseException) -> bool:
    if isinstance(exc, (InvalidSessionIdException, MaxRetryError, ProtocolError)):
        return True
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "10061",
            "10054",
            "max retries",
            "invalid session",
            "connection refused",
            "подключение не установлено",
        )
    )


def main() -> int:
    setup_logging()
    log.info("Старт. USE_DATABASE=%s KEEP_BROWSER_OPEN_ON_ERROR=%s", USE_DATABASE, KEEP_BROWSER_OPEN_ON_ERROR)

    try:
        numbers = get_tracking_numbers()
    except Exception:
        log.exception("Не удалось получить список трек-номеров")
        return 1

    log.info("К обработке %s номеров: %s", len(numbers), numbers)

    driver = None
    fatal = False
    results: list[dict] = []
    try:
        driver = create_driver()
        client = PochtaClient(driver)
        try:
            client.login()
        except Exception:
            log.exception("Авторизация в otpravka.pochta.ru не удалась — дальше смысла нет")
            if driver:
                dump_debug(driver, "fatal_login")
            fatal = True
            return 1

        for index, code in enumerate(numbers, start=1):
            log.info("---- %s/%s ----", index, len(numbers))
            try:
                results.append({"ok": True, **client.process_tracking_number(code)})
            except LetterProcessingError as exc:
                log.error("Номер %s не обработан: %s", code, exc)
                dump_debug(driver, f"fail_{code}")
                results.append({"ok": False, "code": code, "error": str(exc)})
                if _driver_dead(exc):
                    log.error("Сессия Chrome умерла — остальные номера пропускаем")
                    fatal = True
                    break
                try:
                    if client.main_handle:
                        close_extra_tabs(driver, client.main_handle)
                except Exception:
                    log.debug("Не удалось вернуться на основную вкладку после ошибки")
            except Exception as exc:
                log.error("Неожиданная ошибка по номеру %s:\n%s", code, traceback.format_exc())
                dump_debug(driver, f"crash_{code}")
                results.append({"ok": False, "code": code, "error": "unexpected"})
                if _driver_dead(exc):
                    log.error("Сессия Chrome умерла — остальные номера пропускаем")
                    fatal = True
                    break
    except Exception:
        log.exception("Фатальная ошибка скрипта")
        fatal = True
        if driver:
            dump_debug(driver, "fatal")
    finally:
        ok = [r for r in results if r.get("ok")]
        fail = [r for r in results if not r.get("ok")]
        log.info("Итог: успешно %s, ошибок %s, всего %s", len(ok), len(fail), len(results))
        for item in ok:
            log.info("  OK %s письмо=%s отчёт=%s", item.get("code"), item.get("letter_pdf"), item.get("report_pdf"))
        for item in fail:
            log.info("  FAIL %s: %s", item.get("code"), item.get("error"))

        if driver:
            should_keep = KEEP_BROWSER_OPEN_ON_ERROR and (fatal or fail)
            if should_keep:
                log.warning(
                    "Браузер оставлен открытым (KEEP_BROWSER_OPEN_ON_ERROR=true). "
                    "Нажмите Enter в консоли, чтобы закрыть его."
                )
                try:
                    input()
                except EOFError:
                    pass
            log.info("Закрываем браузер")
            try:
                driver.quit()
            except Exception as exc:
                log.debug("driver.quit: %s", exc)

    return 0 if not fatal and not fail else 1


if __name__ == "__main__":
    raise SystemExit(main())
