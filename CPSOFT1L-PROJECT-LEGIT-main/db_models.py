import os
import sqlite3
import hashlib
from typing import Optional
from contextlib import closing
from dotenv import load_dotenv
from pydantic import BaseModel, Field, field_validator

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
if DATABASE_URL and DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

class PostgresCursorWrapper:
    def __init__(self, cursor):
        self.cursor = cursor
    def execute(self, query, vars=None):
        query = query.replace('?', '%s')
        return self.cursor.execute(query, vars)
    def fetchone(self):
        return self.cursor.fetchone()
    def fetchall(self):
        return self.cursor.fetchall()
    def close(self):
        return self.cursor.close()
    @property
    def rowcount(self):
        return self.cursor.rowcount

class PostgresConnectionWrapper:
    def __init__(self, conn):
        self.conn = conn
    def cursor(self):
        return PostgresCursorWrapper(self.conn.cursor())
    def commit(self):
        return self.conn.commit()
    def rollback(self):
        return self.conn.rollback()
    def close(self):
        return self.conn.close()

def get_connection():
    if DATABASE_URL:
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(DATABASE_URL, cursor_factory=psycopg2.extras.DictCursor)
        return PostgresConnectionWrapper(conn)
    else:
        conn = sqlite3.connect("lms_mock.db")
        conn.row_factory = sqlite3.Row
        return conn

def hash_password(password: str, salt: Optional[str] = None):
    if not salt:
        salt = os.urandom(16).hex()
    calc_hash = hashlib.sha256((password + salt).encode('utf-8')).hexdigest()
    return calc_hash, salt

def init_db():
    is_postgres = bool(DATABASE_URL)
    pk_type = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    bool_type = "BOOLEAN" if is_postgres else "INTEGER"
    default_false = "FALSE" if is_postgres else "0"
    
    with closing(get_connection()) as conn:
        cursor = conn.cursor()
        
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS users (
                id {pk_type},
                firstname TEXT NOT NULL,
                lastname TEXT NOT NULL,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                salt TEXT NOT NULL,
                role TEXT NOT NULL,
                gender TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS courses (
                id {pk_type},
                code TEXT UNIQUE NOT NULL,
                title TEXT NOT NULL,
                description TEXT,
                instructor_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
                enrollment_code TEXT UNIQUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS enrollments (
                id {pk_type},
                student_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                enrolled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, course_id)
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS modules (
                id {pk_type},
                course_id INTEGER REFERENCES courses(id) ON DELETE CASCADE,
                section_name TEXT DEFAULT 'General',
                title TEXT NOT NULL,
                type TEXT NOT NULL,
                content TEXT,
                due_date TEXT,
                file_path TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS student_progress (
                id {pk_type},
                student_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                module_id INTEGER REFERENCES modules(id) ON DELETE CASCADE,
                completed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, module_id)
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS student_submissions (
                id {pk_type},
                student_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                module_id INTEGER REFERENCES modules(id) ON DELETE CASCADE,
                score INTEGER DEFAULT 0,
                total_questions INTEGER DEFAULT 0,
                answers_json TEXT,
                file_filename TEXT,
                manual_grade TEXT,
                feedback TEXT,
                submitted_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(student_id, module_id)
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS content_blocks (
                id {pk_type},
                module_id INTEGER REFERENCES modules(id) ON DELETE CASCADE,
                block_type TEXT NOT NULL,
                block_content TEXT,
                file_name TEXT
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS quiz_questions (
                id {pk_type},
                module_id INTEGER REFERENCES modules(id) ON DELETE CASCADE,
                question_text TEXT NOT NULL,
                question_type TEXT DEFAULT 'multiple_choice',
                option_a TEXT,
                option_b TEXT,
                option_c TEXT,
                option_d TEXT,
                correct_answer TEXT
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS messages (
                id {pk_type},
                sender_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                recipient_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                subject TEXT NOT NULL,
                body TEXT NOT NULL,
                sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read {bool_type} DEFAULT {default_false}
            )
        """)

        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS notifications (
                id {pk_type},
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                message TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                is_read {bool_type} DEFAULT {default_false}
            )
        """)
        
        cursor.execute(f"""
            CREATE TABLE IF NOT EXISTS calendar_events (
                id {pk_type},
                user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
                title TEXT NOT NULL,
                event_date TEXT NOT NULL,
                category_color TEXT DEFAULT 'bg-primary',
                description TEXT
            )
        """)
        # Backward-compatible migrations for existing SQLite/PostgreSQL databases.
        migrations = {
            'modules': [('time_limit_minutes', 'INTEGER DEFAULT 0'), ('pass_percentage', 'INTEGER DEFAULT 70')],
            'quiz_questions': [('question_type', "TEXT DEFAULT 'multiple_choice'")],
            'courses': [('grades_posted', f'{bool_type} DEFAULT {default_false}'), ('records_locked', f'{bool_type} DEFAULT {default_false}')],
        }
        for table, fields in migrations.items():
            for column, definition in fields:
                try:
                    cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
                    conn.commit()
                except Exception:
                    conn.rollback()
        cursor.execute(f"""CREATE TABLE IF NOT EXISTS assessment_attempts (
            student_id INTEGER NOT NULL, module_id INTEGER NOT NULL,
            started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (student_id, module_id)
        )""")
        conn.commit()
        try:
            cursor.execute("ALTER TABLE assessment_attempts ADD COLUMN draft_answers_json TEXT")
            conn.commit()
        except Exception:
            conn.rollback()

init_db()

class UserRegisterSchema(BaseModel):
    firstname: str = Field(..., min_length=1, max_length=50)
    lastname: str = Field(..., min_length=1, max_length=50)
    email: str = Field(..., min_length=6, max_length=100)
    password: str = Field(..., min_length=8)
    role: str = Field(default="STUDENT")
    gender: Optional[str] = Field(default=None)

    @field_validator("role")
    def role_validation(cls, r):
        valid_roles = ["STUDENT", "TEACHER"]
        if r.upper() not in valid_roles:
            raise ValueError(f"Invalid role. Must be one of: {', '.join(valid_roles)}")
        return r.upper()
