import os
import sys
import time
import datetime
import threading

import tkinter as tk
from tkinter import filedialog
from PIL import Image, ImageTk

import asyncio
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup


#===============================================================================
# UTILS
#===============================================================================

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


if not os.environ["PLAYWRIGHT_BROWSERS_PATH"]:
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = os.path.join(
        get_application_path(), "browser_drivers"
    )
print(os.environ["PLAYWRIGHT_BROWSERS_PATH"])


#===============================================================================
# IMPLEMENTATION CLASSES
#===============================================================================

class Webscraper:
    def __init__(self, n_concurrent_windows):
        self.semaphore = asyncio.Semaphore(int(n_concurrent_windows))
        self.failed_connections = []

    async def _start_browser(self):
        playwright = await async_playwright().start()
        browser = await playwright.chromium.launch(headless=True)
        # browser = await playwright.chromium.launch(headless=False, slow_mo=10000)
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
            context = await browser.new_context(user_agent=user_agent)
            page = await context.new_page()
            try:
                print(f"sending request to {url}")
                await page.goto(url)

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
        urls_to_raw_htmls = await asyncio.gather(
            *coros
        )

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
    def _get_titles(cls, urls_to_htmls: dict[str, str]):
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
    def _generate_outfile(cls, start_id, stop_id):
        now = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        return os.path.join(
            get_application_path(),
            "output",
            f"{now}_{start_id}-{stop_id}.csv",
        )

    @classmethod
    def run(cls, start_id: int, stop_id: int, outfile: str, n_concurrent_windows: int):
        if outfile == "":
            outfile = cls._generate_outfile(start_id, stop_id)

        urls = [f"https://www.axs.com/series/{id}/" for id in range(start_id, stop_id + 1)]

        # run scraper
        scraper = Webscraper(n_concurrent_windows)
        urls_to_htmls = asyncio.run(scraper.get_htmls(urls))
        urls_to_titles = cls._get_titles(urls_to_htmls)
        # print(f"\n{self.urls_to_titles}")

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
    def __init__(self):
        ...


#===============================================================================
# GUI
#===============================================================================

class AxsWebscraperGui:
    def __init__(self): ...


class AxsGui:
    def __init__(self):
        self.is_running = False

        # ROOT
        self.root = tk.Tk()
        self.root.title("AXS Webscraper")
        self.root.geometry("500x600")

        # START ID
        self.start_id_label = tk.Label(self.root, text="*Start ID:")
        self.start_id_label.pack()
        self.start_id_entry = tk.Entry(self.root)
        self.start_id_entry.pack()

        # STOP ID
        self.stop_id_label = tk.Label(self.root, text="*Stop ID:")
        self.stop_id_label.pack(pady=(10, 0))
        self.stop_id_entry = tk.Entry(self.root)
        self.stop_id_entry.pack()

        # NUMBER OF CONCURRENT WINDOWS
        self.concurrent_windows_label = tk.Label(
            self.root, text="*No. of concurrent windows:"
        )
        self.concurrent_windows_label.pack(pady=(10, 0))
        self.concurrent_windows_entry = tk.Entry(self.root)
        self.concurrent_windows_entry.pack()
        default_concurrent_windows = "5"
        self.concurrent_windows_entry.insert(0, default_concurrent_windows)

        # FILE SELECTION
        self.filename_label = tk.Label(self.root, text="Filename:")
        self.filename_label.pack(pady=(10, 0))

        self.filename_frame = tk.Frame(self.root)
        self.filename_frame.pack(pady=5)

        self.filename_entry = tk.Entry(self.filename_frame, width=30)
        self.filename_entry.pack(side=tk.LEFT, expand=True, fill=tk.X)

        self.file_img = Image.open(
            self.get_asset_path(os.path.join("assets", "file.png"))
        )
        width, height = self.file_img.size
        self.file_img_resized = self.file_img.resize((width // 7, height // 7))
        self.file_img_tk = ImageTk.PhotoImage(self.file_img_resized)

        self.select_folder_button = tk.Button(
            self.filename_frame, image=self.file_img_tk, command=self._select_folder
        )
        # self.select_folder_button = tk.Button(self.filename_frame, image=self.file_img_tk, command=lambda: print("foo"))
        self.select_folder_button.pack(side=tk.RIGHT, ipadx=2, ipady=2)

        # RUN
        self.run_button = tk.Button(
            self.root,
            text="Run",
            command=lambda: threading.Thread(target=self.scrape, daemon=True).start(),
        )
        self.run_button.pack(pady=(10, 0))

        # OUTPUT WINDOW
        self.output_label = tk.Label(self.root, text="Output:")
        self.output_label.pack(pady=(30, 0))
        self.output_text = tk.Text(self.root, height=20, width=50, state=tk.DISABLED)
        self.output_text.pack(expand=True, fill=tk.X)

        self._connect_output_to_tk_text_widget()

    def get_asset_path(self, filename):
        if getattr(sys, "frozen", False):  # if running as bundled executable
            base_path = (
                sys._MEIPASS
            )  # pyinstaller temporary folder for bundled files (https://stackoverflow.com/questions/51060894/adding-a-data-file-in-pyinstaller-using-the-onefile-option)
        else:
            base_path = os.path.abspath(".")

        return os.path.join(base_path, filename)

    def _connect_output_to_tk_text_widget(self, stdout=True, stderr=True):
        class OutputRedirector:
            def __init__(self, text_widget, fd):
                self.text_widget = text_widget
                self.fd = fd

            def write(self, message):
                initial_state = self.text_widget["state"]
                self.text_widget["state"] = tk.NORMAL
                self.text_widget.insert(tk.END, message)
                self.text_widget.see(tk.END)  # scroll to the end
                self.text_widget["state"] = initial_state
                if (
                    self.fd == 1 and sys.__stdout__ != None
                ):  # if connected to stdout (not true when run from pyinstaller executable that was built with --noconsole)
                    sys.__stdout__.write(message)  # write to original stdout
                if (
                    self.fd == 2 and sys.__stderr__ != None
                ):  # if connected to stderr (not true when run from pyinstaller executable that was built with --noconsole)
                    sys.__stderr__.write(message)  # write to original stdout

            def flush(self):
                pass

        if stdout:
            sys.stdout = OutputRedirector(self.output_text, 1)
        if stderr:
            sys.stderr = OutputRedirector(self.output_text, 2)

    def _select_folder(self):
        folder_selected = filedialog.asksaveasfilename()
        if folder_selected:
            self.filename_entry.delete(0, tk.END)
            self.filename_entry.insert(0, folder_selected)

    @property
    def start_id(self):
        if self.start_id_entry.get() == "":
            return -1
        return int(self.start_id_entry.get())

    @property
    def stop_id(self):
        if self.stop_id_entry.get() == "":
            return -1
        return int(self.stop_id_entry.get())

    @property
    def concurrent_windows(self):
        if self.concurrent_windows_entry.get() == "":
            return -1
        return int(self.concurrent_windows_entry.get())

    @property
    def outfile(self):
        return self.filename_entry.get()

    def scrape(self):
        if self.start_id == -1:
            print("Need to provide a start_id")
            return
        if self.stop_id == -1:
            print("Need to provide a stop_id")
            return
        if self.concurrent_windows == -1:
            print("Need to provide the number of concurrent windows")
            return
        if not self.is_running:
            self.is_running = True
            start_time = time.time()  # ----- Benchmark start ----- #

            print("\nStarting scrape:\n")
            AxsSeriesWebscraper.run(
                self.start_id, self.stop_id, self.outfile, self.concurrent_windows
            )
            print("\nFinished scrape")

            print(
                f"Time elapsed: {time.time() - start_time:.2f}s"
            )  # ----- Benchmark stop ----- #
            self.is_running = False
        else:
            print("\nScrape already in progress\n")

    def run(self):
        self.root.mainloop()


def main():
    # parse cli arguments
    # argc = len(sys.argv) - 1
    # if argc != 2:
    #     print(f"Incorrect number of arguments: Expected 2 arguments (start, stop) but received {argc}")
    #     sys.exit()
    # start_id, stop_id = int(sys.argv[1]), int(sys.argv[2])

    gui = AxsGui()
    gui.run()


if __name__ == "__main__":
    main()
