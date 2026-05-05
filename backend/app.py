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
  GET  /exists/<user_id>      — check whether a CRN has any saved data

All endpoints include CORS headers so the static frontend can reach the API
from a different origin during local development.
"""

import io
import json
import re
import uuid

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

# In-memory store for multi-file upload batches.
# Each entry lives until consumed by /normalize/multi or the server restarts.
# Structure: { batch_id: [ {filename, df, row_count}, … ] }
_batch_store: dict = {}


def _strip_canvas_id(col_name: str) -> str:
    """Remove Canvas's trailing numeric assignment ID from a column name.

    Canvas appends a unique integer ID in parentheses to every graded item,
    e.g. 'Lab Assignment 1 (2174782)'.  The same assignment exported from two
    different section gradebooks will carry different IDs, causing the multi-file
    union to treat them as separate columns.  Stripping the suffix normalises
    names so they merge correctly.

    Only a *purely numeric* parenthetical at the very end of the string is
    removed, so columns like 'Final (Curved)' or 'Lab (Week 1)' are untouched.
    Metadata columns (Student, ID, SIS User ID, Section \u2026) never carry this
    suffix and are returned unchanged.
    """
    return re.sub(r"\s*\(\d+\)\s*$", "", col_name).strip()


def _normalise_column_names(df: pd.DataFrame) -> pd.DataFrame:
    """Strip Canvas IDs from all column headers and resolve any resulting duplicates.

    If two columns in the same file normalise to the same base name (i.e. Canvas
    gave the same assignment name two different IDs), they get a simple counter
    suffix \u2014 '(2)', '(3)' \u2014 so pandas never silently overwrites one with the other.
    """
    seen: dict = {}
    new_names = []
    for col in df.columns:
        base = _strip_canvas_id(col)
        if base in seen:
            seen[base] += 1
            new_names.append(f"{base} ({seen[base]})")
        else:
            seen[base] = 0
            new_names.append(base)
    df.columns = new_names
    return df


def read_grades_csv(csv_text):
    """Parse gradebook CSV; strip BOM and normalise Canvas column IDs."""
    text = csv_text.lstrip("\ufeff") if isinstance(csv_text, str) else csv_text
    df = pd.read_csv(io.StringIO(text))
    return _normalise_column_names(df)


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
@app.route("/upload/multi", methods=["OPTIONS"])
@app.route("/save-preferences", methods=["OPTIONS"])
@app.route("/preferences/<user_id>", methods=["OPTIONS"])
@app.route("/categories", methods=["OPTIONS"])
@app.route("/categories/defaults", methods=["OPTIONS"])
@app.route("/categories/<user_id>", methods=["OPTIONS"])
@app.route("/normalize", methods=["OPTIONS"])
@app.route("/normalize/debug", methods=["OPTIONS"])
@app.route("/normalize/multi", methods=["OPTIONS"])
@app.route("/config", methods=["OPTIONS"])
@app.route("/config/<user_id>", methods=["OPTIONS"])
@app.route("/exists/<user_id>", methods=["OPTIONS"])
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
            # Prune old uploads - keep only the latest row per user to avoid unbounded growth.
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


@app.route("/upload/multi", methods=["POST"])
def upload_multi_csv():
    """Parse up to 5 Canvas CSV exports and return a unified column categorization.

    Files are supplied as multipart fields named file0, file1, … file4.
    The union of all column headers is categorized once using the CRN's saved
    (or default) category keywords. A column_presence map indicates which file
    indices contain each column, so the frontend can warn the TA about mismatches.

    The parsed DataFrames are stored in _batch_store under a generated batch_id
    which the frontend must include in the subsequent /normalize/multi request.
    """
    files = [request.files.get(f"file{i}") for i in range(5)]
    files = [f for f in files if f is not None]

    if not files:
        return json_error("No files provided")

    user_id = request.form.get("user_id", DEFAULT_USER_ID)

    # Parse every uploaded file, failing fast on the first bad one.
    parsed = []
    for f in files:
        if not f.filename.endswith(".csv"):
            return json_error(f"'{f.filename}' must be a CSV file")
        try:
            csv_content = f.read().decode("utf-8")
            df = read_grades_csv(csv_content)
            if df.empty:
                return json_error(f"'{f.filename}' appears to be empty")
            parsed.append({"filename": f.filename, "df": df})
        except Exception as e:
            return json_error(f"Failed to parse '{f.filename}': {e}", 500)

    # Build the union of all column headers.
    seen_cols: set = set()
    union_columns: list = []
    for p in parsed:
        for col in p["df"].columns.tolist():
            if col not in seen_cols:
                union_columns.append(col)
                seen_cols.add(col)

    # Track which file indices contain each column (used for mismatch badges).
    column_presence = {
        col: [i for i, p in enumerate(parsed) if col in p["df"].columns]
        for col in union_columns
    }

    # Categorize the union of columns with the CRN's saved (or default) categories.
    custom_categories = get_user_categories_from_db(user_id)
    categories = categorise_columns(union_columns, custom_categories)

    # Store parsed DataFrames for the subsequent /normalize/multi call.
    batch_id = str(uuid.uuid4())
    _batch_store[batch_id] = [
        {
            "filename": p["filename"],
            "df": p["df"],
            "row_count": count_gradesheet_data_rows(p["df"]),
        }
        for p in parsed
    ]

    return jsonify(
        {
            "batch_id": batch_id,
            "file_count": len(parsed),
            "files": [
                {"filename": e["filename"], "row_count": e["row_count"]}
                for e in _batch_store[batch_id]
            ],
            "categories": categories,
            "column_presence": column_presence,
        }
    ), 200


@app.route("/normalize/multi", methods=["POST"])
def normalize_multi():
    """Normalize all files in a batch with a single set of column preferences.

    Accepts batch_id (from /upload/multi) plus the same selected_fields,
    attendance/final exam overrides, and optional formula_config as /normalize.
    Each file is normalized independently and results are concatenated into one
    DataFrame with a 'Section' column identifying the source filename.

    When debug=true is included in the body, the first file's per-student
    breakdown is returned alongside the combined results.

    The batch is removed from _batch_store after successful normalization.
    """
    data = request.get_json()
    if not data:
        return json_error("No JSON body provided")

    batch_id = data.get("batch_id")
    if not batch_id:
        return json_error("batch_id is required")
    if batch_id not in _batch_store:
        return json_error("Batch not found or expired. Please re-upload your files.", 404)

    user_id = data.get("user_id", DEFAULT_USER_ID)
    selected_fields = data.get("selected_fields", [])
    selected_attendance_column = data.get("selected_attendance_column")
    selected_final_exam_column = data.get("selected_final_exam_column")
    include_debug = bool(data.get("debug", False))

    if not selected_fields:
        return json_error("No fields selected")

    inline_config = data.get("formula_config")
    formula_config = inline_config if inline_config is not None else get_formula_config_from_db(user_id)

    batch = _batch_store[batch_id]
    result_dfs = []
    first_debug = None

    try:
        for i, entry in enumerate(batch):
            if include_debug and i == 0:
                result_df, first_debug = build_normalized_with_debug(
                    entry["df"],
                    selected_fields,
                    selected_attendance_override=selected_attendance_column,
                    selected_final_exam_override=selected_final_exam_column,
                    formula_config=formula_config,
                )
            else:
                result_df = build_normalized_dataframe(
                    entry["df"],
                    selected_fields,
                    selected_attendance_override=selected_attendance_column,
                    selected_final_exam_override=selected_final_exam_column,
                    formula_config=formula_config,
                )

            # Prepend a Section column so the TA can identify each row's origin.
            result_df.insert(0, "File Name", entry["filename"])
            result_dfs.append(result_df)

        combined_df = pd.concat(result_dfs, ignore_index=True)

        # Consume the batch (no longer needed after normalization succeeds).
        del _batch_store[batch_id]

        payload = {
            "message": "Grades normalized successfully",
            "columns": combined_df.columns.tolist(),
            "data": rows_to_json_safe_records(combined_df),
            "row_count": len(combined_df),
            "file_count": len(batch),
        }
        if include_debug and first_debug:
            payload["debug"] = first_debug
            payload["debug_note"] = (
                f"Debug details shown for first section only: {batch[0]['filename']}"
            )

        return jsonify(payload), 200

    except Exception as error:
        return json_error(f"Normalization failed: {error}", 500)


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

        # Prefer an inline formula_config from the request body (used by guest sessions
        # which cannot persist config to the DB) over the stored per-CRN config.
        inline_config = data.get("formula_config")
        formula_config = inline_config if inline_config is not None else get_formula_config_from_db(user_id)
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

        # Same inline-config precedence as /normalize
        inline_config = data.get("formula_config")
        formula_config = inline_config if inline_config is not None else get_formula_config_from_db(user_id)
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


@app.route("/exists/<user_id>", methods=["GET"])
def check_user_exists(user_id):
    """Return {"exists": true} if the CRN has any saved data, false otherwise.

    Used by the frontend Register/Login buttons to show the appropriate
    contextual message before entering the app.
    """
    try:
        user_id = str(user_id).strip()
        with get_db() as conn:
            # A CRN is considered "registered" if it has at least one row in
            # any of the three per-user tables
            row = conn.execute(
                """
                SELECT 1 FROM user_preferences WHERE user_id = ?
                UNION ALL
                SELECT 1 FROM user_categories   WHERE user_id = ?
                UNION ALL
                SELECT 1 FROM course_config      WHERE user_id = ?
                LIMIT 1
                """,
                (user_id, user_id, user_id),
            ).fetchone()
        return jsonify({"exists": row is not None}), 200
    except Exception as error:
        return json_error(f"Database error: {error}", 500)


if __name__ == "__main__":
    init_db()
    app.run(debug=True, port=5001)