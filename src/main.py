import os
import csv
import sys
import time
import datetime
import random
import re
import argparse
import subprocess
import toml

from PySide6.QtCore import Qt, QDate, Signal, QObject, QThread
from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QHBoxLayout,
    QVBoxLayout,
    QListWidget,
    QStackedWidget,
    QLabel,
    QPlainTextEdit,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QFileDialog,
    QPushButton,
    QFileIconProvider,
    QDateEdit,
    QSizePolicy,
    QDialog,
    QProgressBar,
)

import asyncio
from scrapling.fetchers import AsyncStealthySession
from scrapling.engines.toolbelt.custom import Response
from plyer import notification

DEBUG = False
NUM_CORES = os.cpu_count() or 1

# ===============================================================================
# UTILS
# ===============================================================================


def get_application_path():
    if getattr(sys, "frozen", False):  # if running as bundled executable
        # if ran using macos application
        path_components = sys.executable.split(os.sep)
        for i in range(len(path_components)-1, -1, -1):
            match =  re.search(r".*\.app$", path_components[i])
            if match:
                dir_index = path_components.index(match.group())
                return os.sep.join(path_components[:dir_index])
        if "sm-webscraper.app" in path_components:
            dir_index = path_components.index("sm-webscraper.app")
            return os.sep.join(path_components[:dir_index])
        # if run using raw executable
        else:
            return os.path.dirname(sys.executable)
    # if invoked via python
    else:
        return os.path.dirname(os.path.dirname(__file__))


def are_browsers_installed() -> bool:
    browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if not browser_path or not os.path.exists(browser_path):
        return False
    try:
        return any("chromium" in d.lower() for d in os.listdir(browser_path))
    except OSError:
        return False


def get_config_path():
    return os.path.join(get_application_path(), "config.toml")


def load_config():
    # TODO: remove default values here since they get added when a scraper runs anyways?
    default_config = {
        "axs_series": {
            "start_id": "",
            "stop_id": "",
        },
        "google_filter": {
            "keywords": "4 Pack,Promo,Crew Pack,Flash,Anniversary,BOGO,2 Pack,2 for 1,Discount",
            "after_date": "-3d",
        },
    }

    config_path = get_config_path()
    if not os.path.exists(config_path):
        return default_config

    try:
        return toml.load(config_path)
    except Exception:
        return default_config


def save_config(config):
    config_path = get_config_path()
    with open(config_path, "w") as f:
        toml.dump(config, f)


def parse_date(after_date: str) -> QDate:
    parsed_date = QDate.fromString(after_date, "yyyy-MM-dd")
    if parsed_date.isValid():
        return parsed_date

    current_date = QDate.currentDate()

    match = re.match(r"^(-?)(\d+)([md])$", after_date)
    if match:
        sign = match.group(1)
        value = int(match.group(2))
        unit = match.group(3)

        if sign == "-":
            if unit == "m":
                return current_date.addMonths(-value)
            elif unit == "d":
                return current_date.addDays(-value)
        else:
            if unit == "m":
                return current_date.addMonths(value)
            elif unit == "d":
                return current_date.addDays(value)

    return current_date


# ===============================================================================
# IMPLEMENTATION CLASSES
# ===============================================================================


class Webscraper:
    def __init__(self, num_concurrent_wins=NUM_CORES, log_callback=None):
        self.num_concurrent_wins = num_concurrent_wins
        self.failed_conn_urls = []
        self.log = log_callback or print

    async def _get_response(
        self, url: str, session: AsyncStealthySession
    ) -> Response | None:
        try:
            self.log(f"sending request to {url}")
            response = await session.fetch(url)
            self.log(f"received response from {url}")

            if url in self.failed_conn_urls:
                self.failed_conn_urls.remove(url)

            if self._is_captcha_page(response):
                self.log(
                    "Response contains captcha. Requiring manual captcha solution."
                )
                notification.notify(
                    title="Webscraper",
                    message="Manual CAPTCHA solution required",
                    app_name="Webscraper",
                )
                response, captcha_cookies = await self.manually_solve_captcha(url)
                await session.context.add_cookies(captcha_cookies)

            return response

        except Exception as err:
            self.log(f"error for {url}:")
            self.log(str(err))
            self.failed_conn_urls.append(url)
            return None

    async def get_responses(
        self, urls: list[str], cookies: list[dict] = []
    ) -> tuple[dict[str, Response], list[dict]]:
        # get html of each url
        #   - note that the result is in the same order as the passed-in list of coros
        async with AsyncStealthySession(
            headless=not DEBUG,
            real_chrome=False,
            block_webrtc=True,
            solve_cloudflare=True,
            timeout=60000,  # 60 seconds for Cloudflare challenges
            max_pages=self.num_concurrent_wins,
        ) as session:
            if cookies:
                await session.context.add_cookies(cookies)

            coros = [self._get_response(url, session) for url in urls]
            responses = await asyncio.gather(*coros)

            # retry failed connections up to 10 times
            url_index_map = {}
            for _ in range(10):
                if len(self.failed_conn_urls) == 0:
                    break
                if len(url_index_map) == 0:
                    url_index_map = {urls[i]: i for i in range(len(urls))}
                self.log(f"\nRetrying failed connections:")
                failed_conn_urls = self.failed_conn_urls
                print(failed_conn_urls)
                print(url_index_map)
                coros = [
                    self._get_response(url, session) for url in self.failed_conn_urls
                ]
                retry_responses = await asyncio.gather(*coros)
                for retry_idx, failed_conn_url in enumerate(failed_conn_urls):
                    idx = url_index_map[failed_conn_url]
                    responses[idx] = retry_responses[retry_idx]
                    print(retry_responses)

            session_cookies = await session.context.cookies()

        url_to_response = {}
        for i in range(len(urls)):
            if responses[i] is not None:
                url_to_response[urls[i]] = responses[i]

        return url_to_response, session_cookies

    @classmethod
    def _is_captcha_page(cls, response):
        return response.find("iframe", title="reCAPTCHA")

    # TODO: take in kwargs like page_action to pass into fetch call
    @classmethod
    async def manually_solve_captcha(cls, url):
        async with AsyncStealthySession(
            headless=False,
            real_chrome=False,
            block_webrtc=True,
            solve_cloudflare=True,
            timeout=60000,  # 60 seconds for Cloudflare challenges
        ) as session:
            # NOTE: This won't work if the site redirects to a different URL. A better method would be to somehow wait until _is_captcha_page returns False, but that is done within Python, not Javascript.
            async def wait_for_url_action(page):
                # await page.wait_for_function(f"() => console.log(decodeURI(window.location.href))", timeout=0)
                await page.wait_for_function(
                    f"() => decodeURI(window.location.href).includes('{url}')", timeout=0
                )
                await page.wait_for_load_state()

            response = await session.fetch(
                url, page_action=wait_for_url_action
            )
            # await session.fetch(url, wait_selector="h3")
            cookies = await session.context.cookies()

        return response, cookies


class AxsSeriesWebscraper(Webscraper):
    @classmethod
    def get_titles(cls, url_to_response: dict[str, Response], log_callback=None):
        url_to_title = {}
        log = log_callback or print

        log("Parsing for series titles...")
        for url, response in url_to_response.items():
            title_tag = response.find(
                "h1", lambda elem: elem.has_class("styles__SeriesTitle-sc-65abd048-1")
            )
            title = title_tag.get_all_text(strip=True) if title_tag else None
            url_to_title[url] = title

        return url_to_title

    @classmethod
    def generate_outfile(cls, start_id, stop_id):
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return os.path.join(
            get_application_path(),
            "output",
            f"axs-series_{now}_{start_id}-{stop_id}.csv",
        )

    @classmethod
    def run(
        cls,
        start_id: int,
        stop_id: int,
        outfile: str,
        num_concurrent_wins: int,
        log_callback=None,
    ):
        log = log_callback or print
        if outfile == "":
            outfile = cls.generate_outfile(start_id, stop_id)

        urls = [
            f"https://www.axs.com/series/{id}/" for id in range(start_id, stop_id + 1)
        ]

        # run scraper
        scraper = Webscraper(num_concurrent_wins, log_callback=log)
        url_to_response, _ = asyncio.run(scraper.get_responses(urls))
        url_to_title = cls.get_titles(url_to_response, log_callback=log)

        log(f"\nUnresolved failed connections: {scraper.failed_conn_urls}")
        log(f"\nSaving results")

        dirname = os.path.dirname(outfile)
        if dirname != "" and not os.path.exists(dirname):
            os.makedirs(dirname)

        with open(outfile, mode="w", encoding="utf-8") as file:
            file.write(f"URL,Title\n")
            for url, title in url_to_title.items():
                file.write(f"{url},{title}\n")

        log(f"\nResults stored in {outfile}")


class GoogleFilterWebscraper(Webscraper):
    @classmethod
    def get_event_title(cls, response: Response):
        title_tag = response.find(
            "h1", lambda elem: elem.has_class("styles__EventTitle-sc-768cdea1-7")
        )
        title = (
            title_tag.get_all_text(strip=True, separator=" ").replace(",", "")
            if title_tag
            else None
        )

        return title

    @classmethod
    def parse_page(cls, page_response: Response) -> dict[str, dict]:
        result = {}
        containers = page_response.find_all("div", class_="N54PNb BToiNc")

        for container in containers:
            link_tag = container.find("a", class_="zReHs")
            link = link_tag["href"] if link_tag else None

            link_title_tag = link_tag.find("h3") if link_tag else None
            link_title = (
                link_title_tag.get_all_text(strip=True, separator=" ").replace(",", "")
                if link_title_tag
                else None
            )

            link_info_span_tag = container.find("span", class_="YrbPuc")

            date_tag = link_info_span_tag.children.first if link_info_span_tag else None
            date = (
                date_tag.get_all_text(strip=True).replace(",", "") if date_tag else None
            )

            desc_tag = (
                link_info_span_tag.siblings.search(lambda sib: sib.tag == "span")
                if link_info_span_tag
                else None
            )
            desc = (
                desc_tag.get_all_text(strip=True, separator=" ").replace(",", "")
                if desc_tag
                else None
            )

            keyword_tags = desc_tag.find_all("em") if desc_tag else None
            keywords = (
                "|".join([keyword_tag.text for keyword_tag in keyword_tags])
                if keyword_tags
                else None
            )

            result[link] = {
                "link_title": link_title,
                "date": date,
                "keywords": keywords,
                "desc": desc,
            }

        return result

    @classmethod
    def generate_outfile(cls):
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return os.path.join(
            get_application_path(),
            "output",
            f"axs-google-filter_{now}.csv",
        )

    @classmethod
    def run(
        cls,
        keywords: list[str],
        after: str,
        outfile: str,
        num_concurrent_wins: int,
        log_callback=None,
    ):
        log = log_callback or print
        if outfile == "":
            outfile = cls.generate_outfile()

        keywords = [f'"{re.sub(r"\s+", "+", keyword)}"' for keyword in keywords]

        # construct query
        url = "https://www.google.com/search?q=site:www.axs.com/events"
        url += "+" + "+OR+".join(keywords)
        url += f"+after:{after}"

        scraper = Webscraper(num_concurrent_wins, log_callback=log)

        # fetch all pages using start parameter pagination
        event_url_to_info = {}

        start = 0
        cookies = []
        while True:
            page_url = f"{url}&start={start}"
            page_url_to_response, cookies = asyncio.run(
                scraper.get_responses([page_url], cookies)
            )

            page_event_url_to_info = cls.parse_page(page_url_to_response[page_url])

            if not page_event_url_to_info:
                log(f"No more results at start={start}. Stopping.")
                break

            event_url_to_info.update(page_event_url_to_info)
            log(
                f"Fetched page {start // 10 + 1} with {len(page_event_url_to_info)} results"
            )
            start += 10

        # add event title to info
        event_url_to_response, _ = asyncio.run(
            scraper.get_responses(list(event_url_to_info.keys()))
        )
        for url, response in event_url_to_response.items():
            event_url_to_info[url]["event_title"] = cls.get_event_title(response)

        dirname = os.path.dirname(outfile)
        if dirname != "" and not os.path.exists(dirname):
            os.makedirs(dirname)

        with open(outfile, mode="w", encoding="utf-8") as file:
            file.write(f"Event Title,Link Title,URL,Date,Keywords,Description\n")
            csv_writer = csv.writer(file)
            for url, info in event_url_to_info.items():
                csv_writer.writerow(
                    [
                        info["event_title"],
                        info["link_title"],
                        url,
                        info["date"],
                        info["keywords"],
                        info["desc"],
                    ]
                )


# ===============================================================================
# GUI
# ===============================================================================


class ScraperWorker(QObject):
    log_signal = Signal(str)
    finished_signal = Signal()

    def __init__(self, scraper_cls, log_callback=None, **kwargs):
        super().__init__()
        self.scraper_cls = scraper_cls
        self.log_callback = log_callback
        self.kwargs = kwargs

    def run(self):
        log = self.log_callback or (lambda msg: self.log_signal.emit(msg))
        self.scraper_cls.run(log_callback=log, **self.kwargs)
        self.finished_signal.emit()


class LoggerWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.text)
        self.setLayout(layout)

    def log(self, message: str):
        self.text.appendPlainText(message)


class FileSelectorWidget(QWidget):
    def __init__(self):
        super().__init__()

        provider = QFileIconProvider()

        self.line_edit = QLineEdit(self)
        self.push_button = QPushButton(
            provider.icon(QFileIconProvider.IconType.File), "", self
        )
        self.push_button.clicked.connect(self.select)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.line_edit)
        layout.addWidget(self.push_button)
        self.setLayout(layout)

    def select(self):
        selected, _ = QFileDialog.getSaveFileName(self, "Save to", "")
        if selected:
            self.line_edit.setText(selected)


class DateSelectorWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.date_edit = QDateEdit(self)
        self.today_push_button = QPushButton("Today", self)
        self.month_inc_push_button = QPushButton("+1m", self)
        self.month_dec_push_button = QPushButton("-1m", self)
        self.day_inc_push_button = QPushButton("+1d", self)
        self.day_dec_push_button = QPushButton("-1d", self)

        self.today_push_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.month_inc_push_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.month_dec_push_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.day_inc_push_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )
        self.day_dec_push_button.setSizePolicy(
            QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed
        )

        self.today_push_button.pressed.connect(
            lambda: self.date_edit.setDate(QDate.currentDate())
        )
        self.month_inc_push_button.pressed.connect(
            lambda: self.date_edit.setDate(self.date_edit.date().addMonths(1))
        )
        self.month_dec_push_button.pressed.connect(
            lambda: self.date_edit.setDate(self.date_edit.date().addMonths(-1))
        )
        self.day_inc_push_button.pressed.connect(
            lambda: self.date_edit.setDate(self.date_edit.date().addDays(1))
        )
        self.day_dec_push_button.pressed.connect(
            lambda: self.date_edit.setDate(self.date_edit.date().addDays(-1))
        )

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.date_edit)
        layout.addWidget(self.today_push_button)
        layout.addWidget(self.month_inc_push_button)
        layout.addWidget(self.month_dec_push_button)
        layout.addWidget(self.day_inc_push_button)
        layout.addWidget(self.day_dec_push_button)
        self.setLayout(layout)


class AxsSeriesWebscraperWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.is_running = False
        self.worker_thread = None
        self.worker = None

        config = load_config()

        # Widgets
        self.start_id_line_edit = QLineEdit(self)
        self.stop_id_line_edit = QLineEdit(self)
        self.concurrent_windows_spin_box = QSpinBox(self)
        self.outfile_widget = FileSelectorWidget()
        self.run_push_button = QPushButton("Run", self)
        self.logger_widget = LoggerWidget()

        # Widget configuration
        self.concurrent_windows_spin_box.setMinimum(1)
        self.concurrent_windows_spin_box.setMaximum(NUM_CORES)
        self.concurrent_windows_spin_box.setValue(NUM_CORES)
        self.run_push_button.clicked.connect(self.run)

        if config["axs_series"]["start_id"]:
            self.start_id_line_edit.setText(str(config["axs_series"]["start_id"]))
        if config["axs_series"]["stop_id"]:
            self.stop_id_line_edit.setText(str(config["axs_series"]["stop_id"]))

        # Layouts
        form_layout = QFormLayout()
        form_layout.addRow("Start ID:", self.start_id_line_edit)
        form_layout.addRow("Stop ID:", self.stop_id_line_edit)
        form_layout.addRow("Windows:", self.concurrent_windows_spin_box)
        form_layout.addRow("Save to:", self.outfile_widget)

        main_layout = QVBoxLayout()
        main_layout.addLayout(form_layout)
        main_layout.addWidget(self.run_push_button)
        main_layout.addWidget(self.logger_widget)
        self.setLayout(main_layout)

    @property
    def start_id(self):
        if self.start_id_line_edit.text() == "":
            return -1
        return int(self.start_id_line_edit.text())

    @property
    def stop_id(self):
        if self.stop_id_line_edit.text() == "":
            return -1
        return int(self.stop_id_line_edit.text())

    @property
    def concurrent_windows(self):
        return self.concurrent_windows_spin_box.value()

    @property
    def outfile(self):
        return self.outfile_widget.line_edit.text()

    def _on_finished(self):
        elapsed = time.time() - self.start_time
        self.logger_widget.log("\nFinished scrape")
        self.logger_widget.log(f"Time elapsed: {elapsed:.2f}s")
        self.is_running = False
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None

    def run(self):
        if self.start_id == -1:
            self.logger_widget.log("Need to provide a start_id")
            return

        if self.stop_id == -1:
            self.logger_widget.log("Need to provide a stop_id")
            return

        config = load_config()
        config["axs_series"]["start_id"] = str(self.start_id)
        config["axs_series"]["stop_id"] = str(self.stop_id)
        save_config(config)

        if not self.is_running:
            self.is_running = True
            self.start_time = time.time()

            self.logger_widget.log("\nStarting scrape:\n")

            self.worker_thread = QThread()
            self.worker = ScraperWorker(
                AxsSeriesWebscraper,
                start_id=self.start_id,
                stop_id=self.stop_id,
                outfile=self.outfile,
                num_concurrent_wins=self.concurrent_windows,
            )
            self.worker.moveToThread(self.worker_thread)
            self.worker.log_signal.connect(self.logger_widget.log)
            self.worker.finished_signal.connect(self._on_finished)
            self.worker_thread.started.connect(self.worker.run)
            self.worker_thread.start()
        else:
            self.logger_widget.log("\nScrape already in progress\n")


class GoogleFilterWebscraperWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.is_running = False
        self.worker_thread = None
        self.worker = None

        config = load_config()

        self.keywords_line_edit = QLineEdit(self)
        self.oldest_date_edit = DateSelectorWidget()
        self.concurrent_windows_spin_box = QSpinBox(self)
        self.outfile_widget = FileSelectorWidget()
        self.run_push_button = QPushButton("Run", self)
        self.logger_widget = LoggerWidget()

        self.concurrent_windows_spin_box.setMinimum(1)
        self.concurrent_windows_spin_box.setMaximum(NUM_CORES)
        self.concurrent_windows_spin_box.setValue(NUM_CORES)

        if config["google_filter"]["keywords"]:
            self.keywords_line_edit.setText(config["google_filter"]["keywords"])
        else:
            self.keywords_line_edit.setText(
                "4 Pack,Promo,Crew Pack,Flash,Anniversary,BOGO,2 Pack,2 for 1,Discount"
            )

        if config["google_filter"]["after_date"]:
            self.oldest_date_edit.date_edit.setDate(
                parse_date(config["google_filter"]["after_date"])
            )
        else:
            self.oldest_date_edit.date_edit.setDate(QDate.currentDate().addMonths(-1))

        self.run_push_button.clicked.connect(self.run)

        form_layout = QFormLayout()
        form_layout.addRow("Keywords:", self.keywords_line_edit)
        form_layout.addRow("Newer than:", self.oldest_date_edit)
        form_layout.addRow("Windows:", self.concurrent_windows_spin_box)
        form_layout.addRow("Save to:", self.outfile_widget)

        main_layout = QVBoxLayout()
        main_layout.addLayout(form_layout)
        main_layout.addWidget(self.run_push_button)
        main_layout.addWidget(self.logger_widget)
        self.setLayout(main_layout)

    @property
    def keywords(self):
        return self.keywords_line_edit.text().split(",")

    @property
    def after_date(self):
        return self.oldest_date_edit.date_edit.date().toString(Qt.ISODate)

    @property
    def concurrent_windows(self):
        return self.concurrent_windows_spin_box.value()

    @property
    def outfile(self):
        return self.outfile_widget.line_edit.text()

    def _on_finished(self):
        elapsed = time.time() - self.start_time
        self.logger_widget.log("\nFinished scrape")
        self.logger_widget.log(f"Time elapsed: {elapsed:.2f}s")
        self.is_running = False
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None
            self.worker = None

    def run(self):
        config = load_config()
        config["google_filter"]["keywords"] = self.keywords_line_edit.text()
        save_config(config)

        if not self.is_running:
            self.is_running = True
            self.start_time = time.time()

            self.logger_widget.log("\nStarting scrape:\n")

            self.worker_thread = QThread()
            self.worker = ScraperWorker(
                GoogleFilterWebscraper,
                keywords=self.keywords,
                after=self.after_date,
                outfile=self.outfile,
                num_concurrent_wins=self.concurrent_windows,
            )
            self.worker.moveToThread(self.worker_thread)
            self.worker.log_signal.connect(self.logger_widget.log)
            self.worker.finished_signal.connect(self._on_finished)
            self.worker_thread.started.connect(self.worker.run)
            self.worker_thread.start()
        else:
            self.logger_widget.log("\nScrape already in progress\n")


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # Sidebar
        self.sidebar = QListWidget()
        self.sidebar.addItem("AXS Series")
        self.sidebar.addItem("AXS Google Filter")
        self.sidebar.setFixedWidth(150)

        # Stack
        self.stack = QStackedWidget()
        self.stack.addWidget(AxsSeriesWebscraperWidget())  # index 0
        self.stack.addWidget(GoogleFilterWebscraperWidget())  # index 1

        # Connect sidebar to stack
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)

        # Layout
        layout = QHBoxLayout()
        layout.addWidget(self.sidebar)
        layout.addWidget(self.stack)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)

        self.sidebar.setCurrentRow(0)  # default page


class BrowserInstallWorker(QThread):
    finished = Signal(bool)

    def run(self):
        try:
            # HACK: taken from https://github.com/microsoft/playwright-python/blob/release-1.58/playwright/__main__.py
            #  - this is what the CLI invokes (see project.scripts section of https://github.com/microsoft/playwright-python/blob/release-1.58/pyproject.toml)
            from playwright._impl._driver import (
                compute_driver_executable,
                get_driver_env,
            )

            driver_executable, driver_cli = compute_driver_executable()
            subprocess.run(
                [driver_executable, driver_cli, "install", "chromium"],
                env=get_driver_env(),
            )
            self.finished.emit(True)
        except Exception as e:
            print(e)
            self.finished.emit(False)


class BrowserInstallDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Installing Browser Drivers")
        self.setMinimumWidth(400)
        self.setModal(True)

        layout = QVBoxLayout(self)

        label = QLabel("Installing browser drivers...")
        label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(label)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)

        self.status_label = QLabel()
        layout.addWidget(self.status_label)

        self.thread = BrowserInstallWorker(self)
        self.thread.finished.connect(self._on_install_finished)
        self.thread.start()

    def _on_install_finished(self, success: bool):
        if success:
            self.status_label.setText("Installation complete!")
        else:
            self.status_label.setText("Installation failed. Please try again.")
        self.accept()


# ===============================================================================
# MAIN
# ===============================================================================


async def test_stealth():
    print("Navigating to bot.sannysoft.com...")
    async with AsyncStealthySession(
        headless=False,
        real_chrome=False,
        block_webrtc=True,
        solve_cloudflare=True,
        timeout=60000,  # 60 seconds for Cloudflare challenges
        max_pages=1,
    ) as session:
        await session.fetch("https://bot.sannysoft.com/", wait=1000000)


def parse_args():
    parser = argparse.ArgumentParser(description="AXS Webscraper")
    parser.add_argument(
        "--test-stealth",
        action="store_true",
        help="Test browser stealth on bot.sannysoft.com and pause for inspection",
    )
    return parser.parse_args()


def main():
    if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
        os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
            get_application_path(), "browser_drivers"
        )
    print(os.environ["PLAYWRIGHT_BROWSERS_PATH"])

    args = parse_args()

    if args.test_stealth:
        asyncio.run(test_stealth())
    else:
        app = QApplication([])
        app.setStyle("Fusion")

        if not are_browsers_installed():
            dialog = BrowserInstallDialog()
            dialog.exec()

        window = MainWindow()
        window.show()
        app.exec()


if __name__ == "__main__":
    main()
