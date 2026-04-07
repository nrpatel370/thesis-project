"""
Application-wide constants.

LAB_TOTAL_POINTS and ATTENDANCE_TOTAL_POINTS are the default maximum output
values shown in the results table. Both can be overridden per-CRN via the
formula configuration stored in course_config (see db.py).
"""

# Fallback user when no CRN is provided in a request.
DEFAULT_USER_ID = "temp_user_001"

# Default maximum output scores (used when no custom formula config is saved).
LAB_TOTAL_POINTS = 530
ATTENDANCE_TOTAL_POINTS = 120
