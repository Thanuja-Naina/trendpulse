"""TrendPulse: collect data from an API and a website, clean it, check it, store it in SQLite.

Run:  python pipeline.py            (needs internet)
Flow: fetch -> clean -> validate -> save to database -> charts
"""
import sqlite3
import time
from urllib.parse import urlparse

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd
import requests
from bs4 import BeautifulSoup

DB_PATH = "data/trendpulse.db"
HN_URL = "https://hacker-news.firebaseio.com/v0"
BOOKS_URL = "https://books.toscrape.com/catalogue/page-{}.html"
RATING_WORDS = {"One": 1, "Two": 2, "Three": 3, "Four": 4, "Five": 5}


# =============================================================== 1. FETCH
def fetch_hn_stories(limit=100):
    """Get the top stories from the Hacker News API (one request per story)."""
    ids = requests.get(f"{HN_URL}/topstories.json", timeout=10).json()[:limit]
    stories = []
    for story_id in ids:
        item = requests.get(f"{HN_URL}/item/{story_id}.json", timeout=10).json()
        if item and item.get("type") == "story" and not item.get("dead") and not item.get("deleted"):
            stories.append(item)
    return stories


def scrape_books(pages=3):
    """Scrape book titles, prices and ratings from books.toscrape.com (a site made for practice)."""
    books = []
    for page in range(1, pages + 1):
        response = requests.get(BOOKS_URL.format(page), timeout=15)
        if response.status_code == 404:          # no more pages
            break
        response.encoding = "utf-8"              # otherwise the pound sign is garbled
        books.extend(parse_books_html(response.text, page))
        time.sleep(0.5)                          # be polite to the website
    return books


def parse_books_html(html, page):
    soup = BeautifulSoup(html, "html.parser")
    books = []
    for card in soup.select("article.product_pod"):
        rating_word = [c for c in card.select_one("p.star-rating")["class"] if c in RATING_WORDS]
        books.append({
            "title": card.select_one("h3 a")["title"].strip(),
            "price_raw": card.select_one("p.price_color").get_text(strip=True),
            "rating_word": rating_word[0] if rating_word else None,
            "availability": card.select_one("p.availability").get_text(strip=True),
            "page": page,
        })
    return books


# =============================================================== 2. CLEAN
def get_domain(url):
    if not isinstance(url, str) or url == "":
        return "news.ycombinator.com"            # text posts (Ask HN) have no external url
    return urlparse(url).netloc.lower().removeprefix("www.")


def clean_stories(raw_stories):
    """Returns (clean_dataframe, report_of_what_changed)."""
    df = pd.DataFrame(raw_stories)
    for col in ["id", "by", "title", "url", "score", "descendants", "time"]:   # make sure columns exist
        if col not in df.columns:
            df[col] = None
    report = {"rows_in": len(df)}

    before = len(df)
    df = df.drop_duplicates(subset="id")
    report["duplicates_removed"] = before - len(df)

    before = len(df)
    df["title"] = df["title"].fillna("").str.strip()
    df = df[df["title"] != ""]
    report["blank_titles_removed"] = before - len(df)

    report["missing_comment_counts_filled"] = int(df["descendants"].isna().sum())
    df["comments"] = df["descendants"].fillna(0).astype(int)        # no value = zero comments
    df["score"] = pd.to_numeric(df["score"], errors="coerce").fillna(0).astype(int)
    df["author"] = df["by"].fillna("unknown")

    # new columns (feature engineering)
    posted = pd.to_datetime(df["time"], unit="s", utc=True)
    df["posted_at"] = posted.dt.strftime("%Y-%m-%d %H:%M:%S")
    df["hour_utc"] = posted.dt.hour
    df["weekday"] = posted.dt.day_name()
    df["domain"] = df["url"].apply(get_domain)
    df["title_length"] = df["title"].str.len()
    df["kind"] = "link"
    df.loc[df["title"].str.lower().str.startswith("ask hn"), "kind"] = "ask"
    df.loc[df["title"].str.lower().str.startswith("show hn"), "kind"] = "show"
    df["score_bucket"] = pd.cut(df["score"], [-1, 9, 99, float("inf")], labels=["low", "medium", "high"]).astype(str)

    df = df.rename(columns={"id": "story_id"})
    df["story_id"] = df["story_id"].astype(int)
    columns = ["story_id", "author", "domain", "title", "url", "score", "comments", "posted_at",
               "hour_utc", "weekday", "title_length", "kind", "score_bucket"]
    report["rows_out"] = len(df)
    return df[columns].reset_index(drop=True), report


def clean_books(raw_books):
    df = pd.DataFrame(raw_books)
    report = {"rows_in": len(df)}

    before = len(df)
    df = df.drop_duplicates(subset="title")
    report["duplicates_removed"] = before - len(df)

    df["price_gbp"] = pd.to_numeric(df["price_raw"].str.replace(r"[^0-9.]", "", regex=True), errors="coerce")
    before = len(df)
    df = df[df["price_gbp"].notna()]                                   # remove rows where the price cannot be read
    report["bad_prices_removed"] = before - len(df)

    df["rating"] = df["rating_word"].map(RATING_WORDS)                 # "Three" -> 3
    df["in_stock"] = df["availability"].str.lower().str.contains("in stock").astype(int)
    df["price_band"] = pd.cut(df["price_gbp"], [0, 20, 40, float("inf")], labels=["budget", "mid", "premium"]).astype(str)
    report["rows_out"] = len(df)
    return df[["title", "price_gbp", "rating", "in_stock", "price_band", "page"]].reset_index(drop=True), report


# =============================================================== 3. VALIDATE
def check_stories(df):
    """Return a list of problems. An empty list means the data is OK."""
    problems = []
    if len(df) == 0:
        problems.append("no rows")
    if not df["story_id"].is_unique:
        problems.append("story_id is not unique")
    if df[["story_id", "title", "author", "domain"]].isna().any().any():
        problems.append("null values in key columns")
    if (df["score"] < 0).any():
        problems.append("negative score")
    if (df["comments"] < 0).any():
        problems.append("negative comments")
    if not df["hour_utc"].between(0, 23).all():
        problems.append("hour outside 0-23")
    return problems


def check_books(df):
    problems = []
    if len(df) == 0:
        problems.append("no rows")
    if not df["title"].is_unique:
        problems.append("duplicate titles")
    if (df["price_gbp"] <= 0).any():
        problems.append("price not positive")
    if not df["rating"].dropna().between(1, 5).all():
        problems.append("rating outside 1-5")
    return problems


# =============================================================== 4. SAVE TO SQLITE
def create_tables(conn):
    """Authors and domains are stored once and referenced by id (this is 'normalisation')."""
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS authors (author_id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS domains (domain_id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT UNIQUE NOT NULL);
        CREATE TABLE IF NOT EXISTS stories (
            story_id INTEGER PRIMARY KEY,
            author_id INTEGER NOT NULL REFERENCES authors(author_id),
            domain_id INTEGER NOT NULL REFERENCES domains(domain_id),
            title TEXT NOT NULL, url TEXT,
            score INTEGER CHECK (score >= 0), comments INTEGER CHECK (comments >= 0),
            posted_at TEXT, hour_utc INTEGER, weekday TEXT, title_length INTEGER, kind TEXT, score_bucket TEXT);
        CREATE TABLE IF NOT EXISTS books (
            title TEXT PRIMARY KEY, price_gbp REAL CHECK (price_gbp > 0), rating INTEGER,
            in_stock INTEGER, price_band TEXT, page INTEGER);
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            run_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, run_time TEXT, rows_loaded INTEGER, passed_checks INTEGER);
    """)


def get_id(conn, table, id_column, value_column, value):
    """Insert the value if new, then return its id."""
    conn.execute(f"INSERT OR IGNORE INTO {table} ({value_column}) VALUES (?)", (value,))
    return conn.execute(f"SELECT {id_column} FROM {table} WHERE {value_column} = ?", (value,)).fetchone()[0]


def save_stories(conn, df):
    for row in df.itertuples(index=False):
        author_id = get_id(conn, "authors", "author_id", "username", row.author)
        domain_id = get_id(conn, "domains", "domain_id", "domain", row.domain)
        # INSERT OR REPLACE: running the pipeline twice updates rows instead of duplicating them
        conn.execute("INSERT OR REPLACE INTO stories VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (row.story_id, author_id, domain_id, row.title, row.url if isinstance(row.url, str) else None,
                      row.score, row.comments, row.posted_at, row.hour_utc, row.weekday, row.title_length,
                      row.kind, row.score_bucket))
    conn.commit()


def save_books(conn, df):
    for row in df.itertuples(index=False):
        rating = None if pd.isna(row.rating) else int(row.rating)
        conn.execute("INSERT OR REPLACE INTO books VALUES (?,?,?,?,?,?)",
                     (row.title, row.price_gbp, rating, row.in_stock, row.price_band, int(row.page)))
    conn.commit()


def log_run(conn, source, rows_loaded, passed):
    conn.execute("INSERT INTO pipeline_runs (source, run_time, rows_loaded, passed_checks) VALUES (?, datetime('now'), ?, ?)",
                 (source, rows_loaded, int(passed)))
    conn.commit()


# =============================================================== 5. CHARTS
def make_charts(conn):
    stories = pd.read_sql_query("SELECT s.score, s.hour_utc, d.domain FROM stories s JOIN domains d USING(domain_id)", conn)
    if len(stories) > 0:
        stories.groupby("domain")["score"].sum().nlargest(10).sort_values().plot.barh(title="Top domains by total score")
        plt.savefig("results/top_domains.png", dpi=120, bbox_inches="tight")
        plt.close()
        stories.groupby("hour_utc")["score"].mean().plot.bar(title="Average score by posting hour (UTC)")
        plt.savefig("results/score_by_hour.png", dpi=120, bbox_inches="tight")
        plt.close()
    books = pd.read_sql_query("SELECT rating, price_gbp FROM books", conn)
    if len(books) > 0:
        books.groupby("rating")["price_gbp"].mean().plot.bar(title="Average book price by rating (GBP)")
        plt.savefig("results/book_price_by_rating.png", dpi=120, bbox_inches="tight")
        plt.close()


# =============================================================== MAIN
def run_pipeline(raw_stories, raw_books, db_path=DB_PATH):
    """Cleans, validates and stores the given raw data. Separate from fetching so it can be tested offline."""
    conn = sqlite3.connect(db_path)
    create_tables(conn)

    stories, story_report = clean_stories(raw_stories)
    problems = check_stories(stories)
    print("Stories:", story_report, "| problems:", problems or "none")
    if problems:
        log_run(conn, "hacker_news_api", 0, False)
        raise ValueError(f"Story validation failed: {problems}")   # stop - never load bad data
    save_stories(conn, stories)
    log_run(conn, "hacker_news_api", len(stories), True)

    if raw_books:
        books, book_report = clean_books(raw_books)
        problems = check_books(books)
        print("Books:", book_report, "| problems:", problems or "none")
        if problems:
            log_run(conn, "books_scrape", 0, False)
            raise ValueError(f"Book validation failed: {problems}")
        save_books(conn, books)
        log_run(conn, "books_scrape", len(books), True)

    make_charts(conn)
    conn.close()


if __name__ == "__main__":
    run_pipeline(fetch_hn_stories(100), scrape_books(3))
    print("Done. Database:", DB_PATH, "| charts in results/")
