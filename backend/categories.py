def get_default_categories():
    return {
        "assignments": {"keywords": ["assign", "hw", "homework", "project", "problem set", "ps"]},
        # "lab" is intentionally excluded — it is too broad and would match Canvas aggregate
        # columns like "Lab Total", "Lab Current Score", etc. Use "lab assignment" to target
        # only individual scored items.
        "labs": {"keywords": ["lab assignment", "practical", "workshop"]},
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
    category_defs = custom_categories or get_default_categories()
    categories = {cat: [] for cat in category_defs.keys()}
    categories["other"] = []

    for col in columns:
        lower = col.lower().strip()
        matched = False
        for cat_name, cat_config in category_defs.items():
            keywords = cat_config.get("keywords", [])
            if any(keyword in lower for keyword in keywords):
                categories[cat_name].append(col)
                matched = True
                break
        if not matched:
            categories["other"].append(col)

    return {k: v for k, v in categories.items() if v}
