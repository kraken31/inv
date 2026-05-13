"""Récupère le dernier prix connu des actions de la table `stocks` via
Yahoo Finance et met à jour la table `pricing`.

Source : yfinance (`Ticker.history`). Pas de scraping HTML, pas de
Selenium. Le ticker Yahoo est construit en suffixant l'identifiant
Euronext par ".PA" (ex. "AC" -> "AC.PA"), comme `get_stocks.py` /
`get_results.py`.

Table cible :
    CREATE TABLE pricing (
        id TEXT,
        date TEXT,
        price REAL,
        capitalisation REAL,
        per REAL,
        rsi REAL
    )
- id             : symbole Euronext (= stocks.id)
- date           : date du dernier prix connu, au format YYYY-MM-DD
- price          : dernier cours de clôture connu
- capitalisation : price * stocks.quantity
- per            : capitalisation / résultat_net pour l'année la plus
                   récente de results.
                   * -1 si le résultat net est négatif
                   * NULL si le résultat net vaut 0 ou est inconnu
                   * NULL si la quantité est inconnue (capitalisation
                     non calculable)
- rsi            : RSI(14) journalier calculé sur les clôtures de
                   l'historique récent, méthode de Wilder (EWM avec
                   alpha = 1/14, identique à TradingView et à la
                   plupart des plateformes).
                   * NULL si moins de 15 clôtures disponibles ou si la
                     moyenne des pertes vaut 0 sur la fenêtre

La colonne `rsi` est ajoutée automatiquement à la table existante via
ALTER TABLE si elle est absente (idempotent).

Le script est idempotent et reprenable : pour chaque action on écrase
uniquement la ligne (id, date) — les autres dates déjà présentes dans
`pricing` sont conservées. On peut donc le relancer sans perdre
l'historique déjà récupéré.
"""

import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import numpy as np
import pandas as pd
import yfinance as yf
from yfinance.exceptions import YFRateLimitError


DB_PATH = "/home/aurelien/dev/div/inv/inv.db"
MAX_WORKERS = 3
RATE_LIMIT_BACKOFF = (5, 15, 45)  # secondes entre tentatives sur rate-limit

# Période d'historique demandée à Yahoo. Il faut suffisamment de
# clôtures pour stabiliser le RSI(14) — l'EWM de Wilder n'a vraiment de
# sens qu'au bout de plusieurs dizaines d'observations. 6 mois (~125
# séances) couvre largement le besoin tout en restant peu coûteux.
HISTORY_PERIOD = "6mo"

# Fenêtre du RSI. 14 est la valeur historique de Wilder, utilisée par
# défaut sur quasiment toutes les plateformes (TradingView, Boursorama,
# Yahoo, etc.).
RSI_PERIOD = 14

_db_lock = threading.Lock()


def compute_rsi(close: "pd.Series", period: int = RSI_PERIOD) -> float | None:
    """Retourne le dernier RSI(`period`) journalier d'une série de
    clôtures, ou None si on n'a pas assez de points ou si le calcul est
    impossible (moyenne des pertes nulle).

    Méthode de Wilder (« New Concepts in Technical Trading Systems »,
    1978), identique à celle utilisée par défaut par TradingView,
    Boursorama, Yahoo, etc. :
      1. Différences `delta = close[t] - close[t-1]`.
      2. `gain = max(delta, 0)`, `loss = max(-delta, 0)`.
      3. Amorçage : avg_gain[period] = SMA des `period` premiers gains
         (idem pour avg_loss).
      4. Récurrence de Wilder pour `t > period` :
            avg[t] = (avg[t-1] * (period - 1) + x[t]) / period
      5. RSI = 100 - 100 / (1 + avg_gain / avg_loss).
    """
    if close is None or len(close) < period + 1:
        return None

    # diff -> NaN en position 0 ; on l'élimine pour travailler sur n-1
    # variations.
    delta = close.diff().to_numpy()[1:]
    if len(delta) < period:
        return None

    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    # Amorçage : SMA des `period` premiers gains/pertes.
    avg_gain = float(gain[:period].mean())
    avg_loss = float(loss[:period].mean())

    # Récurrence de Wilder sur le reste de la série.
    for i in range(period, len(gain)):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period

    if avg_loss == 0:
        # Aucune perte sur la fenêtre -> RSI mathématiquement = 100,
        # mais on préfère retourner None pour signaler le cas dégénéré
        # (titre suspendu, données manquantes, etc.).
        return None

    rs = avg_gain / avg_loss
    try:
        return float(100 - 100 / (1 + rs))
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def fetch_last_price(
    ticker: str,
) -> tuple[str, float, float | None] | None:
    """Retourne (date_iso, price, rsi) du dernier cours de clôture connu
    pour le ticker Yahoo donné. Le RSI peut être None si l'historique
    est trop court. Gère le rate-limit avec backoff. Renvoie None si
    Yahoo n'a pas d'historique pour ce ticker. Lève l'exception sur
    autre erreur après les retries.
    """
    last_exc: Exception | None = None
    attempts = len(RATE_LIMIT_BACKOFF) + 1
    for attempt in range(attempts):
        try:
            df = yf.Ticker(ticker).history(period=HISTORY_PERIOD)
        except YFRateLimitError as exc:
            last_exc = exc
            if attempt < len(RATE_LIMIT_BACKOFF):
                time.sleep(RATE_LIMIT_BACKOFF[attempt])
                continue
            raise
        except AttributeError:
            # Même garde-fou que get_div.py / get_results.py : yfinance
            # plante en interne pour certains tickers délistés ou
            # inconnus.
            return None
        except Exception:
            raise
        else:
            if df is None or df.empty or "Close" not in df.columns:
                return None

            close = df["Close"].dropna()
            if close.empty:
                return None

            ts = close.index[-1]
            value = close.iloc[-1]

            try:
                if pd.isna(value):
                    return None
            except TypeError:
                pass

            try:
                date_iso = ts.strftime("%Y-%m-%d")
            except AttributeError:
                try:
                    date_iso = pd.to_datetime(ts).strftime("%Y-%m-%d")
                except (ValueError, TypeError):
                    return None

            try:
                price = float(value)
            except (TypeError, ValueError):
                return None

            rsi = compute_rsi(close)

            return date_iso, price, rsi

    if last_exc is not None:
        raise last_exc
    return None


def latest_net_income(
    db: sqlite3.Connection, stock_id: str
) -> int | None:
    """Retourne le résultat net de results pour l'année la plus récente
    de cette action, ou None s'il n'y en a pas.
    """
    with _db_lock:
        row = db.execute(
            "SELECT result FROM results "
            "WHERE id = ? AND result IS NOT NULL "
            "ORDER BY year DESC LIMIT 1",
            (stock_id,),
        ).fetchone()
    if row is None:
        return None
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return None


def compute_per(
    capitalisation: float | None, net_income: int | None
) -> float | None:
    """Calcule le PER selon les règles du cahier des charges :
    - -1 si le résultat net est strictement négatif
    - None si on n'a pas de capitalisation ou pas de résultat ou si le
      résultat vaut 0 (division impossible)
    - capitalisation / résultat_net sinon
    """
    if net_income is None or capitalisation is None:
        return None
    if net_income < 0:
        return -1.0
    if net_income == 0:
        return None
    return capitalisation / net_income


def ensure_schema(db: sqlite3.Connection) -> None:
    """Ajoute la colonne `rsi` à la table `pricing` si elle n'existe pas
    déjà. Idempotent : on inspecte `PRAGMA table_info` et on ne fait
    rien si la colonne est déjà là.
    """
    cols = {row[1] for row in db.execute("PRAGMA table_info(pricing)")}
    if "rsi" not in cols:
        db.execute("ALTER TABLE pricing ADD COLUMN rsi REAL")
        db.commit()


def upsert_pricing(
    db: sqlite3.Connection,
    stock_id: str,
    date_iso: str,
    price: float,
    capitalisation: float | None,
    per: float | None,
    rsi: float | None,
) -> None:
    """Insère ou écrase la ligne (id, date) dans `pricing`. Toute ligne
    existante pour la même action ET la même date est remplacée ; les
    autres dates pour cette action sont conservées.
    """
    with _db_lock:
        db.execute(
            "DELETE FROM pricing WHERE id = ? AND date = ?",
            (stock_id, date_iso),
        )
        db.execute(
            "INSERT INTO pricing "
            "(id, date, price, capitalisation, per, rsi) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (stock_id, date_iso, price, capitalisation, per, rsi),
        )
        db.commit()


def process_stock(stock_id: str, quantity: int | None):
    ticker = f"{stock_id}.PA"
    try:
        result = fetch_last_price(ticker)
    except Exception as exc:
        return (
            stock_id,
            ticker,
            quantity,
            None,
            f"{type(exc).__name__}: {exc}",
        )
    return stock_id, ticker, quantity, result, None


def main() -> None:
    db = sqlite3.connect(DB_PATH, check_same_thread=False)
    try:
        ensure_schema(db)
        rows = db.execute(
            "SELECT id, quantity FROM stocks WHERE id IS NOT NULL"
        ).fetchall()
        print(f"{len(rows)} actions à traiter")

        ok = 0
        no_price = 0
        errors = 0
        start = time.time()

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futures = {
                pool.submit(process_stock, sid, qty): sid
                for sid, qty in rows
            }
            for i, fut in enumerate(as_completed(futures), 1):
                stock_id, ticker, quantity, result, err = fut.result()

                if err is not None:
                    errors += 1
                    print(
                        f"[{i}/{len(rows)}] {ticker} ERROR {err}"
                    )
                    continue

                if result is None:
                    no_price += 1
                    print(f"[{i}/{len(rows)}] {ticker} sans prix")
                    continue

                date_iso, price, rsi = result
                capitalisation = (
                    price * quantity if quantity is not None else None
                )
                net_income = latest_net_income(db, stock_id)
                per = compute_per(capitalisation, net_income)

                upsert_pricing(
                    db,
                    stock_id,
                    date_iso,
                    price,
                    capitalisation,
                    per,
                    rsi,
                )

                ok += 1
                print(
                    f"[{i}/{len(rows)}] {ticker} {date_iso} "
                    f"price={price:.4f} "
                    f"cap={capitalisation if capitalisation is None else f'{capitalisation:.0f}'} "
                    f"per={per if per is None else f'{per:.2f}'} "
                    f"rsi={rsi if rsi is None else f'{rsi:.1f}'}"
                )

        elapsed = time.time() - start
        print(
            f"\nTerminé en {elapsed:.1f}s : "
            f"{ok} OK, {no_price} sans prix, {errors} erreurs"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
