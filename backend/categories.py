"""
Column categorization for the Grade Normalizer backend.

get_default_categories() defines the keyword-to-category mapping used when the
TA has not saved a custom configuration. Categories are matched in insertion order
and each column is assigned to the first category whose keywords appear anywhere
in the (lowercased) column name, so order matters — more specific categories should
come before broader ones.

categorise_columns() applies that mapping to the list of column names returned by
pandas after parsing a Canvas gradebook CSV export.
"""


def get_default_categories():
    """Return the default keyword-to-category mapping.

    Each key is a category name; the value is a dict with a `keywords` list.
    Column matching is case-insensitive and substring-based. A column is placed
    into the first category whose keyword appears anywhere in the column name.
    Categories not matched by any keyword fall into the implicit `other` bucket.
    """
    return {
        "assignments": {"keywords": ["hw", "homework", "project", "problem set", "ps"]},
        # "lab" is intentionally excluded since it is too broad and would match Canvas aggregate
        # columns like "Lab Total", "Lab Current Score", etc. Use "lab assignment" to target
        # only individual scored items. "extra credit" is included so those columns appear
        # in the labs group and are summed into the numerator; Canvas sets their Points
        # Possible to 0, so the denominator is unaffected and scores can exceed 100%.
        "labs": {"keywords": ["assign","lab assignment", "extra credit", "practical", "workshop"]},
        "exams": {"keywords": ["exam", "midterm", "final", "quiz", "test"]},
        "attendance": {"keywords": ["attendance", "roll call", "present", "absent"]},
        "debug_dungeon": {"keywords": ["debug dungeon", "dungeon", "dd week"]},
        "participation": {"keywords": ["participation", "engagement", "challenge"]},
        # Aggregate / summary columns exported by Canvas land here so they are never
        # accidentally included in a calculation bucket.
        "final_scores": {"keywords": [
            "final score", "total score", "course grade", "overall",
            "lab total", "current score", "current points", "final points",
        ]},
        "unposted": {"keywords": ["unposted", "unpublished", "pending"]},
    }


def categorise_columns(columns, custom_categories=None):
    """Assign each column name to exactly one category.

    Args:
        columns: Ordered list of column name strings from the CSV header row.
        custom_categories: Optional dict in the same format as get_default_categories().
            When provided it completely replaces the defaults (the TA's saved config).

    Returns:
        Dict mapping category name → list of column names. Empty categories are
        omitted so the caller can iterate only over categories that actually have
        columns. An implicit ``other`` key collects any columns that did not match
        any keyword.
    """
    category_defs = custom_categories or get_default_categories()
    categories = {cat: [] for cat in category_defs.keys()}
    categories["other"] = []

    for col in columns:
        lower = col.lower().strip()
        matched = False
        for cat_name, cat_config in category_defs.items():
            keywords = cat_config.get("keywords", [])
            # First matching category wins. Iteration order determines priority.
            if any(keyword in lower for keyword in keywords):
                categories[cat_name].append(col)
                matched = True
                break
        if not matched:
            categories["other"].append(col)

    # Drop categories with no columns so the frontend only renders populated groups.
    return {k: v for k, v in categories.items() if v}
