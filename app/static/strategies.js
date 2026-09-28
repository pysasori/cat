const $ = (id) => document.getElementById(id);
let state = null;
let selectedProfileId = localStorage.getItem("pwcat-strategy") || null;
let pickedIds = new Set();

function apiError(detail, fallback) {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map(item => item.msg || String(item)).join("; ");
  return fallback;
}

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = apiError((await response.json()).detail, detail); } catch (_) {}
    throw new Error(detail);
  }
  return response.status === 204 ? null : response.json();
}

function toast(message, error = false) {
  const node = $("toast"); node.textContent = message; node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.className = "toast", 3200);
}

function escapeHtml(value) { const node = document.createElement("div"); node.textContent = value ?? ""; return node.innerHTML; }
function money(value) { return value == null ? "—" : Math.round(Number(value)).toLocaleString("uk-UA"); }
function initials(value) { return (value || "?").trim().split(/\s+/).slice(0, 2).map(word => word[0]?.toUpperCase()).join("") || "?"; }
function profile() { return state?.profiles.find(item => item.id === selectedProfileId) || null; }
function catalogItem(id) { return state?.catalog.find(item => item.id === id); }

async function load() {
  try {
    state = await api("/api/state");
    if (!state.profiles.some(item => item.id === selectedProfileId)) selectedProfileId = state.profiles[0]?.id || null;
    if (selectedProfileId) localStorage.setItem("pwcat-strategy", selectedProfileId);
    renderAll();
  } catch (error) { toast(error.message, true); }
}

function renderAll() { renderProfileList(); renderHeader(); renderEntries(); }

function renderProfileList() {
  const root = $("profileList"); root.innerHTML = "";
  state.profiles.forEach(item => {
    const users = state.characters.filter(character => character.profile_id === item.id);
    const button = document.createElement("button");
    button.className = `profile-card${item.id === selectedProfileId ? " active" : ""}`;
    button.innerHTML = `<span class="profile-card-avatar">${escapeHtml(initials(item.name))}</span><span class="profile-card-copy"><b>${escapeHtml(item.name)}</b><small>${users.length ? users.map(user => user.character_name).join(", ") : "не призначена"}</small></span><span class="profile-card-count">${item.entries.length}</span>`;
    button.onclick = () => { selectedProfileId = item.id; localStorage.setItem("pwcat-strategy", item.id); renderAll(); };
    root.append(button);
  });
  if (!state.profiles.length) root.innerHTML = '<div class="sidebar-empty">Створіть першу стратегію</div>';
}

function renderHeader() {
  const current = profile(); const disabled = !current;
  ["scenarioName", "copyProfile", "deleteProfile", "openItemPicker", "refreshProfilePrices"].forEach(id => $(id).disabled = disabled);
  $("scenarioName").value = current?.name || "";
  $("strategyTitle").textContent = current?.name || "Оберіть стратегію";
  const users = current ? state.characters.filter(item => item.profile_id === current.id) : [];
  $("scenarioUsage").textContent = current ? `Призначена: ${users.map(item => item.character_name).join(", ") || "нікому"}` : "Стратегія містить тільки товари й правила цін";
}

async function patchProfile(patch, rerender = true) {
  const current = profile(); if (!current) return;
  try {
    Object.assign(current, await api(`/api/profiles/${current.id}`, { method: "PATCH", body: JSON.stringify(patch) }));
    if (rerender) renderAll();
  } catch (error) { toast(error.message, true); throw error; }
}

function computedPrice(entry, item, side) {
  const mode = entry[`${side}_price_mode`]; const manual = Number(entry[`${side}_price`]);
  const market = item[side === "sale" ? "market_sell" : "market_buy"];
  const adjustment = Number(entry[`${side}_adjustment`] || 0);
  if (mode === "manual") return manual;
  if (market == null) return null;
  if (mode === "market") return market;
  const direction = side === "sale" ? -1 : 1;
  if (mode === "market_percent") return Math.round(market * (1 + direction * adjustment / 100));
  return Math.round(market + direction * adjustment);
}

function priceCell(entry, item, side) {
  const enabledField = `${side}_enabled`; const modeField = `${side}_price_mode`; const priceField = `${side}_price`; const adjustmentField = `${side}_adjustment`;
  const mode = entry[modeField]; const market = item[side === "sale" ? "market_sell" : "market_buy"]; const result = computedPrice(entry, item, side);
  let input = '<span class="no-adjust">без поправки</span>';
  if (mode === "manual") input = `<input class="price-value" data-field="${priceField}" type="number" min="1" value="${entry[priceField]}">`;
  if (mode === "market_percent") input = `<div class="adjust-input"><input data-field="${adjustmentField}" type="number" step="0.1" value="${entry[adjustmentField] || 0}"><span>${side === "sale" ? "−" : "+"} %</span></div>`;
  if (mode === "market_amount") input = `<div class="adjust-input"><input data-field="${adjustmentField}" type="number" step="1" value="${entry[adjustmentField] || 0}"><span>${side === "sale" ? "−" : "+"} мон.</span></div>`;
  return `<div class="price-cell${entry[enabledField] ? "" : " side-off"}"><div class="price-top"><label class="switch-label"><input data-field="${enabledField}" type="checkbox" ${entry[enabledField] ? "checked" : ""}><span>${side === "sale" ? "Продаж" : "Скупка"}</span></label><span class="market-base">ринок ${money(market)}</span></div><div class="price-controls"><select data-field="${modeField}"><option value="manual">Вручну</option><option value="market">Ринкова</option><option value="market_percent">Ринок ± %</option><option value="market_amount">Ринок ± сума</option></select>${input}</div><div class="price-result">Підсумок: <b class="${result == null || result < 1 ? "bad" : ""}">${result == null ? "немає ціни" : money(result)}</b></div></div>`;
}

function entryVisible(entry, item) {
  const query = $("entrySearch").value.trim().toLocaleLowerCase("uk"); const filter = $("entryFilter").value;
  if (query && !`${item.name} ${item.market_item_id || ""}`.toLocaleLowerCase("uk").includes(query)) return false;
  if (filter === "active" && !entry.enabled) return false;
  if (filter === "sale" && !entry.sale_enabled) return false;
  if (filter === "buy" && !entry.buy_enabled) return false;
  if (filter === "missing" && item.market_sell != null && item.market_buy != null) return false;
  return true;
}

function renderEntries() {
  const current = profile(); const root = $("profileEntries"); root.innerHTML = ""; const entries = current?.entries || [];
  $("entryCount").textContent = entries.length; let visible = 0;
  entries.forEach((entry, index) => {
    const item = catalogItem(entry.item_id); if (!item || !entryVisible(entry, item)) return; visible += 1;
    const row = document.createElement("tr"); row.className = entry.enabled ? "" : "row-disabled";
    row.innerHTML = `<td class="center"><input class="row-check" data-field="enabled" type="checkbox" ${entry.enabled ? "checked" : ""}></td><td><div class="item-cell"><img src="/api/catalog/${item.id}/icon"><span><b>${escapeHtml(item.name)}</b><small>ID ${item.market_item_id || "—"}</small></span></div></td><td><input class="max-input" data-field="max_owned" type="number" min="0" value="${entry.max_owned}"></td><td>${priceCell(entry, item, "sale")}</td><td>${priceCell(entry, item, "buy")}</td><td><button class="remove-entry icon-button danger-text" title="Прибрати">×</button></td>`;
    row.querySelectorAll("select[data-field]").forEach(select => select.value = entry[select.dataset.field]);
    row.querySelectorAll("[data-field]").forEach(input => input.onchange = async () => {
      let value = input.type === "checkbox" ? input.checked : input.value; if (input.type === "number") value = Number(value);
      const next = structuredClone(current.entries); next[index][input.dataset.field] = value;
      try { await patchProfile({ entries: next }); toast("Збережено"); } catch (_) {}
    });
    row.querySelector(".remove-entry").onclick = async () => { try { await patchProfile({ entries: current.entries.filter((_, position) => position !== index) }); } catch (_) {} };
    root.append(row);
  });
  $("visibleCount").textContent = entries.length ? `Показано ${visible} із ${entries.length}` : ""; $("emptyProfile").hidden = visible > 0;
}

function renderPicker() {
  const current = profile(); const root = $("pickerResults"); root.innerHTML = ""; const query = $("pickerSearch").value.trim().toLocaleLowerCase("uk");
  const used = new Set(current?.entries.map(entry => entry.item_id) || []);
  const items = state.catalog.filter(item => !used.has(item.id) && (!query || `${item.name} ${item.market_item_id || ""}`.toLocaleLowerCase("uk").includes(query)));
  items.forEach(item => {
    const label = document.createElement("label"); label.className = `picker-item${pickedIds.has(item.id) ? " selected" : ""}`;
    label.innerHTML = `<input type="checkbox" ${pickedIds.has(item.id) ? "checked" : ""}><img src="/api/catalog/${item.id}/icon"><span><b>${escapeHtml(item.name)}</b><small>ID ${item.market_item_id || "—"} · продаж ${money(item.market_sell)} · скупка ${money(item.market_buy)}</small></span>`;
    label.querySelector("input").onchange = event => { event.target.checked ? pickedIds.add(item.id) : pickedIds.delete(item.id); renderPicker(); };
    root.append(label);
  });
  if (!items.length) root.innerHTML = '<div class="empty">Вільних предметів не знайдено</div>';
  $("pickerHint").textContent = pickedIds.size ? `Вибрано: ${pickedIds.size}` : `Доступно: ${items.length}`; $("addPickedItems").disabled = pickedIds.size === 0;
}

async function refreshProfilePrices() {
  const current = profile(); if (!current) return; const button = $("refreshProfilePrices"); button.disabled = true; button.textContent = "Оновлюю…";
  try {
    const result = await api(`/api/profiles/${current.id}/market`, { method: "POST" });
    await load(); toast(result.errors.length ? `Оновлено, помилок: ${result.errors.length}` : `Оновлено товарів: ${result.updated.length}`, result.errors.length > 0);
  } catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "↻ Оновити ціни"; }
}

$("newProfile").onclick = async () => {
  const name = prompt("Назва нової стратегії:", `Стратегія ${state.profiles.length + 1}`); if (!name?.trim()) return;
  try { const created = await api("/api/profiles", { method: "POST", body: JSON.stringify({ name: name.trim() }) }); state.profiles.push(created); selectedProfileId = created.id; localStorage.setItem("pwcat-strategy", created.id); renderAll(); toast("Стратегію створено"); } catch (error) { toast(error.message, true); }
};
$("copyProfile").onclick = async () => { const current = profile(); if (!current) return; try { const copied = await api(`/api/profiles/${current.id}/copy`, { method: "POST" }); state.profiles.push(copied); selectedProfileId = copied.id; localStorage.setItem("pwcat-strategy", copied.id); renderAll(); toast("Копію створено"); } catch (error) { toast(error.message, true); } };
$("deleteProfile").onclick = async () => { const current = profile(); if (!current || !confirm(`Видалити стратегію «${current.name}»?`)) return; try { await api(`/api/profiles/${current.id}`, { method: "DELETE" }); state.profiles = state.profiles.filter(item => item.id !== current.id); selectedProfileId = state.profiles[0]?.id || null; renderAll(); toast("Стратегію видалено"); } catch (error) { toast(error.message, true); } };
$("scenarioName").onchange = async event => { const name = event.target.value.trim(); if (!name) return renderHeader(); try { await patchProfile({ name }); toast("Назву збережено"); } catch (_) {} };
$("entrySearch").oninput = renderEntries; $("entryFilter").onchange = renderEntries;
$("refreshProfilePrices").onclick = refreshProfilePrices;
$("openItemPicker").onclick = () => { if (!profile()) return; pickedIds = new Set(); $("pickerSearch").value = ""; renderPicker(); $("itemPicker").showModal(); };
$("closeItemPicker").onclick = () => $("itemPicker").close(); $("pickerSearch").oninput = renderPicker;
$("addPickedItems").onclick = async () => { const current = profile(); if (!current || !pickedIds.size) return; const additions = [...pickedIds].map(itemId => ({ item_id: itemId, enabled: true, sale_enabled: true, buy_enabled: true, max_owned: 100, sale_price_mode: "manual", buy_price_mode: "manual", sale_price: 20000, buy_price: 500, sale_adjustment: 0, buy_adjustment: 0 })); try { await patchProfile({ entries: [...current.entries, ...additions] }); $("itemPicker").close(); toast(`Додано предметів: ${additions.length}`); } catch (_) {} };

load();
