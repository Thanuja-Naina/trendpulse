# TrendPulse - API and Web-Scraping Data Pipeline

Collects data from a **REST API (Hacker News)** and a **website (Books to Scrape)**, cleans it, creates new columns, **checks data quality**, and stores everything in a **normalised SQLite database**. Running it twice never creates duplicate rows.

**Stack:** Python, requests, BeautifulSoup, pandas, SQLite, matplotlib, pytest, GitHub Actions

## Business problem
A content or product team wants to know which topics and websites attract attention on Hacker News, and a bookshop analyst wants clean price and rating data. Both need **trustworthy, de-duplicated, validated data in a database**, refreshed automatically. The pipeline produces that, and logs every run so failures are visible.

## The pipeline (all in `pipeline.py`, one numbered section per step)

```
Hacker News API --+
                  +--> 2. clean + new columns --> 3. validate --> 4. save to SQLite --> 5. charts
Books website ----+       (fix problems)          (stop if bad)     (no duplicates)
```

| Step | What happens |
|---|---|
| 1. Fetch | API calls for stories; scraping of book pages with a polite delay |
| 2. Clean | Remove duplicates and blank titles, fill missing comment counts with 0, parse prices, add columns (domain, posting hour, weekday, post type, score bucket) |
| 3. Validate | Unique ids, no nulls in key columns, scores/prices valid. If a check fails, nothing is loaded |
| 4. Save | Tables `authors`, `domains`, `stories`, `books`, `pipeline_runs`. Authors and domains are stored once and linked by id (normalisation) |
| 5. Charts | Top domains, score by hour, book price by rating, saved in `results/` |

## Run it
```bash
pip install -r requirements.txt
python pipeline.py
python -c "import sqlite3; c=sqlite3.connect('data/trendpulse.db'); print(*c.execute('SELECT d.domain, COUNT(*) AS stories FROM stories s JOIN domains d USING(domain_id) GROUP BY 1 ORDER BY 2 DESC LIMIT 10;'), sep='\n')"
```

## Results of my latest run
Example: the top domains in the current local database are:

```
('github.com', 6)
('news.ycombinator.com', 4)
('youtube.com', 3)
('spectrum.ieee.org', 2)
('quantamagazine.org', 2)
('yle.fi', 1)
('wsj.com', 1)
('webkit.org', 1)
('washingtonpost.com', 1)
('vt100.net', 1)
```

This is a snapshot; the counts can change when the pipeline runs again. You can also add the pipeline's cleaning report and a finding from the charts here.

## Notes
Books to Scrape is a practice site that allows scraping. Check the terms and robots.txt of any other site first. The Hacker News API needs no key.
