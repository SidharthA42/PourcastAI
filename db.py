"""db.py - one place that opens the SHARED database. Both agents import this."""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "data" / "pourcast.db"

def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row      # rows behave like dicts
    return con
