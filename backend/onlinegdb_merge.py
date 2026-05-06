"""
OnlineGDB grade merge logic for the Grade Normalizer backend.

Public API (used by app.py endpoints):
  merge_gdb_with_results()  — match OnlineGDB rows into normalized Canvas results
  resolve_ambiguous()       — apply TA-supplied resolutions for ambiguous entries

Name-matching strategy
----------------------
Canvas exports names as "Last, First [Middle]".
OnlineGDB exports names as "First [Middle] Last" (or just "First" with no last name).

Matching proceeds in priority order for each Canvas student:
  1. Exact key match   (normalised last + normalised first)
  2. Full-name match   (all tokens normalised and concatenated)
  3. Swapped full-name (handle "Last, First" ↔ "First Last" flip)
  4. Alternate key     (last tokens on each side — helps hyphenated names)
  5. First-name-only   (only when the GDB entry has no last name AND that
                        first name is unique among all no-last-name GDB entries)

Ambiguous first-name-only entries (same first name, multiple GDB rows, no last
name) are returned to the caller so the TA can resolve them via a UI dropdown.
Unmatched students receive score "0" (intentional per spec).
Test students (name contains "test", case-insensitive) are removed before any
matching takes place.
"""

import copy
import re
from dataclasses import dataclass
from typing import Optional, Tuple


# ---------------------------------------------------------------------------
# Internal normalisation helpers
# ---------------------------------------------------------------------------

def _norm_piece(s: str) -> str:
    """Lowercase and strip all punctuation/whitespace from a name token."""
    s = s.strip().lower()
    return re.sub(r"[^a-z0-9]+", "", s)


def _norm_full_name(s: str) -> str:
    """Normalise an entire name string to a single lowercase token."""
    return _norm_piece(s)


@dataclass(frozen=True)
class NameKey:
    last: str
    first: str


# ---------------------------------------------------------------------------
# Name parsers
# ---------------------------------------------------------------------------

def _parse_classroom_name(full: str) -> Tuple[Optional[NameKey], str]:
    """Parse an OnlineGDB / classroom-style "First [Middle] Last" name.

    Returns (NameKey(last, first), full_normalised).  NameKey is None when the
    name has fewer than two tokens (e.g. a single first name only).
    Middle initials (single-letter last token) are skipped so "Giovanni Vega H"
    resolves to last="vega", first="giovanni".
    """
    full_norm = _norm_full_name(full)
    parts = [p for p in re.split(r"\s+", full.strip()) if p]
    if len(parts) >= 2:
        first = _norm_piece(parts[0])
        last_token = parts[-1].strip().rstrip(".")
        if len(parts) >= 3 and len(last_token) == 1:
            last = _norm_piece(parts[-2])
        else:
            last = _norm_piece(parts[-1])
        if first and last:
            return NameKey(last=last, first=first), full_norm
    return None, full_norm


def _parse_combined_name(full: str) -> Tuple[Optional[NameKey], str]:
    """Parse a Canvas-style "Last, First [Middle]" name.

    Returns (NameKey(last, first), full_normalised).  NameKey is None when the
    string contains no comma or either side is empty.  Hyphenated last names
    (e.g. "Vega-Hernandez") use only the first hyphen-segment for key matching
    so they can match a GDB entry that only listed "Vega".
    """
    full_norm = _norm_full_name(full)
    if "," in full:
        left, right = full.split(",", 1)
        last_side_first = re.split(r"[-\s]+", left.strip())[0] if left.strip() else ""
        last_token = [p for p in re.split(r"\s+", last_side_first.strip()) if p]
        first_token = [p for p in re.split(r"\s+", right.strip()) if p]
        if last_token and first_token:
            last = _norm_piece(last_token[0])
            first = _norm_piece(first_token[0])
            if last and first:
                return NameKey(last=last, first=first), full_norm
    return None, full_norm


def _parse_combined_name_alt(full: str) -> Optional[NameKey]:
    """Alternate Canvas-name parse using the *last* token on each side.

    Useful when compound last names differ by which segment appears in GDB.
    """
    if "," not in full:
        return None
    left, right = full.split(",", 1)
    left_parts = [p for p in re.split(r"\s+", left.strip()) if p]
    right_parts = [p for p in re.split(r"\s+", right.strip()) if p]
    if not left_parts or not right_parts:
        return None
    last = _norm_piece(re.split(r"[-\s]+", left_parts[-1])[0])
    first = _norm_piece(right_parts[-1])
    if last and first:
        return NameKey(last=last, first=first)
    return None


def _first_from_canvas(canvas_name: str) -> str:
    """Extract the normalised first name from a Canvas "Last, First" string."""
    if "," in canvas_name:
        _, right = canvas_name.split(",", 1)
        parts = [p for p in re.split(r"\s+", right.strip()) if p]
        if parts:
            return _norm_piece(parts[0])
    return ""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def merge_gdb_with_results(
    normalized_rows: list,
    gdb_rows: list,
    *,
    gdb_name_col: str = "Student Name",
    gdb_total_col: str = "Total Grade",
    canvas_name_col: str = "Student",
    output_col: str = "OnlineGDB Total",
) -> dict:
    """Match OnlineGDB scores into normalized Canvas grade rows.

    Args:
        normalized_rows: List of dicts from the /normalize/multi response.
        gdb_rows:        List of dicts parsed from the OnlineGDB CSV export.
        gdb_name_col:    Column in gdb_rows containing student names.
        gdb_total_col:   Column in gdb_rows containing the total grade.
        canvas_name_col: Column in normalized_rows containing student names.
        output_col:      Name of the new column appended to each result row.

    Returns a dict with keys:
        rows                  — merged rows (test students removed)
        columns               — updated column list (output_col appended)
        ambiguous             — list of {gdb_name, gdb_score, candidates}
                                for first-name-only GDB entries that are not
                                unique and therefore need manual resolution
        matched               — count of auto-matched students
        unmatched             — list of Canvas names that got score "0"
        test_students_removed — count of rows dropped (name contains "test")
        output_col            — the column name that was added
    """
    # ------------------------------------------------------------------
    # Step 1: Remove test students from Canvas results
    # ------------------------------------------------------------------
    test_removed_count = 0
    clean_rows = []
    for row in normalized_rows:
        name = str(row.get(canvas_name_col, ""))
        if "test" in name.lower():
            test_removed_count += 1
        else:
            clean_rows.append(copy.copy(row))

    # ------------------------------------------------------------------
    # Step 2: Build lookup structures from GDB rows
    # ------------------------------------------------------------------
    by_key: dict = {}          # NameKey(last, first) → score
    by_full: dict = {}         # full-normalised-string → score
    # first_only_all: norm_first → [(raw_gdb_name, score), ...]
    first_only_all: dict = {}

    for row in gdb_rows:
        # Pandas converts empty cells to float NaN; guard against both "" and "nan".
        raw_name = str(row.get(gdb_name_col, "") or "").strip()
        if not raw_name or raw_name.lower() == "nan":
            continue
        raw_score = str(row.get(gdb_total_col, "") or "").strip()
        if raw_score.lower() == "nan":
            raw_score = ""

        parts = [p for p in re.split(r"\s+", raw_name.strip()) if p]

        if len(parts) == 1:
            # Single-token name — can only match by first name
            first_norm = _norm_piece(parts[0])
            if first_norm:
                first_only_all.setdefault(first_norm, []).append((raw_name, raw_score))
        else:
            key, full_norm = _parse_classroom_name(raw_name)
            if full_norm and full_norm not in by_full:
                by_full[full_norm] = raw_score
            if key and key not in by_key:
                by_key[key] = raw_score

    # Unique first-name-only entries → safe to auto-match
    by_first_only_unique: dict = {
        k: v[0][1]
        for k, v in first_only_all.items()
        if len(v) == 1
    }
    # Ambiguous first-name-only entries → need manual resolution
    first_only_ambiguous: dict = {
        k: v
        for k, v in first_only_all.items()
        if len(v) > 1
    }

    # ------------------------------------------------------------------
    # Step 3: Match each Canvas student
    # ------------------------------------------------------------------
    matched = 0
    unmatched_names: list = []
    # canvas_name → norm_first for students flagged as ambiguous
    ambiguous_canvas: dict = {}

    for row in clean_rows:
        canvas_name = str(row.get(canvas_name_col, "")).strip()
        key, full_norm = _parse_combined_name(canvas_name)

        score = None

        # Priority 1: exact key (normalised last + first)
        if score is None and key:
            score = by_key.get(key)

        # Priority 2: full normalised string match
        if score is None and full_norm:
            score = by_full.get(full_norm)

        # Priority 3: swap "Last, First" → "FirstLast" to match GDB full-norm
        if score is None and "," in canvas_name:
            left, right = canvas_name.split(",", 1)
            swapped = _norm_full_name(f"{right.strip()} {left.strip()}")
            if swapped:
                score = by_full.get(swapped)

        # Priority 4: alternate key (last tokens on each side)
        if score is None:
            alt_key = _parse_combined_name_alt(canvas_name)
            if alt_key:
                score = by_key.get(alt_key)

        # Priority 5: first-name-only (unique)
        if score is None:
            first_norm_only = _first_from_canvas(canvas_name)
            if first_norm_only:
                if first_norm_only in by_first_only_unique:
                    score = by_first_only_unique[first_norm_only]
                elif first_norm_only in first_only_ambiguous:
                    # Flag for manual resolution — leave blank for now
                    ambiguous_canvas[canvas_name] = first_norm_only
                    score = None

        if score is not None:
            matched += 1
            row[output_col] = score
        elif canvas_name in ambiguous_canvas:
            row[output_col] = ""   # TA must resolve this
        else:
            unmatched_names.append(canvas_name)
            row[output_col] = "0"  # intentional default per spec

    # ------------------------------------------------------------------
    # Step 4: Build the ambiguous resolution list with candidates
    # ------------------------------------------------------------------
    # Candidates for a given first name = Canvas students flagged ambiguous
    # for that first name (i.e. the ones whose row still has an empty score).
    ambiguous: list = []
    for first_norm, gdb_entries in first_only_ambiguous.items():
        candidates = [
            name
            for name, fn in ambiguous_canvas.items()
            if fn == first_norm
        ]
        for gdb_name, gdb_score in gdb_entries:
            ambiguous.append({
                "gdb_name": gdb_name,
                "gdb_score": gdb_score,
                "first_norm": first_norm,
                "candidates": candidates,
            })

    # Build the updated column list (append output_col if not already present)
    sample_cols = list(clean_rows[0].keys()) if clean_rows else []
    columns = sample_cols if output_col in sample_cols else sample_cols + [output_col]

    return {
        "rows": clean_rows,
        "columns": columns,
        "ambiguous": ambiguous,
        "matched": matched,
        "unmatched": unmatched_names,
        "test_students_removed": test_removed_count,
        "output_col": output_col,
    }


def resolve_ambiguous(
    rows: list,
    resolutions: list,
    *,
    canvas_name_col: str = "Student",
    output_col: str = "OnlineGDB Total",
) -> list:
    """Apply TA-supplied resolutions for ambiguous first-name-only matches.

    Args:
        rows:        The merged rows returned by merge_gdb_with_results()
                     (ambiguous students have output_col == "").
        resolutions: List of dicts, each with:
                       canvas_name — the Canvas student name to update
                       gdb_score   — the score to assign
        canvas_name_col: Column holding the Canvas student name.
        output_col:      Column to update with the resolved score.

    Returns the updated rows list.
    """
    resolution_map = {r["canvas_name"]: r["gdb_score"] for r in resolutions}
    result = []
    for row in rows:
        row = copy.copy(row)
        name = str(row.get(canvas_name_col, ""))
        # Only overwrite rows that are still blank (ambiguous) — never clobber
        # a score that was already auto-matched.
        if name in resolution_map and row.get(output_col, "") == "":
            row[output_col] = resolution_map[name]
        result.append(row)
    return result
