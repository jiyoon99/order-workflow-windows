const $ = (selector) => document.querySelector(selector);
const escapeHtml = (value) => String(value == null ? "" : value).replace(/[&<>'"]/g, (char) => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", "'":"&#39;", '"':"&quot;" })[char]);
const formatDate = (value) => value ? new Date(value).toLocaleString("ko-KR", { dateStyle:"short", timeStyle:"short" }) : "";
const formatAmount = (value) => Number(value || 0).toLocaleString("ko-KR");
const dateKeyFromDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
const monthKeyFromDate = (date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}`;
const todayKey = () => dateKeyFromDate(new Date());
const currentMonthKey = () => monthKeyFromDate(new Date());
const formatClock = (date) => date ? date.toLocaleTimeString("ko-KR", { hour:"2-digit", minute:"2-digit", second:"2-digit" }) : "";
const roleLabel = (role) => roleLabels[role] || role;
const roleOptions = (selected, allowedRoles = roleOrder) => allowedRoles
  .map((role) => `<option value="${role}" ${role === selected ? "selected" : ""}>${roleLabel(role)}</option>`).join("");
