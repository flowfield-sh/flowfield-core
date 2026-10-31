"""Additive numeric context evidence tables."""

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage_context_state (
 execution INTEGER PRIMARY KEY REFERENCES usage_executions(number) ON DELETE CASCADE,
 dropped INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS usage_context (
 number INTEGER PRIMARY KEY AUTOINCREMENT,
 execution INTEGER NOT NULL REFERENCES usage_executions(number) ON DELETE CASCADE,
 received_at TEXT NOT NULL, kind TEXT NOT NULL, bytes INTEGER NOT NULL, data TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS usage_context_owner ON usage_context(execution,number);
"""
