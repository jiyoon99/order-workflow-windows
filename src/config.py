from __future__ import annotations

import os
from datetime import timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"

DATA_FILE = Path(os.getenv("DATA_FILE", ROOT / "data" / "orders.json"))
USERS_FILE = Path(os.getenv("USERS_FILE", ROOT / "data" / "users.json"))
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", ROOT / "data" / "audit.jsonl"))
NOTICE_FILE = Path(os.getenv("NOTICE_FILE", ROOT / "data" / "login-notice.json"))

MEMBER_MANAGEMENT_ROLES = {"owner", "developer"}
CANCEL_ORDER_ROLES = {"owner", "developer", "as_manager", "sales_manager", "md"}
ORDER_ADMIN_ROLES = {"owner", "developer", "sales_manager", "md"}
ORDER_EDIT_ROLES = {"owner", "developer", "as_manager", "sales_manager", "md"}
AS_HISTORY_ROLES = {"owner", "developer", "as_manager", "sales_manager", "md"}
MONTHLY_STATS_ROLES = {"owner"}
DUPLICATE_CLEANUP_ROLES = {"owner", "md"}
LATEST_IMPORT_DELETE_ROLES = {"owner", "developer", "md"}
NOTICE_EDIT_ROLES = {"owner", "md"}
ROLE_RANK = {"owner": 0, "developer": 1, "as_manager": 2, "sales_manager": 3, "md": 4, "worker": 5}

MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_UPLOAD_FILES = 10
MAX_ARCHIVE_FILES = 20
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
BACKUP_RETENTION_DAYS = 14
LOGIN_FAILURE_LIMIT = 5
LOGIN_BLOCK_SECONDS = 5 * 60
LOCAL_TZ = timezone(timedelta(hours=9))
