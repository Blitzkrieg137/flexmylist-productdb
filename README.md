# flexmylist-productdb

Wöchentliche Build-Pipeline für die Offline-Produktdatenbank.

Eine GitHub Action lädt den [Open-Food-Facts-Parquet-Dump](https://huggingface.co/datasets/openfoodfacts/product-database)
(Hugging Face), filtert auf Produkte, die in Deutschland, Österreich oder der Schweiz
gelistet sind, und baut daraus eine kompakte SQLite-Datei mit FTS4-Index
(Barcode, Name, Marke, Kategorie). Das Ergebnis wird als Release-Asset veröffentlicht:

```
https://github.com/Blitzkrieg137/flexmylist-productdb/releases/latest/download/products.db.gz
https://github.com/Blitzkrieg137/flexmylist-productdb/releases/latest/download/products.db.gz.sha256
```

Die App lädt die Datei wöchentlich (nur WLAN), prüft die SHA-256-Checksumme und
tauscht die lokale Datenbank atomar aus.

## Schema

```sql
CREATE TABLE products (id INTEGER PRIMARY KEY, barcode TEXT NOT NULL,
                       name TEXT NOT NULL, brand TEXT, category TEXT);
CREATE INDEX idx_products_barcode ON products(barcode);
CREATE VIRTUAL TABLE products_fts USING fts4(content="products",
                       name, brand, category, tokenize=unicode61);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);  -- count / source / created
```

## Manuell bauen

`workflow_dispatch` in Actions auslösen, oder lokal:

```
pip install duckdb
python build_db.py
```

## Datenquelle & Lizenz

Produktdaten: **© Open Food Facts contributors**, lizenziert unter der
[Open Database License (ODbL)](https://opendatacommons.org/licenses/odbl/1-0/).
Quelle: <https://world.openfoodfacts.org> · Dump: <https://huggingface.co/datasets/openfoodfacts/product-database>

Der Code in diesem Repository steht unter der MIT-Lizenz.
