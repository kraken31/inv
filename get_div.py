import sqlite3
import time
from collections import defaultdict

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.wait import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


# Webdriver
options = webdriver.ChromeOptions()
options.add_argument('--headless')
options.add_argument('--no-sandbox')
options.add_argument('--start-maximized')
options.add_argument('--disable-blink-features=AutomationControlled')
options.add_argument(
    '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 '
    '(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
)
service = Service(executable_path=r'/usr/bin/chromedriver')
driver = webdriver.Chrome(service=service, options=options)


# DB
db = sqlite3.connect('/home/aurelien/dev/div/db/per_analysis.db')

rows = db.execute(
    "SELECT isin, url_dividend FROM stocks "
    "WHERE url_dividend IS NOT NULL AND TRIM(url_dividend) != ''"
).fetchall()

db.execute("DELETE FROM dividends3")
db.commit()


def accept_cookies(drv):
    try:
        WebDriverWait(drv, 5).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
    except Exception:
        pass


def parse_year(date_str):
    return int(date_str.strip().split("/")[2])


def parse_dividend(value_str):
    cleaned = (
        value_str.strip()
        .replace("\u202f", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .replace(",", ".")
    )
    return float(cleaned)


cookies_accepted = False
processed = 0

try:
    for isin, url in rows:
        try:
            driver.get(url)

            if not cookies_accepted:
                accept_cookies(driver)
                cookies_accepted = True

            table = WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((
                    By.XPATH,
                    "//table[.//th[contains(normalize-space(.),"
                    " 'Date de détachement')]]",
                ))
            )

            totals = defaultdict(float)
            tr_elements = table.find_elements(
                By.CSS_SELECTOR, "tbody tr"
            )
            for tr in tr_elements:
                cells = tr.find_elements(By.TAG_NAME, "td")
                if len(cells) < 2:
                    continue
                try:
                    year = parse_year(cells[0].text)
                    dividend = parse_dividend(cells[1].text)
                except (ValueError, IndexError):
                    continue
                totals[year] += dividend

            if totals:
                db.executemany(
                    "INSERT INTO dividends3 (isin, year, dividend) "
                    "VALUES (?, ?, ?)",
                    [
                        (isin, y, round(d, 6))
                        for y, d in sorted(totals.items())
                    ],
                )

            print(isin, len(totals), "années")
        except Exception as exc:
            print(isin, "ERROR", exc)

        processed += 1
        if processed % 50 == 0:
            db.commit()

        time.sleep(0.5)
finally:
    driver.quit()
    db.commit()
    db.close()
