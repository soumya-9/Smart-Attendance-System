import base64
import binascii
import csv
import json
import logging
import os
import re
import smtplib
import sys
import threading
import traceback
from email.message import EmailMessage
from functools import wraps
from io import StringIO
from pathlib import Path
from datetime import date, datetime, timedelta
from math import atan2, cos, radians, sin, sqrt

import cv2
import numpy as np
from flask import (
    abort,
    Flask,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    send_file,
    url_for,
)

from assistant_logic import (
    generate_admin_assistant_reply,
    generate_assistant_reply,
    generate_student_assistant_reply,
)
from database import (
    activate_pending_attendance_tracking,
    DAY_NAMES,
    add_holiday,
    apply_attendance_tracking_heartbeat,
    auto_mark_absent_for_closed_sessions,
    class_names_match,
    combine_date_time,
    create_class_schedule,
    create_correction_request,
    create_student,
    defer_attendance_tracking,
    delete_student,
    delete_class_schedule,
    delete_class_session,
    delete_holiday,
    ensure_schedule_days_are_working,
    ensure_default_admin,
    finalize_expired_attendance_tracking,
    get_all_students,
    get_active_session_for_student,
    get_attendance_record_by_id,
    get_attendance_report,
    get_dashboard_stats,
    get_existing_session_attendance,
    get_effective_attendance_record,
    get_app_setting,
    get_known_face_records,
    get_legacy_students,
    get_low_attendance_threshold,
    get_post_attendance_tracking_default_minutes,
    get_primary_admin,
    get_week_bounds,
    get_schedule_by_id,
    get_session_tracking_minutes,
    get_session_by_id,
    get_session_by_schedule_and_date,
    get_student_tracking_record,
    list_student_scheduled_sessions,
    get_student_attendance_summary,
    get_student_by_name,
    get_student_by_id,
    get_student_by_email,
    get_student_by_enrollment,
    get_student_sessions,
    get_valid_override,
    grant_override,
    init_db,
    list_class_schedules,
    list_class_sessions,
    list_correction_requests,
    list_holidays,
    list_attendance_tracking_records,
    list_gps_change_logs,
    list_override_permissions,
    mark_attendance,
    mark_absence_notification_sent,
    mark_override_used,
    register_known_face_seed,
    reassign_attendance_to_student,
    review_correction_request,
    schedule_visible_to_student,
    set_app_setting,
    start_attendance_tracking,
    update_class_schedule,
    update_class_session_status,
    update_session_gps,
    update_working_days,
    update_student,
    verify_admin_credentials,
    verify_student_credentials,
)

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def configure_console_encoding():
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="backslashreplace")
            except Exception:
                pass


configure_console_encoding()

os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
os.environ.setdefault("TF_USE_LEGACY_KERAS", "1")


def resolve_deepface_detector_backend():
    configured_backend = str(os.getenv("DEEPFACE_DETECTOR_BACKEND", "")).strip().lower()
    if configured_backend:
        return configured_backend

    cv2_data = getattr(cv2, "data", None)
    if cv2_data and getattr(cv2_data, "haarcascades", ""):
        return "opencv"

    return "skip"


try:
    from deepface import DeepFace
except Exception as import_error:
    DeepFace = None
    DEEPFACE_IMPORT_ERROR = str(import_error)
else:
    DEEPFACE_IMPORT_ERROR = None

try:
    import face_recognition
except Exception as import_error:
    face_recognition = None
    FACE_RECOGNITION_IMPORT_ERROR = str(import_error)
else:
    FACE_RECOGNITION_IMPORT_ERROR = None


BASE_DIR = Path(__file__).resolve().parent
KNOWN_FACES_DIR = BASE_DIR / "known_faces"
KNOWN_FACES_DIR.mkdir(exist_ok=True)
EMAIL_OUTBOX_DIR = BASE_DIR / "email_outbox"
EMAIL_OUTBOX_DIR.mkdir(exist_ok=True)
PROOF_SNAPSHOTS_DIR = BASE_DIR / "static" / "proof_snapshots"
PROOF_SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
SMTP_SETTINGS_FILE = BASE_DIR / "smtp_settings.json"

DEFAULT_OWNER = "Soumyadip Bhattacharya"
ASSISTANT_NAME = "Smart Attendance System"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 5000
DISPLAY_HOST = "127.0.0.1"
DETECTOR_BACKEND = resolve_deepface_detector_backend()
RECOGNITION_MODEL_NAME = os.getenv("DEEPFACE_RECOGNITION_MODEL", "SFace")
WEIGHTS_DIR = Path.home() / ".deepface" / "weights"
SUPPORTED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png"}
MAX_STUDENT_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CANDIDATE_IMAGES_PER_STUDENT = max(1, int(os.getenv("MAX_CANDIDATE_IMAGES_PER_STUDENT", "4")))
MAX_ATTENDANCE_DISTANCE_METERS = 60.0
GPS_TRACKING_OUT_OF_RANGE_LIMIT = 3
GPS_MAX_READING_AGE_SECONDS = 20.0
GPS_SAME_LOCATION_TOLERANCE_METERS = 10.0
GPS_MIN_POOR_ACCURACY_METERS = 25.0
GPS_MAX_JITTER_BUFFER_METERS = 8.0
LOCKED_TRACKING_WORKFLOW_STATUSES = {"MARKED_PENDING_TRACKING", "TRACKING_ACTIVE"}
COMPLETED_TRACKING_WORKFLOW_STATUSES = {"FINALIZED", "CANCELLED"}
STUDENT_ATTENDANCE_PREVIEW_SESSION_KEY = "student_attendance_preview"
STUDENT_ATTENDANCE_PREVIEW_TTL_SECONDS = 45
MOBILE_NUMBER_PATTERN = re.compile(r"^\+?[0-9]{10,15}$")

ADMIN_EMAIL = os.getenv("ADMIN_EMAIL", "admin@attendance.local")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")
SECRET_KEY = os.getenv("FLASK_SECRET_KEY", "ai-assistant-secret-key")

SMTP_HOST = os.getenv("SMTP_HOST", "")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_SENDER = os.getenv("SMTP_SENDER", SMTP_USERNAME or ADMIN_EMAIL)
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"

MODEL_DOWNLOAD_HINTS = {
    "SFace": "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "VGG-Face": "https://github.com/serengil/deepface_models/releases/download/v1.0/vgg_face_weights.h5",
    "Facenet": "https://github.com/serengil/deepface_models/releases/download/v1.0/facenet_weights.h5",
    "Facenet512": "https://github.com/serengil/deepface_models/releases/download/v1.0/facenet512_weights.h5",
    "ArcFace": "https://github.com/serengil/deepface_models/releases/download/v1.0/arcface_weights.h5",
    "OpenFace": "https://github.com/serengil/deepface_models/releases/download/v1.0/openface_weights.h5",
}

app = Flask(__name__)
app.secret_key = SECRET_KEY
app.config["JSON_SORT_KEYS"] = False

init_db()
ensure_default_admin(ADMIN_EMAIL, ADMIN_PASSWORD)


def sanitize_text(value):
    if value is None:
        return ""
    return str(value).encode("ascii", errors="backslashreplace").decode("ascii")


def normalize_mobile_number(value):
    mobile_number = str(value or "").strip()
    if not mobile_number:
        return ""
    return mobile_number if MOBILE_NUMBER_PATTERN.fullmatch(mobile_number) else ""


def log_message(label, message):
    logger.warning("[%s] %s", label, sanitize_text(message))


def load_face_cascade():
    candidate_paths = []

    cv2_data = getattr(cv2, "data", None)
    if cv2_data and getattr(cv2_data, "haarcascades", ""):
        candidate_paths.append(Path(cv2_data.haarcascades) / "haarcascade_frontalface_default.xml")

    candidate_paths.append(Path(cv2.__file__).resolve().parent / "data" / "haarcascade_frontalface_default.xml")

    for candidate in candidate_paths:
        try:
            if candidate.exists():
                cascade = cv2.CascadeClassifier(str(candidate))
                if not cascade.empty():
                    return cascade, str(candidate)
        except Exception as error:
            log_message("cascade-load", error)

    return None, ""


face_cascade, FACE_CASCADE_PATH = load_face_cascade()

known_faces = {}
all_students = []
ENGINE_STATE = {}
KNOWN_FACE_ENCODINGS = {}
ENGINE_BOOTSTRAP_STARTED = False
ENGINE_BOOTSTRAP_LOCK = threading.Lock()
ANALYZE_LOCK = threading.Lock()
FACE_RECOGNITION_THRESHOLD = float(os.getenv("FACE_RECOGNITION_THRESHOLD", "0.72"))
SINGLE_STUDENT_THRESHOLD = float(os.getenv("SINGLE_STUDENT_THRESHOLD", "0.9"))
ANALYSIS_MAX_WIDTH = max(240, int(os.getenv("ANALYSIS_MAX_WIDTH", "360")))
EMOTION_FACE_MAX_DIMENSION = max(160, int(os.getenv("EMOTION_FACE_MAX_DIMENSION", "224")))
ANTI_SPOOF_MIN_FRAMES = max(2, int(os.getenv("ANTI_SPOOF_MIN_FRAMES", "3")))
ANTI_SPOOF_MOTION_THRESHOLD = float(os.getenv("ANTI_SPOOF_MOTION_THRESHOLD", "1.6"))
ANTI_SPOOF_LANDMARK_DELTA_THRESHOLD = float(os.getenv("ANTI_SPOOF_LANDMARK_DELTA_THRESHOLD", "0.008"))
ANTI_SPOOF_MIN_TEXTURE = float(os.getenv("ANTI_SPOOF_MIN_TEXTURE", "18.0"))
ANTI_SPOOF_MAX_EDGE_DENSITY = float(os.getenv("ANTI_SPOOF_MAX_EDGE_DENSITY", "0.03"))
ANTI_SPOOF_MAX_SATURATED_RATIO = float(os.getenv("ANTI_SPOOF_MAX_SATURATED_RATIO", "0.18"))
ANTI_SPOOF_FACE_SHIFT_THRESHOLD = float(os.getenv("ANTI_SPOOF_FACE_SHIFT_THRESHOLD", "0.008"))
ANTI_SPOOF_POSE_DELTA_THRESHOLD = float(os.getenv("ANTI_SPOOF_POSE_DELTA_THRESHOLD", "0.004"))
ANALYSIS_FRAME_LIMIT = max(
    1,
    int(os.getenv("ANALYSIS_FRAME_LIMIT", os.getenv("EMOTION_BURST_FRAMES", "4"))),
)
EMOTION_CANDIDATE_LIMIT = max(1, int(os.getenv("EMOTION_CANDIDATE_LIMIT", "3")))


def sanitize_filename(value):
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", value.strip().lower())
    return cleaned.strip("_") or "student"


def normalize_email_address(value):
    return str(value or "").strip().lower()


def is_valid_email_address(value):
    return bool(re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", normalize_email_address(value)))


def normalize_smtp_secret(value):
    return re.sub(r"\s+", "", str(value or "").strip())


def is_placeholder_secret(value):
    lowered = normalize_smtp_secret(value).lower()
    placeholders = {
        "",
        "12345",
        "123456",
        "password",
        "your-password",
        "your app password",
        "app-password",
        "app password",
    }
    return lowered in placeholders


def validate_smtp_settings(host, port, username, password, sender):
    normalized_host = str(host or "").strip().lower()
    normalized_username = normalize_email_address(username)
    normalized_sender = normalize_email_address(sender)

    if not normalized_host:
        return "SMTP host is required."

    if not (1 <= int(port) <= 65535):
        return "SMTP port must be between 1 and 65535."

    if not normalized_username:
        return "SMTP username is required."

    if "gmail.com" in normalized_host and not is_valid_email_address(normalized_username):
        return "For Gmail, SMTP username must be the full Gmail address."

    if normalized_sender and not is_valid_email_address(normalized_sender):
        return "Sender email must be a valid email address."

    if "gmail.com" in normalized_host and is_placeholder_secret(password):
        return "For Gmail, use a Google App Password instead of your normal password."

    if not str(password or "").strip():
        return "SMTP password or app password is required."

    return None


def load_smtp_settings():
    settings = {
        "host": SMTP_HOST,
        "port": SMTP_PORT,
        "username": SMTP_USERNAME,
        "password": SMTP_PASSWORD,
        "sender": SMTP_SENDER,
        "use_tls": SMTP_USE_TLS,
    }

    if not SMTP_SETTINGS_FILE.exists():
        return settings

    try:
        saved = json.loads(SMTP_SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return settings

    settings.update(
        {
            "host": str(saved.get("host", settings["host"])).strip(),
            "port": int(saved.get("port", settings["port"])),
            "username": normalize_email_address(saved.get("username", settings["username"])),
            "password": normalize_smtp_secret(saved.get("password", settings["password"])),
            "sender": normalize_email_address(saved.get("sender", settings["sender"])),
            "use_tls": bool(saved.get("use_tls", settings["use_tls"])),
        }
    )
    if not settings["sender"] and is_valid_email_address(settings["username"]):
        settings["sender"] = settings["username"]
    return settings


def save_smtp_settings(host, port, username, password, sender, use_tls):
    normalized_username = normalize_email_address(username)
    normalized_sender = normalize_email_address(sender) or normalized_username
    payload = {
        "host": host.strip(),
        "port": int(port),
        "username": normalized_username,
        "password": normalize_smtp_secret(password),
        "sender": normalized_sender,
        "use_tls": bool(use_tls),
    }
    SMTP_SETTINGS_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def smtp_is_configured():
    settings = load_smtp_settings()
    return bool(settings["host"] and settings["username"] and settings["password"])


def decode_base64_image(data_url):
    if not isinstance(data_url, str) or "," not in data_url:
        return None

    try:
        encoded = data_url.split(",", 1)[1]
        image_bytes = base64.b64decode(encoded)
        image_array = np.frombuffer(image_bytes, dtype=np.uint8)
        return cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    except (ValueError, binascii.Error):
        return None


def resize_frame_for_analysis(frame, max_width=ANALYSIS_MAX_WIDTH):
    if frame is None:
        return None

    height, width = frame.shape[:2]
    if width <= max_width:
        return frame

    scale = max_width / float(width)
    target_size = (max(int(width * scale), 1), max(int(height * scale), 1))
    return cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)


def enhance_frame_for_detection(frame):
    if frame is None or not frame.size:
        return None

    lab = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
    l_channel, a_channel, b_channel = cv2.split(lab)
    clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
    l_channel = clahe.apply(l_channel)
    enhanced_lab = cv2.merge((l_channel, a_channel, b_channel))
    enhanced = cv2.cvtColor(enhanced_lab, cv2.COLOR_LAB2BGR)

    gamma = 1.28
    gamma_table = np.array(
        [((index / 255.0) ** (1.0 / gamma)) * 255 for index in range(256)],
        dtype=np.uint8,
    )
    return cv2.LUT(enhanced, gamma_table)


def resize_image_max_dimension(image, max_dimension=EMOTION_FACE_MAX_DIMENSION):
    if image is None or image.size == 0:
        return None

    height, width = image.shape[:2]
    longest_edge = max(height, width)
    if longest_edge <= max_dimension:
        return image

    scale = max_dimension / float(longest_edge)
    target_size = (max(int(width * scale), 1), max(int(height * scale), 1))
    return cv2.resize(image, target_size, interpolation=cv2.INTER_AREA)


def save_base64_image(data_url, enrollment_number, student_name):
    frame = decode_base64_image(data_url)
    if frame is None:
        return None

    file_name = f"{sanitize_filename(enrollment_number)}_{sanitize_filename(student_name)}.jpg"
    image_path = KNOWN_FACES_DIR / file_name
    cv2.imwrite(str(image_path), frame)
    return str(image_path)


def decode_uploaded_image(uploaded_file):
    if uploaded_file is None:
        return None, "No image file was provided."

    file_name = str(uploaded_file.filename or "").strip()
    if not file_name:
        return None, "No image file was selected."

    suffix = Path(file_name).suffix.lower()
    if suffix not in SUPPORTED_IMAGE_SUFFIXES:
        return None, "Upload a JPG or PNG image for the student's face."

    try:
        image_bytes = uploaded_file.read()
    except OSError:
        return None, "The uploaded image could not be read."

    if not image_bytes:
        return None, "The uploaded image is empty."

    if len(image_bytes) > MAX_STUDENT_IMAGE_BYTES:
        return None, "The uploaded image is too large. Keep it under 5 MB."

    image_array = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
    if frame is None:
        return None, "The uploaded image format is not supported."

    return frame, None


def count_faces_in_frame(frame):
    if frame is None:
        return 0

    if face_recognition is not None:
        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            return len(face_recognition.face_locations(rgb_frame, model="hog"))
        except Exception as error:
            log_message("face-count", error)

    return len(detect_faces(frame))


def save_uploaded_student_image(uploaded_file, enrollment_number, student_name):
    frame, decode_error = decode_uploaded_image(uploaded_file)
    if decode_error:
        return None, decode_error

    face_count = count_faces_in_frame(frame)
    if face_count == 0:
        return None, "No face was detected in the uploaded image. Upload a clear front-facing photo."
    if face_count > 1:
        return None, "Multiple faces were detected in the uploaded image. Upload a photo with only this student."

    face_region, _, found_face = locate_primary_face(frame)
    if not found_face or face_region is None or face_region.size == 0:
        return None, "The uploaded face could not be isolated clearly. Try a brighter, front-facing photo."

    normalized_face = resize_image_min_dimension(face_region, min_dimension=220)
    image_path = KNOWN_FACES_DIR / (
        f"{sanitize_filename(enrollment_number)}_{sanitize_filename(student_name)}.jpg"
    )
    cv2.imwrite(str(image_path), normalized_face if normalized_face is not None else face_region)
    return str(image_path), None


def delete_student_image(image_path):
    if not image_path:
        return

    try:
        target_path = Path(image_path).resolve()
        known_faces_dir = KNOWN_FACES_DIR.resolve()
        if known_faces_dir in target_path.parents and target_path.exists():
            target_path.unlink()
    except OSError as error:
        log_message("student-image-delete", error)


def is_legacy_student(student):
    return str(student.get("enrollment_number", "")).upper().startswith("LEGACY-")


def first_name_token(name):
    return str(name or "").strip().split(" ", 1)[0].lower()


def face_distance_between_images(first_image_path, second_image_path):
    if face_recognition is None:
        return None

    try:
        first_image = face_recognition.load_image_file(first_image_path)
        second_image = face_recognition.load_image_file(second_image_path)
        first_locations = face_recognition.face_locations(first_image)
        second_locations = face_recognition.face_locations(second_image)
        first_encodings = face_recognition.face_encodings(first_image, first_locations)
        second_encodings = face_recognition.face_encodings(second_image, second_locations)
        if not first_encodings or not second_encodings:
            return None
        return float(face_recognition.face_distance([first_encodings[0]], second_encodings[0])[0])
    except Exception as error:
        log_message("face-distance", error)
        return None


def generate_encoding_variants(rgb_image):
    yield rgb_image
    yield cv2.flip(rgb_image, 1)
    yield cv2.convertScaleAbs(rgb_image, alpha=1.08, beta=8)
    yield cv2.convertScaleAbs(rgb_image, alpha=0.82, beta=-6)

    gray = cv2.cvtColor(rgb_image, cv2.COLOR_RGB2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    enhanced_gray = clahe.apply(gray)
    yield cv2.cvtColor(enhanced_gray, cv2.COLOR_GRAY2RGB)


def build_face_recognition_encodings(image_path):
    if face_recognition is None:
        return []

    all_encodings = []
    try:
        rgb_image = np.ascontiguousarray(face_recognition.load_image_file(image_path))
        for variant in generate_encoding_variants(rgb_image):
            safe_variant = np.ascontiguousarray(variant, dtype=np.uint8)
            locations = face_recognition.face_locations(safe_variant)
            variant_encodings = face_recognition.face_encodings(safe_variant, locations)
            if variant_encodings:
                all_encodings.append(variant_encodings[0])
    except Exception as error:
        log_message(f"encoding-build-{Path(image_path).name}", error)

    return all_encodings


def is_allowed_student_media_path(path_value):
    if not path_value:
        return False

    try:
        target_path = Path(path_value).resolve()
    except OSError:
        return False

    allowed_directories = (KNOWN_FACES_DIR.resolve(), PROOF_SNAPSHOTS_DIR.resolve())
    return any(directory in target_path.parents for directory in allowed_directories)


def get_engine_sample_image():
    for student_name, image_path in known_faces.items():
        candidates = collect_candidate_images_for_student(student_name, image_path)
        if candidates:
            return candidates[0]
    return ""


def resolve_preferred_student_image(name, image_path):
    candidates = collect_candidate_images_for_student(name, image_path)
    return candidates[0] if candidates else ""


def collect_candidate_images_for_student(name, image_path):
    candidates = []
    seen = set()
    image_stem = sanitize_filename(Path(str(image_path or "")).stem).replace("-", "_")
    enrollment_hint = image_stem.split("_", 1)[0] if image_stem else ""

    def add_candidate(path_value):
        if not path_value:
            return
        resolved = str(Path(path_value).resolve())
        if resolved in seen:
            return
        if Path(resolved).exists():
            seen.add(resolved)
            candidates.append(resolved)

    add_candidate(image_path)
    if candidates:
        return candidates

    normalized_name = sanitize_filename(name).replace("-", "_")
    first_name = sanitize_filename(first_name_token(name)).replace("-", "_")

    for directory in (KNOWN_FACES_DIR, PROOF_SNAPSHOTS_DIR):
        for file_path in directory.iterdir():
            if file_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
                continue

            stem = sanitize_filename(file_path.stem).replace("-", "_")
            if enrollment_hint and stem.startswith(enrollment_hint):
                add_candidate(file_path)
                continue

            if normalized_name and normalized_name in stem:
                add_candidate(file_path)
                continue

            if len(all_students) == 1 and first_name and first_name in stem:
                add_candidate(file_path)

    if len(all_students) == 1:
        for directory in (KNOWN_FACES_DIR, PROOF_SNAPSHOTS_DIR):
            for file_path in directory.iterdir():
                if file_path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES:
                    add_candidate(file_path)

    def candidate_priority(path_value):
        path = Path(path_value)
        priority = 0 if KNOWN_FACES_DIR.resolve() in path.parents else 1
        try:
            timestamp = -path.stat().st_mtime
        except OSError:
            timestamp = 0
        return (priority, timestamp, len(path.name))

    candidates.sort(key=candidate_priority)
    if len(candidates) > MAX_CANDIDATE_IMAGES_PER_STUDENT:
        candidates = candidates[:MAX_CANDIDATE_IMAGES_PER_STUDENT]

    return candidates


def build_live_face_encodings(frame, face_location=None):
    if face_recognition is None or frame is None:
        return []

    live_encodings = []
    try:
        rgb_frame = np.ascontiguousarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), dtype=np.uint8)

        if face_location is not None:
            face_crop = extract_face_region(frame, face_location, padding=20)
            if face_crop is not None and face_crop.size:
                rgb_face_crop = np.ascontiguousarray(
                    cv2.cvtColor(face_crop, cv2.COLOR_BGR2RGB),
                    dtype=np.uint8,
                )
                variant_encodings = face_recognition.face_encodings(rgb_face_crop)
                if variant_encodings:
                    live_encodings.extend(variant_encodings)

        if not live_encodings:
            frame_locations = None
            if face_location is not None:
                top, right, bottom, left = face_location
                frame_locations = [(int(top), int(right), int(bottom), int(left))]
            else:
                frame_locations = face_recognition.face_locations(rgb_frame, model="hog")

            if frame_locations:
                variant_encodings = face_recognition.face_encodings(
                    rgb_frame,
                    frame_locations,
                )
                if variant_encodings:
                    live_encodings.extend(variant_encodings)
    except Exception as error:
        log_message("live-encoding-build", error)

    return live_encodings


def reconcile_legacy_students():
    legacy_students = get_legacy_students()
    if not legacy_students:
        return

    all_students_data = get_all_students()
    registered_students = [student for student in all_students_data if not is_legacy_student(student)]

    for legacy_student in legacy_students:
        for registered_student in registered_students:
            if first_name_token(legacy_student["name"]) != first_name_token(registered_student["name"]):
                continue

            distance = face_distance_between_images(
                legacy_student["image_path"],
                registered_student["image_path"],
            )
            if distance is None or distance > 0.45:
                continue

            reassign_attendance_to_student(legacy_student, registered_student)
            delete_student(legacy_student["id"])
            log_message(
                "student-merge",
                f"Merged legacy student {legacy_student['name']} into {registered_student['name']} (distance={distance:.3f})",
            )
            break


def sync_legacy_known_faces():
    existing_students = get_all_students()

    for file_path in KNOWN_FACES_DIR.iterdir():
        if file_path.suffix.lower() not in SUPPORTED_IMAGE_SUFFIXES:
            continue
        person_name = file_path.stem.split("_", 1)[-1].replace("_", " ").strip().title()
        if any(
            not is_legacy_student(student)
            and first_name_token(student["name"]) == first_name_token(person_name)
            for student in existing_students
        ):
            continue
        register_known_face_seed(person_name, str(file_path))


def load_known_faces():
    return get_known_face_records()


def make_engine_state():
    sample_image = get_engine_sample_image()
    detector_backend = DETECTOR_BACKEND if face_cascade is not None else "skip"
    return {
        "deepface_imported": DeepFace is not None,
        "deepface_import_error": sanitize_text(DEEPFACE_IMPORT_ERROR),
        "fallback_imported": face_recognition is not None,
        "fallback_import_error": sanitize_text(FACE_RECOGNITION_IMPORT_ERROR),
        "emotion_ready": False,
        "recognition_ready": False,
        "emotion_error": "",
        "recognition_error": "",
        "startup_message": "",
        "sample_image": sample_image,
        "weights_dir": str(WEIGHTS_DIR),
        "recognition_model": RECOGNITION_MODEL_NAME,
        "recognition_backend": "deepface",
        "detector_backend": detector_backend,
        "face_detector_ready": face_cascade is not None,
        "face_detector_path": FACE_CASCADE_PATH,
        "host": DEFAULT_HOST,
        "port": DEFAULT_PORT,
    }


def friendly_engine_error(error):
    message = sanitize_text(error)
    if "Consider downloading it manually to" in message:
        model_url = MODEL_DOWNLOAD_HINTS.get(RECOGNITION_MODEL_NAME, "")
        return (
            f"Face recognition model weights for {RECOGNITION_MODEL_NAME} are missing or could not be downloaded. "
            f"Place the required model files in {WEIGHTS_DIR} and restart the app. "
            f"Model source: {model_url}. Original error: {message}"
        )
    if "OOM when allocating tensor" in message:
        return (
            f"The {RECOGNITION_MODEL_NAME} recognition model ran out of memory while loading. "
            "Close other heavy apps or choose a lighter model before restarting. "
            f"Original error: {message}"
        )
    return message


def bootstrap_engines():
    global KNOWN_FACE_ENCODINGS
    state = make_engine_state()
    KNOWN_FACE_ENCODINGS = {}

    if DeepFace is None:
        state["startup_message"] = "DeepFace could not be imported. Install the required packages and restart."
        state["emotion_error"] = state["deepface_import_error"]
        state["recognition_error"] = state["deepface_import_error"]
        return state

    if not state["sample_image"]:
        state["startup_message"] = "Register at least one student to bootstrap face recognition."
        state["recognition_error"] = "No registered student image is available yet."
        return state

    def try_bootstrap_emotion():
        DeepFace.analyze(
            img_path=state["sample_image"],
            actions=["emotion"],
            enforce_detection=False,
            detector_backend=state["detector_backend"],
            silent=True,
        )

    try:
        try_bootstrap_emotion()
        state["emotion_ready"] = True
    except Exception as error:
        raw_message = sanitize_text(error)
        if "interruption during the download" in raw_message:
            weight_file = WEIGHTS_DIR / "facial_expression_model_weights.h5"
            if weight_file.exists():
                try:
                    weight_file.unlink()
                    try_bootstrap_emotion()
                    state["emotion_ready"] = True
                except Exception as retry_error:
                    state["emotion_error"] = friendly_engine_error(retry_error)
                    log_message("emotion-bootstrap", retry_error)
            else:
                state["emotion_error"] = friendly_engine_error(error)
                log_message("emotion-bootstrap", error)
        else:
            state["emotion_error"] = friendly_engine_error(error)
            log_message("emotion-bootstrap", error)

    try:
        DeepFace.verify(
            img1_path=state["sample_image"],
            img2_path=state["sample_image"],
            model_name=RECOGNITION_MODEL_NAME,
            enforce_detection=False,
            detector_backend=state["detector_backend"],
            silent=True,
        )
        state["recognition_ready"] = True
    except Exception as error:
        state["recognition_error"] = friendly_engine_error(error)
        log_message("recognition-bootstrap", error)
        if face_recognition is not None:
            fallback_encodings = {}
            for name, image_path in known_faces.items():
                try:
                    encodings = []
                    for candidate_image in collect_candidate_images_for_student(name, image_path):
                        encodings.extend(build_face_recognition_encodings(candidate_image))
                    if encodings:
                        fallback_encodings[name] = encodings
                except Exception as fallback_error:
                    log_message(f"fallback-encoding-{name}", fallback_error)

            if fallback_encodings:
                KNOWN_FACE_ENCODINGS = fallback_encodings
                state["recognition_ready"] = True
                state["recognition_error"] = ""
                state["recognition_backend"] = "face_recognition"
                state["recognition_model"] = "face_recognition"
        elif FACE_RECOGNITION_IMPORT_ERROR:
            state["recognition_error"] = (
                f"{state['recognition_error']} Fallback recognizer error: "
                f"{sanitize_text(FACE_RECOGNITION_IMPORT_ERROR)}"
            ).strip()

    if face_recognition is not None:
        fallback_encodings = {}
        for name, image_path in known_faces.items():
            try:
                encodings = []
                for candidate_image in collect_candidate_images_for_student(name, image_path):
                    encodings.extend(build_face_recognition_encodings(candidate_image))
                if encodings:
                    fallback_encodings[name] = encodings
            except Exception as fallback_error:
                log_message(f"fallback-encoding-{name}", fallback_error)

        if fallback_encodings:
            KNOWN_FACE_ENCODINGS = fallback_encodings
            state["recognition_ready"] = True
            state["recognition_error"] = ""
            state["recognition_backend"] = "face_recognition"
            state["recognition_model"] = "face_recognition"

    if state["recognition_ready"] and state["emotion_ready"]:
        state["startup_message"] = "Face recognition and emotion analysis are ready."
    elif state["emotion_ready"]:
        state["startup_message"] = "Emotion analysis is ready. Face recognition still needs setup."
    elif state["recognition_ready"]:
        state["startup_message"] = "Face recognition is ready. Emotion analysis still needs attention."
    else:
        state["startup_message"] = "AI engines are not fully ready. Check the status on the page."

    return state


def ensure_emotion_engine_ready(force=False):
    global ENGINE_STATE

    if DeepFace is None:
        return False

    sample_image = get_engine_sample_image() or ENGINE_STATE.get("sample_image", "")
    if not sample_image:
        return False

    if not force and ENGINE_STATE.get("emotion_ready"):
        return True

    detector_backend = ENGINE_STATE.get("detector_backend") or DETECTOR_BACKEND
    try:
        DeepFace.analyze(
            img_path=sample_image,
            actions=["emotion"],
            enforce_detection=False,
            detector_backend=detector_backend,
            silent=True,
        )
    except Exception as error:
        ENGINE_STATE["emotion_ready"] = False
        ENGINE_STATE["emotion_error"] = friendly_engine_error(error)
        if not ENGINE_STATE.get("recognition_ready"):
            ENGINE_STATE["startup_message"] = "AI engines are not fully ready. Check the status on the page."
        elif not ENGINE_STATE.get("emotion_error"):
            ENGINE_STATE["startup_message"] = "Face recognition is ready. Emotion analysis still needs attention."
        log_message("emotion-ensure", error)
        return False

    ENGINE_STATE["emotion_ready"] = True
    ENGINE_STATE["emotion_error"] = ""
    if ENGINE_STATE.get("recognition_ready"):
        ENGINE_STATE["startup_message"] = "Face recognition and emotion analysis are ready."
    else:
        ENGINE_STATE["startup_message"] = "Emotion analysis is ready. Face recognition still needs setup."
    return True


def ensure_fallback_recognition_ready(force=False):
    global KNOWN_FACE_ENCODINGS, ENGINE_STATE

    if face_recognition is None or not known_faces:
        return False

    if (
        not force
        and ENGINE_STATE.get("recognition_ready")
        and ENGINE_STATE.get("recognition_backend") == "face_recognition"
        and KNOWN_FACE_ENCODINGS
    ):
        return True

    fallback_encodings = {}
    for name, image_path in known_faces.items():
        try:
            encodings = []
            for candidate_image in collect_candidate_images_for_student(name, image_path):
                encodings.extend(build_face_recognition_encodings(candidate_image))
            if encodings:
                fallback_encodings[name] = encodings
        except Exception as fallback_error:
            log_message(f"fallback-encoding-{name}", fallback_error)

    if not fallback_encodings:
        return False

    KNOWN_FACE_ENCODINGS = fallback_encodings
    ENGINE_STATE["recognition_ready"] = True
    ENGINE_STATE["recognition_error"] = ""
    ENGINE_STATE["recognition_backend"] = "face_recognition"
    ENGINE_STATE["recognition_model"] = "face_recognition"
    if ENGINE_STATE.get("emotion_ready"):
        ENGINE_STATE["startup_message"] = "Face recognition and emotion analysis are ready."
    else:
        ENGINE_STATE["startup_message"] = "Face recognition is ready. Emotion analysis still needs attention."
    return True


def refresh_runtime_state(rebuild_engines=False):
    global known_faces, all_students, ENGINE_STATE
    known_faces = load_known_faces()
    all_students = list(known_faces.keys())
    if rebuild_engines:
        ENGINE_STATE = bootstrap_engines()
    elif not ENGINE_STATE:
        ENGINE_STATE = make_engine_state()
        ENGINE_STATE["startup_message"] = "AI engines are starting in the background. The website is ready."
    if rebuild_engines and known_faces and DeepFace is not None:
        ensure_emotion_engine_ready(force=not ENGINE_STATE.get("emotion_ready"))
    if (
        rebuild_engines
        and known_faces
        and face_recognition is not None
        and not ENGINE_STATE.get("recognition_ready")
    ):
        ensure_fallback_recognition_ready(force=True)


def _background_bootstrap_worker():
    global ENGINE_BOOTSTRAP_STARTED
    try:
        try:
            reconcile_legacy_students()
        except Exception as error:
            log_message("legacy-reconcile", error)
        refresh_runtime_state(rebuild_engines=True)
    finally:
        with ENGINE_BOOTSTRAP_LOCK:
            ENGINE_BOOTSTRAP_STARTED = False


def start_background_engine_bootstrap(force=False):
    global ENGINE_BOOTSTRAP_STARTED

    with ENGINE_BOOTSTRAP_LOCK:
        if ENGINE_BOOTSTRAP_STARTED and not force:
            return
        ENGINE_BOOTSTRAP_STARTED = True

    worker = threading.Thread(target=_background_bootstrap_worker, daemon=True)
    worker.start()


def detect_faces(frame):
    if frame is None:
        return []

    if face_cascade is None:
        if face_recognition is None:
            return []

        try:
            rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            locations = face_recognition.face_locations(rgb_frame)
            return [(left, top, right - left, bottom - top) for top, right, bottom, left in locations]
        except Exception as error:
            log_message("face-detection-fallback", error)
            return []

    variants = [frame]
    enhanced_variant = enhance_frame_for_detection(frame)
    if enhanced_variant is not None:
        variants.append(enhanced_variant)

    best_faces = []
    best_area = 0
    for variant in variants:
        gray = cv2.cvtColor(variant, cv2.COLOR_BGR2GRAY)
        for scale_factor, min_neighbors, min_size in (
            (1.1, 4, (60, 60)),
            (1.15, 5, (72, 72)),
            (1.2, 5, (80, 80)),
        ):
            faces = face_cascade.detectMultiScale(
                gray,
                scaleFactor=scale_factor,
                minNeighbors=min_neighbors,
                minSize=min_size,
            )
            if len(faces):
                largest_area = max(int(w) * int(h) for (_, _, w, h) in faces)
                if largest_area > best_area:
                    best_faces = faces
                    best_area = largest_area

    return best_faces


def clamp_face_box(frame, left, top, right, bottom, padding=24):
    frame_height, frame_width = frame.shape[:2]
    left = max(int(left) - padding, 0)
    top = max(int(top) - padding, 0)
    right = min(int(right) + padding, frame_width)
    bottom = min(int(bottom) + padding, frame_height)
    return left, top, right, bottom


def locate_primary_face(frame):
    if frame is None:
        return None, None, False

    locations = get_face_locations(frame)
    if not locations:
        return None, None, False

    top, right, bottom, left = locations[0]
    left, top, right, bottom = clamp_face_box(frame, left, top, right, bottom)
    return frame[top:bottom, left:right], (top, right, bottom, left), True


def get_face_locations(frame):
    if frame is None:
        return []

    variants = [frame]
    enhanced_variant = enhance_frame_for_detection(frame)
    if enhanced_variant is not None:
        variants.append(enhanced_variant)

    if face_recognition is not None:
        for variant in variants:
            try:
                rgb_frame = cv2.cvtColor(variant, cv2.COLOR_BGR2RGB)
                locations = face_recognition.face_locations(rgb_frame, model="hog")
                if locations:
                    return sorted(
                        locations,
                        key=lambda item: (item[2] - item[0]) * (item[1] - item[3]),
                        reverse=True,
                    )
            except Exception as error:
                log_message("face-location-all", error)

    faces = detect_faces(frame)
    normalized_locations = []
    for x, y, w, h in faces:
        normalized_locations.append((int(y), int(x + w), int(y + h), int(x)))

    return sorted(
        normalized_locations,
        key=lambda item: (item[2] - item[0]) * (item[1] - item[3]),
        reverse=True,
    )


def locate_faces(frame, max_faces=5):
    locations = get_face_locations(frame)
    if not locations:
        return []

    faces = []
    for face_location in locations[:max_faces]:
        face_img = extract_face_region(frame, face_location, padding=24)
        if face_img is None or not face_img.size:
            continue
        faces.append(
            {
                "face_img": face_img,
                "face_location": face_location,
            }
        )

    return faces


def extract_face_region(frame, face_location, padding=40):
    if frame is None or face_location is None:
        return None

    top, right, bottom, left = face_location
    left, top, right, bottom = clamp_face_box(frame, left, top, right, bottom, padding=padding)
    region = frame[top:bottom, left:right]
    return region if region.size else None


def normalize_face_gray(face_img, size=(144, 144)):
    if face_img is None or face_img.size == 0:
        return None

    gray_face = cv2.cvtColor(face_img, cv2.COLOR_BGR2GRAY)
    normalized = cv2.resize(gray_face, size, interpolation=cv2.INTER_AREA)
    return cv2.equalizeHist(normalized)


def average_frame_motion(first_face_img, second_face_img):
    first_gray = normalize_face_gray(first_face_img)
    second_gray = normalize_face_gray(second_face_img)
    if first_gray is None or second_gray is None:
        return 0.0

    return float(np.mean(cv2.absdiff(first_gray, second_gray)))


def calculate_face_texture_metrics(face_img):
    gray_face = normalize_face_gray(face_img)
    if gray_face is None:
        return {
            "texture": 0.0,
            "edge_density": 0.0,
            "saturated_ratio": 0.0,
        }

    laplacian_variance = float(cv2.Laplacian(gray_face, cv2.CV_64F).var())
    edges = cv2.Canny(gray_face, 60, 140)
    edge_density = float(np.mean(edges > 0))
    saturated_ratio = float(np.mean(gray_face >= 245))
    return {
        "texture": laplacian_variance,
        "edge_density": edge_density,
        "saturated_ratio": saturated_ratio,
    }


def average_point(points):
    if not points:
        return None
    x_values = [float(point[0]) for point in points]
    y_values = [float(point[1]) for point in points]
    return (sum(x_values) / float(len(x_values)), sum(y_values) / float(len(y_values)))


def point_distance(first_point, second_point):
    if first_point is None or second_point is None:
        return 0.0
    return float(np.hypot(first_point[0] - second_point[0], first_point[1] - second_point[1]))


def normalize_point_in_box(point, face_location):
    if point is None or face_location is None:
        return None

    top, right, bottom, left = face_location
    width = max(float(right - left), 1.0)
    height = max(float(bottom - top), 1.0)
    return (
        (float(point[0]) - float(left)) / width,
        (float(point[1]) - float(top)) / height,
    )


def build_landmark_signature(frame, face_location=None):
    if face_recognition is None or frame is None:
        return None

    try:
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        search_locations = [face_location] if face_location is not None else None
        landmarks = face_recognition.face_landmarks(rgb_frame, search_locations)
    except Exception as error:
        log_message("anti-spoof-landmarks", error)
        return None

    if not landmarks:
        return None

    face_landmarks = landmarks[0]
    left_eye = face_landmarks.get("left_eye", [])
    right_eye = face_landmarks.get("right_eye", [])
    nose_tip = face_landmarks.get("nose_tip", [])
    top_lip = face_landmarks.get("top_lip", [])
    chin = face_landmarks.get("chin", [])

    left_eye_center = average_point(left_eye)
    right_eye_center = average_point(right_eye)
    nose_center = average_point(nose_tip)
    mouth_center = average_point(top_lip)
    chin_center = average_point(chin)

    signature = {
        "left_eye_center": normalize_point_in_box(left_eye_center, face_location),
        "right_eye_center": normalize_point_in_box(right_eye_center, face_location),
        "nose_center": normalize_point_in_box(nose_center, face_location),
        "mouth_center": normalize_point_in_box(mouth_center, face_location),
        "chin_center": normalize_point_in_box(chin_center, face_location),
    }
    return signature


def landmark_signature_delta(first_signature, second_signature):
    if not first_signature or not second_signature:
        return 0.0

    tracked_keys = (
        "left_eye_center",
        "right_eye_center",
        "nose_center",
        "mouth_center",
        "chin_center",
    )
    deltas = []
    for key in tracked_keys:
        first_point = first_signature.get(key)
        second_point = second_signature.get(key)
        if first_point is None or second_point is None:
            continue
        deltas.append(
            float(np.hypot(first_point[0] - second_point[0], first_point[1] - second_point[1]))
        )
    return float(max(deltas)) if deltas else 0.0


def assess_liveness(frames):
    default_result = {
        "label": "Unknown",
        "is_live": True,
        "is_spoof": False,
        "message": "",
        "metrics": {},
    }
    if not frames:
        return default_result

    frame_faces = []
    landmark_signatures = []
    face_centers = []
    face_sizes = []
    texture_scores = []
    edge_densities = []
    saturated_ratios = []

    for frame in frames:
        face_location = get_face_locations(frame)
        if len(face_location) != 1:
            continue

        primary_location = face_location[0]
        face_img = extract_face_region(frame, primary_location, padding=24)
        if face_img is None or not face_img.size:
            continue

        frame_faces.append(face_img)
        landmark_signatures.append(build_landmark_signature(frame, primary_location))
        top, right, bottom, left = primary_location
        face_centers.append(((left + right) / 2.0, (top + bottom) / 2.0))
        face_sizes.append((max(float(right - left), 1.0), max(float(bottom - top), 1.0)))
        texture_metrics = calculate_face_texture_metrics(face_img)
        texture_scores.append(texture_metrics["texture"])
        edge_densities.append(texture_metrics["edge_density"])
        saturated_ratios.append(texture_metrics["saturated_ratio"])

    valid_frames = len(frame_faces)
    if valid_frames < ANTI_SPOOF_MIN_FRAMES:
        return {
            **default_result,
            "label": "Unknown",
            "is_live": True,
            "is_spoof": False,
            "message": "Show your real face clearly and hold steady for a moment.",
            "metrics": {"valid_frames": valid_frames},
        }

    motion_scores = [
        average_frame_motion(frame_faces[index - 1], frame_faces[index])
        for index in range(1, valid_frames)
    ]
    landmark_deltas = [
        landmark_signature_delta(landmark_signatures[index - 1], landmark_signatures[index])
        for index in range(1, valid_frames)
    ]

    average_motion = float(sum(motion_scores) / float(len(motion_scores))) if motion_scores else 0.0
    max_landmark_delta = float(max(landmark_deltas)) if landmark_deltas else 0.0
    average_texture = float(sum(texture_scores) / float(len(texture_scores))) if texture_scores else 0.0
    average_edge_density = float(sum(edge_densities) / float(len(edge_densities))) if edge_densities else 0.0
    average_saturated_ratio = float(sum(saturated_ratios) / float(len(saturated_ratios))) if saturated_ratios else 0.0
    normalized_face_shifts = []
    for index in range(1, len(face_centers)):
        prev_center = face_centers[index - 1]
        curr_center = face_centers[index]
        prev_size = face_sizes[index - 1]
        avg_face_size = max((prev_size[0] + prev_size[1]) / 2.0, 1.0)
        normalized_face_shifts.append(point_distance(prev_center, curr_center) / avg_face_size)

    average_face_shift = (
        float(sum(normalized_face_shifts) / float(len(normalized_face_shifts)))
        if normalized_face_shifts
        else 0.0
    )
    max_face_shift = float(max(normalized_face_shifts)) if normalized_face_shifts else 0.0
    natural_face_motion = (
        (
            average_face_shift >= ANTI_SPOOF_FACE_SHIFT_THRESHOLD
            and max_landmark_delta >= ANTI_SPOOF_POSE_DELTA_THRESHOLD
        )
        or average_motion >= max(ANTI_SPOOF_MOTION_THRESHOLD * 0.8, 1.0)
        or max_landmark_delta >= max(ANTI_SPOOF_POSE_DELTA_THRESHOLD * 1.35, 0.005)
    )

    likely_spoof = (
        (
            average_motion < 0.8
            and average_face_shift < 0.012
            and max_landmark_delta < 0.005
        )
        or (
            average_motion < ANTI_SPOOF_MOTION_THRESHOLD
            and average_face_shift < ANTI_SPOOF_FACE_SHIFT_THRESHOLD
            and max_landmark_delta < ANTI_SPOOF_POSE_DELTA_THRESHOLD
            and (
                average_texture < ANTI_SPOOF_MIN_TEXTURE
                or average_edge_density < ANTI_SPOOF_MAX_EDGE_DENSITY
                or average_saturated_ratio > ANTI_SPOOF_MAX_SATURATED_RATIO
            )
        )
    )
    if (
        not natural_face_motion
        and average_motion < max(ANTI_SPOOF_MOTION_THRESHOLD * 0.75, 1.0)
        and average_texture < max(ANTI_SPOOF_MIN_TEXTURE * 0.9, 14.0)
        and average_edge_density < max(ANTI_SPOOF_MAX_EDGE_DENSITY, 0.02)
    ):
        likely_spoof = True

    metrics = {
        "valid_frames": valid_frames,
        "average_motion": round(average_motion, 4),
        "max_landmark_delta": round(max_landmark_delta, 5),
        "average_face_shift": round(average_face_shift, 5),
        "max_face_shift": round(max_face_shift, 5),
        "average_texture": round(average_texture, 2),
        "average_edge_density": round(average_edge_density, 5),
        "average_saturated_ratio": round(average_saturated_ratio, 5),
        "natural_face_motion": bool(natural_face_motion),
    }

    if likely_spoof:
        return {
            "label": "Spoof",
            "is_live": False,
            "is_spoof": True,
            "message": "Spoof attempt detected. Move your real face slightly left or right during capture. Attendance was blocked.",
            "metrics": metrics,
        }

    return {
        "label": "Real Face" if natural_face_motion or average_texture >= ANTI_SPOOF_MIN_TEXTURE else "Likely Real Face",
        "is_live": True,
        "is_spoof": False,
        "message": "Real face verified.",
        "metrics": metrics,
    }


def resize_image_min_dimension(image, min_dimension=160):
    if image is None or image.size == 0:
        return None

    height, width = image.shape[:2]
    shortest_edge = min(height, width)
    if shortest_edge >= min_dimension:
        return image

    scale = min_dimension / float(shortest_edge)
    target_size = (max(int(width * scale), 1), max(int(height * scale), 1))
    return cv2.resize(image, target_size, interpolation=cv2.INTER_CUBIC)


def enhance_emotion_contrast(image):
    if image is None or image.size == 0:
        return None

    resized = resize_image_min_dimension(image)
    ycrcb = cv2.cvtColor(resized, cv2.COLOR_BGR2YCrCb)
    y_channel, cr_channel, cb_channel = cv2.split(ycrcb)
    y_channel = cv2.equalizeHist(y_channel)
    return cv2.cvtColor(
        cv2.merge((y_channel, cr_channel, cb_channel)),
        cv2.COLOR_YCrCb2BGR,
    )


def collect_emotion_candidates(frame, face_img=None, face_location=None):
    candidates = []
    seen_shapes = set()

    def add_candidate(image, detector_backend):
        if image is None or not image.size:
            return

        image = resize_image_max_dimension(image)
        if image is None or not image.size:
            return

        shape_key = (int(image.shape[0]), int(image.shape[1]), detector_backend)
        if shape_key in seen_shapes:
            return

        seen_shapes.add(shape_key)
        candidates.append((image, detector_backend))

    padded_face = extract_face_region(frame, face_location, padding=52)
    if padded_face is not None:
        add_candidate(resize_image_min_dimension(padded_face), "skip")

    if face_img is not None and face_img.size:
        resized_face = resize_image_min_dimension(face_img)
        add_candidate(resized_face, "skip")

    # Include enhanced variants as additional votes instead of using them only as a fallback.
    if padded_face is not None:
        enhanced_padded_face = enhance_emotion_contrast(padded_face)
        if enhanced_padded_face is not None:
            add_candidate(enhanced_padded_face, "skip")

    if face_img is not None and face_img.size:
        enhanced_face = enhance_emotion_contrast(face_img)
        if enhanced_face is not None:
            add_candidate(enhanced_face, "skip")

    if not candidates and frame is not None and frame.size:
        add_candidate(
            resize_frame_for_analysis(frame, max_width=480),
            ENGINE_STATE["detector_backend"],
        )

    return candidates[:EMOTION_CANDIDATE_LIMIT]


def extract_emotion_prediction(result):
    if isinstance(result, list) and result:
        result = result[0]

    if not isinstance(result, dict):
        return "Unknown", -1.0, {}

    dominant_emotion = str(result.get("dominant_emotion", "Unknown")).title()
    emotion_scores = result.get("emotion", {})
    confidence = -1.0

    if isinstance(emotion_scores, dict) and dominant_emotion:
        confidence = float(emotion_scores.get(dominant_emotion.lower(), -1.0))

    normalized_scores = {}
    if isinstance(emotion_scores, dict):
        for emotion_name, score in emotion_scores.items():
            normalized_scores[str(emotion_name).title()] = float(score)

    adjusted_scores = rebalance_emotion_score_map(normalized_scores)
    if adjusted_scores:
        dominant_emotion, confidence = max(
            adjusted_scores.items(),
            key=lambda item: (item[1], item[0]),
        )

    return dominant_emotion, confidence, adjusted_scores


def rebalance_emotion_score_map(score_map):
    if not isinstance(score_map, dict):
        return {}

    adjusted = {str(name).title(): float(score) for name, score in score_map.items()}
    if not adjusted:
        return adjusted

    adjusted["Happy"] = adjusted.get("Happy", 0.0) * 1.24
    adjusted["Neutral"] = adjusted.get("Neutral", 0.0) * 1.22
    adjusted["Sad"] = adjusted.get("Sad", 0.0) * 0.58
    adjusted["Surprise"] = adjusted.get("Surprise", 0.0) * 0.98
    adjusted["Angry"] = adjusted.get("Angry", 0.0) * 0.9
    adjusted["Fear"] = adjusted.get("Fear", 0.0) * 0.88
    adjusted["Disgust"] = adjusted.get("Disgust", 0.0) * 0.88

    happy_score = adjusted.get("Happy", 0.0)
    neutral_score = adjusted.get("Neutral", 0.0)
    sad_score = adjusted.get("Sad", 0.0)

    if happy_score >= max(sad_score - 6.0, 16.0):
        adjusted["Happy"] = happy_score * 1.08
    if neutral_score >= max(sad_score - 4.0, 14.0):
        adjusted["Neutral"] = neutral_score * 1.06
    if sad_score <= max(happy_score + 4.0, neutral_score + 3.0, 28.0):
        adjusted["Sad"] = sad_score * 0.72

    return adjusted


def adjusted_emotion_score(emotion_name, score):
    if emotion_name == "Happy":
        return score * 1.06
    if emotion_name == "Angry":
        return score * 0.92
    if emotion_name == "Fear":
        return score * 0.88
    if emotion_name == "Surprise":
        return score * 1.0
    if emotion_name == "Neutral":
        return score * 1.12
    if emotion_name == "Sad":
        return score * 0.68
    return score


def choose_final_emotion(emotion_votes, strongest_scores, top_emotion, top_confidence):
    if not emotion_votes:
        return top_emotion

    ranked_votes = sorted(
        emotion_votes.items(),
        key=lambda item: (item[1], strongest_scores.get(item[0], -1.0), item[0]),
        reverse=True,
    )
    aggregate_emotion, aggregate_score = ranked_votes[0]

    sad_vote = emotion_votes.get("Sad", 0.0)
    sad_peak = strongest_scores.get("Sad", -1.0)
    neutral_vote = emotion_votes.get("Neutral", 0.0)
    neutral_peak = strongest_scores.get("Neutral", -1.0)
    happy_vote = emotion_votes.get("Happy", 0.0)
    happy_peak = strongest_scores.get("Happy", -1.0)
    surprise_vote = emotion_votes.get("Surprise", 0.0)
    surprise_peak = strongest_scores.get("Surprise", -1.0)
    top_vote = emotion_votes.get(top_emotion, 0.0)

    if (
        top_emotion == "Surprise"
        and top_confidence >= 48.0
        and surprise_peak >= max(happy_peak + 10.0, neutral_peak + 12.0, sad_peak + 10.0, 35.0)
    ):
        return "Surprise"

    if (
        aggregate_emotion == "Surprise"
        and surprise_peak >= max(happy_peak + 8.0, neutral_peak + 10.0, sad_peak + 8.0, 32.0)
        and surprise_vote >= max(happy_vote, neutral_vote, sad_vote, 24.0)
    ):
        return "Surprise"

    if (
        top_emotion == "Happy"
        and top_confidence >= 52.0
        and happy_peak >= max(neutral_peak + 8.0, sad_peak + 8.0, surprise_peak + 6.0, 34.0)
    ):
        return "Happy"

    if (
        aggregate_emotion == "Happy"
        and happy_peak >= max(neutral_peak + 6.0, sad_peak + 6.0, surprise_peak + 6.0, 30.0)
        and happy_vote >= max(neutral_vote + 5.0, sad_vote + 4.0, surprise_vote + 4.0, 24.0)
    ):
        return "Happy"

    if (
        aggregate_emotion == "Sad"
        and sad_peak >= max(happy_peak + 14.0, neutral_peak + 12.0, surprise_peak + 10.0, 48.0)
        and sad_vote >= max(happy_vote + 12.0, neutral_vote + 10.0, 38.0)
    ):
        return "Sad"

    if sad_peak >= max(neutral_peak + 12.0, happy_peak + 10.0, 44.0):
        return "Sad"

    if (
        neutral_peak >= max(happy_peak - 4.0, sad_peak - 1.0, surprise_peak - 8.0, 20.0)
        and neutral_vote >= max(happy_vote - 2.0, sad_vote + 1.0, surprise_vote - 4.0, 18.0)
    ):
        return "Neutral"

    if (
        top_emotion in {"Neutral", "Happy", "Sad"}
        and top_confidence < 30.0
    ):
        if happy_peak >= max(neutral_peak + 4.0, sad_peak + 8.0, 24.0):
            return "Happy"
        return "Neutral"

    if top_vote > 0.0 and top_emotion in {"Happy", "Neutral", "Sad", "Surprise"}:
        if top_emotion == "Happy":
            if happy_peak >= max(neutral_peak + 10.0, sad_peak + 10.0, surprise_peak + 8.0, 40.0):
                return "Happy"
            return "Neutral"
        if top_emotion == "Neutral":
            return "Neutral"
        if top_emotion == "Sad":
            return "Sad" if sad_peak >= max(neutral_peak + 8.0, happy_peak + 8.0, 36.0) else "Neutral"
        if top_emotion == "Surprise":
            return "Surprise" if surprise_peak >= max(neutral_peak + 10.0, sad_peak + 8.0, 36.0) else "Neutral"

    if aggregate_emotion == "Happy":
        return "Happy" if happy_peak >= max(neutral_peak + 3.0, sad_peak + 8.0, 28.0) else "Neutral"
    if aggregate_emotion in {"Angry", "Fear", "Disgust"}:
        return "Neutral"
    return aggregate_emotion


def summarize_emotion_scores(score_maps):
    combined = {}
    for score_map in score_maps:
        for emotion_name, score in score_map.items():
            combined[emotion_name] = combined.get(emotion_name, 0.0) + float(score)

    if not score_maps:
        return {}

    return {
        emotion_name: score / float(len(score_maps))
        for emotion_name, score in combined.items()
    }


def top_two_emotions(score_map):
    ranked = sorted(
        score_map.items(),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    top_item = ranked[0] if ranked else ("Unknown", -1.0)
    second_item = ranked[1] if len(ranked) > 1 else ("Unknown", -1.0)
    return top_item, second_item


def confidence_weight(score_map):
    (top_name, top_score), (_, second_score) = top_two_emotions(score_map)
    if top_name == "Unknown":
        return 0.0
    margin = max(top_score - second_score, 0.0)
    return top_score + (margin * 0.35)


def finalize_emotion_from_score_maps(score_maps):
    if not score_maps:
        return "Unknown", {}

    averaged_scores = summarize_emotion_scores(score_maps)
    (top_name, top_score), (_, second_score) = top_two_emotions(averaged_scores)
    margin = top_score - second_score
    happy_score = averaged_scores.get("Happy", 0.0)
    neutral_score = averaged_scores.get("Neutral", 0.0)
    sad_score = averaged_scores.get("Sad", 0.0)
    surprise_score = averaged_scores.get("Surprise", 0.0)

    if top_score < 24.0:
        return "Neutral", averaged_scores

    if (
        surprise_score >= 42.0
        and surprise_score >= max(happy_score + 10.0, neutral_score + 12.0, sad_score + 10.0)
        and margin >= 8.0
    ):
        return "Surprise", averaged_scores

    if (
        happy_score >= 48.0
        and happy_score >= max(neutral_score + 8.0, sad_score + 8.0, surprise_score + 6.0)
        and margin >= 6.0
    ):
        return "Happy", averaged_scores

    if (
        sad_score >= 42.0
        and sad_score >= max(happy_score + 14.0, neutral_score + 12.0, surprise_score + 10.0)
        and margin >= 12.0
    ):
        return "Sad", averaged_scores

    if sad_score >= max(neutral_score + 12.0, happy_score + 10.0, 44.0):
        return "Sad", averaged_scores

    if neutral_score >= max(happy_score - 3.0, sad_score - 1.0, surprise_score - 8.0, 22.0):
        return "Neutral", averaged_scores

    if top_name in {"Happy", "Neutral", "Sad"} and margin < 6.0:
        if happy_score >= max(neutral_score + 4.0, sad_score + 8.0, 26.0):
            return "Happy", averaged_scores
        return "Neutral", averaged_scores

    if top_name == "Happy":
        if happy_score >= max(neutral_score + 12.0, sad_score + 12.0, surprise_score + 8.0, 50.0):
            return "Happy", averaged_scores
        return "Neutral", averaged_scores

    if top_name in {"Angry", "Fear", "Disgust"}:
        return "Neutral", averaged_scores

    return top_name, averaged_scores


def analyze_emotion(frame, face_img=None, face_location=None):
    if not ENGINE_STATE["emotion_ready"]:
        return "Unavailable", {}
    if frame is None and face_img is None:
        return "Unknown", {}

    best_emotion = "Unknown"
    best_confidence = -1.0
    emotion_votes = {}
    strongest_scores = {}
    score_maps = []

    for candidate_image, detector_backend in collect_emotion_candidates(
        frame,
        face_img=face_img,
        face_location=face_location,
    ):
        try:
            result = DeepFace.analyze(
                img_path=candidate_image,
                actions=["emotion"],
                enforce_detection=False,
                detector_backend=detector_backend,
                silent=True,
            )
        except Exception as error:
            log_message("emotion", error)
            continue

        emotion, confidence, score_map = extract_emotion_prediction(result)
        if confidence > best_confidence:
            best_emotion = emotion
            best_confidence = confidence
        if score_map:
            score_maps.append(score_map)

        for emotion_name, score in score_map.items():
            weighted_score = adjusted_emotion_score(emotion_name, score)
            emotion_votes[emotion_name] = emotion_votes.get(emotion_name, 0.0) + weighted_score
            strongest_scores[emotion_name] = max(
                strongest_scores.get(emotion_name, -1.0),
                score,
            )

        if (
            emotion == "Surprise"
            and confidence >= 55.0
            and confidence >= max(
                score_map.get("Happy", -1.0) + 8.0,
                score_map.get("Neutral", -1.0) + 10.0,
                score_map.get("Sad", -1.0) + 8.0,
            )
        ):
            return emotion, score_map

        if (
            emotion == "Happy"
            and confidence >= 62.0
            and confidence >= max(
                score_map.get("Neutral", -1.0) + 8.0,
                score_map.get("Sad", -1.0) + 8.0,
                score_map.get("Surprise", -1.0) + 6.0,
            )
        ):
            return emotion, score_map

        if (
            emotion == "Sad"
            and confidence >= 68.0
            and confidence >= score_map.get("Neutral", -1.0) + 10.0
            and confidence >= score_map.get("Happy", -1.0) + 10.0
        ):
            return emotion, score_map

        if (
            emotion == "Happy"
            and confidence >= 46.0
            and confidence >= max(
                score_map.get("Neutral", -1.0) + 4.0,
                score_map.get("Sad", -1.0) + 8.0,
            )
        ):
            return emotion, score_map

        if emotion == "Neutral" and confidence >= 34.0:
            return emotion, score_map

        if emotion not in {"Happy", "Sad", "Neutral", "Surprise"} and confidence >= 72.0:
            return emotion, score_map

    chosen_emotion = choose_final_emotion(
        emotion_votes,
        strongest_scores,
        best_emotion,
        best_confidence,
    )
    final_emotion, averaged_scores = finalize_emotion_from_score_maps(score_maps)
    if final_emotion != "Unknown":
        chosen_emotion = final_emotion

    return chosen_emotion, averaged_scores


def aggregate_batch_results(results, allow_attendance_mark=False):
    if not results:
        return {
            "name": "Unknown",
            "emotion": "Unknown",
            "face_detected": False,
            "recognition_meta": None,
            "attendance_marked": False,
        }

    face_detected = any(result["face_detected"] for result in results)
    max_detected_faces = max(int(result.get("detected_faces", 0)) for result in results)
    multiple_faces_detected = any(result.get("multiple_faces_detected") for result in results)

    name_scores = {}
    best_recognition_meta = None
    best_name = "Unknown"
    best_distance = float("inf")

    emotion_score_maps = []
    emotion_votes = {}

    for index, result in enumerate(results):
        name = result["name"]
        meta = result.get("recognition_meta") or {}
        if name != "Unknown":
            distance = meta.get("distance")
            certainty = 1.0
            if distance is not None:
                certainty = max(0.05, 1.0 - float(distance))
            name_scores[name] = name_scores.get(name, 0.0) + certainty
            if distance is not None and distance < best_distance:
                best_distance = distance
                best_name = name
                best_recognition_meta = meta
            elif best_name == "Unknown":
                best_name = name
                best_recognition_meta = meta

        emotion = result["emotion"]
        emotion_votes[emotion] = emotion_votes.get(emotion, 0.0) + 1.0
        score_map = result.get("emotion_scores") or {}
        if score_map:
            emotion_score_maps.append(score_map)

    final_name = "Unknown"
    if name_scores:
        final_name = max(name_scores.items(), key=lambda item: (item[1], item[0]))[0]
    elif any(result["name"] != "Unknown" for result in results):
        final_name = next(result["name"] for result in results if result["name"] != "Unknown")

    final_emotion, averaged_scores = finalize_emotion_from_score_maps(emotion_score_maps)
    if final_emotion == "Unknown" and emotion_votes:
        final_emotion = max(emotion_votes.items(), key=lambda item: (item[1], item[0]))[0]

    attendance_marked = False
    if (
        allow_attendance_mark
        and not multiple_faces_detected
        and final_name != "Unknown"
        and ENGINE_STATE["recognition_ready"]
    ):
        attendance_marked = mark_attendance(final_name, final_emotion)

    return {
        "name": final_name,
        "emotion": final_emotion,
        "emotion_scores": averaged_scores,
        "face_detected": face_detected,
        "detected_faces": max_detected_faces,
        "multiple_faces_detected": multiple_faces_detected,
        "recognition_meta": best_recognition_meta,
        "attendance_marked": attendance_marked,
    }


def recognize_face(
    frame,
    face_img=None,
    face_location=None,
    allow_single_student_relaxed_match=True,
):
    if not ENGINE_STATE["recognition_ready"] or not known_faces or frame is None:
        return "Unknown", None

    if ENGINE_STATE.get("recognition_backend") == "face_recognition":
        try:
            live_encodings = build_live_face_encodings(frame, face_location=face_location)
            if not live_encodings:
                return "Unknown", None

            best_match_name = "Unknown"
            best_distance = float("inf")

            for name, stored_encodings in KNOWN_FACE_ENCODINGS.items():
                if not stored_encodings:
                    continue
                candidate_distance = float("inf")
                for live_encoding in live_encodings:
                    distances = face_recognition.face_distance(stored_encodings, live_encoding)
                    candidate_distance = min(candidate_distance, float(np.min(distances)))

                if candidate_distance < best_distance:
                    best_distance = candidate_distance
                    best_match_name = name

            if best_distance <= FACE_RECOGNITION_THRESHOLD:
                return best_match_name, {
                    "distance": best_distance,
                    "threshold": FACE_RECOGNITION_THRESHOLD,
                }

            if (
                allow_single_student_relaxed_match
                and len(KNOWN_FACE_ENCODINGS) == 1
                and best_match_name != "Unknown"
                and best_distance <= SINGLE_STUDENT_THRESHOLD
            ):
                return best_match_name, {
                    "distance": best_distance,
                    "threshold": SINGLE_STUDENT_THRESHOLD,
                }
            return "Unknown", None
        except Exception as error:
            log_message("fallback-recognition", error)
            return "Unknown", None

    best_match_name = "Unknown"
    best_distance = float("inf")
    best_threshold = None

    for name, image_path in known_faces.items():
        try:
            verification = DeepFace.verify(
                img1_path=face_img,
                img2_path=image_path,
                model_name=RECOGNITION_MODEL_NAME,
                enforce_detection=False,
                detector_backend=ENGINE_STATE["detector_backend"],
                silent=True,
            )
        except Exception as error:
            log_message(f"verification-{name}", error)
            continue

        distance = float(verification.get("distance", 1.0))
        threshold = float(verification.get("threshold", 0.4))

        if verification.get("verified"):
            return name, {"distance": distance, "threshold": threshold}

        if distance < best_distance and distance <= threshold + 0.05:
            best_distance = distance
            best_match_name = name
            best_threshold = threshold

    if best_match_name == "Unknown":
        return "Unknown", None

    return best_match_name, {"distance": best_distance, "threshold": best_threshold}


def verify_logged_in_student_face(student, frames):
    if not student or not frames or face_recognition is None:
        return None

    ensure_fallback_recognition_ready()
    student_encodings = KNOWN_FACE_ENCODINGS.get(student["name"]) or []
    if not student_encodings:
        image_path = known_faces.get(student["name"]) or student.get("image_path")
        if image_path:
            student_encodings = build_face_recognition_encodings(image_path)
            if student_encodings:
                KNOWN_FACE_ENCODINGS[student["name"]] = student_encodings

    if not student_encodings:
        return None

    best_distance = float("inf")
    try:
        for frame in frames:
            face_locations = get_face_locations(frame)
            live_encodings = build_live_face_encodings(
                frame,
                face_location=face_locations[0] if face_locations else None,
            )
            for live_encoding in live_encodings:
                distances = face_recognition.face_distance(student_encodings, live_encoding)
                if len(distances):
                    best_distance = min(best_distance, float(np.min(distances)))
    except Exception as error:
        log_message("student-face-verification", error)
        return None

    if best_distance == float("inf"):
        return None

    strict_threshold = min(SINGLE_STUDENT_THRESHOLD, FACE_RECOGNITION_THRESHOLD + 0.02)
    app.logger.info(
        "self-attendance-recognition student_id=%s student_name=%s best_distance=%.4f threshold=%.4f encodings=%s",
        student.get("id"),
        student.get("name"),
        best_distance,
        strict_threshold,
        len(student_encodings),
    )
    if best_distance <= strict_threshold:
        return {
            "distance": best_distance,
            "threshold": strict_threshold,
            "mode": "logged-in-student-verification",
        }
    return None


def build_result_message(
    face_detected,
    name,
    emotion,
    attendance_marked,
    multiple_faces_detected=False,
    spoof_detected=False,
):
    if not face_detected:
        return "No clear face was detected. Move closer to the camera and improve lighting."
    if multiple_faces_detected:
        return "Multiple faces were detected. Please keep only one student in front of the camera for each scan."
    if spoof_detected:
        return "Spoof attempt detected. Show your real face to the camera. Attendance was not counted."
    if not ENGINE_STATE["emotion_ready"] and not ENGINE_STATE["recognition_ready"]:
        return "Emotion and face recognition are unavailable. Check the engine status below."
    if not ENGINE_STATE["recognition_ready"] and ENGINE_STATE["emotion_ready"]:
        return "Emotion analysis worked, but face recognition is not ready."
    if ENGINE_STATE["recognition_ready"] and not ENGINE_STATE["emotion_ready"]:
        return "Face recognition is ready, but emotion analysis is unavailable."
    if name == "Unknown":
        return f"Face detected and emotion analyzed as {emotion}, but no registered student matched."
    if attendance_marked:
        return f"Attendance credited for {name} with emotion {emotion}."
    return f"{name} is already marked present today."


def build_multi_result_message(
    face_detected,
    detected_faces,
    recognized_people,
    attendance_marked_names,
    already_marked_names,
):
    if not face_detected:
        return "No clear face was detected. Move closer to the camera and improve lighting."
    if not ENGINE_STATE["emotion_ready"] and not ENGINE_STATE["recognition_ready"]:
        return "Emotion and face recognition are unavailable. Check the engine status below."
    if not ENGINE_STATE["recognition_ready"] and ENGINE_STATE["emotion_ready"]:
        return "Emotion analysis worked, but face recognition is not ready."
    if ENGINE_STATE["recognition_ready"] and not ENGINE_STATE["emotion_ready"]:
        return "Face recognition is ready, but emotion analysis is unavailable."
    if not recognized_people:
        if detected_faces > 1:
            return f"{detected_faces} faces were detected, but no registered student matched."
        return "Face detected, but no registered student matched."

    recognized_names = [person["name"] for person in recognized_people]
    message_parts = [f"Recognized {len(recognized_names)} student(s): {', '.join(recognized_names)}."]

    if attendance_marked_names:
        message_parts.append(f"Attendance credited for: {', '.join(attendance_marked_names)}.")
    if already_marked_names:
        message_parts.append(f"Already marked today: {', '.join(already_marked_names)}.")

    unmatched_faces = max(int(detected_faces or 0) - len(recognized_names), 0)
    if unmatched_faces:
        message_parts.append(f"{unmatched_faces} detected face(s) did not match a registered student.")

    return " ".join(message_parts)


def recognize_and_analyze(
    frame,
    mark_present=True,
    allow_single_student_fallback=True,
    allow_single_student_relaxed_match=True,
):
    try:
        face_locations = get_face_locations(frame)
        detected_faces = len(face_locations)
        multiple_faces_detected = detected_faces > 1
        face_location = face_locations[0] if face_locations else None
        face_img = extract_face_region(frame, face_location, padding=24)
        found_face = face_img is not None and bool(face_img.size)
        if multiple_faces_detected:
            return {
                "name": "Unknown",
                "emotion": "Unknown",
                "emotion_scores": {},
                "face_detected": True,
                "detected_faces": detected_faces,
                "multiple_faces_detected": True,
                "emotion_ready": ENGINE_STATE["emotion_ready"],
                "recognition_ready": ENGINE_STATE["recognition_ready"],
                "emotion_error": ENGINE_STATE["emotion_error"],
                "recognition_error": ENGINE_STATE["recognition_error"],
                "startup_message": ENGINE_STATE["startup_message"],
                "recognition_meta": None,
                "attendance_marked": False,
                "message": build_result_message(True, "Unknown", "Unknown", False, multiple_faces_detected=True),
            }

        emotion, emotion_scores = analyze_emotion(frame, face_img=face_img, face_location=face_location)
        name, recognition_meta = recognize_face(
            frame,
            face_img=face_img,
            face_location=face_location,
            allow_single_student_relaxed_match=allow_single_student_relaxed_match,
        )
        attendance_marked = False
        gps_self_attendance_required = False

        # Pragmatic fallback for the common single-user setup: if one student is
        # registered and a face is clearly detected, attribute the capture to that
        # student even when embedding-based recognition is unstable on webcam frames.
        if name == "Unknown" and found_face and len(all_students) == 1 and allow_single_student_fallback:
            name = all_students[0]
            recognition_meta = {
                "distance": None,
                "threshold": SINGLE_STUDENT_THRESHOLD,
                "mode": "single-student-fallback",
            }

        if mark_present and name != "Unknown" and ENGINE_STATE["recognition_ready"]:
            gps_self_attendance_required = should_skip_auto_attendance_mark_for_student(name)
            if not gps_self_attendance_required:
                attendance_marked = mark_attendance(name, emotion)
        message = build_result_message(found_face, name, emotion, attendance_marked, multiple_faces_detected=False)
        if gps_self_attendance_required and name != "Unknown":
            message = (
                f"{name} was verified. Open Student Self Attendance to finish GPS-based attendance "
                "for the active session."
            )

        return {
            "name": name,
            "emotion": emotion,
            "emotion_scores": emotion_scores,
            "face_detected": found_face,
            "detected_faces": detected_faces,
            "multiple_faces_detected": False,
            "emotion_ready": ENGINE_STATE["emotion_ready"],
            "recognition_ready": ENGINE_STATE["recognition_ready"],
            "emotion_error": ENGINE_STATE["emotion_error"],
            "recognition_error": ENGINE_STATE["recognition_error"],
            "startup_message": ENGINE_STATE["startup_message"],
            "recognition_meta": recognition_meta,
            "attendance_marked": attendance_marked,
            "message": message,
        }
    except Exception as error:
        log_message("recognize-analyze", error)
        return {
            "name": "Unknown",
            "emotion": "Unknown",
            "emotion_scores": {},
            "face_detected": False,
            "detected_faces": 0,
            "multiple_faces_detected": False,
            "emotion_ready": ENGINE_STATE["emotion_ready"],
            "recognition_ready": ENGINE_STATE["recognition_ready"],
            "emotion_error": ENGINE_STATE["emotion_error"],
            "recognition_error": ENGINE_STATE["recognition_error"],
            "startup_message": ENGINE_STATE["startup_message"],
            "recognition_meta": None,
            "attendance_marked": False,
            "message": "Face analysis is temporarily unavailable. Please capture again.",
        }


def recognize_multiple_faces(frame):
    detected_faces = locate_faces(frame)
    recognized_people = []
    unknown_faces = 0
    seen_names = set()

    for face_entry in detected_faces:
        face_img = face_entry["face_img"]
        face_location = face_entry["face_location"]
        emotion, emotion_scores = analyze_emotion(frame, face_img=face_img, face_location=face_location)
        name, recognition_meta = recognize_face(frame, face_img=face_img, face_location=face_location)

        if name == "Unknown":
            unknown_faces += 1
            continue

        if name in seen_names:
            continue

        seen_names.add(name)
        recognized_people.append(
            {
                "name": name,
                "emotion": emotion,
                "emotion_scores": emotion_scores,
                "recognition_meta": recognition_meta,
            }
        )

    recognized_people.sort(key=lambda item: item["name"])

    return {
        "face_detected": bool(detected_faces),
        "detected_faces": len(detected_faces),
        "recognized_people": recognized_people,
        "unknown_faces": unknown_faces,
    }


def aggregate_multi_face_results(results):
    recognized_people = {}
    max_detected_faces = 0
    face_detected = False

    for result in results:
        if result.get("face_detected"):
            face_detected = True
        max_detected_faces = max(max_detected_faces, int(result.get("detected_faces", 0)))

        for person in result.get("recognized_people", []):
            aggregate = recognized_people.setdefault(
                person["name"],
                {
                    "name": person["name"],
                    "emotion_votes": {},
                    "best_meta": person.get("recognition_meta"),
                },
            )
            emotion = person.get("emotion", "Unknown")
            aggregate["emotion_votes"][emotion] = aggregate["emotion_votes"].get(emotion, 0) + 1

            best_meta = aggregate.get("best_meta")
            candidate_meta = person.get("recognition_meta")
            if candidate_meta and candidate_meta.get("distance") is not None:
                if (
                    best_meta is None
                    or best_meta.get("distance") is None
                    or candidate_meta["distance"] < best_meta["distance"]
                ):
                    aggregate["best_meta"] = candidate_meta

    final_people = []
    for name, aggregate in recognized_people.items():
        emotion_votes = aggregate["emotion_votes"]
        final_emotion = "Unknown"
        if emotion_votes:
            final_emotion = max(emotion_votes.items(), key=lambda item: (item[1], item[0]))[0]
        final_people.append(
            {
                "name": name,
                "emotion": final_emotion,
                "recognition_meta": aggregate.get("best_meta"),
            }
        )

    final_people.sort(key=lambda item: item["name"])

    attendance_marked_names = []
    already_marked_names = []
    gps_required_names = []
    for person in final_people:
        if should_skip_auto_attendance_mark_for_student(person["name"]):
            gps_required_names.append(person["name"])
            continue
        if mark_attendance(person["name"], person["emotion"]):
            attendance_marked_names.append(person["name"])
        else:
            already_marked_names.append(person["name"])

    primary_person = final_people[0] if final_people else None
    primary_name = primary_person["name"] if primary_person else "Unknown"
    primary_emotion = primary_person["emotion"] if primary_person else "Unknown"

    return {
        "name": primary_name,
        "emotion": primary_emotion,
        "face_detected": face_detected,
        "detected_faces": max_detected_faces,
        "recognized_people": final_people,
        "recognized_count": len(final_people),
        "attendance_marked": bool(attendance_marked_names),
        "attendance_marked_names": attendance_marked_names,
        "already_marked_names": already_marked_names,
        "recognition_meta": primary_person.get("recognition_meta") if primary_person else None,
        "message": (
            build_multi_result_message(
                face_detected,
                max_detected_faces,
                final_people,
                attendance_marked_names,
                already_marked_names,
            )
            + (
                f" GPS-based self attendance is required for: {', '.join(gps_required_names)}."
                if gps_required_names
                else ""
            )
        ),
    }


def send_enrollment_email(student_name, recipient_email):
    subject = "Student Enrollment Confirmation"
    body = f"Congratulations, you are enrolled as a new student, '{student_name}'."
    smtp_settings = load_smtp_settings()

    def persist_email_copy(status, reason):
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        target_path = EMAIL_OUTBOX_DIR / f"{timestamp}_{sanitize_filename(recipient_email)}.json"
        payload = {
            "status": status,
            "reason": reason,
            "to": recipient_email,
            "subject": subject,
            "body": body,
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        target_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return str(target_path)

    if not smtp_settings["host"] or not smtp_settings["username"] or not smtp_settings["password"]:
        saved_copy = persist_email_copy(
            "not_sent",
            "SMTP settings are not configured.",
        )
        return False, (
            "SMTP email is not configured on this machine. "
            f"A confirmation copy was saved to {saved_copy}."
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_settings["sender"] or smtp_settings["username"] or ADMIN_EMAIL
    message["To"] = recipient_email
    message.set_content(body)

    try:
        if smtp_settings["port"] == 465:
            with smtplib.SMTP_SSL(smtp_settings["host"], smtp_settings["port"], timeout=20) as server:
                server.login(smtp_settings["username"], smtp_settings["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_settings["host"], smtp_settings["port"], timeout=20) as server:
                server.ehlo()
                if smtp_settings["use_tls"]:
                    server.starttls()
                    server.ehlo()
                server.login(smtp_settings["username"], smtp_settings["password"])
                server.send_message(message)
        return True, "Confirmation mail sent successfully."
    except smtplib.SMTPAuthenticationError:
        saved_copy = persist_email_copy(
            "failed",
            "SMTP authentication failed. Check the username and app password.",
        )
        return (
            False,
            "SMTP authentication failed. Verify the SMTP username and password. "
            "If you are using Gmail, enter the full Gmail address and a Google App Password. "
            f"A copy was saved to {saved_copy}.",
        )
    except Exception as error:
        saved_copy = persist_email_copy("failed", sanitize_text(error))
        return False, f"{sanitize_text(error)} A copy was saved to {saved_copy}."


def send_status_email(recipient_email, subject, body):
    smtp_settings = load_smtp_settings()
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    saved_copy = EMAIL_OUTBOX_DIR / f"{timestamp}_{sanitize_filename(recipient_email)}_status.json"

    payload = {
        "to": recipient_email,
        "subject": subject,
        "body": body,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if not smtp_settings["host"] or not smtp_settings["username"] or not smtp_settings["password"]:
        payload["status"] = "not_sent"
        payload["reason"] = "SMTP settings are not configured."
        saved_copy.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return False, f"SMTP not configured. Copy saved to {saved_copy}."

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = smtp_settings["sender"] or smtp_settings["username"] or ADMIN_EMAIL
    message["To"] = recipient_email
    message.set_content(body)

    try:
        if smtp_settings["port"] == 465:
            with smtplib.SMTP_SSL(smtp_settings["host"], smtp_settings["port"], timeout=20) as server:
                server.login(smtp_settings["username"], smtp_settings["password"])
                server.send_message(message)
        else:
            with smtplib.SMTP(smtp_settings["host"], smtp_settings["port"], timeout=20) as server:
                server.ehlo()
                if smtp_settings["use_tls"]:
                    server.starttls()
                    server.ehlo()
                server.login(smtp_settings["username"], smtp_settings["password"])
                server.send_message(message)
        return True, "Email sent successfully."
    except Exception as error:
        payload["status"] = "failed"
        payload["reason"] = sanitize_text(error)
        saved_copy.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return False, f"{sanitize_text(error)} Copy saved to {saved_copy}."


def notify_students_about_new_schedule(form_data):
    recipients = []
    for student in get_all_students():
        if not student.get("email"):
            continue
        if schedule_visible_to_student(form_data.get("class_name"), student.get("class_name")):
            recipients.append(student)

    for student in recipients:
        student_name = student.get("name", "Student")
        subject = f"New Class Assigned: {form_data.get('subject_name', 'Class')}"
        body = (
            f"Hello!! {student_name}\n\n"
            f"A NEW CLASS IS ASSIGNED FOR YOU.\n"
            f"Subject: {form_data.get('subject_name', 'N/A')}\n"
            f"Teacher: {form_data.get('teacher_name', 'N/A')}\n"
            f"Date: {form_data.get('session_date', 'N/A')} ({form_data.get('day_name', 'N/A')})\n"
            f"Class Time: {str(form_data.get('start_time', ''))[:5]} up to {str(form_data.get('end_time', ''))[:5]}\n"
            f"Attendance Portal Window: {str(form_data.get('attendance_open_time', ''))[:5]} up to {str(form_data.get('late_close_time', ''))[:5]}\n\n"
            "Please be ready to mark your attendance within the allowed time window."
        )
        send_status_email(student["email"], subject, body)


def queue_new_schedule_notifications(form_data):
    payload = dict(form_data)
    threading.Thread(
        target=notify_students_about_new_schedule,
        args=(payload,),
        daemon=True,
    ).start()


def build_schedule_form_data(form):
    raw_session_date = str(form.get("session_date", "")).strip()
    if not raw_session_date:
        raise ValueError("session_date is required")
    session_date = datetime.strptime(raw_session_date, "%Y-%m-%d").date()
    raw_tracking_minutes = str(form.get("post_attendance_tracking_minutes", "")).strip()
    if raw_tracking_minutes:
        tracking_minutes = max(0, min(180, int(float(raw_tracking_minutes))))
    else:
        tracking_minutes = get_post_attendance_tracking_default_minutes()
    gps_latitude_raw = str(form.get("gps_latitude", "") or "").strip()
    gps_longitude_raw = str(form.get("gps_longitude", "") or "").strip()
    if bool(gps_latitude_raw) != bool(gps_longitude_raw):
        raise ValueError("Both GPS latitude and longitude are required together.")

    gps_latitude = None
    gps_longitude = None
    if gps_latitude_raw and gps_longitude_raw:
        gps_latitude = coerce_gps_coordinate(gps_latitude_raw, "Admin latitude")
        gps_longitude = coerce_gps_coordinate(gps_longitude_raw, "Admin longitude")

    return {
        "class_name": form.get("class_name", "").strip(),
        "subject_name": form.get("subject_name", "").strip(),
        "teacher_name": form.get("teacher_name", "").strip(),
        "room_name": form.get("room_name", "").strip(),
        "session_date": session_date.strftime("%Y-%m-%d"),
        "day_name": session_date.strftime("%A"),
        "start_time": form.get("start_time", "").strip() + ":00",
        "end_time": form.get("end_time", "").strip() + ":00",
        "attendance_open_time": form.get("attendance_open_time", "").strip() + ":00",
        "attendance_close_time": form.get("attendance_close_time", "").strip() + ":00",
        "late_close_time": form.get("late_close_time", "").strip() + ":00",
        "gps_latitude": gps_latitude,
        "gps_longitude": gps_longitude,
        "allowed_radius_meters": normalize_allowed_radius_meters(form.get("allowed_radius_meters", "60")),
        "post_attendance_tracking_minutes": tracking_minutes,
    }


def process_auto_attendance_tasks():
    activate_pending_attendance_tracking()
    finalize_expired_attendance_tracking()
    notifications = auto_mark_absent_for_closed_sessions()
    for item in notifications:
        subject = f"Attendance Update: {item['subject_name']} on {item['session_date']}"
        body = (
            f"Student: {item['student_name']}\n"
            f"Subject: {item['subject_name']}\n"
            f"Date: {item['session_date']}\n"
            f"Status: {item['status']}\n"
        )
        send_status_email(item["student_email"], subject, body)
        mark_absence_notification_sent(item["attendance_id"])


def haversine_distance_meters(lat1, lon1, lat2, lon2):
    radius_meters = 6371000.0
    lat1, lon1, lat2, lon2 = map(float, [lat1, lon1, lat2, lon2])
    delta_latitude = radians(lat2 - lat1)
    delta_longitude = radians(lon2 - lon1)
    a = (
        sin(delta_latitude / 2) ** 2
        + cos(radians(lat1)) * cos(radians(lat2)) * sin(delta_longitude / 2) ** 2
    )
    c = 2 * atan2(sqrt(a), sqrt(1 - a))
    return radius_meters * c


def normalize_attendance_emotion_label(value):
    normalized = str(value or "").strip().title()
    if normalized in {"", "Unknown", "Unavailable", "None", "Null"}:
        return "Not detected"
    return normalized


def coerce_gps_coordinate(value, label):
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"{label} is invalid.")
    if not np.isfinite(numeric_value):
        raise ValueError(f"{label} is invalid.")
    if "lat" in label.lower():
        if numeric_value < -90.0 or numeric_value > 90.0:
            raise ValueError(f"{label} is out of range.")
    if "lon" in label.lower() or "lng" in label.lower():
        if numeric_value < -180.0 or numeric_value > 180.0:
            raise ValueError(f"{label} is out of range.")
    return numeric_value


def coerce_gps_accuracy_meters(value):
    try:
        accuracy_value = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not np.isfinite(accuracy_value):
        return 0.0
    accuracy_value = min(max(accuracy_value, 0.0), 5000.0)
    return accuracy_value


def coerce_position_timestamp_ms(value):
    if value in {None, ""}:
        return None
    try:
        timestamp_ms = int(float(value))
    except (TypeError, ValueError):
        return None
    if timestamp_ms <= 0:
        return None
    return timestamp_ms


def normalize_allowed_radius_meters(value, default_value=MAX_ATTENDANCE_DISTANCE_METERS):
    try:
        radius_value = float(value)
    except (TypeError, ValueError):
        radius_value = float(default_value)
    if not np.isfinite(radius_value):
        radius_value = float(default_value)
    return min(max(radius_value, 1.0), MAX_ATTENDANCE_DISTANCE_METERS)


def compute_effective_gps_distance_meters(
    lat1,
    lon1,
    lat2,
    lon2,
    accuracy_meters=None,
    position_timestamp_ms=None,
):
    student_latitude = coerce_gps_coordinate(lat1, "Student latitude")
    student_longitude = coerce_gps_coordinate(lon1, "Student longitude")
    admin_latitude = coerce_gps_coordinate(lat2, "Admin latitude")
    admin_longitude = coerce_gps_coordinate(lon2, "Admin longitude")
    raw_distance = haversine_distance_meters(
        student_latitude,
        student_longitude,
        admin_latitude,
        admin_longitude,
    )
    accuracy_value = coerce_gps_accuracy_meters(accuracy_meters)
    timestamp_ms = coerce_position_timestamp_ms(position_timestamp_ms)
    reading_age_seconds = None
    if timestamp_ms is not None:
        reading_age_seconds = max(
            0.0,
            (datetime.now().timestamp() * 1000.0 - float(timestamp_ms)) / 1000.0,
        )
    same_location_tolerance_meters = GPS_SAME_LOCATION_TOLERANCE_METERS
    adjusted_distance = 0.0 if raw_distance <= same_location_tolerance_meters else raw_distance
    return {
        "student_latitude": student_latitude,
        "student_longitude": student_longitude,
        "admin_latitude": admin_latitude,
        "admin_longitude": admin_longitude,
        "raw_distance_meters": raw_distance,
        "adjusted_distance_meters": adjusted_distance,
        "accuracy_meters": accuracy_value,
        "same_location_tolerance_meters": same_location_tolerance_meters,
        "position_timestamp_ms": timestamp_ms,
        "reading_age_seconds": reading_age_seconds,
        "is_stale": reading_age_seconds is not None and reading_age_seconds > GPS_MAX_READING_AGE_SECONDS,
    }


def compute_tracking_distance_decision(distance_payload, allowed_radius_meters):
    raw_distance = float(distance_payload.get("raw_distance_meters") or 0.0)
    adjusted_distance = float(distance_payload.get("adjusted_distance_meters") or raw_distance)
    accuracy_value = float(distance_payload.get("accuracy_meters") or 0.0)
    radius_value = normalize_allowed_radius_meters(allowed_radius_meters)
    same_location_tolerance_meters = float(
        distance_payload.get("same_location_tolerance_meters") or GPS_SAME_LOCATION_TOLERANCE_METERS
    )
    reading_age_seconds = distance_payload.get("reading_age_seconds")
    is_stale = bool(distance_payload.get("is_stale"))
    poor_accuracy_threshold = max(min(radius_value * 1.5, 60.0), GPS_MIN_POOR_ACCURACY_METERS)
    warning_accuracy_threshold = max(min(radius_value, 30.0), 12.0)
    jitter_buffer_meters = (
        min(max(accuracy_value * 0.12, 1.5), GPS_MAX_JITTER_BUFFER_METERS)
        if 0.0 < accuracy_value < poor_accuracy_threshold
        else 0.0
    )
    effective_radius_meters = radius_value + jitter_buffer_meters
    low_accuracy_warning = accuracy_value >= warning_accuracy_threshold if accuracy_value > 0 else False

    if is_stale:
        range_state = "uncertain"
        is_in_range = False
    elif raw_distance <= same_location_tolerance_meters:
        range_state = "in_range"
        is_in_range = True
    elif accuracy_value >= poor_accuracy_threshold:
        range_state = "uncertain"
        is_in_range = False
    elif adjusted_distance <= effective_radius_meters:
        range_state = "in_range"
        is_in_range = True
    else:
        range_state = "out_of_range"
        is_in_range = False

    return {
        "raw_distance_meters": raw_distance,
        "distance_meters": adjusted_distance,
        "allowed_radius_meters": radius_value,
        "jitter_buffer_meters": jitter_buffer_meters,
        "effective_radius_meters": effective_radius_meters,
        "accuracy_meters": accuracy_value,
        "poor_accuracy_threshold": poor_accuracy_threshold,
        "warning_accuracy_threshold": warning_accuracy_threshold,
        "same_location_tolerance_meters": same_location_tolerance_meters,
        "reading_age_seconds": reading_age_seconds,
        "is_stale": is_stale,
        "low_accuracy_warning": low_accuracy_warning,
        "range_state": range_state,
        "is_in_range": is_in_range,
    }


def build_gps_accuracy_warning(decision):
    accuracy_value = float(decision.get("accuracy_meters") or 0.0)
    if not accuracy_value:
        return "Trying to get a stable GPS reading. Tracking will continue and valid readings will be used."
    if decision.get("is_stale"):
        return "Trying to get a fresh GPS reading. Tracking will continue and valid readings will be used."
    return "GPS signal is weak. Tracking will continue and valid readings will be used."


def derive_tracking_gps_state(attendance_record, tracking_state):
    tracking_state = str(tracking_state or "").strip()
    range_state = str((attendance_record or {}).get("last_range_state") or "").strip().lower()

    if tracking_state == "Waiting For Attendance Window To Close":
        return {
            "gps_state": "GPS_WAITING",
            "gps_status_text": "Tracking starts after attendance closes",
        }
    if tracking_state == "Tracking Active":
        if range_state == "out_of_range":
            return {
                "gps_state": "GPS_VALID_OUT_OF_RANGE",
                "gps_status_text": "Outside allowed area",
            }
        if range_state == "in_range":
            return {
                "gps_state": "GPS_VALID_IN_RANGE",
                "gps_status_text": "Tracking live",
            }
        if range_state == "uncertain":
            return {
                "gps_state": "GPS_LOW_SIGNAL",
                "gps_status_text": "GPS signal is weak, tracking continues",
            }
        return {
            "gps_state": "GPS_TRACKING",
            "gps_status_text": "Trying to get a stable GPS reading",
        }
    if tracking_state == "Attendance Cancelled":
        if range_state == "out_of_range":
            return {
                "gps_state": "GPS_VALID_OUT_OF_RANGE",
                "gps_status_text": "Outside allowed area",
            }
        return {
            "gps_state": "GPS_TEMP_UNAVAILABLE",
            "gps_status_text": "GPS verification failed",
        }
    if tracking_state == "Tracking Completed":
        if range_state == "out_of_range":
            return {
                "gps_state": "GPS_VALID_OUT_OF_RANGE",
                "gps_status_text": "Outside allowed area",
            }
        if range_state == "in_range":
            return {
                "gps_state": "GPS_VALID_IN_RANGE",
                "gps_status_text": "Within allowed area",
            }
    if range_state == "in_range":
        return {
            "gps_state": "GPS_VALID_IN_RANGE",
            "gps_status_text": "Within allowed area",
        }
    if range_state == "out_of_range":
        return {
            "gps_state": "GPS_VALID_OUT_OF_RANGE",
            "gps_status_text": "Outside allowed area",
        }
    if range_state == "uncertain":
        return {
            "gps_state": "GPS_LOW_SIGNAL",
            "gps_status_text": "GPS signal is weak, tracking continues",
        }
    return {
        "gps_state": "GPS_NOT_REQUESTED",
        "gps_status_text": "Not captured yet",
    }


def parse_db_datetime(value):
    raw_value = str(value or "").strip()
    if not raw_value:
        return None
    try:
        return datetime.strptime(raw_value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def clear_student_attendance_preview(student_id=None):
    preview_state = session.get(STUDENT_ATTENDANCE_PREVIEW_SESSION_KEY)
    if student_id is not None and isinstance(preview_state, dict):
        try:
            preview_student_id = int(preview_state.get("student_id") or 0)
        except (TypeError, ValueError):
            preview_student_id = 0
        if preview_student_id and preview_student_id != int(student_id):
            return
    session.pop(STUDENT_ATTENDANCE_PREVIEW_SESSION_KEY, None)
    session.modified = True


def cache_student_attendance_preview(
    student,
    session_row,
    verification,
    latitude,
    longitude,
    accuracy_meters=None,
    position_timestamp_ms=None,
    distance_meters=None,
    raw_distance_meters=None,
    proof_snapshot_path="",
):
    result = verification.get("result") or {}
    liveness = verification.get("liveness") or {}
    preview_state = {
        "student_id": student["id"],
        "session_id": session_row["id"],
        "session_date": session_row.get("session_date", ""),
        "identified_name": result.get("name", ""),
        "emotion": result.get("emotion", "Unknown"),
        "liveness_label": liveness.get("label", "Unknown"),
        "liveness_passed": bool(verification.get("success")),
        "latitude": latitude,
        "longitude": longitude,
        "accuracy_meters": accuracy_meters,
        "position_timestamp_ms": coerce_position_timestamp_ms(position_timestamp_ms),
        "distance_meters": distance_meters,
        "raw_distance_meters": raw_distance_meters,
        "proof_snapshot_path": proof_snapshot_path,
        "captured_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    session[STUDENT_ATTENDANCE_PREVIEW_SESSION_KEY] = preview_state
    session.modified = True
    return preview_state


def get_student_attendance_preview(student_id=None, session_id=None, require_fresh=False):
    preview_state = session.get(STUDENT_ATTENDANCE_PREVIEW_SESSION_KEY)
    if not isinstance(preview_state, dict):
        return None
    try:
        preview_student_id = int(preview_state.get("student_id") or 0)
    except (TypeError, ValueError):
        clear_student_attendance_preview()
        return None
    if student_id is not None and preview_student_id != int(student_id):
        return None
    try:
        preview_session_id = int(preview_state.get("session_id") or 0)
    except (TypeError, ValueError):
        clear_student_attendance_preview(student_id=student_id)
        return None
    if session_id is not None and preview_session_id != int(session_id):
        return None
    if require_fresh:
        captured_at = parse_db_datetime(preview_state.get("captured_at"))
        if not captured_at or (datetime.now() - captured_at).total_seconds() > STUDENT_ATTENDANCE_PREVIEW_TTL_SECONDS:
            clear_student_attendance_preview(student_id=student_id)
            return None
    return preview_state


def get_locked_student_preview_session(student_id, requested_session_id=None, reference_time=None):
    preview_state = get_student_attendance_preview(student_id=student_id, require_fresh=True)
    if not preview_state:
        return None, None

    try:
        preview_session_id = int(preview_state.get("session_id") or 0)
    except (TypeError, ValueError):
        clear_student_attendance_preview(student_id=student_id)
        return None, None

    if not preview_session_id:
        clear_student_attendance_preview(student_id=student_id)
        return None, None

    if requested_session_id is not None and preview_session_id != int(requested_session_id):
        return preview_state, None

    preview_session = get_session_by_id(preview_session_id)
    if not preview_session:
        clear_student_attendance_preview(student_id=student_id)
        return None, None

    preview_session = ensure_materialized_student_session(preview_session)
    reference_date = (reference_time or datetime.now()).date()
    preview_session_date = parse_portal_date(
        preview_state.get("session_date") or preview_session.get("session_date") or reference_date
    )
    if preview_session_date != reference_date:
        clear_student_attendance_preview(student_id=student_id)
        return None, None

    return preview_state, preview_session


def get_tracking_reference_config(attendance_record=None, session_row=None):
    attendance_record = attendance_record or {}
    session_row = session_row or {}
    reference_latitude = attendance_record.get("tracking_reference_latitude")
    if reference_latitude is None:
        reference_latitude = session_row.get("gps_latitude")

    reference_longitude = attendance_record.get("tracking_reference_longitude")
    if reference_longitude is None:
        reference_longitude = session_row.get("gps_longitude")

    reference_radius_meters = attendance_record.get("tracking_reference_radius_meters")
    if reference_radius_meters is None:
        reference_radius_meters = session_row.get("allowed_radius_meters")
    reference_radius_meters = (
        normalize_allowed_radius_meters(reference_radius_meters)
        if reference_radius_meters is not None
        else None
    )

    try:
        if reference_latitude is not None:
            reference_latitude = coerce_gps_coordinate(reference_latitude, "Admin latitude")
        if reference_longitude is not None:
            reference_longitude = coerce_gps_coordinate(reference_longitude, "Admin longitude")
    except ValueError as error:
        log_message("gps-reference-config", error)
        reference_latitude = None
        reference_longitude = None

    tracking_window_starts_at = attendance_record.get("tracking_window_starts_at") or ""
    if not tracking_window_starts_at and session_row.get("session_date") and session_row.get("late_close_time"):
        try:
            tracking_window_starts_at = combine_date_time(
                session_row["session_date"],
                session_row["late_close_time"],
            ).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            tracking_window_starts_at = ""

    return {
        "latitude": reference_latitude,
        "longitude": reference_longitude,
        "allowed_radius_meters": reference_radius_meters,
        "tracking_window_starts_at": tracking_window_starts_at,
        "gps_enabled": reference_latitude is not None and reference_longitude is not None,
    }


def evaluate_session_gps_reading(
    session_row,
    latitude,
    longitude,
    accuracy_meters=None,
    position_timestamp_ms=None,
):
    reference_config = get_tracking_reference_config(session_row=session_row)
    if not reference_config["gps_enabled"]:
        return None

    distance_payload = compute_effective_gps_distance_meters(
        latitude,
        longitude,
        reference_config["latitude"],
        reference_config["longitude"],
        accuracy_meters=accuracy_meters,
        position_timestamp_ms=position_timestamp_ms,
    )
    configured_radius_meters = (
        reference_config["allowed_radius_meters"]
        if reference_config["allowed_radius_meters"] is not None
        else get_effective_session_radius_meters(session_row)
    )
    decision = compute_tracking_distance_decision(distance_payload, configured_radius_meters)
    return {
        "reference_config": reference_config,
        "distance_payload": distance_payload,
        "decision": decision,
    }


def should_lock_student_tracking_session(
    tracking_row,
    current_session=None,
    upcoming_session=None,
    requested_session_id=None,
    reference_time=None,
):
    if not tracking_row or not tracking_row.get("session_id"):
        return False
    if str(tracking_row.get("marked_via") or "").strip().lower() != "student_self":
        return False

    try:
        tracking_session_id = int(tracking_row.get("session_id") or 0) or None
    except (TypeError, ValueError):
        return False

    try:
        requested_session_id = int(requested_session_id or 0) or None
    except (TypeError, ValueError):
        requested_session_id = None

    current_session_id = None
    if current_session and current_session.get("id"):
        try:
            current_session_id = int(current_session.get("id") or 0) or None
        except (TypeError, ValueError):
            current_session_id = None

    upcoming_session_id = None
    if upcoming_session and upcoming_session.get("id"):
        try:
            upcoming_session_id = int(upcoming_session.get("id") or 0) or None
        except (TypeError, ValueError):
            upcoming_session_id = None

    workflow_status = derive_attendance_workflow_status(tracking_row)
    tracking_state = derive_tracking_state(tracking_row)
    reference_date = (reference_time or datetime.now()).date()
    session_date = parse_portal_date(
        tracking_row.get("attendance_date") or tracking_row.get("session_date") or reference_date
    )
    if session_date != reference_date:
        return False

    if requested_session_id and tracking_session_id != requested_session_id:
        return False

    if current_session_id and tracking_session_id != current_session_id:
        return False

    # Completed rows should never override another upcoming/current session.
    if workflow_status in COMPLETED_TRACKING_WORKFLOW_STATUSES:
        return (
            tracking_session_id == requested_session_id
            if requested_session_id is not None
            else False
        )

    if (
        not current_session_id
        and requested_session_id is None
        and upcoming_session_id
        and tracking_session_id == upcoming_session_id
    ):
        return False

    if workflow_status in LOCKED_TRACKING_WORKFLOW_STATUSES or tracking_state in {
        "Waiting For Attendance Window To Close",
        "Tracking Active",
    }:
        return True

    return False


def get_student_locked_tracking_flow(
    student_id,
    current_session=None,
    upcoming_session=None,
    requested_session_id=None,
    reference_time=None,
):
    tracking_row = get_student_tracking_record(student_id)
    if not tracking_row or not should_lock_student_tracking_session(
        tracking_row,
        current_session=current_session,
        upcoming_session=upcoming_session,
        requested_session_id=requested_session_id,
        reference_time=reference_time,
    ):
        return None, None

    tracking_session = (
        get_session_by_id(tracking_row["session_id"])
        if tracking_row.get("session_id")
        else None
    )
    if is_generic_gps_placeholder_record(tracking_session, tracking_row):
        return None, None
    return tracking_row, tracking_session or tracking_row


def resolve_student_portal_focus_session(session_context, locked_session=None, reference_time=None):
    if locked_session:
        return ensure_materialized_student_session(locked_session)

    current_session = (session_context or {}).get("current_session")
    current_day_sessions = (session_context or {}).get("day_sessions") or []
    portal_focus_session = current_session or (session_context or {}).get("upcoming_session")
    if not portal_focus_session and current_day_sessions:
        current_clock = reference_time or datetime.now()
        portal_focus_session = min(
            current_day_sessions,
            key=lambda item: abs(
                (
                    combine_date_time(item["session_date"], item["start_time"]) - current_clock
                ).total_seconds()
            ),
        )
    return portal_focus_session or (current_day_sessions[0] if current_day_sessions else None)


def build_api_error_response(message, status_code=500, extra=None):
    payload = {"success": False, "message": message}
    if isinstance(extra, dict):
        payload.update(extra)
    return jsonify(payload), status_code


def log_route_exception(route_label, error):
    logger.exception("[%s] %s", route_label, sanitize_text(error))


def guarded_json_route(route_label, fallback_message):
    def decorator(view_func):
        @wraps(view_func)
        def wrapper(*args, **kwargs):
            try:
                return view_func(*args, **kwargs)
            except Exception as error:
                log_route_exception(route_label, error)
                return build_api_error_response(fallback_message)

        return wrapper

    return decorator


def get_student_attendance_runtime_context(student_id, reference_time=None, requested_session_id=None):
    session_context = get_active_session_for_student(student_id, reference_time=reference_time)
    current_session = session_context.get("current_session")
    if current_session:
        current_session = ensure_materialized_student_session(current_session)
    upcoming_session = session_context.get("upcoming_session")
    if upcoming_session:
        upcoming_session = ensure_materialized_student_session(upcoming_session)
    locked_tracking_row, locked_tracking_session = get_student_locked_tracking_flow(
        student_id,
        current_session=current_session,
        upcoming_session=upcoming_session,
        requested_session_id=requested_session_id,
        reference_time=reference_time,
    )
    if locked_tracking_session:
        locked_tracking_session = ensure_materialized_student_session(locked_tracking_session)
    return {
        "session_context": session_context,
        "current_session": current_session,
        "upcoming_session": upcoming_session,
        "locked_tracking_row": locked_tracking_row,
        "locked_tracking_session": locked_tracking_session,
    }


def build_self_attendance_context(student):
    return get_student_context(student)


def get_effective_session_radius_meters(session_row):
    return normalize_allowed_radius_meters(
        (session_row or {}).get("allowed_radius_meters"),
        default_value=MAX_ATTENDANCE_DISTANCE_METERS,
    )


def build_tracking_cancellation_message(student, session_row):
    class_name = (
        (session_row or {}).get("class_name")
        or student.get("class_name")
        or "this class"
    )
    student_name = str(student.get("name") or "").strip()
    prefix = f"{student_name}, " if student_name else ""
    return (
        f"{prefix}your attendance is cancelled for {class_name} "
        "because your location is out of the range from GPS tracker."
    )


def derive_attendance_workflow_status(attendance_record):
    if not attendance_record:
        return ""
    stored_status = str(attendance_record.get("attendance_status") or "").strip().upper()
    if stored_status in {
        "MARKED_PENDING_TRACKING",
        "TRACKING_ACTIVE",
        "FINALIZED",
        "CANCELLED",
        "REJECTED",
        "PROVISIONAL",
        "FINAL",
    }:
        return stored_status
    raw_tracking_status = str(attendance_record.get("tracking_status") or "").strip()
    raw_status = str(attendance_record.get("status") or "").strip().title()
    if raw_status == "Provisional":
        if raw_tracking_status == "Tracking Active":
            return "TRACKING_ACTIVE"
        return "MARKED_PENDING_TRACKING"
    if raw_status == "Cancelled":
        return "CANCELLED"
    if raw_status in {"Present", "Late", "Absent"}:
        return "FINALIZED"
    if raw_status == "Rejected":
        return "REJECTED"
    return ""


def derive_tracking_state(attendance_record):
    if not attendance_record:
        return "Tracking Not Started"
    raw_tracking_status = str(attendance_record.get("tracking_status") or "").strip() or "Tracking Not Started"
    workflow_status = derive_attendance_workflow_status(attendance_record)
    if workflow_status == "CANCELLED" or raw_tracking_status == "Attendance Cancelled":
        return "Attendance Cancelled"
    if raw_tracking_status == "WAITING_FOR_WINDOW_CLOSE":
        return "Waiting For Attendance Window To Close"
    if raw_tracking_status == "Tracking Active":
        return "Tracking Active"
    if raw_tracking_status == "Tracking Completed":
        return "Tracking Completed"
    if raw_tracking_status == "Not Started":
        return "Tracking Not Started"
    if raw_tracking_status == "Not Required":
        return "Tracking Not Required"
    if workflow_status == "MARKED_PENDING_TRACKING":
        return "Waiting For Attendance Window To Close"
    if workflow_status == "TRACKING_ACTIVE":
        return "Tracking Active"
    if workflow_status == "FINALIZED":
        return "Tracking Completed"
    return "Tracking Not Started"


def get_session_window_timestamps(session_row):
    if not session_row or not session_row.get("session_date"):
        return {"open_dt": None, "close_dt": None, "late_dt": None}

    try:
        open_dt = combine_date_time(session_row["session_date"], session_row["attendance_open_time"])
    except Exception:
        open_dt = None
    try:
        close_dt = combine_date_time(session_row["session_date"], session_row["attendance_close_time"])
    except Exception:
        close_dt = None
    try:
        late_dt = combine_date_time(session_row["session_date"], session_row["late_close_time"])
    except Exception:
        late_dt = close_dt

    return {
        "open_dt": open_dt,
        "close_dt": close_dt,
        "late_dt": late_dt,
    }


def get_session_phase(session_row, attendance_record=None, now_dt=None):
    current_time = now_dt or datetime.now()
    session_windows = get_session_window_timestamps(session_row or attendance_record)
    open_dt = session_windows["open_dt"]
    late_dt = session_windows["late_dt"]
    workflow_status = derive_attendance_workflow_status(attendance_record)
    tracking_state = derive_tracking_state(attendance_record)

    if open_dt and late_dt and open_dt <= current_time < late_dt:
        return "ATTENDANCE_OPEN"
    if open_dt and current_time < open_dt:
        return "UPCOMING"
    if attendance_record and workflow_status in {"MARKED_PENDING_TRACKING", "TRACKING_ACTIVE", "PROVISIONAL"}:
        if tracking_state in {"Tracking Active", "Waiting For Attendance Window To Close"}:
            if not late_dt or current_time >= late_dt:
                return "GPS_TRACKING"
    return "CLOSED"


def can_resume_tracking_for_existing_record(session_row, attendance_record):
    if not session_row or not attendance_record:
        return False
    if session_row.get("gps_latitude") is None or session_row.get("gps_longitude") is None:
        return False
    if get_session_tracking_minutes(session_row) <= 0:
        return False
    workflow_status = derive_attendance_workflow_status(attendance_record)
    tracking_state = derive_tracking_state(attendance_record)
    return workflow_status in {"FINAL", "FINALIZED", "PROVISIONAL"} and tracking_state in {"Tracking Not Started", "Tracking Not Required"}


def should_neutralize_existing_tracking_snapshot(session_row, attendance_record):
    if not session_row or not attendance_record:
        return False
    return can_resume_tracking_for_existing_record(session_row, attendance_record)


def is_generic_gps_placeholder_record(session_row, attendance_record):
    if not session_row or not attendance_record:
        return False
    if attendance_record.get("marked_via") == "student_self":
        return False
    if not session_has_gps_tracker(session_row) or get_session_tracking_minutes(session_row) <= 0:
        return False
    tracking_state = derive_tracking_state(attendance_record)
    workflow_status = derive_attendance_workflow_status(attendance_record)
    return (
        tracking_state in {"Tracking Not Started", "Tracking Not Required"}
        and workflow_status in {"FINAL", "FINALIZED", "PROVISIONAL"}
    )


def build_tracking_snapshot(student, session_row, attendance_record, now_dt=None):
    current_time = now_dt or datetime.now()
    session_windows = get_session_window_timestamps(session_row or attendance_record)
    open_dt = session_windows["open_dt"]
    late_dt = session_windows["late_dt"]
    reference_config = get_tracking_reference_config(session_row=session_row)
    default_tracking_message = (
        "Attendance can be marked during the attendance window. GPS tracking starts after the attendance window closes."
        if reference_config["gps_enabled"]
        else "GPS location not configured for this session."
    )
    default_gps_status = None
    subject_name = (
        (session_row or {}).get("subject_name")
        or (attendance_record or {}).get("subject_name")
        or "this session"
    )
    class_name = (
        (session_row or {}).get("class_name")
        or (attendance_record or {}).get("class_name")
        or student.get("class_name")
        or ""
    )

    phase = get_session_phase(session_row, attendance_record, current_time)
    if default_gps_status is None:
        default_gps_status = (
            "GPS location not configured for this session."
            if not reference_config["gps_enabled"]
            else ("Checked when attendance is marked" if phase == "ATTENDANCE_OPEN" else "Not captured yet")
        )

    if not attendance_record or phase == "UPCOMING":
        attendance_seconds_left = (
            max(0, int((late_dt - current_time).total_seconds()))
            if phase == "ATTENDANCE_OPEN" and late_dt
            else 0 if late_dt and current_time >= late_dt else None
        )
        return {
            "available": False,
            "attendance_id": None,
            "session_id": (session_row or {}).get("id"),
            "class_name": class_name,
            "subject_name": subject_name,
            "status": "Tracking Not Started",
            "attendance_status": "",
            "tracking_state": "Tracking Not Started",
            "phase": phase,
            "attendance_seconds_left": attendance_seconds_left,
            "gps_seconds_left": None,
            "gps_state": "GPS_WAITING" if phase == "ATTENDANCE_OPEN" else "GPS_NOT_REQUESTED",
            "gps_status_text": default_gps_status,
            "message": default_tracking_message,
            "tracking_message": default_tracking_message,
            "tracking_started_at": "",
            "tracking_begins_at": "",
            "tracking_expires_at": "",
            "tracking_completed_at": "",
            "attendance_cancelled_at": "",
            "remaining_seconds": 0,
        }

    raw_tracking_status = str(attendance_record.get("tracking_status") or "").strip() or "Not Required"
    attendance_status = derive_attendance_workflow_status(attendance_record)
    tracking_state = derive_tracking_state(attendance_record)
    expires_dt = parse_db_datetime(attendance_record.get("tracking_expires_at"))
    started_dt = parse_db_datetime(attendance_record.get("tracking_started_at"))
    completed_dt = parse_db_datetime(attendance_record.get("tracking_completed_at"))
    cancelled_dt = parse_db_datetime(attendance_record.get("attendance_cancelled_at"))
    remaining_seconds = (
        max(0, int((expires_dt - current_time).total_seconds()))
        if expires_dt
        else 0
    )
    tracking_minutes_source = session_row or attendance_record
    if (
        tracking_minutes_source
        and tracking_minutes_source.get("post_attendance_tracking_minutes") is None
        and tracking_minutes_source.get("session_tracking_minutes") is not None
    ):
        tracking_minutes_source = dict(tracking_minutes_source)
        tracking_minutes_source["post_attendance_tracking_minutes"] = tracking_minutes_source.get("session_tracking_minutes")
    tracking_minutes = get_session_tracking_minutes(tracking_minutes_source)
    reference_config = get_tracking_reference_config(attendance_record=attendance_record, session_row=session_row)
    tracking_begins_dt = parse_db_datetime(reference_config["tracking_window_starts_at"])
    final_status = str(attendance_record.get("original_status") or "").strip().title()
    if not final_status:
        raw_status = str(attendance_record.get("status") or "").strip().title()
        final_status = raw_status if raw_status and raw_status != "Provisional" else "Present"
    attendance_seconds_left = (
        max(0, int((late_dt - current_time).total_seconds()))
        if phase == "ATTENDANCE_OPEN" and late_dt
        else 0 if late_dt and current_time >= late_dt else None
    )
    gps_seconds_left = remaining_seconds if phase == "GPS_TRACKING" else None

    status = attendance_status or tracking_state
    display_attendance_status = attendance_status or status
    message = "Attendance can be marked during the attendance window. GPS tracking starts after the attendance window closes."
    tracking_message = "Attendance can be marked during the attendance window. GPS tracking starts after the attendance window closes."
    if attendance_status == "CANCELLED" or tracking_state == "Attendance Cancelled":
        status = "CANCELLED"
        display_attendance_status = "CANCELLED"
        message = attendance_record.get("cancellation_reason") or build_tracking_cancellation_message(
            student,
            session_row or attendance_record,
        )
        tracking_message = message
    elif attendance_status in {"MARKED_PENDING_TRACKING", "PROVISIONAL"}:
        status = "MARKED_PENDING_TRACKING"
        display_attendance_status = "MARKED_PENDING_TRACKING"
        message = "Attendance marked, waiting for GPS verification after attendance window closes."
        tracking_message = (
            f"Tracker is waiting for the attendance window to close before GPS verification starts for {subject_name}."
        )
    elif attendance_status == "TRACKING_ACTIVE":
        status = "TRACKING_ACTIVE"
        display_attendance_status = "TRACKING_ACTIVE"
        message = "Attendance is temporarily marked while GPS verification is running."
        tracking_message = (
            f"GPS tracking is active for {subject_name}. Stay within the allowed GPS range until the timer ends."
        )
    elif attendance_status in {"FINALIZED", "FINAL"}:
        status = "FINALIZED"
        display_attendance_status = "FINALIZED"
        message = "Attendance Marked Successfully"
        if tracking_state == "Tracking Not Required":
            tracking_message = f"Post-attendance GPS tracking is not enabled for {subject_name}."
        else:
            tracking_message = f"GPS tracking completed for {subject_name}. Your attendance remains valid."
    elif tracking_state == "Tracking Not Required":
        tracking_message = f"Post-attendance GPS tracking is not enabled for {subject_name}."

    gps_state_payload = derive_tracking_gps_state(attendance_record, tracking_state)

    return {
        "available": True,
        "attendance_id": attendance_record.get("id"),
        "session_id": attendance_record.get("session_id") or (session_row or {}).get("id"),
        "class_name": class_name,
        "subject_name": subject_name,
        "status": status,
        "attendance_status": display_attendance_status,
        "tracking_state": tracking_state,
        "tracking_status": tracking_state,
        "tracking_status_raw": raw_tracking_status,
        "phase": phase,
        "attendance_seconds_left": attendance_seconds_left,
        "gps_seconds_left": gps_seconds_left,
        "final_status": final_status,
        "message": message,
        "tracking_message": tracking_message,
        "tracking_minutes": tracking_minutes,
        "tracking_started_at": started_dt.strftime("%Y-%m-%d %H:%M:%S") if started_dt else "",
        "tracking_begins_at": tracking_begins_dt.strftime("%Y-%m-%d %H:%M:%S") if tracking_begins_dt else "",
        "tracking_expires_at": expires_dt.strftime("%Y-%m-%d %H:%M:%S") if expires_dt else "",
        "tracking_completed_at": completed_dt.strftime("%Y-%m-%d %H:%M:%S") if completed_dt else "",
        "attendance_cancelled_at": cancelled_dt.strftime("%Y-%m-%d %H:%M:%S") if cancelled_dt else "",
        "remaining_seconds": remaining_seconds,
        "tracking_active": tracking_state == "Tracking Active",
        "out_of_range_count": int(attendance_record.get("out_of_range_count") or 0),
        "out_of_range_limit": GPS_TRACKING_OUT_OF_RANGE_LIMIT,
        "distance_meters": attendance_record.get("distance_meters"),
        "raw_distance_meters": attendance_record.get("last_raw_distance_meters"),
        "gps_accuracy_meters": attendance_record.get("last_location_accuracy_meters"),
        "range_state": attendance_record.get("last_range_state") or "",
        "last_location_latitude": attendance_record.get("last_location_latitude"),
        "last_location_longitude": attendance_record.get("last_location_longitude"),
        "last_location_checked_at": attendance_record.get("last_location_checked_at") or "",
        "gps_enabled": reference_config["gps_enabled"],
        "allowed_radius_meters": reference_config["allowed_radius_meters"],
        "reference_latitude": reference_config["latitude"],
        "reference_longitude": reference_config["longitude"],
        "gps_state": gps_state_payload["gps_state"],
        "gps_status_text": gps_state_payload["gps_status_text"],
    }


def save_proof_snapshot(data_url, student, session_row):
    image = decode_base64_image(data_url)
    if image is None:
        return ""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = (
        f"{student['enrollment_number']}_{session_row['id']}_{timestamp}.jpg"
    )
    target = PROOF_SNAPSHOTS_DIR / filename
    try:
        cv2.imwrite(str(target), image)
    except Exception:
        return ""
    return str(target.relative_to(BASE_DIR))


def ensure_materialized_student_session(session_row):
    if session_row and session_row.get("id"):
        return session_row
    if not session_row or not session_row.get("schedule_id") or not session_row.get("session_date"):
        return session_row
    sync_target_date = session_row["session_date"]
    from database import sync_schedule_sessions

    sync_schedule_sessions(
        session_row["schedule_id"],
        start_date=sync_target_date,
        end_date=sync_target_date,
        replace_future=False,
    )
    return get_session_by_schedule_and_date(session_row["schedule_id"], sync_target_date) or session_row


def build_student_attendance_result(student, frames):
    ensure_fallback_recognition_ready()
    liveness = assess_liveness(frames)
    if liveness["is_spoof"]:
        app.logger.info(
            "self-attendance-preview student_id=%s student_name=%s liveness=%s spoof=%s reason=%s",
            student.get("id"),
            student.get("name"),
            liveness.get("label"),
            liveness.get("is_spoof"),
            liveness.get("message", ""),
        )
        return {
            "success": False,
            "status": "Rejected",
            "message": "Anti-spoofing check failed.",
            "liveness": liveness,
            "result": None,
        }

    results = [
        recognize_and_analyze(
            frame,
            mark_present=False,
            allow_single_student_fallback=False,
            allow_single_student_relaxed_match=False,
        )
        for frame in frames
    ]
    result = aggregate_batch_results(results)
    result["emotion"] = normalize_attendance_emotion_label(result.get("emotion"))
    aggregate_name = str(result.get("name") or "Unknown").strip() or "Unknown"
    direct_match = verify_logged_in_student_face(student, frames)
    if direct_match and aggregate_name in {"Unknown", student["name"]}:
        result["name"] = student["name"]
        result["recognition_meta"] = direct_match
    matched_name = result.get("name", "Unknown")
    app.logger.info(
        "self-attendance-preview student_id=%s student_name=%s aggregate_name=%s direct_match=%s face_detected=%s multiple_faces=%s emotion=%s liveness=%s",
        student.get("id"),
        student.get("name"),
        matched_name,
        bool(direct_match),
        result.get("face_detected"),
        result.get("multiple_faces_detected"),
        result.get("emotion"),
        liveness.get("label"),
    )
    if matched_name != student["name"]:
        if result.get("multiple_faces_detected"):
            failure_message = "Multiple faces were detected. Please keep only your face in the frame."
        elif not result.get("face_detected"):
            failure_message = "No clear face was detected. Move closer to the camera and improve lighting."
        elif matched_name == "Unknown":
            failure_message = "Face recognition could not confidently match the logged-in student."
        else:
            failure_message = "Logged-in student does not match the recognized face."
        return {
            "success": False,
            "status": "Rejected",
            "message": failure_message,
            "liveness": liveness,
            "result": result,
        }

    return {
        "success": True,
        "status": "Verified",
        "message": "Face and liveness verification passed.",
        "liveness": liveness,
        "result": result,
    }


def admin_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            flash("Please log in as admin first.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)

    return wrapper


def student_required(view_func):
    @wraps(view_func)
    def wrapper(*args, **kwargs):
        if not session.get("student_logged_in") or not session.get("student_id"):
            flash("Please log in as student first.", "warning")
            return redirect(url_for("student_login"))
        return view_func(*args, **kwargs)

    return wrapper


def redirect_admin_next(default_endpoint):
    target = str(request.form.get("next") or request.args.get("next") or "").strip()
    if target.startswith("/") and not target.startswith("//"):
        return redirect(target)
    return redirect(url_for(default_endpoint))


def get_admin_base_context():
    ensure_schedule_days_are_working()
    process_auto_attendance_tasks()
    primary_admin = get_primary_admin()
    return {
        "engine_state": ENGINE_STATE,
        "assistant_name": ASSISTANT_NAME,
        "project_owner": DEFAULT_OWNER,
        "app_url": f"http://{DISPLAY_HOST}:{DEFAULT_PORT}",
        "admin_email_hint": primary_admin["email"] if primary_admin else ADMIN_EMAIL,
        "smtp_configured": smtp_is_configured(),
        "today_iso": date.today().strftime("%Y-%m-%d"),
        "admin_week_dates": build_week_dates(),
    }


def build_admin_notifications(stats, today_sessions):
    notifications = []
    if not ENGINE_STATE.get("recognition_ready"):
        notifications.append(
            {
                "level": "warning",
                "title": "Face recognition needs attention",
                "message": ENGINE_STATE.get("recognition_error")
                or "The face recognition engine is still warming up or needs a dependency check.",
            }
        )
    if not ENGINE_STATE.get("emotion_ready"):
        notifications.append(
            {
                "level": "warning",
                "title": "Emotion engine needs attention",
                "message": ENGINE_STATE.get("emotion_error")
                or "The emotion model is still warming up or needs a dependency check.",
            }
        )

    active_sessions = [
        item for item in today_sessions if str(item.get("session_status") or "").title() in {"Active", "Delayed"}
    ]
    if active_sessions:
        active_labels = ", ".join(
            f"{item['class_name']} - {item['subject_name']}" for item in active_sessions[:2]
        )
        notifications.append(
            {
                "level": "info",
                "title": "Live classes in progress",
                "message": f"{len(active_sessions)} session(s) are live now. {active_labels}",
            }
        )

    pending_corrections = stats.get("pending_corrections") or []
    if pending_corrections:
        notifications.append(
            {
                "level": "warning",
                "title": "Correction requests waiting",
                "message": f"{len(pending_corrections)} correction request(s) need admin review.",
            }
        )

    low_attendance_students = stats.get("low_attendance_students") or []
    if low_attendance_students:
        notifications.append(
            {
                "level": "danger",
                "title": "Low attendance watchlist",
                "message": f"{len(low_attendance_students)} student(s) are below the attendance threshold.",
            }
        )

    if not notifications:
        notifications.append(
            {
                "level": "success",
                "title": "System looks healthy",
                "message": "No urgent admin alerts are waiting right now.",
            }
        )
    return notifications[:5]


def session_has_gps_tracker(session_row):
    if not session_row:
        return False
    return bool(get_tracking_reference_config(session_row=session_row).get("gps_enabled"))


def get_gps_required_student_session(student_id):
    session_context = get_active_session_for_student(student_id)
    current_session = (session_context or {}).get("current_session")
    if not current_session:
        return None
    current_session = ensure_materialized_student_session(current_session)
    if session_has_gps_tracker(current_session) and get_session_tracking_minutes(current_session) > 0:
        return current_session
    return None


def should_skip_auto_attendance_mark_for_student(student_name):
    student = get_student_by_name(student_name)
    if not student:
        return False
    return get_gps_required_student_session(student["id"]) is not None


def build_admin_gps_tracker_state(today_sessions):
    active_session = next(
        (item for item in (today_sessions or []) if item.get("session_status") in {"Active", "Delayed"}),
        None,
    )
    next_session = next(
        (item for item in (today_sessions or []) if item.get("session_status") == "Scheduled"),
        None,
    )
    focus_session = active_session or next_session or ((today_sessions or [None])[0])

    if active_session and session_has_gps_tracker(active_session):
        return {
            "is_ready": True,
            "is_on": True,
            "label": "GPS Tracker is on",
            "detail": f"{active_session.get('subject_name', 'Live session')} is using GPS validation.",
        }

    if focus_session and session_has_gps_tracker(focus_session):
        return {
            "is_ready": True,
            "is_on": False,
            "label": "GPS Tracker is ready",
            "detail": f"GPS is configured for {focus_session.get('subject_name', 'the next session')}.",
        }

    return {
        "is_ready": False,
        "is_on": False,
        "label": "GPS Tracker is not ready",
        "detail": "Admin GPS is not set for the active or next session.",
    }


def build_student_gps_tracker_state(session_context, portal_focus_session, tracking_snapshot):
    current_session = (session_context or {}).get("current_session")
    focus_session = current_session or portal_focus_session
    tracking_status = str((tracking_snapshot or {}).get("tracking_state") or "").strip()

    if tracking_status == "Tracking Active":
        return {
            "is_ready": True,
            "is_on": True,
            "label": "GPS Tracker is on",
            "detail": (tracking_snapshot or {}).get("tracking_message") or "Attendance tracking is active.",
        }

    if current_session and session_has_gps_tracker(current_session):
        return {
            "is_ready": True,
            "is_on": True,
            "label": "GPS Tracker is on",
            "detail": f"GPS validation is active for {(current_session or {}).get('subject_name', 'this class')}.",
        }

    if focus_session and session_has_gps_tracker(focus_session):
        return {
            "is_ready": True,
            "is_on": False,
            "label": "GPS Tracker is ready",
            "detail": f"GPS is configured for {(focus_session or {}).get('subject_name', 'your next class')}.",
        }

    return {
        "is_ready": False,
        "is_on": False,
        "label": "GPS Tracker is not ready",
        "detail": "Admin has not set GPS for your active or next session.",
    }


def build_student_engine_state():
    anti_spoof_ready = bool(
        ENGINE_STATE.get("face_detector_ready")
        or ENGINE_STATE.get("recognition_ready")
        or face_recognition is not None
    )
    return {
        "recognition_ready": bool(ENGINE_STATE.get("recognition_ready")),
        "emotion_ready": bool(ENGINE_STATE.get("emotion_ready")),
        "anti_spoof_ready": anti_spoof_ready,
    }


def build_admin_tracking_rows(target_date=None, limit=None, active_only=False):
    rows = []
    for record in list_attendance_tracking_records(target_date=target_date, limit=limit, active_only=active_only):
        snapshot = build_tracking_snapshot(
            {
                "name": record.get("student_name") or record.get("name") or "",
                "class_name": record.get("student_class_name") or record.get("class_name") or "",
            },
            record,
            record,
        )
        rows.append(
            {
                "attendance_id": record.get("id"),
                "session_id": record.get("session_id"),
                "student_name": record.get("student_name") or record.get("name") or "Student",
                "enrollment_number": record.get("enrollment_number") or "",
                "class_name": record.get("session_class_name") or record.get("class_name") or "",
                "subject_name": record.get("subject_name") or "",
                "teacher_name": record.get("substitute_teacher") or record.get("session_teacher_name") or "",
                "session_date": record.get("session_date") or "",
                "session_status": record.get("session_status") or "",
                "attendance_status": snapshot.get("attendance_status") or "",
                "tracking_state": snapshot.get("tracking_state") or "",
                "message": snapshot.get("message") or "",
                "tracking_message": snapshot.get("tracking_message") or "",
                "remaining_seconds": snapshot.get("remaining_seconds") or 0,
                "tracking_expires_at": snapshot.get("tracking_expires_at") or "",
                "out_of_range_count": snapshot.get("out_of_range_count") or 0,
                "out_of_range_limit": snapshot.get("out_of_range_limit") or GPS_TRACKING_OUT_OF_RANGE_LIMIT,
                "final_status": snapshot.get("final_status") or "",
                "distance_meters": snapshot.get("distance_meters"),
                "raw_distance_meters": snapshot.get("raw_distance_meters"),
                "gps_accuracy_meters": snapshot.get("gps_accuracy_meters"),
                "range_state": snapshot.get("range_state") or "",
                "allowed_radius_meters": snapshot.get("allowed_radius_meters"),
                "reference_latitude": snapshot.get("reference_latitude"),
                "reference_longitude": snapshot.get("reference_longitude"),
                "last_location_latitude": snapshot.get("last_location_latitude"),
                "last_location_longitude": snapshot.get("last_location_longitude"),
            }
        )
    return rows


def build_report_filters(values):
    raw_student_id = str(values.get("student_id", "") or "").strip()
    try:
        student_id = int(raw_student_id) if raw_student_id else None
    except ValueError:
        student_id = None

    return {
        "date_from": str(values.get("date_from", "") or "").strip(),
        "date_to": str(values.get("date_to", "") or "").strip(),
        "class_name": str(values.get("class_name", "") or "").strip(),
        "student_id": student_id,
        "student_id_raw": raw_student_id,
        "status": str(values.get("status", "") or "").strip(),
    }


def build_report_summary(report_rows):
    summary = {
        "total_records": len(report_rows),
        "present_count": 0,
        "late_count": 0,
        "absent_count": 0,
        "cancelled_count": 0,
        "rejected_count": 0,
    }
    for row in report_rows:
        status = str(row.get("status") or "").title()
        if status == "Present":
            summary["present_count"] += 1
        elif status == "Late":
            summary["late_count"] += 1
        elif status == "Absent":
            summary["absent_count"] += 1
        elif status == "Cancelled":
            summary["cancelled_count"] += 1
        elif status == "Rejected":
            summary["rejected_count"] += 1
    return summary


def get_common_context():
    finalize_expired_attendance_tracking()
    context = get_admin_base_context()
    stats = get_dashboard_stats()
    week_start, week_end = get_week_bounds()
    today_sessions = list_class_sessions(target_date=datetime.now().strftime("%Y-%m-%d"), days=1)
    context.update(
        {
        "stats": stats,
        "working_days": stats.get("working_days", []),
        "schedules": list_class_schedules(),
        "today_sessions": today_sessions,
        "weekly_sessions": list_class_sessions(start_date=week_start, end_date=week_end),
        "sessions": list_class_sessions(start_date=week_start, end_date=week_end),
        "holidays": list_holidays(),
        "correction_requests": list_correction_requests(),
        "gps_change_logs": list_gps_change_logs(),
        "low_attendance_threshold": get_low_attendance_threshold(),
        "post_attendance_tracking_default_minutes": get_post_attendance_tracking_default_minutes(),
        "gps_tracker_state": build_admin_gps_tracker_state(today_sessions),
        "admin_tracking_rows": build_admin_tracking_rows(target_date=date.today(), limit=8),
        }
    )
    return context


def get_dashboard_context():
    context = get_common_context()
    today_sessions = context["today_sessions"]
    current_active_session = next(
        (item for item in today_sessions if item.get("session_status") in {"Active", "Delayed"}),
        None,
    )
    next_session = next(
        (item for item in today_sessions if item.get("session_status") == "Scheduled"),
        None,
    )
    context.update(
        {
            "current_active_session": current_active_session,
            "next_session": next_session,
            "dashboard_notifications": build_admin_notifications(context["stats"], today_sessions),
            "active_tracking_rows": [
                item
                for item in context["admin_tracking_rows"]
                if item["attendance_status"] in {"MARKED_PENDING_TRACKING", "TRACKING_ACTIVE"}
            ],
        }
    )
    return context


def get_admin_analytics_context():
    context = get_common_context()
    attendance_rankings = []
    for student in context["stats"].get("students", []):
        details = context["stats"].get("attendance_details", {}).get(student["name"], {})
        attendance_rankings.append(
            {
                "id": student["id"],
                "name": student["name"],
                "class_name": student["class_name"],
                "attendance": details.get("attendance", 0),
                "total_classes": details.get("total_classes", 0),
                "percentage": details.get("percentage", 0),
            }
        )

    attendance_rankings.sort(key=lambda item: (-item["percentage"], item["name"]))
    context["top_attendance_students"] = attendance_rankings[:5]
    context["lowest_attendance_students"] = sorted(
        attendance_rankings,
        key=lambda item: (item["percentage"], item["name"]),
    )[:5]
    return context


def get_admin_sessions_context():
    return get_common_context()


def get_admin_live_monitor_context():
    context = get_common_context()
    today_sessions = context["today_sessions"]
    live_sessions = [
        item for item in today_sessions if item.get("session_status") in {"Active", "Delayed"}
    ]
    upcoming_sessions = [item for item in today_sessions if item.get("session_status") == "Scheduled"]
    completed_sessions = [item for item in today_sessions if item.get("session_status") == "Completed"]
    cancelled_sessions = [item for item in today_sessions if item.get("session_status") == "Cancelled"]
    context["live_monitor_summary"] = {
        "live_count": len(live_sessions),
        "upcoming_count": len(upcoming_sessions),
        "completed_count": len(completed_sessions),
        "cancelled_count": len(cancelled_sessions),
        "gps_configured_count": sum(
            1
            for item in today_sessions
            if item.get("gps_latitude") is not None and item.get("gps_longitude") is not None
        ),
    }
    context["active_tracking_rows"] = build_admin_tracking_rows(target_date=date.today(), active_only=False)
    return context


def get_admin_overrides_context():
    context = get_common_context()
    override_records = list_override_permissions()
    context.update(
        {
            "override_records": override_records,
            "active_overrides": [item for item in override_records if item["status"] == "Active"],
            "used_overrides": [item for item in override_records if item["status"] == "Used"],
            "expired_overrides": [item for item in override_records if item["status"] == "Expired"],
        }
    )
    return context


def get_admin_holidays_context():
    return get_common_context()


def get_admin_reports_context(args=None):
    context = get_admin_base_context()
    stats = get_dashboard_stats()
    filters = build_report_filters(args or request.args)
    report_rows = get_attendance_report(
        {
            "date_from": filters["date_from"] or None,
            "date_to": filters["date_to"] or None,
            "class_name": filters["class_name"] or None,
            "student_id": filters["student_id"],
            "status": filters["status"] or None,
        }
    )
    context.update(
        {
            "stats": stats,
            "report_filters": filters,
            "report_rows": report_rows,
            "report_summary": build_report_summary(report_rows),
            "report_classes": sorted({student["class_name"] for student in stats.get("students", [])}),
            "report_students": stats.get("students", []),
        }
    )
    return context


def get_admin_settings_context(smtp_settings=None):
    context = get_common_context()
    context["smtp_settings"] = smtp_settings or load_smtp_settings()
    return context


def get_student_context(student):
    finalize_expired_attendance_tracking()
    ensure_schedule_days_are_working()
    process_auto_attendance_tasks()
    summary = get_student_attendance_summary(student["id"])
    session_context = get_active_session_for_student(student["id"])
    current_session = session_context.get("current_session")
    upcoming_session = session_context.get("upcoming_session")
    locked_tracking_row, locked_tracking_session = get_student_locked_tracking_flow(
        student["id"],
        current_session=current_session,
        upcoming_session=upcoming_session,
    )
    portal_focus_session = resolve_student_portal_focus_session(
        session_context,
        locked_session=locked_tracking_session,
    )
    current_session_record = None
    if locked_tracking_row and portal_focus_session and portal_focus_session.get("id") == locked_tracking_row.get("session_id"):
        current_session_record = locked_tracking_row
    elif portal_focus_session and portal_focus_session.get("id"):
        current_session_record = get_effective_attendance_record(student["id"], portal_focus_session["id"])
    if is_generic_gps_placeholder_record(portal_focus_session, current_session_record):
        current_session_record = None
    current_session_can_resume_tracking = can_resume_tracking_for_existing_record(
        portal_focus_session,
        current_session_record,
    )
    tracking_row = locked_tracking_row or current_session_record
    tracking_session = (
        locked_tracking_session
        or portal_focus_session
        or (get_session_by_id(tracking_row["session_id"]) if tracking_row and tracking_row.get("session_id") else None)
    )
    tracking_snapshot = build_tracking_snapshot(
        student,
        tracking_session or portal_focus_session or tracking_row,
        tracking_row or current_session_record,
    )
    student_history_records = filter_student_history_records(summary.get("history", []))
    return {
        "assistant_name": ASSISTANT_NAME,
        "project_owner": DEFAULT_OWNER,
        "app_url": f"http://{DISPLAY_HOST}:{DEFAULT_PORT}",
        "student": student,
        "engine_state": build_student_engine_state(),
        "student_summary": summary,
        "session_context": session_context,
        "current_session_record": current_session_record,
        "current_session_can_resume_tracking": current_session_can_resume_tracking,
        "active_flow_session": portal_focus_session,
        "portal_focus_session": portal_focus_session,
        "tracking_snapshot": tracking_snapshot,
        "gps_tracker_state": build_student_gps_tracker_state(session_context, portal_focus_session, tracking_snapshot),
        "scheduled_sessions": list_student_scheduled_sessions(student["id"]),
        "correction_requests": list_correction_requests(student["id"]),
        "student_history_records": student_history_records,
        "student_history_reset_at": get_student_history_reset_at(),
        "today_iso": date.today().strftime("%Y-%m-%d"),
        "student_week_dates": build_week_dates(),
        "admin_week_dates": build_week_dates(),
    }


def get_student_history_reset_at():
    raw_value = str(get_app_setting("student_history_reset_at", "") or "").strip()
    return parse_db_datetime(raw_value) if raw_value else None


def filter_student_history_records(history_rows):
    reset_at = get_student_history_reset_at()
    if not reset_at:
        return list(history_rows or [])

    filtered_rows = []
    for item in history_rows or []:
        session_date = parse_portal_date(item.get("date"))
        time_value = str(item.get("time") or item.get("start_time") or "00:00:00").strip()
        if len(time_value) == 5:
            time_value = f"{time_value}:00"
        try:
            item_timestamp = datetime.strptime(
                f"{session_date.strftime('%Y-%m-%d')} {time_value[:8]}",
                "%Y-%m-%d %H:%M:%S",
            )
        except ValueError:
            item_timestamp = datetime.combine(session_date, datetime.min.time())

        if item_timestamp >= reset_at:
            filtered_rows.append(item)

    return filtered_rows


def render_student_portal_page(template_name):
    student = get_student_by_id(session.get("student_id"))
    if not student:
        session.clear()
        flash("Student account not found. Please log in again.", "warning")
        return redirect(url_for("student_login"))
    return render_template(template_name, **get_student_context(student))


def build_week_dates(reference_date=None):
    target = reference_date if isinstance(reference_date, date) else None
    if target is None:
        raw_value = reference_date.strftime("%Y-%m-%d") if isinstance(reference_date, datetime) else reference_date
        try:
            target = datetime.strptime(raw_value, "%Y-%m-%d").date() if raw_value else date.today()
        except (TypeError, ValueError):
            target = date.today()
    week_start = target - timedelta(days=target.weekday())
    return [
        {
            "iso_date": (week_start + timedelta(days=offset)).strftime("%Y-%m-%d"),
            "weekday": (week_start + timedelta(days=offset)).strftime("%A"),
            "day_short": (week_start + timedelta(days=offset)).strftime("%a"),
            "day_number": (week_start + timedelta(days=offset)).strftime("%d"),
            "month_short": (week_start + timedelta(days=offset)).strftime("%b"),
            "is_today": (week_start + timedelta(days=offset)) == date.today(),
        }
        for offset in range(7)
    ]


def parse_portal_date(raw_value):
    if isinstance(raw_value, date):
        return raw_value
    try:
        return datetime.strptime(str(raw_value or ""), "%Y-%m-%d").date()
    except ValueError:
        return date.today()


def _format_timer_delta(delta_seconds):
    remaining = max(0, int(delta_seconds))
    hours, remainder = divmod(remaining, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def build_session_ui_payload(session_row, student_id=None, now_dt=None):
    current_time = now_dt or datetime.now()
    session_date = parse_portal_date(session_row["session_date"])
    start_dt = datetime.combine(session_date, datetime.strptime(session_row["start_time"], "%H:%M:%S").time())
    end_dt = datetime.combine(session_date, datetime.strptime(session_row["end_time"], "%H:%M:%S").time())
    open_dt = datetime.combine(session_date, datetime.strptime(session_row["attendance_open_time"], "%H:%M:%S").time())
    late_dt = datetime.combine(session_date, datetime.strptime(session_row["late_close_time"], "%H:%M:%S").time())
    reference_config = get_tracking_reference_config(session_row=session_row)
    record = (
        get_effective_attendance_record(student_id, session_row["id"])
        if student_id and session_row.get("id")
        else None
    )
    tracking_snapshot = (
        build_tracking_snapshot(
            {"name": "", "class_name": session_row.get("class_name", "")},
            session_row,
            record,
            now_dt=current_time,
        )
        if record
        else build_tracking_snapshot(
            {"name": "", "class_name": session_row.get("class_name", "")},
            session_row,
            None,
            now_dt=current_time,
        )
    )

    raw_status = (session_row.get("session_status") or "Scheduled").strip().title()
    if raw_status == "Cancelled":
        class_status = "Cancelled"
    elif raw_status == "Completed" or current_time > end_dt:
        class_status = "Completed"
    elif start_dt <= current_time <= end_dt or raw_status in {"Active", "Delayed"}:
        class_status = "Active"
    else:
        class_status = "Upcoming"

    session_phase = str(tracking_snapshot.get("phase") or "").upper()

    if record and session_phase != "UPCOMING":
        workflow_status = tracking_snapshot["attendance_status"]
        attendance_seconds_left = tracking_snapshot.get("attendance_seconds_left")
        gps_seconds_left = tracking_snapshot.get("gps_seconds_left")
        if workflow_status == "CANCELLED":
            attendance_state = "Attendance Cancelled"
            countdown_state = "closed"
            countdown_text = tracking_snapshot["message"]
        elif session_phase == "ATTENDANCE_OPEN" and workflow_status in {"MARKED_PENDING_TRACKING", "TRACKING_ACTIVE", "PROVISIONAL"}:
            attendance_state = "Temporarily Marked"
            countdown_state = "open"
            countdown_text = (
                f"Attendance closes in: {_format_timer_delta(attendance_seconds_left or 0)}. "
                "GPS tracking will begin after the attendance window closes."
            )
        elif session_phase == "GPS_TRACKING" or workflow_status == "TRACKING_ACTIVE":
            attendance_state = "Temporarily Marked"
            countdown_state = "marked"
            countdown_text = (
                f"Tracking Time Left: {_format_timer_delta(gps_seconds_left or tracking_snapshot['remaining_seconds'])}. "
                "Attendance is temporarily marked while GPS verification is running."
            )
        else:
            attendance_state = "Attendance Marked Successfully" if workflow_status in {"FINALIZED", "FINAL"} else "Temporarily Marked"
            countdown_state = "marked"
            if tracking_snapshot["tracking_state"] == "Tracking Active":
                countdown_text = (
                    f"Tracking Time Left: {_format_timer_delta(tracking_snapshot['remaining_seconds'])}. "
                    f"Attendance is still provisional as {tracking_snapshot['final_status']}."
                )
            else:
                countdown_text = tracking_snapshot["tracking_message"] or tracking_snapshot["message"]
        countdown_target = ""
        can_mark = False
    elif raw_status == "Cancelled":
        attendance_state = "Closed"
        countdown_state = "closed"
        countdown_text = "Attendance window closed"
        countdown_target = ""
        can_mark = False
    elif current_time < open_dt:
        attendance_state = "Not Open Yet"
        countdown_state = "upcoming"
        countdown_text = f"Attendance opens in: {_format_timer_delta((open_dt - current_time).total_seconds())}"
        countdown_target = open_dt.strftime("%Y-%m-%dT%H:%M:%S")
        can_mark = False
    elif open_dt <= current_time <= late_dt and raw_status not in {"Completed", "Cancelled"}:
        attendance_state = "Open"
        countdown_state = "open"
        countdown_text = f"Attendance closes in: {_format_timer_delta((late_dt - current_time).total_seconds())}"
        countdown_target = late_dt.strftime("%Y-%m-%dT%H:%M:%S")
        can_mark = True
    else:
        attendance_state = "Closed"
        countdown_state = "closed"
        countdown_text = "Attendance window closed"
        countdown_target = ""
        can_mark = False

    return {
        "id": session_row.get("id"),
        "schedule_id": session_row.get("schedule_id"),
        "class_name": session_row.get("class_name", ""),
        "subject_name": session_row.get("subject_name", ""),
        "teacher_name": session_row.get("teacher_name", ""),
        "room_name": session_row.get("room_name") or session_row.get("class_name") or "Room not set",
        "session_date": session_row.get("session_date"),
        "day_name": session_row.get("day_name") or session_date.strftime("%A"),
        "start_time": session_row.get("start_time", "")[:5],
        "end_time": session_row.get("end_time", "")[:5],
        "attendance_open_time": session_row.get("attendance_open_time", "")[:5],
        "attendance_close_time": session_row.get("attendance_close_time", "")[:5],
        "late_close_time": session_row.get("late_close_time", "")[:5],
        "session_status": raw_status,
        "class_status": class_status,
        "attendance_state": attendance_state,
        "countdown_state": countdown_state,
        "countdown_text": countdown_text,
        "countdown_target": countdown_target,
        "can_mark_attendance": can_mark,
        "status_reason": session_row.get("status_reason", ""),
        "gps_enabled": reference_config["gps_enabled"],
        "gps_latitude": reference_config["latitude"],
        "gps_longitude": reference_config["longitude"],
        "allowed_radius_meters": reference_config["allowed_radius_meters"],
        "post_attendance_tracking_minutes": get_session_tracking_minutes(session_row),
        "attendance_record_status": record["status"] if record else "",
        "attendance_workflow_status": tracking_snapshot["attendance_status"] if record else "",
        "session_phase": tracking_snapshot.get("phase", "CLOSED"),
        "attendance_seconds_left": tracking_snapshot.get("attendance_seconds_left"),
        "gps_seconds_left": tracking_snapshot.get("gps_seconds_left"),
        "tracking_status": tracking_snapshot["tracking_state"] if record else "",
        "tracking_status_message": tracking_snapshot["tracking_message"] if record else "",
        "tracking_remaining_seconds": tracking_snapshot["remaining_seconds"] if record else 0,
        "tracking_expires_at": tracking_snapshot["tracking_expires_at"] if record else "",
        "cancellation_reason": tracking_snapshot["message"] if record and tracking_snapshot["attendance_status"] == "CANCELLED" else "",
        "present_count": session_row.get("present_count", 0),
        "late_count": session_row.get("late_count", 0),
        "absent_count": session_row.get("absent_count", 0),
        "provisional_count": session_row.get("provisional_count", 0),
        "final_count": session_row.get("final_count", 0),
        "tracking_active_count": session_row.get("tracking_active_count", 0),
        "tracking_cancelled_count": session_row.get("tracking_cancelled_count", 0),
        "rejected_count": session_row.get("rejected_count", 0),
    }


def build_student_schedule_payload(student_id, target_date):
    target = parse_portal_date(target_date)
    rows = [build_session_ui_payload(item, student_id=student_id) for item in get_student_sessions(student_id, target)]
    rows.sort(
        key=lambda item: (
            0 if item["class_status"] == "Active" else 1,
            0 if item["attendance_state"] == "Open" else 1,
            item["start_time"],
        )
    )
    return rows


def build_admin_schedule_payload(target_date):
    target = parse_portal_date(target_date)
    rows = [build_session_ui_payload(item) for item in list_class_sessions(target_date=target.strftime("%Y-%m-%d"), days=1, allow_completion=False)]
    rows.sort(
        key=lambda item: (
            0 if item["class_status"] == "Active" else 1,
            item["start_time"],
            item["class_name"],
        )
    )
    return rows


sync_legacy_known_faces()
refresh_runtime_state(rebuild_engines=False)
start_background_engine_bootstrap()


@app.route("/")
def home():
    if session.get("admin_logged_in"):
        return redirect(url_for("dashboard"))
    if session.get("student_logged_in"):
        return redirect(url_for("student_dashboard"))
    return render_template(
        "home.html",
        assistant_name=ASSISTANT_NAME,
        project_owner=DEFAULT_OWNER,
    )


@app.route("/access-portal")
def access_portal():
    return render_template(
        "home.html",
        assistant_name=ASSISTANT_NAME,
        project_owner=DEFAULT_OWNER,
    )


@app.route("/portal-access/student")
def student_portal_access():
    session.clear()
    flash("Please log in with student credentials.", "info")
    return redirect(url_for("student_login"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if session.get("student_logged_in"):
        return redirect(url_for("student_dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "").strip()

        admin = verify_admin_credentials(email, password)
        if admin or (email == ADMIN_EMAIL.lower() and password == ADMIN_PASSWORD):
            session["admin_logged_in"] = True
            session["admin_id"] = admin["id"] if admin else get_primary_admin()["id"]
            session["admin_email"] = admin["email"] if admin else email
            flash("Admin login successful.", "success")
            return redirect(url_for("dashboard"))

        flash("Invalid admin email or password.", "danger")

    primary_admin = get_primary_admin()

    return render_template(
        "login.html",
        admin_email_hint=primary_admin["email"] if primary_admin else ADMIN_EMAIL,
        assistant_name=ASSISTANT_NAME,
        project_owner=DEFAULT_OWNER,
    )


@app.route("/student-login", methods=["GET", "POST"])
def student_login():
    if session.get("student_logged_in"):
        return redirect(url_for("student_dashboard"))
    if session.get("admin_logged_in"):
        session.clear()

    if request.method == "POST":
        email = normalize_email_address(request.form.get("email", ""))
        enrollment_number = request.form.get("enrollment_number", "").strip().upper()
        student = verify_student_credentials(email, enrollment_number)
        if student:
            session.clear()
            session["student_logged_in"] = True
            session["student_id"] = student["id"]
            session["student_name"] = student["name"]
            flash("Student login successful.", "success")
            return redirect(url_for("student_dashboard"))

        flash("Invalid student email or enrollment number.", "danger")

    return render_template(
        "student_login.html",
        assistant_name=ASSISTANT_NAME,
        project_owner=DEFAULT_OWNER,
    )


@app.route("/logout")
def logout():
    if not session.get("admin_logged_in") and not session.get("student_logged_in"):
        return redirect(url_for("home"))
    session.clear()
    flash("Logged out successfully.", "success")
    return redirect(url_for("home"))


@app.route("/dashboard")
@admin_required
def dashboard():
    return render_template("dashboard.html", **get_dashboard_context())


@app.route("/admin/control-panel")
@admin_required
def admin_control_panel():
    return render_template("admin_control_panel.html", **get_common_context())


@app.route("/admin/analytics")
@admin_required
def admin_analytics():
    return render_template("admin_analytics.html", **get_admin_analytics_context())


@app.route("/admin/sessions")
@admin_required
def admin_sessions():
    return render_template("admin_sessions.html", **get_admin_sessions_context())


@app.route("/admin/live-monitor")
@admin_required
def admin_live_monitor():
    return render_template("admin_live_monitor.html", **get_admin_live_monitor_context())


@app.route("/admin/overrides")
@admin_required
def admin_overrides():
    return render_template("admin_overrides.html", **get_admin_overrides_context())


@app.route("/admin/holidays")
@admin_required
def admin_holidays():
    return render_template("admin_holidays.html", **get_admin_holidays_context())


@app.route("/admin/reports")
@admin_required
def admin_reports():
    return render_template("admin_reports.html", **get_admin_reports_context())


@app.route("/admin/settings")
@admin_required
def admin_settings():
    return render_template("admin_settings.html", **get_admin_settings_context())


@app.route("/student-dashboard")
@student_required
def student_dashboard():
    return render_student_portal_page("student_dashboard.html")


@app.route("/student/self-attendance")
@student_required
def student_self_attendance():
    student = get_student_by_id(session.get("student_id"))
    if not student:
        session.clear()
        flash("Student account not found. Please log in again.", "warning")
        return redirect(url_for("student_login"))
    clear_student_attendance_preview(student["id"])
    return render_template("student_self_attendance.html", **build_self_attendance_context(student))


@app.route("/student/schedule")
@student_required
def student_schedule_page():
    return render_student_portal_page("student_schedule.html")


@app.route("/student/history")
@student_required
def student_history_page():
    return render_student_portal_page("student_history.html")


@app.route("/student/analytics")
@student_required
def student_analytics_page():
    return render_student_portal_page("student_analytics.html")


@app.route("/student/corrections")
@student_required
def student_corrections_page():
    return render_student_portal_page("student_corrections.html")


@app.route("/api/student/schedule")
@student_required
def student_schedule_api():
    finalize_expired_attendance_tracking()
    student = get_student_by_id(session.get("student_id"))
    if not student:
        return jsonify({"success": False, "message": "Student account not found."}), 404
    target = parse_portal_date(request.args.get("date"))
    return jsonify(
        {
            "success": True,
            "selected_date": target.strftime("%Y-%m-%d"),
            "week_dates": build_week_dates(target),
            "sessions": build_student_schedule_payload(student["id"], target),
        }
    )


@app.route("/api/student/tracking-status")
@student_required
def student_tracking_status_api():
    try:
        process_auto_attendance_tasks()
        student = get_student_by_id(session.get("student_id"))
        if not student:
            return jsonify({"success": False, "message": "Student account not found."}), 404

        session_id_raw = request.args.get("session_id", "").strip()
        try:
            session_id = int(session_id_raw) if session_id_raw else None
        except ValueError:
            return jsonify({"success": False, "message": "Invalid session id."}), 400

        runtime_context = get_student_attendance_runtime_context(
            student["id"],
            requested_session_id=session_id,
        )
        locked_tracking_row = runtime_context.get("locked_tracking_row")
        locked_tracking_session = runtime_context.get("locked_tracking_session")
        session_context = runtime_context.get("session_context") or {}
        portal_focus_session = resolve_student_portal_focus_session(
            session_context,
            locked_session=locked_tracking_session,
        )

        tracking_row = None
        tracking_session = None
        if locked_tracking_row and (
            session_id is None or int(locked_tracking_row.get("session_id") or 0) == session_id
        ):
            tracking_row = locked_tracking_row
            tracking_session = locked_tracking_session
            session_id = int(locked_tracking_row.get("session_id") or 0) or session_id
        else:
            if session_id is None and portal_focus_session and portal_focus_session.get("id"):
                session_id = int(portal_focus_session["id"])
            if session_id is not None:
                tracking_row = get_student_tracking_record(student["id"], session_id=session_id)
                tracking_session = get_session_by_id(session_id)

        if is_generic_gps_placeholder_record(tracking_session, tracking_row):
            tracking_row = None

        return jsonify(
            {
                "success": True,
                "tracking": build_tracking_snapshot(
                    student,
                    tracking_session or portal_focus_session or tracking_row,
                    tracking_row,
                ),
            }
        )
    except Exception as error:
        log_route_exception("student-tracking-status", error)
        return build_api_error_response("Tracking status is temporarily unavailable. Please refresh and try again.")


@app.route("/api/student/tracking-heartbeat", methods=["POST"])
@student_required
def student_tracking_heartbeat_api():
    try:
        process_auto_attendance_tasks()
        student = get_student_by_id(session.get("student_id"))
        if not student:
            return jsonify({"success": False, "message": "Student account not found."}), 404

        data = request.get_json(silent=True) or {}
        attendance_id = data.get("attendance_id")
        session_id = data.get("session_id")
        latitude = data.get("latitude")
        longitude = data.get("longitude")
        accuracy_meters = data.get("accuracy_meters")
        position_timestamp_ms = data.get("position_timestamp_ms")

        if latitude is None or longitude is None:
            return jsonify({"success": False, "message": "GPS coordinates are required."}), 400

        try:
            attendance_id = int(attendance_id) if attendance_id else None
            session_id = int(session_id) if session_id else None
            latitude = coerce_gps_coordinate(latitude, "Student latitude")
            longitude = coerce_gps_coordinate(longitude, "Student longitude")
            accuracy_meters = coerce_gps_accuracy_meters(accuracy_meters)
            position_timestamp_ms = coerce_position_timestamp_ms(position_timestamp_ms)
        except (TypeError, ValueError) as error:
            return jsonify({"success": False, "message": sanitize_text(error)}), 400

        tracking_record = None
        if attendance_id:
            tracking_record = get_attendance_record_by_id(attendance_id)
            if tracking_record and tracking_record.get("student_id") != student["id"]:
                return jsonify({"success": False, "message": "Tracking record access denied."}), 403
        elif session_id:
            tracking_record = get_effective_attendance_record(student["id"], session_id)

        if not tracking_record:
            return jsonify({"success": False, "message": "No attendance record found for tracking."}), 404

        tracking_session = (
            get_session_by_id(tracking_record["session_id"])
            if tracking_record.get("session_id")
            else None
        )
        if not tracking_session:
            return jsonify({"success": False, "message": "Class session not found for tracking."}), 404

        if tracking_record.get("tracking_status") != "Tracking Active":
            return jsonify(
                {
                    "success": True,
                    "tracking": build_tracking_snapshot(student, tracking_session, tracking_record),
                }
            )

        reference_config = get_tracking_reference_config(
            attendance_record=tracking_record,
            session_row=tracking_session,
        )
        if not reference_config["gps_enabled"]:
            tracking_record = start_attendance_tracking(
                tracking_record["id"],
                0,
                latitude=latitude,
                longitude=longitude,
            )
            return jsonify(
                {
                    "success": True,
                    "tracking": build_tracking_snapshot(student, tracking_session, tracking_record),
                }
            )

        distance_payload = compute_effective_gps_distance_meters(
            latitude,
            longitude,
            reference_config["latitude"],
            reference_config["longitude"],
            accuracy_meters=accuracy_meters,
            position_timestamp_ms=position_timestamp_ms,
        )
        effective_radius_meters = (
            reference_config["allowed_radius_meters"]
            if reference_config["allowed_radius_meters"] is not None
            else get_effective_session_radius_meters(tracking_session)
        )
        tracking_distance_decision = compute_tracking_distance_decision(
            distance_payload,
            effective_radius_meters,
        )
        distance_meters = tracking_distance_decision["distance_meters"]
        raw_distance_meters = tracking_distance_decision["raw_distance_meters"]
        range_state = tracking_distance_decision["range_state"]
        is_in_range = tracking_distance_decision["is_in_range"]
        effective_tracking_radius_meters = tracking_distance_decision["effective_radius_meters"]
        app.logger.info(
            "tracking-heartbeat student_id=%s attendance_id=%s session_id=%s student_gps=(%s,%s) session_gps=(%s,%s) raw_distance_m=%.2f display_distance_m=%.2f radius_m=%.2f same_location_tolerance_m=%.2f buffer_m=%.2f effective_radius_m=%.2f accuracy_m=%.2f reading_age_s=%s range_state=%s",
            student["id"],
            tracking_record["id"],
            tracking_session["id"],
            latitude,
            longitude,
            reference_config["latitude"],
            reference_config["longitude"],
            raw_distance_meters,
            distance_meters,
            effective_radius_meters,
            tracking_distance_decision["same_location_tolerance_meters"],
            tracking_distance_decision["jitter_buffer_meters"],
            effective_tracking_radius_meters,
            tracking_distance_decision["accuracy_meters"],
            f"{tracking_distance_decision['reading_age_seconds']:.1f}" if tracking_distance_decision["reading_age_seconds"] is not None else "n/a",
            range_state,
        )
        tracking_record = apply_attendance_tracking_heartbeat(
            tracking_record["id"],
            latitude,
            longitude,
            is_in_range=is_in_range,
            distance_meters=distance_meters,
            raw_distance_meters=raw_distance_meters,
            accuracy_meters=accuracy_meters,
            cancellation_reason=build_tracking_cancellation_message(student, tracking_session),
            cancel_threshold=GPS_TRACKING_OUT_OF_RANGE_LIMIT,
            range_state=range_state,
        )
        tracking_snapshot = build_tracking_snapshot(student, tracking_session, tracking_record)
        app.logger.info(
            "tracking-heartbeat-result attendance_id=%s out_of_range_count=%s tracking_state=%s attendance_status=%s",
            tracking_record["id"],
            tracking_record.get("out_of_range_count"),
            tracking_snapshot.get("tracking_state"),
            tracking_snapshot.get("attendance_status"),
        )

        if range_state == "out_of_range" and tracking_snapshot["tracking_state"] == "Tracking Active":
            tracking_snapshot["message"] = (
                f"GPS warning {tracking_snapshot['out_of_range_count']} of {tracking_snapshot['out_of_range_limit']}: "
                "you are outside the allowed GPS range. Move back in range to avoid cancellation."
            )
            tracking_snapshot["gps_state"] = "GPS_VALID_OUT_OF_RANGE"
            tracking_snapshot["gps_status_text"] = "Outside allowed area"
        elif range_state == "uncertain" and tracking_snapshot["tracking_state"] == "Tracking Active":
            tracking_snapshot["message"] = build_gps_accuracy_warning(tracking_distance_decision)
            tracking_snapshot["gps_state"] = "GPS_LOW_SIGNAL"
            tracking_snapshot["gps_status_text"] = "GPS signal is weak, tracking continues"
        elif tracking_snapshot["tracking_state"] == "Tracking Active":
            tracking_snapshot["gps_state"] = "GPS_VALID_IN_RANGE"
            tracking_snapshot["gps_status_text"] = "Tracking live"

        display_distance_meters = tracking_snapshot.get("distance_meters")
        display_raw_distance_meters = tracking_snapshot.get("raw_distance_meters")
        display_accuracy_meters = tracking_snapshot.get("gps_accuracy_meters")
        if range_state == "out_of_range":
            display_distance_meters = distance_meters
            display_raw_distance_meters = raw_distance_meters
            display_accuracy_meters = distance_payload["accuracy_meters"]

        return jsonify(
            {
                "success": True,
                "tracking": tracking_snapshot,
                "distance_meters": round(display_distance_meters, 2) if display_distance_meters is not None else None,
                "raw_distance_meters": round(display_raw_distance_meters, 2) if display_raw_distance_meters is not None else None,
                "gps_accuracy_meters": round(display_accuracy_meters, 2) if display_accuracy_meters is not None else None,
                "student_lat": latitude,
                "student_lng": longitude,
                "admin_lat": reference_config["latitude"],
                "admin_lng": reference_config["longitude"],
                "allowed_radius_meters": round(effective_radius_meters, 2),
                "effective_tracking_radius_meters": round(effective_tracking_radius_meters, 2),
                "same_location_tolerance_meters": round(tracking_distance_decision["same_location_tolerance_meters"], 2),
                "range_state": range_state,
                "in_range": is_in_range,
                "gps_state": tracking_snapshot.get("gps_state"),
                "gps_status_text": tracking_snapshot.get("gps_status_text"),
            }
        )
    except Exception as error:
        log_route_exception("student-tracking-heartbeat", error)
        return build_api_error_response(
            "GPS tracking update failed temporarily. Please keep the page open and try again in a moment."
        )


@app.route("/api/admin/schedule")
@admin_required
def admin_schedule_api():
    process_auto_attendance_tasks()
    target = parse_portal_date(request.args.get("date"))
    return jsonify(
        {
            "success": True,
            "selected_date": target.strftime("%Y-%m-%d"),
            "week_dates": build_week_dates(target),
            "sessions": build_admin_schedule_payload(target),
        }
    )


@app.route("/api/admin/live-tracking")
@admin_required
def admin_live_tracking_api():
    process_auto_attendance_tasks()
    target = parse_portal_date(request.args.get("date"))
    return jsonify(
        {
            "success": True,
            "selected_date": target.strftime("%Y-%m-%d"),
            "tracking_rows": build_admin_tracking_rows(target_date=target),
        }
    )


@app.route("/admin/working-days", methods=["POST"])
@admin_required
def save_working_days():
    update_working_days({day: request.form.get(f"day_{day}") == "on" for day in DAY_NAMES})
    flash("Working days updated successfully.", "success")
    return redirect_admin_next("admin_control_panel")


@app.route("/admin/settings/low-attendance", methods=["POST"])
@admin_required
def save_low_attendance_threshold():
    threshold = request.form.get("low_attendance_threshold", "75").strip()
    try:
        value = max(0.0, min(100.0, float(threshold)))
    except ValueError:
        flash("Low attendance threshold must be a valid number.", "danger")
        return redirect_admin_next("admin_control_panel")
    set_app_setting("low_attendance_threshold", value)
    flash("Low attendance threshold updated.", "success")
    return redirect_admin_next("admin_control_panel")


@app.route("/admin/settings/post-attendance-tracking", methods=["POST"])
@admin_required
def save_post_attendance_tracking_default():
    raw_value = request.form.get("post_attendance_tracking_default_minutes", "5").strip()
    try:
        value = max(0, min(180, int(float(raw_value))))
    except ValueError:
        flash("Post-attendance tracking duration must be a valid number of minutes.", "danger")
        return redirect_admin_next("admin_control_panel")
    set_app_setting("post_attendance_tracking_default_minutes", value)
    flash("Default post-attendance tracking duration updated.", "success")
    return redirect_admin_next("admin_control_panel")


@app.route("/admin/holidays", methods=["POST"])
@admin_required
def create_holiday():
    holiday_date = request.form.get("holiday_date", "").strip()
    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    if not holiday_date or not title:
        flash("Holiday date and title are required.", "danger")
        return redirect_admin_next("admin_holidays")
    add_holiday(holiday_date, title, description)
    flash("Holiday saved successfully.", "success")
    return redirect_admin_next("admin_holidays")


@app.route("/admin/holidays/<int:holiday_id>/delete", methods=["POST"])
@admin_required
def remove_holiday(holiday_id):
    delete_holiday(holiday_id)
    flash("Holiday deleted successfully.", "success")
    return redirect_admin_next("admin_holidays")


@app.route("/admin/schedules", methods=["POST"])
@admin_required
def create_schedule():
    try:
        form_data = build_schedule_form_data(request.form)
    except ValueError:
        flash("Please enter a valid session date, GPS coordinates, and radius.", "danger")
        return redirect_admin_next("admin_control_panel")

    if not all(form_data[key] for key in ("class_name", "subject_name", "teacher_name", "session_date")):
        flash("Class, subject, teacher, and session date are required.", "danger")
        return redirect_admin_next("admin_control_panel")

    try:
        create_class_schedule(form_data)
    except Exception as error:
        log_route_exception("admin-create-schedule", error)
        flash("Class session could not be saved. Please verify the timing and GPS details, then try again.", "danger")
        return redirect_admin_next("admin_control_panel")
    queue_new_schedule_notifications(form_data)
    flash("Class session saved successfully for the selected date. Student email notifications are being sent.", "success")
    return redirect_admin_next("admin_control_panel")


@app.route("/admin/schedules/<int:schedule_id>/update", methods=["POST"])
@admin_required
def edit_schedule(schedule_id):
    try:
        form_data = build_schedule_form_data(request.form)
    except ValueError:
        flash("Please enter a valid session date, GPS coordinates, and radius.", "danger")
        return redirect_admin_next("admin_sessions")

    try:
        update_class_schedule(schedule_id, form_data, session.get("admin_id"))
    except Exception as error:
        log_route_exception("admin-edit-schedule", error)
        flash("Class session could not be updated. Please verify the timing and GPS details, then try again.", "danger")
        return redirect_admin_next("admin_sessions")
    flash("Class session updated successfully.", "success")
    return redirect_admin_next("admin_sessions")


@app.route("/admin/schedules/<int:schedule_id>/delete", methods=["POST"])
@admin_required
def remove_schedule(schedule_id):
    delete_class_schedule(schedule_id)
    flash("Dated class session deleted successfully.", "success")
    return redirect_admin_next("admin_sessions")


@app.route("/admin/sessions/<int:session_id>/status", methods=["POST"])
@admin_required
def save_session_status(session_id):
    status = request.form.get("session_status", "Scheduled").strip().title()
    status_reason = request.form.get("status_reason", "").strip()
    substitute_teacher = request.form.get("substitute_teacher", "").strip()
    open_time = request.form.get("attendance_open_time", "").strip()
    close_time = request.form.get("attendance_close_time", "").strip()
    late_time = request.form.get("late_close_time", "").strip()
    tracking_raw = request.form.get("post_attendance_tracking_minutes", "").strip()
    tracking_minutes = None
    if tracking_raw:
        try:
            tracking_minutes = max(0, min(180, int(float(tracking_raw))))
        except ValueError:
            flash("Tracking duration must be a valid number of minutes.", "danger")
            return redirect_admin_next("admin_sessions")
    update_class_session_status(
        session_id,
        status,
        status_reason=status_reason,
        attendance_open_time=(open_time + ":00") if open_time else None,
        attendance_close_time=(close_time + ":00") if close_time else None,
        late_close_time=(late_time + ":00") if late_time else None,
        substitute_teacher=substitute_teacher,
        activated_by=session.get("admin_id"),
        post_attendance_tracking_minutes=tracking_minutes,
    )
    flash("Session status updated successfully.", "success")
    return redirect_admin_next("admin_sessions")


@app.route("/admin/sessions/<int:session_id>/gps", methods=["POST"])
@admin_required
def save_session_gps(session_id):
    source = request.form.get("gps_source", "manual")
    try:
        latitude = coerce_gps_coordinate(request.form.get("gps_latitude"), "Admin latitude")
        longitude = coerce_gps_coordinate(request.form.get("gps_longitude"), "Admin longitude")
        radius = normalize_allowed_radius_meters(request.form.get("allowed_radius_meters", "100"))
    except (TypeError, ValueError):
        flash("Valid latitude, longitude, and radius are required.", "danger")
        return redirect_admin_next("admin_sessions")

    try:
        updated = update_session_gps(session_id, latitude, longitude, radius, session.get("admin_id"))
    except Exception as error:
        log_route_exception("admin-update-session-gps", error)
        flash("Session GPS could not be updated. Please verify the coordinates and try again.", "danger")
        return redirect_admin_next("admin_sessions")
    if not updated:
        flash("Class session not found.", "danger")
        return redirect_admin_next("admin_sessions")
    flash(
        f"Session GPS updated successfully using {'live browser location' if source == 'live' else 'manual entry'}.",
        "success",
    )
    return redirect_admin_next("admin_sessions")


@app.route("/admin/sessions/<int:session_id>/gps/clear", methods=["POST"])
@admin_required
def clear_session_gps(session_id):
    session_row = get_session_by_id(session_id)
    if not session_row:
        flash("Class session not found.", "danger")
        return redirect_admin_next("admin_sessions")

    live_statuses = {"Active", "Delayed"}
    session_status = str(session_row.get("session_status") or "Scheduled").strip().title()
    confirmation_phrase = request.form.get("active_gps_clear_confirmation", "").strip().upper()

    if session_status in live_statuses and confirmation_phrase != "CLEAR ACTIVE GPS":
        flash(
            "To clear GPS for an active session, type CLEAR ACTIVE GPS and submit again.",
            "danger",
        )
        return redirect_admin_next("admin_sessions")

    radius = session_row.get("allowed_radius_meters") or MAX_ATTENDANCE_DISTANCE_METERS
    update_session_gps(session_id, None, None, radius, session.get("admin_id"))
    flash(
        "Live session GPS was cleared after explicit confirmation."
        if session_status in live_statuses
        else "Session GPS cleared successfully.",
        "warning" if session_status in live_statuses else "success",
    )
    return redirect_admin_next("admin_sessions")


@app.route("/admin/sessions/<int:session_id>/delete", methods=["POST"])
@admin_required
def remove_session(session_id):
    success, message = delete_class_session(session_id, session.get("admin_id"))
    flash(message, "success" if success else "warning")
    return redirect_admin_next("admin_sessions")


@app.route("/admin/overrides", methods=["POST"])
@admin_required
def create_override():
    student_id = request.form.get("student_id", "").strip()
    session_id = request.form.get("session_id", "").strip()
    reason = request.form.get("reason", "").strip()
    if not student_id or not session_id or not reason:
        flash("Student, session, and override reason are required.", "danger")
        return redirect_admin_next("admin_overrides")
    grant_override(int(student_id), int(session_id), session.get("admin_id"), reason, valid_minutes=5)
    flash("5-minute student-specific override granted successfully.", "success")
    return redirect_admin_next("admin_overrides")


@app.route("/mark_attendance", methods=["POST"])
@app.route("/student-attendance", methods=["POST"])
@guarded_json_route("student-mark-attendance", "Attendance could not be saved right now. Please try again.")
@student_required
def mark_student_attendance_route():
    student = get_student_by_id(session.get("student_id"))
    if not student:
        return jsonify({"success": False, "message": "Student account not found."}), 404

    data = request.get_json(silent=True) or {}
    session_id_raw = str(data.get("session_id") or "").strip()
    try:
        requested_session_id = int(session_id_raw) if session_id_raw else None
    except ValueError:
        return jsonify({"success": False, "message": "Invalid session id."}), 400

    runtime_context = get_student_attendance_runtime_context(
        student["id"],
        requested_session_id=requested_session_id,
    )
    session_context = runtime_context.get("session_context") or {}
    current_session = runtime_context.get("current_session")
    locked_tracking_row = runtime_context.get("locked_tracking_row")
    locked_tracking_session = runtime_context.get("locked_tracking_session")
    preview_state, preview_session = get_locked_student_preview_session(
        student["id"],
        requested_session_id=requested_session_id,
    )
    app.logger.info(
        "attendance-mark-request student_id=%s requested_session_id=%s current_session_id=%s upcoming_session_id=%s",
        student["id"],
        requested_session_id,
        current_session.get("id") if current_session else None,
        session_context.get("upcoming_session", {}).get("id") if session_context.get("upcoming_session") else None,
    )
    if locked_tracking_row and locked_tracking_session:
        clear_student_attendance_preview(student["id"])
        tracking_snapshot = build_tracking_snapshot(student, locked_tracking_session, locked_tracking_row)
        return jsonify(
            {
                "success": False,
                "message": tracking_snapshot["message"] or "Attendance is already marked for this class.",
                "session_id": locked_tracking_row["session_id"],
                "subject_name": tracking_snapshot["subject_name"],
                "status": tracking_snapshot["attendance_status"],
                "final_status": tracking_snapshot["final_status"],
                "tracking": tracking_snapshot,
            }
        ), 409

    effective_session = None
    if preview_session and requested_session_id in {None, preview_session.get("id")}:
        effective_session = preview_session
    elif current_session:
        effective_session = ensure_materialized_student_session(current_session)

    if not effective_session:
        upcoming_session = session_context.get("upcoming_session")
        if upcoming_session:
            return jsonify(
                {
                    "success": False,
                    "message": (
                        f"No class is open right now. Your next session is "
                        f"{upcoming_session['subject_name']} on {upcoming_session['session_date']} "
                        f"from {upcoming_session['start_time'][:5]} to {upcoming_session['end_time'][:5]}."
                    ),
                    "subject_name": upcoming_session["subject_name"],
                }
            ), 400
        return jsonify({"success": False, "message": "No active class session is open for attendance."}), 400

    current_session = effective_session
    process_auto_attendance_tasks()
    if requested_session_id is not None and current_session.get("id") != requested_session_id:
        return jsonify(
            {
                "success": False,
                "message": "The analyzed session changed. Please capture and analyze again before marking attendance.",
            }
        ), 400

    required_window_fields = (
        "attendance_open_time",
        "attendance_close_time",
        "late_close_time",
    )
    if any(not str(current_session.get(field) or "").strip() for field in required_window_fields):
        return jsonify(
            {
                "success": False,
                "message": "Attendance is closed now because the admin has not configured the attendance timing yet.",
                "subject_name": current_session.get("subject_name", "N/A"),
            }
        ), 400

    if current_session["session_status"] not in {"Active", "Delayed", "Scheduled"}:
        return jsonify({"success": False, "message": f"Attendance is not open because this session is {current_session['session_status']}."}), 400

    existing = get_existing_session_attendance(
        student["id"],
        current_session["id"],
        current_session.get("session_date"),
    )
    if existing:
        clear_student_attendance_preview(student["id"])
        tracking_snapshot = build_tracking_snapshot(student, current_session, existing)
        return jsonify(
            {
                "success": False,
                "message": "Attendance is already marked for this class.",
                "session_id": current_session["id"],
                "subject_name": current_session["subject_name"],
                "status": tracking_snapshot["attendance_status"],
                "final_status": tracking_snapshot["final_status"],
                "tracking": tracking_snapshot,
            }
        ), 409

    preview_state = get_student_attendance_preview(
        student_id=student["id"],
        session_id=current_session["id"],
        require_fresh=True,
    )
    if not preview_state:
        return jsonify(
            {
                "success": False,
                "message": "Capture and analyze your face, liveness, and emotion before marking attendance.",
                "session_id": current_session["id"],
                "subject_name": current_session["subject_name"],
                "tracking": build_tracking_snapshot(student, current_session, None),
            }
        ), 400

    identified_name = str(preview_state.get("identified_name") or "").strip()
    emotion_label = normalize_attendance_emotion_label(preview_state.get("emotion"))
    liveness_label = str(preview_state.get("liveness_label") or "Unknown").strip() or "Unknown"
    latitude = preview_state.get("latitude")
    longitude = preview_state.get("longitude")
    accuracy_meters = preview_state.get("accuracy_meters")
    position_timestamp_ms = preview_state.get("position_timestamp_ms")
    proof_snapshot_path = preview_state.get("proof_snapshot_path", "")

    if not preview_state.get("liveness_passed"):
        return jsonify(
            {
                "success": False,
                "message": "Liveness verification is not ready. Please capture and analyze again.",
                "subject_name": current_session["subject_name"],
                "tracking": build_tracking_snapshot(student, current_session, None),
            }
        ), 400

    if not identified_name or identified_name.strip().lower() != student["name"].strip().lower():
        clear_student_attendance_preview(student["id"])
        return jsonify(
            {
                "success": False,
                "message": "Face recognition did not match the logged-in student. Please capture and analyze again.",
                "identified_name": identified_name,
                "emotion": emotion_label,
                "liveness_label": liveness_label,
                "subject_name": current_session["subject_name"],
                "tracking": build_tracking_snapshot(student, current_session, None),
            }
        ), 403

    distance_meters = None
    raw_distance_meters = None
    distance_decision = {}
    effective_radius_meters = None

    now_dt = datetime.now()
    close_dt = datetime.combine(now_dt.date(), datetime.strptime(current_session["attendance_close_time"], "%H:%M:%S").time())
    late_dt = datetime.combine(now_dt.date(), datetime.strptime(current_session["late_close_time"], "%H:%M:%S").time())
    override = get_valid_override(student["id"], current_session["id"], now_dt)
    if now_dt > late_dt and not override:
        return jsonify(
            {
                "success": False,
                "message": "Attendance window is closed.",
                "identified_name": identified_name,
                "emotion": emotion_label,
                "liveness_label": liveness_label,
                "distance_meters": round(distance_meters, 2) if distance_meters is not None else None,
                "raw_distance_meters": round(raw_distance_meters, 2) if raw_distance_meters is not None else None,
                "subject_name": current_session["subject_name"],
                "tracking": build_tracking_snapshot(student, current_session, None),
            }
        ), 403

    tracking_minutes = (
        get_session_tracking_minutes(current_session)
        if current_session.get("gps_latitude") is not None and current_session.get("gps_longitude") is not None
        else 0
    )
    final_status = "Present" if now_dt <= close_dt else "Late"
    if override:
        mark_override_used(override["id"])

    attendance_record_id = mark_attendance(
        student["name"],
        emotion_label,
        student_id=student["id"],
        session_id=current_session["id"],
        status="Provisional",
        original_status=final_status,
        attendance_status="MARKED_PENDING_TRACKING",
        face_verified=True,
        spoof_status="passed",
        latitude=latitude,
        longitude=longitude,
        distance_meters=distance_meters,
        marked_via="student_self",
        override_permission_id=override["id"] if override else None,
        override_granted_by=override["granted_by"] if override else None,
        override_used=bool(override),
        proof_snapshot_path=proof_snapshot_path,
        recorded_identity_name=identified_name or student["name"],
        tracking_status="WAITING_FOR_WINDOW_CLOSE",
        tracking_active=False,
        last_location_latitude=latitude,
        last_location_longitude=longitude,
        last_location_checked_at=now_dt.strftime("%Y-%m-%d %H:%M:%S"),
    )
    if not attendance_record_id:
        existing = get_existing_session_attendance(
            student["id"],
            current_session["id"],
            current_session.get("session_date"),
        )
        if existing:
            clear_student_attendance_preview(student["id"])
            tracking_snapshot = build_tracking_snapshot(student, current_session, existing)
            return jsonify(
                {
                    "success": False,
                    "message": "Attendance is already marked for this class.",
                    "session_id": current_session["id"],
                    "subject_name": current_session["subject_name"],
                    "status": tracking_snapshot["attendance_status"],
                    "final_status": tracking_snapshot["final_status"],
                    "tracking": tracking_snapshot,
                }
            ), 409
        return jsonify({"success": False, "message": "Attendance could not be saved for this session."}), 500

    tracking_record = defer_attendance_tracking(
        attendance_record_id,
        latitude=latitude,
        longitude=longitude,
        accuracy_meters=accuracy_meters,
        raw_distance_meters=raw_distance_meters,
        range_state=distance_decision["range_state"] if distance_meters is not None else "in_range",
        marked_at=now_dt,
    )
    tracking_snapshot = build_tracking_snapshot(student, current_session, tracking_record)
    app.logger.info(
        "attendance-mark-success student_id=%s session_id=%s attendance_id=%s status=%s tracking_state=%s tracking_minutes=%s",
        student["id"],
        current_session["id"],
        attendance_record_id,
        tracking_snapshot.get("attendance_status"),
        tracking_snapshot.get("tracking_state"),
        tracking_minutes,
    )
    clear_student_attendance_preview(student["id"])

    return jsonify(
        {
            "success": True,
            "message": "Attendance marked, waiting for GPS verification after attendance window closes.",
            "session_id": current_session["id"],
            "identified_name": identified_name,
            "status": tracking_snapshot["attendance_status"],
            "final_status": tracking_snapshot["final_status"],
            "subject_name": current_session["subject_name"],
            "distance_meters": round(distance_meters, 2) if distance_meters is not None else None,
            "raw_distance_meters": round(raw_distance_meters, 2) if raw_distance_meters is not None else None,
            "gps_accuracy_meters": round(accuracy_meters, 2) if accuracy_meters is not None else None,
            "allowed_radius_meters": round(effective_radius_meters, 2) if distance_meters is not None else None,
            "range_state": distance_decision["range_state"] if distance_meters is not None else "",
            "emotion": emotion_label,
            "liveness_label": liveness_label,
            "tracking": tracking_snapshot,
        }
    )


@app.route("/student-attendance-preview", methods=["POST"])
@guarded_json_route("student-attendance-preview", "Attendance analysis is temporarily unavailable. Please try again.")
@student_required
def student_attendance_preview():
    student = get_student_by_id(session.get("student_id"))
    if not student:
        return jsonify({"success": False, "message": "Student account not found."}), 404

    runtime_context = get_student_attendance_runtime_context(student["id"])
    session_context = runtime_context.get("session_context") or {}
    current_session = runtime_context.get("current_session")
    locked_tracking_row = runtime_context.get("locked_tracking_row")
    locked_tracking_session = runtime_context.get("locked_tracking_session")
    if locked_tracking_row and locked_tracking_session:
        clear_student_attendance_preview(student["id"])
        tracking_snapshot = build_tracking_snapshot(student, locked_tracking_session, locked_tracking_row)
        return jsonify(
            {
                "success": True,
                "message": f"Analysis completed. {tracking_snapshot['message']}",
                "identified_name": student["name"],
                "emotion": "Unknown",
                "liveness_label": "Locked To Active Session",
                "subject_name": tracking_snapshot.get("subject_name") or "N/A",
                "distance_meters": (
                    round(float(tracking_snapshot.get("distance_meters")), 2)
                    if tracking_snapshot.get("distance_meters") is not None
                    else None
                ),
                "recognized": True,
                "recognized_name": student["name"],
                "liveness_passed": True,
                "gps_captured": False,
                "within_radius": True,
                "can_mark_attendance": False,
                "reason": "tracking_in_progress",
                "attendance_open": False,
                "already_marked": True,
                "analysis_ready": False,
                "session_id": locked_tracking_row.get("session_id"),
                "tracking": tracking_snapshot,
            }
        )

    clear_student_attendance_preview(student["id"])
    data = request.get_json(silent=True) or {}
    image_payloads = data.get("images") or []
    latitude = data.get("latitude")
    longitude = data.get("longitude")
    accuracy_meters = data.get("accuracy_meters")
    position_timestamp_ms = data.get("position_timestamp_ms")
    if not image_payloads:
        return jsonify({"success": False, "message": "No image received."}), 400
    gps_captured = latitude is not None or longitude is not None
    if gps_captured and (latitude is None or longitude is None):
        return jsonify({"success": False, "message": "A complete GPS reading is required when location is provided."}), 400

    if gps_captured:
        try:
            latitude = coerce_gps_coordinate(latitude, "Student latitude")
            longitude = coerce_gps_coordinate(longitude, "Student longitude")
        except ValueError as error:
            return jsonify({"success": False, "message": str(error)}), 400
        accuracy_meters = coerce_gps_accuracy_meters(accuracy_meters)
        position_timestamp_ms = coerce_position_timestamp_ms(position_timestamp_ms)
    else:
        latitude = None
        longitude = None
        accuracy_meters = None
        position_timestamp_ms = None

    frames = []
    for image_data in image_payloads[:ANALYSIS_FRAME_LIMIT]:
        frame = decode_base64_image(image_data)
        if frame is not None:
            frames.append(resize_frame_for_analysis(frame))
    if not frames:
        return jsonify({"success": False, "message": "Invalid image data."}), 400

    verification = build_student_attendance_result(student, frames)
    app.logger.info(
        "attendance-preview-request student_id=%s current_session_id=%s upcoming_session_id=%s gps=(%s,%s) accuracy_m=%s frame_count=%s",
        student["id"],
        current_session.get("id") if current_session else None,
        session_context.get("upcoming_session", {}).get("id") if session_context.get("upcoming_session") else None,
        latitude if latitude is not None else "n/a",
        longitude if longitude is not None else "n/a",
        f"{accuracy_meters:.2f}" if accuracy_meters is not None else "n/a",
        len(frames),
    )

    emotion_label = (
        verification.get("result", {}).get("emotion")
        if verification.get("result")
        else "Not detected"
    )
    emotion_label = normalize_attendance_emotion_label(emotion_label)
    identified_name = (
        verification.get("result", {}).get("name")
        if verification.get("result")
        else ""
    )
    liveness_label = verification.get("liveness", {}).get("label", "Unknown")
    existing = None
    tracking_context_session = current_session
    if current_session and current_session.get("id"):
        existing = get_effective_attendance_record(student["id"], current_session["id"])
    elif locked_tracking_row:
        existing = locked_tracking_row
        tracking_context_session = locked_tracking_session
    tracking_snapshot = build_tracking_snapshot(
        student,
        tracking_context_session or locked_tracking_session or locked_tracking_row,
        existing,
    )

    distance_meters = None
    raw_distance_meters = None
    admin_latitude = None
    admin_longitude = None
    distance_decision = {}
    effective_radius_meters = None
    within_radius = not (
        current_session
        and current_session.get("gps_latitude") is not None
        and current_session.get("gps_longitude") is not None
    )
    can_mark_attendance = False
    reason = verification.get("message", "")
    if (
        gps_captured
        and current_session
        and current_session.get("gps_latitude") is not None
        and current_session.get("gps_longitude") is not None
    ):
        try:
            distance_evaluation = evaluate_session_gps_reading(
                current_session,
                latitude,
                longitude,
                accuracy_meters=accuracy_meters,
                position_timestamp_ms=position_timestamp_ms,
            )
        except ValueError as error:
            return jsonify(
                {
                    "success": False,
                    "message": str(error),
                    "recognized": False,
                    "recognized_name": identified_name or "",
                    "liveness_passed": bool(verification.get("success")),
                    "emotion": emotion_label,
                    "gps_captured": False,
                    "student_lat": latitude,
                    "student_lng": longitude,
                    "admin_lat": current_session.get("gps_latitude"),
                    "admin_lng": current_session.get("gps_longitude"),
                    "distance_meters": None,
                    "within_radius": False,
                    "can_mark_attendance": False,
                    "reason": str(error),
                    "tracking": tracking_snapshot,
                }
            ), 400
        distance_payload = distance_evaluation["distance_payload"]
        distance_decision = distance_evaluation["decision"]
        admin_latitude = distance_payload["admin_latitude"]
        admin_longitude = distance_payload["admin_longitude"]
        raw_distance_meters = distance_decision["raw_distance_meters"]
        distance_meters = distance_decision["distance_meters"]
        effective_radius_meters = distance_decision["effective_radius_meters"]
        within_radius = distance_decision["is_in_range"]
        app.logger.info(
            "attendance-preview-distance student_id=%s session_id=%s student_gps=(%s,%s) session_gps=(%s,%s) raw_distance_m=%.2f display_distance_m=%.2f radius_m=%.2f buffer_m=%.2f accuracy_m=%.2f reading_age_s=%s range_state=%s",
            student["id"],
            current_session["id"],
            distance_payload["student_latitude"],
            distance_payload["student_longitude"],
            admin_latitude,
            admin_longitude,
            raw_distance_meters,
            distance_meters,
            effective_radius_meters,
            distance_decision["jitter_buffer_meters"],
            distance_decision["accuracy_meters"],
            f"{distance_decision['reading_age_seconds']:.1f}" if distance_decision["reading_age_seconds"] is not None else "n/a",
            distance_decision["range_state"],
        )
        if distance_decision["range_state"] == "uncertain":
            warning_message = build_gps_accuracy_warning(distance_decision)
            return jsonify(
                {
                    "success": False,
                    "message": warning_message,
                    "identified_name": identified_name,
                    "emotion": emotion_label,
                    "liveness_label": liveness_label,
                    "subject_name": current_session["subject_name"] if current_session else "N/A",
                    "distance_meters": round(distance_meters, 2),
                    "raw_distance_meters": round(raw_distance_meters, 2),
                    "gps_accuracy_meters": round(distance_payload["accuracy_meters"], 2),
                    "recognized": bool(identified_name and identified_name == student["name"]),
                    "recognized_name": identified_name or "",
                    "liveness_passed": bool(verification.get("success")),
                    "gps_captured": True,
                    "student_lat": distance_payload["student_latitude"],
                    "student_lng": distance_payload["student_longitude"],
                    "admin_lat": admin_latitude,
                    "admin_lng": admin_longitude,
                    "allowed_radius_meters": round(effective_radius_meters, 2),
                    "range_state": distance_decision["range_state"],
                    "within_radius": False,
                    "can_mark_attendance": False,
                    "reason": "gps_accuracy_low",
                    "attendance_open": bool(current_session),
                    "already_marked": bool(existing),
                    "analysis_ready": False,
                    "tracking": tracking_snapshot,
                }
            ), 409
        if not within_radius:
            radius_text = (
                str(int(effective_radius_meters))
                if float(effective_radius_meters).is_integer()
                else f"{effective_radius_meters:.2f}"
            )
            reason = "out_of_allowed_radius"
            return jsonify(
                {
                    "success": False,
                    "message": (
                        f"Attendance cannot be marked because you are more than {radius_text} meters away "
                        "from the allowed location."
                    ),
                    "identified_name": identified_name,
                    "emotion": emotion_label,
                    "liveness_label": liveness_label,
                    "subject_name": current_session["subject_name"] if current_session else "N/A",
                    "distance_meters": round(distance_meters, 2),
                    "raw_distance_meters": round(raw_distance_meters, 2),
                    "gps_accuracy_meters": round(distance_payload["accuracy_meters"], 2),
                    "recognized": bool(identified_name and identified_name == student["name"]),
                    "recognized_name": identified_name or "",
                    "liveness_passed": bool(verification.get("success")),
                    "gps_captured": True,
                    "student_lat": distance_payload["student_latitude"],
                    "student_lng": distance_payload["student_longitude"],
                    "admin_lat": admin_latitude,
                    "admin_lng": admin_longitude,
                    "allowed_radius_meters": round(effective_radius_meters, 2),
                    "range_state": distance_decision["range_state"],
                    "within_radius": False,
                    "can_mark_attendance": False,
                    "reason": reason,
                    "attendance_open": bool(current_session),
                    "already_marked": bool(existing),
                    "analysis_ready": False,
                    "tracking": tracking_snapshot,
                }
            ), 403

    if not verification["success"]:
        reason = "verification_failed"
        return jsonify(
            {
                "success": False,
                "message": f"Identified as {identified_name}. {verification['message']}" if identified_name else verification["message"],
                "identified_name": identified_name,
                "emotion": emotion_label,
                "liveness_label": liveness_label,
                "subject_name": current_session["subject_name"] if current_session else "N/A",
                "distance_meters": round(distance_meters, 2) if distance_meters is not None else None,
                "raw_distance_meters": round(raw_distance_meters, 2) if raw_distance_meters is not None else None,
                "recognized": False,
                "recognized_name": identified_name or "",
                "liveness_passed": False,
                "gps_captured": bool(gps_captured),
                "student_lat": latitude,
                "student_lng": longitude,
                "admin_lat": admin_latitude,
                "admin_lng": admin_longitude,
                "gps_accuracy_meters": round(accuracy_meters, 2) if accuracy_meters is not None else None,
                "allowed_radius_meters": round(effective_radius_meters, 2) if distance_meters is not None else None,
                "range_state": distance_decision["range_state"] if distance_meters is not None else "",
                "within_radius": within_radius,
                "can_mark_attendance": False,
                "reason": reason,
                "attendance_open": bool(current_session),
                "already_marked": bool(existing),
                "analysis_ready": False,
                "tracking": tracking_snapshot,
            }
        ), 200

    if existing:
        reason = "already_marked"
        return jsonify(
            {
                "success": True,
                "message": f"Analysis completed. {tracking_snapshot['message']}",
                "identified_name": identified_name,
                "emotion": emotion_label,
                "liveness_label": liveness_label,
                "subject_name": current_session["subject_name"] if current_session else "N/A",
                "distance_meters": round(distance_meters, 2) if distance_meters is not None else None,
                "raw_distance_meters": round(raw_distance_meters, 2) if raw_distance_meters is not None else None,
                "recognized": True,
                "recognized_name": identified_name or "",
                "liveness_passed": True,
                "gps_captured": bool(gps_captured),
                "student_lat": latitude,
                "student_lng": longitude,
                "admin_lat": admin_latitude,
                "admin_lng": admin_longitude,
                "gps_accuracy_meters": round(accuracy_meters, 2) if accuracy_meters is not None else None,
                "allowed_radius_meters": round(effective_radius_meters, 2) if distance_meters is not None else None,
                "range_state": distance_decision["range_state"] if distance_meters is not None else "",
                "within_radius": within_radius,
                "can_mark_attendance": False,
                "reason": reason,
                "attendance_open": False,
                "already_marked": True,
                "analysis_ready": False,
                "session_id": current_session["id"] if current_session else None,
                "tracking": tracking_snapshot,
            }
        )

    if not current_session or not current_session.get("id"):
        reason = "attendance_window_closed"
        if locked_tracking_row:
            return jsonify(
                {
                    "success": True,
                    "message": f"Analysis completed. {tracking_snapshot['message']}",
                    "identified_name": identified_name,
                    "emotion": emotion_label,
                    "liveness_label": liveness_label,
                    "subject_name": tracking_snapshot.get("subject_name") or "N/A",
                    "distance_meters": round(distance_meters, 2) if distance_meters is not None else (
                        round(float(tracking_snapshot.get("distance_meters")), 2)
                        if tracking_snapshot.get("distance_meters") is not None
                        else None
                    ),
                    "raw_distance_meters": round(raw_distance_meters, 2) if raw_distance_meters is not None else None,
                    "recognized": True,
                    "recognized_name": identified_name or "",
                    "liveness_passed": True,
                    "gps_captured": bool(gps_captured),
                    "student_lat": latitude,
                    "student_lng": longitude,
                    "admin_lat": admin_latitude,
                    "admin_lng": admin_longitude,
                    "within_radius": within_radius,
                    "can_mark_attendance": False,
                    "reason": "tracking_in_progress",
                    "attendance_open": False,
                    "already_marked": True,
                    "analysis_ready": False,
                    "session_id": locked_tracking_row.get("session_id"),
                    "tracking": tracking_snapshot,
                }
            )
        return jsonify(
            {
                "success": True,
                "message": "Analysis completed. No active class session is open for attendance.",
                "identified_name": identified_name,
                "emotion": emotion_label,
                "liveness_label": liveness_label,
                "subject_name": "N/A",
                "distance_meters": round(distance_meters, 2) if distance_meters is not None else None,
                "raw_distance_meters": round(raw_distance_meters, 2) if raw_distance_meters is not None else None,
                "recognized": True,
                "recognized_name": identified_name or "",
                "liveness_passed": True,
                "gps_captured": bool(gps_captured),
                "student_lat": latitude,
                "student_lng": longitude,
                "admin_lat": admin_latitude,
                "admin_lng": admin_longitude,
                "gps_accuracy_meters": round(accuracy_meters, 2) if accuracy_meters is not None else None,
                "allowed_radius_meters": round(effective_radius_meters, 2) if distance_meters is not None else None,
                "range_state": distance_decision["range_state"] if distance_meters is not None else "",
                "within_radius": within_radius,
                "can_mark_attendance": False,
                "reason": reason,
                "attendance_open": False,
                "already_marked": False,
                "analysis_ready": False,
                "tracking": build_tracking_snapshot(student, None, None),
            }
        )

    proof_snapshot_path = save_proof_snapshot(image_payloads[0], student, current_session)
    cache_student_attendance_preview(
        student,
        current_session,
        verification,
        latitude,
        longitude,
        accuracy_meters=accuracy_meters,
        position_timestamp_ms=position_timestamp_ms,
        distance_meters=distance_meters,
        raw_distance_meters=raw_distance_meters,
        proof_snapshot_path=proof_snapshot_path,
    )

    status_message = "Analysis completed. Click Mark Attendance to save temporary attendance."
    if identified_name:
        status_message = (
            f"Analysis completed. Identified as {identified_name}. "
            "Click Mark Attendance to save temporary attendance."
        )

    return jsonify(
        {
            "success": True,
            "message": status_message,
            "identified_name": identified_name,
            "emotion": emotion_label,
            "liveness_label": liveness_label,
            "subject_name": current_session["subject_name"],
            "distance_meters": round(distance_meters, 2) if distance_meters is not None else None,
            "raw_distance_meters": round(raw_distance_meters, 2) if raw_distance_meters is not None else None,
            "gps_accuracy_meters": round(accuracy_meters, 2) if accuracy_meters is not None else None,
            "recognized": True,
            "recognized_name": identified_name or "",
            "liveness_passed": True,
            "gps_captured": bool(gps_captured),
            "student_lat": latitude,
            "student_lng": longitude,
            "admin_lat": admin_latitude,
            "admin_lng": admin_longitude,
            "allowed_radius_meters": round(effective_radius_meters, 2) if distance_meters is not None else None,
            "range_state": distance_decision["range_state"] if distance_meters is not None else "",
            "within_radius": within_radius,
            "can_mark_attendance": True,
            "reason": "ready_to_mark",
            "attendance_open": True,
            "already_marked": False,
            "analysis_ready": True,
            "session_id": current_session["id"],
            "tracking": build_tracking_snapshot(student, current_session, None),
        }
    )


@app.route("/student-corrections", methods=["POST"])
@student_required
def student_correction_request():
    student = get_student_by_id(session.get("student_id"))
    session_id = request.form.get("session_id", "").strip()
    attendance_record_id = request.form.get("attendance_record_id", "").strip() or None
    reason = request.form.get("reason", "").strip()
    requested_status = request.form.get("requested_status", "Present").strip().title()
    if not student or not session_id or not reason:
        flash("Session and reason are required for a correction request.", "danger")
        return redirect(url_for("student_corrections_page"))
    create_correction_request(student["id"], int(session_id), int(attendance_record_id) if attendance_record_id else None, reason, requested_status=requested_status)
    flash("Correction request submitted successfully.", "success")
    return redirect(url_for("student_corrections_page"))


@app.route("/admin/corrections/<int:correction_id>/review", methods=["POST"])
@admin_required
def review_correction(correction_id):
    decision = request.form.get("decision", "").strip().title()
    admin_notes = request.form.get("admin_notes", "").strip()
    review_correction_request(correction_id, decision, session.get("admin_id"), admin_notes)
    flash(f"Correction request {decision.lower()} successfully.", "success")
    return redirect_admin_next("admin_overrides")


@app.route("/reports/export")
@admin_required
def export_reports():
    parsed_filters = build_report_filters(request.args)
    filters = {
        "date_from": parsed_filters["date_from"] or None,
        "date_to": parsed_filters["date_to"] or None,
        "class_name": parsed_filters["class_name"] or None,
        "student_id": parsed_filters["student_id"],
        "status": parsed_filters["status"] or None,
    }
    rows = get_attendance_report(filters)
    csv_columns = [
        "student_name",
        "enrollment_number",
        "student_class_name",
        "subject_name",
        "session_date",
        "start_time",
        "end_time",
        "status",
        "attendance_time",
        "distance_meters",
        "marked_via",
        "rejection_reason",
    ]
    export_rows = [{column: row.get(column) for column in csv_columns} for row in rows]
    buffer = StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=csv_columns,
    )
    writer.writeheader()
    writer.writerows(export_rows)
    return app.response_class(
        buffer.getvalue(),
        content_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=attendance_analytics_report.csv"},
    )


@app.route("/register-student", methods=["GET", "POST"])
@admin_required
def register_student():
    context = get_common_context()

    if request.method == "POST":
        name = request.form.get("name", "").strip().title()
        class_name = request.form.get("class_name", "").strip()
        enrollment_number = request.form.get("enrollment_number", "").strip().upper()
        email = request.form.get("email", "").strip().lower()
        mobile_number = normalize_mobile_number(request.form.get("mobile_number"))
        photo_data = request.form.get("photo_data", "")

        if not all([name, class_name, enrollment_number, email, mobile_number, photo_data]):
            flash("All student fields and a captured photo are required.", "danger")
            return render_template("register_student.html", **context)

        if not mobile_number:
            flash("Enter a valid mobile number with 10 to 15 digits.", "danger")
            return render_template("register_student.html", **context)

        if get_student_by_enrollment(enrollment_number):
            flash("This enrollment number is already registered.", "danger")
            return render_template("register_student.html", **context)

        if get_student_by_email(email):
            flash("This email is already registered.", "danger")
            return render_template("register_student.html", **context)

        image_path = save_base64_image(photo_data, enrollment_number, name)
        if not image_path:
            flash("Could not save the captured student photo. Please try again.", "danger")
            return render_template("register_student.html", **context)

        create_student(name, class_name, enrollment_number, email, mobile_number, image_path)
        refresh_runtime_state(rebuild_engines=True)

        sent, mail_message = send_enrollment_email(name, email)
        if sent:
            flash(f"Student registered successfully. {mail_message}", "success")
        else:
            flash(
                "Student registered successfully, but the confirmation email was not sent. "
                f"Reason: {mail_message}",
                "warning",
            )

        return redirect(url_for("registered_students"))

    return render_template("register_student.html", **context)


@app.route("/email-settings", methods=["GET", "POST"])
@admin_required
def email_settings():
    current_settings = load_smtp_settings()
    context = get_admin_settings_context(current_settings)

    if request.method == "POST":
        host = request.form.get("host", "").strip()
        port = request.form.get("port", "587").strip() or "587"
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        sender = request.form.get("sender", "").strip()
        use_tls = request.form.get("use_tls") == "on"

        try:
            port_number = int(port)
        except ValueError:
            flash("SMTP port must be a valid number.", "danger")
            context["smtp_settings"] = {
                "host": host,
                "port": port,
                "username": username,
                "password": password,
                "sender": sender,
                "use_tls": use_tls,
            }
            return render_template("admin_settings.html", **context)

        validation_error = validate_smtp_settings(host, port_number, username, password, sender)
        if validation_error:
            flash(validation_error, "danger")
            context["smtp_settings"] = {
                "host": host,
                "port": port_number,
                "username": username,
                "password": password,
                "sender": sender,
                "use_tls": use_tls,
            }
            return render_template("admin_settings.html", **context)

        save_smtp_settings(host, port_number, username, password, sender, use_tls)
        flash("SMTP settings saved successfully.", "success")
        return redirect_admin_next("admin_settings")

    context["smtp_settings"] = current_settings
    return render_template("admin_settings.html", **context)


@app.route("/registered-students")
@admin_required
def registered_students():
    return render_template("registered_students.html", **get_common_context())


@app.route("/students/<int:student_id>/delete", methods=["POST"])
@admin_required
def remove_student(student_id):
    student = get_student_by_id(student_id)
    if not student:
        flash("Student not found.", "warning")
        return redirect(url_for("registered_students"))

    delete_student_image(student.get("image_path"))
    delete_student(student_id)
    refresh_runtime_state(rebuild_engines=True)

    flash(
        f"Deleted {student['name']} and removed the student's attendance data.",
        "success",
    )
    return redirect(url_for("registered_students"))


@app.route("/students/<int:student_id>/edit", methods=["POST"])
@admin_required
def edit_student(student_id):
    student = get_student_by_id(student_id)
    if not student:
        flash("Student not found.", "warning")
        return redirect(url_for("registered_students"))

    name = request.form.get("name", "").strip().title()
    class_name = request.form.get("class_name", "").strip()
    enrollment_number = request.form.get("enrollment_number", "").strip().upper()
    email = request.form.get("email", "").strip().lower()
    mobile_number_raw = request.form.get("mobile_number", "")
    mobile_number = normalize_mobile_number(mobile_number_raw)
    uploaded_photo = request.files.get("photo_file")

    if not all([name, class_name, enrollment_number, email]):
        flash("All student fields are required to update the record.", "danger")
        return redirect(url_for("registered_students"))

    if mobile_number_raw.strip() and not mobile_number:
        flash("Enter a valid mobile number with 10 to 15 digits.", "danger")
        return redirect(url_for("registered_students"))

    existing_name = get_student_by_name(name)
    if existing_name and existing_name["id"] != student_id:
        flash("Another student is already registered with this name.", "danger")
        return redirect(url_for("registered_students"))

    existing_enrollment = get_student_by_enrollment(enrollment_number)
    if existing_enrollment and existing_enrollment["id"] != student_id:
        flash("This enrollment number is already registered.", "danger")
        return redirect(url_for("registered_students"))

    existing_email = get_student_by_email(email)
    if existing_email and existing_email["id"] != student_id:
        flash("This email is already registered.", "danger")
        return redirect(url_for("registered_students"))

    replacement_image_path = None
    has_uploaded_photo = bool(uploaded_photo and str(uploaded_photo.filename or "").strip())
    if has_uploaded_photo:
        replacement_image_path, upload_error = save_uploaded_student_image(
            uploaded_photo,
            enrollment_number,
            name,
        )
        if upload_error:
            flash(upload_error, "danger")
            return redirect(url_for("registered_students"))

    update_student(
        student_id,
        name,
        class_name,
        enrollment_number,
        email,
        mobile_number,
        image_path=replacement_image_path,
    )

    if replacement_image_path:
        previous_image_path = student.get("image_path")
        if previous_image_path and Path(previous_image_path).resolve() != Path(replacement_image_path).resolve():
            delete_student_image(previous_image_path)

    refresh_runtime_state(rebuild_engines=True)

    if replacement_image_path:
        flash(
            f"Updated student details for {name} and replaced the active face photo.",
            "success",
        )
    else:
        flash(f"Updated student details for {name}.", "success")
    return redirect(url_for("registered_students"))


@app.route("/students/<int:student_id>/photo")
@admin_required
def student_photo(student_id):
    student = get_student_by_id(student_id)
    if not student:
        abort(404)

    image_path = resolve_preferred_student_image(student.get("name"), student.get("image_path"))
    if not image_path:
        abort(404)

    target_path = Path(image_path).resolve()
    if not target_path.exists() or not is_allowed_student_media_path(target_path):
        abort(404)

    return send_file(target_path)


@app.route("/camera-debug")
@admin_required
def camera_debug():
    context = get_common_context()
    context["debug_capture_url"] = url_for("static", filename="camera_debug_capture.jpg")
    return render_template("camera_debug.html", **context)


@app.route("/analyze", methods=["POST"])
@admin_required
def analyze():
    if not ENGINE_STATE.get("recognition_ready") or not ENGINE_STATE.get("emotion_ready"):
        try:
            refresh_runtime_state(rebuild_engines=False)
            start_background_engine_bootstrap()
        except Exception as error:
            log_message("engine-refresh-analyze", error)

    if not ANALYZE_LOCK.acquire(blocking=False):
        return jsonify(
            {
                "success": False,
                "message": "An attendance scan is already running. Please wait for the current capture to finish.",
            }
        ), 429

    try:
        data = request.get_json(silent=True) or {}
        image_items = data.get("images")
        if isinstance(image_items, list):
            image_payloads = [item for item in image_items if isinstance(item, str) and item.strip()]
        else:
            single_image = data.get("image")
            image_payloads = [single_image] if isinstance(single_image, str) and single_image.strip() else []

        if not image_payloads:
            return jsonify({"success": False, "message": "No image received."}), 400

        if not ENGINE_STATE.get("recognition_ready") and not ENGINE_STATE.get("emotion_ready"):
            stats = get_dashboard_stats()
            return jsonify(
                {
                    "success": False,
                    "message": "AI engines are still starting. Please wait a few seconds and try again.",
                    "recognition_ready": ENGINE_STATE["recognition_ready"],
                    "emotion_ready": ENGINE_STATE["emotion_ready"],
                    "recognition_error": ENGINE_STATE["recognition_error"],
                    "emotion_error": ENGINE_STATE["emotion_error"],
                    "startup_message": ENGINE_STATE["startup_message"],
                    "stats": stats,
                }
            ), 503

        frames = []
        for image_data in image_payloads[:ANALYSIS_FRAME_LIMIT]:
            frame = decode_base64_image(image_data)
            if frame is None:
                continue
            frames.append(resize_frame_for_analysis(frame))

        if not frames:
            return jsonify({"success": False, "message": "Invalid image data."}), 400

        liveness = assess_liveness(frames)
        if liveness["is_spoof"]:
            stats = get_dashboard_stats()
            return jsonify(
                {
                    "success": True,
                    "message": build_result_message(
                        True,
                        "Unknown",
                        "Unknown",
                        False,
                        multiple_faces_detected=False,
                        spoof_detected=True,
                    ),
                    "name": "Unknown",
                    "emotion": "Unknown",
                    "face_detected": True,
                    "recognition_ready": ENGINE_STATE["recognition_ready"],
                    "emotion_ready": ENGINE_STATE["emotion_ready"],
                    "recognition_error": ENGINE_STATE["recognition_error"],
                    "emotion_error": ENGINE_STATE["emotion_error"],
                    "startup_message": ENGINE_STATE["startup_message"],
                    "recognition_meta": None,
                    "attendance_marked": False,
                    "attendance_marked_names": [],
                    "already_marked_names": [],
                    "detected_faces": 1,
                    "recognized_count": 0,
                    "recognized_people": [],
                    "liveness_label": liveness["label"],
                    "liveness_message": liveness["message"],
                    "liveness_metrics": liveness["metrics"],
                    "spoof_detected": True,
                    "stats": stats,
                }
            )

        analysis_results = [recognize_and_analyze(frame, mark_present=False) for frame in frames]
        result = aggregate_batch_results(analysis_results, allow_attendance_mark=False)
        attendance_marked = False
        attendance_marked_names = []
        already_marked_names = []
        recognized_people = []
        recognized_count = 0

        if not result.get("multiple_faces_detected") and result["name"] != "Unknown":
            recognized_people.append(
                {
                    "name": result["name"],
                    "emotion": result["emotion"],
                    "recognition_meta": result["recognition_meta"],
                }
            )
            recognized_count = 1
            if ENGINE_STATE["recognition_ready"]:
                if should_skip_auto_attendance_mark_for_student(result["name"]):
                    result["message"] = (
                        f"{result['name']} was verified. Open Student Self Attendance to finish "
                        "GPS-based attendance for the active session."
                    )
                else:
                    attendance_marked = mark_attendance(result["name"], result["emotion"])
                    if attendance_marked:
                        attendance_marked_names.append(result["name"])
                    else:
                        already_marked_names.append(result["name"])

        result["attendance_marked"] = attendance_marked
        if not result.get("message"):
            result["message"] = build_result_message(
                result["face_detected"],
                result["name"],
                result["emotion"],
                attendance_marked,
                multiple_faces_detected=result.get("multiple_faces_detected", False),
            )
        result["emotion_ready"] = ENGINE_STATE["emotion_ready"]
        result["recognition_ready"] = ENGINE_STATE["recognition_ready"]
        result["emotion_error"] = ENGINE_STATE["emotion_error"]
        result["recognition_error"] = ENGINE_STATE["recognition_error"]
        result["startup_message"] = ENGINE_STATE["startup_message"]
        stats = get_dashboard_stats()

        return jsonify(
            {
                "success": True,
                "message": result["message"],
                "name": result["name"],
                "emotion": result["emotion"],
                "face_detected": result["face_detected"],
                "recognition_ready": result["recognition_ready"],
                "emotion_ready": result["emotion_ready"],
                "recognition_error": result["recognition_error"],
                "emotion_error": result["emotion_error"],
                "startup_message": result["startup_message"],
                "recognition_meta": result["recognition_meta"],
                "attendance_marked": attendance_marked,
                "attendance_marked_names": attendance_marked_names,
                "already_marked_names": already_marked_names,
                "detected_faces": result.get("detected_faces", 0),
                "recognized_count": recognized_count,
                "recognized_people": recognized_people,
                "liveness_label": liveness["label"],
                "liveness_message": liveness["message"],
                "liveness_metrics": liveness["metrics"],
                "spoof_detected": False,
                "stats": stats,
            }
        )
    finally:
        ANALYZE_LOCK.release()


@app.route("/chat", methods=["POST"])
@admin_required
def chat():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "")
    reply = generate_admin_assistant_reply(user_message)
    return jsonify(reply)


@app.route("/student-chat", methods=["POST"])
@student_required
def student_chat():
    data = request.get_json(silent=True) or {}
    user_message = data.get("message", "")
    reply = generate_student_assistant_reply(user_message, session.get("student_id"))
    return jsonify(reply)


@app.route("/stats")
@admin_required
def stats():
    return jsonify(get_dashboard_stats())


@app.route("/health")
def health():
    return jsonify(
        {
            "status": "ok",
            "emotion_ready": ENGINE_STATE["emotion_ready"],
            "recognition_ready": ENGINE_STATE["recognition_ready"],
            "emotion_error": ENGINE_STATE["emotion_error"],
            "recognition_error": ENGINE_STATE["recognition_error"],
            "startup_message": ENGINE_STATE["startup_message"],
            "known_faces": all_students,
            "weights_dir": ENGINE_STATE["weights_dir"],
            "recognition_model": ENGINE_STATE["recognition_model"],
            "recognition_backend": ENGINE_STATE["recognition_backend"],
            "app_url": f"http://{DISPLAY_HOST}:{DEFAULT_PORT}",
        }
    )


if __name__ == "__main__":
    try:
        print(f"Starting Flask server on http://{DEFAULT_HOST}:{DEFAULT_PORT}/")
        app.run(host="127.0.0.1", port=5000, debug=True, use_reloader=False)
    except Exception as error:
        print(f"[startup-error] {error}", file=sys.stderr)
        traceback.print_exc()
        raise
