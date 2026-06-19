import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import server
from auth import AuthStore
from excel import read_first_sheet, write_xlsx

class OrderSortingTests(unittest.TestCase):
    def test_sorts_by_order_datetime_then_order_number_oldest_first(self):
        orders = [
            {"orderNumber": "2", "orderedAt": "2026-06-14 23:59:59"},
            {"orderNumber": "2", "orderedAt": "2026-06-15 01:00:00"},
            {"orderNumber": "10", "orderedAt": "2026-06-15 01:00:00"},
        ]
        sorted_orders = sorted(orders, key=server.order_datetime_key)
        self.assertEqual(
            [(order["orderedAt"], order["orderNumber"]) for order in sorted_orders],
            [
                ("2026-06-14 23:59:59", "2"),
                ("2026-06-15 01:00:00", "2"),
                ("2026-06-15 01:00:00", "10"),
            ],
        )

    def test_rejects_archives_with_too_many_excel_files(self):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for index in range(server.MAX_ARCHIVE_FILES + 1):
                archive.writestr(f"orders-{index}.xlsx", b"test")
        with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
            with self.assertRaisesRegex(ValueError, "최대"):
                server.archive_excel_files(archive)

    def test_rejects_invalid_content_length(self):
        handler = object.__new__(server.Handler)
        handler.rfile = io.BytesIO(b"payload")
        for value in ("invalid", "-1"):
            handler.headers = {"Content-Length": value}
            with self.assertRaisesRegex(ValueError, "크기"):
                handler._body()

    def test_writes_latest_shutdown_backup_only(self):
        with tempfile.TemporaryDirectory() as directory:
            original_data = server.DATA_FILE
            original_users = server.USERS_FILE
            original_audit = server.AUDIT_FILE
            try:
                server.DATA_FILE = Path(directory) / "orders.json"
                server.USERS_FILE = Path(directory) / "users.json"
                server.AUDIT_FILE = Path(directory) / "audit.jsonl"
                server.DATA_FILE.write_text(json.dumps([{"id": "first"}], ensure_ascii=False), encoding="utf-8")
                server.USERS_FILE.write_text(json.dumps([{"id": "user-1"}], ensure_ascii=False), encoding="utf-8")
                server.AUDIT_FILE.write_text("first-audit\n", encoding="utf-8")

                server.write_shutdown_backup()

                backup_dir = server.shutdown_backup_dir()
                self.assertTrue(backup_dir.exists())
                self.assertEqual(json.loads((backup_dir / "orders.json").read_text(encoding="utf-8")), [{"id": "first"}])
                self.assertEqual(json.loads((backup_dir / "users.json").read_text(encoding="utf-8")), [{"id": "user-1"}])
                self.assertEqual((backup_dir / "audit.jsonl").read_text(encoding="utf-8"), "first-audit\n")

                (backup_dir / "stale.txt").write_text("old", encoding="utf-8")
                server.DATA_FILE.write_text(json.dumps([{"id": "second"}], ensure_ascii=False), encoding="utf-8")
                server.USERS_FILE.write_text(json.dumps([{"id": "user-2"}], ensure_ascii=False), encoding="utf-8")
                server.AUDIT_FILE.write_text("second-audit\n", encoding="utf-8")

                server.write_shutdown_backup()

                self.assertFalse((backup_dir / "stale.txt").exists())
                self.assertEqual(json.loads((backup_dir / "orders.json").read_text(encoding="utf-8")), [{"id": "second"}])
                self.assertEqual(json.loads((backup_dir / "users.json").read_text(encoding="utf-8")), [{"id": "user-2"}])
                self.assertEqual((backup_dir / "audit.jsonl").read_text(encoding="utf-8"), "second-audit\n")
            finally:
                server.DATA_FILE = original_data
                server.USERS_FILE = original_users
                server.AUDIT_FILE = original_audit

    def test_removes_duplicates_within_one_import_batch(self):
        existing = [{"importKey": "existing"}]
        imported = [
            {"importKey": "new-1", "orderNumber": "1"},
            {"importKey": "existing", "orderNumber": "2"},
            {"importKey": "new-1", "orderNumber": "1-duplicate"},
            {"importKey": "new-2", "orderNumber": "3"},
        ]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual([order["orderNumber"] for order in added], ["1", "3"])

    def test_ignores_partial_content_when_building_dedupe_key(self):
        existing = [{
            "importKey": "first-import",
            "orderNumber": "ORDER-1",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "",
            "address": "",
        }]
        imported = [{
            "importKey": "second-import",
            "orderNumber": "ORDER-2",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "",
            "address": "",
        }]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual([order["orderNumber"] for order in added], ["ORDER-2"])

    def test_requires_matching_address_for_content_based_duplicates(self):
        existing = [{
            "importKey": "first-import",
            "orderNumber": "ORDER-1",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구",
        }]
        imported = [{
            "importKey": "second-import",
            "orderNumber": "ORDER-2",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 서초구",
        }]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual([order["orderNumber"] for order in added], ["ORDER-2"])

    def test_normalizes_whitespace_and_case_for_content_based_duplicates(self):
        existing = [{
            "importKey": "first-import",
            "recipient": "홍길동",
            "productName": "Notebook Pro",
            "optionName": "16GB",
            "address": "서울시 강남구  테헤란로 1",
        }]
        imported = [{
            "importKey": "second-import",
            "recipient": " 홍길동 ",
            "productName": "notebook pro",
            "optionName": "16gb",
            "address": "서울시  강남구 테헤란로   1",
        }]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual(added, [])

    def test_normalizes_address_punctuation_for_content_based_duplicates(self):
        existing = [{
            "importKey": "first-import",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구 테헤란로 1, 101동",
        }]
        imported = [{
            "importKey": "second-import",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구 테헤란로 1 101동",
        }]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual(added, [])

    def test_uses_phone_when_available_for_content_based_duplicates(self):
        existing = [{
            "importKey": "first-import",
            "orderNumber": "ORDER-1",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구 테헤란로 1",
            "phone": "010-1234-5678",
        }]
        imported = [{
            "importKey": "second-import",
            "orderNumber": "ORDER-2",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구 테헤란로 1",
            "phone": "010-9999-0000",
        }]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual([order["orderNumber"] for order in added], ["ORDER-2"])

    def test_removes_duplicate_orders_with_same_content(self):
        existing = [{
            "importKey": "first-import",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구",
        }]
        imported = [
            {
                "importKey": "second-import",
                "recipient": "홍길동",
                "productName": "노트북",
                "optionName": "기본형",
                "address": "서울시 강남구",
            }
        ]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual(added, [])

    def test_keeps_same_product_when_address_differs(self):
        existing = [{
            "importKey": "first-import",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구",
        }]
        imported = [
            {
                "importKey": "second-import",
                "recipient": "홍길동",
                "productName": "노트북",
                "optionName": "기본형",
                "address": "서울시 서초구",
            }
        ]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual(len(added), 1)

    def test_detects_shipping_update_for_same_order_number(self):
        existing = [{
            "id": "order-1",
            "importKey": "first-import",
            "orderNumber": "ORDER-1",
            "recipient": "홍길동",
            "phone": "010-1111-2222",
            "postalCode": "12345",
            "address": "서울시 강남구",
            "deliveryMessage": "문 앞",
            "productName": "노트북",
            "optionName": "기본형",
            "shippingDone": False,
        }]
        imported = [{
            "importKey": "second-import",
            "orderNumber": "ORDER-1",
            "recipient": "홍길동",
            "phone": "010-9999-0000",
            "postalCode": "54321",
            "address": "서울시 서초구",
            "deliveryMessage": "경비실",
            "productName": "노트북",
            "optionName": "기본형",
            "sourceFile": "changed-address.xlsx",
        }]
        added, shipping_updates = server.new_unique_orders(existing, imported, "2026-06-18T00:00:00+00:00")
        self.assertEqual(added, [])
        self.assertEqual(shipping_updates, 1)
        self.assertEqual(existing[0]["pendingShippingUpdate"]["fields"]["address"], "서울시 서초구")
        self.assertEqual(existing[0]["pendingShippingUpdate"]["changed"]["phone"]["incoming"], "010-9999-0000")

    def test_removes_duplicate_orders_against_shipped_orders(self):
        existing = [{
            "importKey": "shipped-import",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구",
            "shippingDone": True,
            "archivedAt": "2026-06-17T00:00:00+00:00",
        }]
        imported = [{
            "importKey": "fresh-import",
            "recipient": "홍길동",
            "productName": "노트북",
            "optionName": "기본형",
            "address": "서울시 강남구",
        }]
        added, _ = server.new_unique_orders(existing, imported)
        self.assertEqual(added, [])
    def test_blocks_repeated_login_failures_and_clears_on_success(self):
        key = "127.0.0.1:test-user"
        server.LOGIN_FAILURES.clear()
        for index in range(server.LOGIN_FAILURE_LIMIT):
            server.record_login_result(key, False, now=float(index))
        self.assertTrue(server.login_blocked(key, now=float(server.LOGIN_FAILURE_LIMIT)))
        server.record_login_result(key, True)
        self.assertFalse(server.login_blocked(key))

    def test_creates_daily_order_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            original = server.DATA_FILE
            try:
                server.DATA_FILE = Path(directory) / "orders.json"
                server.write_orders([{"id": "first"}])
                server.write_orders([{"id": "second"}])
                if os.name != "nt":
                    self.assertEqual(server.DATA_FILE.stat().st_mode & 0o777, 0o600)
                backups = list((Path(directory) / "backups").glob("orders.json.*.bak"))
                self.assertEqual(len(backups), 1)
                if os.name != "nt":
                    self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
                self.assertEqual(json.loads(backups[0].read_text(encoding="utf-8")), [{"id": "first"}])
            finally:
                server.DATA_FILE = original

    def test_writes_minimal_audit_record(self):
        with tempfile.TemporaryDirectory() as directory:
            original = server.AUDIT_FILE
            try:
                server.AUDIT_FILE = Path(directory) / "audit.jsonl"
                server.write_audit("order_updated", {"id": "user-1", "displayName": "작업자"}, orderId="order-1", action="production")
                record = json.loads(server.AUDIT_FILE.read_text(encoding="utf-8"))
                if os.name != "nt":
                    self.assertEqual(server.AUDIT_FILE.stat().st_mode & 0o777, 0o600)
                self.assertEqual(record["event"], "order_updated")
                self.assertEqual(record["orderId"], "order-1")
                self.assertNotIn("phone", record)
            finally:
                server.AUDIT_FILE = original

    def test_reads_orders_with_utf8_bom(self):
        with tempfile.TemporaryDirectory() as directory:
            original = server.DATA_FILE
            try:
                server.DATA_FILE = Path(directory) / "orders.json"
                server.DATA_FILE.write_bytes(b"\xef\xbb\xbf" + json.dumps([{"id": "order-1"}], ensure_ascii=False).encode("utf-8"))
                self.assertEqual(server.read_orders(), [{"id": "order-1"}])
            finally:
                server.DATA_FILE = original


class ServerFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        server.DATA_FILE = Path(cls.temp.name) / "orders.json"
        server.USERS_FILE = Path(cls.temp.name) / "users.json"
        server.AUTH = AuthStore(server.USERS_FILE)
        cls.cookie = ""
        cls.httpd = server.OrderHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.port = cls.httpd.server_address[1]
        payload = json.dumps({"username": "admin", "displayName": "관리자", "password": "password123"}).encode()
        status, _, _ = cls.request_raw("POST", "/api/auth/setup", payload, {"Content-Type": "application/json", "Content-Length": str(len(payload))})
        assert status == 201

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    @classmethod
    def request_raw(cls, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        request_headers = dict(headers or {})
        if cls.cookie: request_headers["Cookie"] = cls.cookie
        connection.request(method, path, body=body, headers=request_headers)
        response = connection.getresponse()
        content = response.read()
        set_cookie = response.getheader("Set-Cookie")
        if set_cookie: cls.cookie = set_cookie.split(";", 1)[0]
        connection.close()
        return response.status, response.getheaders(), content

    def request(self, method, path, body=None, headers=None):
        return self.request_raw(method, path, body, headers)

    def login(self, username, password="password123"):
        payload = json.dumps({"username": username, "password": password}).encode()
        return self.request(
            "POST",
            "/api/auth/login",
            payload,
            {"Content-Type": "application/json", "Content-Length": str(len(payload))},
        )

    def logout(self):
        return self.request("POST", "/api/auth/logout")

    def test_business_role_permissions(self):
        for username, display_name, role in [
            ("developer-role", "개발자", "developer"),
            ("as-role", "AS담당자", "as_manager"),
            ("sales-role", "판매담당자", "sales_manager"),
            ("md-role", "MD담당자", "md"),
        ]:
            payload = json.dumps({"username": username, "displayName": display_name, "password": "password123", "role": role}).encode()
            self.assertEqual(self.request("POST", "/api/users", payload, {"Content-Type": "application/json", "Content-Length": str(len(payload))})[0], 201)

        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("developer-role")[0], 200)
        self.assertEqual(self.request("GET", "/api/users")[0], 200)
        forbidden_owner = json.dumps({"username": "owner-by-dev", "displayName": "개발자생성", "password": "password123", "role": "owner"}).encode()
        self.assertEqual(self.request("POST", "/api/users", forbidden_owner, {"Content-Type": "application/json", "Content-Length": str(len(forbidden_owner))})[0], 403)

        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("sales-role")[0], 200)
        self.assertEqual(self.request("POST", "/api/import")[0], 400)

        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("md-role")[0], 200)
        self.assertEqual(self.request("GET", "/api/export/shipped")[0], 400)

        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("as-role")[0], 200)
        self.assertEqual(self.request("GET", "/api/users")[0], 403)
        self.assertEqual(self.request("GET", "/api/orders/as-history")[0], 200)
        self.assertEqual(self.request("POST", "/api/import")[0], 403)
        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("admin")[0], 200)

    def test_cancel_order_moves_it_out_of_active_orders(self):
        self.logout()
        self.assertEqual(self.login("admin")[0], 200)
        payload = json.dumps({
            "orderNumber": "CANCEL-001", "productName": "취소 테스트 상품",
            "quantity": 1, "amount": 10000, "recipient": "취소고객", "phone": "010-0000-0000",
        }).encode()
        headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        status, _, content = self.request("POST", "/api/orders/manual", payload, headers)
        self.assertEqual(status, 201)
        order = json.loads(content)

        missing_reason = json.dumps({"action": "cancel", "reason": ""}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", missing_reason, {"Content-Type": "application/json", "Content-Length": str(len(missing_reason))})
        self.assertEqual(status, 400)

        cancel = json.dumps({"action": "cancel", "reason": "고객 요청"}).encode()
        status, _, content = self.request("PATCH", f"/api/orders/{order['id']}", cancel, {"Content-Type": "application/json", "Content-Length": str(len(cancel))})
        self.assertEqual(status, 200)
        cancelled = json.loads(content)
        self.assertEqual(cancelled["cancelReason"], "고객 요청")
        self.assertEqual(cancelled["cancelledBy"], "관리자")

        status, _, content = self.request("GET", "/api/orders")
        self.assertNotIn(order["id"], [item["id"] for item in json.loads(content)])
        status, _, content = self.request("GET", "/api/orders/cancelled")
        self.assertIn(order["id"], [item["id"] for item in json.loads(content)])

        worker_payload = json.dumps({"username": "cancel-worker", "displayName": "취소일반", "password": "password123", "role": "worker"}).encode()
        self.assertEqual(self.request("POST", "/api/users", worker_payload, {"Content-Type": "application/json", "Content-Length": str(len(worker_payload))})[0], 201)
        second_payload = json.dumps({
            "orderNumber": "CANCEL-002", "productName": "권한 테스트 상품",
            "quantity": 1, "amount": 10000, "recipient": "권한고객", "phone": "010-0000-0001",
        }).encode()
        status, _, content = self.request("POST", "/api/orders/manual", second_payload, {"Content-Type": "application/json", "Content-Length": str(len(second_payload))})
        second_order = json.loads(content)
        self.logout()
        self.assertEqual(self.login("cancel-worker")[0], 200)
        status, _, _ = self.request("PATCH", f"/api/orders/{second_order['id']}", cancel, {"Content-Type": "application/json", "Content-Length": str(len(cancel))})
        self.assertEqual(status, 403)
        self.logout()
        self.assertEqual(self.login("admin")[0], 200)
        status, _, _ = self.request("PATCH", f"/api/orders/{second_order['id']}", cancel, {"Content-Type": "application/json", "Content-Length": str(len(cancel))})
        self.assertEqual(status, 200)

        for role, username, display_name, order_number in [
            ("md", "cancel-md", "취소MD", "CANCEL-004"),
            ("as_manager", "cancel-as", "취소AS", "CANCEL-005"),
            ("sales_manager", "cancel-sales", "취소판매", "CANCEL-006"),
        ]:
            role_payload = json.dumps({"username": username, "displayName": display_name, "password": "password123", "role": role}).encode()
            self.assertEqual(self.request("POST", "/api/users", role_payload, {"Content-Type": "application/json", "Content-Length": str(len(role_payload))})[0], 201)
            order_payload = json.dumps({
                "orderNumber": order_number, "productName": f"{role} 제작 완료 취소",
                "quantity": 1, "amount": 10000, "recipient": f"{display_name}고객", "phone": "010-0000-0003",
            }).encode()
            status, _, content = self.request("POST", "/api/orders/manual", order_payload, {"Content-Type": "application/json", "Content-Length": str(len(order_payload))})
            self.assertEqual(status, 201)
            order_item = json.loads(content)
            production = json.dumps({"action": "production", "checked": True}).encode()
            self.assertEqual(self.request("PATCH", f"/api/orders/{order_item['id']}", production, {"Content-Type": "application/json", "Content-Length": str(len(production))})[0], 200)
            self.logout()
            self.assertEqual(self.login(username)[0], 200)
            status, _, _ = self.request("PATCH", f"/api/orders/{order_item['id']}", cancel, {"Content-Type": "application/json", "Content-Length": str(len(cancel))})
            self.assertEqual(status, 200)
            self.logout()
            self.assertEqual(self.login("admin")[0], 200)

        third_payload = json.dumps({
            "orderNumber": "CANCEL-003", "productName": "출고 완료 취소 테스트 상품",
            "quantity": 1, "amount": 10000, "recipient": "출고완료고객", "phone": "010-0000-0002",
        }).encode()
        status, _, content = self.request("POST", "/api/orders/manual", third_payload, {"Content-Type": "application/json", "Content-Length": str(len(third_payload))})
        self.assertEqual(status, 201)
        third_order = json.loads(content)
        production = json.dumps({"action": "production", "checked": True}).encode()
        self.assertEqual(self.request("PATCH", f"/api/orders/{third_order['id']}", production, {"Content-Type": "application/json", "Content-Length": str(len(production))})[0], 200)
        inspection = json.dumps({"action": "softwareInspection", "checked": True}).encode()
        self.assertEqual(self.request("PATCH", f"/api/orders/{third_order['id']}", inspection, {"Content-Type": "application/json", "Content-Length": str(len(inspection))})[0], 200)
        shipping = json.dumps({"action": "shipping", "checked": True}).encode()
        self.assertEqual(self.request("PATCH", f"/api/orders/{third_order['id']}", shipping, {"Content-Type": "application/json", "Content-Length": str(len(shipping))})[0], 200)
        status, _, content = self.request("PATCH", f"/api/orders/{third_order['id']}", cancel, {"Content-Type": "application/json", "Content-Length": str(len(cancel))})
        self.assertEqual(status, 200)
        cancelled_shipped = json.loads(content)
        self.assertEqual(cancelled_shipped["id"], third_order["id"])
        self.assertEqual(cancelled_shipped["cancelReason"], "고객 요청")

        status, _, content = self.request("GET", "/api/orders")
        active_ids = [item["id"] for item in json.loads(content)]
        self.assertNotIn(third_order["id"], active_ids)
        self.assertNotIn(order["id"], active_ids)

        status, _, content = self.request("GET", "/api/orders/cancelled")
        cancelled_ids = [item["id"] for item in json.loads(content)]
        self.assertIn(order["id"], cancelled_ids)
        self.assertIn(second_order["id"], cancelled_ids)
        self.assertIn(third_order["id"], cancelled_ids)

        status, _, content = self.request("GET", "/api/orders/as-history")
        as_history_ids = [item["id"] for item in json.loads(content)]
        self.assertNotIn(third_order["id"], as_history_ids)
        self.assertEqual(as_history_ids, [])

    def test_complete_order_flow(self):
        boundary = "order-workflow-test-boundary"
        chunks = []
        workbooks = [
            ("kakao.xlsx", write_xlsx(
                ["결제번호", "채널상품번호", "상품명", "수령인명", "수량", "주문일시"],
                [["KAKAO-1", "K-PRODUCT", "카카오 상품", "카카오 고객", "1", "2026-06-11 10:00:00"]],
            )),
            ("coupang.xlsx", write_xlsx(
                ["주문번호", "묶음배송번호", "등록상품명", "수취인이름", "구매수(수량)", "주문일"],
                [
                    ["COUPANG-1", "BUNDLE-1", "쿠팡 상품 1", "쿠팡 고객 1", "1", "2026-06-11 11:00:00"],
                    ["COUPANG-2", "BUNDLE-2", "쿠팡 상품 2", "쿠팡 고객 2", "1", "2026-06-11 12:00:00"],
                    ["COUPANG-3", "BUNDLE-3", "쿠팡 상품 3", "쿠팡 고객 3", "1", "2026-06-11 13:00:00"],
                ],
            )),
        ]
        for filename, content in workbooks:
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="files"; filename="{filename}"\r\n'.encode(),
                b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n",
                content, b"\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        status, _, content = self.request("POST", "/api/import", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["added"], 4)

        status, _, content = self.request("GET", "/api/orders")
        orders = json.loads(content)
        self.assertEqual(status, 200)
        self.assertEqual(len(orders), 4)

        order = orders[0]
        other_order = orders[1]
        update = json.dumps({"action": "preparing", "checked": True, "worker": "테스트작업자"}).encode()
        status, _, content = self.request("PATCH", f"/api/orders/{order['id']}", update, {"Content-Type": "application/json", "Content-Length": str(len(update))})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(content)["preparing"])

        new_user = json.dumps({"username": "worker2", "displayName": "다른작업자", "password": "password123"}).encode()
        status, _, _ = self.request("POST", "/api/users", new_user, {"Content-Type": "application/json", "Content-Length": str(len(new_user))})
        self.assertEqual(status, 201)
        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("worker2")[0], 200)

        self.assertEqual(self.request("GET", "/api/users")[0], 403)
        self.assertEqual(self.request("GET", "/api/orders/as-history")[0], 403)
        self.assertEqual(self.request("GET", "/api/export/shipped")[0], 403)
        self.assertEqual(self.request("POST", "/api/import")[0], 403)
        self.assertEqual(self.request("POST", "/api/orders/manual")[0], 403)
        self.assertEqual(self.request("POST", "/api/export/shipped")[0], 403)

        conflict = json.dumps({"action": "preparing", "checked": True, "worker": "다른작업자"}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", conflict, {"Content-Type": "application/json", "Content-Length": str(len(conflict))})
        self.assertEqual(status, 409)

        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("admin")[0], 200)
        second_admin = json.dumps({"username": "admin2", "displayName": "부관리자", "password": "password123", "role": "owner"}).encode()
        status, _, content = self.request("POST", "/api/users", second_admin, {"Content-Type": "application/json", "Content-Length": str(len(second_admin))})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(content)["role"], "owner")
        management = json.dumps({"action": "managementNumber", "checked": True, "worker": "테스트작업자", "managementNumber": "PC-2026-0001"}).encode()
        status, _, content = self.request("PATCH", f"/api/orders/{order['id']}", management, {"Content-Type": "application/json", "Content-Length": str(len(management))})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["managementNumber"], "PC-2026-0001")
        duplicate_management = json.dumps({"action": "managementNumber", "checked": True, "worker": "다른작업자", "managementNumber": "PC-2026-0001"}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{other_order['id']}", duplicate_management, {"Content-Type": "application/json", "Content-Length": str(len(duplicate_management))})
        self.assertEqual(status, 409)
        premature_shipping = json.dumps({"action": "shipping", "checked": True}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", premature_shipping, {"Content-Type": "application/json", "Content-Length": str(len(premature_shipping))})
        self.assertEqual(status, 409)
        premature_inspection = json.dumps({"action": "softwareInspection", "checked": True}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", premature_inspection, {"Content-Type": "application/json", "Content-Length": str(len(premature_inspection))})
        self.assertEqual(status, 409)
        update = json.dumps({"action": "production", "checked": True, "worker": "테스트작업자"}).encode()
        status, _, content = self.request("PATCH", f"/api/orders/{order['id']}", update, {"Content-Type": "application/json", "Content-Length": str(len(update))})
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(content)["preparing"])
        update = json.dumps({"action": "softwareInspection", "checked": True, "worker": "테스트작업자"}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", update, {"Content-Type": "application/json", "Content-Length": str(len(update))})
        self.assertEqual(status, 200)
        update = json.dumps({"action": "shipping", "checked": True, "worker": "테스트작업자"}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", update, {"Content-Type": "application/json", "Content-Length": str(len(update))})
        self.assertEqual(status, 200)
        undo_production = json.dumps({"action": "production", "checked": False}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", undo_production, {"Content-Type": "application/json", "Content-Length": str(len(undo_production))})
        self.assertEqual(status, 409)

        with mock.patch.object(server, "write_xlsx", side_effect=RuntimeError("test failure")):
            status, _, content = self.request("POST", "/api/export/shipped")
        self.assertEqual(status, 500)
        self.assertIn("보관 처리되지 않았습니다", json.loads(content)["error"])
        status, _, content = self.request("GET", "/api/orders")
        self.assertIn(order["id"], [item["id"] for item in json.loads(content)])

        status, _, content = self.request("GET", "/api/export/shipped")
        self.assertEqual(status, 200)
        exported = read_first_sheet(content)
        self.assertEqual(len(exported), 1)
        self.assertEqual(exported[0]["출고담당자"], "관리자")
        self.assertEqual(exported[0]["제품관리번호"], "PC-2026-0001")

        status, _, content = self.request("POST", "/api/export/shipped")
        self.assertEqual(status, 200)
        self.assertEqual(len(read_first_sheet(content)), 1)
        self.assertEqual(self.request("GET", "/api/export/shipped")[0], 400)
        self.assertEqual(self.request("POST", "/api/export/shipped")[0], 400)
        status, _, content = self.request("GET", "/api/orders")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(content)), 3)
        status, _, content = self.request("GET", "/api/orders/archived")
        archived = json.loads(content)
        self.assertEqual(status, 200)
        self.assertEqual(len(archived), 1)
        self.assertEqual(archived[0]["orderNumber"], order["orderNumber"])

    def test_manual_phone_order(self):
        payload = json.dumps({
            "orderNumber": "TEL-001", "productName": "전화 주문 노트북", "optionName": "16GB",
            "quantity": 1, "amount": 250000, "recipient": "전화고객", "phone": "010-1234-5678",
            "address": "서울시 테스트구", "deliveryMessage": "문 앞", "worker": "접수자",
        }).encode()
        headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        status, _, content = self.request("POST", "/api/orders/manual", payload, headers)
        self.assertEqual(status, 201)
        order = json.loads(content)
        self.assertEqual(order["channel"], "전화")
        self.assertEqual(order["createdBy"], "관리자")
        status, _, _ = self.request("POST", "/api/orders/manual", payload, headers)
        self.assertEqual(status, 409)

    def test_manual_and_detail_edit_cannot_bypass_software_inspection_flow(self):
        payload = json.dumps({
            "orderNumber": "SW-BYPASS-001", "productName": "검수 우회 테스트",
            "quantity": 1, "recipient": "검수고객", "softwareInspectionDone": True,
        }).encode()
        headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
        status, _, _ = self.request("POST", "/api/orders/manual", payload, headers)
        self.assertEqual(status, 409)

        order_payload = json.dumps({
            "orderNumber": "SW-BYPASS-002", "productName": "검수 수정 테스트",
            "quantity": 1, "recipient": "수정고객",
        }).encode()
        status, _, content = self.request("POST", "/api/orders/manual", order_payload, {"Content-Type": "application/json", "Content-Length": str(len(order_payload))})
        self.assertEqual(status, 201)
        order = json.loads(content)

        edit = json.dumps({"action": "details", "fields": {"softwareInspectionDone": True}}).encode()
        status, _, _ = self.request("PATCH", f"/api/orders/{order['id']}", edit, {"Content-Type": "application/json", "Content-Length": str(len(edit))})
        self.assertEqual(status, 409)

        production = json.dumps({"action": "production", "checked": True}).encode()
        self.assertEqual(self.request("PATCH", f"/api/orders/{order['id']}", production, {"Content-Type": "application/json", "Content-Length": str(len(production))})[0], 200)
        status, _, content = self.request("PATCH", f"/api/orders/{order['id']}", edit, {"Content-Type": "application/json", "Content-Length": str(len(edit))})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(content)["softwareInspectionDone"])

    def test_import_detects_and_applies_shipping_update(self):
        payload = json.dumps({
            "orderNumber": "SHIP-UPDATE-001", "productName": "배송지 변경 테스트",
            "quantity": 1, "recipient": "기존고객", "phone": "010-1111-2222",
            "postalCode": "12345", "address": "서울시 강남구", "deliveryMessage": "문 앞",
        }).encode()
        status, _, content = self.request("POST", "/api/orders/manual", payload, {"Content-Type": "application/json", "Content-Length": str(len(payload))})
        self.assertEqual(status, 201)
        order = json.loads(content)

        workbook = write_xlsx(
            ["주문번호", "상품명", "수령인", "연락처", "우편번호", "배송지주소", "배송메시지", "수량"],
            [["SHIP-UPDATE-001", "배송지 변경 테스트", "변경고객", "010-9999-0000", "54321", "서울시 서초구", "경비실", "1"]],
        )
        boundary = "shipping-update-boundary"
        body = b"".join([
            f"--{boundary}\r\n".encode(),
            b'Content-Disposition: form-data; name="files"; filename="shipping-update.xlsx"\r\n',
            b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n",
            workbook,
            b"\r\n",
            f"--{boundary}--\r\n".encode(),
        ])
        status, _, content = self.request("POST", "/api/import", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))})
        self.assertEqual(status, 200)
        result = json.loads(content)
        self.assertEqual(result["added"], 0)
        self.assertEqual(result["shippingUpdates"], 1)

        status, _, content = self.request("GET", "/api/orders")
        updated_order = next(item for item in json.loads(content) if item["id"] == order["id"])
        self.assertEqual(updated_order["address"], "서울시 강남구")
        self.assertEqual(updated_order["pendingShippingUpdate"]["fields"]["address"], "서울시 서초구")

        apply_update = json.dumps({"action": "applyShippingUpdate", "checked": True}).encode()
        status, _, content = self.request("PATCH", f"/api/orders/{order['id']}", apply_update, {"Content-Type": "application/json", "Content-Length": str(len(apply_update))})
        self.assertEqual(status, 200)
        applied = json.loads(content)
        self.assertEqual(applied["recipient"], "변경고객")
        self.assertEqual(applied["phone"], "010-9999-0000")
        self.assertEqual(applied["postalCode"], "54321")
        self.assertEqual(applied["address"], "서울시 서초구")
        self.assertEqual(applied["deliveryMessage"], "경비실")
        self.assertNotIn("pendingShippingUpdate", applied)

    def test_public_registration_creates_worker(self):
        self.assertEqual(self.logout()[0], 200)
        payload = json.dumps({"username": "signup-worker", "displayName": "가입작업자", "password": "password123", "role": "admin"}).encode()
        status, _, content = self.request("POST", "/api/auth/register", payload, {"Content-Type": "application/json", "Content-Length": str(len(payload))})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(content)["role"], "worker")
        self.assertEqual(self.login("signup-worker")[0], 200)
        self.assertEqual(self.request("GET", "/api/users")[0], 403)
        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("admin")[0], 200)

    def test_rejects_too_many_uploaded_files(self):
        self.logout()
        self.assertEqual(self.login("admin")[0], 200)
        boundary = "too-many-files-boundary"
        chunks = []
        for index in range(server.MAX_UPLOAD_FILES + 1):
            chunks.extend([
                f"--{boundary}\r\n".encode(),
                f'Content-Disposition: form-data; name="files"; filename="orders-{index}.xlsx"\r\n'.encode(),
                b"Content-Type: application/vnd.openxmlformats-officedocument.spreadsheetml.sheet\r\n\r\n",
                b"invalid but must be rejected before parsing\r\n",
            ])
        chunks.append(f"--{boundary}--\r\n".encode())
        body = b"".join(chunks)
        status, _, content = self.request("POST", "/api/import", body, {"Content-Type": f"multipart/form-data; boundary={boundary}", "Content-Length": str(len(body))})
        self.assertEqual(status, 400)
        self.assertIn("최대", json.loads(content)["error"])

    def test_role_permissions(self):
        worker = json.dumps({"username": "permission-worker", "displayName": "권한작업자", "password": "password123", "role": "worker"}).encode()
        status, _, content = self.request("POST", "/api/users", worker, {"Content-Type": "application/json", "Content-Length": str(len(worker))})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(content)["role"], "worker")
        worker_id = json.loads(content)["id"]

        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("permission-worker")[0], 200)
        order_id = "permission-management-order"
        server.write_orders([{
            "id": order_id, "importKey": order_id, "channel": "테스트", "orderNumber": "PERMISSION-1",
            "orderedAt": "2026-06-12", "productName": "권한 테스트 상품", "managementNumber": "",
            "preparing": False, "productionDone": False, "shippingDone": False,
        }])
        management = json.dumps({"action": "managementNumber", "checked": True, "managementNumber": "WORKER-PC-001"}).encode()
        status, _, content = self.request("PATCH", f"/api/orders/{order_id}", management, {"Content-Type": "application/json", "Content-Length": str(len(management))})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["managementNumber"], "WORKER-PC-001")
        self.assertEqual(json.loads(content)["managementNumberBy"], "권한작업자")
        self.assertEqual(self.request("GET", "/api/users")[0], 403)
        self.assertEqual(self.request("GET", "/api/export/shipped")[0], 403)
        self.assertEqual(self.request("POST", "/api/import")[0], 403)
        self.assertEqual(self.request("POST", "/api/orders/manual")[0], 403)
        self.assertEqual(self.request("POST", "/api/export/shipped")[0], 403)
        denied_update = json.dumps({"displayName": "변경시도", "role": "owner", "enabled": True}).encode()
        self.assertEqual(self.request("PATCH", f"/api/users/{worker_id}", denied_update, {"Content-Type": "application/json", "Content-Length": str(len(denied_update))})[0], 403)
        self.assertEqual(self.request("DELETE", f"/api/users/{worker_id}")[0], 403)

        self.assertEqual(self.logout()[0], 200)
        self.assertEqual(self.login("admin")[0], 200)
        update = json.dumps({"username": "permission-worker-edit", "displayName": "수정작업자", "password": "newpassword123", "role": "worker", "enabled": False}).encode()
        status, _, content = self.request("PATCH", f"/api/users/{worker_id}", update, {"Content-Type": "application/json", "Content-Length": str(len(update))})
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["displayName"], "수정작업자")
        self.assertFalse(json.loads(content)["enabled"])
        self.assertEqual(self.login("permission-worker-edit", "newpassword123")[0], 401)

        status, _, content = self.request("GET", "/api/users")
        admin_id = next(item["id"] for item in json.loads(content) if item["username"] == "admin")
        self.assertEqual(self.request("DELETE", f"/api/users/{admin_id}")[0], 400)
        status, _, content = self.request("DELETE", f"/api/users/{worker_id}")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(content)["id"], worker_id)

        admin = json.dumps({"username": "permission-admin", "displayName": "추가총책임자", "password": "password123", "role": "owner"}).encode()
        status, _, content = self.request("POST", "/api/users", admin, {"Content-Type": "application/json", "Content-Length": str(len(admin))})
        self.assertEqual(status, 201)
        self.assertEqual(json.loads(content)["role"], "owner")


if __name__ == "__main__":
    unittest.main()
