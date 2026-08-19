import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env(name, "true" if default else "false").lower()
    return raw in {"1", "true", "yes", "y", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if not raw:
        return default
    return int(raw)


def _resolve_path(value: str | None, default: Path) -> Path:
    if not value:
        return default
    path = Path(os.path.expandvars(value)).expanduser()
    if not path.is_absolute():
        path = BASE_DIR / path
    return path


def _find_chrome() -> str:
    configured = _env("CHROME_PATH")
    if configured:
        path = _resolve_path(configured, Path(configured))
        if path.exists():
            return str(path)
    home = Path.home()
    candidates = [
        home / r"AppData\Local\Chromium\Application\chrome.exe",
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        home / r"AppData\Local\Google\Chrome\Application\chrome.exe",
        Path("/usr/bin/google-chrome"),
        Path("/usr/bin/google-chrome-stable"),
        Path("/usr/bin/chromium"),
        Path("/usr/bin/chromium-browser"),
        Path("/snap/bin/chromium"),
        Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        Path("/Applications/Chromium.app/Contents/MacOS/Chromium"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    raise FileNotFoundError(
        "Не найден Chrome/Chromium. Укажите CHROME_PATH в .env относительно папки проекта или абсолютным путём."
    )


def _usable_driver(path: Path) -> bool:
    if not path.exists():
        return False
    if os.name != "nt" and path.suffix.lower() == ".exe":
        return False
    return True


def _find_driver() -> str:
    configured = _env("DRIVER_PATH")
    if configured:
        path = _resolve_path(configured, Path(configured))
        if _usable_driver(path):
            return str(path)
    names = [
        BASE_DIR / "chromedriver-win64" / "chromedriver.exe",
        BASE_DIR / "chromedriver.exe",
        BASE_DIR / "chromedriver-linux64" / "chromedriver",
        BASE_DIR / "chromedriver-mac64" / "chromedriver",
        BASE_DIR / "chromedriver",
    ]
    for candidate in names:
        if _usable_driver(candidate):
            return str(candidate)
    raise FileNotFoundError(
        "Не найден chromedriver. Положите его в папку проекта (chromedriver-win64/chromedriver.exe "
        "или chromedriver) либо укажите DRIVER_PATH в .env."
    )


def _sql_from_env() -> str:
    """SQL из .env; многострочный запрос можно обернуть в кавычки."""
    raw = os.getenv("TRACKING_NUMBERS_SQL", "") or ""
    sql = raw.strip().strip('"').strip("'")
    sql_file = _env("TRACKING_NUMBERS_SQL_FILE")
    if sql_file:
        path = _resolve_path(sql_file, BASE_DIR / sql_file)
        if path.exists():
            sql = path.read_text(encoding="utf-8").strip()
    return sql


def _stub_numbers() -> list[str]:
    raw = _env("STUB_TRACKING_NUMBERS", "80106011593121")
    numbers = []
    for part in raw.replace(";", ",").split(","):
        value = part.strip()
        if value:
            numbers.append(value)
    return numbers or ["80106011593121"]


DRIVER_PATH = _find_driver()
CHROME_PATH = _find_chrome()
DRIVER_LOG_PATH = "NUL" if os.name == "nt" else "/dev/null"

EMAIL = _env("EMAIL")
PASSWORD = _env("PASSWORD")

DOWNLOAD_DIR = _resolve_path(_env("DOWNLOAD_DIR"), BASE_DIR / "downloads_pdf")
REPORTS_DIR = _resolve_path(_env("REPORTS_DIR"), BASE_DIR / "reports_pdf")
LOG_DIR = _resolve_path(_env("LOG_DIR"), BASE_DIR / "logs")
DEBUG_DIR = _resolve_path(_env("DEBUG_DIR"), LOG_DIR / "debug")

LOGIN_URL = _env("LOGIN_URL", "https://otpravka.pochta.ru/statistics#/statistics-overall")

WAIT_TIMEOUT = _env_int("WAIT_TIMEOUT", 60)
SHORT_WAIT = _env_int("SHORT_WAIT", 12)
DOWNLOAD_TIMEOUT = _env_int("DOWNLOAD_TIMEOUT", 45)

# cdp | kiosk | auto
# auto: клик печати + тихая печать Chrome, если PDF не появился — CDP Page.printToPDF
PRINT_METHOD = _env("PRINT_METHOD", "auto").lower() or "auto"

USE_DATABASE = _env_bool("USE_DATABASE", False)
STUB_TRACKING_NUMBERS = _stub_numbers()
TRACKING_NUMBERS_SQL = _sql_from_env()

POSTGRES_HOST = _env("POSTGRES_HOST", "localhost")
POSTGRES_PORT = _env_int("POSTGRES_PORT", 5432)
POSTGRES_DB = _env("POSTGRES_DB", "postgres")
POSTGRES_USER = _env("POSTGRES_USER", "postgres")
POSTGRES_PASSWORD = _env("POSTGRES_PASSWORD")
POSTGRES_CONNECT_TIMEOUT = _env_int("POSTGRES_CONNECT_TIMEOUT", 10)

KEEP_BROWSER_OPEN_ON_ERROR = _env_bool("KEEP_BROWSER_OPEN_ON_ERROR", False)
HEADLESS = _env_bool("HEADLESS", False)
