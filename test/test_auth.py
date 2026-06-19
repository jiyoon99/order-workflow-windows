import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from auth import AuthStore


class AuthTests(unittest.TestCase):
    def test_password_is_hashed_and_login_creates_session(self):
        with tempfile.TemporaryDirectory() as directory:
            users_path = Path(directory) / "users.json"
            store = AuthStore(users_path)
            store.create_user("worker1", "Worker One", "password123", "worker", datetime.now(timezone.utc).isoformat())
            raw = users_path.read_text(encoding="utf-8")
            self.assertNotIn("password123", raw)
            if os.name != "nt":
                self.assertEqual(users_path.stat().st_mode & 0o777, 0o600)
            authenticated = store.authenticate("worker1", "password123")
            self.assertIsNotNone(authenticated)
            token, user = authenticated
            self.assertEqual(user["displayName"], "Worker One")
            self.assertEqual(store.user_for_token(token)["username"], "worker1")

    def test_rejects_short_password(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(Path(directory) / "users.json")
            with self.assertRaises(ValueError):
                store.create_user("worker1", "Worker One", "1234", "worker", "now")

    def test_supports_all_business_roles(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(Path(directory) / "users.json")
            roles = ["owner", "developer", "as_manager", "sales_manager", "md", "worker"]
            for index, role in enumerate(roles):
                user = store.create_user(f"user{index}", f"User {index}", "password123", role, "now")
                self.assertEqual(user["role"], role)

    def test_legacy_admin_is_returned_as_owner(self):
        with tempfile.TemporaryDirectory() as directory:
            store = AuthStore(Path(directory) / "users.json")
            user = store.create_user("legacy", "Legacy Admin", "password123", "admin", "now")
            self.assertEqual(user["role"], "owner")


if __name__ == "__main__":
    unittest.main()
