import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline import (check_stories, clean_books, clean_stories, parse_books_html, run_pipeline)

STORIES = [
    {"id": 1, "by": "alice", "title": "Show HN: My tool", "url": "https://www.example.com/a", "score": 150, "descendants": 40, "time": 1_700_000_000},
    {"id": 2, "by": "bob", "title": "Ask HN: Best way to learn ML?", "score": 5, "time": 1_700_003_600},   # no url, no comments
    {"id": 2, "by": "bob", "title": "Ask HN: Best way to learn ML?", "score": 5, "time": 1_700_003_600},   # duplicate
    {"id": 3, "by": "carol", "title": "   ", "url": "https://x.org", "score": 3, "time": 1_700_007_200},     # blank title
]
BOOK_HTML = """<article class="product_pod"><p class="star-rating Three"></p>
<h3><a title="A Light in the Attic">A Light...</a></h3><p class="price_color">\u00a351.77</p>
<p class="instock availability"> In stock </p></article>"""


def test_clean_stories_fixes_problems_and_adds_features():
    df, report = clean_stories(STORIES)
    assert report["duplicates_removed"] == 1 and report["blank_titles_removed"] == 1
    assert len(df) == 2 and check_stories(df) == []
    ask = df[df.story_id == 2].iloc[0]
    assert ask["comments"] == 0 and ask["kind"] == "ask" and ask["domain"] == "news.ycombinator.com"
    show = df[df.story_id == 1].iloc[0]
    assert show["domain"] == "example.com" and show["kind"] == "show" and show["score_bucket"] == "high"


def test_validation_catches_negative_score():
    df, _ = clean_stories(STORIES)
    df.loc[0, "score"] = -1
    assert "negative score" in check_stories(df)


def test_books_are_parsed_and_cleaned():
    df, _ = clean_books(parse_books_html(BOOK_HTML, page=1))
    assert df.loc[0, "price_gbp"] == 51.77 and df.loc[0, "rating"] == 3 and df.loc[0, "in_stock"] == 1


def test_running_twice_does_not_duplicate_rows(tmp_path):
    db = str(tmp_path / "t.db")
    raw_books = parse_books_html(BOOK_HTML, page=1)
    run_pipeline(STORIES, raw_books, db)
    run_pipeline(STORIES, raw_books, db)
    conn = sqlite3.connect(db)
    assert conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM books").fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM pipeline_runs").fetchone()[0] == 4
