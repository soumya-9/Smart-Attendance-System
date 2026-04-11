import calendar
import logging
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash


BASE_DIR = Path(__file__).resolve().parent
DB_NAME = BASE_DIR / "attendance.db"
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
LAST_3_DAYS_TOTAL_CLASSES = 7
DAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]
SESSION_STATUSES = {"Scheduled", "Active", "Delayed", "Cancelled", "Completed"}
DEFAULT_LOW_ATTENDANCE_THRESHOLD = 75.0
DEFAULT_MAX_ATTENDANCE_RADIUS_METERS = 60.0
DEFAULT_POST_ATTENDANCE_TRACKING_MINUTES = 5
DEFAULT_TRACKING_OUT_OF_RANGE_LIMIT = 3
DEFAULT_TRACKING_HEARTBEAT_INTERVAL_SECONDS = 15
DEFAULT_TRACKING_HEARTBEAT_GRACE_SECONDS = 45


def get_connection():
    conn = sqlite3.connect(DB_NAME, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def get_recent_dates(days=LAST_3_DAYS_TOTAL_CLASSES):
    return [
        (datetime.now().date() - timedelta(days=offset)).strftime("%Y-%m-%d")
        for offset in range(days - 1, -1, -1)
    ]


def column_exists(cursor, table_name, column_name):
    cursor.execute(f"PRAGMA table_info({table_name})")
    return any(row["name"] == column_name for row in cursor.fetchall())


def ensure_column(cursor, table_name, column_name, column_definition):
    if not column_exists(cursor, table_name, column_name):
        cursor.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS students (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            class_name TEXT DEFAULT '',
            enrollment_number TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL UNIQUE,
            image_path TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """
    )

    ensure_column(cur, "students", "class_name", "TEXT DEFAULT ''")
    ensure_column(cur, "students", "enrollment_number", "TEXT DEFAULT ''")
    ensure_column(cur, "students", "email", "TEXT DEFAULT ''")
    ensure_column(cur, "students", "mobile_number", "TEXT DEFAULT ''")
    ensure_column(cur, "students", "image_path", "TEXT DEFAULT ''")
    ensure_column(cur, "students", "created_at", "TEXT DEFAULT ''")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS attendance (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            name TEXT NOT NULL,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            emotion TEXT,
            FOREIGN KEY (student_id) REFERENCES students (id)
        )
        """
    )

    ensure_column(cur, "attendance", "student_id", "INTEGER")
    ensure_column(cur, "attendance", "emotion", "TEXT")
    ensure_column(cur, "attendance", "session_id", "INTEGER")
    ensure_column(cur, "attendance", "class_name", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "subject_name", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "teacher_name", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "attendance_date", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "attendance_time", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "latitude", "REAL")
    ensure_column(cur, "attendance", "longitude", "REAL")
    ensure_column(cur, "attendance", "distance_meters", "REAL")
    ensure_column(cur, "attendance", "face_verified", "INTEGER DEFAULT 0")
    ensure_column(cur, "attendance", "spoof_status", "TEXT DEFAULT 'pending'")
    ensure_column(cur, "attendance", "status", "TEXT DEFAULT 'Present'")
    ensure_column(cur, "attendance", "rejection_reason", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "marked_via", "TEXT DEFAULT 'face_scan'")
    ensure_column(cur, "attendance", "override_permission_id", "INTEGER")
    ensure_column(cur, "attendance", "override_granted_by", "INTEGER")
    ensure_column(cur, "attendance", "override_used", "INTEGER DEFAULT 0")
    ensure_column(cur, "attendance", "proof_snapshot_path", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "recorded_identity_name", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "notification_sent", "INTEGER DEFAULT 0")
    ensure_column(cur, "attendance", "correction_request_id", "INTEGER")
    ensure_column(cur, "attendance", "original_status", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "tracking_started_at", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "tracking_expires_at", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "tracking_status", "TEXT DEFAULT 'Not Started'")
    ensure_column(cur, "attendance", "attendance_status", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "tracking_active", "INTEGER DEFAULT 0")
    ensure_column(cur, "attendance", "tracking_completed_at", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "attendance_cancelled_at", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "cancellation_reason", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "tracking_window_starts_at", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "tracking_reference_latitude", "REAL")
    ensure_column(cur, "attendance", "tracking_reference_longitude", "REAL")
    ensure_column(cur, "attendance", "tracking_reference_radius_meters", "REAL")
    ensure_column(cur, "attendance", "last_location_latitude", "REAL")
    ensure_column(cur, "attendance", "last_location_longitude", "REAL")
    ensure_column(cur, "attendance", "last_location_accuracy_meters", "REAL")
    ensure_column(cur, "attendance", "last_raw_distance_meters", "REAL")
    ensure_column(cur, "attendance", "last_range_state", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "last_location_checked_at", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "out_of_range_count", "INTEGER DEFAULT 0")
    ensure_column(cur, "attendance", "created_at", "TEXT DEFAULT ''")
    ensure_column(cur, "attendance", "updated_at", "TEXT DEFAULT ''")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            setting_key TEXT PRIMARY KEY,
            setting_value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS working_days (
            day_name TEXT PRIMARY KEY,
            is_working INTEGER NOT NULL DEFAULT 1,
            updated_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS holidays (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            holiday_date TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS class_schedules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            class_name TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            teacher_name TEXT NOT NULL,
            room_name TEXT DEFAULT '',
            session_date TEXT DEFAULT '',
            day_name TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            attendance_open_time TEXT NOT NULL,
            attendance_close_time TEXT NOT NULL,
            late_close_time TEXT NOT NULL,
            gps_latitude REAL,
            gps_longitude REAL,
            allowed_radius_meters REAL DEFAULT 100,
            is_active INTEGER DEFAULT 1,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    ensure_column(cur, "class_schedules", "room_name", "TEXT DEFAULT ''")
    ensure_column(cur, "class_schedules", "session_date", "TEXT DEFAULT ''")
    ensure_column(cur, "class_schedules", "post_attendance_tracking_minutes", "INTEGER")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS class_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER,
            class_name TEXT NOT NULL,
            subject_name TEXT NOT NULL,
            teacher_name TEXT NOT NULL,
            room_name TEXT DEFAULT '',
            day_name TEXT NOT NULL,
            session_date TEXT NOT NULL,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            attendance_open_time TEXT NOT NULL,
            attendance_close_time TEXT NOT NULL,
            late_close_time TEXT NOT NULL,
            gps_latitude REAL,
            gps_longitude REAL,
            allowed_radius_meters REAL DEFAULT 100,
            session_status TEXT NOT NULL DEFAULT 'Scheduled',
            status_reason TEXT DEFAULT '',
            substitute_teacher TEXT DEFAULT '',
            is_substitute_class INTEGER DEFAULT 0,
            activated_by INTEGER,
            completed_at TEXT DEFAULT '',
            absent_processed INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(schedule_id, session_date)
        )
        """
    )
    ensure_column(cur, "class_sessions", "room_name", "TEXT DEFAULT ''")
    ensure_column(cur, "class_sessions", "post_attendance_tracking_minutes", "INTEGER")

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS gps_change_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            target_type TEXT NOT NULL,
            schedule_id INTEGER,
            session_id INTEGER,
            old_latitude REAL,
            old_longitude REAL,
            old_radius REAL,
            new_latitude REAL,
            new_longitude REAL,
            new_radius REAL,
            changed_by INTEGER NOT NULL,
            changed_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS deleted_class_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            schedule_id INTEGER NOT NULL,
            session_date TEXT NOT NULL,
            deleted_by INTEGER,
            deleted_at TEXT NOT NULL,
            UNIQUE(schedule_id, session_date)
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS override_permissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            granted_by INTEGER NOT NULL,
            reason TEXT NOT NULL,
            granted_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            is_used INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS correction_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER NOT NULL,
            session_id INTEGER NOT NULL,
            attendance_record_id INTEGER,
            reason TEXT NOT NULL,
            requested_status TEXT DEFAULT 'Present',
            status TEXT NOT NULL DEFAULT 'Pending',
            admin_notes TEXT DEFAULT '',
            reviewed_by INTEGER,
            reviewed_at TEXT DEFAULT '',
            created_at TEXT NOT NULL
        )
        """
    )

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS correction_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            correction_request_id INTEGER NOT NULL,
            attendance_record_id INTEGER,
            action TEXT NOT NULL,
            previous_status TEXT DEFAULT '',
            new_status TEXT DEFAULT '',
            acted_by INTEGER,
            acted_at TEXT NOT NULL,
            notes TEXT DEFAULT ''
        )
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attendance_name_date
        ON attendance (name, date)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attendance_student_date
        ON attendance (student_id, date)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attendance_session_student
        ON attendance (session_id, student_id)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_session_date_class
        ON class_sessions (session_date, class_name)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_schedule_session_date
        ON class_schedules (session_date, start_time)
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_deleted_class_sessions_lookup
        ON deleted_class_sessions (schedule_id, session_date)
        """
    )

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for day_name in DAY_NAMES:
        cur.execute(
            """
            INSERT OR IGNORE INTO working_days (day_name, is_working, updated_at)
            VALUES (?, ?, ?)
            """,
            (day_name, 0 if day_name == "Sunday" else 1, timestamp),
        )

    cur.execute(
        """
        INSERT OR IGNORE INTO app_settings (setting_key, setting_value, updated_at)
        VALUES ('low_attendance_threshold', '75', ?)
        """,
        (timestamp,),
    )

    cur.execute(
        """
        UPDATE attendance
        SET attendance_date = COALESCE(NULLIF(attendance_date, ''), date),
            attendance_time = COALESCE(NULLIF(attendance_time, ''), time),
            created_at = COALESCE(NULLIF(created_at, ''), date || ' ' || time),
            updated_at = COALESCE(NULLIF(updated_at, ''), date || ' ' || time),
            recorded_identity_name = COALESCE(NULLIF(recorded_identity_name, ''), name),
            original_status = COALESCE(NULLIF(original_status, ''), status)
        """
    )

    cur.execute(
        """
        UPDATE class_schedules
        SET session_date = COALESCE(
                NULLIF(session_date, ''),
                (
                    SELECT MIN(class_sessions.session_date)
                    FROM class_sessions
                    WHERE class_sessions.schedule_id = class_schedules.id
                      AND class_sessions.session_date >= date('now')
                ),
                (
                    SELECT MIN(class_sessions.session_date)
                    FROM class_sessions
                    WHERE class_sessions.schedule_id = class_schedules.id
                ),
                SUBSTR(created_at, 1, 10),
                date('now')
            )
        WHERE COALESCE(session_date, '') = ''
        """
    )

    cur.execute(
        """
        UPDATE class_schedules
        SET day_name = CASE
            WHEN COALESCE(session_date, '') <> '' THEN
                CASE CAST(strftime('%w', session_date) AS INTEGER)
                    WHEN 0 THEN 'Sunday'
                    WHEN 1 THEN 'Monday'
                    WHEN 2 THEN 'Tuesday'
                    WHEN 3 THEN 'Wednesday'
                    WHEN 4 THEN 'Thursday'
                    WHEN 5 THEN 'Friday'
                    WHEN 6 THEN 'Saturday'
                END
            ELSE day_name
        END
        WHERE COALESCE(session_date, '') <> ''
        """
    )

    cur.execute(
        """
        DELETE FROM class_sessions
        WHERE schedule_id IS NOT NULL
          AND session_date >= date('now')
          AND id NOT IN (
              SELECT DISTINCT session_id
              FROM attendance
              WHERE session_id IS NOT NULL
          )
          AND EXISTS (
              SELECT 1
              FROM class_schedules
              WHERE class_schedules.id = class_sessions.schedule_id
                AND COALESCE(class_schedules.session_date, '') <> ''
                AND class_schedules.session_date <> class_sessions.session_date
          )
        """
    )

    cur.execute(
        """
        UPDATE attendance
        SET tracking_status = 'Not Started'
        WHERE COALESCE(tracking_status, '') = ''
        """
    )

    cur.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_attendance_student_session_date
        ON attendance(student_id, session_id, attendance_date)
        """
    )
    cur.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_attendance_unique_student_session_date_active
        ON attendance(student_id, session_id, attendance_date)
        WHERE student_id IS NOT NULL
          AND session_id IS NOT NULL
          AND COALESCE(attendance_date, '') <> ''
          AND COALESCE(status, '') != 'Rejected'
        """
    )

    cur.execute(
        """
        UPDATE class_sessions
        SET post_attendance_tracking_minutes = (
            SELECT class_schedules.post_attendance_tracking_minutes
            FROM class_schedules
            WHERE class_schedules.id = class_sessions.schedule_id
        )
        WHERE post_attendance_tracking_minutes IS NULL
        """
    )

    conn.commit()
    conn.close()


def create_admin(email, password):
    conn = get_connection()
    try:
        cur = conn.cursor()
        created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur.execute(
            """
            INSERT INTO admins (email, password_hash, created_at)
            VALUES (?, ?, ?)
            """,
            (email.strip().lower(), generate_password_hash(password), created_at),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_admin_by_email(email):
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, email, password_hash, created_at
            FROM admins
            WHERE email = ?
            """,
            (email.strip().lower(),),
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def get_primary_admin():
    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT id, email, password_hash, created_at
            FROM admins
            ORDER BY id ASC
            LIMIT 1
            """
        )
        row = cur.fetchone()
        return dict(row) if row else None
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def ensure_default_admin(email, password):
    try:
        admin = get_admin_by_email(email)
        if admin:
            return admin["id"]
        return create_admin(email, password)
    except sqlite3.Error:
        return None


def verify_admin_credentials(email, password):
    try:
        admin = get_admin_by_email(email)
        if not admin:
            return None
        if check_password_hash(admin["password_hash"], password):
            return admin
        return None
    except (sqlite3.Error, ValueError):
        return None


def create_student(name, class_name, enrollment_number, email, mobile_number, image_path):
    conn = get_connection()
    cur = conn.cursor()

    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT INTO students (
            name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, class_name, enrollment_number, email, mobile_number, image_path, created_at),
    )

    conn.commit()
    student_id = cur.lastrowid
    conn.close()
    return student_id


def register_known_face_seed(name, image_path):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("SELECT id FROM students WHERE name = ?", (name,))
    existing = cur.fetchone()
    if existing:
        cur.execute(
            "UPDATE students SET image_path = ? WHERE id = ?",
            (image_path, existing["id"]),
        )
        conn.commit()
        conn.close()
        return existing["id"]

    safe_key = name.lower().replace(" ", "_")
    enrollment_number = f"LEGACY-{safe_key}"
    email = f"{safe_key}@example.com"
    created_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute(
        """
        INSERT OR IGNORE INTO students (
            name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (name, "Unassigned", enrollment_number, email, "", image_path, created_at),
    )
    conn.commit()
    student_id = cur.lastrowid
    conn.close()
    return student_id


def get_all_students():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        FROM students
        ORDER BY name ASC
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def get_student_by_name(name):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        FROM students
        WHERE name = ?
        """,
        (name,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_student_by_id(student_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        FROM students
        WHERE id = ?
        """,
        (student_id,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_student_by_enrollment(enrollment_number):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        FROM students
        WHERE enrollment_number = ?
        """,
        (enrollment_number,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def get_student_by_email(email):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        FROM students
        WHERE email = ?
        """,
        (email,),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def verify_student_credentials(email, enrollment_number):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, class_name, enrollment_number, email, mobile_number, image_path, created_at
        FROM students
        WHERE email = ? AND enrollment_number = ?
        """,
        (str(email or "").strip().lower(), str(enrollment_number or "").strip().upper()),
    )
    row = cur.fetchone()
    conn.close()
    return dict(row) if row else None


def update_student(
    student_id,
    name,
    class_name,
    enrollment_number,
    email,
    mobile_number,
    image_path=None,
):
    conn = get_connection()
    cur = conn.cursor()
    if image_path is None:
        cur.execute(
            """
            UPDATE students
            SET name = ?, class_name = ?, enrollment_number = ?, email = ?, mobile_number = ?
            WHERE id = ?
            """,
            (name, class_name, enrollment_number, email, mobile_number, student_id),
        )
    else:
        cur.execute(
            """
            UPDATE students
            SET name = ?, class_name = ?, enrollment_number = ?, email = ?, mobile_number = ?, image_path = ?
            WHERE id = ?
            """,
            (name, class_name, enrollment_number, email, mobile_number, image_path, student_id),
        )
    cur.execute(
        """
        UPDATE attendance
        SET name = ?
        WHERE student_id = ?
        """,
        (name, student_id),
    )
    conn.commit()
    conn.close()


def get_known_face_records():
    students = get_all_students()
    return {
        student["name"]: student["image_path"]
        for student in students
        if student.get("image_path")
    }


def get_legacy_students():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        SELECT id, name, class_name, enrollment_number, email, image_path, created_at
        FROM students
        WHERE enrollment_number LIKE 'LEGACY-%'
        ORDER BY name ASC
        """
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()
    return rows


def reassign_attendance_to_student(source_student, target_student):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        """
        UPDATE attendance
        SET student_id = ?, name = ?
        WHERE student_id = ?
           OR (student_id IS NULL AND name = ?)
        """,
        (
            target_student["id"],
            target_student["name"],
            source_student["id"],
            source_student["name"],
        ),
    )
    conn.commit()
    conn.close()


def delete_student(student_id):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM attendance WHERE student_id = ?", (student_id,))
    cur.execute("DELETE FROM students WHERE id = ?", (student_id,))
    conn.commit()
    conn.close()


def mark_attendance(name, emotion):
    student = get_student_by_name(name)
    if student is None:
        return False

    conn = get_connection()
    cur = conn.cursor()

    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M:%S")

    cur.execute(
        "SELECT id FROM attendance WHERE student_id = ? AND date = ?",
        (student["id"], today),
    )

    if cur.fetchone() is None:
        cur.execute(
            """
            INSERT INTO attendance (student_id, name, date, time, emotion)
            VALUES (?, ?, ?, ?, ?)
            """,
            (student["id"], name, today, current_time, emotion),
        )
        conn.commit()
        conn.close()
        return True

    conn.close()
    return False


def get_today_present_students():
    conn = get_connection()
    cur = conn.cursor()

    today = datetime.now().strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT DISTINCT COALESCE(students.name, attendance.name) AS student_name
        FROM attendance
        LEFT JOIN students ON students.id = attendance.student_id
        WHERE date = ?
        ORDER BY student_name ASC
        """,
        (today,),
    )
    rows = cur.fetchall()

    conn.close()
    return [row["student_name"] for row in rows]


def get_last_3_days_attendance_map(student_names):
    attendance_count = {student: 0 for student in student_names}
    if not student_names:
        return attendance_count

    conn = get_connection()
    cur = conn.cursor()

    start_date = (datetime.now().date() - timedelta(days=2)).strftime("%Y-%m-%d")
    cur.execute(
        """
        SELECT COALESCE(students.name, attendance.name) AS student_name, COUNT(DISTINCT attendance.date) AS present_days
        FROM attendance
        LEFT JOIN students ON students.id = attendance.student_id
        WHERE date >= ?
        GROUP BY student_name
        """,
        (start_date,),
    )
    rows = cur.fetchall()

    conn.close()

    for row in rows:
        name = row["student_name"]
        if name in attendance_count:
            attendance_count[name] = row["present_days"]

    return attendance_count


def get_last_3_days_attendance_details(student_names):
    recent_dates = get_recent_dates()
    details = {
        student: {
            "present_dates": [],
            "attendance": 0,
            "total_classes": LAST_3_DAYS_TOTAL_CLASSES,
            "percentage": 0,
        }
        for student in student_names
    }
    if not student_names:
        return details

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        f"""
        SELECT COALESCE(students.name, attendance.name) AS student_name, attendance.date
        FROM attendance
        LEFT JOIN students ON students.id = attendance.student_id
        WHERE attendance.date IN ({",".join("?" for _ in recent_dates)})
        """,
        recent_dates,
    )
    rows = cur.fetchall()
    conn.close()

    for row in rows:
        name = row["student_name"]
        if name in details and row["date"] not in details[name]["present_dates"]:
            details[name]["present_dates"].append(row["date"])

    for name, student_details in details.items():
        attendance = len(student_details["present_dates"])
        student_details["attendance"] = attendance
        student_details["percentage"] = round(
            (attendance / LAST_3_DAYS_TOTAL_CLASSES) * 100, 2
        )

    return details


def get_last_3_days_chart(student_names):
    attendance_details = get_last_3_days_attendance_details(student_names)
    return [
        {
            "name": name,
            "attendance": attendance_details.get(name, {}).get("attendance", 0),
            "total_classes": LAST_3_DAYS_TOTAL_CLASSES,
            "percentage": attendance_details.get(name, {}).get("percentage", 0),
        }
        for name in student_names
    ]


def get_month_calendar_data(student_names, year=None, month=None):
    today = datetime.now().date()
    target_year = year or today.year
    target_month = month or today.month
    _, total_days = calendar.monthrange(target_year, target_month)

    attendance_by_student = {student: set() for student in student_names}
    if student_names:
        conn = get_connection()
        cur = conn.cursor()
        month_prefix = f"{target_year:04d}-{target_month:02d}-"
        cur.execute(
            """
            SELECT COALESCE(students.name, attendance.name) AS student_name, attendance.date
            FROM attendance
            LEFT JOIN students ON students.id = attendance.student_id
            WHERE attendance.date LIKE ?
            """,
            (f"{month_prefix}%",),
        )
        rows = cur.fetchall()
        conn.close()

        for row in rows:
            student_name = row["student_name"]
            if student_name in attendance_by_student:
                attendance_by_student[student_name].add(row["date"])

    calendar_data = {}
    for student_name in student_names:
        days = []
        present_dates = attendance_by_student.get(student_name, set())
        for day in range(1, total_days + 1):
            date_value = datetime(target_year, target_month, day).date()
            iso_date = date_value.strftime("%Y-%m-%d")
            if date_value > today:
                status = "upcoming"
            elif iso_date in present_dates:
                status = "present"
            else:
                status = "absent"

            days.append(
                {
                    "day": day,
                    "date": iso_date,
                    "status": status,
                    "weekday": date_value.strftime("%a"),
                }
            )

        calendar_data[student_name] = days

    return {
        "year": target_year,
        "month": target_month,
        "month_name": calendar.month_name[target_month],
        "days": calendar_data,
    }


def get_dashboard_stats():
    today = datetime.now().date()
    ensure_sessions_for_date(today)
    students = get_all_students()
    student_names = [student["name"] for student in students]
    present_today = get_today_present_students()
    absent_students = [name for name in student_names if name not in present_today]

    conn = get_connection()
    try:
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                SUM(CASE WHEN status = 'Late' THEN 1 ELSE 0 END) AS late_count,
                SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) AS absent_count,
                SUM(CASE WHEN status = 'Rejected' THEN 1 ELSE 0 END) AS rejected_count
            FROM attendance
            WHERE attendance_date = ?
            """,
            (today.strftime("%Y-%m-%d"),),
        ).fetchone()
        active_sessions = conn.execute(
            "SELECT * FROM class_sessions WHERE session_date = ? ORDER BY start_time ASC",
            (today.strftime("%Y-%m-%d"),),
        ).fetchall()
        class_rows = conn.execute(
            """
            SELECT class_sessions.class_name,
                   COUNT(DISTINCT class_sessions.id) AS total_sessions,
                   SUM(CASE WHEN attendance.status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN attendance.status = 'Late' THEN 1 ELSE 0 END) AS late_count,
                   SUM(CASE WHEN attendance.status = 'Absent' THEN 1 ELSE 0 END) AS absent_count
            FROM class_sessions
            LEFT JOIN attendance ON attendance.session_id = class_sessions.id
            GROUP BY class_sessions.class_name
            ORDER BY class_sessions.class_name ASC
            """
        ).fetchall()
        corrections = conn.execute(
            """
            SELECT correction_requests.*, students.name AS student_name, class_sessions.subject_name, class_sessions.session_date
            FROM correction_requests
            JOIN students ON students.id = correction_requests.student_id
            JOIN class_sessions ON class_sessions.id = correction_requests.session_id
            ORDER BY CASE correction_requests.status WHEN 'Pending' THEN 0 ELSE 1 END,
                     correction_requests.created_at DESC
            LIMIT 10
            """
        ).fetchall()
    finally:
        conn.close()

    student_summaries = {
        student["name"]: get_student_attendance_summary(student["id"])
        for student in students
    }
    stats_3_days = get_last_3_days_attendance_map(student_names, summary_map=student_summaries)
    attendance_details = get_last_3_days_attendance_details(student_names, summary_map=student_summaries)
    chart_data = get_last_3_days_chart(student_names, summary_map=student_summaries)
    calendar_data = get_month_calendar_data(student_names, summary_map=student_summaries)
    highest_student = sorted(stats_3_days.items(), key=lambda item: (-item[1], item[0]))[0][0] if stats_3_days else None
    lowest_student = sorted(stats_3_days.items(), key=lambda item: (item[1], item[0]))[0][0] if stats_3_days else None
    threshold = get_low_attendance_threshold()

    low_attendance_students = []
    for student in students:
        summary = student_summaries.get(student["name"]) or {}
        total_classes = int(summary.get("total_classes") or 0)
        attended = int(summary.get("attended_classes") or 0)
        percentage = round((attended / total_classes) * 100, 2) if total_classes else 0
        if total_classes and percentage < threshold:
            low_attendance_students.append(
                {
                    "id": student["id"],
                    "name": student["name"],
                    "class_name": student["class_name"],
                    "attendance_percentage": percentage,
                    "total_classes": total_classes,
                    "attended_classes": attended,
                }
            )

    return {
        "total_students": len(student_names),
        "present_count": counts["present_count"] or 0,
        "late_count": counts["late_count"] or 0,
        "absent_count": counts["absent_count"] or 0,
        "rejected_count": counts["rejected_count"] or 0,
        "present_students": present_today,
        "absent_students": absent_students,
        "highest_student": highest_student,
        "lowest_student": lowest_student,
        "stats_3_days": stats_3_days,
        "attendance_details": attendance_details,
        "chart_data": chart_data,
        "calendar_data": calendar_data,
        "students": students,
        "total_classes_last_3_days": LAST_3_DAYS_TOTAL_CLASSES,
        "recent_dates": get_recent_dates(),
        "active_sessions": [dict(row) for row in active_sessions],
        "low_attendance_students": low_attendance_students,
        "class_wise_stats": [dict(row) for row in class_rows],
        "pending_corrections": [dict(row) for row in corrections],
        "low_attendance_threshold": threshold,
        "working_days": get_working_days(),
    }


def get_student_attendance_summary(student_id):
    student = get_student_by_id(student_id)
    if not student:
        return None
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT class_sessions.id AS session_id,
                   class_sessions.class_name,
                   class_sessions.session_date,
                   class_sessions.subject_name,
                   class_sessions.teacher_name,
                   class_sessions.substitute_teacher,
                   class_sessions.session_status,
                   class_sessions.start_time,
                   class_sessions.end_time,
                   attendance.id AS attendance_id,
                   attendance.status,
                   attendance.attendance_status,
                   attendance.original_status,
                   attendance.attendance_time,
                   attendance.emotion,
                   attendance.distance_meters,
                   attendance.proof_snapshot_path,
                   attendance.marked_via,
                   attendance.tracking_status,
                   attendance.tracking_started_at,
                   attendance.tracking_expires_at,
                   attendance.tracking_completed_at,
                   attendance.attendance_cancelled_at,
                   attendance.cancellation_reason
            FROM class_sessions
            LEFT JOIN attendance
                ON attendance.session_id = class_sessions.id
               AND attendance.student_id = ?
               AND attendance.status IN ('Present', 'Late', 'Absent', 'Cancelled', 'Provisional')
            ORDER BY class_sessions.session_date DESC, class_sessions.start_time DESC
            """,
            (student_id,),
        ).fetchall()
    finally:
        conn.close()

    rows = [row for row in rows if class_names_match(row["class_name"], student["class_name"])]

    total_classes = 0
    attended_classes = 0
    absent_classes = 0
    history = []
    present_dates = []
    absent_dates = []
    subject_stats = defaultdict(lambda: {"attended": 0, "total": 0})

    for row in rows:
        if row["session_status"] == "Completed":
            total_classes += 1
            subject_stats[row["subject_name"]]["total"] += 1
            if row["status"] in {"Present", "Late"}:
                attended_classes += 1
                subject_stats[row["subject_name"]]["attended"] += 1
                present_dates.append(row["session_date"])
            elif row["status"] in {"Absent", "Cancelled"}:
                absent_classes += 1
                absent_dates.append(row["session_date"])

        history.append(
            {
                "session_id": row["session_id"],
                "attendance_id": row["attendance_id"],
                "date": row["session_date"],
                "subject_name": row["subject_name"],
                "teacher_name": row["substitute_teacher"] or row["teacher_name"],
                "session_status": row["session_status"],
                "status": row["status"] or ("Cancelled" if row["session_status"] == "Cancelled" else "Pending"),
                "attendance_status": row["attendance_status"] or "",
                "original_status": row["original_status"] or "",
                "time": row["attendance_time"] or "",
                "emotion": row["emotion"] or "",
                "distance_meters": row["distance_meters"],
                "proof_snapshot_path": row["proof_snapshot_path"] or "",
                "marked_via": row["marked_via"] or "",
                "tracking_status": row["tracking_status"] or "",
                "tracking_started_at": row["tracking_started_at"] or "",
                "tracking_expires_at": row["tracking_expires_at"] or "",
                "tracking_completed_at": row["tracking_completed_at"] or "",
                "attendance_cancelled_at": row["attendance_cancelled_at"] or "",
                "cancellation_reason": row["cancellation_reason"] or "",
                "start_time": row["start_time"],
                "end_time": row["end_time"],
            }
        )

    subject_breakdown = []
    for subject_name, value in sorted(subject_stats.items()):
        percentage = round((value["attended"] / value["total"]) * 100, 2) if value["total"] else 0
        subject_breakdown.append(
            {
                "subject_name": subject_name,
                "attended_classes": value["attended"],
                "total_classes": value["total"],
                "attendance_percentage": percentage,
            }
        )

    percentage = round((attended_classes / total_classes) * 100, 2) if total_classes else 0
    threshold = get_low_attendance_threshold()
    return {
        "student": student,
        "total_classes": total_classes,
        "attended_classes": attended_classes,
        "absent_classes": absent_classes,
        "attendance_percentage": percentage,
        "present_dates": sorted(set(present_dates)),
        "absent_dates": sorted(set(absent_dates)),
        "history": history,
        "subject_stats": subject_breakdown,
        "low_attendance_threshold": threshold,
        "is_low_attendance": total_classes > 0 and percentage < threshold,
    }


def create_correction_request(student_id, session_id, attendance_record_id, reason, requested_status="Present"):
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO correction_requests (
                student_id, session_id, attendance_record_id, reason, requested_status, status, created_at
            )
            VALUES (?, ?, ?, ?, ?, 'Pending', ?)
            """,
            (student_id, session_id, attendance_record_id, reason, requested_status, now_string()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_correction_requests(student_id=None):
    conn = get_connection()
    try:
        query = """
            SELECT correction_requests.*, students.name AS student_name, class_sessions.subject_name, class_sessions.session_date
            FROM correction_requests
            JOIN students ON students.id = correction_requests.student_id
            JOIN class_sessions ON class_sessions.id = correction_requests.session_id
        """
        params = []
        if student_id:
            query += " WHERE correction_requests.student_id = ?"
            params.append(student_id)
        query += " ORDER BY correction_requests.created_at DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def review_correction_request(correction_request_id, status, reviewed_by, admin_notes=""):
    if status not in {"Approved", "Rejected"}:
        raise ValueError("Invalid review status.")

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM correction_requests WHERE id = ?",
            (correction_request_id,),
        ).fetchone()
        if not row:
            return False

        conn.execute(
            """
            UPDATE correction_requests
            SET status = ?, admin_notes = ?, reviewed_by = ?, reviewed_at = ?
            WHERE id = ?
            """,
            (status, admin_notes, reviewed_by, now_string(), correction_request_id),
        )

        conn.execute(
            """
            INSERT INTO correction_logs (
                correction_request_id, attendance_record_id, action, previous_status,
                new_status, acted_by, acted_at, notes
            )
            VALUES (?, ?, ?, '', ?, ?, ?, ?)
            """,
            (
                correction_request_id,
                row["attendance_record_id"],
                status,
                row["requested_status"] if status == "Approved" else "",
                reviewed_by,
                now_string(),
                admin_notes,
            ),
        )

        if status == "Approved":
            student = get_student_by_id(row["student_id"])
            session_row = get_session_by_id(row["session_id"])
            create_attendance_record(
                student_id=student["id"],
                session_id=session_row["id"],
                name=student["name"],
                class_name=student["class_name"],
                subject_name=session_row["subject_name"],
                teacher_name=session_row["substitute_teacher"] or session_row["teacher_name"],
                status=row["requested_status"],
                marked_via="correction_approved",
                face_verified=True,
                spoof_status="approved",
                correction_request_id=correction_request_id,
                original_status=row["requested_status"],
                attendance_date_value=session_row["session_date"],
            )

        conn.commit()
        return True
    finally:
        conn.close()


def get_attendance_report(filters=None):
    filters = filters or {}
    query = """
        SELECT attendance.id, students.name AS student_name, students.enrollment_number, students.class_name AS student_class_name,
               class_sessions.subject_name, class_sessions.session_date, class_sessions.start_time, class_sessions.end_time,
               attendance.status, attendance.attendance_time, attendance.distance_meters, attendance.marked_via,
               attendance.rejection_reason
        FROM attendance
        JOIN students ON students.id = attendance.student_id
        JOIN class_sessions ON class_sessions.id = attendance.session_id
        WHERE 1 = 1
    """
    params = []

    if filters.get("date_from"):
        query += " AND class_sessions.session_date >= ?"
        params.append(filters["date_from"])
    if filters.get("date_to"):
        query += " AND class_sessions.session_date <= ?"
        params.append(filters["date_to"])
    if filters.get("class_name"):
        query += " AND students.class_name = ?"
        params.append(filters["class_name"])
    if filters.get("student_id"):
        query += " AND students.id = ?"
        params.append(filters["student_id"])
    if filters.get("status"):
        query += " AND attendance.status = ?"
        params.append(filters["status"])

    query += " ORDER BY class_sessions.session_date DESC, class_sessions.start_time DESC, students.name ASC"

    conn = get_connection()
    try:
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def auto_mark_absent_for_closed_sessions(reference_time=None):
    current_time = reference_time or datetime.now()
    ensure_sessions_for_date(current_time.strftime("%Y-%m-%d"))
    conn = get_connection()
    notifications = []
    try:
        sessions = conn.execute(
            """
            SELECT *
            FROM class_sessions
            WHERE session_status IN ('Scheduled', 'Active', 'Delayed')
            """
        ).fetchall()

        for session_row in sessions:
            _, _, late_dt = _session_windows(session_row)
            if current_time <= late_dt:
                continue

            if session_row["session_status"] == "Cancelled":
                conn.execute(
                    "UPDATE class_sessions SET session_status = 'Completed', absent_processed = 1, completed_at = ?, updated_at = ? WHERE id = ?",
                    (now_string(), now_string(), session_row["id"]),
                )
                continue

            students = conn.execute(
                "SELECT * FROM students WHERE class_name = ? ORDER BY name ASC",
                (session_row["class_name"],),
            ).fetchall()

            for student in students:
                existing = conn.execute(
                    """
                    SELECT id
                    FROM attendance
                    WHERE student_id = ? AND session_id = ? AND status != 'Rejected'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (student["id"], session_row["id"]),
                ).fetchone()
                if existing:
                    continue

                attendance_id = create_attendance_record(
                    student_id=student["id"],
                    session_id=session_row["id"],
                    name=student["name"],
                    class_name=student["class_name"],
                    subject_name=session_row["subject_name"],
                    teacher_name=session_row["substitute_teacher"] or session_row["teacher_name"],
                    status="Absent",
                    marked_via="auto_absent",
                    spoof_status="not_attempted",
                    attendance_date_value=session_row["session_date"],
                )
                notifications.append(
                    {
                        "attendance_id": attendance_id,
                        "student_id": student["id"],
                        "student_name": student["name"],
                        "student_email": student["email"],
                        "subject_name": session_row["subject_name"],
                        "session_date": session_row["session_date"],
                        "status": "Absent",
                    }
                )

            conn.execute(
                "UPDATE class_sessions SET session_status = 'Completed', absent_processed = 1, completed_at = ?, updated_at = ? WHERE id = ?",
                (now_string(), now_string(), session_row["id"]),
            )

        conn.commit()
        return notifications
    finally:
        conn.close()


def mark_absence_notification_sent(attendance_id):
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE attendance SET notification_sent = 1, updated_at = ? WHERE id = ?",
            (now_string(), attendance_id),
        )
        conn.commit()
    finally:
        conn.close()


def get_dashboard_stats():
    students = get_all_students()
    student_names = [student["name"] for student in students]
    present_today = get_today_present_students()
    total_students = len(student_names)
    present_count = len(present_today)
    absent_count = max(total_students - present_count, 0)

    stats_3_days = get_last_3_days_attendance_map(student_names)
    attendance_details = get_last_3_days_attendance_details(student_names)
    chart_data = get_last_3_days_chart(student_names)
    calendar_data = get_month_calendar_data(student_names)
    absent_students = [name for name in student_names if name not in present_today]

    highest_student = None
    lowest_student = None

    if stats_3_days:
        highest_student = sorted(
            stats_3_days.items(), key=lambda item: (-item[1], item[0])
        )[0][0]
        lowest_student = sorted(stats_3_days.items(), key=lambda item: (item[1], item[0]))[
            0
        ][0]

    return {
        "total_students": total_students,
        "present_count": present_count,
        "absent_count": absent_count,
        "present_students": present_today,
        "absent_students": absent_students,
        "highest_student": highest_student,
        "lowest_student": lowest_student,
        "stats_3_days": stats_3_days,
        "attendance_details": attendance_details,
        "chart_data": chart_data,
        "calendar_data": calendar_data,
        "students": students,
        "total_classes_last_3_days": LAST_3_DAYS_TOTAL_CLASSES,
        "recent_dates": get_recent_dates(),
    }


def get_student_attendance_summary(student_id):
    student = get_student_by_id(student_id)
    if not student:
        return None

    conn = get_connection()
    cur = conn.cursor()

    cur.execute(
        """
        SELECT DISTINCT date
        FROM attendance
        ORDER BY date DESC
        """
    )
    class_dates = [row["date"] for row in cur.fetchall()]

    cur.execute(
        """
        SELECT date, time, emotion
        FROM attendance
        WHERE student_id = ?
        ORDER BY date DESC, time DESC
        """,
        (student_id,),
    )
    rows = [dict(row) for row in cur.fetchall()]
    conn.close()

    attendance_by_date = {}
    for row in rows:
        attendance_by_date.setdefault(row["date"], row)

    present_dates = set(attendance_by_date.keys())
    total_classes = len(class_dates)
    present_count = len(present_dates)
    absent_count = max(total_classes - present_count, 0)
    percentage = round((present_count / total_classes) * 100, 2) if total_classes else 0.0

    history = []
    for class_date in class_dates:
        record = attendance_by_date.get(class_date)
        history.append(
            {
                "date": class_date,
                "status": "Present" if record else "Absent",
                "time": record["time"] if record else "",
                "emotion": record.get("emotion", "") if record else "",
            }
        )

    return {
        "student": student,
        "total_classes": total_classes,
        "attended_classes": present_count,
        "absent_classes": absent_count,
        "attendance_percentage": percentage,
        "present_dates": sorted(present_dates),
        "absent_dates": [item["date"] for item in history if item["status"] == "Absent"],
        "history": history,
    }


def now_string():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_dashboard_stats():
    today = datetime.now().date()
    ensure_sessions_for_date(today)
    students = get_all_students()
    student_names = [student["name"] for student in students]
    present_today = get_today_present_students()
    absent_students = [name for name in student_names if name not in present_today]

    conn = get_connection()
    try:
        counts = conn.execute(
            """
            SELECT
                SUM(CASE WHEN status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                SUM(CASE WHEN status = 'Late' THEN 1 ELSE 0 END) AS late_count,
                SUM(CASE WHEN status = 'Absent' THEN 1 ELSE 0 END) AS absent_count,
                SUM(CASE WHEN status = 'Rejected' THEN 1 ELSE 0 END) AS rejected_count
            FROM attendance
            WHERE attendance_date = ?
            """,
            (today.strftime("%Y-%m-%d"),),
        ).fetchone()
        active_sessions = conn.execute(
            "SELECT * FROM class_sessions WHERE session_date = ? ORDER BY start_time ASC",
            (today.strftime("%Y-%m-%d"),),
        ).fetchall()
        class_rows = conn.execute(
            """
            SELECT class_sessions.class_name,
                   COUNT(DISTINCT class_sessions.id) AS total_sessions,
                   SUM(CASE WHEN attendance.status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN attendance.status = 'Late' THEN 1 ELSE 0 END) AS late_count,
                   SUM(CASE WHEN attendance.status = 'Absent' THEN 1 ELSE 0 END) AS absent_count
            FROM class_sessions
            LEFT JOIN attendance ON attendance.session_id = class_sessions.id
            GROUP BY class_sessions.class_name
            ORDER BY class_sessions.class_name ASC
            """
        ).fetchall()
        corrections = conn.execute(
            """
            SELECT correction_requests.*, students.name AS student_name, class_sessions.subject_name, class_sessions.session_date
            FROM correction_requests
            JOIN students ON students.id = correction_requests.student_id
            JOIN class_sessions ON class_sessions.id = correction_requests.session_id
            ORDER BY CASE correction_requests.status WHEN 'Pending' THEN 0 ELSE 1 END,
                     correction_requests.created_at DESC
            LIMIT 10
            """
        ).fetchall()
    finally:
        conn.close()

    student_summaries = {
        student["name"]: get_student_attendance_summary(student["id"])
        for student in students
    }
    stats_3_days = get_last_3_days_attendance_map(student_names, summary_map=student_summaries)
    attendance_details = get_last_3_days_attendance_details(student_names, summary_map=student_summaries)
    chart_data = get_last_3_days_chart(student_names, summary_map=student_summaries)
    calendar_data = get_month_calendar_data(student_names, summary_map=student_summaries)
    highest_student = sorted(stats_3_days.items(), key=lambda item: (-item[1], item[0]))[0][0] if stats_3_days else None
    lowest_student = sorted(stats_3_days.items(), key=lambda item: (item[1], item[0]))[0][0] if stats_3_days else None
    threshold = get_low_attendance_threshold()

    low_attendance_students = []
    for student in students:
        summary = student_summaries.get(student["name"]) or {}
        total_classes = int(summary.get("total_classes") or 0)
        attended = int(summary.get("attended_classes") or 0)
        percentage = round((attended / total_classes) * 100, 2) if total_classes else 0
        if total_classes and percentage < threshold:
            low_attendance_students.append(
                {
                    "id": student["id"],
                    "name": student["name"],
                    "class_name": student["class_name"],
                    "attendance_percentage": percentage,
                    "total_classes": total_classes,
                    "attended_classes": attended,
                }
            )

    return {
        "total_students": len(student_names),
        "present_count": counts["present_count"] or 0,
        "late_count": counts["late_count"] or 0,
        "absent_count": counts["absent_count"] or 0,
        "rejected_count": counts["rejected_count"] or 0,
        "present_students": present_today,
        "absent_students": absent_students,
        "highest_student": highest_student,
        "lowest_student": lowest_student,
        "stats_3_days": stats_3_days,
        "attendance_details": attendance_details,
        "chart_data": chart_data,
        "calendar_data": calendar_data,
        "students": students,
        "total_classes_last_3_days": LAST_3_DAYS_TOTAL_CLASSES,
        "recent_dates": get_recent_dates(),
        "active_sessions": [dict(row) for row in active_sessions],
        "low_attendance_students": low_attendance_students,
        "class_wise_stats": [dict(row) for row in class_rows],
        "pending_corrections": [dict(row) for row in corrections],
        "low_attendance_threshold": threshold,
        "working_days": get_working_days(),
    }


def get_student_attendance_summary(student_id):
    student = get_student_by_id(student_id)
    if not student:
        return None

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT class_sessions.id AS session_id,
                   class_sessions.class_name,
                   class_sessions.session_date,
                   class_sessions.subject_name,
                   class_sessions.teacher_name,
                   class_sessions.substitute_teacher,
                   class_sessions.session_status,
                   class_sessions.start_time,
                   class_sessions.end_time,
                   attendance.id AS attendance_id,
                   attendance.status,
                   attendance.attendance_status,
                   attendance.original_status,
                   attendance.attendance_time,
                   attendance.emotion,
                   attendance.distance_meters,
                   attendance.proof_snapshot_path,
                   attendance.marked_via,
                   attendance.tracking_status,
                   attendance.tracking_started_at,
                   attendance.tracking_expires_at,
                   attendance.tracking_completed_at,
                   attendance.attendance_cancelled_at,
                   attendance.cancellation_reason
            FROM class_sessions
            LEFT JOIN attendance
                ON attendance.session_id = class_sessions.id
               AND attendance.student_id = ?
               AND attendance.status IN ('Present', 'Late', 'Absent', 'Cancelled', 'Rejected', 'Provisional')
            ORDER BY class_sessions.session_date DESC, class_sessions.start_time DESC
            """,
            (student_id,),
        ).fetchall()
    finally:
        conn.close()

    known_class_names = list_registered_class_names()
    rows = [
        row
        for row in rows
        if schedule_visible_to_student(
            row["class_name"],
            student["class_name"],
            known_class_names,
        )
    ]

    total_classes = 0
    attended_classes = 0
    absent_classes = 0
    history = []
    present_dates = []
    absent_dates = []
    subject_stats = defaultdict(lambda: {"attended": 0, "total": 0})

    for row in rows:
        if row["session_status"] == "Completed":
            total_classes += 1
            subject_stats[row["subject_name"]]["total"] += 1
            if row["status"] in {"Present", "Late"}:
                attended_classes += 1
                subject_stats[row["subject_name"]]["attended"] += 1
                present_dates.append(row["session_date"])
            elif row["status"] in {"Absent", "Cancelled", "Rejected"}:
                absent_classes += 1
                absent_dates.append(row["session_date"])

        history.append(
            {
                "session_id": row["session_id"],
                "attendance_id": row["attendance_id"],
                "date": row["session_date"],
                "subject_name": row["subject_name"],
                "teacher_name": row["substitute_teacher"] or row["teacher_name"],
                "session_status": row["session_status"],
                "status": row["status"] or ("Cancelled" if row["session_status"] == "Cancelled" else "Pending"),
                "attendance_status": row["attendance_status"] or "",
                "original_status": row["original_status"] or "",
                "time": row["attendance_time"] or "",
                "emotion": row["emotion"] or "",
                "distance_meters": row["distance_meters"],
                "proof_snapshot_path": row["proof_snapshot_path"] or "",
                "marked_via": row["marked_via"] or "",
                "tracking_status": row["tracking_status"] or "",
                "tracking_started_at": row["tracking_started_at"] or "",
                "tracking_expires_at": row["tracking_expires_at"] or "",
                "tracking_completed_at": row["tracking_completed_at"] or "",
                "attendance_cancelled_at": row["attendance_cancelled_at"] or "",
                "cancellation_reason": row["cancellation_reason"] or "",
                "start_time": row["start_time"],
                "end_time": row["end_time"],
            }
        )

    subject_breakdown = []
    for subject_name, value in sorted(subject_stats.items()):
        percentage = round((value["attended"] / value["total"]) * 100, 2) if value["total"] else 0
        subject_breakdown.append(
            {
                "subject_name": subject_name,
                "attended_classes": value["attended"],
                "total_classes": value["total"],
                "attendance_percentage": percentage,
            }
        )

    percentage = round((attended_classes / total_classes) * 100, 2) if total_classes else 0
    threshold = get_low_attendance_threshold()
    return {
        "student": student,
        "total_classes": total_classes,
        "attended_classes": attended_classes,
        "absent_classes": absent_classes,
        "attendance_percentage": percentage,
        "present_dates": sorted(set(present_dates)),
        "absent_dates": sorted(set(absent_dates)),
        "history": history,
        "subject_stats": subject_breakdown,
        "low_attendance_threshold": threshold,
        "is_low_attendance": total_classes > 0 and percentage < threshold,
    }


def date_string(value=None):
    return (value or datetime.now()).strftime("%Y-%m-%d")


def time_string(value=None):
    return (value or datetime.now()).strftime("%H:%M:%S")


def parse_date(value):
    if isinstance(value, date):
        return value
    return datetime.strptime(str(value), "%Y-%m-%d").date()


def parse_time_value(value):
    return datetime.strptime(str(value), "%H:%M:%S").time()


def combine_date_time(date_value, time_value):
    return datetime.combine(parse_date(date_value), parse_time_value(time_value))


def normalize_class_name(value):
    cleaned = "".join(ch.lower() if ch.isalnum() else " " for ch in str(value or ""))
    return " ".join(part for part in cleaned.split() if part)


def class_names_match(left_value, right_value):
    left = normalize_class_name(left_value)
    right = normalize_class_name(right_value)
    if not left or not right:
        return False
    if left == right:
        return True
    return left in right or right in left


def list_registered_class_names():
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT class_name
            FROM students
            WHERE TRIM(COALESCE(class_name, '')) <> ''
            """
        ).fetchall()
        return [row["class_name"] for row in rows]
    finally:
        conn.close()


def schedule_visible_to_student(schedule_class_name, student_class_name, known_class_names=None):
    if class_names_match(schedule_class_name, student_class_name):
        return True

    normalized_schedule = normalize_class_name(schedule_class_name)
    if not normalized_schedule:
        return True

    class_names = known_class_names if known_class_names is not None else list_registered_class_names()
    # If a timetable label does not correspond to any registered student class, treat it as
    # a generic/global schedule so it still appears in the student portal.
    return not any(class_names_match(schedule_class_name, class_name) for class_name in class_names)


def get_app_setting(key, default_value=None):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT setting_value FROM app_settings WHERE setting_key = ?",
            (key,),
        ).fetchone()
        return row["setting_value"] if row else default_value
    finally:
        conn.close()


def set_app_setting(key, value):
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO app_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key)
            DO UPDATE SET setting_value = excluded.setting_value, updated_at = excluded.updated_at
            """,
            (key, str(value), now_string()),
        )
        conn.commit()
    finally:
        conn.close()


def get_low_attendance_threshold():
    try:
        return float(get_app_setting("low_attendance_threshold", "75"))
    except (TypeError, ValueError):
        return DEFAULT_LOW_ATTENDANCE_THRESHOLD


def _coerce_tracking_minutes(value, default_value=None):
    if value is None or value == "":
        return default_value
    try:
        minutes = int(float(value))
    except (TypeError, ValueError):
        return default_value
    return max(0, min(180, minutes))


def get_post_attendance_tracking_default_minutes():
    minutes = _coerce_tracking_minutes(
        get_app_setting(
            "post_attendance_tracking_default_minutes",
            DEFAULT_POST_ATTENDANCE_TRACKING_MINUTES,
        ),
        default_value=DEFAULT_POST_ATTENDANCE_TRACKING_MINUTES,
    )
    return minutes if minutes is not None else DEFAULT_POST_ATTENDANCE_TRACKING_MINUTES


def get_session_tracking_minutes(session_row):
    if not session_row:
        return get_post_attendance_tracking_default_minutes()
    minutes = _coerce_tracking_minutes(
        session_row.get("post_attendance_tracking_minutes"),
        default_value=None,
    )
    if minutes is None:
        return get_post_attendance_tracking_default_minutes()
    return minutes


def get_working_days():
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT day_name, is_working
            FROM working_days
            ORDER BY CASE day_name
                WHEN 'Monday' THEN 1
                WHEN 'Tuesday' THEN 2
                WHEN 'Wednesday' THEN 3
                WHEN 'Thursday' THEN 4
                WHEN 'Friday' THEN 5
                WHEN 'Saturday' THEN 6
                WHEN 'Sunday' THEN 7
            END
            """
        ).fetchall()
        return [
            {"day_name": row["day_name"], "is_working": bool(row["is_working"])}
            for row in rows
        ]
    finally:
        conn.close()


def update_working_days(day_status_map):
    conn = get_connection()
    try:
        for day_name in DAY_NAMES:
            conn.execute(
                "UPDATE working_days SET is_working = ?, updated_at = ? WHERE day_name = ?",
                (1 if day_status_map.get(day_name) else 0, now_string(), day_name),
            )
        conn.commit()
    finally:
        conn.close()


def is_working_day(target_date):
    target = parse_date(target_date)
    conn = get_connection()
    try:
        work_row = conn.execute(
            "SELECT is_working FROM working_days WHERE day_name = ?",
            (target.strftime("%A"),),
        ).fetchone()
        holiday_row = conn.execute(
            "SELECT id FROM holidays WHERE holiday_date = ?",
            (target.strftime("%Y-%m-%d"),),
        ).fetchone()
        return bool(work_row["is_working"]) and holiday_row is None
    finally:
        conn.close()


def list_holidays():
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT id, holiday_date, title, description, created_at FROM holidays ORDER BY holiday_date ASC"
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def add_holiday(holiday_date, title, description=""):
    conn = get_connection()
    try:
        cur = conn.execute(
            "INSERT INTO holidays (holiday_date, title, description, created_at) VALUES (?, ?, ?, ?)",
            (holiday_date, title, description, now_string()),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_week_bounds(reference_date=None):
    target = parse_date(reference_date or date.today())
    start = target - timedelta(days=target.weekday())
    end = start + timedelta(days=6)
    return start, end


def delete_holiday(holiday_id):
    conn = get_connection()
    try:
        conn.execute("DELETE FROM holidays WHERE id = ?", (holiday_id,))
        conn.commit()
    finally:
        conn.close()


def _schedule_payload(form_data):
    session_date = parse_date(form_data["session_date"]).strftime("%Y-%m-%d")
    return (
        form_data["class_name"].strip(),
        form_data["subject_name"].strip(),
        form_data["teacher_name"].strip(),
        form_data.get("room_name", "").strip(),
        session_date,
        parse_date(session_date).strftime("%A"),
        form_data["start_time"].strip(),
        form_data["end_time"].strip(),
        form_data["attendance_open_time"].strip(),
        form_data["attendance_close_time"].strip(),
        form_data["late_close_time"].strip(),
        form_data.get("gps_latitude"),
        form_data.get("gps_longitude"),
        form_data.get("allowed_radius_meters", DEFAULT_MAX_ATTENDANCE_RADIUS_METERS),
        _coerce_tracking_minutes(
            form_data.get("post_attendance_tracking_minutes"),
            default_value=get_post_attendance_tracking_default_minutes(),
        ),
    )


def create_class_schedule(form_data):
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO class_schedules (
                class_name, subject_name, teacher_name, room_name, session_date, day_name,
                start_time, end_time, attendance_open_time, attendance_close_time,
                late_close_time, gps_latitude, gps_longitude, allowed_radius_meters,
                post_attendance_tracking_minutes, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            _schedule_payload(form_data) + (now_string(), now_string()),
        )
        conn.commit()
        schedule_id = cur.lastrowid
    finally:
        conn.close()
    sync_schedule_sessions(
        schedule_id,
        start_date=form_data["session_date"],
        end_date=form_data["session_date"],
    )
    return schedule_id


def _log_gps_change_with_connection(
    conn,
    target_type,
    schedule_id,
    session_id,
    old_latitude,
    old_longitude,
    old_radius,
    new_latitude,
    new_longitude,
    new_radius,
    changed_by,
):
    if (
        old_latitude == new_latitude
        and old_longitude == new_longitude
        and float(old_radius or 0) == float(new_radius or 0)
    ):
        return

    conn.execute(
        """
        INSERT INTO gps_change_logs (
            target_type, schedule_id, session_id,
            old_latitude, old_longitude, old_radius,
            new_latitude, new_longitude, new_radius,
            changed_by, changed_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            target_type,
            schedule_id,
            session_id,
            old_latitude,
            old_longitude,
            old_radius,
            new_latitude,
            new_longitude,
            new_radius,
            changed_by,
            now_string(),
        ),
    )


def update_class_schedule(schedule_id, form_data, admin_id=None):
    conn = get_connection()
    try:
        old_row = conn.execute(
            "SELECT * FROM class_schedules WHERE id = ?",
            (schedule_id,),
        ).fetchone()
        conn.execute(
            """
            UPDATE class_schedules
            SET class_name = ?, subject_name = ?, teacher_name = ?, room_name = ?, session_date = ?, day_name = ?,
                start_time = ?, end_time = ?, attendance_open_time = ?, attendance_close_time = ?,
                late_close_time = ?, gps_latitude = ?, gps_longitude = ?, allowed_radius_meters = ?,
                post_attendance_tracking_minutes = ?,
                updated_at = ?
            WHERE id = ?
            """,
            _schedule_payload(form_data) + (now_string(), schedule_id),
        )
        if old_row and admin_id is not None:
            _log_gps_change_with_connection(
                conn,
                "schedule",
                schedule_id,
                None,
                old_row["gps_latitude"],
                old_row["gps_longitude"],
                old_row["allowed_radius_meters"],
                form_data.get("gps_latitude"),
                form_data.get("gps_longitude"),
                form_data.get("allowed_radius_meters"),
                admin_id,
            )
        conn.execute(
            "DELETE FROM deleted_class_sessions WHERE schedule_id = ?",
            (schedule_id,),
        )
        conn.commit()
    finally:
        conn.close()
    sync_start = form_data["session_date"]
    if old_row and old_row["session_date"]:
        sync_start = min(sync_start, old_row["session_date"])
    sync_schedule_sessions(
        schedule_id,
        start_date=sync_start,
        end_date=form_data["session_date"],
        replace_future=True,
    )


def delete_class_schedule(schedule_id):
    conn = get_connection()
    try:
        session_rows = conn.execute(
            "SELECT id FROM class_sessions WHERE schedule_id = ?",
            (schedule_id,),
        ).fetchall()
        session_ids = [row["id"] for row in session_rows]

        if session_ids:
            placeholders = ",".join("?" for _ in session_ids)
            conn.execute(
                f"DELETE FROM correction_logs WHERE correction_request_id IN (SELECT id FROM correction_requests WHERE session_id IN ({placeholders}))",
                session_ids,
            )
            conn.execute(
                f"DELETE FROM correction_requests WHERE session_id IN ({placeholders})",
                session_ids,
            )
            conn.execute(
                f"DELETE FROM override_permissions WHERE session_id IN ({placeholders})",
                session_ids,
            )
            conn.execute(
                f"DELETE FROM attendance WHERE session_id IN ({placeholders})",
                session_ids,
            )
        conn.execute(
            "DELETE FROM class_sessions WHERE schedule_id = ?",
            (schedule_id,),
        )
        conn.execute(
            "DELETE FROM deleted_class_sessions WHERE schedule_id = ?",
            (schedule_id,),
        )
        conn.execute(
            "DELETE FROM class_schedules WHERE id = ?",
            (schedule_id,),
        )
        conn.commit()
    finally:
        conn.close()


def list_class_schedules():
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM class_schedules
            WHERE is_active = 1
            ORDER BY session_date ASC, start_time ASC, class_name ASC
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def ensure_schedule_days_are_working():
    # Admin-selected working days should remain authoritative.
    # Scheduled sessions are allowed to exist on non-working days
    # without silently re-enabling those weekdays in the settings UI.
    return


def get_schedule_by_id(schedule_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM class_schedules WHERE id = ?",
            (schedule_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _get_deleted_session_keys(conn, start_date=None, end_date=None, schedule_id=None):
    clauses = []
    params = []

    if schedule_id is not None:
        clauses.append("schedule_id = ?")
        params.append(schedule_id)

    if start_date is not None:
        clauses.append("session_date >= ?")
        params.append(parse_date(start_date).strftime("%Y-%m-%d"))

    if end_date is not None:
        clauses.append("session_date <= ?")
        params.append(parse_date(end_date).strftime("%Y-%m-%d"))

    query = "SELECT schedule_id, session_date FROM deleted_class_sessions"
    if clauses:
        query += " WHERE " + " AND ".join(clauses)

    rows = conn.execute(query, tuple(params)).fetchall()
    return {(row["schedule_id"], row["session_date"]) for row in rows}


def _record_deleted_session(conn, schedule_id, session_date, deleted_by=None):
    if not schedule_id:
        return

    conn.execute(
        """
        INSERT OR IGNORE INTO deleted_class_sessions (schedule_id, session_date, deleted_by, deleted_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            schedule_id,
            parse_date(session_date).strftime("%Y-%m-%d"),
            deleted_by,
            now_string(),
        ),
    )


def can_delete_session(session_row, reference_time=None):
    if not session_row:
        return False

    session_status = str(session_row.get("session_status") or "Scheduled").strip().title()

    return (
        bool(session_row.get("schedule_id"))
        and session_status != "Active"
    )


def delete_class_session(session_id, deleted_by=None):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT s.*,
                   SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END) AS late_count,
                   SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent_count,
                   SUM(CASE WHEN a.attendance_status IN ('MARKED_PENDING_TRACKING', 'TRACKING_ACTIVE', 'PROVISIONAL') THEN 1 ELSE 0 END) AS provisional_count,
                   SUM(CASE WHEN a.attendance_status IN ('FINALIZED', 'FINAL') THEN 1 ELSE 0 END) AS final_count,
                   SUM(CASE WHEN a.attendance_status = 'CANCELLED' THEN 1 ELSE 0 END) AS tracking_cancelled_count,
                   SUM(CASE WHEN a.tracking_status = 'Tracking Active' THEN 1 ELSE 0 END) AS tracking_active_count,
                   SUM(CASE WHEN a.status = 'Rejected' THEN 1 ELSE 0 END) AS rejected_count
            FROM class_sessions s
            LEFT JOIN attendance a ON a.session_id = s.id
            WHERE s.id = ?
            GROUP BY s.id
            """,
            (session_id,),
        ).fetchone()
        if not row:
            return False, "The selected class session could not be found."

        session_row = dict(row)
        if not can_delete_session(session_row):
            return False, "Active sessions cannot be deleted while the class is in progress."

        _record_deleted_session(
            conn,
            session_row["schedule_id"],
            session_row["session_date"],
            deleted_by=deleted_by,
        )
        conn.execute("DELETE FROM correction_logs WHERE correction_request_id IN (SELECT id FROM correction_requests WHERE session_id = ?)", (session_id,))
        conn.execute("DELETE FROM correction_requests WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM override_permissions WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM attendance WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM class_sessions WHERE id = ?", (session_id,))
        conn.execute("DELETE FROM deleted_class_sessions WHERE schedule_id = ?", (session_row["schedule_id"],))
        conn.execute("DELETE FROM class_schedules WHERE id = ?", (session_row["schedule_id"],))
        conn.commit()
        return True, "Class session deleted successfully."
    finally:
        conn.close()


def ensure_sessions_for_date(target_date=None):
    target = parse_date(target_date or date.today())

    conn = get_connection()
    try:
        deleted_keys = _get_deleted_session_keys(
            conn,
            start_date=target,
            end_date=target,
        )
        rows = conn.execute(
            """
            SELECT *
            FROM class_schedules
            WHERE is_active = 1 AND session_date = ?
            ORDER BY start_time ASC
            """,
            (target.strftime("%Y-%m-%d"),),
        ).fetchall()
        for row in rows:
            session_key = (row["id"], target.strftime("%Y-%m-%d"))
            if session_key in deleted_keys:
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO class_sessions (
                    schedule_id, class_name, subject_name, teacher_name, room_name, day_name, session_date,
                    start_time, end_time, attendance_open_time, attendance_close_time, late_close_time,
                    gps_latitude, gps_longitude, allowed_radius_meters, post_attendance_tracking_minutes,
                    session_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)
                """,
                (
                    row["id"],
                    row["class_name"],
                    row["subject_name"],
                    row["teacher_name"],
                    row["room_name"],
                    row["day_name"],
                    target.strftime("%Y-%m-%d"),
                    row["start_time"],
                    row["end_time"],
                    row["attendance_open_time"],
                    row["attendance_close_time"],
                    row["late_close_time"],
                    row["gps_latitude"],
                    row["gps_longitude"],
                    row["allowed_radius_meters"],
                    row["post_attendance_tracking_minutes"],
                    now_string(),
                    now_string(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def ensure_sessions_for_range(start_date, end_date):
    current = parse_date(start_date)
    finish = parse_date(end_date)
    while current <= finish:
        ensure_sessions_for_date(current.strftime("%Y-%m-%d"))
        current += timedelta(days=1)


def sync_schedule_sessions(schedule_id, start_date=None, end_date=None, replace_future=False):
    schedule_row = get_schedule_by_id(schedule_id)
    if not schedule_row or not schedule_row.get("is_active"):
        return

    start = parse_date(start_date or date.today())
    end = parse_date(end_date or (start + timedelta(days=27)))
    schedule_date = parse_date(schedule_row.get("session_date") or start)
    conn = get_connection()
    try:
        deleted_keys = _get_deleted_session_keys(
            conn,
            start_date=min(start, schedule_date),
            end_date=max(end, schedule_date),
            schedule_id=schedule_id,
        )
        if replace_future:
            conn.execute(
                """
                DELETE FROM class_sessions
                WHERE schedule_id = ?
                  AND session_date >= ?
                  AND id NOT IN (
                      SELECT DISTINCT session_id
                      FROM attendance
                      WHERE session_id IS NOT NULL
                  )
                """,
                (schedule_id, start.strftime("%Y-%m-%d")),
            )

        session_key = (schedule_row["id"], schedule_date.strftime("%Y-%m-%d"))
        if start <= schedule_date <= end and session_key not in deleted_keys:
            conn.execute(
                """
                INSERT OR IGNORE INTO class_sessions (
                    schedule_id, class_name, subject_name, teacher_name, room_name, day_name, session_date,
                    start_time, end_time, attendance_open_time, attendance_close_time, late_close_time,
                    gps_latitude, gps_longitude, allowed_radius_meters, post_attendance_tracking_minutes,
                    session_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Scheduled', ?, ?)
                """,
                (
                    schedule_row["id"],
                    schedule_row["class_name"],
                    schedule_row["subject_name"],
                    schedule_row["teacher_name"],
                    schedule_row["room_name"],
                    schedule_row["day_name"],
                    schedule_date.strftime("%Y-%m-%d"),
                    schedule_row["start_time"],
                    schedule_row["end_time"],
                    schedule_row["attendance_open_time"],
                    schedule_row["attendance_close_time"],
                    schedule_row["late_close_time"],
                    schedule_row["gps_latitude"],
                    schedule_row["gps_longitude"],
                    schedule_row["allowed_radius_meters"],
                    schedule_row["post_attendance_tracking_minutes"],
                    now_string(),
                    now_string(),
                ),
            )
        conn.commit()
    finally:
        conn.close()


def refresh_session_statuses(reference_time=None, start_date=None, end_date=None, allow_completion=False):
    current_time = reference_time or datetime.now()
    start = parse_date(start_date or current_time.date())
    end = parse_date(end_date or current_time.date())
    ensure_sessions_for_range(start, end)

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM class_sessions
            WHERE session_date BETWEEN ? AND ?
            ORDER BY session_date ASC, start_time ASC
            """,
            (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        ).fetchall()

        for row in rows:
            row_dict = dict(row)
            current_status = row_dict["session_status"]
            if current_status in {"Cancelled", "Completed"}:
                continue

            open_dt, _, late_dt = _session_windows(row_dict)
            desired_status = "Scheduled"
            if open_dt <= current_time <= late_dt:
                desired_status = "Delayed" if current_status == "Delayed" else "Active"
            elif current_time > late_dt and allow_completion:
                desired_status = "Completed"

            if desired_status != current_status:
                conn.execute(
                    """
                    UPDATE class_sessions
                    SET session_status = ?,
                        completed_at = CASE WHEN ? = 'Completed' THEN COALESCE(NULLIF(completed_at, ''), ?) ELSE completed_at END,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (
                        desired_status,
                        desired_status,
                        now_string(),
                        now_string(),
                        row_dict["id"],
                    ),
                )

        conn.commit()
    finally:
        conn.close()


def get_session_by_id(session_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM class_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_session_by_schedule_and_date(schedule_id, session_date):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM class_sessions
            WHERE schedule_id = ? AND session_date = ?
            LIMIT 1
            """,
            (schedule_id, parse_date(session_date).strftime("%Y-%m-%d")),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_class_sessions(target_date=None, days=7, start_date=None, end_date=None, allow_completion=True):
    if start_date is not None or end_date is not None:
        start = parse_date(start_date or target_date or date.today())
        end = parse_date(end_date or start)
    elif target_date is None and days == 7:
        start, end = get_week_bounds(date.today())
    else:
        start = parse_date(target_date or date.today())
        end = start + timedelta(days=max(days - 1, 0))

    ensure_sessions_for_range(start, end)
    refresh_session_statuses(
        start_date=start,
        end_date=end,
        allow_completion=allow_completion,
    )
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT s.*,
                   SUM(CASE WHEN a.status = 'Present' THEN 1 ELSE 0 END) AS present_count,
                   SUM(CASE WHEN a.status = 'Late' THEN 1 ELSE 0 END) AS late_count,
                   SUM(CASE WHEN a.status = 'Absent' THEN 1 ELSE 0 END) AS absent_count,
                   SUM(CASE WHEN a.status = 'Rejected' THEN 1 ELSE 0 END) AS rejected_count
            FROM class_sessions s
            LEFT JOIN attendance a ON a.session_id = s.id
            WHERE s.session_date BETWEEN ? AND ?
            GROUP BY s.id
            ORDER BY s.session_date ASC, s.start_time ASC, s.class_name ASC
            """,
            (
                start.strftime("%Y-%m-%d"),
                end.strftime("%Y-%m-%d"),
            ),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_session_gps(session_id, latitude, longitude, allowed_radius_meters, admin_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM class_sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
        if not row:
            return False
        _log_gps_change_with_connection(
            conn,
            "session",
            row["schedule_id"],
            session_id,
            row["gps_latitude"],
            row["gps_longitude"],
            row["allowed_radius_meters"],
            latitude,
            longitude,
            allowed_radius_meters,
            admin_id,
        )
        conn.execute(
            """
            UPDATE class_sessions
            SET gps_latitude = ?, gps_longitude = ?, allowed_radius_meters = ?, updated_at = ?
            WHERE id = ?
            """,
            (latitude, longitude, allowed_radius_meters, now_string(), session_id),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def list_gps_change_logs(limit=50):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT g.*, a.email AS admin_email
            FROM gps_change_logs g
            LEFT JOIN admins a ON a.id = g.changed_by
            ORDER BY g.changed_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def update_class_session_status(
    session_id,
    session_status,
    status_reason="",
    attendance_open_time=None,
    attendance_close_time=None,
    late_close_time=None,
    substitute_teacher="",
    activated_by=None,
    post_attendance_tracking_minutes=None,
):
    if session_status not in SESSION_STATUSES:
        raise ValueError("Invalid session status.")

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE class_sessions
            SET session_status = ?,
                status_reason = ?,
                attendance_open_time = COALESCE(?, attendance_open_time),
                attendance_close_time = COALESCE(?, attendance_close_time),
                late_close_time = COALESCE(?, late_close_time),
                substitute_teacher = ?,
                is_substitute_class = ?,
                activated_by = COALESCE(?, activated_by),
                post_attendance_tracking_minutes = COALESCE(?, post_attendance_tracking_minutes),
                completed_at = CASE WHEN ? = 'Completed' THEN ? ELSE completed_at END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                session_status,
                status_reason,
                attendance_open_time,
                attendance_close_time,
                late_close_time,
                substitute_teacher,
                1 if substitute_teacher.strip() else 0,
                activated_by,
                post_attendance_tracking_minutes,
                session_status,
                now_string(),
                now_string(),
                session_id,
            ),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def _session_windows(session_row):
    return (
        combine_date_time(session_row["session_date"], session_row["attendance_open_time"]),
        combine_date_time(session_row["session_date"], session_row["attendance_close_time"]),
        combine_date_time(session_row["session_date"], session_row["late_close_time"]),
    )


def get_student_sessions(student_id, target_date=None):
    student = get_student_by_id(student_id)
    if not student:
        return []
    known_class_names = list_registered_class_names()
    target = parse_date(target_date or date.today())
    ensure_sessions_for_date(target)
    refresh_session_statuses(start_date=target, end_date=target, allow_completion=False)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM class_sessions
            WHERE session_date = ?
            ORDER BY start_time ASC
            """,
            (target.strftime("%Y-%m-%d"),),
        ).fetchall()
        return [
            dict(row)
            for row in rows
            if schedule_visible_to_student(
                row["class_name"],
                student["class_name"],
                known_class_names,
            )
        ]
    finally:
        conn.close()


def get_active_session_for_student(student_id, reference_time=None):
    current_time = reference_time or datetime.now()
    sessions = get_student_sessions(student_id, current_time.strftime("%Y-%m-%d"))
    current_session = None
    upcoming_session = None
    for session in sessions:
        open_dt, _, late_dt = _session_windows(session)
        start_dt = combine_date_time(session["session_date"], session["start_time"])
        if session["session_status"] in {"Active", "Delayed", "Scheduled"} and open_dt <= current_time <= late_dt:
            current_session = session
            break
        if session["session_status"] == "Scheduled" and start_dt >= current_time and not upcoming_session:
            upcoming_session = session
    if not upcoming_session:
        for offset in range(1, 8):
            future_date = (current_time.date() + timedelta(days=offset)).strftime("%Y-%m-%d")
            future_sessions = get_student_sessions(student_id, future_date)
            if future_sessions:
                upcoming_session = future_sessions[0]
                break
    if not current_session or not upcoming_session:
        projected_sessions = list_student_scheduled_sessions(student_id, days=14)
        for projected in projected_sessions:
            open_dt, _, late_dt = _session_windows(projected)
            start_dt = combine_date_time(projected["session_date"], projected["start_time"])
            if not current_session and open_dt <= current_time <= late_dt and projected["session_status"] != "Cancelled":
                current_session = projected
            if not upcoming_session and start_dt >= current_time:
                upcoming_session = projected
            if current_session and upcoming_session:
                break
    return {
        "current_session": current_session,
        "upcoming_session": upcoming_session,
        "day_sessions": sessions,
    }


def list_student_scheduled_sessions(student_id, days=14):
    student = get_student_by_id(student_id)
    if not student:
        return []
    known_class_names = list_registered_class_names()

    start = date.today()
    end = start + timedelta(days=max(days - 1, 0))

    ensure_sessions_for_range(start, end)
    refresh_session_statuses(start_date=start, end_date=end, allow_completion=False)

    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM class_sessions
            WHERE session_date BETWEEN ? AND ?
            ORDER BY session_date ASC, start_time ASC
            """,
            (start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")),
        ).fetchall()
        return [
            dict(row)
            for row in rows
            if schedule_visible_to_student(
                row["class_name"],
                student["class_name"],
                known_class_names,
            )
        ]
    finally:
        conn.close()


def get_valid_override(student_id, session_id, reference_time=None):
    current_time = (reference_time or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM override_permissions
            WHERE student_id = ? AND session_id = ? AND is_used = 0 AND expires_at >= ?
            ORDER BY id DESC
            LIMIT 1
            """,
            (student_id, session_id, current_time),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def grant_override(student_id, session_id, granted_by, reason, valid_minutes=5):
    now = datetime.now()
    expires_at = now + timedelta(minutes=valid_minutes)
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO override_permissions (
                student_id, session_id, granted_by, reason, granted_at, expires_at, is_used
            )
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (
                student_id,
                session_id,
                granted_by,
                reason,
                now.strftime("%Y-%m-%d %H:%M:%S"),
                expires_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def list_override_permissions(limit=100):
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT o.*,
                   s.name AS student_name,
                   s.class_name,
                   cs.subject_name,
                   cs.session_date,
                   cs.start_time,
                   cs.end_time,
                   a.email AS admin_email
            FROM override_permissions o
            JOIN students s ON s.id = o.student_id
            JOIN class_sessions cs ON cs.id = o.session_id
            LEFT JOIN admins a ON a.id = o.granted_by
            ORDER BY o.granted_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    current_time = datetime.now()
    items = []
    for row in rows:
        item = dict(row)
        expires_text = str(item.get("expires_at") or "").strip()
        try:
            expires_at = datetime.strptime(expires_text, "%Y-%m-%d %H:%M:%S") if expires_text else None
        except ValueError:
            expires_at = None

        if int(item.get("is_used") or 0):
            status = "Used"
        elif expires_at and expires_at < current_time:
            status = "Expired"
        else:
            status = "Active"

        item["status"] = status
        item["is_active"] = status == "Active"
        items.append(item)

    def status_sort_key(item):
        return {
            "Active": 0,
            "Used": 1,
            "Expired": 2,
        }.get(item.get("status"), 3)

    items.sort(key=lambda item: (status_sort_key(item), item.get("expires_at", ""), item.get("granted_at", "")))
    return items


def mark_override_used(override_id):
    conn = get_connection()
    try:
        conn.execute("UPDATE override_permissions SET is_used = 1 WHERE id = ?", (override_id,))
        conn.commit()
    finally:
        conn.close()


def get_effective_attendance_record(student_id, session_id):
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM attendance
            WHERE student_id = ? AND session_id = ? AND status != 'Rejected'
            ORDER BY
                CASE tracking_status
                    WHEN 'Tracking Active' THEN 0
                    WHEN 'WAITING_FOR_WINDOW_CLOSE' THEN 1
                    WHEN 'Tracking Completed' THEN 2
                    WHEN 'Attendance Cancelled' THEN 3
                    WHEN 'Not Started' THEN 4
                    WHEN 'Tracking Not Started' THEN 4
                    WHEN 'Not Required' THEN 5
                    ELSE 6
                END,
                CASE attendance_status
                    WHEN 'TRACKING_ACTIVE' THEN 0
                    WHEN 'MARKED_PENDING_TRACKING' THEN 1
                    WHEN 'FINALIZED' THEN 2
                    WHEN 'CANCELLED' THEN 2
                    WHEN 'PROVISIONAL' THEN 3
                    WHEN 'FINAL' THEN 4
                    ELSE 5
                END,
                COALESCE(NULLIF(last_location_checked_at, ''), NULLIF(updated_at, ''), created_at) DESC,
                id DESC
            LIMIT 1
            """,
            (student_id, session_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_attendance_record_by_id(attendance_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM attendance WHERE id = ?",
            (attendance_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_existing_session_attendance(student_id, session_id, attendance_date_value=None):
    if not student_id or not session_id:
        return None
    conn = get_connection()
    try:
        params = [student_id, session_id]
        date_clause = ""
        if attendance_date_value:
            date_clause = "AND attendance_date = ?"
            params.append(attendance_date_value)
        row = conn.execute(
            f"""
            SELECT *
            FROM attendance
            WHERE student_id = ?
              AND session_id = ?
              AND status != 'Rejected'
              {date_clause}
            ORDER BY COALESCE(NULLIF(updated_at, ''), created_at) DESC, id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_student_tracking_record(student_id, session_id=None):
    conn = get_connection()
    try:
        params = [student_id]
        session_clause = ""
        if session_id is not None:
            session_clause = "AND attendance.session_id = ?"
            params.append(session_id)

        row = conn.execute(
            f"""
            SELECT attendance.*,
                   class_sessions.class_name AS session_class_name,
                   class_sessions.subject_name,
                   class_sessions.teacher_name AS session_teacher_name,
                   class_sessions.substitute_teacher,
                   class_sessions.session_date,
                   class_sessions.start_time,
                   class_sessions.end_time,
                   class_sessions.gps_latitude AS session_gps_latitude,
                   class_sessions.gps_longitude AS session_gps_longitude,
                   class_sessions.allowed_radius_meters AS session_allowed_radius_meters,
                   class_sessions.post_attendance_tracking_minutes AS session_tracking_minutes
            FROM attendance
            LEFT JOIN class_sessions ON class_sessions.id = attendance.session_id
            WHERE attendance.student_id = ?
              AND attendance.status != 'Rejected'
              {session_clause}
            ORDER BY
                CASE attendance.tracking_status
                    WHEN 'Tracking Active' THEN 0
                    WHEN 'WAITING_FOR_WINDOW_CLOSE' THEN 1
                    WHEN 'Not Started' THEN 2
                    WHEN 'Tracking Not Started' THEN 2
                    WHEN 'Attendance Cancelled' THEN 3
                    WHEN 'Tracking Completed' THEN 4
                    WHEN 'Not Required' THEN 5
                    ELSE 6
                END,
                COALESCE(NULLIF(attendance.last_location_checked_at, ''), NULLIF(attendance.updated_at, ''), attendance.created_at) DESC,
                attendance.id DESC
            LIMIT 1
            """,
            tuple(params),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def list_attendance_tracking_records(target_date=None, active_only=False, limit=None):
    session_date = parse_date(target_date or date.today()).strftime("%Y-%m-%d")
    conn = get_connection()
    try:
        params = [session_date]
        where_clauses = [
            "class_sessions.session_date = ?",
            "attendance.status != 'Rejected'",
        ]
        if active_only:
            where_clauses.append("attendance.tracking_status = 'Tracking Active'")
        else:
            where_clauses.append(
                "(attendance.attendance_status IN ('MARKED_PENDING_TRACKING', 'TRACKING_ACTIVE', 'FINALIZED', 'CANCELLED', 'PROVISIONAL', 'FINAL') "
                "OR attendance.tracking_status IN ('WAITING_FOR_WINDOW_CLOSE', 'Tracking Active', 'Tracking Completed', 'Attendance Cancelled', 'Not Required'))"
            )
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT ?"
            params.append(max(1, int(limit)))

        rows = conn.execute(
            f"""
            SELECT attendance.*,
                   students.name AS student_name,
                   students.class_name AS student_class_name,
                   students.enrollment_number,
                   class_sessions.class_name AS session_class_name,
                   class_sessions.subject_name,
                   class_sessions.teacher_name AS session_teacher_name,
                   class_sessions.substitute_teacher,
                   class_sessions.session_date,
                   class_sessions.start_time,
                   class_sessions.end_time,
                   class_sessions.session_status,
                   class_sessions.gps_latitude AS session_gps_latitude,
                   class_sessions.gps_longitude AS session_gps_longitude,
                   class_sessions.allowed_radius_meters AS session_allowed_radius_meters,
                   class_sessions.post_attendance_tracking_minutes AS session_tracking_minutes
            FROM attendance
            JOIN students ON students.id = attendance.student_id
            JOIN class_sessions ON class_sessions.id = attendance.session_id
            WHERE {" AND ".join(where_clauses)}
            ORDER BY
                CASE attendance.attendance_status
                    WHEN 'TRACKING_ACTIVE' THEN 0
                    WHEN 'MARKED_PENDING_TRACKING' THEN 1
                    WHEN 'CANCELLED' THEN 2
                    WHEN 'FINALIZED' THEN 3
                    WHEN 'PROVISIONAL' THEN 4
                    WHEN 'FINAL' THEN 5
                    ELSE 6
                END,
                CASE attendance.tracking_status
                    WHEN 'Tracking Active' THEN 0
                    WHEN 'WAITING_FOR_WINDOW_CLOSE' THEN 1
                    WHEN 'Attendance Cancelled' THEN 2
                    WHEN 'Tracking Completed' THEN 3
                    WHEN 'Not Required' THEN 4
                    ELSE 5
                END,
                COALESCE(NULLIF(attendance.tracking_expires_at, ''), NULLIF(attendance.updated_at, ''), attendance.created_at) ASC,
                students.name ASC
            {limit_clause}
            """,
            tuple(params),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def start_attendance_tracking(
    attendance_id,
    tracking_minutes,
    latitude=None,
    longitude=None,
    started_at=None,
):
    tracking_minutes = _coerce_tracking_minutes(tracking_minutes, default_value=0) or 0
    started_dt = started_at or datetime.now()
    started_text = started_dt.strftime("%Y-%m-%d %H:%M:%S")

    conn = get_connection()
    try:
        if tracking_minutes <= 0:
            cur = conn.execute(
                """
                UPDATE attendance
                SET status = CASE
                        WHEN status = 'Provisional' THEN COALESCE(NULLIF(original_status, ''), 'Present')
                        ELSE status
                    END,
                    attendance_status = 'FINALIZED',
                    tracking_started_at = ?,
                    tracking_expires_at = '',
                    tracking_status = 'Not Required',
                    tracking_active = 0,
                    tracking_completed_at = ?,
                    attendance_cancelled_at = '',
                    cancellation_reason = '',
                    last_location_latitude = ?,
                    last_location_longitude = ?,
                    last_location_checked_at = ?,
                    out_of_range_count = 0,
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('Present', 'Late', 'Provisional')
                  AND COALESCE(attendance_status, '') NOT IN ('FINALIZED', 'CANCELLED', 'REJECTED')
                """,
                (
                    started_text,
                    started_text,
                    latitude,
                    longitude,
                    started_text if latitude is not None and longitude is not None else "",
                    started_text,
                    attendance_id,
                ),
            )
        else:
            expires_text = (started_dt + timedelta(minutes=tracking_minutes)).strftime(
                "%Y-%m-%d %H:%M:%S"
            )
            cur = conn.execute(
                """
                UPDATE attendance
                SET status = CASE
                        WHEN status = 'Rejected' THEN status
                        ELSE 'Provisional'
                    END,
                    attendance_status = 'TRACKING_ACTIVE',
                    tracking_started_at = ?,
                    tracking_expires_at = ?,
                    tracking_status = 'Tracking Active',
                    tracking_active = 1,
                    tracking_completed_at = '',
                    attendance_cancelled_at = '',
                    cancellation_reason = '',
                    last_location_latitude = ?,
                    last_location_longitude = ?,
                    last_location_checked_at = ?,
                    out_of_range_count = 0,
                    updated_at = ?
                WHERE id = ?
                  AND status IN ('Present', 'Late', 'Provisional')
                  AND COALESCE(attendance_status, '') NOT IN ('FINALIZED', 'CANCELLED', 'REJECTED')
                """,
                (
                    started_text,
                    expires_text,
                    latitude,
                    longitude,
                    started_text,
                    started_text,
                    attendance_id,
                ),
            )
        conn.commit()
        if cur.rowcount:
            logger.info(
                "tracking-start attendance_id=%s tracking_minutes=%s started_at=%s expires_at=%s",
                attendance_id,
                tracking_minutes,
                started_text,
                expires_text if tracking_minutes > 0 else "",
            )
        else:
            logger.info(
                "tracking-start-skipped attendance_id=%s tracking_minutes=%s",
                attendance_id,
                tracking_minutes,
            )
    finally:
        conn.close()
    return get_attendance_record_by_id(attendance_id)


def defer_attendance_tracking(
    attendance_id,
    latitude=None,
    longitude=None,
    accuracy_meters=None,
    raw_distance_meters=None,
    range_state="",
    marked_at=None,
):
    marked_text = (marked_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    tracking_window_starts_at = marked_text
    record = get_attendance_record_by_id(attendance_id)
    if record and record.get("session_id"):
        session_row = get_session_by_id(record["session_id"])
        if session_row and session_row.get("session_date") and session_row.get("late_close_time"):
            try:
                tracking_window_starts_at = combine_date_time(
                    session_row["session_date"],
                    session_row["late_close_time"],
                ).strftime("%Y-%m-%d %H:%M:%S")
            except Exception:
                tracking_window_starts_at = marked_text
    tracking_reference_latitude = None
    tracking_reference_longitude = None
    tracking_reference_radius_meters = None
    if record and record.get("session_id"):
        session_row = session_row if "session_row" in locals() else get_session_by_id(record["session_id"])
        if session_row:
            tracking_reference_latitude = session_row.get("gps_latitude")
            tracking_reference_longitude = session_row.get("gps_longitude")
            tracking_reference_radius_meters = session_row.get("allowed_radius_meters")
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE attendance
            SET status = CASE
                    WHEN status = 'Rejected' THEN status
                    ELSE 'Provisional'
                END,
                attendance_status = 'MARKED_PENDING_TRACKING',
                tracking_started_at = '',
                tracking_expires_at = '',
                tracking_status = 'WAITING_FOR_WINDOW_CLOSE',
                tracking_active = 0,
                tracking_completed_at = '',
                attendance_cancelled_at = '',
                cancellation_reason = '',
                tracking_window_starts_at = COALESCE(?, tracking_window_starts_at),
                tracking_reference_latitude = COALESCE(?, tracking_reference_latitude),
                tracking_reference_longitude = COALESCE(?, tracking_reference_longitude),
                tracking_reference_radius_meters = COALESCE(?, tracking_reference_radius_meters),
                latitude = COALESCE(?, latitude),
                longitude = COALESCE(?, longitude),
                last_location_latitude = COALESCE(?, last_location_latitude),
                last_location_longitude = COALESCE(?, last_location_longitude),
                last_location_accuracy_meters = COALESCE(?, last_location_accuracy_meters),
                last_raw_distance_meters = COALESCE(?, last_raw_distance_meters),
                last_range_state = COALESCE(NULLIF(?, ''), last_range_state),
                last_location_checked_at = CASE
                    WHEN ? IS NOT NULL AND ? IS NOT NULL THEN ?
                    ELSE last_location_checked_at
                END,
                out_of_range_count = 0,
                updated_at = ?
            WHERE id = ?
              AND status IN ('Present', 'Late', 'Provisional')
              AND COALESCE(attendance_status, '') NOT IN ('FINALIZED', 'CANCELLED', 'REJECTED')
            """,
            (
                tracking_window_starts_at,
                tracking_reference_latitude,
                tracking_reference_longitude,
                tracking_reference_radius_meters,
                latitude,
                longitude,
                latitude,
                longitude,
                accuracy_meters,
                raw_distance_meters,
                range_state,
                latitude,
                longitude,
                marked_text,
                marked_text,
                attendance_id,
            ),
        )
        conn.commit()
        if cur.rowcount:
            logger.info(
                "tracking-deferred attendance_id=%s tracking_window_starts_at=%s reference_gps=(%s,%s) radius=%s",
                attendance_id,
                tracking_window_starts_at,
                tracking_reference_latitude,
                tracking_reference_longitude,
                tracking_reference_radius_meters,
            )
    finally:
        conn.close()
    return get_attendance_record_by_id(attendance_id)


def complete_attendance_tracking(attendance_id, completed_at=None):
    completed_text = (completed_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE attendance
            SET tracking_status = 'Tracking Completed',
                status = CASE
                    WHEN status = 'Provisional' THEN COALESCE(NULLIF(original_status, ''), 'Present')
                    ELSE status
                END,
                attendance_status = 'FINALIZED',
                tracking_active = 0,
                tracking_completed_at = CASE
                    WHEN COALESCE(NULLIF(tracking_completed_at, ''), '') = '' THEN ?
                    ELSE tracking_completed_at
                END,
                out_of_range_count = 0,
                updated_at = ?
            WHERE id = ?
              AND status IN ('Present', 'Late', 'Provisional')
              AND tracking_status = 'Tracking Active'
            """,
            (
                completed_text,
                completed_text,
                attendance_id,
            ),
        )
        conn.commit()
        if cur.rowcount:
            logger.info("tracking-finalized attendance_id=%s completed_at=%s", attendance_id, completed_text)
    finally:
        conn.close()
    return get_attendance_record_by_id(attendance_id)


def cancel_attendance_tracking(attendance_id, cancellation_reason, cancelled_at=None):
    cancelled_text = (cancelled_at or datetime.now()).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            UPDATE attendance
            SET status = 'Cancelled',
                attendance_status = 'CANCELLED',
                tracking_status = 'Attendance Cancelled',
                tracking_active = 0,
                attendance_cancelled_at = ?,
                cancellation_reason = ?,
                rejection_reason = CASE
                    WHEN COALESCE(NULLIF(rejection_reason, ''), '') = '' THEN ?
                    ELSE rejection_reason
                END,
                updated_at = ?
            WHERE id = ?
            """,
            (
                cancelled_text,
                cancellation_reason,
                cancellation_reason,
                cancelled_text,
                attendance_id,
            ),
        )
        conn.commit()
        if cur.rowcount:
            logger.warning(
                "tracking-cancelled attendance_id=%s cancelled_at=%s reason=%s",
                attendance_id,
                cancelled_text,
                cancellation_reason,
            )
    finally:
        conn.close()
    return get_attendance_record_by_id(attendance_id)


def activate_pending_attendance_tracking(reference_time=None):
    current_dt = reference_time or datetime.now()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT attendance.id,
                   attendance.latitude,
                   attendance.longitude,
                   attendance.last_location_latitude,
                   attendance.last_location_longitude,
                   attendance.attendance_status,
                   attendance.tracking_status,
                   class_sessions.session_date,
                   class_sessions.late_close_time,
                   class_sessions.gps_latitude,
                   class_sessions.gps_longitude,
                   class_sessions.post_attendance_tracking_minutes
            FROM attendance
            JOIN class_sessions ON class_sessions.id = attendance.session_id
            WHERE attendance.status = 'Provisional'
              AND attendance.status != 'Rejected'
              AND (
                    attendance.attendance_status IN ('MARKED_PENDING_TRACKING', 'PROVISIONAL')
                    OR attendance.tracking_status IN ('WAITING_FOR_WINDOW_CLOSE', 'Not Started', 'Tracking Not Started')
              )
              AND class_sessions.session_status != 'Cancelled'
            """
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        record = dict(row)
        try:
            tracking_start_dt = combine_date_time(record["session_date"], record["late_close_time"])
        except Exception:
            tracking_start_dt = current_dt
        if current_dt < tracking_start_dt:
            continue

        latitude = record.get("last_location_latitude")
        longitude = record.get("last_location_longitude")
        if latitude is None:
            latitude = record.get("latitude")
        if longitude is None:
            longitude = record.get("longitude")

        tracking_minutes = _coerce_tracking_minutes(
            record.get("post_attendance_tracking_minutes"),
            default_value=get_post_attendance_tracking_default_minutes(),
        ) or 0
        if record.get("gps_latitude") is None or record.get("gps_longitude") is None:
            tracking_minutes = 0

        start_attendance_tracking(
            record["id"],
            tracking_minutes,
            latitude=latitude,
            longitude=longitude,
            started_at=tracking_start_dt,
        )
        logger.info(
            "tracking-activation-check attendance_id=%s tracking_minutes=%s tracking_start_at=%s",
            record["id"],
            tracking_minutes,
            tracking_start_dt.strftime("%Y-%m-%d %H:%M:%S"),
        )


def apply_attendance_tracking_heartbeat(
    attendance_id,
    latitude,
    longitude,
    is_in_range,
    distance_meters=None,
    raw_distance_meters=None,
    accuracy_meters=None,
    cancellation_reason="",
    cancel_threshold=DEFAULT_TRACKING_OUT_OF_RANGE_LIMIT,
    checked_at=None,
    range_state="in_range",
):
    checked_dt = checked_at or datetime.now()
    checked_text = checked_dt.strftime("%Y-%m-%d %H:%M:%S")

    record = get_attendance_record_by_id(attendance_id)
    if not record:
        return None

    if record.get("tracking_status") != "Tracking Active":
        return record

    expires_text = str(record.get("tracking_expires_at") or "").strip()
    expires_dt = None
    if expires_text:
        try:
            expires_dt = datetime.strptime(expires_text, "%Y-%m-%d %H:%M:%S")
        except ValueError:
            expires_dt = None

    current_count = int(record.get("out_of_range_count") or 0)
    previous_range_state = str(record.get("last_range_state") or "").strip().lower()
    normalized_range_state = str(range_state or "").strip().lower() or (
        "in_range" if is_in_range else "out_of_range"
    )
    if normalized_range_state == "uncertain":
        next_count = current_count
    elif is_in_range:
        next_count = 0
    else:
        next_count = current_count + 1

    conn = get_connection()
    try:
        if normalized_range_state == "uncertain":
            conn.execute(
                """
                UPDATE attendance
                SET last_location_accuracy_meters = COALESCE(?, last_location_accuracy_meters),
                    last_location_checked_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    accuracy_meters,
                    checked_text,
                    checked_text,
                    attendance_id,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE attendance
                SET last_location_latitude = ?,
                    last_location_longitude = ?,
                    distance_meters = COALESCE(?, distance_meters),
                    last_raw_distance_meters = COALESCE(?, last_raw_distance_meters),
                    last_location_accuracy_meters = COALESCE(?, last_location_accuracy_meters),
                    last_range_state = ?,
                    last_location_checked_at = ?,
                    out_of_range_count = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    latitude,
                    longitude,
                    distance_meters,
                    raw_distance_meters,
                    accuracy_meters,
                    normalized_range_state,
                    checked_text,
                    next_count,
                    checked_text,
                    attendance_id,
                ),
            )
        conn.commit()
    finally:
        conn.close()

    threshold = max(3, int(cancel_threshold or 0))

    if expires_dt and checked_dt >= expires_dt:
        if normalized_range_state == "out_of_range":
            logger.warning(
                "tracking-heartbeat-out-of-range-final attendance_id=%s out_of_range_count=%s checked_at=%s",
                attendance_id,
                next_count,
                checked_text,
            )
            return cancel_attendance_tracking(
                attendance_id,
                cancellation_reason or "Attendance cancelled because your final GPS check was outside the allowed range.",
                cancelled_at=checked_dt,
            )
        if normalized_range_state == "uncertain":
            if previous_range_state == "in_range":
                logger.info(
                    "tracking-heartbeat-complete attendance_id=%s reason=previous_in_range_final checked_at=%s",
                    attendance_id,
                    checked_text,
                )
                return complete_attendance_tracking(attendance_id, completed_at=checked_dt)
            if previous_range_state == "out_of_range":
                logger.warning(
                    "tracking-heartbeat-cancel attendance_id=%s reason=previous_out_of_range_final checked_at=%s",
                    attendance_id,
                    checked_text,
                )
                return cancel_attendance_tracking(
                    attendance_id,
                    cancellation_reason or "Attendance cancelled because the latest reliable GPS reading was outside the allowed range.",
                    cancelled_at=checked_dt,
                )
            logger.warning(
                "tracking-heartbeat-uncertain-final attendance_id=%s accuracy=%s checked_at=%s",
                attendance_id,
                accuracy_meters,
                checked_text,
            )
            return cancel_attendance_tracking(
                attendance_id,
                "Attendance cancelled because the final GPS reading was not accurate enough for reliable verification.",
                cancelled_at=checked_dt,
            )
        logger.info("tracking-heartbeat-expired attendance_id=%s checked_at=%s", attendance_id, checked_text)
        return complete_attendance_tracking(attendance_id, completed_at=checked_dt)

    if normalized_range_state == "out_of_range" and next_count >= threshold:
        logger.warning(
            "tracking-heartbeat-out-of-range attendance_id=%s out_of_range_count=%s checked_at=%s",
            attendance_id,
            next_count,
            checked_text,
        )
        return cancel_attendance_tracking(
            attendance_id,
            cancellation_reason,
            cancelled_at=checked_dt,
        )
    return get_attendance_record_by_id(attendance_id)


def finalize_expired_attendance_tracking(reference_time=None):
    current_dt = reference_time or datetime.now()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id,
                   tracking_expires_at,
                   last_location_checked_at,
                   out_of_range_count,
                   cancellation_reason,
                   last_range_state,
                   last_location_accuracy_meters,
                   last_raw_distance_meters
            FROM attendance
            WHERE tracking_status = 'Tracking Active'
              AND status IN ('Present', 'Late', 'Provisional')
              AND COALESCE(NULLIF(tracking_expires_at, ''), '') != ''
              AND tracking_expires_at <= ?
            """,
            (current_dt.strftime("%Y-%m-%d %H:%M:%S"),),
        ).fetchall()
    finally:
        conn.close()

    for row in rows:
        record = dict(row)
        try:
            expires_dt = datetime.strptime(str(record.get("tracking_expires_at") or "").strip(), "%Y-%m-%d %H:%M:%S")
        except ValueError:
            expires_dt = current_dt
        last_checked_text = str(record.get("last_location_checked_at") or "").strip()
        last_checked_dt = None
        if last_checked_text:
            try:
                last_checked_dt = datetime.strptime(last_checked_text, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                last_checked_dt = None

        out_of_range_count = int(record.get("out_of_range_count") or 0)
        last_range_state = str(record.get("last_range_state") or "").strip().lower()
        heartbeat_is_stale = (
            last_checked_dt is None
            or last_checked_dt < (expires_dt - timedelta(seconds=DEFAULT_TRACKING_HEARTBEAT_GRACE_SECONDS))
        )

        if out_of_range_count >= DEFAULT_TRACKING_OUT_OF_RANGE_LIMIT:
            logger.warning(
                "tracking-finalize-cancel attendance_id=%s reason=out_of_range_count count=%s",
                record["id"],
                out_of_range_count,
            )
            cancel_attendance_tracking(
                record["id"],
                record.get("cancellation_reason")
                or "Attendance cancelled because your GPS tracker detected that you moved outside the allowed range before tracking completed.",
                cancelled_at=current_dt,
            )
            continue

        if last_range_state == "out_of_range":
            logger.warning(
                "tracking-finalize-cancel attendance_id=%s reason=final_out_of_range distance=%s",
                record["id"],
                record.get("last_raw_distance_meters"),
            )
            cancel_attendance_tracking(
                record["id"],
                record.get("cancellation_reason")
                or "Attendance cancelled because the final GPS reading was outside the allowed range.",
                cancelled_at=current_dt,
            )
            continue

        if last_range_state == "uncertain":
            logger.warning(
                "tracking-finalize-cancel attendance_id=%s reason=low_accuracy accuracy=%s",
                record["id"],
                record.get("last_location_accuracy_meters"),
            )
            cancel_attendance_tracking(
                record["id"],
                "Attendance cancelled because the final GPS reading was not accurate enough for reliable verification.",
                cancelled_at=current_dt,
            )
            continue

        if heartbeat_is_stale:
            if last_checked_dt and last_range_state == "in_range":
                logger.info(
                    "tracking-finalize-complete attendance_id=%s reason=last_valid_in_range last_checked_at=%s",
                    record["id"],
                    record.get("last_location_checked_at"),
                )
                complete_attendance_tracking(record["id"], completed_at=current_dt)
                continue
            logger.warning(
                "tracking-finalize-cancel attendance_id=%s reason=stale_heartbeat expires_at=%s last_checked_at=%s",
                record["id"],
                record.get("tracking_expires_at"),
                record.get("last_location_checked_at"),
            )
            cancel_attendance_tracking(
                record["id"],
                "Attendance cancelled because GPS tracking was interrupted before the tracking timer completed.",
                cancelled_at=current_dt,
            )
            continue

        logger.info("tracking-finalize-complete attendance_id=%s completed_at=%s", record["id"], current_dt.strftime("%Y-%m-%d %H:%M:%S"))
        complete_attendance_tracking(record["id"], completed_at=current_dt)


def create_attendance_record(
    student_id,
    session_id,
    name,
    class_name,
    subject_name,
    teacher_name,
    emotion="",
    latitude=None,
    longitude=None,
    distance_meters=None,
    face_verified=False,
    spoof_status="pending",
    status="Present",
    rejection_reason="",
    marked_via="face_scan",
    override_permission_id=None,
    override_granted_by=None,
    override_used=False,
    proof_snapshot_path="",
    recorded_identity_name="",
    notification_sent=False,
    correction_request_id=None,
    original_status=None,
    attendance_status="",
    attendance_date_value=None,
    attendance_time_value=None,
    tracking_started_at="",
    tracking_expires_at="",
    tracking_status="Not Started",
    tracking_active=False,
    tracking_completed_at="",
    attendance_cancelled_at="",
    cancellation_reason="",
    tracking_window_starts_at="",
    tracking_reference_latitude=None,
    tracking_reference_longitude=None,
    tracking_reference_radius_meters=None,
    last_location_latitude=None,
    last_location_longitude=None,
    last_location_checked_at="",
    out_of_range_count=0,
):
    attendance_date_value = attendance_date_value or date_string()
    attendance_time_value = attendance_time_value or time_string()
    conn = get_connection()
    try:
        if student_id and session_id:
            existing = conn.execute(
                """
                SELECT id
                FROM attendance
                WHERE student_id = ?
                  AND session_id = ?
                  AND attendance_date = ?
                  AND status != 'Rejected'
                ORDER BY id DESC
                LIMIT 1
                """,
                (student_id, session_id, attendance_date_value),
            ).fetchone()
            if existing:
                return None
        cur = conn.execute(
            """
            INSERT INTO attendance (
                student_id, session_id, name, class_name, subject_name, teacher_name,
                date, time, attendance_date, attendance_time, emotion, latitude, longitude,
                distance_meters, face_verified, spoof_status, status, rejection_reason,
                marked_via, override_permission_id, override_granted_by, override_used,
                proof_snapshot_path, recorded_identity_name, notification_sent,
                correction_request_id, original_status, tracking_started_at,
                attendance_status, tracking_expires_at, tracking_status, tracking_active, tracking_completed_at,
                attendance_cancelled_at, cancellation_reason, tracking_window_starts_at,
                tracking_reference_latitude, tracking_reference_longitude, tracking_reference_radius_meters,
                last_location_latitude,
                last_location_longitude, last_location_checked_at, out_of_range_count,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                student_id,
                session_id,
                name,
                class_name,
                subject_name,
                teacher_name,
                attendance_date_value,
                attendance_time_value,
                attendance_date_value,
                attendance_time_value,
                emotion,
                latitude,
                longitude,
                distance_meters,
                1 if face_verified else 0,
                spoof_status,
                status,
                rejection_reason,
                marked_via,
                override_permission_id,
                override_granted_by,
                1 if override_used else 0,
                proof_snapshot_path,
                recorded_identity_name or name,
                1 if notification_sent else 0,
                correction_request_id,
                original_status or status,
                tracking_started_at,
                attendance_status,
                tracking_expires_at,
                tracking_status,
                1 if tracking_active else 0,
                tracking_completed_at,
                attendance_cancelled_at,
                cancellation_reason,
                tracking_window_starts_at,
                tracking_reference_latitude,
                tracking_reference_longitude,
                tracking_reference_radius_meters,
                last_location_latitude,
                last_location_longitude,
                last_location_checked_at,
                out_of_range_count,
                f"{attendance_date_value} {attendance_time_value}",
                now_string(),
            ),
        )
        conn.commit()
        return cur.lastrowid
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def create_rejected_attendance_attempt(
    student_id,
    session_id,
    rejection_reason,
    marked_via="student_self",
    latitude=None,
    longitude=None,
    distance_meters=None,
    spoof_status="failed",
    proof_snapshot_path="",
    recorded_identity_name="",
):
    student = get_student_by_id(student_id)
    session_row = get_session_by_id(session_id)
    if not student or not session_row:
        return None
    return create_attendance_record(
        student_id=student_id,
        session_id=session_id,
        name=student["name"],
        class_name=student["class_name"],
        subject_name=session_row["subject_name"],
        teacher_name=session_row["substitute_teacher"] or session_row["teacher_name"],
        latitude=latitude,
        longitude=longitude,
        distance_meters=distance_meters,
        face_verified=False,
        spoof_status=spoof_status,
        status="Rejected",
        attendance_status="REJECTED",
        rejection_reason=rejection_reason,
        marked_via=marked_via,
        proof_snapshot_path=proof_snapshot_path,
        recorded_identity_name=recorded_identity_name or student["name"],
        attendance_date_value=session_row["session_date"],
    )


def mark_attendance(name, emotion, student_id=None, session_id=None, **kwargs):
    student = get_student_by_id(student_id) if student_id else get_student_by_name(name)
    if student is None:
        return False
    if session_id is None:
        active_context = get_active_session_for_student(student["id"])
        session = active_context.get("current_session") if active_context else None
        session_id = session["id"] if session else None
        if session_id is None:
            return False
    if get_effective_attendance_record(student["id"], session_id):
        return False
    session_row = get_session_by_id(session_id)
    if not session_row:
        return False
    return create_attendance_record(
        student_id=student["id"],
        session_id=session_id,
        name=student["name"],
        class_name=student.get("class_name", ""),
        subject_name=session_row["subject_name"],
        teacher_name=session_row["substitute_teacher"] or session_row["teacher_name"],
        emotion=emotion,
        latitude=kwargs.get("latitude"),
        longitude=kwargs.get("longitude"),
        distance_meters=kwargs.get("distance_meters"),
        face_verified=kwargs.get("face_verified", True),
        spoof_status=kwargs.get("spoof_status", "passed"),
        status=kwargs.get("status", "Present"),
        rejection_reason=kwargs.get("rejection_reason", ""),
        marked_via=kwargs.get("marked_via", "face_scan"),
        override_permission_id=kwargs.get("override_permission_id"),
        override_granted_by=kwargs.get("override_granted_by"),
        override_used=kwargs.get("override_used", False),
        proof_snapshot_path=kwargs.get("proof_snapshot_path", ""),
        recorded_identity_name=kwargs.get("recorded_identity_name", student["name"]),
        original_status=kwargs.get("original_status"),
        attendance_status=kwargs.get("attendance_status", "FINAL"),
        attendance_date_value=session_row["session_date"],
        tracking_started_at=kwargs.get("tracking_started_at", ""),
        tracking_expires_at=kwargs.get("tracking_expires_at", ""),
        tracking_status=kwargs.get("tracking_status", "Not Started"),
        tracking_active=kwargs.get("tracking_active", False),
        tracking_completed_at=kwargs.get("tracking_completed_at", ""),
        attendance_cancelled_at=kwargs.get("attendance_cancelled_at", ""),
        cancellation_reason=kwargs.get("cancellation_reason", ""),
        last_location_latitude=kwargs.get("last_location_latitude"),
        last_location_longitude=kwargs.get("last_location_longitude"),
        last_location_checked_at=kwargs.get("last_location_checked_at", ""),
        out_of_range_count=kwargs.get("out_of_range_count", 0),
    )


def get_today_present_students():
    today = date_string()
    ensure_sessions_for_date(today)
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT DISTINCT students.name AS student_name
            FROM attendance
            JOIN students ON students.id = attendance.student_id
            JOIN class_sessions ON class_sessions.id = attendance.session_id
            WHERE class_sessions.session_date = ?
              AND attendance.status IN ('Present', 'Late')
            ORDER BY student_name ASC
            """,
            (today,),
        ).fetchall()
        return [row["student_name"] for row in rows]
    finally:
        conn.close()


def _coerce_summary_history_date(value):
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return parse_date(value)
    except Exception:
        return None


def _history_row_is_attended(row):
    status = str(row.get("status") or "").strip().lower()
    attendance_status = str(row.get("attendance_status") or "").strip().lower()
    return status in {"present", "late"} or attendance_status in {"finalized", "final"}


def _history_row_is_absent(row):
    status = str(row.get("status") or "").strip().lower()
    attendance_status = str(row.get("attendance_status") or "").strip().lower()
    return status in {"absent", "cancelled", "rejected"} or attendance_status in {"cancelled", "rejected"}


def _build_student_summary_map(student_names, summary_map=None):
    if summary_map is not None:
        return summary_map

    requested_names = {str(name) for name in student_names}
    return {
        student["name"]: get_student_attendance_summary(student["id"])
        for student in get_all_students()
        if student["name"] in requested_names
    }


def get_last_3_days_attendance_map(student_names, summary_map=None):
    details = get_last_3_days_attendance_details(student_names, summary_map=summary_map)
    return {name: details.get(name, {}).get("attendance", 0) for name in student_names}


def get_last_3_days_attendance_details(student_names, summary_map=None):
    details = {
        student: {"present_dates": [], "attendance": 0, "total_classes": 0, "percentage": 0}
        for student in student_names
    }
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=2)
    ensure_sessions_for_range(start_date, end_date)
    student_summaries = _build_student_summary_map(student_names, summary_map=summary_map)

    for student_name in student_names:
        summary = student_summaries.get(student_name) or {}
        history = summary.get("history") or []
        recent_rows = []
        seen_sessions = set()

        for row in history:
            session_date = _coerce_summary_history_date(row.get("date"))
            if not session_date or session_date < start_date or session_date > end_date:
                continue
            if str(row.get("session_status") or "").strip() != "Completed":
                continue
            session_key = row.get("session_id") or (
                row.get("date"),
                row.get("subject_name"),
                row.get("start_time"),
            )
            if session_key in seen_sessions:
                continue
            seen_sessions.add(session_key)
            recent_rows.append((session_date, row))

        present_dates = sorted(
            {
                session_date.strftime("%Y-%m-%d")
                for session_date, row in recent_rows
                if _history_row_is_attended(row)
            }
        )
        attendance = sum(1 for _, row in recent_rows if _history_row_is_attended(row))
        total_classes = len(recent_rows)
        details[student_name] = {
            "present_dates": present_dates,
            "attendance": attendance,
            "total_classes": total_classes,
            "percentage": round((attendance / total_classes) * 100, 2) if total_classes else 0,
        }

    return details


def get_last_3_days_chart(student_names, summary_map=None):
    details = get_last_3_days_attendance_details(student_names, summary_map=summary_map)
    return [
        {
            "name": name,
            "attendance": details.get(name, {}).get("attendance", 0),
            "total_classes": details.get(name, {}).get("total_classes", 0),
            "percentage": details.get(name, {}).get("percentage", 0),
        }
        for name in student_names
    ]


def get_month_calendar_data(student_names, year=None, month=None, summary_map=None):
    today = datetime.now().date()
    target_year = year or today.year
    target_month = month or today.month
    _, total_days = calendar.monthrange(target_year, target_month)
    start = date(target_year, target_month, 1)
    end = date(target_year, target_month, total_days)
    ensure_sessions_for_range(start, end)
    student_summaries = _build_student_summary_map(student_names, summary_map=summary_map)

    calendar_data = {}
    for student_name in student_names:
        items = []
        student_lookup = defaultdict(list)
        summary = student_summaries.get(student_name) or {}

        for row in summary.get("history") or []:
            session_date = _coerce_summary_history_date(row.get("date"))
            if not session_date or session_date < start or session_date > end:
                continue
            student_lookup[session_date.strftime("%Y-%m-%d")].append(row)

        for day_number in range(1, total_days + 1):
            current = date(target_year, target_month, day_number)
            iso = current.strftime("%Y-%m-%d")
            if current > today:
                status = "upcoming"
            else:
                day_records = student_lookup.get(iso, [])
                if any(_history_row_is_attended(record) for record in day_records):
                    status = "present"
                elif any(str(record.get("session_status") or "").strip() == "Cancelled" for record in day_records):
                    status = "holiday"
                elif any(_history_row_is_absent(record) for record in day_records):
                    status = "absent"
                else:
                    status = "idle"
            items.append({"day": day_number, "date": iso, "status": status, "weekday": current.strftime("%a")})
        calendar_data[student_name] = items

    return {
        "year": target_year,
        "month": target_month,
        "month_name": calendar.month_name[target_month],
        "days": calendar_data,
    }
