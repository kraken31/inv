import sqlite3
import time

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
isins = [
    row[0].strip()
    for row in db.execute(
        "SELECT isin2 FROM stocks "
        "WHERE isin2 IS NOT NULL AND TRIM(isin2) != ''"
    ).fetchall()
]


def accept_cookies(drv):
    try:
        WebDriverWait(drv, 5).until(
            EC.element_to_be_clickable((By.ID, "onetrust-accept-btn-handler"))
        ).click()
    except Exception:
        pass


cookies_accepted = False

try:
    for isin in isins:
        try:
            driver.get(
                f"https://fr.investing.com/search/?q={isin}&tab=quotes"
            )

            if not cookies_accepted:
                accept_cookies(driver)
                cookies_accepted = True

            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located(
                    (By.CSS_SELECTOR, "a.js-inner-all-results-quote-item")
                )
            )
            first = driver.find_element(
                By.CSS_SELECTOR, "a.js-inner-all-results-quote-item"
            )
            equity_url = first.get_attribute("href")
            dividends_url = equity_url.rstrip('/') + "-dividends"
            db.execute(
                "UPDATE stocks SET url_dividend = ? WHERE isin2 = ?",
                (dividends_url, isin),
            )
            print(isin, dividends_url)
        except Exception:
            db.execute(
                "UPDATE stocks SET url_dividend = NULL WHERE isin2 = ?",
                (isin,),
            )
            print(isin, "NOT_FOUND")

        time.sleep(0.5)
finally:
    driver.quit()
    db.commit()
    db.close()
