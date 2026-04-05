import pandas as pd


def to_json_safe_value(value):
    if pd.isna(value) or value is None:
        return None
    if isinstance(value, (int, float)):
        if value != value or value in (float("inf"), float("-inf")):
            return None
        return float(value) if isinstance(value, float) else int(value)
    return str(value)


def rows_to_json_safe_records(df):
    records = []
    columns = df.columns.tolist()
    for _, row in df.iterrows():
        row_dict = {}
        for col in columns:
            row_dict[col] = to_json_safe_value(row[col])
        records.append(row_dict)
    return records
