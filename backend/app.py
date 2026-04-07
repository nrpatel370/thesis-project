"""
Flask REST API for the Grade Normalizer application.

Endpoints:
  POST /upload                — parse a Canvas CSV export and return categorized columns
  POST /normalize             — normalize grades using the TA's column selections
  POST /normalize/debug       — same as /normalize but includes per-student debug info
  GET  /categories/defaults   — return the built-in keyword-to-category mapping
  GET  /categories/<user_id>  — return the saved (or default) categories for a CRN
  POST /categories            — save custom categories for a CRN
  GET  /preferences/<user_id> — return the saved column-selection preferences for a CRN
  POST /save-preferences      — persist column-selection preferences for a CRN
  GET  /config/<user_id>      — return the saved (or default) formula weights for a CRN
  POST /config                — save custom formula weights for a CRN

All endpoints include CORS headers so the static frontend can reach the API
from a different origin during local development.
"""

import io
import json

import pandas as pd
from flask import Flask, jsonify, request

from categories import categorise_columns, get_default_categories
from constants import DEFAULT_USER_ID
from db import get_db, get_latest_uploaded_csv, init_db
from normalization import (
    build_normalized_dataframe,
    build_normalized_with_debug,
    count_gradesheet_data_rows,
)
from serializers import rows_to_json_safe_records

app = Flask(__name__)


def read_grades_csv(csv_text):
    """Parse gradebook CSV; strip BOM so column names match across exports."""
    text = csv_text.lstrip("\ufeff") if isinstance(csv_text, str) else csv_text
    return pd.read_csv(io.StringIO(text))


def json_error(message, status_code=400):
    return jsonify({"error": message}), status_code


# default formula weights returned when a CRN has no saved config.
# These match the placeholder text shown in the frontend formula inputs.
FORMULA_DEFAULTS = {
    "lab_weight": 40.0,
    "dd_weight": 60.0,
    "lab_scale": 5.3,
    "lab_total_points": 530.0,
    "attendance_multiplier": 1.2,
    "attendance_total_points": 120.0,
    "lab_denominator_fallback": 300.0,
    "dd_denominator_fallback": 230.0,
}


def validate_formula_config(config):
    """Return a list of error strings; empty list means the config is valid."""
    errors = []
    for field, value in config.items():
        if field not in FORMULA_DEFAULTS:
            continue  # silently ignore unknown keys
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            errors.append(f"'{field}' must be a number")
        elif value <= 0:
            errors.append(f"'{field}' must be greater than zero")
    return errors


def get_formula_config_from_db(user_id):
    """Return the saved formula config dict for this CRN, or None if not set."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT config FROM course_config WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return json.loads(row["config"]) if row else None


def get_user_categories_from_db(user_id):
    """Return the saved custom categories dict for this CRN, or None if not set."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT categories FROM user_categories WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    return json.loads(row["categories"]) if row else None


@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response


@app.route("/upload", methods=["OPTIONS"])
@app.route("/save-preferences", methods=["OPTIONS"])
@app.route("/preferences/<user_id>", methods=["OPTIONS"])
@app.route("/categories", methods=["OPTIONS"])
@app.route("/categories/defaults", methods=["OPTIONS"])
@app.route("/categories/<user_id>", methods=["OPTIONS"])
@app.route("/normalize", methods=["OPTIONS"])
@app.route("/normalize/debug", methods=["OPTIONS"])
@app.route("/config", methods=["OPTIONS"])
@app.route("/config/<user_id>", methods=["OPTIONS"])
def handle_preflight(user_id=None):
    return jsonify({"status": "ok"}), 200


@app.route("/upload", methods=["POST"])
def upload_csv():
    if "file" not in request.files:
        return json_error("No file provided")

    file = request.files["file"]
    user_id = request.form.get("user_id", DEFAULT_USER_ID)
    if not file.filename.endswith(".csv"):
        return json_error("File must be a CSV")

    try:
        csv_content = file.read().decode("utf-8")
        df = read_grades_csv(csv_content)
        if df.empty:
            return json_error("CSV file is empty")

        with get_db() as conn:
            conn.execute(
                "INSERT INTO uploaded_csvs (user_id, csv_data) VALUES (?, ?)",
                (user_id, csv_content),
            )
            # Prune old uploads — keep only the latest row per user to avoid unbounded growth.
            conn.execute(
                """
                DELETE FROM uploaded_csvs
                WHERE user_id = ? AND id NOT IN (
                    SELECT id FROM uploaded_csvs WHERE user_id = ? ORDER BY uploaded_at DESC LIMIT 1
                )
                """,
                (user_id, user_id),
            )
            conn.commit()

        columns = df.columns.tolist()
        custom_categories = get_user_categories_from_db(user_id)
        categories = categorise_columns(columns, custom_categories)
        student_row_count = count_gradesheet_data_rows(df)
        return jsonify(
            {
                "columns": columns,
                "categories": categories,
                "row_count": student_row_count,
                "raw_csv_row_count": int(len(df)),
            }
        ), 200
    except Exception as error:
        return json_error(f"Failed to parse CSV: {error}", 500)


@app.route("/normalize", methods=["POST"])
def normalize_grades():
    data = request.get_json()
    if not data:
        return json_error("No JSON body provided")

    user_id = data.get("user_id", DEFAULT_USER_ID)
    selected_fields = data.get("selected_fields", [])
    selected_attendance_column = data.get("selected_attendance_column")
    selected_final_exam_column = data.get("selected_final_exam_column")
    if not selected_fields:
        return json_error("No fields selected")

    try:
        csv_data = get_latest_uploaded_csv(user_id)
        if not csv_data:
            return json_error("No CSV data found. Please upload a CSV first.", 404)

        formula_config = get_formula_config_from_db(user_id)
        input_df = read_grades_csv(csv_data)
        result_df = build_normalized_dataframe(
            input_df,
            selected_fields,
            selected_attendance_override=selected_attendance_column,
            selected_final_exam_override=selected_final_exam_column,
            formula_config=formula_config,
        )
        return jsonify(
            {
                "message": "Grades normalized successfully",
                "columns": result_df.columns.tolist(),
                "data": rows_to_json_safe_records(result_df),
                "row_count": len(result_df),
            }
        ), 200
    except Exception as error:
        return json_error(f"Normalization failed: {error}", 500)


@app.route("/normalize/debug", methods=["POST"])
def normalize_grades_debug():
    data = request.get_json()
    if not data:
        return json_error("No JSON body provided")

    user_id = data.get("user_id", DEFAULT_USER_ID)
    selected_fields = data.get("selected_fields", [])
    selected_attendance_column = data.get("selected_attendance_column")
    selected_final_exam_column = data.get("selected_final_exam_column")
    if not selected_fields:
        return json_error("No fields selected")

    try:
        csv_data = get_latest_uploaded_csv(user_id)
        if not csv_data:
            return json_error("No CSV data found. Please upload a CSV first.", 404)

        formula_config = get_formula_config_from_db(user_id)
        input_df = read_grades_csv(csv_data)
        result_df, debug_payload = build_normalized_with_debug(
            input_df,
            selected_fields,
            selected_attendance_override=selected_attendance_column,
            selected_final_exam_override=selected_final_exam_column,
            formula_config=formula_config,
        )
        return jsonify(
            {
                "message": "Grades normalized successfully (debug)",
                "columns": result_df.columns.tolist(),
                "data": rows_to_json_safe_records(result_df),
                "row_count": len(result_df),
                "debug": debug_payload,
            }
        ), 200
    except Exception as error:
        return json_error(f"Debug normalization failed: {error}", 500)


@app.route("/categories/defaults", methods=["GET"])
def get_defaults():
    return jsonify(get_default_categories()), 200


@app.route("/categories/<user_id>", methods=["GET"])
def get_user_categories(user_id):
    try:
        categories = get_user_categories_from_db(user_id)
        if categories:
            return jsonify({"categories": categories, "is_custom": True}), 200
        return jsonify({"categories": get_default_categories(), "is_custom": False}), 200
    except Exception as error:
        return json_error(f"Database error: {error}", 500)


@app.route("/categories", methods=["POST"])
def save_categories():
    data = request.get_json()
    if not data:
        return json_error("No JSON body provided")

    user_id = data.get("user_id", "default_user")
    course_id = data.get("course_id")
    categories = data.get("categories", {})
    if not categories:
        return json_error("No categories provided")

    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM user_categories WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE user_categories
                    SET categories = ?, course_id = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (json.dumps(categories), course_id, user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO user_categories (user_id, course_id, categories) VALUES (?, ?, ?)",
                    (user_id, course_id, json.dumps(categories)),
                )
            conn.commit()
        return jsonify({"message": "Categories saved successfully", "user_id": user_id}), 200
    except Exception as error:
        return json_error(f"Database error: {error}", 500)


@app.route("/save-preferences", methods=["POST"])
def save_preferences():
    data = request.get_json()
    if not data:
        return json_error("No JSON body provided")

    user_id = str(data.get("user_id", "default_user")).strip()
    course_id = data.get("course_id")
    preferences = data.get("preferences", [])
    selected_attendance_column = data.get("selected_attendance_column")
    selected_final_exam_column = data.get("selected_final_exam_column")
    if not preferences:
        return json_error("No preferences provided")

    # Store structured preferences so UI selections can be restored on refresh.
    if isinstance(preferences, list):
        preferences_payload = {
            "selected_columns": preferences,
            "selected_attendance_column": selected_attendance_column,
            "selected_final_exam_column": selected_final_exam_column,
        }
    elif isinstance(preferences, dict):
        preferences_payload = preferences
    else:
        return json_error("Invalid preferences format")

    try:
        with get_db() as conn:
            # Replace all rows for this CRN so duplicate legacy rows cannot mask updates.
            conn.execute("DELETE FROM user_preferences WHERE user_id = ?", (user_id,))
            conn.execute(
                """
                INSERT INTO user_preferences (user_id, course_id, preferences, updated_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (user_id, course_id, json.dumps(preferences_payload)),
            )
            conn.commit()

        return jsonify(
            {
                "message": "Preferences saved successfully",
                "user_id": user_id,
                "preferences": preferences_payload,
            }
        ), 200
    except Exception as error:
        return json_error(f"Database error: {error}", 500)


@app.route("/preferences/<user_id>", methods=["GET"])
def get_preferences(user_id):
    try:
        user_id = str(user_id).strip()
        with get_db() as conn:
            row = conn.execute(
                """
                SELECT * FROM user_preferences
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()

        if not row:
            return json_error("No preferences found for this user", 404)

        return jsonify(
            {
                "user_id": row["user_id"],
                "course_id": row["course_id"],
                "preferences": json.loads(row["preferences"]),
                "updated_at": row["updated_at"],
            }
        ), 200
    except Exception as error:
        return json_error(f"Database error: {error}", 500)


@app.route("/config/<user_id>", methods=["GET"])
def get_formula_config(user_id):
    try:
        config = get_formula_config_from_db(user_id)
        if config:
            return jsonify({"config": config, "is_custom": True}), 200
        return jsonify({"config": FORMULA_DEFAULTS, "is_custom": False}), 200
    except Exception as error:
        return json_error(f"Database error: {error}", 500)


@app.route("/config", methods=["POST"])
def save_formula_config():
    data = request.get_json()
    if not data:
        return json_error("No JSON body provided")

    user_id = str(data.get("user_id", "default_user")).strip()
    config = data.get("config", {})

    if not isinstance(config, dict):
        return json_error("Config must be an object")

    errors = validate_formula_config(config)
    if errors:
        return json_error("; ".join(errors), 400)

    try:
        with get_db() as conn:
            existing = conn.execute(
                "SELECT id FROM course_config WHERE user_id = ?",
                (user_id,),
            ).fetchone()
            if existing:
                conn.execute(
                    """
                    UPDATE course_config
                    SET config = ?, updated_at = CURRENT_TIMESTAMP
                    WHERE user_id = ?
                    """,
                    (json.dumps(config), user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO course_config (user_id, config) VALUES (?, ?)",
                    (user_id, json.dumps(config)),
                )
            conn.commit()
        return jsonify({"message": "Formula config saved", "user_id": user_id, "config": config}), 200
    except Exception as error:
        return json_error(f"Database error: {error}", 500)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001)