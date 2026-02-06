"""SQLite database models and manager for BountyHound Local."""

import sqlite3
import json
import os
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = os.environ.get("BHL_DB_PATH", str(Path.home() / "bountyhound-local" / "data" / "bountyhound.db"))


def get_db() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        domain TEXT UNIQUE NOT NULL,
        platform TEXT DEFAULT 'private',
        program_url TEXT,
        scope_json TEXT,
        bounty_min INTEGER DEFAULT 0,
        bounty_max INTEGER DEFAULT 0,
        priority INTEGER DEFAULT 5,
        credentials_path TEXT,
        notes TEXT,
        status TEXT DEFAULT 'pending',
        last_recon_at TEXT,
        last_scan_at TEXT,
        last_full_hunt_at TEXT,
        total_findings INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS hunts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id INTEGER NOT NULL,
        hunt_type TEXT NOT NULL,
        status TEXT DEFAULT 'running',
        phase TEXT DEFAULT 'recon',
        started_at TEXT DEFAULT (datetime('now')),
        completed_at TEXT,
        findings_count INTEGER DEFAULT 0,
        error TEXT,
        checkpoint_json TEXT,
        FOREIGN KEY (target_id) REFERENCES targets(id)
    );

    CREATE TABLE IF NOT EXISTS findings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hunt_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        finding_type TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        url TEXT,
        evidence_json TEXT,
        payload TEXT,
        curl_command TEXT,
        status TEXT DEFAULT 'unverified',
        discovered_by TEXT,
        verified_by TEXT,
        discovered_at TEXT DEFAULT (datetime('now')),
        verified_at TEXT,
        reported_at TEXT,
        report_json TEXT,
        FOREIGN KEY (hunt_id) REFERENCES hunts(id),
        FOREIGN KEY (target_id) REFERENCES targets(id)
    );

    CREATE TABLE IF NOT EXISTS hypothesis_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hunt_id INTEGER NOT NULL,
        target_id INTEGER NOT NULL,
        hypothesis_id TEXT NOT NULL,
        hypothesis TEXT NOT NULL,
        category TEXT,
        confidence TEXT DEFAULT 'medium',
        reasoning TEXT,
        test_method TEXT,
        payload TEXT,
        success_indicator TEXT,
        status TEXT DEFAULT 'pending',
        result TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (hunt_id) REFERENCES hunts(id),
        FOREIGN KEY (target_id) REFERENCES targets(id)
    );

    CREATE TABLE IF NOT EXISTS recon_data (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        target_id INTEGER NOT NULL,
        data_type TEXT NOT NULL,
        data_json TEXT NOT NULL,
        source TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        FOREIGN KEY (target_id) REFERENCES targets(id)
    );

    CREATE TABLE IF NOT EXISTS worker_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hunt_id INTEGER,
        worker_type TEXT NOT NULL,
        model_used TEXT,
        task_type TEXT,
        input_summary TEXT,
        output_summary TEXT,
        tokens_used INTEGER,
        duration_seconds REAL,
        status TEXT DEFAULT 'success',
        error TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_findings_target ON findings(target_id);
    CREATE INDEX IF NOT EXISTS idx_findings_hunt ON findings(hunt_id);
    CREATE INDEX IF NOT EXISTS idx_findings_status ON findings(status);
    CREATE INDEX IF NOT EXISTS idx_hunts_target ON hunts(target_id);
    CREATE INDEX IF NOT EXISTS idx_hunts_status ON hunts(status);
    CREATE INDEX IF NOT EXISTS idx_hypothesis_hunt ON hypothesis_cards(hunt_id);
    """)
    conn.commit()
    conn.close()


class TargetDB:
    @staticmethod
    def add(domain: str, platform: str = "private", **kwargs) -> int:
        conn = get_db()
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO targets (domain, platform, program_url, scope_json,
                   bounty_min, bounty_max, priority, credentials_path, notes)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (domain, platform, kwargs.get("program_url", ""),
                 json.dumps(kwargs.get("scope", {})),
                 kwargs.get("bounty_min", 0), kwargs.get("bounty_max", 0),
                 kwargs.get("priority", 5), kwargs.get("credentials_path", ""),
                 kwargs.get("notes", ""))
            )
            conn.commit()
            if cur.lastrowid:
                return cur.lastrowid
            row = conn.execute("SELECT id FROM targets WHERE domain=?", (domain,)).fetchone()
            return row["id"]
        finally:
            conn.close()

    @staticmethod
    def get(domain: str) -> dict | None:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM targets WHERE domain=?", (domain,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def get_by_id(target_id: int) -> dict | None:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM targets WHERE id=?", (target_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def list_all() -> list[dict]:
        conn = get_db()
        try:
            rows = conn.execute("SELECT * FROM targets ORDER BY priority DESC, updated_at ASC").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_next_targets(limit: int = 3) -> list[dict]:
        """Get highest priority targets that need hunting."""
        conn = get_db()
        try:
            rows = conn.execute("""
                SELECT * FROM targets
                WHERE status != 'disabled'
                ORDER BY
                    priority DESC,
                    CASE WHEN last_full_hunt_at IS NULL THEN 0 ELSE 1 END,
                    last_full_hunt_at ASC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def update(domain: str, **kwargs):
        conn = get_db()
        try:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values()) + [domain]
            conn.execute(f"UPDATE targets SET {sets}, updated_at=datetime('now') WHERE domain=?", vals)
            conn.commit()
        finally:
            conn.close()


class HuntDB:
    @staticmethod
    def create(target_id: int, hunt_type: str = "full") -> int:
        conn = get_db()
        try:
            cur = conn.execute(
                "INSERT INTO hunts (target_id, hunt_type) VALUES (?, ?)",
                (target_id, hunt_type)
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    @staticmethod
    def get(hunt_id: int) -> dict | None:
        conn = get_db()
        try:
            row = conn.execute("SELECT * FROM hunts WHERE id=?", (hunt_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    @staticmethod
    def update(hunt_id: int, **kwargs):
        conn = get_db()
        try:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values()) + [hunt_id]
            conn.execute(f"UPDATE hunts SET {sets} WHERE id=?", vals)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_active() -> list[dict]:
        conn = get_db()
        try:
            rows = conn.execute("SELECT * FROM hunts WHERE status='running'").fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def checkpoint(hunt_id: int, phase: str, data: dict):
        conn = get_db()
        try:
            conn.execute(
                "UPDATE hunts SET phase=?, checkpoint_json=? WHERE id=?",
                (phase, json.dumps(data), hunt_id)
            )
            conn.commit()
        finally:
            conn.close()


class FindingDB:
    @staticmethod
    def create(hunt_id: int, target_id: int, finding_type: str, severity: str,
               title: str, **kwargs) -> int:
        conn = get_db()
        try:
            cur = conn.execute(
                """INSERT INTO findings (hunt_id, target_id, finding_type, severity, title,
                   description, url, evidence_json, payload, curl_command, discovered_by)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (hunt_id, target_id, finding_type, severity, title,
                 kwargs.get("description", ""), kwargs.get("url", ""),
                 json.dumps(kwargs.get("evidence", {})),
                 kwargs.get("payload", ""), kwargs.get("curl_command", ""),
                 kwargs.get("discovered_by", ""))
            )
            conn.commit()
            return cur.lastrowid
        finally:
            conn.close()

    @staticmethod
    def get_by_hunt(hunt_id: int) -> list[dict]:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM findings WHERE hunt_id=? ORDER BY severity, id", (hunt_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def get_by_target(target_id: int) -> list[dict]:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM findings WHERE target_id=? ORDER BY discovered_at DESC", (target_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def update(finding_id: int, **kwargs):
        conn = get_db()
        try:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values()) + [finding_id]
            conn.execute(f"UPDATE findings SET {sets} WHERE id=?", vals)
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_unverified(hunt_id: int) -> list[dict]:
        conn = get_db()
        try:
            rows = conn.execute(
                "SELECT * FROM findings WHERE hunt_id=? AND status='unverified'", (hunt_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()


class HypothesisDB:
    @staticmethod
    def create_batch(hunt_id: int, target_id: int, cards: list[dict]):
        conn = get_db()
        try:
            for card in cards:
                conn.execute(
                    """INSERT INTO hypothesis_cards (hunt_id, target_id, hypothesis_id,
                       hypothesis, category, confidence, reasoning, test_method, payload,
                       success_indicator)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (hunt_id, target_id, card.get("id", ""),
                     card["hypothesis"], card.get("category", ""),
                     card.get("confidence", "medium"), card.get("reasoning", ""),
                     card.get("test_method", ""), card.get("payload", ""),
                     card.get("success_indicator", ""))
                )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_pending(hunt_id: int) -> list[dict]:
        conn = get_db()
        try:
            rows = conn.execute(
                """SELECT * FROM hypothesis_cards WHERE hunt_id=? AND status='pending'
                   ORDER BY CASE confidence WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END""",
                (hunt_id,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    @staticmethod
    def update(card_id: int, **kwargs):
        conn = get_db()
        try:
            sets = ", ".join(f"{k}=?" for k in kwargs)
            vals = list(kwargs.values()) + [card_id]
            conn.execute(f"UPDATE hypothesis_cards SET {sets} WHERE id=?", vals)
            conn.commit()
        finally:
            conn.close()


class WorkerLogDB:
    @staticmethod
    def log(worker_type: str, model_used: str, task_type: str, **kwargs):
        conn = get_db()
        try:
            conn.execute(
                """INSERT INTO worker_logs (hunt_id, worker_type, model_used, task_type,
                   input_summary, output_summary, tokens_used, duration_seconds, status, error)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (kwargs.get("hunt_id"), worker_type, model_used, task_type,
                 kwargs.get("input_summary", ""), kwargs.get("output_summary", ""),
                 kwargs.get("tokens_used", 0), kwargs.get("duration_seconds", 0),
                 kwargs.get("status", "success"), kwargs.get("error"))
            )
            conn.commit()
        finally:
            conn.close()


class ReconDB:
    @staticmethod
    def store(target_id: int, data_type: str, data: dict, source: str = ""):
        conn = get_db()
        try:
            conn.execute(
                "INSERT INTO recon_data (target_id, data_type, data_json, source) VALUES (?, ?, ?, ?)",
                (target_id, data_type, json.dumps(data), source)
            )
            conn.commit()
        finally:
            conn.close()

    @staticmethod
    def get_latest(target_id: int, data_type: str) -> dict | None:
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT * FROM recon_data WHERE target_id=? AND data_type=? ORDER BY created_at DESC LIMIT 1",
                (target_id, data_type)
            ).fetchone()
            if row:
                result = dict(row)
                result["data"] = json.loads(result["data_json"])
                return result
            return None
        finally:
            conn.close()
