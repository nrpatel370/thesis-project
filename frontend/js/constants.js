// Base URL for all API requests. Update this if the Flask server moves to a
// different host or port (e.g. behind a reverse proxy in production).
export const API_URL = "http://localhost:5001";

// Accepted CRN value. Replace or extend with a proper lookup/validation
// strategy when deploying to support multiple real course sections.
export const VALID_CRN = "123456";

// Human-readable display labels for each backend category key.
// Used in renderCategoryGroup() to title each column group in the UI.
export const CATEGORY_LABELS = {
    assignments: "Assignments",
    labs: "Lab Assignments",
    exams: "Exams",
    attendance: "Attendance",
    debug_dungeon: "Debug Dungeon",
    participation: "Participation",
    final_scores: "Final Scores",
    unposted: "Unposted Scores",
    other: "Other",
};
