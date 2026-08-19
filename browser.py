import json
import logging
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service

from config import (
    CHROME_PATH,
    DOWNLOAD_DIR,
    DRIVER_LOG_PATH,
    DRIVER_PATH,
    HEADLESS,
    REPORTS_DIR,
)

log = logging.getLogger(__name__)


def _print_prefs() -> dict:
    """Chrome сам выбирает «Save as PDF», без системного Microsoft Print to PDF."""
    app_state = {
        "recentDestinations": [
            {
                "id": "Save as PDF",
                "origin": "local",
                "account": "",
            }
        ],
        "selectedDestinationId": "Save as PDF",
        "version": 2,
        "isHeaderFooterEnabled": False,
        "isLandscapeEnabled": False,
        "isCssBackgroundEnabled": True,
        "customMargins": {},
        "marginsType": 0,
        "scaling": 100,
        "scalingType": 3,
        "isColorEnabled": True,
        "isDuplexEnabled": False,
        "isDuplexShortEdge": False,
        "isGoogleDriveEnabled": False,
    }
    return {
        "download.default_directory": str(DOWNLOAD_DIR),
        "download.prompt_for_download": False,
        "download.directory_upgrade": True,
        "safebrowsing.enabled": True,
        "plugins.always_open_pdf_externally": True,
        "savefile.default_directory": str(REPORTS_DIR),
        "printing.print_preview_sticky_settings.appState": json.dumps(app_state),
        "printing.default_destination_selection_rules": json.dumps(
            {
                "kind": "local",
                "idPattern": "Save as PDF",
                "namePattern": ".*PDF.*",
            }
        ),
    }


def create_driver() -> webdriver.Chrome:
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    options = Options()
    options.binary_location = CHROME_PATH
    options.add_argument("--start-maximized")
    options.add_argument("--disable-notifications")
    options.add_argument("--disable-popup-blocking")
    options.add_argument("--disable-gpu")
    options.add_argument("--no-first-run")
    options.add_argument("--no-default-browser-check")
    # Тихая печать: диалог не показывается, сразу Save as PDF в REPORTS_DIR
    options.add_argument("--kiosk-printing")
    if HEADLESS:
        options.add_argument("--headless=new")
        log.info("Запуск Chrome в headless")

    options.add_experimental_option("prefs", _print_prefs())
    options.add_experimental_option("excludeSwitches", ["enable-logging", "enable-automation"])

    log.info("Chrome: %s", CHROME_PATH)
    log.info("chromedriver: %s", DRIVER_PATH)
    log.info("Папка писем (DOWNLOAD_DIR): %s", DOWNLOAD_DIR)
    log.info("Папка отчётов (REPORTS_DIR): %s", REPORTS_DIR)

    service = Service(executable_path=DRIVER_PATH, log_output=DRIVER_LOG_PATH)
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(90)

    try:
        driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {"behavior": "allow", "downloadPath": str(DOWNLOAD_DIR)},
        )
        log.debug("CDP Page.setDownloadBehavior -> %s", DOWNLOAD_DIR)
    except Exception as exc:
        log.warning("Не удалось задать CDP downloadPath: %s", exc)

    return driver
