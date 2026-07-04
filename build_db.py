"""Builds the FlexMyList offline product database.

Reads the Open Food Facts parquet dump from Hugging Face, filters to products
sold in DE/AT/CH, and writes a compact SQLite file with an FTS4 index for
on-device search-as-you-type.

Data source: Open Food Facts (https://world.openfoodfacts.org)
License of the data: Open Database License (ODbL) - see README.md.
"""

import os
import sqlite3
import sys

import duckdb

# The workflow pre-downloads the dump (HF rate-limits DuckDB's many range requests
# with 429); remote access stays as fallback for ad-hoc local runs.
PARQUET_REMOTE = (
    "https://huggingface.co/datasets/openfoodfacts/product-database"
    "/resolve/main/food.parquet"
)
PARQUET_LOCAL = "food.parquet"
PARQUET = PARQUET_LOCAL if os.path.exists(PARQUET_LOCAL) else PARQUET_REMOTE
OUT_FILE = "products.db"
COUNTRIES = ["en:germany", "en:austria", "en:switzerland"]
# Sanity gate: fail the workflow loudly instead of publishing a broken/empty DB.
MIN_PRODUCTS = 100_000
BATCH_SIZE = 50_000

SCHEMA = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    barcode TEXT NOT NULL,
    name TEXT NOT NULL,
    brand TEXT,
    category TEXT
);
CREATE INDEX idx_products_barcode ON products(barcode);
CREATE VIRTUAL TABLE products_fts USING fts4(
    content="products", name, brand, category, tokenize=unicode61
);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

# product_name is a list of {lang, text} structs; prefer German, then the
# product's main language, then whatever comes first.
QUERY = f"""
SELECT * FROM (
    SELECT
        code,
        COALESCE(
            list_filter(product_name, x -> x.lang = 'de')[1].text,
            list_filter(product_name, x -> x.lang = 'main')[1].text,
            product_name[1].text
        ) AS name,
        brands,
        categories_tags[-1] AS category_tag
    FROM read_parquet('{PARQUET}')
    WHERE len(list_intersect(countries_tags, {COUNTRIES!r})) > 0
      AND code IS NOT NULL
)
WHERE name IS NOT NULL AND length(trim(name)) > 1
"""


def prettify(tag):
    """'en:sugary-snacks' -> 'Sugary snacks' (display/grouping string)."""
    if not tag:
        return None
    tag = tag.split(":", 1)[-1].replace("-", " ").strip()
    return (tag[:1].upper() + tag[1:]) if tag else None


def main():
    con = duckdb.connect()
    cur = con.execute(QUERY)

    if os.path.exists(OUT_FILE):
        os.remove(OUT_FILE)
    db = sqlite3.connect(OUT_FILE)
    db.executescript(SCHEMA)

    count = 0
    while True:
        rows = cur.fetchmany(BATCH_SIZE)
        if not rows:
            break
        db.executemany(
            "INSERT INTO products (barcode, name, brand, category) VALUES (?, ?, ?, ?)",
            [
                (code, name.strip(), brands, prettify(tag))
                for code, name, brands, tag in rows
            ],
        )
        count += len(rows)
        db.commit()
        print(f"  ... {count} products", flush=True)

    db.execute("INSERT INTO products_fts(products_fts) VALUES('rebuild')")
    db.execute("INSERT INTO meta (key, value) VALUES ('count', ?)", (str(count),))
    db.execute("INSERT INTO meta (key, value) VALUES ('source', 'Open Food Facts (ODbL)')")
    db.execute("INSERT INTO meta (key, value) VALUES ('created', datetime('now'))")
    db.commit()
    db.execute("VACUUM")
    db.close()

    if count <= MIN_PRODUCTS:
        print(f"FEHLER: nur {count} Produkte - Abbruch statt kaputter DB.", file=sys.stderr)
        sys.exit(1)

    size_mb = os.path.getsize(OUT_FILE) / 1_000_000
    print(f"OK: {count} Produkte, {size_mb:.1f} MB")


if __name__ == "__main__":
    main()
