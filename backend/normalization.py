"""
Core grade-normalization logic for the Grade Normalizer backend.

The public entry points are:
  build_normalized_dataframe()   — returns just the result DataFrame
  build_normalized_with_debug()  — returns the DataFrame plus a debug payload

Both delegate to the private _compute_normalization(), which:
  1. Extracts the optional "Points Possible" row from the raw CSV.
  2. Strips non-student rows (_clean_rows).
  3. Resolves which columns to use for labs, Debug Dungeon, attendance, and
     Final Exam 2 based solely on the TA's UI selections (_resolve_columns).
  4. Computes per-student Lab and Attendance scores using configurable formula
     weights (_compute_normalization / _dynamic_denominator).

Formula math (default weights):
  lab_component  = (lab_raw_sum  / lab_denominator)  * 40
  dd_component   = (dd_raw_sum   / dd_denominator)   * 60
  lab_value      = (lab_component + dd_component)     * 5.3   max ≈ 530 pts
  attendance     = raw_attendance * 1.2               max ≈ 120 pts
  (multiplier skipped when the column is literally "Attendance Total")

All eight weights are overridable per-CRN via the formula_config dict loaded
from the course_config database table.
"""

import pandas as pd

from constants import LAB_TOTAL_POINTS, ATTENDANCE_TOTAL_POINTS

DEFAULT_LAB_DENOMINATOR = 300.0
DEFAULT_DD_DENOMINATOR = 230.0


def _normalize_header_name(value):
    """Collapse whitespace and lowercase a header string for reliable comparisons."""
    return " ".join(value.strip().split()).lower()


def _is_attendance_total_column(column_name):
    """Return True if the column is the Canvas "Attendance Total" aggregate.

    When this column is selected the multiplier is skipped. The value is
    already a total rather than a raw per-session score.
    """
    return _normalize_header_name(column_name) == "attendance total"


def _clean_rows(df):
    """Remove non-student rows from the DataFrame.

    Canvas gradebook exports include a "Points Possible" meta-row and sometimes
    blank rows. Both are excluded so normalization operates only on real student
    records. The index is reset so subsequent positional operations work correctly.
    """
    if len(df) == 0:
        return df
    first_col = df.columns[0]
    df = df[~df[first_col].astype(str).str.contains("Points Possible", case=False, na=False)]
    df = df[df[first_col].notna()]
    df = df[df[first_col].astype(str).str.strip() != ""]
    return df.reset_index(drop=True)


def count_gradesheet_data_rows(df):
    """How many student/data rows will be normalized (excludes Points Possible and blank name rows)."""
    return len(_clean_rows(df.copy()))


def _extract_points_possible_row(df):
    """Return the "Points Possible" row from the raw DataFrame, or None if absent.

    Canvas always inserts this row immediately after the header. It stores the
    maximum possible score for each assignment column and is used to compute
    dynamic denominators so the normalization stays correct even when point
    totals change between semesters.
    """
    if len(df) == 0:
        return None

    first_col = df.columns[0]
    mask = df[first_col].astype(str).str.contains("Points Possible", case=False, na=False)
    if not mask.any():
        return None

    return df[mask].iloc[0]


def _dynamic_denominator(points_row, columns, default_value):
    """Sum the Points Possible values across the given columns.

    If the CSV has a Points Possible row, its values are used as the denominator
    for the relevant score bucket. If the row is absent, or the sum works out to
    zero (e.g. all extra-credit columns whose Points Possible is 0), the
    caller-supplied default_value is used instead.
    """
    if points_row is None or not columns:
        return default_value

    total = 0.0
    for col in columns:
        if col not in points_row.index:
            continue
        value = pd.to_numeric(points_row[col], errors="coerce")
        if pd.notna(value):
            total += float(value)

    return total if total > 0 else default_value


def _resolve_columns(
    clean_df,
    selected_fields,
    selected_attendance_override=None,
    selected_final_exam_override=None,
):
    """Map the TA's UI selections to concrete DataFrame column lists.

    Labs and Debug Dungeon columns come exclusively from the checked checkboxes
    (selected_fields). Attendance and Final Exam fall back to Canvas naming
    conventions when no dropdown override is supplied.

    Returns a 5-tuple: (student_col, labs, debug_dungeon, attendance_col, final_exam_col)
    where any element may be None/empty if the relevant columns are absent.
    """
    # User selections are authoritative for labs and debug dungeon.
    # Whatever the TA checked is exactly what goes into the calculation — no hidden
    # prefix-detection layer filtering or overriding the selection.
    student_col = "Student" if "Student" in clean_df.columns else None
    labs = []
    debug_dungeon = []

    for field in selected_fields:
        col_name = field["column"]
        category = field["category"]
        lower_name = col_name.lower()

        if category == "labs":
            if col_name in clean_df.columns:
                labs.append(col_name)
        elif category in ("debug_dungeon", "participation"):
            if col_name in clean_df.columns:
                debug_dungeon.append(col_name)
        elif student_col is None and "student" in lower_name and "id" not in lower_name:
            if col_name in clean_df.columns:
                student_col = col_name

    attendance_col = None
    if selected_attendance_override and selected_attendance_override in clean_df.columns:
        attendance_col = selected_attendance_override
    if attendance_col is None:
        for col in clean_df.columns:
            if _normalize_header_name(col) == "attendance total":
                attendance_col = col
                break

    # Final exam: dropdown override → auto-detect by Canvas naming convention → nothing.
    final_exam_col = None
    if selected_final_exam_override and selected_final_exam_override in clean_df.columns:
        final_exam_col = selected_final_exam_override
    if final_exam_col is None:
        for col in clean_df.columns:
            normalized = _normalize_header_name(col)
            if normalized.startswith("final exam part2 - coding assessment"):
                final_exam_col = col
                break
    if not final_exam_col:
        for col in clean_df.columns:
            normalized = _normalize_header_name(col)
            if normalized == "final exam part2" or normalized.startswith("final exam part2 "):
                if "score" not in normalized and "current" not in normalized and "unposted" not in normalized:
                    final_exam_col = col
                    break

    # Deduplicate while preserving the TA's selection order.
    labs = list(dict.fromkeys(labs))
    debug_dungeon = list(dict.fromkeys(debug_dungeon))

    return student_col, labs, debug_dungeon, attendance_col, final_exam_col


def _compute_normalization(
    source_df,
    selected_fields,
    selected_attendance_override=None,
    selected_final_exam_override=None,
    formula_config=None,
):
    # Resolve formula weights — fall back to module-level defaults for any key not provided.
    cfg = formula_config or {}
    lab_weight          = float(cfg.get("lab_weight",              40.0))
    dd_weight           = float(cfg.get("dd_weight",               60.0))
    lab_scale           = float(cfg.get("lab_scale",               5.3))
    lab_total_pts       = float(cfg.get("lab_total_points",        LAB_TOTAL_POINTS))
    attendance_mult     = float(cfg.get("attendance_multiplier",   1.2))
    attendance_total_pts = float(cfg.get("attendance_total_points", ATTENDANCE_TOTAL_POINTS))
    lab_denom_fallback  = float(cfg.get("lab_denominator_fallback", DEFAULT_LAB_DENOMINATOR))
    dd_denom_fallback   = float(cfg.get("dd_denominator_fallback",  DEFAULT_DD_DENOMINATOR))

    points_row = _extract_points_possible_row(source_df)
    clean_df = _clean_rows(source_df.copy())
    student_col, labs, debug_dungeon, attendance_col, final_exam_col = _resolve_columns(
        clean_df,
        selected_fields,
        selected_attendance_override=selected_attendance_override,
        selected_final_exam_override=selected_final_exam_override,
    )

    for col in labs + debug_dungeon:
        if col in clean_df.columns:
            clean_df[col] = pd.to_numeric(clean_df[col], errors="coerce").fillna(0)

    if attendance_col and attendance_col in clean_df.columns:
        clean_df[attendance_col] = pd.to_numeric(clean_df[attendance_col], errors="coerce").fillna(0)

    if final_exam_col and final_exam_col in clean_df.columns:
        clean_df[final_exam_col] = pd.to_numeric(clean_df[final_exam_col], errors="coerce").fillna(0)

    lab_denominator = _dynamic_denominator(points_row, labs, lab_denom_fallback)
    dd_denominator  = _dynamic_denominator(points_row, debug_dungeon, dd_denom_fallback)

    if labs:
        lab_raw_sum   = clean_df[labs].sum(axis=1)
        lab_component = (lab_raw_sum / lab_denominator) * lab_weight
    else:
        lab_raw_sum   = pd.Series(0.0, index=clean_df.index)
        lab_component = pd.Series(0.0, index=clean_df.index)

    if debug_dungeon:
        dd_raw_sum   = clean_df[debug_dungeon].sum(axis=1)
        dd_component = (dd_raw_sum / dd_denominator) * dd_weight
    else:
        dd_raw_sum   = pd.Series(0.0, index=clean_df.index)
        dd_component = pd.Series(0.0, index=clean_df.index)

    lab_value = (lab_component + dd_component) * lab_scale

    attendance_series = (
        clean_df[attendance_col]
        if attendance_col and attendance_col in clean_df.columns
        else pd.Series(0.0, index=clean_df.index)
    )
    if attendance_col and _is_attendance_total_column(attendance_col):
        attendance_value = attendance_series
    else:
        # Multiply by the attendance multiplier (default 1.2) to give students
        # some cushion, then clamp to the configured maximum so scores never
        # exceed attendance_total_pts (default 120).
        attendance_value = (attendance_series * attendance_mult).clip(upper=attendance_total_pts)

    final_exam_2 = (
        clean_df[final_exam_col]
        if final_exam_col and final_exam_col in clean_df.columns
        else pd.Series(0.0, index=clean_df.index)
    )

    result_df = pd.DataFrame()
    result_df["Student"] = (
        clean_df[student_col] if student_col and student_col in clean_df.columns else "Unknown"
    )
    result_df["Lab Total"]         = int(lab_total_pts)
    result_df["Attendance Total"]  = int(attendance_total_pts)
    result_df["Lab"]               = lab_value.round(0).astype(int)
    result_df["Attendance"]        = attendance_value.round(0).astype(int)
    result_df["Final Exam 2"]      = final_exam_2

    debug_rows = []
    for idx in clean_df.index:
        debug_rows.append(
            {
                "student": str(result_df.loc[idx, "Student"]),
                "lab_raw_sum": float(lab_raw_sum.loc[idx]),
                "lab_component": float(lab_component.loc[idx]),
                "dd_raw_sum": float(dd_raw_sum.loc[idx]),
                "dd_component": float(dd_component.loc[idx]),
                "lab_before_rounding": float(lab_value.loc[idx]),
                "attendance_source": float(attendance_series.loc[idx]),
                "attendance_before_rounding": float(attendance_value.loc[idx]),
                "final_exam_2": float(final_exam_2.loc[idx]),
            }
        )

    debug_payload = {
        "detected_columns": {
            "student": student_col,
            "attendance": attendance_col,
            "final_exam_2": final_exam_col,
            "labs": labs,
            "debug_dungeon": debug_dungeon,
        },
        "selection_summary": {
            "selected_field_count": int(len(selected_fields)),
            "labs_used_count": int(len(labs)),
            "debug_dungeon_used_count": int(len(debug_dungeon)),
            "selected_attendance_override": selected_attendance_override,
            "selected_final_exam_override": selected_final_exam_override,
        },
        "denominators": {
            "lab": float(lab_denominator),
            "debug_dungeon": float(dd_denominator),
            "lab_source": "dynamic_from_points_possible" if points_row is not None else "default_300",
            "debug_dungeon_source": "dynamic_from_points_possible" if points_row is not None else "default_230",
        },
        "row_counts": {
            "input_rows": int(len(source_df)),
            "clean_rows": int(len(clean_df)),
            "student_rows_normalized": int(len(clean_df)),
            "points_possible_rows_found": int(
                source_df[source_df[source_df.columns[0]].astype(str).str.contains("Points Possible", case=False, na=False)].shape[0]
            )
            if len(source_df) > 0
            else 0,
        },
        "per_student": debug_rows,
    }

    return result_df, debug_payload


def build_normalized_dataframe(
    df,
    selected_fields,
    selected_attendance_override=None,
    selected_final_exam_override=None,
    formula_config=None,
):
    """Normalize grades and return only the result DataFrame.

    Convenience wrapper around _compute_normalization for callers that do not
    need the debug payload (i.e. the standard /normalize endpoint).
    """
    result_df, _ = _compute_normalization(
        df.copy(),
        selected_fields,
        selected_attendance_override=selected_attendance_override,
        selected_final_exam_override=selected_final_exam_override,
        formula_config=formula_config,
    )
    return result_df


def build_normalized_with_debug(
    df,
    selected_fields,
    selected_attendance_override=None,
    selected_final_exam_override=None,
    formula_config=None,
):
    """Normalize grades and return both the result DataFrame and the debug payload.

    Used by the /normalize/debug endpoint so TAs can verify denominator values,
    column detection, and intermediate per-student calculations.
    """
    return _compute_normalization(
        df.copy(),
        selected_fields,
        selected_attendance_override=selected_attendance_override,
        selected_final_exam_override=selected_final_exam_override,
        formula_config=formula_config,
    )
