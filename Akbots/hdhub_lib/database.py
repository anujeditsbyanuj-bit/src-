"""
Database module for managing bot settings and post history
Uses SQLite for persistent storage
"""

import sqlite3
import os
from datetime import datetime
from typing import List, Dict, Optional


class Database:
    def __init__(self, db_path: Optional[str] = None):
        """Initialize database connection"""
        self.db_path = self._resolve_db_path(db_path)
        self.conn = None
        self._init_database()

    def _resolve_db_path(self, db_path: Optional[str]) -> str:
        """
        Resolve database path with support for BOT_DB_PATH env override.
        Falls back to project-local persistent file path.
        """
        env_db_path = os.getenv('BOT_DB_PATH', '').strip()

        if db_path:
            resolved_path = db_path
        elif env_db_path:
            resolved_path = env_db_path
        else:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            resolved_path = os.path.join(base_dir, 'bot_data.db')

        directory = os.path.dirname(os.path.abspath(resolved_path))
        if directory and not os.path.exists(directory):
            os.makedirs(directory, exist_ok=True)

        return resolved_path
    
    def _init_database(self):
        """Create database tables if they don't exist"""
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()
        
        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Posts history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                url TEXT UNIQUE NOT NULL,
                posted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        # Create index for faster lookups
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_url ON posts(url)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_posted_at ON posts(posted_at DESC)
        ''')
        
        self.conn.commit()
    
    def set_setting(self, key: str, value: str):
        """Set a configuration setting"""
        cursor = self.conn.cursor()
        cursor.execute('''
            INSERT OR REPLACE INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
        ''', (key, value))
        self.conn.commit()
    
    def get_setting(self, key: str) -> Optional[str]:
        """Get a configuration setting"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT value FROM settings WHERE key = ?', (key,))
        result = cursor.fetchone()
        return result['value'] if result else None
    
    def add_post(self, title: str, url: str):
        """Add a post to history"""
        cursor = self.conn.cursor()
        try:
            cursor.execute('''
                INSERT INTO posts (title, url)
                VALUES (?, ?)
            ''', (title, url))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            # URL already exists
            return False
    
    def is_posted(self, url: str) -> bool:
        """Check if URL has already been posted"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT 1 FROM posts WHERE url = ?', (url,))
        return cursor.fetchone() is not None
    
    def get_recent_posts(self, limit: int = 10) -> List[Dict]:
        """Get recent posts"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT title, url, posted_at
            FROM posts
            ORDER BY posted_at DESC
            LIMIT ?
        ''', (limit,))
        return [dict(row) for row in cursor.fetchall()]
    
    def get_total_posts(self) -> int:
        """Get total number of posts"""
        cursor = self.conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM posts')
        return cursor.fetchone()['count']
    
    def get_posts_count_today(self) -> int:
        """Get number of posts made today"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT COUNT(*) as count FROM posts
            WHERE DATE(posted_at) = DATE('now')
        ''')
        return cursor.fetchone()['count']
    
    def get_unique_content_count(self) -> int:
        """Get count of unique content"""
        return self.get_total_posts()
    
    def get_last_post_time(self) -> Optional[str]:
        """Get timestamp of last post"""
        cursor = self.conn.cursor()
        cursor.execute('''
            SELECT posted_at FROM posts
            ORDER BY posted_at DESC
            LIMIT 1
        ''')
        result = cursor.fetchone()
        return result['posted_at'] if result else None
    
    def get_size_mb(self) -> float:
        """Get database size in MB"""
        if os.path.exists(self.db_path):
            size_bytes = os.path.getsize(self.db_path)
            return size_bytes / (1024 * 1024)
        return 0.0
    
    def update_post_timestamp(self, url: str):
        """Update the timestamp for a post (when links are updated)"""
        cursor = self.conn.cursor()
        cursor.execute('''
            UPDATE posts
            SET updated_at = CURRENT_TIMESTAMP
            WHERE url = ?
        ''', (url,))
        self.conn.commit()
    
    def clear_old_posts(self, days: int = 90):
        """Clear posts older than specified days"""
        cursor = self.conn.cursor()
        cursor.execute('''
            DELETE FROM posts
            WHERE posted_at < datetime('now', '-' || ? || ' days')
        ''', (days,))
        self.conn.commit()
        return cursor.rowcount
    
    def close(self):
        """Close database connection"""
        if self.conn:
            self.conn.close()
