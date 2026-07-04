"""Builds the FlexMyList offline product database.

Reads the Open Food Facts parquet dump from Hugging Face, filters to products
sold in DE/AT/CH, resolves category names in German AND English via the OFF
category taxonomy, and writes a compact SQLite file with an FTS4 index for
on-device search-as-you-type.

Data source: Open Food Facts (https://world.openfoodfacts.org)
License of the data: Open Database License (ODbL) - see README.md.
"""

import json
import os
import sqlite3
import sys
import urllib.request

import duckdb

# The workflow pre-downloads the dump (HF rate-limits DuckDB's many range requests
# with 429); remote access stays as fallback for ad-hoc local runs.
PARQUET_REMOTE = (
    "https://huggingface.co/datasets/openfoodfacts/product-database"
    "/resolve/main/food.parquet"
)
PARQUET_LOCAL = "food.parquet"
PARQUET = PARQUET_LOCAL if os.path.exists(PARQUET_LOCAL) else PARQUET_REMOTE

TAXONOMY_URL = "https://static.openfoodfacts.org/data/taxonomies/categories.json"
USER_AGENT = "flexmylist-productdb/1.0 (build pipeline)"

OUT_FILE = "products.db"
COUNTRIES = ["en:germany", "en:austria", "en:switzerland"]
# Sanity gate: fail the workflow loudly instead of publishing a broken/empty DB.
MIN_PRODUCTS = 100_000
BATCH_SIZE = 50_000
SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE products (
    id INTEGER PRIMARY KEY,
    barcode TEXT NOT NULL,
    name_de TEXT,
    name_en TEXT,
    brand TEXT,
    category_de TEXT,
    category_en TEXT
);
CREATE INDEX idx_products_barcode ON products(barcode);
CREATE VIRTUAL TABLE products_fts USING fts4(
    content="products", name_de, name_en, brand, category_de, category_en,
    tokenize=unicode61
);
CREATE TABLE category_names (tag TEXT PRIMARY KEY, de TEXT, en TEXT);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""

# product_name is a list of {lang, text} structs; per language prefer the exact
# match, then the product's main language, then whatever comes first.
QUERY = f"""
SELECT * FROM (
    SELECT
        code,
        COALESCE(
            list_filter(product_name, x -> x.lang = 'de')[1].text,
            list_filter(product_name, x -> x.lang = 'main')[1].text,
            product_name[1].text
        ) AS name_de,
        COALESCE(
            list_filter(product_name, x -> x.lang = 'en')[1].text,
            list_filter(product_name, x -> x.lang = 'main')[1].text,
            product_name[1].text
        ) AS name_en,
        brands,
        categories_tags[-1] AS category_tag
    FROM read_parquet('{PARQUET}')
    WHERE len(list_intersect(countries_tags, {COUNTRIES!r})) > 0
      AND code IS NOT NULL
)
WHERE (name_de IS NOT NULL AND length(trim(name_de)) > 1)
   OR (name_en IS NOT NULL AND length(trim(name_en)) > 1)
"""


def prettify(tag):
    """'en:sugary-snacks' -> 'Sugary snacks' (fallback when the taxonomy has no name)."""
    if not tag:
        return None
    tag = tag.split(":", 1)[-1].replace("-", " ").strip()
    return (tag[:1].upper() + tag[1:]) if tag else None


def clean(text):
    if text is None:
        return None
    text = text.strip()
    return text if len(text) > 1 else None


def load_taxonomy():
    """tag -> (de, en) display names from the official OFF category taxonomy."""
    request = urllib.request.Request(TAXONOMY_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response:
        taxonomy = json.load(response)
    names = {}
    for tag, entry in taxonomy.items():
        name = entry.get("name", {})
        de = name.get("de")
        en = name.get("en")
        if de or en:
            names[tag] = (de, en)
    print(f"Taxonomie: {len(names)} Kategorien mit Namen")
    return names


def main():
    taxonomy = load_taxonomy()

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
        batch = []
        for code, name_de, name_en, brands, tag in rows:
            de_name, en_name = taxonomy.get(tag, (None, None))
            batch.append(
                (
                    code,
                    clean(name_de),
                    clean(name_en),
                    clean(brands),
                    de_name or prettify(tag),
                    en_name or prettify(tag),
                ),
            )
        db.executemany(
            "INSERT INTO products (barcode, name_de, name_en, brand, category_de, category_en) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            batch,
        )
        count += len(rows)
        db.commit()
        print(f"  ... {count} products", flush=True)

    db.executemany(
        "INSERT INTO category_names (tag, de, en) VALUES (?, ?, ?)",
        [(tag, de, en) for tag, (de, en) in taxonomy.items()],
    )
    db.execute("INSERT INTO products_fts(products_fts) VALUES('rebuild')")
    db.execute("INSERT INTO meta (key, value) VALUES ('schema', ?)", (SCHEMA_VERSION,))
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
    print(f"OK: {count} Produkte, {size_mb:.1f} MB, Schema {SCHEMA_VERSION}")


if __name__ == "__main__":
    main()
