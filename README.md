# Grade Normalizer

A lightweight web app for Teaching Assistants to upload Canvas gradebook CSV exports and produce normalized grade outputs (Lab Total, Attendance, Final Exam 2) ready to use for final grade calculations.

---

## Overview

Canvas exports gradebooks with raw per-assignment scores scattered across dozens of columns. This tool lets TAs:

1. Verify their course CRN to load their saved settings.
2. Upload a Canvas CSV export for any lab section.
3. Review and adjust which columns feed into each grade bucket.
4. Run normalization and download the results as a clean CSV.

Preferences and formula weights are saved per-CRN so uploading a second section CSV for the same course requires no reconfiguration.

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Python 3, Flask, pandas, SQLite |
| Frontend | Vanilla JavaScript (ES modules), HTML, CSS |

No build step is required. The frontend is served as static files and communicates with the Flask API over HTTP.

---

## Project Structure

```
├── backend/
│   ├── app.py              # Flask REST API and route handlers
│   ├── categories.py       # Keyword-based column categorization
│   ├── constants.py        # Shared default values
│   ├── db.py               # SQLite helpers and schema initialization
│   ├── normalization.py    # Core grade-normalization logic
│   ├── serializers.py      # JSON-safe DataFrame serialization
│   └── requirements.txt
│
├── frontend/
│   ├── index.html
│   ├── script.js           # Main frontend logic 
│   ├── styles.css
│   └── js/
│       ├── constants.js    # API URL, CRN, category labels
│       └── helpers.js      # showMessage(), capitalize()
│
├── example.csv             # Sample Canvas gradebook export for testing
│
└── requirements.txt        # Txt file containg all installed external libraries  
```

---

## Setup

### Prerequisites

- Python 3.10+
- A modern browser (Chrome, Firefox, Safari, Edge)

### Install backend dependencies

```bash
cd backend
pip install -r requirements.txt
```

### Run the API server

```bash
cd backend
python app.py
```

The API starts on `http://localhost:5001`. The database file (`grades.db`) is created automatically on first run.

### Serve the frontend

Open `frontend/index.html` directly in your browser, or serve it with any static file server:

```bash
cd frontend
python -m http.server 8080
```

Then navigate to `http://localhost:8080`.

---

## Usage

### 1. Enter your CRN

Type your 5–6 digit Course Reference Number and press **Verify**. This loads any previously saved column preferences and formula weights for that course.


### 2. (Optional) Configure formula weights

Expand **Configure Formula Weights** to adjust any of the eight normalization parameters. Leave a field blank to use the server-side default. Click **Save Formula** to persist, or **Reset to Defaults** to clear all overrides.

| Field | Default | Description |
|---|---|---|
| Lab weight | 40 | Weight applied to the labs component |
| Debug Dungeon weight | 60 | Weight applied to the Debug Dungeon component |
| Lab score multiplier | 5.3 | Scales the combined lab+DD component |
| Lab total points | 530 | Denominator used as the Lab column ceiling |
| Attendance multiplier | 1.2 | Applied to raw attendance scores |
| Attendance total points | 120 | Denominator used as the Attendance column ceiling |
| Lab max points (fallback) | 300 | Fallback denominator if Points Possible row is absent |
| DD max points (fallback) | 230 | Fallback denominator if Points Possible row is absent |

### 3. Upload a Canvas CSV

Click the file area (or drag and drop) to select a `.csv` Canvas gradebook export. Click **Process & Normalize Grades**.

### 4. Select columns

The **Select Required Fields** panel shows all columns grouped by category:

- **Labs**, **Debug Dungeon**, and **Participation** columns have checkboxes and directly feed into the grade calculation.
- **Attendance** and **Final Exam** columns are controlled by dropdowns and only one column can be selected for each.
- All other categories (Assignments, Exams, Final Scores, etc.) are grayed out and excluded from the calculation.

Previously saved preferences are applied automatically. The **Select All** master checkbox and per-category checkboxes respect disabled items.

### 5. Save & Calculate

Click **Save & Calculate Grades** to persist your selection and run normalization. Results appear in a table below.

Click **Debug Normalize** to run the same calculation with a detailed breakdown showing denominators, intermediate values, and per-student debug rows.

### 6. Download results

Click **Download Results (CSV)** to export the normalized grade table. Click **Upload another CSV** to process another section without losing your preferences.

---

## Normalization Formula

```
lab_component  = (lab_raw_sum  / lab_denominator)  × lab_weight
dd_component   = (dd_raw_sum   / dd_denominator)   × dd_weight
Lab            = (lab_component + dd_component)     × lab_scale

Attendance     = raw_score × attendance_multiplier
               (multiplier is skipped when the column is "Attendance Total")

Final Exam 2   = raw score passed through unchanged
```

Denominators are read dynamically from the Canvas "Points Possible" row, falling back to the configurable fallback values when that row is absent.

**Extra credit:** Extra credit columns are categorized under Labs and summed into the numerator. Because Canvas sets their Points Possible to 0, they do not affect the denominator, so scores can exceed 100%.

---

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `POST` | `/upload` | Parse a Canvas CSV and return categorized columns |
| `POST` | `/normalize` | Run normalization, return result table |
| `POST` | `/normalize/debug` | Run normalization with debug payload |
| `GET` | `/categories/defaults` | Return built-in category keyword map |
| `GET` | `/categories/<user_id>` | Return saved (or default) categories for a CRN |
| `POST` | `/categories` | Save custom categories for a CRN |
| `GET` | `/preferences/<user_id>` | Return saved column preferences for a CRN |
| `POST` | `/save-preferences` | Persist column preferences for a CRN |
| `GET` | `/config/<user_id>` | Return saved (or default) formula weights for a CRN |
| `POST` | `/config` | Save custom formula weights for a CRN |

---

## Database Schema

SQLite database stored at `backend/grades.db`.

| Table | Purpose |
|---|---|
| `uploaded_csvs` | Stores the most recent CSV upload per CRN |
| `user_preferences` | Stores saved column-selection preferences per CRN |
| `user_categories` | Stores custom keyword-to-category mappings per CRN |
| `course_config` | Stores custom formula weights per CRN |

Only the latest CSV upload is kept per CRN — older rows are pruned automatically on each new upload to prevent unbounded growth.
