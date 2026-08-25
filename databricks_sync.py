"""
databricks_sync.py  -  PourCastAI (Step 8)

Pulls the 3 Gold Delta tables from Databricks and writes them into local
SQLite as plain tables (gold_inventory_health, gold_shipments_open,
gold_risk_scores) -- replacing the old SQL VIEWs of the same name from
schema.sql. This is a deliberate snapshot, not a live connection: Free
Edition has no uptime SLA, so the demo runs off this synced copy instead of
depending on Databricks being reachable at demo time. Re-run this anytime
you want fresher data (e.g. right before presenting).

Needs 3 new .env values (Databricks connection details, NOT your HubSpot
token):
    DATABRICKS_SERVER_HOSTNAME   (Databricks UI -> SQL Warehouses -> your
                                   warehouse -> Connection details)
    DATABRICKS_HTTP_PATH          (same page)
    DATABRICKS_TOKEN              (User icon -> Settings -> Developer ->
                                   Access tokens -> Generate new token)

Run:  pip install databricks-sql-connector --break-system-packages
      python databricks_sync.py
"""
import os
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
from databricks import sql

load_dotenv(Path(__file__).resolve().parent / ".env")

HOSTNAME = os.getenv("DATABRICKS_SERVER_HOSTNAME", "")
HTTP_PATH = os.getenv("DATABRICKS_HTTP_PATH", "")
TOKEN = os.getenv("DATABRICKS_TOKEN", "")

if not all([HOSTNAME, HTTP_PATH, TOKEN]):
    raise SystemExit(
        "Missing Databricks connection details in .env. Need:\n"
        "  DATABRICKS_SERVER_HOSTNAME\n  DATABRICKS_HTTP_PATH\n  DATABRICKS_TOKEN"
    )

DB_PATH = Path(__file__).resolve().parent / "data" / "pourcast.db"

TABLES = ["gold_inventory_health", "gold_shipments_open", "gold_risk_scores"]


def fetch_table(cursor, table_name):
    cursor.execute(f"SELECT * FROM pourcastai.gold.{table_name}")
    columns = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    return columns, rows


def write_sqlite(con, table_name, columns, rows):
    existing = con.execute(
        "SELECT type FROM sqlite_master WHERE name=?", (table_name,)).fetchone()
    if existing:
        con.execute(f"DROP {existing[0].upper()} {table_name}")
    col_defs = ", ".join(f'"{c}"' for c in columns)
    con.execute(f"CREATE TABLE {table_name} ({col_defs})")
    placeholders = ", ".join("?" for _ in columns)
    con.executemany(f"INSERT INTO {table_name} VALUES ({placeholders})", rows)


def main():
    print("Connecting to Databricks...")
    with sql.connect(server_hostname=HOSTNAME, http_path=HTTP_PATH, access_token=TOKEN) as conn:
        with conn.cursor() as cursor:
            sqlite_con = sqlite3.connect(DB_PATH)
            for table in TABLES:
                columns, rows = fetch_table(cursor, table)
                write_sqlite(sqlite_con, table, columns, rows)
                print(f"  synced {table}: {len(rows)} rows")
            sqlite_con.commit()
            sqlite_con.close()

    print(f"\nDone. Local SQLite ({DB_PATH.name}) now has fresh Gold snapshots.")
    print("Re-run this anytime -- e.g. right before your demo -- to refresh.")


if __name__ == "__main__":
    main()
