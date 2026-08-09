function systemTheme() {
  return window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function applyTheme(theme) {
  const nextTheme = theme === "dark" ? "dark" : "light";
  document.body.dataset.theme = nextTheme;
  localStorage.setItem(themeStorageKey, nextTheme);
  const button = $("#theme-toggle");
  if (button) button.textContent = nextTheme === "dark" ? "라이트 모드" : "다크 모드";
  syncNotificationSurfaceStyles();
}

function setAuthScreenState(isAuthenticated) {
  document.body.classList.toggle("is-authenticated", isAuthenticated);
  document.body.classList.toggle("is-login-page", !isAuthenticated);
  $("#auth-screen").hidden = isAuthenticated;
}

const channelRequirementNoticeKey = "channel-requirement-notice-hidden-until-next-login";

function renderChannelRequirementNotice(notice) {
  $("#channel-requirement-title").textContent = notice.title;
  $("#channel-requirement-lead").textContent = notice.lead;
  $("#channel-requirement-message").textContent = notice.message;
  const editor = $("#channel-notice-editor");
  editor.elements.title.value = notice.title;
  editor.elements.lead.value = notice.lead;
  editor.elements.message.value = notice.message;
  $("#notice-preview-title").textContent = notice.title;
  $("#notice-preview-lead").textContent = notice.lead;
  $("#notice-preview-message").textContent = notice.message;
}

async function loadChannelRequirementNotice() {
  const response = await OrderApi.get("/api/login-notice");
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "공지사항을 불러오지 못했습니다.");
  renderChannelRequirementNotice(result);
}

async function showChannelRequirementNotice() {
  if (sessionStorage.getItem(channelRequirementNoticeKey) === "true") return;
  try {
    await loadChannelRequirementNotice();
  } catch (error) {}
  $("#channel-requirement-snooze").checked = false;
  $("#channel-requirement-notice").hidden = false;
  $("#channel-requirement-close").focus();
}

function closeChannelRequirementNotice() {
  if ($("#channel-requirement-snooze").checked) {
    sessionStorage.setItem(channelRequirementNoticeKey, "true");
  }
  $("#channel-requirement-notice").hidden = true;
}

function initTheme() {
  applyTheme(localStorage.getItem(themeStorageKey) || systemTheme());
}

function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds || 0));
  const days = Math.floor(value / 86400);
  const hours = Math.floor((value % 86400) / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  if (days) return `${days}일 ${hours}시간`;
  if (hours) return `${hours}시간 ${minutes}분`;
  return `${minutes}분`;
}

function serverDataHasIssue(files) {
  return Object.values(files || {}).some((file) => file && file.status === "warning");
}

function renderServerStatus() {
  const container = $("#server-status");
  if (!container) return;
  const status = state.serverStatus;
  const label = $("#server-status-label");
  const detail = $("#server-status-detail");
  if (!label || !detail) return;
  const hasIssue = status.online && serverDataHasIssue(status.dataFiles);
  container.classList.toggle("is-online", status.online && !hasIssue);
  container.classList.toggle("is-warning", hasIssue);
  container.classList.toggle("is-offline", !status.online && !status.checking);
  container.classList.toggle("is-checking", status.checking);
  if (status.checking) {
    label.textContent = "서버 확인 중";
    detail.textContent = "상태를 불러오는 중";
    container.title = "서버 상태 확인 중";
    return;
  }
  if (!status.online) {
    label.textContent = "서버 응답 없음";
    detail.textContent = status.checkedAt ? `마지막 확인 ${formatClock(status.checkedAt)}` : "연결 실패";
    container.title = status.error || "서버에 연결하지 못했습니다.";
    return;
  }
  label.textContent = hasIssue ? "서버 확인 필요" : "서버 정상";
  detail.textContent = `${hasIssue ? "데이터 확인 필요" : "데이터 정상"} · ${status.latencyMs}ms · ${formatDuration(status.uptimeSeconds)}`;
  container.title = `마지막 확인 ${formatClock(status.checkedAt)} · 서버 시간 ${formatDate(status.serverTime)}`;
}

async function refreshServerStatus() {
  const startedAt = performance.now();
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 4000);
  try {
    const response = await fetch(`/api/health?t=${Date.now()}`, { cache:"no-store", signal: controller.signal });
    const latencyMs = Math.round(performance.now() - startedAt);
    const result = await response.json();
    if (!response.ok || !result.ok) throw new Error(result.error || `HTTP ${response.status}`);
    state.serverStatus = {
      online: true,
      checking: false,
      latencyMs,
      uptimeSeconds: result.uptimeSeconds || 0,
      checkedAt: new Date(),
      serverTime: result.serverTime || "",
      dataFiles: result.dataFiles || {},
      error: "",
    };
  } catch (error) {
    state.serverStatus = {
      online: false,
      checking: false,
      latencyMs: 0,
      uptimeSeconds: 0,
      checkedAt: new Date(),
      dataFiles: {},
      error: error.message,
    };
  } finally {
    window.clearTimeout(timeout);
  }
  renderServerStatus();
}

function startServerStatusMonitor() {
  refreshServerStatus().catch(() => {});
  if (state.serverStatusTimer) window.clearInterval(state.serverStatusTimer);
  state.serverStatusTimer = window.setInterval(() => refreshServerStatus().catch(() => {}), 15000);
}

initTheme();

function productDetails(order) {
  // 서버에는 상품/옵션이 납작한 문자열로 저장된다.
  // 복수 노트북 주문은 여기서 다시 상품 그룹으로 풀어 화면에 각각 보여준다.
  const optionParts = String(order.optionName || "").split(" / ").map((value) => value.trim()).filter(Boolean);
  const registeredOption = String(order.productCode || optionParts[0] || "").trim();
  const extras = optionParts.filter((value, index) => !(index === 0 && value === registeredOption));
  const extraProducts = extras.filter((value) => value.includes("노트북"));
  const extraOptions = extras.filter((value) => !value.includes("노트북"));
  const productGroups = [{ name: String(order.productName || "").trim(), registeredOption, options: [] }];
  extras.forEach((value) => {
    if (value.includes("노트북")) {
      productGroups.push({ name: value, registeredOption: "", options: [] });
    } else {
      productGroups[productGroups.length - 1].options.push(value);
    }
  });
  return { registeredOption, extras, extraProducts, extraOptions, productGroups };
}

function renderProductInfo(order, { memo = "", amountText = "" } = {}) {
  // 주문 목록, 월간 날짜 조회, 보관함이 같은 상품 표시 규칙을 공유한다.
  const details = productDetails(order);
  const productBlocks = details.productGroups
    .filter((group) => group.name || group.registeredOption || group.options.length)
    .map((group) => `
      <div class="order-product-block">
        <span class="product-label">상품명 + 옵션명</span>
        ${group.name ? `<div class="product">${escapeHtml(group.name)}</div>` : ""}
        ${group.registeredOption ? `<div class="registered-option"><span>등록옵션명</span><strong>${escapeHtml(group.registeredOption)}</strong></div>` : ""}
        ${group.options.length ? `<div class="extra-options"><span>선택 옵션</span>${group.options.map((option) => `<em>${escapeHtml(option)}</em>`).join("")}</div>` : ""}
      </div>
    `).join("");
  return `
    <div class="product-info product-info--grouped">
      ${productBlocks}
      <div class="product-summary"><span>수량 <strong>${order.quantity}개</strong></span><span>결제금액 <strong>${amountText || `${formatAmount(order.amount)}원`}</strong></span></div>
      ${memo}
    </div>`;
}

function channelTone(channel) {
  const value = String(channel || "");
  if (value === "b2b" || value.toLowerCase().includes("b2b")) return "b2b";
  if (value === "b2c" || value.toLowerCase().includes("b2c")) return "b2c";
  if (value.includes("전화") || value.includes("수기")) return "phone";
  if (value.includes("방문")) return "visit";
  if (value.includes("교환")) return "exchange";
  if (value.includes("스마트스토어") || value.includes("스마트 스토어")) return "smartstore";
  if (value.includes("쿠팡")) return "coupang";
  if (value.includes("카카오")) return "kakao";
  if (value.includes("고도몰") || value.includes("자사")) return "godomall";
  return "other";
}

function manualOrderChannelValue(channel) {
  const value = String(channel || "");
  const lower = value.toLowerCase();
  if (value.includes("스마트스토어") || value.includes("스마트 스토어")) return "스마트스토어";
  if (value.includes("고도몰") || value.includes("자사")) return "고도몰";
  if (value.includes("카카오")) return "카카오";
  if (value.includes("쿠팡")) return "쿠팡";
  if (lower.includes("b2b")) return "b2b";
  if (lower.includes("b2c")) return "b2c";
  if (value.includes("방문")) return "방문";
  if (value.includes("교환")) return "제품 교환";
  return "전화";
}

function orderStatus(order) {
  if (order.cancelledAt) return { tone:"cancelled", label:"취소 처리" };
  if (order.archivedAt) return { tone:"shipped", label:"출고 보관" };
  if (order.shippingDone) return { tone:"shipped", label:"출고 확인" };
  if (order.softwareInspectionDone) return { tone:"produced", label:"검수 완료" };
  if (order.productionDone) return { tone:"produced", label:"제작 완료" };
  if (order.preparing) return { tone:"preparing", label:"준비 중" };
  return { tone:"waiting", label:"제작 대기" };
}

function shippingUpdatePanel(order) {
  const pending = order.pendingShippingUpdate;
  if (!pending || !pending.fields) return "";
  const fields = pending.fields;
  const warning = pending.contentChangeWarning ? `<span class="shipping-update-warning">상품/수량 변경도 감지됨</span>` : "";
  return `
    <div class="shipping-update">
      <strong>배송지 변경 감지</strong>
      <dl>
        <div><dt>수령인</dt><dd>${escapeHtml(fields.recipient || order.recipient)}</dd></div>
        <div><dt>연락처</dt><dd>${escapeHtml(fields.phone || order.phone)}</dd></div>
        <div><dt>우편번호</dt><dd>${escapeHtml(fields.postalCode || order.postalCode)}</dd></div>
        <div><dt>주소</dt><dd>${escapeHtml(fields.address || order.address)}</dd></div>
        <div><dt>메시지</dt><dd>${escapeHtml(fields.deliveryMessage || "")}</dd></div>
      </dl>
      ${warning}
      ${canEditOrders() && !order.shippingDone ? `<button class="apply-shipping-update primary" type="button">배송지 반영</button>` : ""}
    </div>`;
}

function orderStatusKey(order) {
  if (order.cancelledAt) return "cancelled";
  return order.shippingDone ? "shipped" : order.softwareInspectionDone ? "softwareInspection" : order.productionDone ? "produced" : order.preparing ? "preparing" : "waiting";
}

function isArchivedOrCancelled(order) {
  return Boolean(order.archivedAt || order.cancelledAt);
}

function isBulkSelectableOrder(order) {
  return !isArchivedOrCancelled(order) && !order.shippingDone;
}

function boardOrders() {
  if (state.selectedCalendarDate && state.calendarOrdersDate === state.selectedCalendarDate) return state.calendarOrders;
  return state.orders;
}

function replaceOrderInList(list, updatedOrder) {
  const index = list.findIndex((order) => order.id === updatedOrder.id);
  if (index >= 0) list[index] = updatedOrder;
}

function worker() { return state.user && state.user.displayName || ""; }
function currentRole() { return state.user && state.user.role === "admin" ? "owner" : state.user && state.user.role; }
function canManageOrders() { return orderAdminRoles.has(currentRole()); }
function canEditOrders() { return orderEditRoles.has(currentRole()); }
function showMessage(text, error = false) { $("#message").textContent = text; $("#message").classList.toggle("error", error); }
function showManualMessage(text, error = false) { $("#manual-message").textContent = text; $("#manual-message").classList.toggle("error", error); }

function updateAccessUI() {
  // 역할에 따라 화면 전체에서 볼 수 있는 기능을 묶어서 숨기거나 보여준다.
  const role = state.user && state.user.role === "admin" ? "owner" : state.user && state.user.role;
  const canManageMembers = memberManagementRoles.has(role);
  const canEditNotice = noticeEditRoles.has(role);
  const canViewAudit = auditViewRoles.has(role);
  const canViewMonthlyStats = monthlyStatsRoles.has(role);
  const canCleanupDuplicates = duplicateCleanupRoles.has(role);
  const canManageOrders = orderAdminRoles.has(role);
  const canEditOrdersNow = orderEditRoles.has(role);
  const canViewAsHistory = asHistoryRoles.has(role);
  $("#members-tab").toggleAttribute("hidden", !canManageMembers);
  $("#audit-tab").toggleAttribute("hidden", !canViewAudit);
  $("#monthly-tab").toggleAttribute("hidden", !canViewMonthlyStats);
  $("#workload-tab").toggleAttribute("hidden", !canViewMonthlyStats);
  $("#notice-management-tab").toggleAttribute("hidden", !canEditNotice);
  $("#notification-button").toggleAttribute("hidden", !state.user);
  if (!state.user) $("#notification-popover").hidden = true;
  document.querySelectorAll(".as-history-only").forEach((element) => element.toggleAttribute("hidden", !canViewAsHistory));
  $("#upload-form").toggleAttribute("hidden", !canManageOrders);
  $("#export-button").toggleAttribute("hidden", !canManageOrders);
  // 중복 정리는 실제 저장 파일에서 행을 제거하므로 총책임자와 MD에게만 노출한다.
  $("#dedupe-button").toggleAttribute("hidden", !canCleanupDuplicates);
  const canDeleteLatestImport = canManageMembers || role === "md";
  $("#delete-latest-import").toggleAttribute("hidden", !canDeleteLatestImport);
  const importBar = document.querySelector(".order-import-bar");
  if (importBar) importBar.hidden = !(canManageOrders || canCleanupDuplicates || canDeleteLatestImport);
  $("#manual-order").toggleAttribute("hidden", !(canManageOrders || canEditOrdersNow));
  if (!canManageMembers && !$("#members-view").hidden) showWorkspaceView("orders-view");
  if (!canViewAudit && !$("#audit-view").hidden) showWorkspaceView("orders-view");
  if (!canViewMonthlyStats && !$("#monthly-stats-view").hidden) showWorkspaceView("orders-view");
  if (!canViewMonthlyStats && !$("#workload-view").hidden) showWorkspaceView("orders-view");
  if (!canEditNotice && !$("#notice-management-view").hidden) showWorkspaceView("orders-view");
}

function showWorkspaceView(viewId) {
  // 탭 이동 시 필요한 데이터만 다시 읽어서 화면을 최신 상태로 맞춘다.
  if (viewId === "monthly-stats-view" && !monthlyStatsRoles.has(currentRole())) viewId = "orders-view";
  if (viewId === "workload-view" && !monthlyStatsRoles.has(currentRole())) viewId = "orders-view";
  if (viewId === "notice-management-view" && !noticeEditRoles.has(currentRole())) viewId = "orders-view";
  document.querySelectorAll(".workspace-view").forEach((view) => { view.hidden = view.id !== viewId; });
  document.querySelectorAll(".workspace-tab").forEach((tab) => { tab.classList.toggle("is-active", tab.dataset.view === viewId); });
  if (viewId === "members-view") loadUsers();
  if (viewId === "shipping-customers-view") {
    loadArchivedOrders();
    if (asHistoryRoles.has(currentRole())) loadAsHistory();
  }
  if (viewId === "monthly-stats-view") {
    loadOrders();
    loadArchivedOrders();
    if (asHistoryRoles.has(currentRole())) loadAsHistory();
    renderShippingCalendar();
  }
  if (viewId === "workload-view") loadWorkloadStats();
  if (viewId === "cancelled-view") loadCancelledOrders();
  if (viewId === "audit-view") {
    loadAuditLogs();
  }
  if (viewId === "notice-management-view") {
    $("#channel-notice-editor-message").textContent = "";
    loadChannelRequirementNotice().catch((error) => {
      $("#channel-notice-editor-message").textContent = error.message;
    });
  }
}

function resetOrderFilters() {
  state.selectedChannel = "all";
  state.selectedStatus = "all";
  state.selectedCalendarDate = "";
  state.monthlySelectedCalendarDate = "";
  state.monthlyRangeStart = "";
  state.monthlyRangeEnd = "";
  state.monthlyProductionWorkerQuery = "";
  state.calendarOrders = [];
  state.calendarOrdersDate = "";
  state.monthlyCalendarOrders = [];
  state.monthlyCalendarOrdersDate = "";
  state.calendarMonth = currentMonthKey();
  $("#search").value = "";
  const monthlyWorkerSearch = $("#monthly-worker-search");
  if (monthlyWorkerSearch) monthlyWorkerSearch.value = "";
  $("#order-sort").value = "date-asc";
  $("#calendar-month").value = state.calendarMonth;
  syncChannelFilter();
}

function resetListFiltersForDateMode() {
  state.selectedChannel = "all";
  state.selectedStatus = "all";
  state.selectedOrderIds.clear();
  $("#search").value = "";
}

function setAuthMode(mode) {
  state.authMode = state.setupRequired ? "setup" : mode;
  const needsName = state.authMode !== "login";
  $("#display-name-field").hidden = !needsName;
  $("#display-name-field input").required = needsName;
  $("#auth-tabs").hidden = state.setupRequired;
  document.querySelectorAll("[data-auth-mode]").forEach((button) => { button.classList.toggle("is-active", button.dataset.authMode === mode); });
  $("#auth-title").textContent = state.setupRequired ? "최초 관리자 설정" : state.authMode === "register" ? "회원가입" : "로그인";
  $("#auth-help").textContent = state.setupRequired
    ? "처음 사용할 관리자 계정을 생성하세요."
    : state.authMode === "register" ? "가입한 계정은 일반 작업자 권한으로 생성됩니다." : "아이디와 비밀번호로 로그인하세요.";
  $("#auth-form button[type=submit]").textContent = state.setupRequired ? "관리자 계정 생성" : state.authMode === "register" ? "가입하기" : "로그인";
  $("#auth-message").textContent = "";
}

function filteredOrders() {
  const query = $("#search").value.trim().toLowerCase();
  const channel = state.selectedChannel;
  const status = state.selectedStatus;
  const selectedDate = state.selectedCalendarDate;
  const usingCalendarOrders = selectedDate && state.calendarOrdersDate === selectedDate;
  return boardOrders().filter((order) => {
    const matchesText = !query || [order.orderNumber, order.productName, order.optionName, order.recipient, order.productCode, order.address, order.deliveryMessage].join(" ").toLowerCase().includes(query);
    const matchesChannel = channel === "all" || order.channel === channel;
    const matchesStatus = status === "all" || orderStatusKey(order) === status;
    const matchesDate = !selectedDate || usingCalendarOrders || orderCollectionDateKey(order) === selectedDate;
    return matchesText && matchesChannel && matchesStatus && matchesDate;
  });
}

function orderTime(order) {
  const value = String(order.orderedAt || "").trim();
  const digits = value.replace(/\D/g, "");
  if (digits.length >= 8) {
    const year = Number(digits.slice(0, 4));
    const month = Number(digits.slice(4, 6));
    const day = Number(digits.slice(6, 8));
    const hour = Number(digits.slice(8, 10) || 0);
    const minute = Number(digits.slice(10, 12) || 0);
    const second = Number(digits.slice(12, 14) || 0);
    return new Date(year, month - 1, day, hour, minute, second).getTime();
  }
  const timestamp = Date.parse(value.replace(/[./]/g, "-").replace(/\s+/, "T"));
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function orderDateKey(order) {
  const timestamp = orderTime(order);
  if (!timestamp) return "";
  return dateKeyFromDate(new Date(timestamp));
}

function orderCollectionDateKey(order) {
  const value = String(order.createdAt || "").trim();
  const timestamp = Date.parse(value.replace(" ", "T"));
  if (!Number.isNaN(timestamp)) return dateKeyFromDate(new Date(timestamp));
  return orderDateKey(order);
}

function shippingTime(order) {
  const value = String(order.shippingAt || "").trim();
  const timestamp = Date.parse(value.replace(" ", "T"));
  return Number.isNaN(timestamp) ? 0 : timestamp;
}

function shippingDateKey(order) {
  const timestamp = shippingTime(order);
  if (!timestamp) return "";
  return dateKeyFromDate(new Date(timestamp));
}

function formatCalendarDateLabel(dateKey) {
  if (!dateKey) return "선택일";
  const [year, month, day] = dateKey.split("-").map(Number);
  const date = new Date(year, month - 1, day);
  return date.toLocaleDateString("ko-KR", { month:"long", day:"numeric", weekday:"short" });
}

function formatMonthLabel(monthKey) {
  const [year, month] = (monthKey || currentMonthKey()).split("-").map(Number);
  return `${year}년 ${String(month).padStart(2, "0")}월`;
}

function shiftCalendarMonth(direction) {
  const [year, month] = (state.calendarMonth || currentMonthKey()).split("-").map(Number);
  const date = new Date(year, month - 1 + direction, 1);
  state.calendarMonth = monthKeyFromDate(date);
  $("#calendar-month").value = state.calendarMonth;
  render();
}

function shiftShippingCalendarMonth(direction) {
  const [year, month] = (state.shippingCalendarMonth || currentMonthKey()).split("-").map(Number);
  const date = new Date(year, month - 1 + direction, 1);
  state.shippingCalendarMonth = monthKeyFromDate(date);
  renderShippingCalendar();
}

function captureOrderInputState() {
  const input = document.activeElement;
  if (!input || (!input.classList || (!input.classList.contains("management-number") && !input.classList.contains("order-edit-field")))) return null;
  const row = input.closest("tr");
  if (!row || !row.dataset.id) return null;
  return {
    orderId: row.dataset.id,
    field: input.dataset.field || "managementNumber",
    selectionStart: input.selectionStart,
    selectionEnd: input.selectionEnd,
    selectionDirection: input.selectionDirection,
  };
}

function restoreOrderInputState(snapshot) {
  if (!snapshot) return;
  const selector = snapshot.field === "managementNumber"
    ? `tr[data-id="${snapshot.orderId}"] .management-number`
    : `tr[data-id="${snapshot.orderId}"] .order-edit-field[data-field="${snapshot.field}"]`;
  const input = document.querySelector(selector);
  if (!input) return;
  input.focus({ preventScroll: true });
  if (typeof snapshot.selectionStart === "number" && typeof snapshot.selectionEnd === "number") {
    input.setSelectionRange(snapshot.selectionStart, snapshot.selectionEnd, snapshot.selectionDirection || "none");
  }
}

function getOrderDraft(order) {
  return state.orderDrafts[order.id] || null;
}

function orderFieldValue(order, field) {
  const draft = getOrderDraft(order);
  return draft ? (draft[field] == null ? "" : draft[field]) : (order[field] == null ? "" : order[field]);
}

function managementNumberCount(value) {
  return String(value || "").split(/\r?\n/).map((line) => line.trim()).filter(Boolean).length;
}

function managementNumberSummary(order, value) {
  const count = managementNumberCount(value);
  if (!count) return "미등록";
  const total = Number(order.quantity || 0);
  return total > 0 ? `${count}/${total}개 입력` : `${count}개 입력`;
}

function setManualOrderMode(order) {
  const form = $("#manual-order-form");
  const details = $("#manual-order");
  const summary = $("#manual-order-summary");
  const submit = $("#manual-order-submit");
  const cancel = $("#manual-order-cancel");
  const editingId = order && order.id || "";
  state.editingOrderId = editingId || null;
  form.elements.editOrderId.value = editingId;
  if (order) {
    form.elements.orderNumber.readOnly = true;
    form.elements.orderedAt.readOnly = false;
    form.elements.orderNumber.value = order.orderNumber || "";
    form.elements.orderedAt.value = String(order.orderedAt || "").replace(" ", "T").slice(0, 16);
    form.elements.channel.value = manualOrderChannelValue(order.channel);
    form.elements.productName.value = order.productName || "";
    form.elements.optionName.value = order.optionName || "";
    form.elements.productCode.value = order.productCode || "";
    form.elements.quantity.value = order.quantity == null ? 1 : order.quantity;
    form.elements.amount.value = order.amount == null ? 0 : order.amount;
    form.elements.recipient.value = order.recipient || "";
    form.elements.phone.value = order.phone || "";
    form.elements.postalCode.value = order.postalCode || "";
    form.elements.address.value = order.address || "";
    form.elements.deliveryMessage.value = order.deliveryMessage || "";
    form.elements.memo.value = order.memo || "";
    summary.textContent = `주문 수정: ${order.orderNumber}`;
    submit.textContent = "수정 저장";
    cancel.hidden = false;
    details.hidden = false;
    details.open = true;
    details.scrollIntoView({ behavior: "smooth", block: "start" });
    showManualMessage(`${order.orderNumber} 주문 수정 모드`);
    return;
  }
  summary.textContent = "수기 주문 등록";
  submit.textContent = "주문 목록에 등록";
  cancel.hidden = true;
  form.elements.orderNumber.readOnly = false;
  form.elements.orderedAt.readOnly = false;
  form.elements.channel.value = "전화";
  form.elements.editOrderId.value = "";
  state.editingOrderId = null;
  details.hidden = !(canManageOrders() || canEditOrders());
}

function resetManualOrderFormMode(message = "") {
  $("#manual-order-form").reset();
  setManualOrderMode(null);
  if (message) showManualMessage(message);
}

async function commitDateTimeInputs(form) {
  const inputs = [...form.querySelectorAll("input[type='datetime-local']")];
  inputs.forEach((input) => input.blur());
  await new Promise((resolve) => setTimeout(resolve, 0));
}

function compareOrders(left, right) {
  const timeDifference = orderTime(left) - orderTime(right);
  if (timeDifference) return timeDifference;
  return String(left.orderNumber || "").localeCompare(String(right.orderNumber || ""), "ko", { numeric:true, sensitivity:"base" });
}

function compareOrdersBySelectedSort(left, right) {
  const timeDifference = orderTime(left) - orderTime(right);
  const orderNumberDifference = String(left.orderNumber || "").localeCompare(String(right.orderNumber || ""), "ko", { numeric:true, sensitivity:"base" });
  const result = timeDifference || orderNumberDifference;
  const sort = $("#order-sort");
  return sort && sort.value === "date-desc" ? -result : result;
}

function syncOrderColumnVisibility() {
  defaultOrderColumns.forEach((column) => {
    document.body.classList.toggle(`hide-order-${column}`, !state.visibleOrderColumns.has(column));
  });
}

function syncBulkActionBar(visible) {
  const bar = $("#bulk-action-bar");
  if (!bar) return;
  const canBulkEdit = canEditOrders();
  const selectableIds = new Set(visible.filter(isBulkSelectableOrder).map((order) => order.id));
  [...state.selectedOrderIds].forEach((id) => {
    if (!selectableIds.has(id)) state.selectedOrderIds.delete(id);
  });
  const selectedCount = state.selectedOrderIds.size;
  const selectAll = $("#select-visible-orders");
  bar.hidden = !canBulkEdit || boardOrders().length === 0;
  $("#bulk-selected-count").textContent = `${selectedCount}건 선택`;
  if (selectAll) {
    selectAll.checked = selectableIds.size > 0 && selectedCount === selectableIds.size;
    selectAll.indeterminate = selectedCount > 0 && selectedCount < selectableIds.size;
    selectAll.disabled = selectableIds.size === 0;
  }
  bar.querySelectorAll("[data-bulk-action]").forEach((button) => {
    button.disabled = selectedCount === 0;
  });
}

function orderDetailPanel(order) {
  const details = productDetails(order);
  const status = orderStatus(order);
  const managementSummary = managementNumberSummary(order, state.managementDrafts[order.id] == null ? order.managementNumber : state.managementDrafts[order.id]);
  const rows = [
    ["준비 중", order.preparingBy, order.preparingAt, order.preparing],
    ["제작 완료", order.productionBy, order.productionAt, order.productionDone],
    ["SW 검수", order.softwareInspectionBy, order.softwareInspectionAt, order.softwareInspectionDone],
    ["출고 확인", order.shippingBy, order.shippingAt, order.shippingDone],
  ];
  return `
    <tr class="order-detail-row" data-id="${order.id}">
      <td colspan="8">
        <div class="order-detail-panel">
          <section class="order-detail-main">
            <div class="detail-section-heading"><h3>상품 정보</h3><span class="status-badge status-badge--${status.tone}">${status.label}</span></div>
            <strong class="detail-product-name">${escapeHtml(order.productName)}</strong>
            ${details.registeredOption ? `<p class="detail-muted">등록옵션명: ${escapeHtml(details.registeredOption)}</p>` : ""}
            ${details.extras.length ? `<div class="detail-chip-list">${details.extras.map((option) => `<span>${escapeHtml(option)}</span>`).join("")}</div>` : ""}
            <div class="detail-product-metrics">
              <span>수량 <strong>${escapeHtml(order.quantity)}개</strong></span>
              <span>관리번호 <strong>${escapeHtml(managementSummary)}</strong></span>
            </div>
          </section>
          <section>
            <h3>결제 정보</h3>
            <dl class="detail-list">
              <div><dt>결제금액</dt><dd>${formatAmount(order.amount)}원</dd></div>
              <div><dt>주문일</dt><dd>${escapeHtml(order.orderedAt || "-")}</dd></div>
              <div><dt>채널</dt><dd>${escapeHtml(order.channel || "-")}</dd></div>
            </dl>
          </section>
          <section>
            <h3>구매자 / 배송지</h3>
            <dl class="detail-list">
              <div><dt>수령인</dt><dd>${escapeHtml(order.recipient || "-")}</dd></div>
              <div><dt>연락처</dt><dd>${escapeHtml(order.phone || "-")}</dd></div>
              <div><dt>주소</dt><dd>${escapeHtml(order.address || "-")}</dd></div>
              <div><dt>메시지</dt><dd>${escapeHtml(order.deliveryMessage || "-")}</dd></div>
              <div><dt>비고</dt><dd>${escapeHtml(order.memo || "-")}</dd></div>
            </dl>
          </section>
          <section>
            <h3>처리 이력</h3>
            <div class="detail-timeline">
              ${rows.map(([label, by, at, done]) => `<div class="${done ? "is-done" : ""}"><span></span><strong>${label}</strong><small>${done ? `${escapeHtml(by || "담당자 미등록")} · ${formatDate(at)}` : "대기"}</small></div>`).join("")}
            </div>
          </section>
        </div>
      </td>
    </tr>`;
}

function syncStatusCards() {
  const selected = state.selectedStatus;
  document.querySelectorAll(".stat-card").forEach((card) => {
    const active = card.dataset.status === selected;
    card.classList.toggle("is-active", active);
    card.setAttribute("aria-pressed", String(active));
  });
}

function syncChannelFilter() {
  const preferredOrder = ["스마트스토어", "고도몰", "카카오", "쿠팡", "b2b", "b2c", "전화", "방문", "제품 교환"];
  const source = boardOrders();
  const channels = [...new Set(source.map((order) => order.channel).filter(Boolean))]
    .sort((left, right) => {
      const leftIndex = preferredOrder.indexOf(left);
      const rightIndex = preferredOrder.indexOf(right);
      if (leftIndex >= 0 || rightIndex >= 0) return (leftIndex < 0 ? 999 : leftIndex) - (rightIndex < 0 ? 999 : rightIndex);
      return left.localeCompare(right, "ko");
    });
  if (state.selectedChannel !== "all" && !channels.includes(state.selectedChannel)) state.selectedChannel = "all";
  $("#channel-filter").innerHTML = [
    { value:"all", label:"전체", count:source.length },
    ...channels.map((channel) => ({ value:channel, label:channel, count:source.filter((order) => order.channel === channel).length })),
  ].map((item) => `<button type="button" data-channel="${escapeHtml(item.value)}" class="channel-filter--${item.value === "all" ? "all" : channelTone(item.value)} ${state.selectedChannel === item.value ? "is-active" : ""}">${escapeHtml(item.label)} <strong>${item.count}</strong></button>`).join("");
}

function renderDailyOrderCalendar() {
  if (!state.calendarMonth) state.calendarMonth = currentMonthKey();
  const monthInput = $("#calendar-month");
  const calendarPanelHidden = $("#calendar-grid-panel").hidden;
  monthInput.hidden = calendarPanelHidden;
  $("#calendar-prev-month").hidden = calendarPanelHidden;
  $("#calendar-next-month").hidden = calendarPanelHidden;
  if (monthInput && monthInput.value !== state.calendarMonth) monthInput.value = state.calendarMonth;

  const counts = state.orderDateCounts;
  const [year, month] = state.calendarMonth.split("-").map(Number);
  const firstDate = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const leadingDays = firstDate.getDay();
  const monthPrefix = `${year}-${String(month).padStart(2, "0")}`;
  const monthCount = Object.entries(counts)
    .filter(([key]) => key.startsWith(monthPrefix))
    .reduce((sum, [, count]) => sum + count, 0);
  const rangeStart = state.monthlyRangeStart;
  const rangeEnd = state.monthlyRangeEnd;
  if (rangeStart) {
    $("#collection-range-from").value = rangeStart;
    $("#collection-range-to").value = rangeEnd || rangeStart;
  }
  const selectedCount = rangeStart ? Object.entries(counts)
    .filter(([key]) => key >= rangeStart && key <= (rangeEnd || rangeStart))
    .reduce((sum, [, count]) => sum + count, 0) : 0;
  const today = todayKey();

  $("#calendar-month-count").textContent = `${monthCount}건`;
  $("#calendar-selected-count").textContent = rangeStart ? `${selectedCount}건` : "미선택";
  $("#calendar-selected-label").textContent = rangeStart
    ? rangeEnd ? `${formatCalendarDateLabel(rangeStart)} ~ ${formatCalendarDateLabel(rangeEnd)}` : `${formatCalendarDateLabel(rangeStart)}부터 종료일 선택`
    : "시작일과 종료일 선택";
  $("#calendar-selected-orders").classList.toggle("is-active", Boolean(rangeStart));
  $("#calendar-clear-date").classList.toggle("is-active", !rangeStart);
  $("#calendar-workload-sync").disabled = !rangeStart;
  $("#calendar-toggle").textContent = $("#calendar-grid-panel").hidden ? "월간 달력 보기" : "월간 달력 접기";
  $("#calendar-toggle").setAttribute("aria-expanded", String(!$("#calendar-grid-panel").hidden));

  const cells = [];
  for (let index = 0; index < leadingDays; index += 1) cells.push(`<span class="calendar-day is-empty" aria-hidden="true"></span>`);
  for (let day = 1; day <= daysInMonth; day += 1) {
    const key = `${monthPrefix}-${String(day).padStart(2, "0")}`;
    const count = counts[key] || 0;
    const label = `${day}일 신규 주문 ${count}건`;
    const isRangeEdge = key === rangeStart || key === rangeEnd;
    const isInRange = rangeStart && rangeEnd && key > rangeStart && key < rangeEnd;
    cells.push(`
      <button class="calendar-day ${key === today ? "is-today" : ""} ${isRangeEdge ? "is-selected" : ""} ${isInRange ? "is-in-range" : ""} ${count ? "has-orders" : ""}" type="button" data-date="${key}" aria-label="${label}">
        <span>${day}</span>
        <strong>${count ? `${count}건` : ""}</strong>
      </button>`);
  }
  $("#daily-order-calendar").innerHTML = cells.join("");
}

function renderShippingCalendar() {
  if (!$("#shipping-calendar")) return;
  if (!state.shippingCalendarMonth) state.shippingCalendarMonth = currentMonthKey();
  const monthInput = $("#shipping-calendar-month");
  const calendarPanelHidden = $("#shipping-calendar-grid-panel").hidden;
  monthInput.hidden = calendarPanelHidden;
  $("#shipping-calendar-prev-month").hidden = calendarPanelHidden;
  $("#shipping-calendar-next-month").hidden = calendarPanelHidden;
  if (monthInput) monthInput.value = state.shippingCalendarMonth;

  const shippingOrders = [...new Map([...state.archivedOrders, ...state.asHistoryOrders].map((order) => [order.id, order])).values()];
  const counts = shippingOrders.reduce((result, order) => {
    const key = shippingDateKey(order);
    if (!key) return result;
    result[key] = (result[key] || 0) + 1;
    return result;
  }, {});
  const [year, month] = state.shippingCalendarMonth.split("-").map(Number);
  const firstDate = new Date(year, month - 1, 1);
  const daysInMonth = new Date(year, month, 0).getDate();
  const leadingDays = firstDate.getDay();
  const monthPrefix = `${year}-${String(month).padStart(2, "0")}`;
  const monthCount = Object.entries(counts)
    .filter(([key]) => key.startsWith(monthPrefix))
    .reduce((sum, [, count]) => sum + count, 0);
  const today = todayKey();
  const rangeStart = state.shippingRangeStart;
  const rangeEnd = state.shippingRangeEnd;
  const dayKey = state.selectedShippingDate || today;
  const dayCount = rangeStart ? Object.entries(counts)
    .filter(([key]) => key >= rangeStart && key <= (rangeEnd || rangeStart))
    .reduce((sum, [, count]) => sum + count, 0) : counts[dayKey] || 0;
  if (rangeStart) {
    $("#shipping-range-from").value = rangeStart;
    $("#shipping-range-to").value = rangeEnd || rangeStart;
  }

  $("#shipping-calendar-day-count").textContent = `${dayCount}건`;
  $("#shipping-calendar-day-label").textContent = rangeStart ? "선택 기간 출고" : state.selectedShippingDate ? `${formatCalendarDateLabel(state.selectedShippingDate)} 출고` : "오늘 출고";
  $("#shipping-calendar-month-count").textContent = `${monthCount}건`;
  $("#shipping-calendar-selected-label").textContent = rangeStart ? `${formatCalendarDateLabel(rangeStart)} ~ ${formatCalendarDateLabel(rangeEnd || rangeStart)}` : state.selectedShippingDate ? formatCalendarDateLabel(state.selectedShippingDate) : "전체 날짜";
  $("#shipping-calendar-day").classList.toggle("is-active", Boolean(rangeStart || state.selectedShippingDate));
  $("#shipping-calendar-selected").classList.toggle("is-active", Boolean(rangeStart || state.selectedShippingDate));
  $("#shipping-calendar-clear-date").classList.toggle("is-active", !rangeStart && !state.selectedShippingDate);
  $("#shipping-workload-sync").disabled = !rangeStart && !state.selectedShippingDate;
  $("#shipping-calendar-toggle").textContent = $("#shipping-calendar-grid-panel").hidden ? "월간 달력 보기" : "월간 달력 접기";
  $("#shipping-calendar-toggle").setAttribute("aria-expanded", String(!$("#shipping-calendar-grid-panel").hidden));

  const cells = [];
  for (let index = 0; index < leadingDays; index += 1) cells.push(`<span class="calendar-day is-empty" aria-hidden="true"></span>`);
  for (let day = 1; day <= daysInMonth; day += 1) {
    const key = `${monthPrefix}-${String(day).padStart(2, "0")}`;
    const count = counts[key] || 0;
    const label = `${day}일 출고 ${count}건`;
    const isRangeEdge = key === rangeStart || key === rangeEnd || (!rangeStart && key === state.selectedShippingDate);
    const isInRange = rangeStart && rangeEnd && key > rangeStart && key < rangeEnd;
    cells.push(`
      <button class="calendar-day ${key === today ? "is-today" : ""} ${isRangeEdge ? "is-selected" : ""} ${isInRange ? "is-in-range" : ""} ${count ? "has-orders" : ""}" type="button" data-date="${key}" aria-label="${label}">
        <span>${day}</span>
        <strong>${count ? `${count}건` : ""}</strong>
      </button>`);
  }
  $("#shipping-calendar").innerHTML = cells.join("");
  renderShippingDateOrders();
}

function renderShippingDateOrders() {
  const panel = $("#shipping-date-orders-panel");
  const body = $("#shipping-date-orders-body");
  if (!panel || !body) return;
  const rangeStart = state.shippingRangeStart || state.selectedShippingDate;
  const rangeEnd = state.shippingRangeEnd || rangeStart;
  const source = [...new Map([...state.archivedOrders, ...state.asHistoryOrders].map((order) => [order.id, order])).values()];
  const visible = rangeStart
    ? source.filter((order) => {
      const dateKey = shippingDateKey(order);
      return dateKey && dateKey >= rangeStart && dateKey <= rangeEnd;
    }).sort(compareOrders)
    : [];
  panel.hidden = !rangeStart;
  $("#shipping-date-orders-count").textContent = `${visible.length}건 표시`;
  $("#shipping-date-mode-label").textContent = rangeStart
    ? rangeEnd && rangeEnd !== rangeStart
      ? `${formatCalendarDateLabel(rangeStart)} ~ ${formatCalendarDateLabel(rangeEnd)}`
      : formatCalendarDateLabel(rangeStart)
    : "선택 기간";
  $("#shipping-date-mode-count").textContent = `${visible.length}건`;
  $("#shipping-date-orders-empty").hidden = visible.length > 0;
  body.innerHTML = visible.map((order, index) => {
    const memo = order.memo ? `<span class="meta order-memo">비고: ${escapeHtml(order.memo)}</span>` : "";
    return `
      <tr>
        <td>${index + 1}</td>
        <td><span class="channel channel--${channelTone(order.channel)}">${escapeHtml(order.channel)}</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span></td>
        <td>${renderProductInfo(order, { amountText: `${formatAmount(order.amount)}원` })}</td>
        <td><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div><span class="meta">${escapeHtml(order.deliveryMessage)}</span>${memo}</td>
        <td><strong>${escapeHtml(order.productionBy || "-")}</strong><span class="meta">${formatDate(order.productionAt)}</span><span class="meta">관리번호 ${escapeHtml(order.managementNumber || "미등록")}</span></td>
        <td><strong>${escapeHtml(order.shippingBy || "-")}</strong><span class="meta">${formatDate(order.shippingAt)}</span></td>
      </tr>`;
  }).join("");
}

function clearShippingDateSelection() {
  state.selectedShippingDate = "";
  state.shippingRangeStart = "";
  state.shippingRangeEnd = "";
  renderShippingCalendar();
}

function clearCollectionDateSelection() {
  state.monthlySelectedCalendarDate = "";
  state.monthlyRangeStart = "";
  state.monthlyRangeEnd = "";
  renderMonthlyDateOrders();
  renderDailyOrderCalendar();
}

function renderMonthlyDateOrders() {
  const rangeStart = state.monthlyRangeStart;
  const rangeEnd = state.monthlyRangeEnd;
  const rangeKey = rangeStart ? `${rangeStart}:${rangeEnd || rangeStart}` : "";
  const source = rangeKey && state.monthlyCalendarOrdersDate === rangeKey ? state.monthlyCalendarOrders : [];
  const workerQuery = state.monthlyProductionWorkerQuery.trim().toLowerCase();
  const visible = source.filter((order) => !workerQuery || (order.productionDone && String(order.productionBy || "").toLowerCase().includes(workerQuery)));
  const panel = $("#monthly-date-orders-panel");
  const body = $("#monthly-orders-body");
  if (!panel || !body) return;
  panel.hidden = !rangeStart;
  $("#monthly-empty").hidden = !rangeStart || visible.length > 0;
  $("#monthly-visible-count").textContent = `${visible.length}건 표시`;
  const banner = $("#monthly-date-mode-banner");
  banner.hidden = !rangeStart;
  $("#monthly-date-mode-label").textContent = rangeEnd ? `${formatCalendarDateLabel(rangeStart)} ~ ${formatCalendarDateLabel(rangeEnd)}` : formatCalendarDateLabel(rangeStart);
  $("#monthly-date-mode-count").textContent = `${source.length}건`;
  const completedByWorker = visible.filter((order) => order.productionDone).length;
  const monthlyWorkerSearchCount = $("#monthly-worker-search-count");
  if (monthlyWorkerSearchCount) monthlyWorkerSearchCount.textContent = workerQuery ? `${completedByWorker}건 완료` : `제작완료 ${completedByWorker}건`;
  const monthlyEmpty = $("#monthly-empty");
  if (monthlyEmpty) {
    monthlyEmpty.querySelector("strong").textContent = workerQuery ? "검색 조건에 맞는 주문이 없습니다" : "선택한 날짜의 주문이 없습니다";
    monthlyEmpty.querySelector("span").textContent = workerQuery ? "작업자 검색어를 변경하세요." : "월간 현황에서 주문 수집 날짜를 선택하세요.";
  }
  body.innerHTML = visible.map((order, index) => {
    const details = productDetails(order);
    const status = orderStatus(order);
    const channelClass = channelTone(order.channel);
    const rowClasses = [
      order.preparing ? "is-preparing" : "",
      order.productionDone ? "is-produced" : "",
      isArchivedOrCancelled(order) ? "is-readonly" : "",
      order.archivedAt ? "is-archived" : "",
      order.cancelledAt ? "is-cancelled" : "",
    ].filter(Boolean).join(" ");
    const shippingLabel = order.cancelledAt ? "취소 처리" : "출고 확인";
    const shippingChecked = order.cancelledAt || order.shippingDone;
    const shippingNote = order.cancelledAt
      ? `${escapeHtml(order.cancelledBy || "담당자 미등록")} · ${formatDate(order.cancelledAt)}`
      : order.shippingDone
        ? `${escapeHtml(order.shippingBy)} · ${formatDate(order.shippingAt)}${order.archivedAt ? " · 엑셀 출력" : ""}`
        : order.softwareInspectionDone
          ? "출고 전"
          : "SW 검수 후 출고";
    const memo = order.memo ? `<div class="order-memo">비고: ${escapeHtml(order.memo)}</div>` : "";
    const deliveryMessage = String(order.deliveryMessage || "").trim();
    const deliveryMessageBlock = deliveryMessage ? `<div class="delivery-message"><span>배송메시지</span><strong>${escapeHtml(deliveryMessage)}</strong></div>` : "";
    return `
      <tr data-id="${order.id}" class="${rowClasses}">
        <td class="col-channel" data-label="채널 / 주문"><div class="order-row-head"><div class="order-sequence">${index + 1}</div></div><div class="order-badges"><span class="channel channel--${channelClass}">${escapeHtml(order.channel)}</span><span class="status-badge status-badge--${status.tone}">${status.label}</span></div><span class="order-number-label">주문번호</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span></td>
        <td class="col-product" data-label="상품 정보">${renderProductInfo(order, { memo })}</td>
        <td class="col-address" data-label="배송지"><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div>${deliveryMessageBlock}</td>
        <td class="col-preparing" data-label="준비 중"><div class="check-card"><input type="checkbox" ${order.preparing ? "checked" : ""} disabled><label>준비 중</label><small>${order.preparing ? `${escapeHtml(order.preparingBy)} · ${formatDate(order.preparingAt)}` : "작업자 미지정"}</small></div></td>
        <td class="col-management" data-label="제품 관리번호"><div class="management-field"><div class="management-field__head"><span class="management-field__title">관리번호</span><span class="management-field__hint">조회 전용</span></div><textarea class="management-number" rows="5" disabled>${escapeHtml(order.managementNumber || "")}</textarea><small>${order.managementNumberBy ? `${escapeHtml(order.managementNumberBy)} · ${formatDate(order.managementNumberAt)} · ${managementNumberSummary(order, order.managementNumber)}` : managementNumberSummary(order, order.managementNumber)}</small></div></td>
        <td class="col-production" data-label="제작 완료"><div class="check-card"><input type="checkbox" ${order.productionDone ? "checked" : ""} disabled><label>제작 완료</label><small>${order.productionDone ? `${escapeHtml(order.productionBy)} · ${formatDate(order.productionAt)}` : "담당자 미지정"}</small></div></td>
        <td class="col-inspection" data-label="SW 검수"><div class="check-card"><input type="checkbox" ${order.softwareInspectionDone ? "checked" : ""} disabled><label>SW 검수</label><small>${order.softwareInspectionDone ? `${escapeHtml(order.softwareInspectionBy)} · ${formatDate(order.softwareInspectionAt)}` : "검수 전"}</small></div></td>
        <td class="col-shipping" data-label="출고 처리"><div class="check-card"><input type="checkbox" ${shippingChecked ? "checked" : ""} disabled><label>${shippingLabel}</label><small>${shippingNote}</small></div></td>
      </tr>
    `;
  }).join("");
}

function render() {
  const managementInputState = captureOrderInputState();
  const canEditNow = canEditOrders();
  const canCancelNow = cancelOrderRoles.has(currentRole());
  // 주문 목록은 현재 필터 상태를 기준으로 매번 다시 그린다.
  syncStatusCards();
  syncOrderColumnVisibility();
  renderDailyOrderCalendar();
  const source = boardOrders();
  const filtered = filteredOrders().sort(compareOrdersBySelectedSort);
  const visible = filtered;
  const statusCounts = source.reduce((counts, order) => {
    const key = orderStatusKey(order);
    if (counts[key] != null) counts[key] += 1;
    return counts;
  }, { waiting: 0, preparing: 0, produced: 0, softwareInspection: 0, shipped: 0 });
  $("#total-count").textContent = source.length;
  $("#waiting-count").textContent = statusCounts.waiting;
  $("#preparing-count").textContent = statusCounts.preparing;
  $("#produced-count").textContent = statusCounts.produced;
  $("#inspection-count").textContent = statusCounts.softwareInspection;
  $("#shipped-count").textContent = statusCounts.shipped;
  $("#visible-count").textContent = `${visible.length}건 표시`;
  document.body.classList.toggle("is-calendar-date-mode", Boolean(state.selectedCalendarDate));
  const dateModeBanner = $("#date-mode-banner");
  if (dateModeBanner) {
    dateModeBanner.hidden = !state.selectedCalendarDate;
    $("#date-mode-label").textContent = state.selectedCalendarDate ? formatCalendarDateLabel(state.selectedCalendarDate) : "선택 날짜";
    $("#date-mode-count").textContent = `${source.length}건`;
  }
  $("#empty").hidden = visible.length > 0;
  syncBulkActionBar(visible);
  const orderRowsHtml = visible.map((order, index) => {
    const details = productDetails(order);
    const status = orderStatus(order);
    const channelClass = channelTone(order.channel);
    const readOnly = isArchivedOrCancelled(order);
    const disabled = readOnly ? "disabled" : "";
    const memo = order.memo ? `<div class="order-memo">비고: ${escapeHtml(order.memo)}</div>` : "";
    const deliveryMessage = String(order.deliveryMessage || "").trim();
    const deliveryMessageBlock = deliveryMessage ? `<div class="delivery-message"><span>배송메시지</span><strong>${escapeHtml(deliveryMessage)}</strong></div>` : "";
    const shippingLabel = order.cancelledAt ? "취소 처리" : "출고 확인";
    const shippingChecked = order.cancelledAt || order.shippingDone;
    const shippingNote = order.cancelledAt
      ? `${escapeHtml(order.cancelledBy || "담당자 미등록")} · ${formatDate(order.cancelledAt)}`
      : order.shippingDone
        ? `${escapeHtml(order.shippingBy)} · ${formatDate(order.shippingAt)}${order.archivedAt ? " · 엑셀 출력" : ""}`
        : order.softwareInspectionDone
          ? "출고 전"
          : "SW 검수 후 출고";
    const rowClasses = [
      order.preparing ? "is-preparing" : "",
      order.productionDone ? "is-produced" : "",
      readOnly ? "is-readonly" : "",
      order.archivedAt ? "is-archived" : "",
      order.cancelledAt ? "is-cancelled" : "",
    ].filter(Boolean).join(" ");
    const row = `
    <tr data-id="${order.id}" class="${rowClasses}">
      <td class="col-channel" data-label="채널 / 주문"><div class="order-row-head">${canEditNow && isBulkSelectableOrder(order) ? `<input class="order-select" type="checkbox" aria-label="${escapeHtml(order.orderNumber)} 선택" ${state.selectedOrderIds.has(order.id) ? "checked" : ""}>` : ""}<div class="order-sequence">${index + 1}</div></div><div class="order-badges"><span class="channel channel--${channelClass}">${escapeHtml(order.channel)}</span><span class="status-badge status-badge--${status.tone}">${status.label}</span></div><span class="order-number-label">주문번호</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span><div class="order-actions"><button class="row-detail-toggle" type="button">${state.expandedOrderId === order.id ? "상세 닫기" : "상세 보기"}</button>${canEditNow && !order.shippingDone && !readOnly ? `<button class="edit-order" type="button">주문 수정</button>` : ""}${canCancelNow && !readOnly ? `<button class="cancel-order" type="button">주문 취소</button>` : ""}</div></td>
      <td class="col-product" data-label="상품 정보">${renderProductInfo(order, { memo })}</td>
      <td class="col-address" data-label="배송지"><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div>${deliveryMessageBlock}${shippingUpdatePanel(order)}</td>
      <td class="col-preparing" data-label="준비 중"><div class="check-card"><input class="preparing-check" type="checkbox" ${order.preparing ? "checked" : ""} ${disabled}><label>준비 중</label><small>${order.preparing ? `${escapeHtml(order.preparingBy)} · ${formatDate(order.preparingAt)}` : "작업자 미지정"}</small></div></td>
      <td class="col-management" data-label="제품 관리번호"><div class="management-field"><div class="management-field__head"><span class="management-field__title">관리번호</span><span class="management-field__hint">한 줄에 1개씩</span></div><textarea class="management-number" rows="5" placeholder="ABC001&#10;ABC002&#10;ABC003" ${disabled}>${escapeHtml(state.managementDrafts[order.id] == null ? order.managementNumber : state.managementDrafts[order.id])}</textarea><div class="management-field__actions">${readOnly ? "" : `<button class="save-management" type="button">저장</button>`}</div><small>${order.managementNumberBy ? `${escapeHtml(order.managementNumberBy)} · ${formatDate(order.managementNumberAt)} · ${managementNumberSummary(order, order.managementNumber)}` : managementNumberSummary(order, state.managementDrafts[order.id] == null ? order.managementNumber : state.managementDrafts[order.id])}</small></div></td>
      <td class="col-production" data-label="제작 완료"><div class="check-card"><input class="production-check" type="checkbox" ${order.productionDone ? "checked" : ""} ${disabled}><label>제작 완료</label><small>${order.productionDone ? `${escapeHtml(order.productionBy)} · ${formatDate(order.productionAt)}` : "담당자 미지정"}</small></div></td>
      <td class="col-inspection" data-label="SW 검수"><div class="check-card"><input class="software-inspection-check" type="checkbox" ${order.softwareInspectionDone ? "checked" : ""} ${disabled}><label>SW 검수</label><small>${order.softwareInspectionDone ? `${escapeHtml(order.softwareInspectionBy)} · ${formatDate(order.softwareInspectionAt)}` : "검수 전"}</small></div></td>
      <td class="col-shipping" data-label="출고 처리"><div class="check-card"><input class="shipping-check" type="checkbox" ${shippingChecked ? "checked" : ""} ${disabled}><label>${shippingLabel}</label><small>${shippingNote}</small></div></td>
    </tr>`;
    return state.expandedOrderId === order.id ? row + orderDetailPanel(order) : row;
  }).join("");
  $("#orders-body").innerHTML = orderRowsHtml;
  renderMonthlyDateOrders();
  restoreOrderInputState(managementInputState);
}

async function loadOrders() {
  // 메인 주문 목록은 서버 기준 상태를 그대로 다시 가져온다.
  // 월간 통계 API는 owner 전용이므로 권한이 있을 때만 요청한다.
  const statsRequest = currentRole() === "owner" ? fetch("/api/orders/daily-stats").catch(() => null) : Promise.resolve(null);
  const [ordersResponse, statsResult] = await Promise.all([OrderApi.get("/api/orders"), statsRequest]);
  if (!ordersResponse.ok) throw new Error("주문을 불러오지 못했습니다.");
  state.orders = await ordersResponse.json();
  if (statsResult && statsResult.ok) {
    state.orderDateCounts = Object.fromEntries((await statsResult.json()).map((item) => [item.date, item.count]));
  } else {
    state.orderDateCounts = state.orders.reduce((result, order) => {
      const key = orderCollectionDateKey(order);
      if (key) result[key] = (result[key] || 0) + 1;
      return result;
    }, {});
  }
  syncChannelFilter();
  render();
}

async function loadMonthlyCalendarDateOrders(dateFrom, dateTo = dateFrom) {
  if (!dateFrom) {
    state.monthlyCalendarOrders = [];
    state.monthlyCalendarOrdersDate = "";
    render();
    return;
  }
  const response = await fetch(`/api/orders/by-date?from=${encodeURIComponent(dateFrom)}&to=${encodeURIComponent(dateTo)}`);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "선택 날짜의 주문을 불러오지 못했습니다.");
  state.monthlyCalendarOrders = result;
  state.monthlyCalendarOrdersDate = `${dateFrom}:${dateTo}`;
  render();
}

async function loadCalendarDateOrders(dateKey) {
  if (!dateKey) {
    state.calendarOrders = [];
    state.calendarOrdersDate = "";
    syncChannelFilter();
    render();
    return;
  }
  const response = await fetch(`/api/orders/by-date?date=${encodeURIComponent(dateKey)}`);
  const result = await response.json();
  if (!response.ok) throw new Error(result.error || "선택 날짜의 주문을 불러오지 못했습니다.");
  state.calendarOrders = result;
  state.calendarOrdersDate = dateKey;
  syncChannelFilter();
  render();
}

function isEditingOrderInput() {
  const active = document.activeElement;
  if (!active) return false;
  if (active.closest && active.closest("#manual-order-form")) return true;
  if (!active.closest || (!active.closest("#orders-body") && !active.closest("#monthly-orders-body"))) return false;
  if (active.tagName === "TEXTAREA" || active.tagName === "SELECT") return true;
  return active.tagName === "INPUT" && !["checkbox", "radio", "button", "submit"].includes(active.type);
}

async function refreshVisibleData({ force = false } = {}) {
  if (!state.user) return;
  const editing = isEditingOrderInput();
  const tasks = [];
  if (!editing || force) {
    tasks.push(loadOrders().catch(() => {}));
    if (!$("#shipping-customers-view").hidden) {
      tasks.push(loadArchivedOrders().catch(() => {}));
      if (asHistoryRoles.has(currentRole())) {
        tasks.push(loadAsHistory().catch(() => {}));
      }
    }
    if (!$("#monthly-stats-view").hidden && asHistoryRoles.has(currentRole())) {
      tasks.push(loadArchivedOrders().catch(() => {}));
      tasks.push(loadAsHistory().catch(() => {}));
    }
    if (!$("#cancelled-view").hidden) {
      tasks.push(loadCancelledOrders().catch(() => {}));
    }
    if (auditViewRoles.has(currentRole())) {
      tasks.push(loadAuditLogs().catch(() => {}));
    }
  }
  tasks.push(pollNotifications().catch(() => {}));
  await Promise.all(tasks);
}

function startOrderAutoRefresh() {
  window.setInterval(() => {
    if (!state.user || isEditingOrderInput()) return;
    refreshVisibleData().catch(() => {});
  }, 5000);
}

function syncArchivedSectionState() {
  const body = $("#archived-section-body");
  const toggle = $("#archived-toggle");
  if (!body || !toggle) return;
  const expanded = body.hidden !== true;
  toggle.textContent = expanded ? "접기" : "펼치기";
  toggle.setAttribute("aria-expanded", String(expanded));
}

function renderArchivedOrders() {
  const query = $("#archived-search").value.trim().toLowerCase();
  const selectedChannel = $("#archived-channel").value;
  const visible = state.archivedOrders
    .filter((order) => selectedChannel === "all" || order.channel === selectedChannel)
    .filter((order) => !query || [order.orderNumber, order.productName, order.optionName, order.recipient, order.phone, order.channel, order.productCode, order.managementNumber, order.memo].join(" ").toLowerCase().includes(query))
    .sort(compareOrders);
  $("#archived-count").textContent = `${visible.length}건`;
  $("#archived-empty").hidden = visible.length > 0;
  $("#archived-orders-body").innerHTML = visible.map((order, index) => {
    const details = productDetails(order);
    const memo = order.memo ? `<span class="meta order-memo">비고: ${escapeHtml(order.memo)}</span>` : "";
    const productSummary = `${formatAmount(order.amount)}원`;
    return `
      <tr>
        <td>${index + 1}</td>
        <td><span class="channel channel--${channelTone(order.channel)}">${escapeHtml(order.channel)}</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span></td>
        <td>${renderProductInfo(order, { amountText: productSummary })}</td>
        <td><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div><span class="meta">${escapeHtml(order.deliveryMessage)}</span>${memo}</td>
        <td><strong>${escapeHtml(order.productionBy)}</strong><span class="meta">${formatDate(order.productionAt)}</span><span class="meta">관리번호 ${escapeHtml(order.managementNumber || "미등록")}</span></td>
        <td><strong>${escapeHtml(order.shippingBy)}</strong><span class="meta">${formatDate(order.shippingAt)}</span></td>
      </tr>
    `;
  }).join("");
}

async function loadArchivedOrders() {
  // 출고 고객 조회용 아카이브 목록은 별도 엔드포인트로 가져온다.
  const response = await OrderApi.get("/api/orders/archived");
  if (!response.ok) throw new Error("완료 주문을 불러오지 못했습니다.");
  state.archivedOrders = await response.json();
  const channelSelect = $("#archived-channel");
  const selected = channelSelect.value;
  const channels = [...new Set(state.archivedOrders.map((order) => String(order.channel || "").trim()).filter(Boolean))].sort();
  channelSelect.innerHTML = `<option value="all">전체 마켓</option>${channels.map((channel) => `<option value="${escapeHtml(channel)}">${escapeHtml(channel)}</option>`).join("")}`;
  channelSelect.value = channels.includes(selected) ? selected : "all";
  renderArchivedOrders();
  renderShippingCalendar();
  syncArchivedSectionState();
}

function renderCancelledOrders() {
  const query = $("#cancelled-search").value.trim().toLowerCase();
  const visible = state.cancelledOrders
    .filter((order) => !query || [order.orderNumber, order.productName, order.optionName, order.recipient, order.channel, order.productCode, order.cancelReason, order.memo].join(" ").toLowerCase().includes(query))
    .sort(compareOrders);
  $("#cancelled-count").textContent = `${visible.length}건`;
  $("#cancelled-empty").hidden = visible.length > 0;
  $("#cancelled-orders-body").innerHTML = visible.map((order, index) => {
    const memo = order.memo ? `<span class="meta order-memo">비고: ${escapeHtml(order.memo)}</span>` : "";
    const restoreButton = `<button class="restore-order" type="button">주문 복구</button>`;
    return `<tr data-id="${escapeHtml(order.id)}">
    <td>${index + 1}</td>
    <td><span class="channel channel--${channelTone(order.channel)}">${escapeHtml(order.channel)}</span><strong class="order-number-value">${escapeHtml(order.orderNumber)}</strong><span class="order-no">${escapeHtml(order.orderedAt)}</span></td>
    <td><div class="product-info"><div class="product">${escapeHtml(order.productName)}</div><div class="meta">${escapeHtml(order.optionName)}</div><div class="product-summary"><span>수량 <strong>${order.quantity}개</strong></span><span>결제금액 <strong>${formatAmount(order.amount)}원</strong></span></div></div></td>
    <td><strong>${escapeHtml(order.recipient)}</strong> · ${escapeHtml(order.phone)}<div class="address">${escapeHtml(order.address)}</div>${memo}</td>
    <td><strong>${escapeHtml(order.cancelledBy)}</strong><span class="meta">${formatDate(order.cancelledAt)}</span></td>
    <td>${escapeHtml(order.cancelReason)}${restoreButton}</td>
  </tr>`;
  }).join("");
}

async function loadCancelledOrders() {
  // 취소 목록은 취소 사유까지 포함해 별도로 읽어온다.
  const response = await OrderApi.get("/api/orders/cancelled");
  if (!response.ok) throw new Error("취소 주문을 불러오지 못했습니다.");
  state.cancelledOrders = await response.json();
  renderCancelledOrders();
}

function renderAsHistory() {
  const query = $("#as-history-search").value.trim().toLowerCase();
  if (!query) {
    $("#as-history-count").textContent = "고객을 검색하세요";
    $("#as-history-empty strong").textContent = "고객 이름을 검색하세요";
    $("#as-history-empty span").textContent = "해당 고객에게 출고된 모든 제품과 배송 이력을 확인할 수 있습니다.";
    $("#as-history-empty").hidden = false;
    $("#as-history-body").innerHTML = "";
    return;
  }
  const matches = state.asHistoryOrders.filter((order) => [order.recipient, order.phone].join(" ").toLowerCase().includes(query));
  const customers = new Map();
  for (const order of matches) {
    const key = `${String(order.recipient || "").trim()}|${String(order.phone || "").replace(/\D/g, "")}`;
    if (!customers.has(key)) customers.set(key, []);
    customers.get(key).push(order);
  }
  $("#as-history-count").textContent = `${customers.size}명 · 출고 ${matches.length}건`;
  $("#as-history-empty strong").textContent = "검색 결과가 없습니다";
    $("#as-history-empty span").textContent = "고객 이름을 다시 확인하세요.";
  $("#as-history-empty").hidden = customers.size > 0;
  $("#as-history-body").innerHTML = [...customers.values()].map((orders) => {
    orders.sort((left, right) => String(left.shippingAt || "").localeCompare(String(right.shippingAt || "")));
    const customer = orders[orders.length - 1];
    return `<article class="customer-card">
      <header class="customer-card-header"><div><h3>${escapeHtml(customer.recipient || "이름 미등록")}</h3><p>${escapeHtml(customer.phone || "연락처 미등록")} · ${escapeHtml(customer.address || "주소 미등록")}</p></div><strong>총 출고 ${orders.length}건</strong></header>
      <div class="customer-shipments">${orders.map((order) => `<div class="customer-shipment">
        <div><span class="customer-shipment-label">주문 / 출고</span><strong>${escapeHtml(order.orderNumber)}</strong><span class="meta">주문 ${escapeHtml(order.orderedAt)}</span><span class="meta">출고 ${formatDate(order.shippingAt)}</span></div>
        <div><span class="customer-shipment-label">출고 제품</span><strong class="product">${escapeHtml(order.productName)}</strong><span class="meta">${escapeHtml(order.optionName)}</span></div>
        <div><span class="customer-shipment-label">제품 식별</span><span class="management-badge">관리번호 ${escapeHtml(order.managementNumber || managementNumberSummary(order, order.managementNumber) || "미등록")}</span><span class="meta">상품코드 ${escapeHtml(order.productCode || "-")}</span></div>
        <div><span class="customer-shipment-label">출고 처리</span><strong>${escapeHtml(order.shippingBy || "담당자 미등록")}</strong><span class="meta">${formatDate(order.shippingAt)}</span></div>
      </div>`).join("")}</div>
    </article>`;
  }).join("");
}

async function loadAsHistory() {
  // AS 이력은 고객 단위로 묶어 보여주기 위해 별도 목록을 유지한다.
  const response = await OrderApi.get("/api/orders/as-history");
  if (!response.ok) throw new Error("고객 출고 이력을 불러오지 못했습니다.");
  state.asHistoryOrders = await response.json();
  renderShippingCalendar();
  renderAsHistory();
}

function auditEventLabel(event) {
  return {
    login_failed: "로그인 실패",
    login_succeeded: "로그인 성공",
    orders_imported: "주문 가져오기",
    order_created: "주문 등록",
    order_updated: "주문 수정",
    order_cancelled: "주문 취소",
    order_restored: "주문 복구",
    shipping_update_applied: "배송지 반영",
    orders_exported: "출고 엑셀 생성",
  }[event] || event || "-";
}

function notificationKey(item) {
  return [item.timestamp, item.event, item.orderNumber, item.action, item.displayName].join("|");
}

function notificationFingerprint(item) {
  return [item.event, item.orderNumber, item.action, item.checked, item.displayName].join("|");
}

function notificationText(item) {
  const user = item.displayName || "작업자";
  const order = item.orderNumber ? ` · ${item.orderNumber}` : "";
  if (item.event === "order_created") return { title:"수기 주문 등록", body:`${user}님이 수기 주문을 등록했습니다${order}` };
  if (item.event === "order_cancelled") return { title:"주문 취소", body:`${user}님이 주문을 취소했습니다${order}` };
  if (item.event === "order_restored") return { title:"주문 복구", body:`${user}님이 주문을 복구했습니다${order}` };
  if (item.event === "order_updated") {
    const action = {
      details: "주문 정보를 수정했습니다",
      preparing: item.checked ? "준비 중으로 변경했습니다" : "준비 중을 해제했습니다",
      production: item.checked ? "제작 완료로 변경했습니다" : "제작 완료를 해제했습니다",
      softwareInspection: item.checked ? "SW 검수 완료로 변경했습니다" : "SW 검수를 해제했습니다",
      shipping: item.checked ? "출고 확인 처리했습니다" : "출고 확인을 해제했습니다",
      managementNumber: "제품 관리번호를 수정했습니다",
      applyShippingUpdate: "배송지 변경을 반영했습니다",
    }[item.action] || "주문을 수정했습니다";
    return { title:"주문 수정", body:`${user}님이 ${action}${order}` };
  }
  return { title:"업무 알림", body:`${user}님이 작업을 처리했습니다${order}` };
}

function syncNotificationSurfaceStyles() {
  const popover = $("#notification-popover");
  if (!popover) return;
  const isDark = document.body.dataset.theme === "dark";
  const popoverBackground = isDark ? "#0b0d10" : "#ffffff";
  const headerBackground = isDark ? "#11151b" : "#f8fafc";
  popover.style.setProperty("backdrop-filter", "none", "important");
  popover.style.setProperty("background-color", popoverBackground, "important");
  popover.style.setProperty("background-image", "none", "important");
  popover.style.setProperty("opacity", "1", "important");
  popover.style.setProperty("filter", "none", "important");
  popover.style.setProperty("mix-blend-mode", "normal", "important");
  popover.style.setProperty("border-color", isDark ? "#2a2f39" : "rgba(34,56,46,.10)", "important");
  popover.style.setProperty("isolation", "isolate", "important");
  popover.style.setProperty("box-shadow", isDark ? "0 24px 60px rgba(0,0,0,.72)" : "0 18px 42px rgba(15, 23, 42, .22)", "important");
  const header = popover.querySelector("header");
  if (header) header.style.setProperty("background-color", headerBackground, "important");
}

function pushNotification(item) {
  const stack = $("#notification-stack");
  if (!stack) return;
  const message = notificationText(item);
  const toast = document.createElement("article");
  toast.className = `notification-toast notification-toast--${escapeHtml(item.event || "info")}`;
  toast.innerHTML = `
    <div>
      <strong>${escapeHtml(message.title)}</strong>
      <span>${escapeHtml(message.body)}</span>
      <small>${formatDate(item.timestamp)}</small>
    </div>
    <button type="button" aria-label="알림 닫기">×</button>`;
  toast.querySelector("button").addEventListener("click", () => toast.remove());
  stack.prepend(toast);
  while (stack.children.length > 4) stack.lastElementChild.remove();
  window.setTimeout(() => toast.remove(), 7000);
}

function renderNotificationCenter() {
  const badge = $("#notification-badge");
  const list = $("#notification-list");
  const empty = $("#notification-empty");
  const count = state.unreadNotifications;
  badge.textContent = count > 99 ? "99+" : String(count);
  badge.hidden = count === 0;
  empty.hidden = state.notifications.length > 0;
  list.innerHTML = state.notifications.map((item) => {
    const message = notificationText(item);
    return `<article class="notification-item notification-item--${escapeHtml(item.event || "info")}">
      <strong>${escapeHtml(message.title)}</strong>
      <span>${escapeHtml(message.body)}</span>
      <small>${formatDate(item.timestamp)}</small>
    </article>`;
  }).join("");
}

function addNotification(item, { toast = true, unread = true } = {}) {
  const notification = {
    timestamp: item.timestamp || new Date().toISOString(),
    userId: item.userId || state.user && state.user.id || "",
    displayName: item.displayName || worker(),
    checked: item.checked == null ? "" : item.checked,
    ...item,
  };
  const key = notificationKey(notification);
  if (state.seenNotificationKeys.has(key)) return;
  state.seenNotificationKeys.add(key);
  const fingerprint = notificationFingerprint(notification);
  state.recentNotificationFingerprints.set(fingerprint, Date.now());
  state.notifications.unshift(notification);
  state.notifications = state.notifications.slice(0, 50);
  if (unread) state.unreadNotifications += 1;
  if (toast) pushNotification(notification);
  renderNotificationCenter();
}

function markNotificationsRead() {
  state.unreadNotifications = 0;
  state.notifications = [];
  renderNotificationCenter();
}

function markNotificationBadgeRead() {
  state.unreadNotifications = 0;
  renderNotificationCenter();
}

function toggleNotificationPopover(forceOpen) {
  const popover = $("#notification-popover");
  const button = $("#notification-button");
  const open = forceOpen == null ? popover.hidden : forceOpen;
  popover.hidden = !open;
  button.setAttribute("aria-expanded", String(open));
  if (open) {
    syncNotificationSurfaceStyles();
    markNotificationBadgeRead();
  }
}

async function pollNotifications({ prime = false } = {}) {
  if (!state.user) return;
  try {
    const response = await OrderApi.get("/api/notifications");
    if (!response.ok) return;
    const items = await response.json();
    const incoming = [...items].reverse();
    for (const item of incoming) {
      const key = notificationKey(item);
      if (state.seenNotificationKeys.has(key)) continue;
      const fingerprint = notificationFingerprint(item);
      const recentAt = state.recentNotificationFingerprints.get(fingerprint);
      if (recentAt && Date.now() - recentAt < 30000) {
        state.seenNotificationKeys.add(key);
        continue;
      }
      addNotification(item, { toast: !prime && state.notificationsReady, unread: !prime && state.notificationsReady });
    }
    renderNotificationCenter();
    state.notificationsReady = true;
  } catch {
    // 알림 조회 실패는 주요 업무 흐름을 막지 않는다.
  }
}

function startNotifications() {
  stopNotifications();
  state.notificationsReady = false;
  state.seenNotificationKeys.clear();
  state.recentNotificationFingerprints.clear();
  state.notifications = [];
  state.unreadNotifications = 0;
  renderNotificationCenter();
  pollNotifications({ prime: true });
  state.notificationTimer = window.setInterval(() => pollNotifications(), 10000);
}

function stopNotifications() {
  if (state.notificationTimer) window.clearInterval(state.notificationTimer);
  state.notificationTimer = null;
}

function auditDetail(record) {
  const hidden = new Set(["timestamp", "event", "userId", "displayName", "orderNumber"]);
  return Object.entries(record)
    .filter(([key, value]) => !hidden.has(key) && value !== "" && value !== null && value !== undefined)
    .map(([key, value]) => {
      const text = typeof value === "object" ? JSON.stringify(value) : String(value);
      return `<span><strong>${escapeHtml(key)}</strong>${escapeHtml(text)}</span>`;
    }).join("");
}

function renderAuditLogs() {
  const search = $("#audit-search").value.trim().toLowerCase();
  const visible = state.auditLogs.filter((record) => {
    if (!search) return true;
    return [
      record.timestamp,
      record.displayName,
      record.event,
      auditEventLabel(record.event),
      record.orderNumber,
      record.orderId,
      JSON.stringify(record),
    ].join(" ").toLowerCase().includes(search);
  });
  $("#audit-count").textContent = `${visible.length}건`;
  $("#audit-empty").hidden = visible.length > 0;
  $("#audit-body").innerHTML = visible.map((record) => `
    <tr>
      <td>${formatDate(record.timestamp)}</td>
      <td><strong>${escapeHtml(record.displayName || "-")}</strong><span class="meta">${escapeHtml(record.userId || "")}</span></td>
      <td><span class="audit-event">${escapeHtml(auditEventLabel(record.event))}</span><span class="meta">${escapeHtml(record.event || "")}</span></td>
      <td>${record.orderNumber ? `<strong>${escapeHtml(record.orderNumber)}</strong>` : `<span class="meta">-</span>`}</td>
      <td><div class="audit-detail">${auditDetail(record) || `<span>상세 없음</span>`}</div></td>
    </tr>`).join("");
}

async function loadAuditLogs() {
  const response = await OrderApi.get("/api/audit");
  if (!response.ok) {
    state.auditLogs = [];
    renderAuditLogs();
    return;
  }
  state.auditLogs = await response.json();
  renderAuditLogs();
}

async function cancelOrder(row) {
  // 취소는 서버 저장 후 모든 로컬 목록에서 제거/이동시켜 화면 잔상을 없앤다.
  const order = boardOrders().find((item) => item.id === row.dataset.id);
  if (!order) return;
  const reasonInput = prompt(`${order.orderNumber} 주문의 취소 사유를 입력하세요.`);
  const reason = reasonInput == null ? "" : reasonInput.trim();
  if (!reason) return;
  if (!confirm(`${order.orderNumber} 주문을 취소하시겠습니까?`)) return;
  const response = await fetch(`/api/orders/${order.id}`, {
    method:"PATCH",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ action:"cancel", reason }),
  });
  const result = await response.json();
  if (!response.ok) return showMessage(result.error || "주문을 취소하지 못했습니다.", true);
  state.orders = state.orders.filter((item) => item.id !== result.id);
  state.calendarOrders = state.calendarOrders.filter((item) => item.id !== result.id);
  state.monthlyCalendarOrders = state.monthlyCalendarOrders.filter((item) => item.id !== result.id);
  state.cancelledOrders = [result, ...state.cancelledOrders.filter((item) => item.id !== result.id)];
  state.asHistoryOrders = state.asHistoryOrders.filter((item) => item.id !== result.id);
  state.selectedOrderIds.delete(result.id);
  delete state.managementDrafts[result.id];
  delete state.orderDrafts[result.id];
  syncChannelFilter();
  // 출고 고객 조회가 열려 있으면, 취소 반영 결과를 다시 읽어 화면과 맞춘다.
  if (!$("#shipping-customers-view").hidden) {
    await loadArchivedOrders().catch(() => {});
    if (asHistoryRoles.has(currentRole())) {
      await loadAsHistory().catch(() => {});
    }
  }
  if (!$("#cancelled-view").hidden) renderCancelledOrders();
  if (!$("#shipping-customers-view").hidden) renderAsHistory();
  showMessage(`${result.orderNumber} 주문을 취소했습니다.`);
  addNotification({ event:"order_cancelled", orderNumber:result.orderNumber });
  render();
  await pollNotifications();
}

function renderWorkloadStats() {
  const result = state.workloadStats;
  if (!result) return;
  $("#workload-summary").textContent = `${result.dateFrom} ~ ${result.dateTo} · 출고 완료 기준 · 총 작업 ${result.total}건 · 처리 수량 ${result.units}개`;
  $("#workload-empty").hidden = result.workers.length > 0;
  $("#workload-cards").innerHTML = result.workers.map((item) => `
    <button type="button" class="workload-card ${state.selectedWorkloadWorker === item.worker ? "is-selected" : ""}" data-workload-worker="${escapeHtml(item.worker)}">
      <div class="workload-card-head"><h3>${escapeHtml(item.worker)}</h3><strong>${item.total}건</strong></div>
      <p>처리 제품 수량 ${item.units}개</p>
      <dl>${Object.entries(result.stageLabels).map(([key, label]) => `<div><dt>${escapeHtml(label)}</dt><dd>${item.stages[key] || 0}건</dd></div>`).join("")}</dl>
    </button>`).join("");
  const workers = result.workers.filter((item) => item.total > 0).map((item) => item.worker);
  if (state.selectedWorkloadWorker && !workers.includes(state.selectedWorkloadWorker)) state.selectedWorkloadWorker = "";
  $("#workload-worker-select").innerHTML = `<option value="">작업자를 선택하세요</option>${workers.map((name) => `<option value="${escapeHtml(name)}" ${state.selectedWorkloadWorker === name ? "selected" : ""}>${escapeHtml(name)}</option>`).join("")}`;
  renderWorkloadProductionDetails();
}

function renderWorkloadProductionDetails() {
  const result = state.workloadStats;
  const workerName = state.selectedWorkloadWorker;
  const detail = $("#workload-production-detail");
  if (!result || !detail) return;
  detail.hidden = result.workers.length === 0;
  const rows = (result.workOrders || []).filter((item) => item.worker === workerName && (state.selectedWorkloadStage === "all" || item.stage === state.selectedWorkloadStage));
  $("#workload-production-body").innerHTML = rows.map((item) => `
    <tr><td><span class="work-stage-tag work-stage-tag--${item.stage}">${escapeHtml(item.stageLabel)}</span></td><td>${formatDate(item.completedAt)}</td><td><strong>${escapeHtml(item.worker)}</strong></td><td><span class="channel">${escapeHtml(item.channel)}</span><br><strong>${escapeHtml(item.orderNumber)}</strong></td><td>${escapeHtml(item.productName)}${item.optionName ? `<span class="meta">${escapeHtml(item.optionName)}</span>` : ""}</td><td>${item.quantity}개</td><td>${escapeHtml(item.recipient)}</td><td>${escapeHtml(item.managementNumber || "미등록")}</td></tr>`).join("");
  $("#workload-stage-tags").querySelectorAll("[data-workload-stage]").forEach((button) => button.classList.toggle("is-active", button.dataset.workloadStage === state.selectedWorkloadStage));
  $("#workload-production-empty").hidden = rows.length > 0;
  $("#workload-production-empty strong").textContent = workerName ? "제작 완료 건이 없습니다" : "작업자를 선택하세요";
  $("#workload-production-empty span").textContent = workerName ? "선택한 기간에 해당 작업자의 제작 완료 건이 없습니다." : "선택한 작업자의 제작 완료 건이 표시됩니다.";
}

async function loadWorkloadStats() {
  const from = $("#workload-from").value;
  const to = $("#workload-to").value;
  if (!from || !to) return;
  const response = await OrderApi.get(`/api/workload-stats?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`);
  const result = await response.json();
  if (!response.ok) {
    $("#workload-summary").textContent = result.error || "작업량을 불러오지 못했습니다.";
    return;
  }
  state.workloadStats = result;
  renderWorkloadStats();
}

async function useCalendarDatesForWorkload(dateFrom, dateTo = dateFrom) {
  if (!dateFrom) return;
  $("#workload-from").value = dateFrom;
  $("#workload-to").value = dateTo || dateFrom;
  showWorkspaceView("workload-view");
  await loadWorkloadStats();
  $("#workload-form").closest(".workload-panel").scrollIntoView({ behavior:"smooth", block:"start" });
}

async function restoreCancelledOrder(row) {
  const order = state.cancelledOrders.find((item) => item.id === row.dataset.id);
  if (!order) return;
  if (!confirm(`${order.orderNumber} 주문을 주문 목록으로 복구하시겠습니까?`)) return;
  const response = await fetch(`/api/orders/${order.id}`, {
    method:"PATCH",
    headers:{"Content-Type":"application/json"},
    body:JSON.stringify({ action:"restoreCancel" }),
  });
  const result = await response.json();
  if (!response.ok) return showMessage(result.error || "주문을 복구하지 못했습니다.", true);
  state.cancelledOrders = state.cancelledOrders.filter((item) => item.id !== result.id);
  if (!result.archivedAt) {
    state.orders = [result, ...state.orders.filter((item) => item.id !== result.id)];
  }
  replaceOrderInList(state.calendarOrders, result);
  replaceOrderInList(state.monthlyCalendarOrders, result);
  state.selectedOrderIds.delete(result.id);
  syncChannelFilter();
  renderCancelledOrders();
  render();
  if (!$("#shipping-customers-view").hidden) {
    await loadArchivedOrders().catch(() => {});
    if (asHistoryRoles.has(currentRole())) {
      await loadAsHistory().catch(() => {});
    }
  }
  showMessage(`${result.orderNumber} 주문을 복구했습니다.`);
  addNotification({ event:"order_restored", orderNumber:result.orderNumber });
  await pollNotifications();
}

async function updateOrder(row, action, checked) {
  try {
    // 체크박스, 관리번호, 배송지 변경, 상세 수정이 모두 이 경로를 탄다.
    // 상태 전이와 권한은 서버가 최종 검증하고, 실패하면 서버가 돌려준 주문으로 화면을 복구한다.
    const body = { action, checked, worker: worker() };
    if (action === "managementNumber") body.managementNumber = row.querySelector(".management-number").value;
    if (action === "details") {
      const draft = state.orderDrafts[row.dataset.id];
      if (!draft) throw new Error("수정할 내용을 먼저 입력하세요.");
      body.fields = draft;
    }
    const response = await fetch(`/api/orders/${row.dataset.id}`, { method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(body) });
    const result = await response.json();
    if (!response.ok) {
      if (result.order) {
        replaceOrderInList(state.orders, result.order);
        replaceOrderInList(state.calendarOrders, result.order);
        replaceOrderInList(state.monthlyCalendarOrders, result.order);
      }
      showMessage(result.error, true); render(); return;
    }
    replaceOrderInList(state.orders, result);
    replaceOrderInList(state.calendarOrders, result);
    replaceOrderInList(state.monthlyCalendarOrders, result);
    if (action === "managementNumber") delete state.managementDrafts[result.id];
    if (action === "details") delete state.orderDrafts[result.id];
    // 출고 최종 상태가 바뀌면 출고 고객 조회와 AS 이력도 함께 갱신한다.
    if (action === "shipping" && !$("#shipping-customers-view").hidden) {
      await loadArchivedOrders().catch(() => {});
      if (asHistoryRoles.has(currentRole())) {
        await loadAsHistory().catch(() => {});
      }
    }
    showMessage(action === "applyShippingUpdate" ? `${result.orderNumber} 배송지 변경을 반영했습니다.` : `${result.orderNumber} 주문이 저장되었습니다.`);
    addNotification({ event:"order_updated", orderNumber:result.orderNumber, action, checked });
    render();
    await pollNotifications();
  } catch (error) {
    showMessage(`서버 연결 오류: ${error.message}. 주문 상태를 다시 불러왔습니다.`, true);
    await loadOrders().catch(() => {});
  }
}

async function bulkUpdateOrders(action) {
  const selectedIds = [...state.selectedOrderIds];
  if (!selectedIds.length) return;
  const actionLabels = { preparing:"준비 중", production:"제작 완료", softwareInspection:"SW 검수 완료", shipping:"출고 확인" };
  const confirmMessage = `선택한 ${selectedIds.length}건을 '${actionLabels[action] || action}' 처리하시겠습니까?`;
  if (!confirm(confirmMessage)) return;
  const buttons = document.querySelectorAll("[data-bulk-action]");
  buttons.forEach((button) => { button.disabled = true; });
  showMessage(`선택 주문 ${selectedIds.length}건을 처리하고 있습니다.`);
  let success = 0;
  const errors = [];
  for (const id of selectedIds) {
    try {
      const response = await fetch(`/api/orders/${id}`, {
        method:"PATCH",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({ action, checked:true, worker:worker() }),
      });
      const result = await response.json();
      if (!response.ok) {
        errors.push(result.error || `${id} 처리 실패`);
        continue;
      }
      replaceOrderInList(state.orders, result);
      replaceOrderInList(state.calendarOrders, result);
      replaceOrderInList(state.monthlyCalendarOrders, result);
      success += 1;
    } catch (error) {
      errors.push(error.message);
    }
  }
  state.selectedOrderIds.clear();
  syncChannelFilter();
  render();
  showMessage(errors.length ? `${success}건 처리, ${errors.length}건 실패: ${errors.slice(0, 3).join(" / ")}` : `${success}건을 처리했습니다.`, Boolean(errors.length));
  await pollNotifications();
}

async function loadUsers() {
  if (!memberManagementRoles.has(state.user && state.user.role)) return;
  const response = await OrderApi.get("/api/users");
  if (!response.ok) return;
  const users = await response.json();
  $("#user-list").innerHTML = users.map((user) => {
    const canEdit = state.user.role === "owner" || !["owner", "developer"].includes(user.role);
    const disabled = canEdit ? "" : "disabled";
    return `
    <div class="user-row" data-user-id="${user.id}">
      <label>로그인 아이디<input class="edit-username" value="${escapeHtml(user.username)}" ${disabled}></label>
      <label>이름<input class="edit-display-name" value="${escapeHtml(user.displayName)}" ${disabled}></label>
      <label>권한<select class="edit-role" ${disabled}>${roleOptions(user.role, canEdit ? (state.user.role === "developer" ? roleOrder.slice(2) : roleOrder) : [user.role])}</select></label>
      <label>새 비밀번호<input class="edit-password" type="password" minlength="8" placeholder="변경할 때만 입력" ${disabled}></label>
      <label class="enabled-field"><input class="edit-enabled" type="checkbox" ${user.enabled !== false ? "checked" : ""} ${disabled}> 사용</label>
      <button class="save-user primary" type="button" ${disabled}>저장</button>
      <button class="delete-user" type="button" ${user.id === state.user.id || !canEdit ? "disabled" : ""}>삭제</button>
    </div>`;
  }).join("");
}

async function initializeAuth() {
  const response = await OrderApi.get("/api/auth/status");
  const result = await response.json();
  state.user = result.user;
  state.setupRequired = result.setupRequired;
  if (!state.user) {
    setAuthScreenState(false);
    updateAccessUI();
    setAuthMode(state.setupRequired ? "setup" : "login");
    return;
  }
  setAuthScreenState(true);
  $("#current-user").textContent = `${state.user.displayName} (${roleLabel(state.user.role)})`;
  const createRoleSelect = $("#user-form select[name=role]");
  createRoleSelect.innerHTML = roleOptions("worker", state.user.role === "developer" ? roleOrder.slice(2) : roleOrder);
  updateAccessUI();
  showWorkspaceView("orders-view");
  resetOrderFilters();
  await loadOrders();
  startNotifications();
}

document.querySelectorAll(".workspace-tab").forEach((button) => {
  button.addEventListener("click", () => showWorkspaceView(button.dataset.view));
});

$("#home-button").addEventListener("click", () => {
  showWorkspaceView("orders-view");
  $("#manual-order").open = false;
  resetOrderFilters();
  resetManualOrderFormMode();
  render();
  window.scrollTo({ top: 0, behavior: "smooth" });
});

document.querySelectorAll("[data-auth-mode]").forEach((button) => {
  button.addEventListener("click", () => setAuthMode(button.dataset.authMode));
});

$("#auth-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  const path = state.setupRequired ? "/api/auth/setup" : state.authMode === "register" ? "/api/auth/register" : "/api/auth/login";
  const response = await fetch(path, { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const result = await response.json();
  if (!response.ok) { $("#auth-message").textContent = result.error; return; }
  event.target.reset();
  if (state.authMode === "register") {
    setAuthMode("login");
    $("#auth-message").textContent = "회원가입이 완료됐습니다. 가입한 계정으로 로그인하세요.";
    return;
  }
  $("#auth-message").textContent = "";
  sessionStorage.removeItem(channelRequirementNoticeKey);
  await initializeAuth();
  showChannelRequirementNotice();
});

$("#logout-button").addEventListener("click", async () => {
  await fetch("/api/auth/logout", { method:"POST" });
  state.user = null;
  state.orders = [];
  state.calendarOrders = [];
  state.calendarOrdersDate = "";
  state.archivedOrders = [];
  state.cancelledOrders = [];
  state.asHistoryOrders = [];
  state.orderDateCounts = {};
  state.auditLogs = [];
  state.selectedShippingDate = "";
  state.shippingCalendarMonth = currentMonthKey();
  state.notificationsReady = false;
  state.seenNotificationKeys.clear();
  state.recentNotificationFingerprints.clear();
  state.notifications = [];
  state.unreadNotifications = 0;
  sessionStorage.removeItem(channelRequirementNoticeKey);
  $("#channel-requirement-notice").hidden = true;
  document.body.classList.remove("is-calendar-date-mode");
  stopNotifications();
  $("#notification-stack").innerHTML = "";
  $("#notification-popover").hidden = true;
  $("#notification-button").setAttribute("aria-expanded", "false");
  renderNotificationCenter();
  resetOrderFilters();
  render();
  await initializeAuth();
});

document.querySelectorAll("[data-close-channel-notice]").forEach((button) => {
  button.addEventListener("click", closeChannelRequirementNotice);
});
$("#channel-requirement-notice").addEventListener("click", (event) => {
  if (event.target === event.currentTarget) closeChannelRequirementNotice();
});
$("#channel-notice-editor").addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = $("#channel-notice-editor-message");
  message.textContent = "저장 중...";
  const payload = Object.fromEntries(new FormData(event.currentTarget).entries());
  const response = await OrderApi.patchJson("/api/login-notice", payload);
  const result = await response.json();
  if (!response.ok) {
    message.textContent = result.error || "공지사항을 저장하지 못했습니다.";
    return;
  }
  renderChannelRequirementNotice(result);
  message.textContent = "저장했습니다.";
});
$("#channel-notice-editor").addEventListener("input", (event) => {
  const editor = event.currentTarget;
  $("#notice-preview-title").textContent = editor.elements.title.value || "공지사항";
  $("#notice-preview-lead").textContent = editor.elements.lead.value || "소제목";
  $("#notice-preview-message").textContent = editor.elements.message.value || "공지 내용이 여기에 표시됩니다.";
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("#channel-requirement-notice").hidden) {
    closeChannelRequirementNotice();
  }
});

$("#theme-toggle").addEventListener("click", () => {
  applyTheme(document.body.dataset.theme === "dark" ? "light" : "dark");
});

$("#server-status").addEventListener("click", () => {
  state.serverStatus = { ...state.serverStatus, checking: true };
  renderServerStatus();
  refreshServerStatus().catch(() => {});
});

$("#notification-button").addEventListener("click", (event) => {
  event.stopPropagation();
  toggleNotificationPopover();
});

$("#notification-clear").addEventListener("click", () => {
  markNotificationsRead();
});

document.addEventListener("click", (event) => {
  const popover = $("#notification-popover");
  if (popover.hidden) return;
  if (event.target.closest("#notification-popover") || event.target.closest("#notification-button")) return;
  toggleNotificationPopover(false);
});

$("#user-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const payload = Object.fromEntries(new FormData(event.target).entries());
  const response = await fetch("/api/users", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
  const result = await response.json();
  $("#user-message").textContent = response.ok ? `${result.displayName} ${roleLabel(result.role)} 계정을 추가했습니다.` : result.error;
  if (response.ok) { event.target.reset(); await loadUsers(); }
});
$("#user-list").addEventListener("click", async (event) => {
  const row = event.target.closest(".user-row");
  if (!row) return;
  if (event.target.classList.contains("save-user")) {
    const payload = {
      username: row.querySelector(".edit-username").value,
      displayName: row.querySelector(".edit-display-name").value,
      role: row.querySelector(".edit-role").value,
      password: row.querySelector(".edit-password").value,
      enabled: row.querySelector(".edit-enabled").checked,
    };
    const response = await fetch(`/api/users/${row.dataset.userId}`, { method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
    const result = await response.json();
    $("#user-message").textContent = response.ok ? `${result.displayName} 계정을 수정했습니다.` : result.error;
    if (response.ok) await loadUsers();
  }
  if (event.target.classList.contains("delete-user")) {
    const name = row.querySelector(".edit-display-name").value;
    if (!confirm(`${name} 계정을 삭제하시겠습니까?`)) return;
    const response = await fetch(`/api/users/${row.dataset.userId}`, { method:"DELETE" });
    const result = await response.json();
    $("#user-message").textContent = response.ok ? `${result.displayName} 계정을 삭제했습니다.` : result.error;
    if (response.ok) await loadUsers();
  }
});
$("#files").addEventListener("change", (event) => {
  const files = [...event.target.files];
  $("#import-button").disabled = files.length === 0;
  $("#selected-files").textContent = files.length ? `${files.length}개 선택: ${files.map((file) => file.name).join(", ")}` : "선택된 파일 없음";
  showMessage(files.length ? "파일 선택 완료. '주문 가져오기'를 눌러주세요." : "");
});
$("#search").addEventListener("input", render);
$("#monthly-worker-search").addEventListener("input", (event) => {
  state.monthlyProductionWorkerQuery = event.target.value;
  render();
});
$("#daily-order-calendar").addEventListener("click", async (event) => {
  const button = event.target.closest("[data-date]");
  if (!button) return;
  clearShippingDateSelection();
  const dateKey = button.dataset.date;
  if (!state.monthlyRangeStart || state.monthlyRangeEnd) {
    state.monthlyRangeStart = dateKey;
    state.monthlyRangeEnd = "";
  } else {
    state.monthlyRangeEnd = dateKey;
    if (state.monthlyRangeEnd < state.monthlyRangeStart) [state.monthlyRangeStart, state.monthlyRangeEnd] = [state.monthlyRangeEnd, state.monthlyRangeStart];
  }
  state.monthlySelectedCalendarDate = state.monthlyRangeStart;
  try {
    await loadMonthlyCalendarDateOrders(state.monthlyRangeStart, state.monthlyRangeEnd || state.monthlyRangeStart);
  } catch (error) {
    showMessage(error.message, true);
    render();
  }
});
$("#calendar-clear-date").addEventListener("click", async () => {
  state.monthlySelectedCalendarDate = "";
  state.monthlyRangeStart = "";
  state.monthlyRangeEnd = "";
  await loadMonthlyCalendarDateOrders("");
});
$("#collection-range-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  clearShippingDateSelection();
  const dateFrom = $("#collection-range-from").value;
  const dateTo = $("#collection-range-to").value;
  if (dateTo < dateFrom) return showMessage("종료일은 시작일보다 빠를 수 없습니다.", true);
  state.monthlyRangeStart = dateFrom;
  state.monthlyRangeEnd = dateTo;
  state.monthlySelectedCalendarDate = dateFrom;
  state.calendarMonth = dateFrom.slice(0, 7);
  try {
    await loadMonthlyCalendarDateOrders(dateFrom, dateTo);
  } catch (error) {
    showMessage(error.message, true);
  }
});
$("#calendar-workload-sync").addEventListener("click", () => {
  useCalendarDatesForWorkload(state.monthlyRangeStart, state.monthlyRangeEnd || state.monthlyRangeStart).catch((error) => showMessage(error.message, true));
});
$("#calendar-month").addEventListener("change", (event) => {
  state.calendarMonth = event.target.value || currentMonthKey();
  render();
});
$("#calendar-prev-month").addEventListener("click", () => shiftCalendarMonth(-1));
$("#calendar-next-month").addEventListener("click", () => shiftCalendarMonth(1));
$("#calendar-today").addEventListener("click", async () => {
  clearShippingDateSelection();
  state.monthlySelectedCalendarDate = todayKey();
  state.monthlyRangeStart = state.monthlySelectedCalendarDate;
  state.monthlyRangeEnd = state.monthlySelectedCalendarDate;
  state.calendarMonth = state.monthlySelectedCalendarDate.slice(0, 7);
  $("#calendar-month").value = state.calendarMonth;
  try {
    await loadMonthlyCalendarDateOrders(state.monthlyRangeStart, state.monthlyRangeEnd);
  } catch (error) {
    showMessage(error.message, true);
    render();
  }
});
$("#calendar-toggle").addEventListener("click", () => {
  const panel = $("#calendar-grid-panel");
  panel.hidden = !panel.hidden;
  render();
});
$("#shipping-calendar").addEventListener("click", (event) => {
  const button = event.target.closest("[data-date]");
  if (!button) return;
  clearCollectionDateSelection();
  const dateKey = button.dataset.date;
  if (!state.shippingRangeStart || state.shippingRangeEnd) {
    state.shippingRangeStart = dateKey;
    state.shippingRangeEnd = "";
  } else {
    state.shippingRangeEnd = dateKey;
    if (state.shippingRangeEnd < state.shippingRangeStart) [state.shippingRangeStart, state.shippingRangeEnd] = [state.shippingRangeEnd, state.shippingRangeStart];
  }
  state.selectedShippingDate = dateKey;
  renderShippingCalendar();
});
$("#shipping-calendar-clear-date").addEventListener("click", () => {
  state.selectedShippingDate = "";
  state.shippingRangeStart = "";
  state.shippingRangeEnd = "";
  renderShippingCalendar();
});
$("#shipping-range-form").addEventListener("submit", (event) => {
  event.preventDefault();
  clearCollectionDateSelection();
  const dateFrom = $("#shipping-range-from").value;
  const dateTo = $("#shipping-range-to").value;
  if (dateTo < dateFrom) return showMessage("종료일은 시작일보다 빠를 수 없습니다.", true);
  state.shippingRangeStart = dateFrom;
  state.shippingRangeEnd = dateTo;
  state.selectedShippingDate = dateFrom;
  state.shippingCalendarMonth = dateFrom.slice(0, 7);
  renderShippingCalendar();
});
$("#shipping-workload-sync").addEventListener("click", () => {
  useCalendarDatesForWorkload(state.shippingRangeStart || state.selectedShippingDate, state.shippingRangeEnd || state.shippingRangeStart || state.selectedShippingDate).catch((error) => showMessage(error.message, true));
});
$("#shipping-calendar-month").addEventListener("change", (event) => {
  state.shippingCalendarMonth = event.target.value || currentMonthKey();
  renderShippingCalendar();
});
$("#shipping-calendar-prev-month").addEventListener("click", () => shiftShippingCalendarMonth(-1));
$("#shipping-calendar-next-month").addEventListener("click", () => shiftShippingCalendarMonth(1));
$("#shipping-calendar-today").addEventListener("click", () => {
  clearCollectionDateSelection();
  state.selectedShippingDate = todayKey();
  state.shippingRangeStart = state.selectedShippingDate;
  state.shippingRangeEnd = state.selectedShippingDate;
  state.shippingCalendarMonth = state.selectedShippingDate.slice(0, 7);
  renderShippingCalendar();
});
$("#shipping-calendar-toggle").addEventListener("click", () => {
  const panel = $("#shipping-calendar-grid-panel");
  panel.hidden = !panel.hidden;
  renderShippingCalendar();
});
$("#order-sort").addEventListener("change", render);
$("#archived-search").addEventListener("input", renderArchivedOrders);
$("#archived-channel").addEventListener("change", renderArchivedOrders);
$("#archived-toggle").addEventListener("click", () => {
  const body = $("#archived-section-body");
  body.hidden = !body.hidden;
  syncArchivedSectionState();
});
$("#cancelled-search").addEventListener("input", renderCancelledOrders);
$("#cancelled-orders-body").addEventListener("click", (event) => {
  const button = event.target.closest(".restore-order");
  if (!button) return;
  restoreCancelledOrder(button.closest("tr"));
});
$("#as-history-search").addEventListener("input", renderAsHistory);
$("#audit-search").addEventListener("input", renderAuditLogs);
$("#workload-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadWorkloadStats();
});
$("#workload-worker-select").addEventListener("change", (event) => {
  state.selectedWorkloadWorker = event.target.value;
  renderWorkloadStats();
});
$("#workload-cards").addEventListener("click", (event) => {
  const card = event.target.closest("[data-workload-worker]");
  if (!card) return;
  state.selectedWorkloadWorker = card.dataset.workloadWorker;
  renderWorkloadStats();
});
$("#workload-stage-tags").addEventListener("click", (event) => {
  const tag = event.target.closest("[data-workload-stage]");
  if (!tag) return;
  state.selectedWorkloadStage = tag.dataset.workloadStage;
  renderWorkloadProductionDetails();
});
$("#channel-filter").addEventListener("click", (event) => {
  const button = event.target.closest("[data-channel]");
  if (!button) return;
  state.selectedChannel = button.dataset.channel;
  syncChannelFilter();
  render();
});
$("#select-visible-orders").addEventListener("change", (event) => {
  const visible = filteredOrders().sort(compareOrdersBySelectedSort).filter(isBulkSelectableOrder);
  if (event.target.checked) visible.forEach((order) => state.selectedOrderIds.add(order.id));
  else visible.forEach((order) => state.selectedOrderIds.delete(order.id));
  render();
});
$("#bulk-action-bar").addEventListener("click", (event) => {
  const button = event.target.closest("[data-bulk-action]");
  if (!button) return;
  bulkUpdateOrders(button.dataset.bulkAction);
});
document.querySelector(".stats").addEventListener("click", (event) => {
  const card = event.target.closest(".stat-card");
  if (!card) return;
  state.selectedStatus = card.dataset.status;
  render();
});
$("#manual-order-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  await commitDateTimeInputs(event.target);
  const form = new FormData(event.target);
  const payload = Object.fromEntries(form.entries());
  payload.worker = worker();
  try {
    const editOrderId = String(payload.editOrderId || "").trim();
    delete payload.editOrderId;
    const response = editOrderId
      ? await fetch(`/api/orders/${editOrderId}`, { method:"PATCH", headers:{"Content-Type":"application/json"}, body:JSON.stringify({ action:"details", fields:payload, worker:worker() }) })
      : await fetch("/api/orders/manual", { method:"POST", headers:{"Content-Type":"application/json"}, body:JSON.stringify(payload) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error);
    event.target.reset();
    event.target.elements.quantity.value = "1";
    event.target.elements.amount.value = "0";
    resetManualOrderFormMode(editOrderId ? `${result.orderNumber} 주문을 수정했습니다.` : `${result.orderNumber} 수기 주문을 등록했습니다.`);
    addNotification({ event:editOrderId ? "order_updated" : "order_created", orderNumber:result.orderNumber, action:editOrderId ? "details" : "" });
    await loadOrders();
    await pollNotifications();
    if (!$("#shipping-customers-view").hidden) {
      await loadArchivedOrders().catch(() => {});
      if (asHistoryRoles.has(currentRole())) {
        await loadAsHistory().catch(() => {});
      }
    }
  } catch (error) { showManualMessage(error.message, true); }
});
$("#manual-order-cancel").addEventListener("click", () => {
  resetManualOrderFormMode("주문 수정이 취소되었습니다.");
});
$("#export-button").addEventListener("click", async () => {
  const button = $("#export-button");
  button.disabled = true;
    showMessage("새로 출고 확인된 주문의 엑셀을 만들고 있습니다.");
  try {
    const response = await OrderApi.postJson("/api/export/shipped");
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.error || "엑셀을 만들지 못했습니다.");
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const today = new Date().toLocaleDateString("sv-SE");
    const filenameMatch = disposition.match(/filename="([^"]+)"/);
    const filename = filenameMatch ? filenameMatch[1] : `shipped-orders-${today}.xlsx`;
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    await loadOrders();
    if (!$("#shipping-customers-view").hidden) {
      await loadArchivedOrders().catch(() => {});
      if (asHistoryRoles.has(currentRole())) {
        await loadAsHistory().catch(() => {});
      }
    }
    showMessage("새 출고 건을 엑셀로 저장하고 출고고객조회로 이동했습니다. 다음 엑셀에는 다시 포함되지 않습니다.");
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});
$("#dedupe-button").addEventListener("click", async () => {
  // 후보 미리보기 없이 바로 정리하는 운영 버튼이다.
  // 서버는 작업 진행이 더 많은 주문을 남기고 중복 행의 빈 정보만 병합한다.
  if (!confirm("중복으로 판단된 주문을 정리하시겠습니까? 작업 체크가 더 많이 된 주문을 남기고 중복 행만 제거합니다.")) return;
  const button = $("#dedupe-button");
  button.disabled = true;
  showMessage("중복 주문을 확인하고 있습니다.");
  try {
    const response = await OrderApi.postJson("/api/orders/dedupe");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "중복 주문을 정리하지 못했습니다.");
    await loadOrders();
    showMessage(result.removed ? `중복 주문 ${result.removed}건을 정리했습니다. 정리 묶음 ${result.groups}개` : "정리할 중복 주문이 없습니다.");
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});
$("#delete-latest-import").addEventListener("click", async () => {
  const button = $("#delete-latest-import");
  button.disabled = true;
  try {
    const previewResponse = await OrderApi.get("/api/orders/latest-import");
    const preview = await previewResponse.json();
    if (!previewResponse.ok) throw new Error(preview.error || "최근 수집 주문을 확인하지 못했습니다.");
    if (!preview.count) throw new Error("삭제할 수 있는 최근 수집 주문이 없습니다. 이미 작업이 시작된 주문은 보호됩니다.");
    const files = preview.sourceFiles.length ? `\n파일: ${preview.sourceFiles.join(", ")}` : "";
    if (!confirm(`가장 최근에 수집된 미작업 주문 ${preview.count}건을 삭제하시겠습니까?${files}\n이 작업은 되돌릴 수 없습니다.`)) return;
    const response = await fetch("/api/orders/latest-import", { method:"DELETE" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "최근 수집 주문을 삭제하지 못했습니다.");
    await loadOrders();
    showMessage(`최근 수집 주문 ${result.deleted}건을 삭제했습니다.`);
  } catch (error) {
    showMessage(error.message, true);
  } finally {
    button.disabled = false;
  }
});
function bindOrderTableEvents(tableBody) {
  if (!tableBody) return;
  tableBody.addEventListener("change", (event) => {
    const row = event.target.closest("tr");
    if (!row) return;
    if (event.target.classList.contains("order-select")) {
      if (event.target.checked) state.selectedOrderIds.add(row.dataset.id);
      else state.selectedOrderIds.delete(row.dataset.id);
      syncBulkActionBar(filteredOrders().sort(compareOrdersBySelectedSort));
      return;
    }
    if (event.target.classList.contains("preparing-check")) updateOrder(row, "preparing", event.target.checked);
    if (event.target.classList.contains("production-check")) updateOrder(row, "production", event.target.checked);
    if (event.target.classList.contains("software-inspection-check")) updateOrder(row, "softwareInspection", event.target.checked);
    if (event.target.classList.contains("shipping-check")) updateOrder(row, "shipping", event.target.checked);
  });
  tableBody.addEventListener("click", (event) => {
    const cancelButton = event.target.closest(".cancel-order");
    if (cancelButton) return cancelOrder(cancelButton.closest("tr"));
    const detailButton = event.target.closest(".row-detail-toggle");
    if (detailButton) {
      const id = detailButton.closest("tr").dataset.id;
      state.expandedOrderId = state.expandedOrderId === id ? null : id;
      render();
      return;
    }
    const editButton = event.target.closest(".edit-order");
    if (editButton) {
      const order = boardOrders().find((item) => item.id === editButton.closest("tr").dataset.id);
      if (order) setManualOrderMode(order);
      return;
    }
    const button = event.target.closest(".save-management");
    if (button) updateOrder(button.closest("tr"), "managementNumber", true);
    const shippingUpdateButton = event.target.closest(".apply-shipping-update");
    if (shippingUpdateButton) updateOrder(shippingUpdateButton.closest("tr"), "applyShippingUpdate", true);
  });
  tableBody.addEventListener("input", (event) => {
    const row = event.target.closest("tr");
    if (!row) return;
    if (event.target.classList.contains("management-number")) {
      state.managementDrafts[row.dataset.id] = event.target.value;
    }
    if (event.target.classList.contains("order-edit-field")) {
      const field = event.target.dataset.field;
      if (!field) return;
      state.orderDrafts[row.dataset.id] = state.orderDrafts[row.dataset.id] || {};
      state.orderDrafts[row.dataset.id][field] = event.target.value;
    }
  });
  tableBody.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && event.target.classList.contains("management-number")) {
      if (!event.ctrlKey && !event.metaKey) return;
      event.preventDefault();
      updateOrder(event.target.closest("tr"), "managementNumber", true);
      return;
    }
    if (event.key === "Enter" && event.target.classList.contains("order-edit-field")) {
      event.preventDefault();
      updateOrder(event.target.closest("tr"), "details", true);
    }
  });
}

bindOrderTableEvents($("#orders-body"));
$("#upload-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const files = $("#files").files;
  if (!files.length) return;
  const data = new FormData();
  const excelPassword = $("#excel-password").value;
  if (excelPassword) data.append("excelPassword", excelPassword);
  for (const file of files) data.append("files", file);
  showMessage("주문을 가져오고 있습니다. 창을 닫지 마세요.");
  $("#import-button").disabled = true;
  try {
    const response = await OrderApi.postForm("/api/import", data);
    const result = await response.json();
    if (!response.ok) return showMessage(result.error || "파일을 가져오지 못했습니다.", true);
    const updates = Number(result.shippingUpdates || 0);
    showMessage(`${result.added}건 추가, 중복 ${result.duplicates}건 제외${updates ? `, 배송지 변경 ${updates}건 감지` : ""}${result.errors.length ? `\n일부 오류: ${result.errors.join(", ")}` : ""}`);
    event.target.reset();
    $("#selected-files").textContent = "선택된 파일 없음";
    try {
      await loadOrders();
    } catch (refreshError) {
      showMessage(`주문은 가져왔지만 화면 갱신에 실패했습니다. 새로고침 해주세요: ${refreshError.message}`, true);
    }
  } catch (error) {
    showMessage(`서버 연결 또는 파일 처리 오류: ${error.message}`, true);
  } finally {
    $("#import-button").disabled = $("#files").files.length === 0;
  }
});

const workloadToday = new Date();
const workloadLocalDate = (value) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
$("#workload-to").value = workloadLocalDate(workloadToday);
$("#workload-from").value = workloadLocalDate(new Date(workloadToday.getFullYear(), workloadToday.getMonth(), 1));
$("#collection-range-from").value = workloadLocalDate(new Date(workloadToday.getFullYear(), workloadToday.getMonth(), 1));
$("#collection-range-to").value = workloadLocalDate(workloadToday);
$("#shipping-range-from").value = workloadLocalDate(new Date(workloadToday.getFullYear(), workloadToday.getMonth(), 1));
$("#shipping-range-to").value = workloadLocalDate(workloadToday);
initializeAuth().catch((error) => { $("#auth-message").textContent = error.message; });
startServerStatusMonitor();
startOrderAutoRefresh();
syncArchivedSectionState();

window.addEventListener("focus", () => {
  refreshVisibleData({ force: true }).catch(() => {});
});

document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshVisibleData({ force: true }).catch(() => {});
  }
});
