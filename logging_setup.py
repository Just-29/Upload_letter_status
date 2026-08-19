import logging
from datetime import datetime
from pathlib import Path

from selenium.webdriver.remote.webdriver import WebDriver

from config import DEBUG_DIR, LOG_DIR


def setup_logging() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)

    log_file = LOG_DIR / f"run_{datetime.now():%Y%m%d_%H%M%S}.log"
    fmt = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
    datefmt = "%H:%M:%S"

    root = logging.getLogger()
    root.setLevel(logging.DEBUG)
    root.handlers.clear()

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(fmt, datefmt))

    console = logging.StreamHandler()
    console.setLevel(logging.INFO)
    console.setFormatter(logging.Formatter(fmt, datefmt))

    root.addHandler(file_handler)
    root.addHandler(console)

    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("WDM").setLevel(logging.WARNING)

    logging.getLogger(__name__).info("Лог файл: %s", log_file)
    return log_file


def log_browser_state(driver: WebDriver, prefix: str = "") -> None:
    log = logging.getLogger(__name__)
    try:
        handles = driver.window_handles
        current = driver.current_window_handle
        log.debug(
            "%sокон=%s текущая=%s url=%s title=%s",
            f"{prefix} " if prefix else "",
            len(handles),
            current[-6:],
            driver.current_url,
            driver.title,
        )
        for i, handle in enumerate(handles):
            marker = "*" if handle == current else " "
            try:
                driver.switch_to.window(handle)
                log.debug(
                    "  %s[%s] handle=...%s url=%s title=%s",
                    marker,
                    i,
                    handle[-6:],
                    driver.current_url,
                    driver.title,
                )
            except Exception as exc:
                log.debug("  %s[%s] не удалось прочитать вкладку: %s", marker, i, exc)
        driver.switch_to.window(current)
    except Exception as exc:
        log.debug("Не удалось снять состояние браузера: %s", exc)


def dump_debug(driver: WebDriver, name: str) -> Path | None:
    """Скриншот + HTML текущей вкладки — чтобы по ошибке сразу видеть, где скрипт остановился."""
    log = logging.getLogger(__name__)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%H%M%S")
    safe = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:80]
    png = DEBUG_DIR / f"{stamp}_{safe}.png"
    html = DEBUG_DIR / f"{stamp}_{safe}.html"
    try:
        driver.save_screenshot(str(png))
        html.write_text(driver.page_source or "", encoding="utf-8")
        log.warning(
            "Диагностика %s: screenshot=%s html=%s url=%s title=%s",
            name,
            png.name,
            html.name,
            getattr(driver, "current_url", "?"),
            getattr(driver, "title", "?"),
        )
        log_browser_state(driver, prefix=f"dump:{name}")
        return png
    except Exception as exc:
        log.warning("Не удалось сохранить диагностику %s: %s", name, exc)
        return None
