const defaultOrderColumns = ["channel", "product", "address", "preparing", "management", "production", "inspection", "shipping"];
const state = { orders: [], calendarOrders: [], calendarOrdersDate: "", monthlyCalendarOrders: [], monthlyCalendarOrdersDate: "", archivedOrders: [], cancelledOrders: [], asHistoryOrders: [], orderDateCounts: {}, auditLogs: [], workloadStats: null, selectedWorkloadWorker: "", selectedWorkloadStage: "all", notifications: [], unreadNotifications: 0, notificationsReady: false, seenNotificationKeys: new Set(), recentNotificationFingerprints: new Map(), notificationTimer: null, serverStatusTimer: null, serverStatus: { online: false, checking: true, latencyMs: 0, uptimeSeconds: 0, checkedAt: null, dataFiles: {}, error: "" }, user: null, setupRequired: false, authMode: "login", selectedChannel: "all", selectedStatus: "all", selectedCalendarDate: "", monthlySelectedCalendarDate: "", monthlyRangeStart: "", monthlyRangeEnd: "", selectedShippingDate: "", shippingRangeStart: "", shippingRangeEnd: "", calendarMonth: "", shippingCalendarMonth: "", monthlyProductionWorkerQuery: "", visibleOrderColumns: new Set(defaultOrderColumns), selectedOrderIds: new Set(), expandedOrderId: null, managementDrafts: {}, orderDrafts: {}, editingOrderId: null };

const roleLabels = { owner:"총책임자", developer:"개발자", as_manager:"AS 담당자", sales_manager:"판매 담당자", md:"MD", worker:"일반 작업자", admin:"총책임자" };
const roleOrder = ["owner", "developer", "as_manager", "sales_manager", "md", "worker"];
const memberManagementRoles = new Set(["owner", "developer"]);
const auditViewRoles = new Set(["owner", "developer"]);
const monthlyStatsRoles = new Set(["owner"]);
const duplicateCleanupRoles = new Set(["owner", "md"]);
const noticeEditRoles = new Set(["owner", "md"]);
const cancelOrderRoles = new Set(["owner", "developer", "as_manager", "sales_manager", "md"]);
const orderAdminRoles = new Set(["owner", "developer", "sales_manager", "md"]);
const orderEditRoles = new Set(["owner", "developer", "as_manager", "sales_manager", "md"]);
const asHistoryRoles = new Set(["owner", "developer", "as_manager", "sales_manager", "md"]);
const themeStorageKey = "order-workflow-theme";
