from __future__ import annotations

import json
import os
import re
import signal
import shutil
import threading
import time
import io
import zipfile
from datetime import date, datetime, timezone
from email.parser import BytesParser
from email.policy import default
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from excel import write_xlsx
from importers import import_workbook
from auth import AuthStore, normalize_role

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "public"
DATA_FILE = Path(os.getenv("DATA_FILE", ROOT / "data" / "orders.json"))
USERS_FILE = Path(os.getenv("USERS_FILE", ROOT / "data" / "users.json"))
AUDIT_FILE = Path(os.getenv("AUDIT_FILE", ROOT / "data" / "audit.jsonl"))
LOCK = threading.Lock()
AUDIT_LOCK = threading.Lock()
SETUP_LOCK = threading.Lock()
AUTH = AuthStore(USERS_FILE)
MEMBER_MANAGEMENT_ROLES = {"owner", "developer"}
# 주문 취소는 운영 책임자에게도 열어두되, 일반 작업자에는 열지 않는다.
CANCEL_ORDER_ROLES = {"owner", "developer", "as_manager", "sales_manager", "md"}
ORDER_ADMIN_ROLES = {"owner", "developer", "sales_manager", "md"}
ORDER_EDIT_ROLES = {"owner", "developer", "as_manager", "sales_manager", "md"}
AS_HISTORY_ROLES = {"owner", "developer", "as_manager"}
ROLE_RANK = {"owner": 0, "developer": 1, "as_manager": 2, "sales_manager": 3, "md": 4, "worker": 5}
MAX_UPLOAD_BYTES = 30 * 1024 * 1024
MAX_UPLOAD_FILES = 10
MAX_ARCHIVE_FILES = 20
MAX_ARCHIVE_UNCOMPRESSED_BYTES = 100 * 1024 * 1024
BACKUP_RETENTION_DAYS = 14
LOGIN_FAILURE_LIMIT = 5
LOGIN_BLOCK_SECONDS = 5 * 60
LOGIN_LOCK = threading.Lock()
LOGIN_FAILURES: dict[str, list[float]] = {}


class OrderHTTPServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True
    request_queue_size = 64


def user_role(user: dict) -> str:
    return normalize_role(str(user.get("role", "worker")))


def split_management_numbers(value: object) -> list[str]:
    return [line.strip() for line in str(value or "").splitlines() if line.strip()]


def read_orders() -> list[dict]:
    try:
        return json.loads(DATA_FILE.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        return []


def backup_file(file_path: Path) -> None:
    # 하루에 한 번만 백업을 남기고, 오래된 백업은 자동으로 정리한다.
    if not file_path.exists():
        return
    backup_dir = file_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(backup_dir, 0o700)
    today = datetime.now().strftime("%Y%m%d")
    backup = backup_dir / f"{file_path.name}.{today}.bak"
    if not backup.exists():
        shutil.copy2(file_path, backup)
        os.chmod(backup, 0o600)
    cutoff = time.time() - BACKUP_RETENTION_DAYS * 24 * 60 * 60
    for old_backup in backup_dir.glob(f"{file_path.name}.*.bak"):
        if old_backup.stat().st_mtime < cutoff:
            old_backup.unlink()


def write_audit(event: str, user: dict | None = None, **details: object) -> None:
    # 감사 로그는 JSONL로 누적해서 남긴다.
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "event": event,
        "userId": user.get("id", "") if user else "",
        "displayName": user.get("displayName", "") if user else "",
        **details,
    }
    AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with AUDIT_LOCK, AUDIT_FILE.open("a", encoding="utf-8") as output:
        output.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
        output.flush()
        os.fsync(output.fileno())
        os.chmod(AUDIT_FILE, 0o600)


def login_key(client_ip: str, username: str) -> str:
    return f"{client_ip}:{username.strip().lower()}"


def login_blocked(key: str, now: float | None = None) -> bool:
    # 같은 계정/주소 조합의 연속 실패를 시간창 기준으로 계산한다.
    current = now if now is not None else time.time()
    with LOGIN_LOCK:
        failures = [item for item in LOGIN_FAILURES.get(key, []) if current - item < LOGIN_BLOCK_SECONDS]
        LOGIN_FAILURES[key] = failures
        return len(failures) >= LOGIN_FAILURE_LIMIT


def record_login_result(key: str, success: bool, now: float | None = None) -> None:
    # 성공 시 실패 이력을 비우고, 실패 시에는 카운트를 적립한다.
    with LOGIN_LOCK:
        if success:
            LOGIN_FAILURES.pop(key, None)
        else:
            LOGIN_FAILURES.setdefault(key, []).append(now if now is not None else time.time())


def _normalized_order_value(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def _normalized_address_value(value: object) -> str:
    text = _normalized_order_value(value)
    return re.sub(r"[,\.\-_/()]+", "", text)


def _normalized_phone_value(value: object) -> str:
    return re.sub(r"\D+", "", str(value or ""))


def _normalized_order_number(value: object) -> str:
    return _normalized_order_value(value)


def order_fingerprint(order: dict) -> tuple[str, ...]:
    return (
        _normalized_order_value(order.get("recipient")),
        _normalized_order_value(order.get("productName")),
        _normalized_order_value(order.get("optionName")),
        _normalized_address_value(order.get("address")),
    )


def order_dedupe_key(order: dict) -> tuple[str, ...]:
    order_number = _normalized_order_number(order.get("orderNumber"))
    if order_number:
        return ("orderNumber", order_number)
    fingerprint = order_fingerprint(order)
    phone = _normalized_phone_value(order.get("phone"))
    if all(fingerprint) and phone:
        return ("fingerprint", *fingerprint, phone)
    if all(fingerprint):
        return ("fingerprint", *fingerprint)
    return ("importKey", _normalized_order_value(order.get("importKey")))


SHIPPING_UPDATE_FIELDS = ("recipient", "phone", "postalCode", "address", "deliveryMessage")


def shipping_update_candidate(existing: dict, imported: dict, now: str) -> dict | None:
    current = {field: str(existing.get(field, "")).strip() for field in SHIPPING_UPDATE_FIELDS}
    incoming = {field: str(imported.get(field, "")).strip() for field in SHIPPING_UPDATE_FIELDS}
    changed = {
        field: {"current": current[field], "incoming": incoming[field]}
        for field in SHIPPING_UPDATE_FIELDS
        if incoming[field] and incoming[field] != current[field]
    }
    if not changed:
        return None
    candidate = {
        "detectedAt": now,
        "sourceFile": imported.get("sourceFile", ""),
        "orderNumber": imported.get("orderNumber", ""),
        "fields": {field: incoming[field] for field in SHIPPING_UPDATE_FIELDS},
        "changed": changed,
    }
    product_changed = any(
        str(imported.get(field, "")).strip() and str(imported.get(field, "")).strip() != str(existing.get(field, "")).strip()
        for field in ("productName", "optionName", "quantity")
    )
    if product_changed:
        candidate["contentChangeWarning"] = True
    return candidate


def new_unique_orders(existing: list[dict], imported: list[dict], now: str | None = None) -> tuple[list[dict], int]:
    # 저장된 주문 전체를 기준으로 본다. 출고 완료/보관 주문도 같은 기준에 포함된다.
    detected_at = now or datetime.now(timezone.utc).isoformat()
    keys = {order["importKey"] for order in existing}
    fingerprints = {order_dedupe_key(order) for order in existing}
    by_order_number = {
        _normalized_order_number(order.get("orderNumber")): order
        for order in existing
        if _normalized_order_number(order.get("orderNumber"))
    }
    added = []
    shipping_updates = 0
    for order in imported:
        key = order["importKey"]
        fingerprint = order_dedupe_key(order)
        order_number = _normalized_order_number(order.get("orderNumber"))
        matched = by_order_number.get(order_number) if order_number else None
        if matched:
            if not matched.get("archivedAt") and not matched.get("cancelledAt") and not matched.get("shippingDone"):
                candidate = shipping_update_candidate(matched, order, detected_at)
                if candidate:
                    matched["pendingShippingUpdate"] = candidate
                    matched["updatedAt"] = detected_at
                    shipping_updates += 1
            continue
        if key in keys or fingerprint in fingerprints:
            continue
        keys.add(key)
        fingerprints.add(fingerprint)
        if order_number:
            by_order_number[order_number] = order
        added.append(order)
    return added, shipping_updates


def write_orders(orders: list[dict]) -> None:
    # 임시 파일에 먼저 쓰고 원자적으로 교체해서 저장 중 손상을 줄인다.
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(DATA_FILE.parent, 0o700)
    backup_file(DATA_FILE)
    temporary = DATA_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(orders, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.chmod(temporary, 0o600)
    temporary.replace(DATA_FILE)
    os.chmod(DATA_FILE, 0o600)


def shutdown_backup_dir() -> Path:
    return DATA_FILE.parent / "backups" / "shutdown-latest"


def write_shutdown_backup() -> None:
    # 종료 시점의 주문/사용자/감사 로그를 묶어서 보관한다.
    sources = [DATA_FILE, USERS_FILE, AUDIT_FILE]
    existing_sources = [source for source in sources if source.exists()]
    if not existing_sources:
        return
    backup_dir = shutdown_backup_dir()
    temp_dir = backup_dir.parent / ".shutdown-latest.tmp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(temp_dir, 0o700)
    for source in existing_sources:
        shutil.copy2(source, temp_dir / source.name)
        os.chmod(temp_dir / source.name, 0o600)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    temp_dir.rename(backup_dir)
    os.chmod(backup_dir, 0o700)


def serve() -> None:
    # 운영 환경에서는 0.0.0.0 바인딩으로 외부 접속을 허용한다.
    port = int(os.getenv("PORT", "3000"))
    host = os.getenv("HOST", "0.0.0.0")
    httpd = OrderHTTPServer((host, port), Handler)
    stopped = threading.Event()

    def stop_server(signum: int, frame: object | None) -> None:
        if stopped.is_set():
            return
        stopped.set()
        httpd.shutdown()

    previous_handlers: dict[int, object] = {}
    for signum in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if signum is None:
            continue
        previous_handlers[signum] = signal.getsignal(signum)
        signal.signal(signum, stop_server)

    print(f"Order workflow sample: http://{host}:{port}")
    server_thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    server_thread.start()
    try:
        while server_thread.is_alive():
            server_thread.join(timeout=0.5)
    finally:
        httpd.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        write_shutdown_backup()


def archive_excel_files(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    files = [
        item for item in archive.infolist()
        if not item.is_dir()
        and item.filename.lower().endswith((".xlsx", ".xls"))
        and not item.filename.startswith("__MACOSX/")
    ]
    if not files:
        raise ValueError("ZIP 안에 .xlsx 또는 .xls 파일이 없습니다.")
    if len(files) > MAX_ARCHIVE_FILES:
        raise ValueError(f"ZIP 안의 엑셀 파일은 최대 {MAX_ARCHIVE_FILES}개까지 지원합니다.")
    if sum(item.file_size for item in files) > MAX_ARCHIVE_UNCOMPRESSED_BYTES:
        raise ValueError("ZIP 압축 해제 크기는 최대 100MB입니다.")
    return files


def order_number_key(order: dict) -> list[tuple[int, object]]:
    value = str(order.get("orderNumber", ""))
    return [(0, int(part)) if part.isdigit() else (1, part.lower()) for part in re.split(r"(\d+)", value)]


def order_datetime_key(order: dict) -> tuple[str, str]:
    # 주문일시가 다양한 형식으로 들어와도 정렬 가능한 문자열로 정규화한다.
    ordered_at = str(order.get("orderedAt", "")).strip().replace("/", "-").replace(".", "-")
    ordered_at = re.sub(r"\s+", " ", ordered_at)
    digits = re.sub(r"\D", "", ordered_at)
    if len(digits) >= 14:
        normalized = digits[:14]
    elif len(digits) == 8:
        normalized = f"{digits}000000"
    else:
        normalized = ordered_at
    order_number = re.sub(r"\d+", lambda match: match.group(0).zfill(24), str(order.get("orderNumber", "")).lower())
    return normalized, order_number


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def end_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: int, payload: object, headers: dict[str, str] | None = None) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        for key, value in (headers or {}).items(): self.send_header(key, value)
        self.end_headers()
        self.wfile.write(content)

    def _session_token(self) -> str:
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        return cookie["halfbook_session"].value if "halfbook_session" in cookie else ""

    def _current_user(self) -> dict | None:
        return AUTH.user_for_token(self._session_token())

    def _require_user(self) -> dict | None:
        user = self._current_user()
        if not user: self._json(401, {"error": "로그인이 필요합니다."})
        return user

    def _require_member_manager(self) -> dict | None:
        user = self._require_user()
        if user and user_role(user) not in MEMBER_MANAGEMENT_ROLES:
            self._json(403, {"error": "총책임자 또는 개발자만 회원을 관리할 수 있습니다."})
            return None
        return user

    def _body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except (TypeError, ValueError):
            raise ValueError("요청 본문 크기가 올바르지 않습니다.")
        if length < 0:
            raise ValueError("요청 본문 크기가 올바르지 않습니다.")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("업로드 크기는 최대 30MB입니다.")
        return self.rfile.read(length)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/health":
            return self._json(200, {"ok": True})
        if path == "/api/auth/status":
            return self._json(200, {"setupRequired": len(AUTH.read_users()) == 0, "user": self._current_user()})
        if path == "/api/users":
            if not self._require_member_manager(): return
            return self._json(200, [AUTH.public_user(item) for item in AUTH.read_users()])
        if path.startswith("/api/") and not self._require_user(): return
        if path == "/api/orders/as-history":
            user = self._current_user()
            if not user or user_role(user) not in AS_HISTORY_ROLES:
                return self._json(403, {"error": "고객 출고 이력은 총책임자, 개발자, AS 담당자만 조회할 수 있습니다."})
            orders = sorted(
                (item for item in read_orders() if item.get("shippingDone") and not item.get("cancelledAt")),
                key=lambda order: (str(order.get("shippingAt", "")), order_datetime_key(order)),
            )
            return self._json(200, orders)
        if path == "/api/orders/cancelled":
            orders = sorted(
                (item for item in read_orders() if item.get("cancelledAt")),
                key=order_datetime_key,
            )
            return self._json(200, orders)
        if path == "/api/orders/archived":
            orders = sorted(
                (item for item in read_orders() if item.get("archivedAt")),
                key=order_datetime_key,
            )
            return self._json(200, orders)
        if path == "/api/orders":
            orders = sorted(
                (item for item in read_orders() if not item.get("archivedAt") and not item.get("cancelledAt")),
                key=order_datetime_key,
            )
            return self._json(200, orders)
        if path == "/api/export/shipped":
            user = self._require_user()
            if not user: return
            if user_role(user) not in ORDER_ADMIN_ROLES: return self._json(403, {"error": "해당 권한으로 출고 엑셀을 조회할 수 없습니다."})
            return self._export_shipped(archive=False)
        return super().do_GET()

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/auth/setup":
            with SETUP_LOCK:
                if AUTH.read_users(): return self._json(409, {"error": "관리자 설정이 이미 완료됐습니다."})
                return self._create_user(role="owner", login_after=True)
        if path == "/api/auth/register":
            if not AUTH.read_users():
                return self._json(409, {"error": "먼저 최초 관리자 계정을 설정하세요."})
            return self._create_user(role="worker", login_after=False)
        if path == "/api/auth/login":
            try:
                payload = json.loads(self._body())
                username = str(payload.get("username", ""))
                key = login_key(self.client_address[0], username)
                if login_blocked(key):
                    return self._json(429, {"error": "로그인 시도가 너무 많습니다. 5분 후 다시 시도하세요."}, {"Retry-After": str(LOGIN_BLOCK_SECONDS)})
                authenticated = AUTH.authenticate(username, str(payload.get("password", "")))
                record_login_result(key, authenticated is not None)
                if not authenticated:
                    write_audit("login_failed", username=username.strip().lower(), clientIp=self.client_address[0])
                    return self._json(401, {"error": "아이디 또는 비밀번호가 올바르지 않습니다."})
                token, user = authenticated
                write_audit("login_succeeded", user, clientIp=self.client_address[0])
                return self._json(200, {"user": user}, {"Set-Cookie": f"halfbook_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"})
            except (ValueError, json.JSONDecodeError): return self._json(400, {"error": "로그인 정보를 확인하세요."})
        if path == "/api/auth/logout":
            AUTH.logout(self._session_token())
            return self._json(200, {"ok": True}, {"Set-Cookie": "halfbook_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
        user = self._require_user()
        if not user: return
        if path == "/api/users":
            if user_role(user) not in MEMBER_MANAGEMENT_ROLES: return self._json(403, {"error": "총책임자 또는 개발자만 계정을 추가할 수 있습니다."})
            return self._create_user(role=None, login_after=False, current_user=user)
        if path == "/api/orders/manual":
            if user_role(user) not in ORDER_ADMIN_ROLES: return self._json(403, {"error": "해당 권한으로 수기 주문을 등록할 수 없습니다."})
            return self._create_manual_order(user)
        if path == "/api/export/shipped":
            if user_role(user) not in ORDER_ADMIN_ROLES: return self._json(403, {"error": "해당 권한으로 출고 완료 엑셀을 만들 수 없습니다."})
            return self._export_shipped(archive=True)
        if path != "/api/import":
            return self._json(404, {"error": "요청 경로를 찾을 수 없습니다."})
        if user_role(user) not in ORDER_ADMIN_ROLES:
            return self._json(403, {"error": "해당 권한으로 주문 엑셀을 가져올 수 없습니다."})
        try:
            content_type = self.headers.get("Content-Type", "")
            message = BytesParser(policy=default).parsebytes(
                f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode() + self._body()
            )
            files = [part for part in message.iter_attachments() if part.get_filename()]
            if not files:
                return self._json(400, {"error": "엑셀 파일을 선택하세요."})
            if len(files) > MAX_UPLOAD_FILES:
                return self._json(400, {"error": f"한 번에 최대 {MAX_UPLOAD_FILES}개 파일까지 가져올 수 있습니다."})
            imported, errors = [], []
            for part in files:
                filename = Path(part.get_filename()).name
                try:
                    content = part.get_payload(decode=True)
                    if filename.lower().endswith((".xlsx", ".xls")):
                        imported.extend(import_workbook(content, filename))
                    elif filename.lower().endswith(".zip"):
                        with zipfile.ZipFile(io.BytesIO(content)) as archive:
                            for excel_file in archive_excel_files(archive):
                                imported.extend(import_workbook(archive.read(excel_file), Path(excel_file.filename).name))
                    else:
                        raise ValueError(".xlsx, .xls 또는 .zip 파일만 지원합니다.")
                except Exception as error:
                    errors.append(f"{filename}: {error}")
            if not imported:
                return self._json(400, {"error": "\n".join(errors)})
            with LOCK:
                orders = read_orders()
                now = datetime.now(timezone.utc).isoformat()
                added, shipping_updates = new_unique_orders(orders, imported, now)
                orders.extend(added)
                write_orders(orders)
            write_audit("orders_imported", user, added=len(added), duplicates=len(imported) - len(added), shippingUpdates=shipping_updates, files=len(files))
            return self._json(200, {"added": len(added), "duplicates": len(imported) - len(added), "shippingUpdates": shipping_updates, "errors": errors})
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        except Exception:
            return self._json(500, {"error": "엑셀 처리 중 오류가 발생했습니다."})

    def _create_user(self, role: str | None, login_after: bool, current_user: dict | None = None) -> None:
        try:
            payload = json.loads(self._body())
            now = datetime.now(timezone.utc).isoformat()
            selected_role = normalize_role(role or str(payload.get("role", "worker")))
            if current_user and user_role(current_user) == "developer" and ROLE_RANK.get(selected_role, 999) <= ROLE_RANK["developer"]:
                return self._json(403, {"error": "개발자는 총책임자나 개발자 권한을 지정할 수 없습니다."})
            user = AUTH.create_user(str(payload.get("username", "")), str(payload.get("displayName", "")), str(payload.get("password", "")), selected_role, now)
            if login_after:
                token, user = AUTH.authenticate(str(payload.get("username", "")), str(payload.get("password", "")))
                return self._json(201, {"user": user}, {"Set-Cookie": f"halfbook_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age=43200"})
            return self._json(201, user)
        except ValueError as error: return self._json(400, {"error": str(error)})
        except json.JSONDecodeError: return self._json(400, {"error": "계정 정보를 확인하세요."})

    def _create_manual_order(self, user: dict) -> None:
        try:
            payload = json.loads(self._body())
            order_number = str(payload.get("orderNumber", "")).strip() or f"수기-{datetime.now().strftime('%Y%m%d%H%M%S')}-{uuid4().hex[:6]}"
            product_name = str(payload.get("productName", "")).strip()
            recipient = str(payload.get("recipient", "")).strip()
            phone = str(payload.get("phone", "")).strip()
            channel = str(payload.get("channel", "전화")).strip() or "전화"
            worker = user["displayName"]
            if not worker:
                return self._json(400, {"error": "작업자를 확인할 수 없습니다."})
            try:
                quantity = max(1, int(payload.get("quantity") or 1))
                amount = max(0, int(payload.get("amount") or 0))
            except (TypeError, ValueError):
                return self._json(400, {"error": "수량과 금액은 숫자로 입력하세요."})
            software_inspection_done = bool(payload.get("softwareInspectionDone"))
            if software_inspection_done:
                return self._json(409, {"error": "제작 완료 후 소프트웨어 검수를 완료할 수 있습니다."})
            now = datetime.now(timezone.utc).isoformat()
            with LOCK:
                orders = read_orders()
                if any(str(order.get("orderNumber", "")).strip() == order_number for order in orders):
                    return self._json(409, {"error": "이미 등록된 주문번호입니다."})
                order = {
                    "id": str(uuid4()), "importKey": f"{channel}:{order_number}", "channel": channel,
                    "sourceFile": "수기입력", "orderNumber": order_number,
                    "orderedAt": str(payload.get("orderedAt", "")).strip() or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "productName": product_name, "optionName": str(payload.get("optionName", "")).strip(),
                    "productCode": str(payload.get("productCode", "")).strip(), "quantity": quantity, "amount": amount,
                    "recipient": recipient, "phone": phone, "postalCode": str(payload.get("postalCode", "")).strip(),
                    "address": str(payload.get("address", "")).strip(),
                    "deliveryMessage": str(payload.get("deliveryMessage", "")).strip(), "courier": "", "trackingNumber": "",
                    "managementNumber": "", "preparing": False, "preparingBy": "", "preparingAt": "",
                    "softwareInspectionDone": software_inspection_done, "softwareInspectionBy": worker if software_inspection_done else "", "softwareInspectionAt": now if software_inspection_done else "",
                    "productionDone": False, "productionBy": "", "productionAt": "",
                    "shippingDone": False, "shippingBy": "", "shippingAt": "",
                    "createdBy": worker, "createdAt": now, "updatedAt": now,
                }
                orders.append(order)
                write_orders(orders)
            write_audit("order_created", user, orderId=order["id"], orderNumber=order_number, channel=channel)
            return self._json(201, order)
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "수기 주문 내용을 확인하세요."})

    def do_PATCH(self) -> None:
        path = urlparse(self.path).path
        user = self._require_user()
        if not user: return
        if path.startswith("/api/users/"):
            if user_role(user) not in MEMBER_MANAGEMENT_ROLES: return self._json(403, {"error": "총책임자 또는 개발자만 계정을 수정할 수 있습니다."})
            return self._update_user(path.rsplit("/", 1)[-1], user)
        if not path.startswith("/api/orders/"):
            return self._json(404, {"error": "요청 경로를 찾을 수 없습니다."})
        try:
            payload = json.loads(self._body())
            worker = user["displayName"]
            action, checked = payload.get("action"), bool(payload.get("checked"))
            now = datetime.now(timezone.utc).isoformat()
            with LOCK:
                orders = read_orders()
                order = next((item for item in orders if item["id"] == path.rsplit("/", 1)[-1]), None)
                if not order:
                    return self._json(404, {"error": "주문을 찾을 수 없습니다."})
                if order.get("cancelledAt"):
                    return self._json(409, {"error": "이미 취소된 주문입니다.", "order": order})
                if action == "cancel":
                    # 취소는 전용 권한으로만 허용하고, 출고 완료 후에도 같은 흐름으로 처리한다.
                    if user_role(user) not in CANCEL_ORDER_ROLES:
                        return self._json(403, {"error": "해당 권한으로 주문을 취소할 수 없습니다.", "order": order})
                    reason = str(payload.get("reason", "")).strip()
                    if not reason:
                        return self._json(400, {"error": "취소 사유를 입력하세요.", "order": order})
                    if len(reason) > 500:
                        return self._json(400, {"error": "취소 사유는 500자 이내로 입력하세요.", "order": order})
                    if order.get("archivedAt"):
                        return self._json(409, {"error": "보관된 주문은 취소할 수 없습니다.", "order": order})
                    order.update({
                        "cancelledAt": now, "cancelledBy": worker, "cancelReason": reason,
                        "preparing": False, "preparingBy": "", "preparingAt": "", "updatedAt": now,
                    })
                    write_orders(orders)
                    write_audit("order_cancelled", user, orderId=order["id"], orderNumber=order.get("orderNumber", ""))
                    return self._json(200, order)
                if action == "preparing":
                    if checked and (order.get("productionDone") or order.get("shippingDone")):
                        return self._json(409, {"error": "제작 또는 출고가 완료된 주문은 준비 중으로 되돌릴 수 없습니다.", "order": order})
                    current_worker = str(order.get("preparingBy", "")).strip()
                    if checked and order.get("preparing") and current_worker != worker:
                        return self._json(409, {"error": f"{current_worker} 작업자가 이미 준비 중입니다.", "order": order})
                    if not checked and order.get("preparing") and current_worker and current_worker != worker:
                        return self._json(409, {"error": f"준비 중 상태는 {current_worker} 작업자만 해제할 수 있습니다.", "order": order})
                    order.update({"preparing": checked, "preparingBy": worker if checked else "", "preparingAt": now if checked else ""})
                elif action == "softwareInspection":
                    if checked and order.get("shippingDone"):
                        return self._json(409, {"error": "출고 완료된 주문은 소프트웨어 검수 상태를 변경할 수 없습니다.", "order": order})
                    if checked and not order.get("productionDone"):
                        return self._json(409, {"error": "제작 완료 후 소프트웨어 검수를 완료할 수 있습니다.", "order": order})
                    order.update({
                        "softwareInspectionDone": checked, "softwareInspectionBy": worker if checked else "", "softwareInspectionAt": now if checked else "",
                    })
                    if not checked:
                        order.update({
                            "shippingDone": False, "shippingBy": "", "shippingAt": "",
                        })
                elif action == "managementNumber":
                    management_numbers = split_management_numbers(payload.get("managementNumber", ""))
                    quantity = max(1, int(order.get("quantity") or 1))
                    if management_numbers and len(management_numbers) != quantity:
                        return self._json(400, {"error": f"수량 {quantity}개에 맞게 제품 관리번호를 {quantity}줄 입력하세요.", "order": order})
                    if len(management_numbers) > 100:
                        return self._json(400, {"error": "제품 관리번호는 100개까지 입력할 수 있습니다.", "order": order})
                    if len(management_numbers) != len(set(management_numbers)):
                        return self._json(409, {"error": "제품 관리번호가 중복되었습니다.", "order": order})
                    existing_numbers = set()
                    for item in orders:
                        if item.get("id") == order.get("id"):
                            continue
                        existing_numbers.update(split_management_numbers(item.get("managementNumber", "")))
                    duplicate = next((number for number in management_numbers if number in existing_numbers), None)
                    if duplicate:
                        return self._json(409, {"error": f"이미 다른 주문에 등록된 관리번호입니다: {duplicate}", "order": order})
                    management_number = "\n".join(management_numbers)
                    order.update({"managementNumber": management_number, "managementNumberBy": worker if management_number else "", "managementNumberAt": now if management_number else ""})
                elif action == "applyShippingUpdate":
                    if user_role(user) not in ORDER_EDIT_ROLES:
                        return self._json(403, {"error": "해당 권한으로 배송지 변경을 반영할 수 없습니다.", "order": order})
                    if order.get("archivedAt"):
                        return self._json(409, {"error": "보관된 주문은 배송지를 수정할 수 없습니다.", "order": order})
                    if order.get("cancelledAt"):
                        return self._json(409, {"error": "취소된 주문은 배송지를 수정할 수 없습니다.", "order": order})
                    if order.get("shippingDone"):
                        return self._json(409, {"error": "출고 확인된 주문은 배송지를 수정할 수 없습니다.", "order": order})
                    pending = order.get("pendingShippingUpdate")
                    if not isinstance(pending, dict) or not isinstance(pending.get("fields"), dict):
                        return self._json(400, {"error": "반영할 배송지 변경이 없습니다.", "order": order})
                    before = {field: order.get(field, "") for field in SHIPPING_UPDATE_FIELDS}
                    updates = {field: str(pending["fields"].get(field, order.get(field, ""))).strip() for field in SHIPPING_UPDATE_FIELDS}
                    order.update(updates)
                    order.pop("pendingShippingUpdate", None)
                    order["shippingUpdateAppliedBy"] = worker
                    order["shippingUpdateAppliedAt"] = now
                    write_audit("shipping_update_applied", user, orderId=order["id"], orderNumber=order.get("orderNumber", ""), before=before, after=updates)
                elif action == "details":
                    if user_role(user) not in ORDER_EDIT_ROLES:
                        return self._json(403, {"error": "해당 권한으로 주문을 수정할 수 없습니다.", "order": order})
                    if order.get("archivedAt"):
                        return self._json(409, {"error": "보관된 주문은 수정할 수 없습니다.", "order": order})
                    if order.get("cancelledAt"):
                        return self._json(409, {"error": "취소된 주문은 수정할 수 없습니다.", "order": order})
                    if order.get("shippingDone"):
                        return self._json(409, {"error": "출고 완료된 주문은 수정할 수 없습니다.", "order": order})
                    fields = payload.get("fields")
                    if not isinstance(fields, dict):
                        return self._json(400, {"error": "수정할 주문 정보를 확인하세요.", "order": order})
                    try:
                        quantity = max(1, int(fields.get("quantity", order.get("quantity", 1)) or 1))
                        amount = max(0, int(fields.get("amount", order.get("amount", 0)) or 0))
                    except (TypeError, ValueError):
                        return self._json(400, {"error": "수량과 금액은 숫자로 입력하세요.", "order": order})
                    updates = {
                        "channel": str(fields.get("channel", order.get("channel", ""))).strip() or order.get("channel", ""),
                        "productName": str(fields.get("productName", order.get("productName", ""))).strip(),
                        "optionName": str(fields.get("optionName", order.get("optionName", ""))).strip(),
                        "productCode": str(fields.get("productCode", order.get("productCode", ""))).strip(),
                        "quantity": quantity,
                        "amount": amount,
                        "recipient": str(fields.get("recipient", order.get("recipient", ""))).strip(),
                        "phone": str(fields.get("phone", order.get("phone", ""))).strip(),
                        "postalCode": str(fields.get("postalCode", order.get("postalCode", ""))).strip(),
                        "address": str(fields.get("address", order.get("address", ""))).strip(),
                        "deliveryMessage": str(fields.get("deliveryMessage", order.get("deliveryMessage", ""))).strip(),
                    }
                    order.update(updates)
                    if any(field in fields for field in SHIPPING_UPDATE_FIELDS):
                        order.pop("pendingShippingUpdate", None)
                    if "softwareInspectionDone" in fields:
                        software_inspection_done = bool(fields.get("softwareInspectionDone"))
                        if software_inspection_done and not order.get("productionDone"):
                            return self._json(409, {"error": "제작 완료 후 소프트웨어 검수를 완료할 수 있습니다.", "order": order})
                        order.update({
                            "softwareInspectionDone": software_inspection_done,
                            "softwareInspectionBy": worker if software_inspection_done else "",
                            "softwareInspectionAt": now if software_inspection_done else "",
                        })
                        if not software_inspection_done:
                            order.update({
                                "shippingDone": False, "shippingBy": "", "shippingAt": "",
                            })
                elif action == "production":
                    if not checked and order.get("shippingDone"):
                        return self._json(409, {"error": "출고 완료된 주문은 제작 완료를 해제할 수 없습니다.", "order": order})
                    if not checked and order.get("softwareInspectionDone"):
                        return self._json(409, {"error": "소프트웨어 검수 완료된 주문은 제작 완료를 해제할 수 없습니다.", "order": order})
                    current_worker = str(order.get("preparingBy", "")).strip()
                    if checked and order.get("preparing") and current_worker and current_worker != worker:
                        return self._json(409, {"error": f"{current_worker} 작업자가 준비 중인 주문입니다.", "order": order})
                    order.update({
                        "productionDone": checked, "productionBy": worker if checked else "", "productionAt": now if checked else "",
                        "preparing": False if checked else order.get("preparing", False),
                        "preparingBy": "" if checked else order.get("preparingBy", ""),
                        "preparingAt": "" if checked else order.get("preparingAt", ""),
                    })
                elif action == "shipping":
                    if checked and not order.get("productionDone"):
                        return self._json(409, {"error": "제작 완료 후 출고 처리할 수 있습니다.", "order": order})
                    if checked and not order.get("softwareInspectionDone"):
                        return self._json(409, {"error": "소프트웨어 검수 완료 후 출고 처리할 수 있습니다.", "order": order})
                    order.update({
                        "shippingDone": checked, "shippingBy": worker if checked else "", "shippingAt": now if checked else "",
                    })
                else:
                    return self._json(400, {"error": "잘못된 처리 요청입니다."})
                order["updatedAt"] = now
                write_orders(orders)
            write_audit("order_updated", user, orderId=order["id"], orderNumber=order.get("orderNumber", ""), action=action, checked=checked)
            return self._json(200, order)
        except (ValueError, json.JSONDecodeError):
            return self._json(400, {"error": "요청 내용을 확인하세요."})

    def do_DELETE(self) -> None:
        path = urlparse(self.path).path
        user = self._require_user()
        if not user: return
        if not path.startswith("/api/users/"):
            return self._json(404, {"error": "요청 경로를 찾을 수 없습니다."})
        if user_role(user) not in MEMBER_MANAGEMENT_ROLES:
            return self._json(403, {"error": "총책임자 또는 개발자만 계정을 삭제할 수 있습니다."})
        user_id = path.rsplit("/", 1)[-1]
        if user_id == user["id"]:
            return self._json(400, {"error": "현재 로그인한 계정은 삭제할 수 없습니다."})
        users = AUTH.read_users()
        target = next((item for item in users if item["id"] == user_id), None)
        if not target:
            return self._json(404, {"error": "사용자를 찾을 수 없습니다."})
        if user_role(user) == "developer" and ROLE_RANK.get(user_role(target), 999) <= ROLE_RANK["developer"]:
            return self._json(403, {"error": "개발자는 총책임자나 다른 개발자 계정을 삭제할 수 없습니다."})
        if user_role(target) == "owner" and sum(user_role(item) == "owner" and item.get("enabled", True) for item in users) <= 1:
            return self._json(400, {"error": "마지막 총책임자 계정은 삭제할 수 없습니다."})
        try:
            deleted = AUTH.delete_user(user_id)
            return self._json(200, deleted)
        except ValueError as error:
            return self._json(400, {"error": str(error)})

    def _update_user(self, user_id: str, current_user: dict) -> None:
        try:
            payload = json.loads(self._body())
            users = AUTH.read_users()
            target = next((item for item in users if item["id"] == user_id), None)
            if not target:
                return self._json(404, {"error": "사용자를 찾을 수 없습니다."})
            role = normalize_role(str(payload.get("role", target.get("role", "worker"))))
            enabled = bool(payload.get("enabled", target.get("enabled", True)))
            if user_role(current_user) == "developer" and (
                ROLE_RANK.get(user_role(target), 999) <= ROLE_RANK["developer"]
                or ROLE_RANK.get(role, 999) <= ROLE_RANK["developer"]
            ):
                return self._json(403, {"error": "개발자는 총책임자나 개발자 권한을 수정 또는 지정할 수 없습니다."})
            if user_id == current_user["id"] and (role not in MEMBER_MANAGEMENT_ROLES or not enabled):
                return self._json(400, {"error": "현재 로그인한 계정의 관리 권한이나 사용 상태는 변경할 수 없습니다."})
            active_owners = sum(user_role(item) == "owner" and item.get("enabled", True) for item in users)
            removes_active_owner = user_role(target) == "owner" and target.get("enabled", True) and (role != "owner" or not enabled)
            if removes_active_owner and active_owners <= 1:
                return self._json(400, {"error": "마지막 총책임자는 다른 권한으로 변경하거나 비활성화할 수 없습니다."})
            updated = AUTH.update_user(
                user_id,
                str(payload.get("username", target.get("username", ""))),
                str(payload.get("displayName", target.get("displayName", ""))),
                role,
                enabled,
                str(payload.get("password", "")),
            )
            return self._json(200, updated)
        except ValueError as error:
            return self._json(400, {"error": str(error)})
        except json.JSONDecodeError:
            return self._json(400, {"error": "계정 정보를 확인하세요."})

    def _export_shipped(self, archive: bool) -> None:
        # 출고 완료 주문만 내보내고, 아카이브 방식이면 내보낸 뒤 보관 상태로 바꾼다.
        headers = ["번호", "채널", "주문번호", "주문일", "상품명", "옵션", "수량", "상품코드", "제품관리번호", "수령인", "연락처", "우편번호", "주소", "배송메시지", "제작담당자", "제작완료일", "출고담당자", "출고완료일"]
        fields = ["channel", "orderNumber", "orderedAt", "productName", "optionName", "quantity", "productCode", "managementNumber", "recipient", "phone", "postalCode", "address", "deliveryMessage", "productionBy", "productionAt", "shippingBy", "shippingAt"]
        with LOCK:
            orders = read_orders()
            new_shipped = [
                order for order in orders
                if order.get("shippingDone") and not order.get("archivedAt") and not order.get("cancelledAt")
            ]
            if not new_shipped:
                return self._json(400, {"error": "새로 출고 완료된 주문이 없습니다."})
            new_shipped.sort(key=lambda order: order.get("shippingAt", ""))
            rows = [[index, *(order.get(field, "") for field in fields)] for index, order in enumerate(new_shipped, 1)]
            try:
                content = write_xlsx(headers, rows)
            except Exception:
                return self._json(500, {"error": "출고 엑셀을 만들지 못했습니다. 주문은 보관 처리되지 않았습니다."})
            if archive:
                archived_at = datetime.now(timezone.utc).isoformat()
                for order in new_shipped:
                    order["archivedAt"] = archived_at
                    order["updatedAt"] = archived_at
                if new_shipped:
                    write_orders(orders)
        if archive:
            write_audit("orders_exported", self._current_user(), count=len(new_shipped), archived=len(new_shipped))
        self.send_response(200)
        self.send_header("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        self.send_header("Content-Disposition", f'attachment; filename="shipped-orders-{date.today().isoformat()}.xlsx"')
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args) -> None:
        print(f"[{self.log_date_time_string()}] {format % args}")


if __name__ == "__main__":
    serve()
