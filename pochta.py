from __future__ import annotations

import base64
import logging
import shutil
import time
from pathlib import Path

import requests
from selenium.common.exceptions import (
    ElementClickInterceptedException,
    InvalidSessionIdException,
    StaleElementReferenceException,
    TimeoutException,
    WebDriverException,
)
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.remote.webelement import WebElement
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from config import (
    DOWNLOAD_DIR,
    DOWNLOAD_TIMEOUT,
    EMAIL,
    LOGIN_URL,
    PASSWORD,
    PRINT_METHOD,
    REPORTS_DIR,
    SHORT_WAIT,
    WAIT_TIMEOUT,
)
from logging_setup import dump_debug, log_browser_state

log = logging.getLogger(__name__)

XPATH_USERNAME = '//input[@id="username"]'
XPATH_PASSWORD = '//input[@id="userpassword"]'
XPATH_NEXT = '//button[contains(., "Далее")]'
XPATH_LOGIN = '//button[contains(., "Войти")]'
XPATH_SEARCH = '//input[@id="search-field"]'
# На дашборде «Новые» тоже есть mailing-group__header (1133 ЭЗП) — это НЕ поиск
XPATH_SEARCH_GROUP = '//div[contains(@class, "search-group")]'
XPATH_SEARCH_GROUP_HEADER = '//div[contains(@class, "search-group")]//div[contains(@class, "mailing-group__header")]'
XPATH_SEARCH_STATUS = '//div[contains(@class, "search-status") and contains(., "Найдено")]'
XPATH_PRINT = '//button[@id="tracking-card-footer__print-button"]'
# Предупреждение на трекинге: «Войдите, чтобы сохранять трек-номера...»
XPATH_LOGIN_WARNING_LINK = (
    '//a[contains(@href, "/api/auth/login") and contains(normalize-space(.), "Войдите")]'
)
XPATH_LOGIN_WARNING_TEXT = '//p[contains(., "чтобы сохранять трек-номера")]'
XPATH_SKIP = '//button[contains(normalize-space(.), "Пропустить")]'
XPATH_POPUP_CLOSE = (
    '//button[contains(@class, "popup__close") and @title="Закрыть"]'
    ' | //button[contains(@class, "mailing-form__close-button")]'
)


class LetterProcessingError(Exception):
    """Ошибка по одному трек-номеру: цикл должен идти дальше."""


def _wait(driver: WebDriver, timeout: int | None = None) -> WebDriverWait:
    return WebDriverWait(driver, timeout or WAIT_TIMEOUT)


def snapshot_files(directory: Path) -> set[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    return {p.resolve() for p in directory.iterdir() if p.is_file()}


def wait_for_new_file(
    directory: Path,
    before: set[Path],
    timeout: int = DOWNLOAD_TIMEOUT,
    suffix: str = ".pdf",
) -> Path:
    log.debug(
        "Ждём новый %s в %s (уже было %s файлов, timeout=%ss)",
        suffix,
        directory,
        len(before),
        timeout,
    )
    deadline = time.time() + timeout
    while time.time() < deadline:
        current = snapshot_files(directory)
        newcomers = []
        for path in current - before:
            name = path.name.lower()
            if name.endswith(".crdownload") or name.endswith(".tmp") or name.endswith(".download"):
                log.debug("Ещё качается: %s", path.name)
                continue
            if suffix and path.suffix.lower() != suffix.lower():
                continue
            newcomers.append(path)

        for path in newcomers:
            try:
                size1 = path.stat().st_size
            except OSError:
                continue
            if size1 <= 0:
                continue
            time.sleep(0.4)
            try:
                size2 = path.stat().st_size
            except OSError:
                continue
            if size1 == size2:
                log.info("Появился файл %s (%s байт)", path.name, size2)
                return path
        time.sleep(0.4)

    after = sorted(p.name for p in snapshot_files(directory) - before)
    raise TimeoutError(
        f"Файл {suffix} не появился в {directory} за {timeout}с. Новые файлы: {after or 'нет'}"
    )


def unique_dest(directory: Path, filename: str) -> Path:
    dest = directory / filename
    if not dest.exists():
        return dest
    stem, suffix = dest.stem, dest.suffix
    n = 2
    while True:
        candidate = directory / f"{stem}_{n}{suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def safe_click(driver: WebDriver, element: WebElement, what: str) -> None:
    log.debug("Клик: %s", what)
    try:
        driver.execute_script("arguments[0].scrollIntoView({block:'center'});", element)
        time.sleep(0.15)
        element.click()
        log.debug("Клик обычный ок: %s", what)
        return
    except (ElementClickInterceptedException, StaleElementReferenceException) as exc:
        log.debug("Обычный клик не прошёл (%s), пробуем JS: %s", exc.__class__.__name__, what)
    except Exception as exc:
        log.debug("Обычный клик исключение %s: %s", exc, what)

    try:
        driver.execute_script("arguments[0].click();", element)
        log.debug("Клик JS ок: %s", what)
    except Exception as exc:
        log.error("Не удалось кликнуть «%s»: %s", what, exc)
        raise


def find_optional(driver: WebDriver, xpath: str) -> WebElement | None:
    els = driver.find_elements(By.XPATH, xpath)
    visible = [el for el in els if el.is_displayed()]
    return (visible or els or [None])[0]


def close_extra_tabs(driver: WebDriver, keep_handle: str) -> None:
    try:
        handles = list(driver.window_handles)
    except (InvalidSessionIdException, WebDriverException) as exc:
        log.warning("Не читаются вкладки (драйвер/Chrome уже закрыт): %s", exc)
        return
    if len(handles) == 1:
        if handles[0] != keep_handle:
            log.warning("Единственная вкладка не совпадает с основной, переключаемся")
            driver.switch_to.window(handles[0])
        return

    log.debug("Закрываем лишние вкладки, оставляем ...%s (сейчас %s)", keep_handle[-6:], len(handles))
    for handle in handles:
        if handle == keep_handle:
            continue
        try:
            driver.switch_to.window(handle)
            log.debug("Закрываю вкладку url=%s title=%s", driver.current_url, driver.title)
            driver.close()
        except Exception as exc:
            log.debug("Не закрылась вкладка ...%s: %s", handle[-6:], exc)
    try:
        driver.switch_to.window(keep_handle)
    except (InvalidSessionIdException, WebDriverException) as exc:
        log.warning("Не переключились на основную вкладку: %s", exc)


def switch_to_new_tab(driver: WebDriver, before: list[str], timeout: int = SHORT_WAIT) -> str:
    deadline = time.time() + timeout
    started = time.time()
    last_log = 0.0
    while time.time() < deadline:
        handles = driver.window_handles
        new = [h for h in handles if h not in before]
        elapsed = time.time() - started
        if elapsed - last_log >= 5:
            log.debug("Ждём новую вкладку: прошло %.0fс, вкладок %s", elapsed, len(handles))
            last_log = elapsed
        if new:
            driver.switch_to.window(new[-1])
            log.info(
                "Переключились на новую вкладку за %.1fс url=%s title=%s",
                elapsed,
                driver.current_url,
                driver.title,
            )
            return new[-1]
        time.sleep(0.3)
    log_browser_state(driver, prefix="нет новой вкладки")
    raise TimeoutError(f"Новая вкладка не открылась за {timeout}с. Было {len(before)}, стало {len(driver.window_handles)}")


class PochtaClient:
    def __init__(self, driver: WebDriver):
        self.driver = driver
        self.main_handle: str | None = None

    def login(self) -> None:
        d = self.driver
        log.info("Открываем страницу входа: %s", LOGIN_URL)
        d.get(LOGIN_URL)
        self.main_handle = d.current_window_handle
        log.debug("Основная вкладка ...%s", self.main_handle[-6:])

        if find_optional(d, XPATH_SEARCH):
            log.info("Поле поиска уже есть — похоже, сессия жива, логин пропускаем")
            return

        try:
            user = _wait(d).until(EC.presence_of_element_located((By.XPATH, XPATH_USERNAME)))
        except TimeoutException:
            dump_debug(d, "login_no_username")
            if find_optional(d, XPATH_SEARCH):
                log.info("После таймаута логина нашли поиск — считаем, что уже внутри")
                return
            raise LetterProcessingError("Не появилось поле логина и нет поля поиска")

        log.info("Вводим логин %s", EMAIL)
        user.clear()
        user.send_keys(EMAIL)
        safe_click(d, _wait(d).until(EC.element_to_be_clickable((By.XPATH, XPATH_NEXT))), "Далее")

        password = _wait(d).until(EC.presence_of_element_located((By.XPATH, XPATH_PASSWORD)))
        log.info("Вводим пароль (%s символов)", len(PASSWORD))
        password.clear()
        password.send_keys(PASSWORD)
        safe_click(d, _wait(d).until(EC.element_to_be_clickable((By.XPATH, XPATH_LOGIN))), "Войти")

        try:
            _wait(d).until(EC.presence_of_element_located((By.XPATH, XPATH_SEARCH)))
            log.info("Авторизация в otpravka.pochta.ru прошла, поле поиска на месте")
        except TimeoutException:
            dump_debug(d, "login_no_search")
            raise LetterProcessingError("После «Войти» не появилось поле поиска — логин не удался")

        self.main_handle = d.current_window_handle

    def process_tracking_number(self, code: str) -> dict:
        d = self.driver
        if not self.main_handle:
            self.main_handle = d.current_window_handle
        close_extra_tabs(d, self.main_handle)

        result = {
            "code": code,
            "letter_pdf": None,
            "report_pdf": None,
        }
        log.info("========== Трек-номер %s ==========", code)

        try:
            self._open_letter(code)
            result["letter_pdf"] = str(self._download_letter_pdf(code))
            result["report_pdf"] = str(self._download_tracking_report(code))
            log.info(
                "Готово %s: письмо=%s отчёт=%s",
                code,
                result["letter_pdf"],
                result["report_pdf"],
            )
            return result
        finally:
            try:
                self._close_letter_popup()
            except Exception as exc:
                log.debug("Закрытие карточки после номера %s: %s", code, exc)

    def _search_field_value(self) -> str:
        el = find_optional(self.driver, XPATH_SEARCH)
        if not el:
            return ""
        return (el.get_attribute("value") or "").strip()

    def _page_kind(self) -> str:
        d = self.driver
        if d.find_elements(By.XPATH, XPATH_SEARCH_GROUP) or d.find_elements(By.XPATH, XPATH_SEARCH_STATUS):
            return "search"
        if d.find_elements(By.CSS_SELECTOR, "div.prepare"):
            return "prepare"
        return "other"

    def _fill_search_query(self, code: str) -> None:
        """Пишем номер в Angular-поле и жмём Enter — как вручную в шапке."""
        d = self.driver
        field = _wait(d).until(EC.element_to_be_clickable((By.XPATH, XPATH_SEARCH)))
        before = (field.get_attribute("value") or "").strip()
        log.info("Поле поиска до ввода: %r url=%s kind=%s", before, d.current_url, self._page_kind())

        result = d.execute_script(
            """
            const el = arguments[0];
            const value = arguments[1];
            el.focus();
            el.value = value;
            el.dispatchEvent(new Event('input', {bubbles: true}));
            el.dispatchEvent(new Event('change', {bubbles: true}));
            let submitted = false;
            if (window.angular) {
                try {
                    const ngEl = angular.element(el);
                    const ngModel = ngEl.controller('ngModel');
                    const scope = ngEl.scope();
                    if (ngModel && scope) {
                        scope.$apply(function () {
                            ngModel.$setViewValue(value);
                            if (scope.ssc && typeof scope.ssc.search === 'function') {
                                scope.ssc.search();
                                submitted = true;
                            }
                        });
                    }
                } catch (e) {
                    return {value: el.value, submitted: false, error: String(e)};
                }
            }
            return {value: el.value, submitted: submitted};
            """,
            field,
            code,
        )
        log.info("Результат записи в поиск: %s", result)
        written = (result or {}).get("value") if isinstance(result, dict) else result
        submitted = bool((result or {}).get("submitted")) if isinstance(result, dict) else False

        actual = self._search_field_value()
        log.info("Поле поиска после JS: %r submitted=%s", actual, submitted)
        if actual != code:
            raise LetterProcessingError(
                f"{code}: в поиске оказалось {actual!r}, а не трек-номер — поиск не запускаем"
            )

        if not submitted:
            log.info("Angular ssc.search() не вызвали, жмём Enter")
            field.send_keys(Keys.ENTER)
        else:
            log.info("Поиск запущен через Angular ssc.search()")

    def _wait_search_results(self, code: str, timeout: int | None = None) -> None:
        d = self.driver
        timeout = timeout or WAIT_TIMEOUT
        log.info("Ждём страницу поиска (search-group / «Найдено в разделе»), не дашборд «Новые»")

        def _ready(driver: WebDriver) -> bool:
            kind = self._page_kind()
            value = self._search_field_value()
            url = driver.current_url
            found = bool(
                driver.find_elements(By.XPATH, XPATH_SEARCH_GROUP)
                or driver.find_elements(By.XPATH, XPATH_SEARCH_STATUS)
            )
            log.debug("ожидание поиска: kind=%s field=%r found=%s url=%s", kind, value, found, url)
            return found

        try:
            WebDriverWait(d, timeout).until(_ready)
        except TimeoutException:
            dump_debug(d, f"no_search_group_{code}")
            raise LetterProcessingError(
                f"{code}: поиск не открылся. kind={self._page_kind()} "
                f"field={self._search_field_value()!r} url={d.current_url}"
            )

        log.info(
            "Поиск открылся: kind=%s field=%r url=%s",
            self._page_kind(),
            self._search_field_value(),
            d.current_url,
        )

    def _open_letter(self, code: str) -> None:
        d = self.driver
        log.info("Ищем письмо по треку %s через поле в шапке (не через URL дашборда)", code)

        last_error = None
        for attempt in range(1, 4):
            try:
                self._fill_search_query(code)
                self._wait_search_results(code, timeout=SHORT_WAIT if attempt < 3 else WAIT_TIMEOUT)
                last_error = None
                break
            except LetterProcessingError as exc:
                last_error = exc
                log.warning("Попытка поиска %s/%s не удалась: %s", attempt, 3, exc)
                dump_debug(d, f"search_retry_{attempt}_{code}")
                hash_target = f"/search?query={code}&type=SHIPMENTS"
                log.info("Запасной вариант: ставим location.hash=%s", hash_target)
                d.execute_script("window.location.hash = arguments[0];", hash_target)
                time.sleep(1.0)
        if last_error:
            raise last_error

        header = _wait(d).until(EC.element_to_be_clickable((By.XPATH, XPATH_SEARCH_GROUP_HEADER)))
        header_text = (header.text or "").replace("\n", " ")
        log.info("Заголовок группы поиска: %s", header_text)

        group = header.find_element(By.XPATH, "./ancestor::div[contains(@class,'search-group')]")
        group_open = "mailing-group--open" in (group.get_attribute("class") or "")
        panel_visible = bool(
            [
                el
                for el in group.find_elements(By.XPATH, ".//div[contains(@class, 'mailing-group__panel')]")
                if el.is_displayed()
            ]
        )
        log.debug("Группа поиска open=%s panel_visible=%s", group_open, panel_visible)
        if not group_open and not panel_visible:
            log.info("Раскрываем группу результатов поиска")
            safe_click(d, header, "search-group header")
            time.sleep(0.5)

        row_xpath = (
            f"{XPATH_SEARCH_GROUP}//div[contains(@class, 'mailing__row')]"
            f"//div[contains(normalize-space(.), '{code}')]"
            f"/ancestor::div[contains(@class, 'mailing__row')]"
        )
        log.debug("XPath строки письма: %s", row_xpath)
        try:
            row = _wait(d).until(EC.element_to_be_clickable((By.XPATH, row_xpath)))
        except TimeoutException:
            dump_debug(d, f"no_letter_row_{code}")
            raise LetterProcessingError(f"{code}: строка письма в результатах поиска не найдена")

        log.info("Открываем карточку письма")
        safe_click(d, row, f"строка {code}")

        try:
            _wait(d).until(
                EC.presence_of_element_located(
                    (By.XPATH, f"//a[contains(@href, '/pdf') and contains(@class, 'input__document-icon')]")
                )
            )
            log.info("Карточка письма открылась, ссылка на PDF есть")
        except TimeoutException:
            dump_debug(d, f"no_letter_popup_{code}")
            raise LetterProcessingError(f"{code}: карточка открылась, но ссылки на PDF нет")

    def _download_letter_pdf(self, code: str) -> Path:
        d = self.driver
        pdf_xpath = "//a[contains(@class, 'input__document-icon') and contains(@href, '/pdf')]"
        link = _wait(d).until(EC.presence_of_element_located((By.XPATH, pdf_xpath)))
        href = link.get_attribute("href") or ""
        log.info("Ссылка на PDF письма: %s", href)
        if not href:
            dump_debug(d, f"empty_pdf_href_{code}")
            raise LetterProcessingError(f"{code}: у иконки документа пустой href")

        dest = unique_dest(DOWNLOAD_DIR, f"{code}_letter.pdf")
        try:
            self._http_download(href, dest)
            return dest
        except Exception as exc:
            log.warning("Скачивание через requests не вышло (%s), кликаем ссылку", exc)

        before = snapshot_files(DOWNLOAD_DIR)
        handles_before = list(d.window_handles)
        safe_click(d, link, "иконка PDF письма")
        time.sleep(0.8)
        if len(d.window_handles) > len(handles_before):
            log.debug("Клик по PDF открыл вкладку, ждём файл и закроем её")
        try:
            downloaded = wait_for_new_file(DOWNLOAD_DIR, before)
        except TimeoutError:
            dump_debug(d, f"letter_pdf_download_timeout_{code}")
            close_extra_tabs(d, self.main_handle)
            raise LetterProcessingError(f"{code}: PDF письма не скачался за {DOWNLOAD_TIMEOUT}с")

        close_extra_tabs(d, self.main_handle)
        if downloaded.resolve() != dest.resolve():
            shutil.move(str(downloaded), str(dest))
            log.info("PDF письма переименован: %s -> %s", downloaded.name, dest.name)
        return dest

    def _http_download(self, url: str, dest: Path) -> None:
        session = requests.Session()
        for cookie in self.driver.get_cookies():
            session.cookies.set(cookie["name"], cookie["value"])
        ua = self.driver.execute_script("return navigator.userAgent")
        headers = {
            "User-Agent": ua,
            "Referer": self.driver.current_url,
        }
        log.debug("HTTP GET %s", url)
        response = session.get(url, headers=headers, timeout=60, allow_redirects=True)
        log.info(
            "HTTP %s %s content-type=%s size=%s",
            response.status_code,
            url,
            response.headers.get("Content-Type"),
            len(response.content),
        )
        response.raise_for_status()
        content_type = (response.headers.get("Content-Type") or "").lower()
        if "pdf" not in content_type and not response.content.startswith(b"%PDF"):
            preview = response.text[:300].replace("\n", " ")
            raise RuntimeError(
                f"Ответ не похож на PDF (content-type={content_type}): {preview}"
            )
        dest.write_bytes(response.content)
        log.info("PDF письма сохранён через HTTP: %s (%s байт)", dest, dest.stat().st_size)

    def _download_tracking_report(self, code: str) -> Path:
        d = self.driver
        try:
            self._open_tracking_tab(code)
            decision = self._wait_tracking_decision(code)

            if decision == "login_warning":
                log.info("Есть предупреждение «Войдите» — пропускаем вход и откроем трекинг заново")
                self._skip_tracking_login()
                log.info("Закрываем вкладку трекинга, возвращаемся к карточке письма")
                close_extra_tabs(d, self.main_handle)
                self._open_tracking_tab(code)
                decision = self._wait_tracking_decision(code)
                if decision == "login_warning":
                    dump_debug(d, f"login_warning_again_{code}")
                    raise LetterProcessingError(
                        f"{code}: после «Войдите»/«Пропустить» и повторного открытия "
                        "предупреждение всё ещё на месте"
                    )

            self._ensure_print_button(code)
            return self._save_report_pdf(code)
        finally:
            try:
                if self.main_handle:
                    close_extra_tabs(d, self.main_handle)
                    log.info("Вкладка трекинга закрыта, остались на карточке письма")
            except Exception as exc:
                log.warning("Не удалось закрыть вкладку трекинга: %s", exc)

    def _tracking_link(self, code: str) -> WebElement:
        d = self.driver
        if self.main_handle:
            d.switch_to.window(self.main_handle)
        link_xpath = (
            f"//a[contains(@href, 'pochta.ru/tracking') and contains(normalize-space(.), '{code}')]"
        )
        log.debug("XPath ссылки трекинга: %s", link_xpath)
        try:
            return _wait(d).until(EC.element_to_be_clickable((By.XPATH, link_xpath)))
        except TimeoutException:
            dump_debug(d, f"no_tracking_link_{code}")
            raise LetterProcessingError(f"{code}: ссылка на трекинг в карточке не найдена")

    def _open_tracking_tab(self, code: str) -> None:
        d = self.driver
        tracking_link = self._tracking_link(code)
        href = tracking_link.get_attribute("href")
        log.info("Открываем трекинг %s href=%s", code, href)
        handles_before = list(d.window_handles)
        safe_click(d, tracking_link, "ссылка трек-номера")
        try:
            switch_to_new_tab(d, handles_before, timeout=WAIT_TIMEOUT)
        except TimeoutError:
            log.warning("Новая вкладка не открылась, url=%s", d.current_url)
            if "pochta.ru/tracking" not in (d.current_url or ""):
                raise LetterProcessingError(f"{code}: клик по треку не открыл страницу отслеживания")

    def _wait_tracking_decision(self, code: str) -> str:
        """После полной загрузки: либо предупреждение «Войдите», либо кнопка отчёта."""
        d = self.driver
        log.info("Ждём загрузку трекинга: предупреждение «Войдите» или кнопка печати")
        deadline = time.time() + WAIT_TIMEOUT
        last = None
        while time.time() < deadline:
            try:
                warning = find_optional(d, XPATH_LOGIN_WARNING_LINK) or find_optional(
                    d, XPATH_LOGIN_WARNING_TEXT
                )
                print_btn = find_optional(d, XPATH_PRINT)
                skip = find_optional(d, XPATH_SKIP)
            except (InvalidSessionIdException, WebDriverException) as exc:
                raise LetterProcessingError(
                    f"{code}: Chrome/chromedriver отвалился, пока ждали трекинг: {exc}"
                ) from exc

            if warning:
                state = "login_warning"
            elif print_btn:
                state = "print"
            elif skip:
                state = "skip"
            else:
                state = "loading"

            if state != last:
                log.info("Трекинг: %s url=%s title=%s", state, d.current_url, d.title)
                last = state

            if state in {"login_warning", "print"}:
                return state
            if state == "skip":
                log.info("Сразу страница «Пропустить» — считаем, что просят вход")
                return "login_warning"
            time.sleep(0.4)

        dump_debug(d, f"tracking_undecided_{code}")
        raise LetterProcessingError(
            f"{code}: трекинг загрузился, но нет ни «Войдите», ни кнопки печати. "
            f"url={d.current_url} title={d.title}"
        )

    def _skip_tracking_login(self) -> None:
        d = self.driver
        link = find_optional(d, XPATH_LOGIN_WARNING_LINK)
        if link:
            log.info("Нажимаем «Войдите» в предупреждении (href=%s)", link.get_attribute("href"))
            safe_click(d, link, "Войдите")
        else:
            log.warning("Ссылки «Войдите» нет — возможно уже passport, ищем «Пропустить»")

        try:
            skip = _wait(d, SHORT_WAIT).until(EC.element_to_be_clickable((By.XPATH, XPATH_SKIP)))
        except TimeoutException:
            dump_debug(d, "no_skip_after_vijdite")
            raise LetterProcessingError(
                f"После «Войдите» нет кнопки «Пропустить». url={d.current_url}"
            )

        log.info("Нажимаем «Пропустить» (дальше вкладку закроем, отчёт будет на повторном открытии)")
        try:
            safe_click(d, skip, "Пропустить")
            time.sleep(1.5)
        except (InvalidSessionIdException, WebDriverException) as exc:
            log.warning(
                "После «Пропустить» сессия драйвера могла оборваться (%s). "
                "Попробуем закрыть вкладку и открыть трек ещё раз.",
                exc,
            )

    def _ensure_print_button(self, code: str) -> None:
        d = self.driver
        log.info("Ждём кнопку печати отчёта на трекинге")
        try:
            _wait(d).until(EC.element_to_be_clickable((By.XPATH, XPATH_PRINT)))
            log.info("Кнопка #tracking-card-footer__print-button готова")
        except TimeoutException:
            dump_debug(d, f"no_print_button_{code}")
            raise LetterProcessingError(
                f"{code}: нет кнопки печати. url={d.current_url} title={d.title}"
            )

    def _save_report_pdf(self, code: str) -> Path:
        dest = unique_dest(REPORTS_DIR, f"{code}_tracking_report.pdf")
        method = PRINT_METHOD
        log.info("Сохраняем отчёт трекинга методом %s -> %s", method, dest)

        if method in {"kiosk", "auto"}:
            try:
                return self._save_report_kiosk(code, dest)
            except Exception as exc:
                log.warning("Kiosk-печать не сработала: %s", exc)
                if method == "kiosk":
                    dump_debug(self.driver, f"kiosk_failed_{code}")
                    raise LetterProcessingError(f"{code}: kiosk-печать не сохранила PDF: {exc}") from exc
                log.info("Переключаемся на CDP Page.printToPDF")

        return self._save_report_cdp(code, dest)

    def _save_report_kiosk(self, code: str, dest: Path) -> Path:
        d = self.driver
        before = snapshot_files(REPORTS_DIR)
        button = _wait(d).until(EC.element_to_be_clickable((By.XPATH, XPATH_PRINT)))
        log.info("Кликаем печать (ожидаем тихий Save as PDF в %s)", REPORTS_DIR)
        safe_click(d, button, "кнопка печати отчёта")
        downloaded = wait_for_new_file(REPORTS_DIR, before, timeout=DOWNLOAD_TIMEOUT)
        if downloaded.resolve() != dest.resolve():
            shutil.move(str(downloaded), str(dest))
            log.info("Отчёт переименован: %s -> %s", downloaded.name, dest.name)
        log.info("Отчёт сохранён kiosk-печатью: %s (%s байт)", dest, dest.stat().st_size)
        return dest

    def _save_report_cdp(self, code: str, dest: Path) -> Path:
        d = self.driver
        try:
            d.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            time.sleep(0.4)
        except Exception:
            pass

        log.info("CDP Page.printToPDF для url=%s", d.current_url)
        try:
            result = d.execute_cdp_cmd(
                "Page.printToPDF",
                {
                    "printBackground": True,
                    "preferCSSPageSize": True,
                    "paperWidth": 8.27,
                    "paperHeight": 11.69,
                    "landscape": False,
                },
            )
        except Exception as exc:
            dump_debug(d, f"cdp_print_failed_{code}")
            raise LetterProcessingError(f"{code}: CDP printToPDF упал: {exc}") from exc

        data = result.get("data")
        if not data:
            dump_debug(d, f"cdp_empty_{code}")
            raise LetterProcessingError(f"{code}: CDP вернул пустой PDF")
        dest.write_bytes(base64.b64decode(data))
        log.info("Отчёт сохранён через CDP: %s (%s байт)", dest, dest.stat().st_size)
        return dest

    def _close_letter_popup(self) -> None:
        d = self.driver
        if self.main_handle and d.current_window_handle != self.main_handle:
            close_extra_tabs(d, self.main_handle)
        close_btn = find_optional(d, XPATH_POPUP_CLOSE)
        if not close_btn:
            log.debug("Кнопки закрытия карточки нет — возможно уже закрыта")
            return
        log.info("Закрываем карточку письма")
        try:
            safe_click(d, close_btn, "закрыть карточку")
            time.sleep(0.4)
        except Exception as exc:
            log.warning("Карточка не закрылась: %s", exc)
