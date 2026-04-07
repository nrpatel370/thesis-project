"""
Serialization helpers for converting pandas DataFrames to JSON-safe records.

pandas uses numpy scalar types (float64, int64) and special float values
(NaN, inf) that are not natively JSON-serializable. These helpers normalize
every cell to a plain Python int, float, str, or None before the data is
passed to Flask's jsonify.
"""

import pandas as pd


def to_json_safe_value(value):
    """Convert a single cell value to a JSON-serializable Python scalar.

    NaN and infinite floats are returned as None so the JSON output remains
    valid. numpy int/float types are cast to their plain Python equivalents.
    Everything else is coerced to a string as a safe fallback.
    """
    if pd.isna(value) or value is None:
        return None
    if isinstance(value, (int, float)):
        # Guard against NaN (value != value) and ±inf which json.dumps rejects.
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return float(value) if isinstance(value, float) else int(value)
    return str(value)


def rows_to_json_safe_records(df):
    """Return a list of dicts representing each row in the DataFrame.

    Equivalent to df.to_dict('records') but with every value passed through
    to_json_safe_value so the result is always safe to JSON-serialize.
    """
    records = []
    columns = df.columns.tolist()
    for _, row in df.iterrows():
        row_dict = {}
        for col in columns:
            row_dict[col] = to_json_safe_value(row[col])
        records.append(row_dict)
    return records
