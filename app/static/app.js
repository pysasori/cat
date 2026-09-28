const $ = (id) => document.getElementById(id);
let state = null;
let selectedCharacterId = localStorage.getItem("pwcat-character") || null;
let picking = false;
let savingConfig = false;
let pickedIds = new Set();

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { "Content-Type": "application/json", ...(options.headers || {}) }, ...options });
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try { detail = (await response.json()).detail || detail; } catch (_) {}
    throw new Error(detail);
  }
  if (response.status === 204) return null;
  return response.json();
}

function toast(message, error = false) {
  const node = $("toast"); node.textContent = message; node.className = `toast show${error ? " error" : ""}`;
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.className = "toast", 3200);
}

function escapeHtml(value) { const node = document.createElement("div"); node.textContent = value ?? ""; return node.innerHTML; }
function money(value) { return value == null ? "—" : Math.round(Number(value)).toLocaleString("uk-UA"); }
function selectedHwnd() { return $("window") ? (Number($("window").value) || null) : (state?.config.window_hwnd || null); }
function character() { return state?.characters.find(item => item.id === selectedCharacterId) || null; }
function profile() { const current = character(); return state?.profiles.find(item => item.id === current?.profile_id) || null; }
function catalogItem(id) { return state.catalog.find(item => item.id === id); }
function initials(value) { const words = (value || "?").trim().split(/\s+/); return words.slice(0, 2).map(word => word[0]?.toUpperCase()).join("") || "?"; }

async function load() {
  try {
    state = await api("/api/state");
    if (!state.characters.some(item => item.id === selectedCharacterId)) selectedCharacterId = state.characters[0]?.id || null;
    if (selectedCharacterId) localStorage.setItem("pwcat-character", selectedCharacterId);
    renderAll();
  } catch (error) { toast(error.message, true); }
}

function renderAll() {
  renderWindows(); renderConfig(); renderCharacterList(); renderCharacterHeader(); renderEntries(); renderCatalog(); renderJob(); reloadFrame();
}

function renderWindows() {
  if (!$("window")) return;
  const select = $("window"); const current = state.config.window_hwnd; select.innerHTML = "";
  state.windows.forEach((window, index) => {
    const option = document.createElement("option"); option.value = window.hwnd;
    option.textContent = `${window.title} · PID ${window.pid}`;
    option.selected = current ? window.hwnd === current : index === state.config.window_index; select.append(option);
  });
  if (!state.windows.length) select.innerHTML = '<option value="">Вікна PW не знайдено</option>';
}

function renderConfig() { if ($("inputMode")) $("inputMode").value = state.config.input_mode; if ($("threshold")) $("threshold").value = state.config.match_threshold; }

async function saveConfig() {
  if (!state || savingConfig || !$("window")) return;
  savingConfig = true;
  const next = structuredClone(state.config); next.window_hwnd = selectedHwnd(); next.input_mode = $("inputMode").value;
  next.match_threshold = Number($("threshold").value); next.offline_trade = false;
  try { state.config = await api("/api/config", { method: "PUT", body: JSON.stringify(next) }); }
  catch (error) { toast(error.message, true); }
  finally { savingConfig = false; }
}

function renderCharacterList() {
  const root = $("characterList"); root.innerHTML = "";
  state.characters.forEach(item => {
    const assigned = state.profiles.find(value => value.id === item.profile_id); const active = item.id === selectedCharacterId;
    const button = document.createElement("button"); button.className = `profile-card${active ? " active" : ""}`;
    button.innerHTML = `<span class="profile-card-avatar">${escapeHtml(initials(item.character_name))}</span><span class="profile-card-copy"><b>${escapeHtml(item.character_name)}</b><small>${escapeHtml(assigned?.name || "Без сценарію")}</small></span><span class="profile-card-count">${assigned?.entries.filter(entry => entry.enabled).length || 0}</span>`;
    button.onclick = () => { selectedCharacterId = item.id; localStorage.setItem("pwcat-character", item.id); renderCharacterList(); renderCharacterHeader(); renderEntries(); renderJob(); };
    root.append(button);
  });
  if (!state.characters.length) root.innerHTML = '<div class="sidebar-empty">Додайте першого персонажа</div>';
}

function renderCharacterHeader() {
  const current = character(); const scenario = profile(); const disabled = !current;
  ["characterName", "characterShopName", "characterScenario", "bindWindow", "deleteCharacter"].forEach(id => $(id).disabled = disabled);
  ["scenarioName", "copyProfile", "deleteProfile", "openItemPicker", "refreshProfilePrices"].forEach(id => $(id).disabled = !scenario);
  $("characterName").value = current?.character_name || ""; $("characterShopName").value = current?.shop_name || "";
  const select = $("characterScenario"); select.innerHTML = state.profiles.map(item => `<option value="${item.id}">${escapeHtml(item.name)}</option>`).join(""); if (current) select.value = current.profile_id;
  $("scenarioName").value = scenario?.name || "";
  $("characterTitle").textContent = current?.character_name || "Оберіть персонажа";
  $("characterSubtitle").textContent = scenario ? `Сценарій: ${scenario.name} · ${scenario.entries.length} предметів` : "Сценарій не призначено";
  $("characterAvatar").textContent = initials(current?.character_name);
  $("runProfileName").textContent = current ? `· ${current.character_name}` : "";
  const assigned = scenario ? state.characters.filter(item => item.profile_id === scenario.id) : [];
  $("scenarioUsage").textContent = scenario ? `Використовують: ${assigned.map(item => item.character_name).join(", ") || "ніхто"}` : "";
  const bound = current?.window_hwnd ? state.windows.find(item => item.hwnd === current.window_hwnd) : null;
  $("profileBinding").textContent = current ? `Вікно: ${bound ? `${bound.title} · PID ${bound.pid}` : (current.window_hwnd ? `HWND ${current.window_hwnd} зараз не знайдено` : "використовується вікно сесії")}` : "";
}

async function patchCharacter(patch, render = true) {
  const current = character(); if (!current) return;
  try {
    const updated = await api(`/api/characters/${current.id}`, { method: "PATCH", body: JSON.stringify(patch) }); Object.assign(current, updated);
    if (render) { renderCharacterList(); renderCharacterHeader(); renderEntries(); renderJob(); }
  } catch (error) { toast(error.message, true); }
}

async function patchProfile(patch, render = true) {
  const current = profile(); if (!current) return;
  try {
    const updated = await api(`/api/profiles/${current.id}`, { method: "PATCH", body: JSON.stringify(patch) });
    Object.assign(current, updated);
    if (render) { renderCharacterList(); renderCharacterHeader(); renderEntries(); }
  } catch (error) { toast(error.message, true); }
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
      const next = structuredClone(current.entries); next[index][input.dataset.field] = value; await patchProfile({ entries: next }); toast("Збережено");
    });
    row.querySelector(".remove-entry").onclick = async () => { const next = current.entries.filter((_, position) => position !== index); await patchProfile({ entries: next }); };
    root.append(row);
  });
  $("visibleCount").textContent = entries.length ? `Показано ${visible} із ${entries.length}` : ""; $("emptyProfile").hidden = visible > 0;
}

function renderCatalog() {
  const root = $("catalog"); if (!root) return; root.innerHTML = ""; const query = $("catalogSearch").value.trim().toLocaleLowerCase("uk");
  const items = state.catalog.filter(item => !query || `${item.name} ${item.market_item_id || ""}`.toLocaleLowerCase("uk").includes(query));
  $("catalogCount").textContent = state.catalog.length; $("emptyCatalog").hidden = items.length > 0;
  items.forEach(item => {
    const row = document.createElement("tr");
    row.innerHTML = `<td><div class="item-cell"><img src="/api/catalog/${item.id}/icon?t=${encodeURIComponent(item.market_updated_at || "")}"><input data-name value="${escapeHtml(item.name)}"></div></td><td><input class="market-id-input" data-market-id type="number" min="1" placeholder="item ID" value="${item.market_item_id || ""}"></td><td>${money(item.market_sell)}</td><td>${money(item.market_buy)}</td><td><button class="ghost sync" ${item.market_item_id ? "" : "disabled"}>Оновити</button></td><td><button class="icon-button danger-text delete">×</button></td>`;
    row.querySelector("[data-name]").onchange = async event => { try { Object.assign(item, await api(`/api/catalog/${item.id}`, { method: "PATCH", body: JSON.stringify({ name: event.target.value }) })); renderCatalog(); renderEntries(); } catch (error) { toast(error.message, true); } };
    row.querySelector("[data-market-id]").onchange = async event => { try { Object.assign(item, await api(`/api/catalog/${item.id}`, { method: "PATCH", body: JSON.stringify({ market_item_id: Number(event.target.value) || null }) })); renderCatalog(); renderEntries(); } catch (error) { toast(error.message, true); } };
    row.querySelector(".sync").onclick = async () => { try { Object.assign(item, await api(`/api/catalog/${item.id}/market`, { method: "POST" })); renderCatalog(); renderEntries(); toast("Ціну оновлено"); } catch (error) { toast(error.message, true); } };
    row.querySelector(".delete").onclick = async () => { if (!confirm(`Видалити «${item.name}» з бази та всіх сценаріїв?`)) return; try { await api(`/api/catalog/${item.id}`, { method: "DELETE" }); state.catalog = state.catalog.filter(value => value.id !== item.id); state.profiles.forEach(value => value.entries = value.entries.filter(entry => entry.item_id !== item.id)); renderAll(); } catch (error) { toast(error.message, true); } };
    root.append(row);
  });
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

function openPicker() { if (!profile()) return; pickedIds = new Set(); $("pickerSearch").value = ""; renderPicker(); $("itemPicker").showModal(); }

async function addPickedItems() {
  const current = profile(); if (!current || !pickedIds.size) return;
  const additions = [...pickedIds].map(itemId => ({ item_id: itemId, enabled: true, sale_enabled: true, buy_enabled: true, max_owned: 100, sale_price_mode: "manual", buy_price_mode: "manual", sale_price: 20000, buy_price: 500, sale_adjustment: 0, buy_adjustment: 0 }));
  await patchProfile({ entries: [...current.entries, ...additions] }); $("itemPicker").close(); toast(`Додано предметів: ${additions.length}`);
}

function reloadFrame() { const hwnd = selectedHwnd(); if (hwnd && $("gameFrame")) $("gameFrame").src = `/api/frame?hwnd=${hwnd}&t=${Date.now()}`; }
function beginPick() { if (!$("newName").value.trim()) return toast("Введіть назву предмета", true); picking = true; $("frameWrap").classList.add("picking"); $("pickState").textContent = "Клацніть предмет у рюкзаку…"; }
async function frameClick(event) {
  if (!picking) return; const image = $("gameFrame"); const rect = image.getBoundingClientRect();
  const x = Math.round((event.clientX - rect.left) * image.naturalWidth / rect.width); const y = Math.round((event.clientY - rect.top) * image.naturalHeight / rect.height);
  try { const item = await api("/api/catalog", { method: "POST", body: JSON.stringify({ name: $("newName").value.trim(), market_item_id: Number($("newMarketId").value) || null, x, y }) }); state.catalog.push(item); $("newName").value = ""; $("newMarketId").value = ""; renderCatalog(); toast(`Додано: ${item.name}`); }
  catch (error) { toast(error.message, true); }
  picking = false; $("frameWrap").classList.remove("picking"); $("pickState").textContent = "Після натискання виберіть комірку предмета на кадрі.";
}

async function importMarket() {
  const text = $("marketImport").value.trim(); const urlMatch = text.match(/[?&]item_id=(\d+)/); const plainMatch = text.match(/^\d+$/); const itemId = Number(urlMatch?.[1] || plainMatch?.[0]);
  if (!itemId) return toast("Вкажіть item ID або посилання ComebackPW", true);
  try { const item = await api("/api/catalog/import-market", { method: "POST", body: JSON.stringify({ item_id: itemId }) }); const index = state.catalog.findIndex(value => value.id === item.id); if (index >= 0) state.catalog[index] = item; else state.catalog.push(item); $("marketImport").value = ""; renderCatalog(); renderEntries(); toast(`Імпортовано: ${item.name}`); }
  catch (error) { toast(error.message, true); }
}

async function refreshProfilePrices() {
  const current = profile(); if (!current) return;
  const button = $("refreshProfilePrices"); button.disabled = true; button.textContent = "Оновлюю…";
  try { const result = await api(`/api/profiles/${current.id}/market`, { method: "POST" }); result.updated.forEach(updated => { const index = state.catalog.findIndex(item => item.id === updated.id); if (index >= 0) state.catalog[index] = updated; }); renderCatalog(); renderEntries(); toast(`Оновлено: ${result.updated.length}${result.errors.length ? `, помилок: ${result.errors.length}` : ""}`, result.updated.length === 0); }
  catch (error) { toast(error.message, true); }
  finally { button.disabled = false; button.textContent = "↻ Оновити ціни"; }
}

function renderJob() {
  const job = state.job; const pill = $("statusPill"); pill.className = `status ${job.error ? "error" : job.running ? "running" : job.stage === "done" ? "done" : "idle"}`;
  pill.querySelector("span").textContent = job.error ? "помилка" : job.running ? job.stage : job.stage === "done" ? "готово" : "очікування";
  $("log").textContent = job.log.length ? job.log.join("\n") : "Очікування…";
  $("preview").innerHTML = job.preview.map(item => `<span><b>${escapeHtml(item.name)}</b> · ${item.side === "buy" ? "скупка" : "продаж"} ${item.quantity} · ${money(item.price)}${item.owned !== undefined ? ` · є ${item.owned}/${item.maximum}` : ""}</span>`).join("");
  $("run").disabled = job.running || !character() || !profile(); $("dryRun").disabled = job.running || !character() || !profile(); $("stop").disabled = !job.running;
}

async function run(dryRun) { const current = character(); if (!current) return toast("Оберіть персонажа", true); try { state.job = await api("/api/run", { method: "POST", body: JSON.stringify({ dry_run: dryRun, character_id: current.id }) }); renderJob(); } catch (error) { toast(error.message, true); } }
async function poll() { try { const fresh = await api("/api/state"); state.job = fresh.job; renderJob(); } catch (_) {} }

if ($("refresh")) $("refresh").onclick = load;
$("newCharacter").onclick = async () => {
  const name = prompt("Нік нового персонажа:", ""); if (!name?.trim()) return;
  try {
    let assigned = profile() || state.profiles[0];
    if (!assigned) { assigned = await api("/api/profiles", { method: "POST", body: JSON.stringify({ name: "Новий сценарій" }) }); state.profiles.push(assigned); }
    const created = await api("/api/characters", { method: "POST", body: JSON.stringify({ character_name: name.trim(), profile_id: assigned.id, window_hwnd: selectedHwnd(), window_index: 0, shop_name: name.trim() }) });
    state.characters.push(created); selectedCharacterId = created.id; localStorage.setItem("pwcat-character", created.id); renderAll();
  } catch (error) { toast(error.message, true); }
};
$("deleteCharacter").onclick = async () => { const current = character(); if (!current || !confirm(`Видалити персонажа «${current.character_name}»? Сценарій залишиться.`)) return; try { await api(`/api/characters/${current.id}`, { method: "DELETE" }); state.characters = state.characters.filter(item => item.id !== current.id); selectedCharacterId = state.characters[0]?.id || null; renderAll(); } catch (error) { toast(error.message, true); } };
$("newProfile").onclick = async () => { const current = character(); if (!current) return toast("Спочатку додайте персонажа", true); const name = prompt("Назва нового сценарію:", `Сценарій ${state.profiles.length + 1}`); if (!name?.trim()) return; try { const created = await api("/api/profiles", { method: "POST", body: JSON.stringify({ name: name.trim() }) }); state.profiles.push(created); await patchCharacter({ profile_id: created.id }); toast("Новий сценарій призначено персонажу"); } catch (error) { toast(error.message, true); } };
$("copyProfile").onclick = async () => { const current = profile(); const owner = character(); if (!current || !owner) return; try { const copied = await api(`/api/profiles/${current.id}/copy`, { method: "POST" }); state.profiles.push(copied); await patchCharacter({ profile_id: copied.id }); toast("Копію створено й призначено цьому персонажу"); } catch (error) { toast(error.message, true); } };
$("deleteProfile").onclick = async () => {
  const current = profile(); const owner = character(); if (!current || !owner) return;
  const users = state.characters.filter(item => item.profile_id === current.id); const replacement = state.profiles.find(item => item.id !== current.id);
  if (users.length > 1) return toast(`Спочатку перепризначте інших персонажів: ${users.filter(item => item.id !== owner.id).map(item => item.character_name).join(", ")}`, true);
  if (!replacement) return toast("Спочатку створіть інший сценарій", true);
  if (!confirm(`Видалити сценарій «${current.name}»? ${owner.character_name} буде переключено на «${replacement.name}».`)) return;
  try { const updated = await api(`/api/characters/${owner.id}`, { method: "PATCH", body: JSON.stringify({ profile_id: replacement.id }) }); Object.assign(owner, updated); await api(`/api/profiles/${current.id}`, { method: "DELETE" }); state.profiles = state.profiles.filter(item => item.id !== current.id); renderAll(); }
  catch (error) { toast(error.message, true); }
};
$("scenarioName").onchange = event => patchProfile({ name: event.target.value });
$("characterName").onchange = event => patchCharacter({ character_name: event.target.value }); $("characterShopName").onchange = event => patchCharacter({ shop_name: event.target.value }); $("characterScenario").onchange = event => patchCharacter({ profile_id: event.target.value });
$("bindWindow").onclick = async () => { await patchCharacter({ window_hwnd: selectedHwnd(), window_index: 0 }); toast("Вікно закріплено за персонажем"); };
$("entrySearch").oninput = renderEntries; $("entryFilter").onchange = renderEntries; if ($("catalogSearch")) $("catalogSearch").oninput = renderCatalog; if ($("importMarket")) $("importMarket").onclick = importMarket; $("refreshProfilePrices").onclick = refreshProfilePrices;
$("openItemPicker").onclick = openPicker; $("closeItemPicker").onclick = () => $("itemPicker").close(); $("pickerSearch").oninput = renderPicker; $("addPickedItems").onclick = addPickedItems;
if ($("reloadFrame")) $("reloadFrame").onclick = reloadFrame; if ($("pickIcon")) $("pickIcon").onclick = beginPick; if ($("gameFrame")) $("gameFrame").onclick = frameClick;
$("dryRun").onclick = () => run(true); $("run").onclick = () => run(false); $("stop").onclick = async () => { state.job = await api("/api/stop", { method: "POST" }); renderJob(); };
[$("window"), $("inputMode"), $("threshold")].filter(Boolean).forEach(node => node.onchange = async () => { await saveConfig(); if (node.id === "window") reloadFrame(); });
setInterval(poll, 1000); load();
