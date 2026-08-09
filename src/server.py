from __future__ import annotations

import json
import os
import re
import signal
import shutil
import threading
import time
import copy
import sys
from collections import deque
from datetime import date, datetime, timezone
from http.cookies import SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from logging import Formatter, Logger
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TextIO
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent))

from excel import write_xlsx
from auth import AuthStore, normalize_role
from config import (
    AS_HISTORY_ROLES,
    AUDIT_FILE,
    BACKUP_RETENTION_DAYS,
    CANCEL_ORDER_ROLES,
    DATA_FILE,
    DUPLICATE_CLEANUP_ROLES,
    LATEST_IMPORT_DELETE_ROLES,
    LOCAL_TZ,
    LOGIN_BLOCK_SECONDS,
    LOGIN_FAILURE_LIMIT,
    MAX_ARCHIVE_FILES,
    MAX_UPLOAD_BYTES,
    MAX_UPLOAD_FILES,
    MEMBER_MANAGEMENT_ROLES,
    MONTHLY_STATS_ROLES,
    NOTICE_EDIT_ROLES,
    NOTICE_FILE,
    ORDER_ADMIN_ROLES,
    ORDER_EDIT_ROLES,
    PUBLIC,
    ROLE_RANK,
    ROOT,
    USERS_FILE,
)
from orders.dedupe import (
    SHIPPING_UPDATE_FIELDS,
    cleanup_duplicate_orders,
    is_synthetic_order_number as _is_synthetic_order_number,
    merge_duplicate_order_details,
    new_unique_orders,
    normalized_order_number as _normalized_order_number,
    normalized_order_value as _normalized_order_value,
)
from orders.import_service import archive_excel_files, imported_orders_from_multipart

LOCK = threading.Lock()
AUDIT_LOCK = threading.Lock()
SETUP_LOCK = threading.Lock()
AUTH = AuthStore(USERS_FILE)
LOGIN_LOCK = threading.Lock()
LOGIN_FAILURES: dict[str, list[float]] = {}
SERVER_STARTED_AT = time.time()

DEFAULT_LOGIN_NOTICE = {
    "title": "공지사항",
    "lead": "채널 주문 필수 확인",
    "message": "스마트스토어, 쿠팡, 카카오 주문에는\n원키와 리브레가 반드시 들어가 있어야 합니다.",
}


class RotatingTextStream:
    """Line-buffered text stream backed by a size-rotating log file."""

    def __init__(self, path: Path, max_bytes: int, backup_count: int) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._logger = Logger(f"runtime.{path.name}.{id(self)}")
        handler = RotatingFileHandler(
            path,
            maxBytes=max_bytes,
            backupCount=backup_count,
            encoding="utf-8",
            delay=True,
        )
        handler.setFormatter(Formatter("%(message)s"))
        self._logger.addHandler(handler)
        self._buffer = ""

    def write(self, value: str) -> int:
        text = str(value)
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._logger.info(line.rstrip("\r"))
        return len(text)

    def flush(self) -> None:
        if self._buffer:
            self._logger.info(self._buffer.rstrip("\r"))
            self._buffer = ""
        for handler in self._logger.handlers:
            handler.flush()

    def close(self) -> None:
        self.flush()
        for handler in self._logger.handlers:
            handler.close()
        self._logger.handlers.clear()

    def isatty(self) -> bool:
        return False


def configure_runtime_logs() -> tuple[TextIO, TextIO]:
    max_bytes = max(1024, int(os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024))))
    backup_count = max(1, int(os.getenv("LOG_BACKUP_COUNT", "5")))
    output_path = Path(os.getenv("SERVER_LOG_FILE", ROOT / "order-workflow-server.log"))
    error_path = Path(os.getenv("SERVER_ERROR_LOG_FILE", ROOT / "order-workflow-server.err.log"))
    return (
        RotatingTextStream(output_path, max_bytes, backup_count),
        RotatingTextStream(error_path, max_bytes, backup_count),
    )


def restrict_permissions(path: Path, mode: int) -> None:
    if os.name != "nt":
        os.chmod(path, mode)


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


WORKLOAD_STAGES = (
    ("production", "제작 완료", "productionBy", "productionAt"),
    ("inspection", "SW 검수", "softwareInspectionBy", "softwareInspectionAt"),
)


def workload_stats(date_from: str, date_to: str) -> dict:
    """Aggregate recorded order work by worker for an inclusive local-date range."""
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as error:
        raise ValueError("조회 기간을 올바르게 입력하세요.") from error
    if end < start:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
    if (end - start).days > 366:
        raise ValueError("조회 기간은 최대 1년입니다.")

    workers: dict[str, dict] = {}
    work_orders: list[dict] = []
    for order in read_orders():
        # 최종 실적은 출고가 완료된 정상 주문에서만 집계한다.
        if not order.get("shippingDone") or order.get("cancelledAt"):
            continue
        try:
            quantity = max(1, int(order.get("quantity") or 1))
        except (TypeError, ValueError):
            quantity = 1
        for key, label, worker_field, time_field in WORKLOAD_STAGES:
            worker = str(order.get(worker_field, "")).strip()
            timestamp = str(order.get(time_field, "")).strip()
            if not worker or not timestamp:
                continue
            try:
                occurred_at = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                if occurred_at.tzinfo is None:
                    occurred_at = occurred_at.replace(tzinfo=LOCAL_TZ)
                work_date = occurred_at.astimezone(LOCAL_TZ).date()
            except ValueError:
                continue
            if not start <= work_date <= end:
                continue
            row = workers.setdefault(worker, {
                "worker": worker, "total": 0, "units": 0,
                "stages": {stage_key: 0 for stage_key, *_ in WORKLOAD_STAGES},
            })
            row["total"] += 1
            row["units"] += quantity
            row["stages"][key] += 1
            work_orders.append({
                    "id": str(order.get("id", "")),
                    "stage": key,
                    "stageLabel": label,
                    "worker": worker,
                    "completedAt": timestamp,
                    "orderNumber": str(order.get("orderNumber", "")),
                    "channel": str(order.get("channel", "")),
                    "productName": str(order.get("productName", "")),
                    "optionName": str(order.get("optionName", "")),
                    "quantity": quantity,
                    "recipient": str(order.get("recipient", "")),
                    "managementNumber": str(order.get("managementNumber", "")),
            })

    rows = sorted(workers.values(), key=lambda item: (-item["total"], item["worker"]))
    return {
        "dateFrom": date_from, "dateTo": date_to, "days": (end - start).days + 1,
        "stageLabels": {key: label for key, label, *_ in WORKLOAD_STAGES},
        "workers": rows,
        "workOrders": sorted(
            work_orders,
            key=lambda item: (item["completedAt"], item["orderNumber"]),
            reverse=True,
        ),
        "total": sum(item["total"] for item in rows),
        "units": sum(item["units"] for item in rows),
    }


def latest_import_candidates(orders: list[dict]) -> list[dict]:
    imported = [order for order in orders if order.get("sourceFile") and order.get("sourceFile") != "수기입력"]
    if not imported:
        return []
    latest_created_at = max(str(order.get("createdAt", "")) for order in imported)
    if not latest_created_at:
        return []
    candidates = [order for order in imported if str(order.get("createdAt", "")) == latest_created_at]
    protected_fields = (
        "preparing", "managementNumber", "productionDone", "softwareInspectionDone",
        "shippingDone", "cancelledAt", "archivedAt",
    )
    return [order for order in candidates if not any(order.get(field) for field in protected_fields)]


def file_status(file_path: Path) -> dict:
    parent = file_path.parent
    exists = file_path.exists()
    stat = file_path.stat() if exists else None
    readable = os.access(file_path if exists else parent, os.R_OK)
    writable = os.access(file_path if exists else parent, os.W_OK)
    status = "ok" if exists and readable and writable else "ready" if not exists and parent.exists() and writable else "warning"
    return {
        "exists": exists,
        "status": status,
        "size": stat.st_size if stat else 0,
        "updatedAt": datetime.fromtimestamp(stat.st_mtime, LOCAL_TZ).isoformat() if stat else "",
        "readable": readable,
        "writable": writable,
        "directoryReady": parent.exists(),
    }


def server_health() -> dict:
    return {
        "ok": True,
        "status": "ok",
        "serverTime": datetime.now(LOCAL_TZ).isoformat(),
        "uptimeSeconds": int(time.time() - SERVER_STARTED_AT),
        "activeThreads": threading.active_count(),
        "dataFiles": {
            "orders": file_status(DATA_FILE),
            "users": file_status(USERS_FILE),
            "audit": file_status(AUDIT_FILE),
        },
    }


def backup_file(file_path: Path) -> None:
    # 하루에 한 번만 백업을 남기고, 오래된 백업은 자동으로 정리한다.
    if not file_path.exists():
        return
    backup_dir = file_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    restrict_permissions(backup_dir, 0o700)
    today = datetime.now().strftime("%Y%m%d")
    backup = backup_dir / f"{file_path.name}.{today}.bak"
    if not backup.exists():
        shutil.copy2(file_path, backup)
        restrict_permissions(backup, 0o600)
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
        restrict_permissions(AUDIT_FILE, 0o600)


def read_audit(limit: int = 1000) -> list[dict]:
    max_records = max(1, min(limit, 1000))
    try:
        with AUDIT_FILE.open(encoding="utf-8") as audit_file:
            lines = deque(audit_file, maxlen=max_records)
    except FileNotFoundError:
        return []
    records: list[dict] = []
    for line in lines:
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    records.reverse()
    return records


def read_notifications(limit: int = 50) -> list[dict]:
    allowed_events = {"order_created", "order_updated", "order_cancelled", "order_restored"}
    notifications: list[dict] = []
    for record in read_audit(limit=300):
        if record.get("event") not in allowed_events:
            continue
        notifications.append({
            "timestamp": record.get("timestamp", ""),
            "event": record.get("event", ""),
            "userId": record.get("userId", ""),
            "displayName": record.get("displayName", ""),
            "orderNumber": record.get("orderNumber", ""),
            "action": record.get("action", ""),
            "checked": record.get("checked", ""),
        })
        if len(notifications) >= max(1, min(limit, 100)):
            break
    return notifications


def read_login_notice() -> dict:
    try:
        stored = json.loads(NOTICE_FILE.read_text(encoding="utf-8-sig"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        stored = {}
    return {
        key: str(stored.get(key, default)).strip() or default
        for key, default in DEFAULT_LOGIN_NOTICE.items()
    }


def write_login_notice(notice: dict) -> None:
    NOTICE_FILE.parent.mkdir(parents=True, exist_ok=True)
    restrict_permissions(NOTICE_FILE.parent, 0o700)
    temporary = NOTICE_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(notice, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    restrict_permissions(temporary, 0o600)
    temporary.replace(NOTICE_FILE)
    restrict_permissions(NOTICE_FILE, 0o600)


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


def write_orders(orders: list[dict]) -> None:
    # 임시 파일에 먼저 쓰고 원자적으로 교체해서 저장 중 손상을 줄인다.
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    restrict_permissions(DATA_FILE.parent, 0o700)
    backup_file(DATA_FILE)
    temporary = DATA_FILE.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(orders, output, ensure_ascii=False, indent=2)
        output.flush()
        os.fsync(output.fileno())
    restrict_permissions(temporary, 0o600)
    temporary.replace(DATA_FILE)
    restrict_permissions(DATA_FILE, 0o600)


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
    restrict_permissions(temp_dir, 0o700)
    for source in existing_sources:
        shutil.copy2(source, temp_dir / source.name)
        restrict_permissions(temp_dir / source.name, 0o600)
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    temp_dir.rename(backup_dir)
    restrict_permissions(backup_dir, 0o700)


def serve() -> None:
    sys.stdout, sys.stderr = configure_runtime_logs()
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
    try:
        httpd.serve_forever(poll_interval=0.5)
    finally:
        httpd.server_close()
        for signum, handler in previous_handlers.items():
            signal.signal(signum, handler)
        write_shutdown_backup()


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


def date_key_from_value(value: object) -> str:
    value = str(value or "").strip().replace("/", "-").replace(".", "-")
    digits = re.sub(r"\D", "", value)
    if len(digits) >= 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", value)
    if match:
        year, month, day = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return ""


def normalize_ordered_at_input(value: object, fallback: object = "") -> str:
    text = str(value or "").strip()
    if not text:
        return str(fallback or "").strip()
    text = text.replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", text):
        return f"{text}:00"
    return text


def order_collection_date_key(order: dict) -> str:
    created_at = str(order.get("createdAt", "")).strip()
    if created_at:
        try:
            parsed = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(LOCAL_TZ).date().isoformat()
        except ValueError:
            pass
    return date_key_from_value(order.get("createdAt")) or date_key_from_value(order.get("orderedAt"))


def daily_order_stats() -> list[dict]:
    counts: dict[str, int] = {}
    for order in read_orders():
        key = order_collection_date_key(order)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1
    return [{"date": key, "count": counts[key]} for key in sorted(counts)]


def orders_collected_on(date_key: str) -> list[dict]:
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_key):
        raise ValueError("조회 날짜는 YYYY-MM-DD 형식이어야 합니다.")
    return sorted(
        (order for order in read_orders() if order_collection_date_key(order) == date_key),
        key=order_datetime_key,
    )


def orders_collected_between(date_from: str, date_to: str) -> list[dict]:
    try:
        start = date.fromisoformat(date_from)
        end = date.fromisoformat(date_to)
    except ValueError as error:
        raise ValueError("조회 기간을 올바르게 입력하세요.") from error
    if end < start:
        raise ValueError("종료일은 시작일보다 빠를 수 없습니다.")
    if (end - start).days > 366:
        raise ValueError("조회 기간은 최대 1년입니다.")
    return sorted(
        (
            order for order in read_orders()
            if date_from <= order_collection_date_key(order) <= date_to
        ),
        key=order_datetime_key,
    )


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PUBLIC), **kwargs)

    def end_headers(self) -> None:
        path = urlparse(getattr(self, "path", "")).path
        if path == "/" or path.endswith((".html", ".js")) or path.startswith("/api/"):
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "public, max-age=86400")
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

    def _request_payload(self) -> tuple[dict, bool]:
        body = self._body()
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type == "application/x-www-form-urlencoded":
            parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
            return {key: values[-1] if values else "" for key, values in parsed.items()}, True
        return json.loads(body), False

    def _redirect(self, location: str, headers: dict[str, str] | None = None) -> None:
        self.send_response(303)
        self.send_header("Location", location)
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()

    def _login_success_headers(self, token: str) -> dict[str, str]:
        return {"Set-Cookie": f"halfbook_session={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=43200"}

    def _serve_index(self) -> None:
        content = (PUBLIC / "index.html").read_text(encoding="utf-8")
        if self._current_user():
            content = content.replace('<body class="is-login-page">', '<body class="is-authenticated">')
        encoded = content.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def do_GET(self) -> None:
        parsed_url = urlparse(self.path)
        path = parsed_url.path
        if path == "/":
            query = parse_qs(parsed_url.query, keep_blank_values=True)
            if "username" in query and "password" in query:
                username = query.get("username", [""])[-1]
                authenticated = AUTH.authenticate(username, query.get("password", [""])[-1])
                if authenticated:
                    token, user = authenticated
                    write_audit("login_succeeded", user, clientIp=self.client_address[0])
                    return self._redirect("/", self._login_success_headers(token))
            return self._serve_index()
        if path == "/api/health":
            return self._json(200, server_health())
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
        if path == "/api/orders/daily-stats":
            user = self._current_user()
            if not user or user_role(user) not in MONTHLY_STATS_ROLES:
                return self._json(403, {"error": "월간 현황은 총책임자만 조회할 수 있습니다."})
            return self._json(200, daily_order_stats())
        if path == "/api/orders/by-date":
            user = self._current_user()
            if not user or user_role(user) not in MONTHLY_STATS_ROLES:
                return self._json(403, {"error": "월간 현황은 총책임자만 조회할 수 있습니다."})
            query = parse_qs(parsed_url.query)
            date_key = query.get("date", [""])[-1]
            try:
                date_from = query.get("from", [""])[-1]
                date_to = query.get("to", [""])[-1]
                if date_from or date_to:
                    return self._json(200, orders_collected_between(date_from, date_to))
                return self._json(200, orders_collected_on(date_key))
            except ValueError as error:
                return self._json(400, {"error": str(error)})
        if path == "/api/orders":
            orders = sorted(
                (item for item in read_orders() if not item.get("archivedAt") and not item.get("cancelledAt")),
                key=order_datetime_key,
            )
            return self._json(200, orders)
        if path == "/api/notifications":
            return self._json(200, read_notifications())
        if path == "/api/login-notice":
            return self._json(200, read_login_notice())
        if path == "/api/audit":
            user = self._current_user()
            if not user or user_role(user) not in MEMBER_MANAGEMENT_ROLES:
                return self._json(403, {"error": "관리자만 변경 이력을 조회할 수 있습니다."})
            return self._json(200, read_audit())
        if path == "/api/workload-stats":
            user = self._current_user()
            if not user or user_role(user) not in MEMBER_MANAGEMENT_ROLES:
                return self._json(403, {"error": "관리자만 작업량을 조회할 수 있습니다."})
            query = parse_qs(parsed_url.query)
            today = datetime.now(LOCAL_TZ).date()
            date_from = query.get("from", [today.replace(day=1).isoformat()])[-1]
            date_to = query.get("to", [today.isoformat()])[-1]
            try:
                return self._json(200, workload_stats(date_from, date_to))
            except ValueError as error:
                return self._json(400, {"error": str(error)})
        if path == "/api/orders/latest-import":
            user = self._current_user()
            if not user or user_role(user) not in LATEST_IMPORT_DELETE_ROLES:
                return self._json(403, {"error": "총책임자, 개발자 또는 MD만 최근 수집 주문을 확인할 수 있습니다."})
            orders = read_orders()
            candidates = latest_import_candidates(orders)
            return self._json(200, {
                "count": len(candidates),
                "createdAt": candidates[0].get("createdAt", "") if candidates else "",
                "sourceFiles": sorted({str(item.get("sourceFile", "")) for item in candidates}),
            })
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
                payload, is_form_post = self._request_payload()
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
                if is_form_post:
                    return self._redirect("/", self._login_success_headers(token))
                return self._json(200, {"user": user}, self._login_success_headers(token))
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
        if path == "/api/orders/dedupe":
            if user_role(user) not in DUPLICATE_CLEANUP_ROLES:
                return self._json(403, {"error": "총책임자 또는 MD만 중복 주문을 정리할 수 있습니다."})
            with LOCK:
                orders = read_orders()
                result = cleanup_duplicate_orders(orders)
                if result["removed"]:
                    write_orders(orders)
            write_audit("orders_deduplicated", user, **result)
            return self._json(200, result)
        if path == "/api/export/shipped":
            if user_role(user) not in ORDER_ADMIN_ROLES: return self._json(403, {"error": "해당 권한으로 출고 완료 엑셀을 만들 수 없습니다."})
            return self._export_shipped(archive=True)
        if path not in {"/api/import", "/api/import/preview"}:
            return self._json(404, {"error": "요청 경로를 찾을 수 없습니다."})
        if user_role(user) not in ORDER_ADMIN_ROLES:
            return self._json(403, {"error": "해당 권한으로 주문 엑셀을 가져올 수 없습니다."})
        try:
            imported, errors, file_count = imported_orders_from_multipart(self.headers.get("Content-Type", ""), self._body())
            if path == "/api/import/preview":
                with LOCK:
                    orders = copy.deepcopy(read_orders())
                    added, shipping_updates = new_unique_orders(orders, copy.deepcopy(imported), datetime.now(timezone.utc).isoformat())
                preview = [{
                    "orderNumber": order.get("orderNumber", ""),
                    "channel": order.get("channel", ""),
                    "recipient": order.get("recipient", ""),
                    "productName": order.get("productName", ""),
                    "quantity": order.get("quantity", 0),
                    "amount": order.get("amount", 0),
                    "sourceFile": order.get("sourceFile", ""),
                } for order in added[:30]]
                return self._json(200, {
                    "parsed": len(imported), "added": len(added), "duplicates": len(imported) - len(added),
                    "shippingUpdates": shipping_updates, "files": file_count, "errors": errors, "preview": preview,
                })
            with LOCK:
                orders = read_orders()
                now = datetime.now(timezone.utc).isoformat()
                added, shipping_updates = new_unique_orders(orders, imported, now)
                orders.extend(added)
                write_orders(orders)
            write_audit("orders_imported", user, added=len(added), duplicates=len(imported) - len(added), shippingUpdates=shipping_updates, files=file_count)
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
                    "orderedAt": normalize_ordered_at_input(payload.get("orderedAt"), datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                    "productName": product_name, "optionName": str(payload.get("optionName", "")).strip(),
                    "productCode": str(payload.get("productCode", "")).strip(), "quantity": quantity, "amount": amount,
                    "recipient": recipient, "phone": phone, "postalCode": str(payload.get("postalCode", "")).strip(),
                    "address": str(payload.get("address", "")).strip(),
                    "deliveryMessage": str(payload.get("deliveryMessage", "")).strip(), "courier": "", "trackingNumber": "",
                    "memo": str(payload.get("memo", "")).strip(),
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
        if path == "/api/login-notice":
            if user_role(user) not in NOTICE_EDIT_ROLES:
                return self._json(403, {"error": "공지사항은 총관리자와 MD만 수정할 수 있습니다."})
            try:
                payload = json.loads(self._body())
            except (json.JSONDecodeError, ValueError):
                return self._json(400, {"error": "공지사항 내용을 확인하세요."})
            limits = {"title": 80, "lead": 120, "message": 2000}
            notice = {}
            for key, limit in limits.items():
                value = str(payload.get(key, "")).strip()
                if not value:
                    return self._json(400, {"error": "제목, 소제목, 내용은 모두 입력해야 합니다."})
                if len(value) > limit:
                    return self._json(400, {"error": f"공지 {key} 항목은 {limit}자 이내로 입력하세요."})
                notice[key] = value
            write_login_notice(notice)
            write_audit("login_notice_updated", user)
            return self._json(200, notice)
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
                if action == "restoreCancel":
                    if user_role(user) not in CANCEL_ORDER_ROLES:
                        return self._json(403, {"error": "해당 권한으로 주문을 복구할 수 없습니다.", "order": order})
                    if not order.get("cancelledAt"):
                        return self._json(409, {"error": "취소된 주문이 아닙니다.", "order": order})
                    order.pop("cancelledAt", None)
                    order.pop("cancelledBy", None)
                    order.pop("cancelReason", None)
                    order["restoredAt"] = now
                    order["restoredBy"] = worker
                    order["updatedAt"] = now
                    write_orders(orders)
                    write_audit("order_restored", user, orderId=order["id"], orderNumber=order.get("orderNumber", ""))
                    return self._json(200, order)
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
                        if item.get("cancelledAt"):
                            continue
                        existing_numbers.update(split_management_numbers(item.get("managementNumber", "")))
                    existing_numbers.clear()
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
                        "orderedAt": normalize_ordered_at_input(fields.get("orderedAt"), order.get("orderedAt", "")),
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
                        "memo": str(fields.get("memo", order.get("memo", ""))).strip(),
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
        if path == "/api/orders/latest-import":
            if user_role(user) not in LATEST_IMPORT_DELETE_ROLES:
                return self._json(403, {"error": "총책임자, 개발자 또는 MD만 최근 수집 주문을 삭제할 수 있습니다."})
            with LOCK:
                orders = read_orders()
                candidates = latest_import_candidates(orders)
                candidate_ids = {item.get("id") for item in candidates}
                if not candidate_ids:
                    return self._json(409, {"error": "삭제할 수 있는 최근 수집 주문이 없습니다. 이미 작업이 시작된 주문은 보호됩니다."})
                orders[:] = [item for item in orders if item.get("id") not in candidate_ids]
                write_orders(orders)
            write_audit("latest_import_deleted", user, count=len(candidate_ids))
            return self._json(200, {"deleted": len(candidate_ids)})
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
        message = f"[{self.log_date_time_string()}] {format % args}"
        try:
            sys.stdout.write(message + "\n")
            sys.stdout.flush()
        except Exception:
            pass


if __name__ == "__main__":
    serve()
