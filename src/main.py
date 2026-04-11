import os
import sys
import time
import datetime
import random
import re
import argparse
import threading
from abc import abstractmethod

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
)

import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

DEBUG = True
NUM_CORES = os.cpu_count() or 1

# ===============================================================================
# UTILS
# ===============================================================================


def get_application_path():
    if getattr(sys, "frozen", False):  # if running as bundled executable
        # if ran using macos application
        path_components = sys.executable.split(os.sep)
        if "axs-webscraper.app" in path_components:
            dir_index = path_components.index("axs-webscraper.app")
            return os.sep.join(path_components[:dir_index])
        # if run using raw executable
        else:
            return os.path.dirname(sys.executable)
    # if invoked via python
    else:
        return os.path.dirname(__file__)


if not os.environ.get("PLAYWRIGHT_BROWSERS_PATH"):
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
        get_application_path(), "browser_drivers"
    )
print(os.environ["PLAYWRIGHT_BROWSERS_PATH"])


# ===============================================================================
# IMPLEMENTATION CLASSES
# ===============================================================================


class Webscraper:
    def __init__(self, num_concurrent_wins, log_callback=None):
        self.semaphore = asyncio.Semaphore(int(num_concurrent_wins))
        self.failed_connections = []
        self.log_callback = log_callback or print

    @staticmethod
    def _get_browser_args():
        return [
            "--disable-blink-features=AutomationControlled",
            "--disable-features=IsolateOrigins,site-per-process",
            "--no-sandbox",
            "--disable-setuid-sandbox",
            "--disable-dev-shm-usage",
            "--disable-accelerated-2d-canvas",
            "--no-first-run",
            "--no-zygote",
            "--disable-gpu",
        ]

    async def _start_browser(self):
        playwright = await async_playwright().start()
        if DEBUG:
            browser = await playwright.chromium.launch(
                headless=False,
                slow_mo=10000,
                args=self._get_browser_args(),
            )
        else:
            browser = await playwright.chromium.launch(
                headless=True,
                args=self._get_browser_args(),
            )
        return browser, playwright

    async def _close_browser(self, playwright, browser):
        await browser.close()
        await playwright.stop()

    async def _get_html(self, url, browser):
        """Get html for a url

        Args:
          url (str): url to be requested

        Returns:
            dict{str: str}: Mapping of url to raw html
        """
        async with self.semaphore:
            user_agent = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
            )
            stealth_script = """
                (function() {
                    function createPluginArray() {
                        var plugins = [
                            { name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer', length: 0 },
                            { name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', length: 1 },
                            { name: 'Native Client', description: '', filename: 'internal-nacl-plugin', length: 2 }
                        ];
                        plugins.length = 3;
                        plugins.item = function(i) { return this[i] || null; };
                        plugins.namedItem = function(name) { return null; };
                        plugins.refresh = function() {};
                        return plugins;
                    }
                    Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                    Object.defineProperty(navigator, 'plugins', { get: () => createPluginArray() });
                    Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
                })();
            """
            context = await browser.new_context(
                user_agent=user_agent,
                locale="en-US",
                timezone_id="America/New_York",
                viewport={"width": 1920, "height": 1080},
                permissions=["geolocation"],
            )
            page = await context.new_page()
            await page.add_init_script(stealth_script)
            try:
                self.log_callback(f"sending request to {url}")
                await page.goto(url)
                if DEBUG:
                    await page.pause()

            except Exception as err:
                self.log_callback(f"error for {url}:")
                self.log_callback(str(err))
                self.failed_connections.append(url)
                await page.close()
                return {url: ""}

            self.log_callback(f"received response from {url}")

            if url in self.failed_connections:
                self.failed_connections.remove(url)
            html = await page.content()
            await context.close()

            await asyncio.sleep(random.uniform(1, 3))

        return {url: html}

    async def get_htmls(self, urls):
        """Get html for urls

        Returns:
            dict{str: BeautifulSoup.BeautifulSoup}: Mapping of urls to BeautifulSoup objects representing the url's corresponding HTML
        """

        browser, playwright = await self._start_browser()

        coros = [self._get_html(url, browser) for url in urls]

        # get html of each url
        #   - note that the result is in the same order as the passed-in list of coros
        urls_to_raw_htmls = await asyncio.gather(*coros)

        # retry failed connections up to 10 times
        for _ in range(10):
            if len(self.failed_connections) == 0:
                break
            self.log_callback(f"\nRetrying failed connections:")
            coros = [self._get_html(url, browser) for url in self.failed_connections]
            urls_to_raw_htmls.extend(await asyncio.gather(*coros))

        await self._close_browser(playwright, browser)

        # return {url: BeautifulSoup(html, 'html.parser') for url_to_raw_html in urls_to_raw_htmls for url, html in url_to_raw_html.items()}
        return {
            url: html
            for url_to_raw_html in urls_to_raw_htmls
            for url, html in url_to_raw_html.items()
        }


class AxsSeriesWebscraper(Webscraper):
    @classmethod
    def get_titles(cls, urls_to_htmls: dict[str, str], log_callback=None):
        """Parse the html for the title of the series

        Returns:
            dict{str: str|None}: Mapping of urls to their AXS event titles
        """
        titles = {}
        log = log_callback or print

        log("Parsing responses...")
        for url, html in urls_to_htmls.items():
            html = BeautifulSoup(html, "html.parser")
            title = (
                html.find("h1", class_="series-header__main-title")
                or html.find("div", class_="styles__SeriesName-sc-a987fbc9-2")
                or html.find("div", class_="styles__SeriesName-sc-7ec0aa62-2")
                or html.find("h1", class_="styles__SeriesTitle-sc-22d8e9ab-1")
                or html.find("h1", class_="styles__SeriesTitle-sc-3de48f0c-1")
                or html.find("h1", class_="styles__SeriesTitle-sc-65abd048-1")
            )
            if title:
                title = title.text.strip()
            titles[url] = title

        return titles

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
        urls_to_htmls = asyncio.run(scraper.get_htmls(urls))
        urls_to_titles = cls.get_titles(urls_to_htmls, log_callback=log)

        log(f"\nUnresolved failed connections: {scraper.failed_connections}")
        log(f"\nSaving results")

        dirname = os.path.dirname(outfile)
        if dirname != "" and not os.path.exists(dirname):
            os.makedirs(dirname)

        with open(outfile, mode="w", encoding="utf-8") as file:
            file.write(f"URL,Title\n")
            for url, title in urls_to_titles.items():
                file.write(f"{url},{title}\n")

        log(f"\nResults stored in {outfile}")


class GoogleFilterWebscraper(Webscraper):
    @classmethod
    def get_axs_event_title(cls, event_html, log_callback):
        log = log_callback or print

        soup = BeautifulSoup(event_html, "html.parser")
        h1_tag = (
            soup.find("h1", class_="styles__EventTitle-sc-768cdea1-7 eNioSo")
        )
        title = h1_tag.get_text(strip=True).replace(",", "") if h1_tag else None

        return title

    @classmethod
    def parse_page(cls, html: str) -> dict:
        result = {}
        soup = BeautifulSoup(html, "html.parser")
        containers = soup.find_all("div", class_="N54PNb BToiNc")

        for container in containers:
            link_tag = container.find("a", class_="zReHs")
            link = link_tag["href"] if link_tag else None

            h3_tag = link_tag.find("h3")
            title = h3_tag.get_text(strip=True).replace(",", "") if h3_tag else None

            span_tag = container.find("span", class_="YrbPuc")
            date = (
                span_tag.find("span").get_text(strip=True).replace(",", "")
                if span_tag
                else None
            )

            desc_tag = span_tag.find_next_sibling("span") if span_tag else None
            desc = desc_tag.get_text(strip=True).replace(",", "") if desc_tag else None

            result[link] = {"title": title, "date": date, "desc": desc}

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
        event_urls_to_info = {}
        start = 0
        log(f"Fetching results starting from page 0...")

        while True:
            page_url = f"{url}&start={start}"
            page_url_to_html = asyncio.run(scraper.get_htmls([page_url]))
            page_html = page_url_to_html[page_url]

            page_urls_to_info = cls.parse_page(page_html)

            if not page_urls_to_info:
                log(f"No more results at start={start}. Stopping.")
                break

            event_urls_to_info.update(page_urls_to_info)
            log(f"Fetched page (start={start}) with {len(page_urls_to_info)} results")
            start += 10

        event_urls_to_htmls = asyncio.run(scraper.get_htmls(event_urls_to_info.keys()))
        for url, html in event_urls_to_htmls.items():
            event_urls_to_info[url]["event_title"] = cls.get_axs_event_title(html, log_callback)

        dirname = os.path.dirname(outfile)
        if dirname != "" and not os.path.exists(dirname):
            os.makedirs(dirname)

        with open(outfile, mode="w", encoding="utf-8") as file:
            file.write(f"Event Title,Title,URL,Date,Description\n")
            for url, info in event_urls_to_info.items():
                file.write(
                    f"{info['event_title']},{info['title']},{url},{info['date']},{info['desc']}\n"
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

        self.keywords_line_edit = QLineEdit(self)
        self.oldest_date_edit = DateSelectorWidget()
        self.concurrent_windows_spin_box = QSpinBox(self)
        self.outfile_widget = FileSelectorWidget()
        self.run_push_button = QPushButton("Run", self)
        self.logger_widget = LoggerWidget()

        self.keywords_line_edit.setText(
            "4 Pack,Promo,Crew Pack,Flash,Anniversary,BOGO,2 Pack,2 for 1,Discount"
        )
        self.concurrent_windows_spin_box.setMinimum(1)
        self.concurrent_windows_spin_box.setMaximum(NUM_CORES)
        self.concurrent_windows_spin_box.setValue(NUM_CORES)
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


async def test_stealth():
    """Launch browser and navigate to bot.sannysoft.com for inspection."""
    stealth_script = """
        (function() {
            function createPluginArray() {
                var plugins = [
                    { name: 'Chrome PDF Plugin', description: 'Portable Document Format', filename: 'internal-pdf-viewer', length: 0 },
                    { name: 'Chrome PDF Viewer', description: '', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', length: 1 },
                    { name: 'Native Client', description: '', filename: 'internal-nacl-plugin', length: 2 }
                ];
                plugins.length = 3;
                plugins.item = function(i) { return this[i] || null; };
                plugins.namedItem = function(name) { return null; };
                plugins.refresh = function() {};
                return plugins;
            }
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => createPluginArray() });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
        })();
    """
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=False)
    context = await browser.new_context(
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="America/New_York",
        viewport={"width": 1920, "height": 1080},
    )
    context.add_init_script(stealth_script)
    page = await context.new_page()
    await page.add_init_script(stealth_script)

    print("Navigating to bot.sannysoft.com...")
    await page.goto("https://bot.sannysoft.com/")
    print("Page loaded. Opening Playwright inspector for inspection...")
    await page.pause()

    await browser.close()
    await playwright.stop()


def parse_args():
    parser = argparse.ArgumentParser(description="AXS Webscraper")
    parser.add_argument(
        "--test-stealth",
        action="store_true",
        help="Test browser stealth on bot.sannysoft.com and pause for inspection",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    if args.test_stealth:
        asyncio.run(test_stealth())
    else:
        app = QApplication([])
        app.setStyle("Fusion")
        window = MainWindow()
        window.show()
        app.exec()


if __name__ == "__main__":
    main()
