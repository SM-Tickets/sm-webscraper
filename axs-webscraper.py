import os
import sys
import time
import datetime
import threading
from abc import abstractmethod

import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk

from PySide6.QtCore import Qt, QDate
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
    QSizePolicy
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
    def __init__(self, num_concurrent_wins):
        self.semaphore = asyncio.Semaphore(int(num_concurrent_wins))
        self.failed_connections = []

    async def _start_browser(self):
        playwright = await async_playwright().start()
        if DEBUG:
            browser = await playwright.chromium.launch(headless=False, slow_mo=10000)
        else:
            browser = await playwright.chromium.launch(headless=True)
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
            user_agent = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.36"
            # user_agent = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/144.0.0.0 Safari/537.36"
            context = await browser.new_context(user_agent=user_agent)
            page = await context.new_page()
            try:
                print(f"sending request to {url}")
                await page.goto(url)
                if DEBUG:
                    await page.pause()

            except Exception as err:
                print(f"error for {url}:")
                print(err)
                self.failed_connections.append(url)
                await page.close()
                return {url: ""}

            print(f"received response from {url}")

            if url in self.failed_connections:
                self.failed_connections.remove(url)
            html = await page.content()
            await context.close()

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
            print(f"\nRetrying failed connections:")
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
    def get_titles(cls, urls_to_htmls: dict[str, str]):
        """Parse the html for the title of the series

        Returns:
            dict{str: str|None}: Mapping of urls to their AXS event titles
        """
        titles = {}

        print("Parsing responses...")
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
    def run(cls, start_id: int, stop_id: int, outfile: str, num_concurrent_wins: int):
        if outfile == "":
            outfile = cls.generate_outfile(start_id, stop_id)

        urls = [
            f"https://www.axs.com/series/{id}/" for id in range(start_id, stop_id + 1)
        ]

        # run scraper
        scraper = Webscraper(num_concurrent_wins)
        urls_to_htmls = asyncio.run(scraper.get_htmls(urls))
        urls_to_titles = cls.get_titles(urls_to_htmls)

        print(f"\nUnresolved failed connections: {scraper.failed_connections}")
        print(f"\nSaving results")

        dirname = os.path.dirname(outfile)
        if dirname != "" and not os.path.exists(dirname):
            os.makedirs(dirname)

        with open(outfile, mode="w", encoding="utf-8") as file:
            file.write(f"URL,Title\n")
            for url, title in urls_to_titles.items():
                file.write(f"{url},{title}\n")

        print(f"\nResults stored in {outfile}")


class GoogleFilterWebscraper(Webscraper):

    @classmethod
    def get_links(cls, urls_to_htmls: dict[str, str]):
        all_links = []

        print("Parsing responses...")
        for url, html in urls_to_htmls.items():
            html = BeautifulSoup(html, "html.parser")
            links = (
                html.find_all("a", class_="zReHs")
            )
            if len(links) > 0:
                all_links.extend([link["href"] for link in links])

        return all_links

    @classmethod
    def generate_outfile(cls):
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return os.path.join(
            get_application_path(),
            "output",
            f"axs-google-filter_{now}.csv",
        )

    @classmethod
    def run(cls, keywords: list[str], after: str, outfile: str, num_concurrent_wins: int):
        if outfile == "":
            outfile = cls.generate_outfile()

        keywords = [ f'"{keyword}"' for keyword in keywords ]
        # construct query
        url = "https://www.google.com/search?q=site:www.axs.com"
        url += "+" + "+".join(keywords)
        url += f"+after:{after}"

        urls = [url]

        scraper = Webscraper(num_concurrent_wins)
        urls_to_htmls = asyncio.run(scraper.get_htmls(urls))
        links = cls.get_links(urls_to_htmls)

        with open(outfile, mode="w", encoding="utf-8") as file:
            file.write(f"URL,Title\n")
            for link in links:
                file.write(f"{link}\n")


# ===============================================================================
# GUI
# ===============================================================================

class LoggerWidget(QWidget):
    def __init__(self):
        super().__init__()

        text = QPlainTextEdit()
        text.setReadOnly(True)
        text.insertPlainText("hello world")

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(text)
        self.setLayout(layout)

class FileSelectorWidget(QWidget):
    def __init__(self):
        super().__init__()

        provider = QFileIconProvider()

        self.line_edit = QLineEdit(self)
        self.push_button = QPushButton(provider.icon(QFileIconProvider.IconType.File), "", self)
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

        self.today_push_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.month_inc_push_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.month_dec_push_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.day_inc_push_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.day_dec_push_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

        self.today_push_button.pressed.connect(lambda: self.date_edit.setDate(QDate.currentDate()))
        self.month_inc_push_button.pressed.connect(lambda: self.date_edit.setDate(self.date_edit.date().addMonths(1)))
        self.month_dec_push_button.pressed.connect(lambda: self.date_edit.setDate(self.date_edit.date().addMonths(-1)))
        self.day_inc_push_button.pressed.connect(lambda: self.date_edit.setDate(self.date_edit.date().addDays(1)))
        self.day_dec_push_button.pressed.connect(lambda: self.date_edit.setDate(self.date_edit.date().addDays(-1)))

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

    def run(self):
        if self.start_id == -1:
            print("Need to provide a start_id")
            return

        if self.stop_id == -1:
            print("Need to provide a stop_id")
            return

        if not self.is_running:
            self.is_running = True

            # ----- Benchmark start ----- #
            start_time = time.time()

            print("\nStarting scrape:\n")

            AxsSeriesWebscraper.run(
                self.start_id, self.stop_id, self.outfile, self.concurrent_windows
            )

            print("\nFinished scrape")

            print(
                f"Time elapsed: {time.time() - start_time:.2f}s"
            )
            # ----- Benchmark stop ----- #

            self.is_running = False
        else:
            print("\nScrape already in progress\n")


class GoogleFilterWebscraperWidget(QWidget):
    def __init__(self):
        super().__init__()

        self.is_running = False

        self.keywords_line_edit = QLineEdit(self)
        self.oldest_date_edit = DateSelectorWidget()
        self.concurrent_windows_spin_box = QSpinBox(self)
        self.outfile_widget = FileSelectorWidget()
        self.run_push_button = QPushButton("Run", self)

        self.keywords_line_edit.setText("CODE,PROMO")
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
        main_layout.addWidget(LoggerWidget())
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

    def run(self):
        if not self.is_running:
            self.is_running = True

            # ----- Benchmark start ----- #
            start_time = time.time()

            print("\nStarting scrape:\n")

            GoogleFilterWebscraper.run(self.keywords, self.after_date, self.outfile, self.concurrent_windows)

            print("\nFinished scrape")

            print(
                f"Time elapsed: {time.time() - start_time:.2f}s"
            )
            # ----- Benchmark stop ----- #

            self.is_running = False
        else:
            print("\nScrape already in progress\n")


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


def main():
    app = QApplication([])
    window = MainWindow()
    window.show()
    app.exec()


if __name__ == "__main__":
    main()
