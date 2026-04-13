from collections import defaultdict
from datetime import date, datetime, timedelta

from database import get_connection, get_dashboard_stats, get_student_by_id, get_student_attendance_summary


def _week_bounds(reference_date=None):
    today = reference_date or date.today()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=6)
    return start, end


def _normalize(text):
    return str(text or "").strip().lower()


def _contains_all(message, *parts):
    return all(part in message for part in parts)


def _fetch_subject_names(class_name=None):
    conn = get_connection()
    try:
        if class_name:
            rows = conn.execute(
                """
                SELECT DISTINCT subject_name
                FROM class_schedules
                WHERE class_name = ?
                ORDER BY subject_name ASC
                """,
                (class_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT DISTINCT subject_name
                FROM class_schedules
                ORDER BY subject_name ASC
                """
            ).fetchall()
        return [row["subject_name"] for row in rows]
    finally:
        conn.close()


def _detect_subject(message, subjects):
    normalized = _normalize(message)
    for subject in subjects:
        if subject.lower() in normalized:
            return subject
    return None


def _is_attended_history_row(row):
    status = _normalize(row.get("status"))
    attendance_status = _normalize(row.get("attendance_status"))
    return status in {"present", "late"} or attendance_status in {"finalized", "final"}


def _is_missed_history_row(row):
    status = _normalize(row.get("status"))
    attendance_status = _normalize(row.get("attendance_status"))
    return status in {"absent", "cancelled", "rejected"} or attendance_status in {"cancelled", "rejected"}


def _get_student_history_summary(student_id):
    summary = get_student_attendance_summary(student_id)
    if not summary:
        return None
    return summary


def _student_subject_summary(student_id):
    summary_payload = _get_student_history_summary(student_id)
    if not summary_payload:
        return [], None

    rows = summary_payload.get("history", [])
    student = summary_payload.get("student")
    summary = defaultdict(
        lambda: {
            "subject_name": "",
            "days": set(),
            "total_classes": 0,
            "present_classes": 0,
            "late_classes": 0,
            "absent_classes": 0,
            "attendance_percentage": 0,
        }
    )

    for row in rows:
        if row.get("session_status") != "Completed":
            continue
        subject = summary[row["subject_name"]]
        subject["subject_name"] = row["subject_name"]
        if row.get("date"):
            try:
                subject["days"].add(datetime.strptime(row["date"], "%Y-%m-%d").strftime("%A"))
            except ValueError:
                pass
        subject["total_classes"] += 1
        if _is_attended_history_row(row):
            subject["present_classes"] += 1
            if _normalize(row.get("status")) == "late":
                subject["late_classes"] += 1
        elif _is_missed_history_row(row):
            subject["absent_classes"] += 1

    result = []
    for item in summary.values():
        attended = item["present_classes"] + item["late_classes"]
        item["attendance_percentage"] = round(
            (attended / item["total_classes"]) * 100, 2
        ) if item["total_classes"] else 0
        item["days"] = sorted(item["days"])
        result.append(item)

    result.sort(key=lambda item: item["subject_name"])
    return result, student


def _student_weekly_summary(student_id):
    summary_payload = _get_student_history_summary(student_id)
    if not summary_payload:
        return None

    rows = summary_payload.get("history", [])
    start, end = _week_bounds()
    total_classes = 0
    attended_classes = 0
    missed_classes = 0
    for row in rows:
        if row.get("session_status") != "Completed" or not row.get("date"):
            continue
        try:
            session_date = datetime.strptime(row["date"], "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= session_date <= end):
            continue
        total_classes += 1
        if _is_attended_history_row(row):
            attended_classes += 1
        elif _is_missed_history_row(row):
            missed_classes += 1

    return {
        "start_date": start.strftime("%Y-%m-%d"),
        "end_date": end.strftime("%Y-%m-%d"),
        "total_classes": total_classes,
        "attended_classes": attended_classes,
        "missed_classes": missed_classes,
    }


def _student_overall_summary(student_id):
    summary_payload = _get_student_history_summary(student_id)
    if not summary_payload:
        return None
    subject_rows, student = _student_subject_summary(student_id)
    total_classes = summary_payload.get("total_classes", 0)
    attended_classes = summary_payload.get("attended_classes", 0)
    absent_classes = summary_payload.get("absent_classes", 0)
    present_classes = max(attended_classes - sum(item["late_classes"] for item in subject_rows), 0)
    late_classes = sum(item["late_classes"] for item in subject_rows)
    attended_classes = present_classes + late_classes
    percentage = round((attended_classes / total_classes) * 100, 2) if total_classes else 0
    return {
        "student": student,
        "subject_rows": subject_rows,
        "total_classes": total_classes,
        "present_classes": present_classes,
        "late_classes": late_classes,
        "absent_classes": absent_classes,
        "attended_classes": attended_classes,
        "attendance_percentage": percentage,
        "weekly_summary": _student_weekly_summary(student_id),
    }


def _admin_rankings(limit=2, reverse=True):
    rows = _fetch_admin_attendance_rows()
    grouped = defaultdict(lambda: {"name": "", "class_name": "", "session_ids": set(), "attended_classes": 0})
    for row in rows:
        entry = grouped[row["student_id"]]
        entry["name"] = row["student_name"]
        entry["class_name"] = row["student_class_name"]
        if row["session_id"]:
            entry["session_ids"].add(row["session_id"])
        if _is_attended_history_row(row):
            entry["attended_classes"] += 1

    items = []
    for entry in grouped.values():
        total_classes = len(entry["session_ids"])
        if not total_classes:
            continue
        attended = entry["attended_classes"]
        percentage = round((attended / total_classes) * 100, 2) if total_classes else 0
        items.append(
            {
                "name": entry["name"],
                "class_name": entry["class_name"],
                "total_classes": total_classes,
                "attended_classes": attended,
                "attendance_percentage": percentage,
            }
        )

    items.sort(
        key=lambda item: (
            -item["attendance_percentage"] if reverse else item["attendance_percentage"],
            item["name"],
        )
    )
    return items[:limit]


def _fetch_admin_attendance_rows():
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT attendance.student_id,
                   students.name AS student_name,
                   students.class_name AS student_class_name,
                   attendance.session_id,
                   class_sessions.class_name AS session_class_name,
                   class_sessions.subject_name,
                   class_sessions.session_date,
                   class_sessions.session_status,
                   attendance.status,
                   attendance.attendance_status
            FROM attendance
            LEFT JOIN students
                ON students.id = attendance.student_id
            LEFT JOIN class_sessions
                ON class_sessions.id = attendance.session_id
            WHERE attendance.status IN ('Present', 'Late', 'Absent', 'Cancelled', 'Rejected', 'Provisional')
            """
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def _admin_subject_analytics():
    rows = _fetch_admin_attendance_rows()
    grouped = defaultdict(lambda: {"subject_name": "", "session_ids": set(), "attended_marks": 0, "absent_marks": 0})
    for row in rows:
        subject_name = row.get("subject_name") or ""
        if not subject_name:
            continue
        entry = grouped[subject_name]
        entry["subject_name"] = subject_name
        if row.get("session_id"):
            entry["session_ids"].add(row["session_id"])
        if _is_attended_history_row(row):
            entry["attended_marks"] += 1
        elif _is_missed_history_row(row):
            entry["absent_marks"] += 1

    result = []
    for entry in grouped.values():
        result.append(
            {
                "subject_name": entry["subject_name"],
                "total_classes": len(entry["session_ids"]),
                "attended_marks": entry["attended_marks"],
                "absent_marks": entry["absent_marks"],
            }
        )
    result.sort(key=lambda item: item["subject_name"])
    return result


def _admin_class_analytics():
    rows = _fetch_admin_attendance_rows()
    grouped = defaultdict(lambda: {"class_name": "", "session_ids": set(), "attended_marks": 0, "absent_marks": 0})
    for row in rows:
        class_name = row.get("session_class_name") or row.get("student_class_name") or ""
        if not class_name:
            continue
        entry = grouped[class_name]
        entry["class_name"] = class_name
        if row.get("session_id"):
            entry["session_ids"].add(row["session_id"])
        if _is_attended_history_row(row):
            entry["attended_marks"] += 1
        elif _is_missed_history_row(row):
            entry["absent_marks"] += 1

    result = []
    for entry in grouped.values():
        result.append(
            {
                "class_name": entry["class_name"],
                "total_sessions": len(entry["session_ids"]),
                "attended_marks": entry["attended_marks"],
                "absent_marks": entry["absent_marks"],
            }
        )
    result.sort(key=lambda item: item["class_name"])
    return result


def _admin_weekly_trend():
    start, end = _week_bounds()
    rows = _fetch_admin_attendance_rows()
    grouped = defaultdict(lambda: {"session_date": "", "attended_marks": 0, "absent_marks": 0})
    for row in rows:
        raw_date = row.get("session_date")
        if not raw_date:
            continue
        try:
            session_date = datetime.strptime(raw_date, "%Y-%m-%d").date()
        except ValueError:
            continue
        if not (start <= session_date <= end):
            continue
        key = session_date.strftime("%Y-%m-%d")
        entry = grouped[key]
        entry["session_date"] = key
        if _is_attended_history_row(row):
            entry["attended_marks"] += 1
        elif _is_missed_history_row(row):
            entry["absent_marks"] += 1

    return [grouped[key] for key in sorted(grouped.keys())]


def _chart_payload(chart_type, labels, datasets, title):
    return {
        "type": chart_type,
        "title": title,
        "labels": labels,
        "datasets": datasets,
    }


def generate_student_assistant_reply(user_message, student_id):
    summary = _student_overall_summary(student_id)
    if not summary or not summary.get("student"):
        return {"message": "I could not find your attendance data right now. Please refresh and try again."}
    student = summary["student"]
    subject_rows = summary["subject_rows"]
    weekly = summary["weekly_summary"]
    message = _normalize(user_message)
    subjects = [item["subject_name"] for item in subject_rows]
    subject_name = _detect_subject(message, subjects)

    if not message:
        return {
            "message": f"Hi, {student['name']}! You have attended {summary['attendance_percentage']}% of your classes. Ask me about your weekly attendance, subject-wise attendance, absences, or attendance graph.",
            "chart": _chart_payload(
                "doughnut",
                ["Present", "Late", "Absent"],
                [
                    {
                        "label": "Attendance Distribution",
                        "data": [
                            summary["present_classes"],
                            summary["late_classes"],
                            summary["absent_classes"],
                        ],
                        "backgroundColor": ["#22c55e", "#f59e0b", "#ef4444"],
                    }
                ],
                "My Attendance Distribution",
            ),
        }

    if subject_name and ("how many classes happened" in message or _contains_all(message, "classes", subject_name.lower())):
        item = next(item for item in subject_rows if item["subject_name"] == subject_name)
        weekly_days = ", ".join(item["days"]) if item["days"] else "No weekly pattern saved yet"
        return {
            "message": f"{subject_name} has {item['total_classes']} completed classes for you. It occurs on {weekly_days}.",
            "table": {
                "columns": ["Subject", "Total Classes", "Weekly Days"],
                "rows": [[subject_name, item["total_classes"], weekly_days]],
            },
        }

    if subject_name and ("which days" in message or "days of the week" in message):
        item = next(item for item in subject_rows if item["subject_name"] == subject_name)
        weekly_days = ", ".join(item["days"]) if item["days"] else "No weekly days available"
        return {"message": f"{subject_name} occurs on {weekly_days}."}

    if "how many classes am i present" in message or "present in" in message:
        return {"message": f"You are present in {summary['present_classes']} classes and late in {summary['late_classes']} classes."}

    if "how many classes am i absent" in message or "miss" in message:
        return {"message": f"You are absent in {summary['absent_classes']} classes."}

    if "attendance percentage" in message:
        return {"message": f"Your attendance percentage is {summary['attendance_percentage']}%."}

    if "subject-wise attendance" in message or "subject wise attendance" in message:
        return {
            "message": "Here is your subject-wise attendance percentage.",
            "chart": _chart_payload(
                "bar",
                [item["subject_name"] for item in subject_rows],
                [
                    {
                        "label": "Attendance %",
                        "data": [item["attendance_percentage"] for item in subject_rows],
                        "backgroundColor": "#3b82f6",
                    }
                ],
                "Subject-Wise Attendance Percentage",
            ),
            "table": {
                "columns": ["Subject", "Attended", "Total", "Attendance %"],
                "rows": [
                    [item["subject_name"], item["present_classes"] + item["late_classes"], item["total_classes"], item["attendance_percentage"]]
                    for item in subject_rows
                ],
            },
        }

    if "attendance graph" in message or "show my attendance graph" in message:
        return {
            "message": "Here is your personal attendance graph.",
            "chart": _chart_payload(
                "bar",
                [item["subject_name"] for item in subject_rows],
                [
                    {"label": "Present", "data": [item["present_classes"] for item in subject_rows], "backgroundColor": "#22c55e"},
                    {"label": "Late", "data": [item["late_classes"] for item in subject_rows], "backgroundColor": "#f59e0b"},
                    {"label": "Absent", "data": [item["absent_classes"] for item in subject_rows], "backgroundColor": "#ef4444"},
                ],
                "Attendance By Subject",
            ),
        }

    if "highest attendance" in message:
        best = max(subject_rows, key=lambda item: (item["attendance_percentage"], item["subject_name"]), default=None)
        if not best:
            return {"message": "No subject attendance data is available yet."}
        return {"message": f"Your highest attendance is in {best['subject_name']} at {best['attendance_percentage']}%."}

    if "lowest attendance" in message:
        worst = min(subject_rows, key=lambda item: (item["attendance_percentage"], item["subject_name"]), default=None)
        if not worst:
            return {"message": "No subject attendance data is available yet."}
        return {"message": f"Your lowest attendance is in {worst['subject_name']} at {worst['attendance_percentage']}%."}

    if "this week" in message or "weekly attendance" in message:
        return {
            "message": f"This week ({weekly['start_date']} to {weekly['end_date']}), {weekly['total_classes']} classes happened, you attended {weekly['attended_classes']}, and you missed {weekly['missed_classes']}.",
            "chart": _chart_payload(
                "doughnut",
                ["Attended", "Missed"],
                [
                    {
                        "label": "Weekly Attendance",
                        "data": [weekly["attended_classes"], weekly["missed_classes"]],
                        "backgroundColor": ["#22c55e", "#ef4444"],
                    }
                ],
                "This Week",
            ),
        }

    return {
        "message": f"Hi, {student['name']}! I can help with your attendance summary, subject-wise percentages, weekly attendance, absences, and attendance graphs."
    }


def generate_admin_assistant_reply(user_message):
    stats = get_dashboard_stats()
    message = _normalize(user_message)
    subjects = _fetch_subject_names()
    subject_name = _detect_subject(message, subjects)
    subject_analytics = _admin_subject_analytics()
    class_analytics = _admin_class_analytics()
    weekly_trend = _admin_weekly_trend()
    top_students = _admin_rankings(limit=2, reverse=True)
    low_students = _admin_rankings(limit=2, reverse=False)

    if not message:
        return {
            "message": f"Welcome back, admin. Today you have {stats['present_count']} present marks, {stats['late_count']} late marks, and {stats['absent_count']} absent marks. Ask me about subject analytics, top attendance students, class analytics, low attendance, or weekly trends.",
            "chart": _chart_payload(
                "doughnut",
                ["Present", "Late", "Absent", "Rejected"],
                [
                    {
                        "label": "Today's Attendance",
                        "data": [stats["present_count"], stats["late_count"], stats["absent_count"], stats["rejected_count"]],
                        "backgroundColor": ["#22c55e", "#f59e0b", "#ef4444", "#64748b"],
                    }
                ],
                "Today's Attendance Summary",
            ),
        }

    if (
        message in {"present", "who is present", "who is present today"}
        or "who is present" in message
        or "present students" in message
        or _contains_all(message, "how many", "students", "present")
    ):
        present_list = ", ".join(stats["present_students"]) if stats["present_students"] else "none"
        if "who is present" in message or "present students" in message:
            return {
                "message": f"Present students today ({stats['present_count']}): {present_list}."
            }
        return {
            "message": f"{stats['present_count']} students are present today. Names: {present_list}."
        }

    if (
        message in {"absent", "who is absent", "who is absent today"}
        or "who is absent" in message
        or "absent students" in message
        or _contains_all(message, "how many", "students", "absent")
    ):
        absent_list = ", ".join(stats["absent_students"]) if stats["absent_students"] else "none"
        if "who is absent" in message or "absent students" in message:
            return {
                "message": f"Absent students today ({stats['absent_count']}): {absent_list}."
            }
        return {
            "message": f"{stats['absent_count']} students are absent today. Names: {absent_list}."
        }

    if "registered students" in message or "total students" in message or "enrolled students" in message:
        student_list = ", ".join(student["name"] for student in stats["students"]) if stats["students"] else "none"
        return {
            "message": f"There are {stats['total_students']} registered students. Students: {student_list}."
        }

    if subject_name and "how many classes happened" in message:
        item = next((row for row in subject_analytics if row["subject_name"] == subject_name), None)
        total_classes = item["total_classes"] if item else 0
        return {"message": f"{subject_name} has {total_classes} completed classes recorded in the system."}

    if subject_name and ("present in" in message or _contains_all(message, "students", "present")):
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT
                    SUM(CASE WHEN attendance.status IN ('Present', 'Late') THEN 1 ELSE 0 END) AS present_count,
                    SUM(CASE WHEN attendance.status = 'Absent' THEN 1 ELSE 0 END) AS absent_count
                FROM class_sessions
                LEFT JOIN attendance ON attendance.session_id = class_sessions.id
                WHERE class_sessions.subject_name = ?
                  AND class_sessions.session_date = ?
                """,
                (subject_name, datetime.now().strftime("%Y-%m-%d")),
            ).fetchone()
        finally:
            conn.close()
        return {
            "message": f"Today's {subject_name} session has {row['present_count'] or 0} present/late marks and {row['absent_count'] or 0} absences."
        }

    if "top attendance" in message:
        if not top_students:
            return {"message": "No ranked attendance data is available yet."}
        return {
            "message": "Here are the top 2 students by attendance percentage.",
            "table": {
                "columns": ["Student", "Class", "Attendance %"],
                "rows": [[item["name"], item["class_name"], item["attendance_percentage"]] for item in top_students],
            },
        }

    if "lowest attendance" in message or "below attendance threshold" in message or "low attendance" in message:
        if "below attendance threshold" in message and stats.get("low_attendance_students"):
            return {
                "message": f"{len(stats['low_attendance_students'])} students are below the configured attendance threshold.",
                "table": {
                    "columns": ["Student", "Class", "Attendance %"],
                    "rows": [
                        [item["name"], item["class_name"], item["attendance_percentage"]]
                        for item in stats["low_attendance_students"]
                    ],
                },
            }
        if not low_students:
            return {"message": "No ranked attendance data is available yet."}
        return {
            "message": "Here are the top 2 students with the lowest attendance.",
            "table": {
                "columns": ["Student", "Class", "Attendance %"],
                "rows": [[item["name"], item["class_name"], item["attendance_percentage"]] for item in low_students],
            },
        }

    if "subject analytics" in message or "highest attendance overall" in message or "lowest attendance overall" in message:
        if not subject_analytics:
            return {"message": "No subject analytics are available yet."}
        ranked = []
        for row in subject_analytics:
            total_marks = (row["attended_marks"] or 0) + (row["absent_marks"] or 0)
            percentage = round(((row["attended_marks"] or 0) / total_marks) * 100, 2) if total_marks else 0
            ranked.append((row["subject_name"], percentage, row["total_classes"]))
        best = max(ranked, key=lambda item: (item[1], item[0]))
        worst = min(ranked, key=lambda item: (item[1], item[0]))
        return {
            "message": f"Highest overall subject attendance: {best[0]} at {best[1]}%. Lowest overall subject attendance: {worst[0]} at {worst[1]}%.",
            "chart": _chart_payload(
                "bar",
                [item[0] for item in ranked],
                [{"label": "Attendance %", "data": [item[1] for item in ranked], "backgroundColor": "#0ea5e9"}],
                "Subject-Wise Attendance",
            ),
        }

    if "class-wise attendance" in message or "class attendance chart" in message or "which class had the lowest attendance" in message or "which class had the highest attendance" in message:
        ranked = []
        for row in class_analytics:
            total_marks = (row["attended_marks"] or 0) + (row["absent_marks"] or 0)
            percentage = round(((row["attended_marks"] or 0) / total_marks) * 100, 2) if total_marks else 0
            ranked.append((row["class_name"], percentage, row["total_sessions"]))
        best = max(ranked, key=lambda item: (item[1], item[0])) if ranked else None
        worst = min(ranked, key=lambda item: (item[1], item[0])) if ranked else None
        return {
            "message": (
                f"Highest attendance class: {best[0]} at {best[1]}%. Lowest attendance class: {worst[0]} at {worst[1]}%."
                if best and worst
                else "No class-wise attendance analytics are available yet."
            ),
            "chart": _chart_payload(
                "bar",
                [item[0] for item in ranked],
                [{"label": "Attendance %", "data": [item[1] for item in ranked], "backgroundColor": "#14b8a6"}],
                "Class-Wise Attendance",
            ) if ranked else None,
        }

    if "attendance trend summary" in message or "trend summary" in message or "attendance summary" in message:
        best_name = stats.get("highest_student") or "N/A"
        low_name = stats.get("lowest_student") or "N/A"
        return {
            "message": (
                f"In the last 7 days, {stats['present_count']} students are present today, "
                f"{stats['absent_count']} are absent today, top attendance is {best_name}, "
                f"and lowest attendance is {low_name}."
            )
        }

    if (
        "weekly trend" in message
        or "attendance trend by week" in message
        or "full attendance chart" in message
        or "show attendance in last 7 days" in message
        or "7-day graph" in message
        or "attendance chart" in message
        or "graph" in message
    ):
        return {
            "message": "Here is the weekly attendance trend for completed sessions.",
            "chart": _chart_payload(
                "line",
                [item["session_date"] for item in weekly_trend],
                [
                    {"label": "Attended", "data": [item["attended_marks"] or 0 for item in weekly_trend], "borderColor": "#22c55e", "backgroundColor": "rgba(34,197,94,0.2)"},
                    {"label": "Absent", "data": [item["absent_marks"] or 0 for item in weekly_trend], "borderColor": "#ef4444", "backgroundColor": "rgba(239,68,68,0.2)"},
                ],
                "Weekly Attendance Trend",
            ),
        }

    return {
        "message": "I can answer attendance-focused admin questions about subject analytics, top and low attendance students, class-wise attendance, threshold alerts, and weekly trends."
    }


def generate_assistant_reply(user_message, stats=None):
    reply = generate_admin_assistant_reply(user_message)
    return reply["message"]
