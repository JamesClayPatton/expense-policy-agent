"""Database connection shared by the app and the agent's tools."""
import psycopg

from expense_agent.config import load_settings


def connect() -> psycopg.Connection:
    return psycopg.connect(load_settings().database_url)
