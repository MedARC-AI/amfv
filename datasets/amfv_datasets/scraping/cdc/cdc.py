"""
Standalone CDC guideline scraper.
Extracted from the original 12-source scraper script — CDC only.

# Install dependencies
pip install -r guidelines/requirements.txt

# Use spacy to get the English language pipeline
python -m spacy download en_core_web_sm 

# Install scipdf from GitHub to convert PDFs to text
pip install git+https://github.com/titipata/scipdf_parser

Note:
CDCScraper uses pdfs=True, which means Stage 3 (PDF -> text conversion)
requires GROBID to be running.

Example GROBID command:
docker run -d --name grobid -p 8070:8070 grobid/grobid:0.8.0

GROBID should be reachable at:
http://localhost:8070
"""

# ------------------- Imports ------------------- #

import argparse
import os
import time
import json

from tqdm import tqdm
import scipdf

from selenium import webdriver
from selenium.webdriver import ChromeOptions
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common import NoSuchElementException


# ------------------- Selenium Setup ------------------- #

def setup_chrome_driver(
    include_experimental: bool = True,
    download_path: str = None,
    binary_location: str = None,
    driver_location: str = None,
    headless: bool = False,
    eager_mode: bool = False
):
    """
    Set up Chrome driver instance with download path.
    """

    chrome_options = ChromeOptions()

    if binary_location:
        chrome_options.binary_location = binary_location

    if eager_mode:
        chrome_options.page_load_strategy = "eager"

    if headless:
        chrome_options.add_argument("--headless=new")

    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-extensions")
    chrome_options.add_argument("--dns-prefetch-disable")
    chrome_options.add_argument("--disable-gpu")

    if include_experimental:
        assert download_path is not None, "Download path must be provided."

        os.makedirs(download_path, exist_ok=True)

        chrome_options.add_experimental_option("prefs", {
            "download.default_directory": os.path.abspath(download_path),
            "download.prompt_for_download": False,
            "download.directory_upgrade": True,
            "plugins.always_open_pdf_externally": True,
            "safebrowsing.enabled": True,
        })

    chrome_params = {
        "options": chrome_options
    }

    if driver_location:
        webdriver_service = Service(driver_location)
        chrome_params["service"] = webdriver_service

    driver = webdriver.Chrome(**chrome_params)

    return driver


# ------------------- Helper Functions ------------------- #

def pdf2text(src_path, dest_path, grobid_url="http://localhost:8070"):
    """
    Convert all PDF files in src_path to text, then save as JSONL to dest_path.
    Assumes GROBID is already running at grobid_url.
    """

    guidelines = []

    if not os.path.exists(src_path):
        print(f"PDF folder does not exist: {src_path}")
        return guidelines

    pdf_files = [
        file for file in os.listdir(src_path)
        if file.lower().endswith(".pdf")
    ]

    print(f"Found {len(pdf_files)} PDF files for conversion.")

    for file in tqdm(pdf_files):
        pdf_file_path = os.path.join(src_path, file)

        try:
            article = scipdf.parse_pdf_to_dict(
                pdf_file_path,
                grobid_url=grobid_url
            )

            text = str(article.get("abstract", "")) + "\n"

            sections = article.get("sections", [])
            for sec in sections:
                heading = sec.get("heading", "")
                section_text = sec.get("text", "")
                text += f"# {heading}\n{section_text}\n"

            guideline = {
                "file_name": file,
                "text": text,
                "doi": article.get("doi")
            }

            guidelines.append(guideline)

        except Exception as e:
            print(f"Exception when parsing file {file}: {e}")

    os.makedirs(os.path.dirname(dest_path), exist_ok=True)

    with open(dest_path, "w", encoding="utf-8") as f:
        for guideline in guidelines:
            f.write(f"{json.dumps(guideline)}\n")

    return guidelines


def wait_for_downloads(download_path, timeout=60):
    """
    Wait until Chrome finishes downloading files.
    Chrome temporary downloads usually end with .crdownload.
    """

    start_time = time.time()

    while True:
        if not os.path.exists(download_path):
            return

        downloading_files = [
            file for file in os.listdir(download_path)
            if file.endswith(".crdownload")
        ]

        if not downloading_files:
            return

        if time.time() - start_time > timeout:
            print("Download wait timeout reached.")
            return

        time.sleep(1)


# ------------------- Scraper Base Class ------------------- #

class Scraper:
    """
    Web scraper base class.
    Downloads scraped articles to:
    {path}/raw/{source}.jsonl
    """

    def __init__(
        self,
        source: str,
        path: str,
        pdfs: bool = False,
        chrome_binary_location: str = None,
        chrome_driver_location: str = None,
        headless: bool = False
    ):
        self.path = path
        self.source = source

        self.source_path = os.path.join(self.path, self.source)
        os.makedirs(self.source_path, exist_ok=True)

        self.pdfs = pdfs
        self.pdf_path = os.path.join(self.source_path, "pdfs")
        self.links_path = os.path.join(self.source_path, "links.txt")

        if self.pdfs:
            os.makedirs(self.pdf_path, exist_ok=True)

        articles_dir = os.path.join(self.path, "raw")
        os.makedirs(articles_dir, exist_ok=True)

        self.articles_path = os.path.join(
            articles_dir,
            f"{source}.jsonl"
        )

        self.driver = setup_chrome_driver(
            include_experimental=self.pdfs,
            download_path=self.pdf_path if self.pdfs else None,
            binary_location=chrome_binary_location,
            driver_location=chrome_driver_location,
            headless=headless
        )

    def save_articles(self, articles, path=None):
        articles_path = self.articles_path if path is None else path

        os.makedirs(os.path.dirname(articles_path), exist_ok=True)

        with open(articles_path, "w", encoding="utf-8") as f:
            for article in articles:
                f.write(f"{json.dumps(article)}\n")

    def save_links(self, links):
        links = list(set(links))

        with open(self.links_path, "w", encoding="utf-8") as f:
            for link in links:
                f.write(f"{link}\n")

    def reset_download_path(self):
        """
        Optional CDP download path reset.
        Not called inside loop because it can fail on some Chrome/Selenium setups.
        """

        self.driver.execute_cdp_cmd(
            "Page.setDownloadBehavior",
            {
                "behavior": "allow",
                "downloadPath": os.path.abspath(self.pdf_path)
            }
        )

    def _scrape_links(self):
        raise NotImplementedError

    def scrape_links(self):
        if os.path.exists(self.links_path):
            print(f"Using existing links file: {self.links_path}")

            with open(self.links_path, "r", encoding="utf-8") as f:
                return [i.strip() for i in f.readlines() if i.strip()]

        return self._scrape_links()

    def scrape_articles(self, links):
        raise NotImplementedError

    def scrape(self):
        try:
            print(f"Stage 1: Scraping article links from {self.source}...")

            links = self.scrape_links()
            unique_links = list(set(links))

            print(
                f"Scraped {len(unique_links)} unique article links "
                f"from {self.source}."
            )

            print(f"Stage 2: Scraping articles from {self.source}...")

            articles = self.scrape_articles(unique_links)

            if self.pdfs:
                print("Waiting for any active downloads to finish...")
                wait_for_downloads(self.pdf_path, timeout=120)

                downloaded_pdfs = [
                    file for file in os.listdir(self.pdf_path)
                    if file.lower().endswith(".pdf")
                ]

                print(f"Downloaded {len(downloaded_pdfs)} PDF files.")

                print("Stage 3: Converting PDFs to text...")
                articles = pdf2text(
                    self.pdf_path,
                    self.articles_path
                )

            print(
                f"Saved {len(articles)} {self.source} articles "
                f"to {self.articles_path}."
            )

            return articles

        finally:
            self.driver.quit()


# ------------------- CDC Scraper ------------------- #

class CDCScraper(Scraper):
    """
    Source: CDC - Centers for Disease Control and Prevention
    https://www.cdc.gov/
    """

    def __init__(self, path, **kwargs):
        super().__init__(
            source="cdc",
            path=path,
            pdfs=True,
            **kwargs
        )

    def _scrape_links(self):
        url = (
            "https://stacks.cdc.gov/cbrowse?"
            "pid=cdc%3A100&parentId=cdc%3A100"
            "&maxResults=100&start=0"
        )

        self.driver.get(url)

        links = []

        def next_page():
            try:
                continue_link = self.driver.find_element(
                    By.PARTIAL_LINK_TEXT,
                    "Next"
                )

                continue_link.click()
                time.sleep(5)

                return True

            except NoSuchElementException:
                return False

        search_expr = "//div[@class='object-title']/a"

        wait = WebDriverWait(self.driver, 15)

        try:
            hrefs = wait.until(
                EC.presence_of_all_elements_located(
                    (By.XPATH, search_expr)
                )
            )

        except Exception as e:
            with open("debug_cdc_page.html", "w", encoding="utf-8") as f:
                f.write(self.driver.page_source)

            print(
                "Timed out waiting for CDC results. "
                "Page source saved to debug_cdc_page.html"
            )

            raise e

        links.extend([
            self._to_absolute_url(a.get_attribute("href"))
            for a in hrefs
            if a.get_attribute("href")
        ])

        while next_page():
            try:
                wait = WebDriverWait(self.driver, 10)

                hrefs = wait.until(
                    EC.presence_of_all_elements_located(
                        (By.XPATH, search_expr)
                    )
                )

                links.extend([
                    self._to_absolute_url(a.get_attribute("href"))
                    for a in hrefs
                    if a.get_attribute("href")
                ])

            except Exception as e:
                print(f"Could not scrape links on next page: {e}")

        self.save_links(links)

        return links

    @staticmethod
    def _to_absolute_url(href):
        """
        Convert relative CDC Stack URLs to absolute URLs.
        """

        if href and href.startswith("/"):
            return "https://stacks.cdc.gov" + href

        return href

    def scrape_articles(self, links):
        os.makedirs(self.pdf_path, exist_ok=True)

        articles = []

        for page in tqdm(links):
            try:
                self.driver.get(page)

                wait = WebDriverWait(self.driver, 10)

                button = wait.until(
                    EC.element_to_be_clickable(
                        (By.XPATH, "//button[@id='download-document-submit']")
                    )
                )

                button.click()

                print(f"Clicked download button for: {page}")

                time.sleep(3)

            except Exception as e:
                print(f"Failed to download from {page}: {e}")

        self.save_articles(articles)

        return articles


# ------------------- Main ------------------- #

if __name__ == "__main__":
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--path",
        type=str,
        default=os.getcwd(),
        help="Path to download scraped guidelines to."
    )

    parser.add_argument(
        "--chrome_binary_location",
        type=str,
        default=None,
        help="Path to Chrome binary."
    )

    parser.add_argument(
        "--chrome_driver_location",
        type=str,
        default=None,
        help="Path to Chrome driver."
    )

    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome in headless mode."
    )

    args = parser.parse_args()

    os.makedirs(args.path, exist_ok=True)

    print(f"Downloading CDC guidelines to {args.path}")

    scrap_params = {
        "path": args.path,
        "headless": args.headless
    }

    if args.chrome_binary_location:
        scrap_params["chrome_binary_location"] = args.chrome_binary_location

    if args.chrome_driver_location:
        scrap_params["chrome_driver_location"] = args.chrome_driver_location

    scraper = CDCScraper(**scrap_params)
    scraper.scrape()