# mypy: disable-error-code="no-untyped-call,no-untyped-def"
import argparse
import atexit
import base64
import os.path
import platform
import re
import subprocess
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from time import sleep
from typing import Dict, List, Optional

import requests
from requests import Response
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.core.os_manager import ChromeType, OperationSystemManager

__version__ = "0.0.15a2"

PATH_TO_HTML2PDF_JS = os.path.join(
    os.path.dirname(os.path.join(__file__)), "html2pdf_js", "html2pdf.min.js"
)

DEFAULT_CACHE_DIR = os.path.join(Path.home(), ".html2print", "chromedriver")

# HTML2PDF.js prints unicode symbols to console. The following makes it work on
# Windows which otherwise complains:
# UnicodeEncodeError: 'charmap' codec can't encode characters in position 129-130: character maps to <undefined>
# How to make python 3 print() utf8
# https://stackoverflow.com/questions/3597480/how-to-make-python-3-print-utf8
sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf8", closefd=False)


class ChromeDriverManager:
    def get_chrome_driver(self, path_to_cache_dir: str):
        chrome_version = self.get_chrome_version()

        # If Web Driver Manager cannot detect Chrome, it returns None.
        if chrome_version is None:
            raise RuntimeError(
                "html2print: "
                "Web Driver Manager could not detect an existing Chrome installation."
            )

        chrome_major_version = chrome_version.split(".")[0]

        print(  # noqa: T201
            f"html2print: Installed Chrome version: {chrome_version}"
        )

        system_map = {
            "Windows": "win32",
            "Darwin": "mac-arm64"
            if platform.machine() == "arm64"
            else "mac-x64",
            "Linux": "linux64",
        }
        os_type = system_map[platform.system()]
        is_windows = platform.system() == "Windows"

        print(  # noqa: T201
            f"html2print: OS system: {platform.system()}, OS type: {os_type}."
        )

        path_to_cached_chrome_driver_dir = os.path.join(
            path_to_cache_dir, chrome_major_version
        )
        path_to_cached_chrome_driver = os.path.join(
            path_to_cached_chrome_driver_dir,
            f"chromedriver-{os_type}",
            "chromedriver",
        )
        if is_windows:
            path_to_cached_chrome_driver += ".exe"

        if os.path.isfile(path_to_cached_chrome_driver):
            print(  # noqa: T201
                f"html2print: ChromeDriver exists in the local cache: "
                f"{path_to_cached_chrome_driver}"
            )
            return path_to_cached_chrome_driver
        print(  # noqa: T201
            f"html2print: ChromeDriver does not exist in the local cache: "
            f"{path_to_cached_chrome_driver}"
        )

        path_to_downloaded_chrome_driver = self._download_chromedriver(
            chrome_major_version,
            os_type,
            path_to_cached_chrome_driver_dir,
            path_to_cached_chrome_driver,
        )
        assert os.path.isfile(path_to_downloaded_chrome_driver)
        os.chmod(path_to_downloaded_chrome_driver, 0o755)

        return path_to_downloaded_chrome_driver

    @staticmethod
    def _download_chromedriver(
        chrome_major_version,
        os_type: str,
        path_to_driver_cache_dir,
        path_to_cached_chrome_driver,
    ):
        url = "https://googlechromelabs.github.io/chrome-for-testing/known-good-versions-with-downloads.json"
        response = ChromeDriverManager.send_http_get_request(url).json()

        matching_versions = [
            item
            for item in response["versions"]
            if item["version"].startswith(chrome_major_version)
        ]

        if not matching_versions:
            raise Exception(
                f"No compatible ChromeDriver found for Chrome version {chrome_major_version}"
            )

        latest_version = matching_versions[-1]

        driver_url: str
        chrome_downloadable_versions = latest_version["downloads"][
            "chromedriver"
        ]
        for chrome_downloadable_version_ in chrome_downloadable_versions:
            if chrome_downloadable_version_["platform"] == os_type:
                driver_url = chrome_downloadable_version_["url"]
                break
        else:
            raise RuntimeError(
                f"Could not find a downloadable URL from downloadable versions: {chrome_downloadable_versions}"
            )

        print(  # noqa: T201
            f"html2print: downloading ChromeDriver from: {driver_url}"
        )
        response = ChromeDriverManager.send_http_get_request(driver_url)

        Path(path_to_driver_cache_dir).mkdir(parents=True, exist_ok=True)
        zip_path = os.path.join(path_to_driver_cache_dir, "chromedriver.zip")
        print(  # noqa: T201
            f"html2print: saving downloaded ChromeDriver to path: {zip_path}"
        )
        with open(zip_path, "wb") as file:
            file.write(response.content)

        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            zip_ref.extractall(path_to_driver_cache_dir)

        print(  # noqa: T201
            f"html2print: ChromeDriver downloaded to: {path_to_cached_chrome_driver}"
        )
        return path_to_cached_chrome_driver

    @staticmethod
    def send_http_get_request(url, params=None, **kwargs) -> Response:
        last_error: Optional[Exception] = None
        for attempt in range(1, 4):
            print(  # noqa: T201
                f"html2print: sending GET request attempt {attempt}: {url}"
            )
            try:
                return requests.get(url, params, timeout=(5, 5), **kwargs)
            except requests.exceptions.ConnectTimeout as connect_timeout_:
                last_error = connect_timeout_
            except requests.exceptions.ReadTimeout as read_timeout_:
                last_error = read_timeout_
            except Exception as exception_:
                raise AssertionError(
                    "html2print: unknown exception", exception_
                ) from None
        print(  # noqa: T201
            f"html2print: "
            f"failed to get response for URL: {url} with error: {last_error}"
        )

    @staticmethod
    def get_chrome_version():
        # Special case: GitHub Actions macOS CI machines have both
        # Google Chrome for Testing and normal Google Chrome installed, and
        # sometimes their versions are of different major version families.
        # The solution is to check if the Google Chrome for Testing is available,
        # and use its version instead of the normal one.
        if platform.system() == "Darwin":
            chrome_path = "/Applications/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing"
            try:
                print(  # noqa: T201
                    "html2print: "
                    "checking if there is Google Chrome for Testing instead of "
                    "a normal Chrome available."
                )

                version_output = subprocess.run(
                    [chrome_path, "--version"],
                    capture_output=True,
                    text=True,
                    check=True,
                )
                chrome_version = version_output.stdout.strip()
                match = re.search(r"\d+(\.\d+)+", chrome_version)
                if not match:
                    raise RuntimeError(
                        "Cannot extract the version part using regex."
                    )

                chrome_version = match.group(0)

                print(  # noqa: T201
                    f"html2print: Google Chrome for Testing Version: {chrome_version}"
                )

                return chrome_version
            except FileNotFoundError:
                print("html2print: Chrome for Testing not available.")  # noqa: T201
            except Exception as e:
                print(  # noqa: T201
                    f"html2print: Error getting Google Chrome for Testing version: {e}"
                )

        os_manager = OperationSystemManager(os_type=None)
        version = os_manager.get_browser_version_from_os(ChromeType.GOOGLE)
        return version


def get_inches_from_millimeters(mm: float) -> float:
    return mm / 25.4


def get_pdf_from_html(driver, url) -> bytes:
    print(f"html2print: opening URL with ChromeDriver: {url}")  # noqa: T201

    driver.get(url)

    # https://chromedevtools.github.io/devtools-protocol/tot/Page/#method-printToPDF
    calculated_print_options = {
        "landscape": False,
        "displayHeaderFooter": False,
        "printBackground": True,
        # This is an experimental feature that generates a document outline
        # (table of contents).
        "generateDocumentOutline": True,
        # Whether to prefer page size as defined by css. Defaults to
        # false, in which case the content will be scaled to fit the paper size.
        "preferCSSPageSize": True,
        # Paper width in inches. Defaults to 8.5 inches.
        "paperWidth": get_inches_from_millimeters(210),
        # Paper height in inches. Defaults to 11 inches.
        "paperHeight": get_inches_from_millimeters(297),
        # WIP: Changing the margin settings has no effect.
        # Top margin in inches. Defaults to 1cm (~0.4 inches).
        "marginTop": get_inches_from_millimeters(12),
        # Bottom margin in inches. Defaults to 1cm (~0.4 inches).
        "marginBottom": get_inches_from_millimeters(12),
        # Left margin in inches. Defaults to 1cm (~0.4 inches).
        "marginLeft": get_inches_from_millimeters(21),
        # Right margin in inches. Defaults to 1cm (~0.4 inches).
        "marginRight": get_inches_from_millimeters(21),
    }

    class Done(Exception):
        pass

    datetime_start = datetime.today()

    logs: List[Dict[str, str]] = []
    try:
        while True:
            logs = driver.get_log("browser")
            for entry_ in logs:
                if "HTML2PDF4DOC time" in entry_["message"]:
                    print("success: HTML2PDF completed its job.")  # noqa: T201
                    raise Done
            if (datetime.today() - datetime_start).total_seconds() > 60:
                raise TimeoutError
            sleep(0.5)
    except Done:
        pass
    except TimeoutError:
        print(  # noqa: T201
            "error: could not receive a successful completion status from HTML2PDF."
        )
        # sys.exit(1)

    print("html2print: JS logs from the print session:")  # noqa: T201
    print('"""')  # noqa: T201
    for entry in logs:
        print(entry)  # noqa: T201
    print('"""')  # noqa: T201

    #
    # Execute Print command with ChromeDriver.
    #
    print("html2print: executing print command with ChromeDriver.")  # noqa: T201
    result = driver.execute_cdp_cmd("Page.printToPDF", calculated_print_options)

    data = base64.b64decode(result["data"])
    return data


def create_webdriver(
    chromedriver: Optional[str], path_to_cache_dir: str, page_load_timeout: int
) -> webdriver.Chrome:
    print("html2print: creating ChromeDriver service.", flush=True)  # noqa: T201
    if chromedriver is None:
        path_to_chrome = ChromeDriverManager().get_chrome_driver(
            path_to_cache_dir
        )
    else:
        path_to_chrome = chromedriver
    print(f"html2print: ChromeDriver available at path: {path_to_chrome}")  # noqa: T201

    service = Service(path_to_chrome)

    # user_data_dir = tempfile.mkdtemp()

    webdriver_options = Options()
    webdriver_options.add_argument("start-maximized")
    webdriver_options.add_argument("disable-infobars")
    webdriver_options.add_argument('--disable-gpu')
    webdriver_options.add_argument("--disable-extensions")
    webdriver_options.add_argument("--headless=new")
    # FIXME: This is not nice but otherwise it does not work in Ubuntu 24-based Docker image.
    # https://github.com/SeleniumHQ/selenium/issues/15327#issuecomment-2689287561
    webdriver_options.add_argument("--no-sandbox")
    # webdriver_options.add_argument(f'--user-data-dir={user_data_dir}')

    # The Chrome option --disable-dev-shm-usage disables the use of /dev/shm
    # (shared memory) for temporary storage in Chrome.
    # By default, Chrome uses /dev/shm for storing temporary files to improve
    # performance. However, in environments with limited shared memory (such as
    # Docker containers), this can lead to crashes or issues due to insufficient
    # space.
    webdriver_options.add_argument("--disable-dev-shm-usage")

    webdriver_options.add_experimental_option("useAutomationExtension", False)
    webdriver_options.add_experimental_option(
        "excludeSwitches", ["enable-automation"]
    )

    # Enable the capturing of everything in JS console.
    webdriver_options.set_capability("goog:loggingPrefs", {"browser": "ALL"})

    print("html2print: creating ChromeDriver.", flush=True)  # noqa: T201

    driver = webdriver.Chrome(
        options=webdriver_options,
        service=service,
    )
    driver.set_page_load_timeout(page_load_timeout)

    return driver


def main():
    if not os.path.isfile(PATH_TO_HTML2PDF_JS):
        raise RuntimeError(
            f"Corrupted html2print package bundle. "
            f"The HTML2PDF.js file is missing at path: {PATH_TO_HTML2PDF_JS}."
        )

    parser = argparse.ArgumentParser(description="HTML2Print printer script.")

    parser.add_argument(
        "-v", "--version", action="version", version=__version__
    )

    command_subparsers = parser.add_subparsers(title="command", dest="command")
    command_subparsers.required = True

    print(f"html2print: version {__version__}")  # noqa: T201

    #
    # Get driver command.
    #
    command_parser_get_driver = command_subparsers.add_parser(
        "get_driver",
        help="Check if ChromeDriver already exists locally. If not, download it.",
        description="",
    )
    command_parser_get_driver.add_argument(
        "--cache-dir",
        type=str,
        help="Optional path to a cache directory whereto the ChromeDriver is downloaded.",
    )

    #
    # Print command.
    #
    command_parser_print = command_subparsers.add_parser(
        "print",
        help="Main print command",
        description="",
    )
    command_parser_print.add_argument(
        "--chromedriver",
        type=str,
        help="Optional chromedriver path. Downloaded if not given.",
    )
    command_parser_print.add_argument(
        "--cache-dir",
        type=str,
        help="Optional path to a cache directory whereto the ChromeDriver is downloaded.",
    )
    command_parser_print.add_argument(
        "--page-load-timeout",
        type=int,
        default=2 * 60,
        # 10 minutes should be enough to print even the largest documents.
        choices=range(0, 10 * 60),
        help=(
            "How long shall HTML2Print wait while the Chrome Driver is printing "
            "a given HTML page to PDF. "
            "This is mainly driven by the time it takes for Chrome to open an "
            "HTML file, load it, and let HTML2PDF.js finish its job."
        ),
    )
    command_parser_print.add_argument(
        "paths", nargs="+", help="Paths to input HTML file."
    )

    args = parser.parse_args()

    path_to_cache_dir: str
    if args.command == "get_driver":
        path_to_cache_dir = (
            args.cache_dir if args.cache_dir is not None else DEFAULT_CACHE_DIR
        )

        path_to_chrome = ChromeDriverManager().get_chrome_driver(
            path_to_cache_dir
        )
        print(f"html2print: ChromeDriver available at path: {path_to_chrome}")  # noqa: T201
        sys.exit(0)

    elif args.command == "print":
        paths: List[str] = args.paths

        page_load_timeout: int = args.page_load_timeout

        path_to_cache_dir = (
            args.cache_dir if args.cache_dir is not None else DEFAULT_CACHE_DIR
        )
        driver = create_webdriver(
            args.chromedriver, path_to_cache_dir, page_load_timeout
        )

        @atexit.register
        def exit_handler():
            print("html2print: exit handler: quitting the ChromeDriver.")  # noqa: T201
            driver.quit()

        assert len(paths) % 2 == 0, (
            f"Expecting an even number of input/output path arguments: {paths}."
        )
        for current_pair_idx in range(0, len(paths), 2):
            path_to_input_html = paths[current_pair_idx]
            path_to_output_pdf = paths[current_pair_idx + 1]

            assert os.path.isfile(path_to_input_html), path_to_input_html

            path_to_output_pdf_dir = os.path.dirname(path_to_output_pdf)
            Path(path_to_output_pdf_dir).mkdir(parents=True, exist_ok=True)

            url = Path(os.path.abspath(path_to_input_html)).as_uri()

            pdf_bytes = get_pdf_from_html(driver, url)
            with open(path_to_output_pdf, "wb") as f:
                f.write(pdf_bytes)
    else:
        print("html2print: unknown command.")  # noqa: T201
        sys.exit(1)


if __name__ == "__main__":
    main()
