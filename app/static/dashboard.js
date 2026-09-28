const $ = (id) => document.getElementById(id);
let state = null;
let polling = false;

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
  clearTimeout(toast.timer); toast.timer = setTimeout(() => node.className = "toast", 3600);
}

function escapeHtml(value) { const node = document.createElement("div"); node.textContent = value ?? ""; return node.innerHTML; }
function money(value) { return value == null ? "—" : Math.round(Number(value)).toLocaleString("uk-UA"); }
function profileFor(character) { return state.profiles.find(item => item.id === character.profile_id); }
function readiness(character) {
  return [
    [Boolean(character.profile_id && profileFor(character)), "стратегія"],
    [Boolean(character.launcher_file), "BAT"],
    [Boolean(character.has_password), "пароль"],
    [Boolean(character.offline_trade), "офлайн"],
    [Boolean(character.queue_enabled), "у черзі"],
  ];
}
function queueReady(character) { return readiness(character).every(([ok]) => ok); }
function statusLabel(status) { return ({ idle: "ще не запускався", running: "виконується", done: "успішно", checked: "перевірено", warning: "частково", stopped: "зупинено", error: "помилка" })[status] || status || "очікує"; }
function formattedTime(value) { if (!value) return ""; const date = new Date(value); return Number.isNaN(date.valueOf()) ? "" : date.toLocaleString("uk-UA", { dateStyle: "short", timeStyle: "short" }); }

async function load(quiet = false) {
  if (polling) return; polling = true;
  try { state = await api("/api/state"); render(); }
  catch (error) { if (!quiet) toast(error.message, true); }
  finally { polling = false; }
}

function render() {
  renderSummary(); renderQueue(); renderCharacters(); renderJob();
}

function renderSummary() {
  const ready = state.characters.filter(queueReady).length;
  const errors = state.characters.filter(item => item.last_run_status === "error");
  const warnings = state.characters.filter(item => item.last_run_status === "warning");
  const total = state.characters.reduce((sum, item) => sum + Number(item.total_value || 0), 0);
  $("characterCount").textContent = state.characters.length; $("readyCount").textContent = ready;
  $("totalFunds").textContent = money(total); $("errorCount").textContent = errors.length;
  $("errorMetric").classList.toggle("metric-error", errors.length > 0);
  const alert = $("activeAlert");
  const globalError = state.queue.error || state.job.error;
  if (globalError) { alert.hidden = false; alert.innerHTML = `<b>Помилка:</b> ${escapeHtml(globalError)}`; }
  else if (errors.length || warnings.length) { const attention = [...errors, ...warnings]; alert.hidden = false; alert.innerHTML = `<b>Потрібна увага:</b> ${attention.map(item => `${escapeHtml(item.character_name)} — ${escapeHtml(item.last_run_message || "помилка")}`).join(" · ")}`; }
  else alert.hidden = true;
}

function renderQueue() {
  const queue = state.queue;
  $("queueTitle").textContent = queue.running ? `${queue.current_character_name || "Підготовка"} · ${queue.message || queue.stage}` : (queue.message || "Черга не запущена");
  $("queueCounter").textContent = `${queue.completed} / ${queue.total}`;
  const percent = queue.total ? Math.max(0, Math.min(100, queue.completed / queue.total * 100)) : 0;
  $("queueProgress").style.setProperty("--progress", `${percent}%`);
  $("queueLog").textContent = queue.log.length ? queue.log.join("\n") : "Очікування…";
  const queued = state.characters.filter(item => item.queue_enabled);
  $("startAll").disabled = queue.running || state.job.running || !queued.length || queued.some(item => !queueReady(item));
  $("stopAll").disabled = !queue.running && !state.job.running;
}

function renderCharacters() {
  const root = $("characterGrid"); root.innerHTML = "";
  state.characters.forEach(character => {
    const strategy = profileFor(character); const checks = readiness(character); const failed = checks.filter(([ok]) => !ok).map(([, label]) => label);
    const status = character.last_run_status || "idle"; const card = document.createElement("article");
    card.className = `character-run-card status-${status}`;
    const enabledEntries = strategy?.entries.filter(entry => entry.enabled).length || 0;
    card.innerHTML = `
      <div class="character-card-head"><div><p class="eyebrow">${character.queue_enabled ? "У ЧЕРЗІ" : "ПОЗА ЧЕРГОЮ"}</p><h3>${escapeHtml(character.character_name)}</h3><p>${escapeHtml(strategy?.name || "Стратегію не призначено")} · ${enabledEntries} товарів</p></div><span class="run-state ${status}">${escapeHtml(statusLabel(status))}</span></div>
      <div class="readiness-list">${checks.map(([ok, label]) => `<span class="${ok ? "ok" : "missing"}"><i></i>${escapeHtml(label)}</span>`).join("")}</div>
      <div class="card-finance"><span>Вільні<b>${money(character.free_funds)}</b></span><span>На продажу<b>${money(character.sale_value)}</b></span><span>Разом<b>${money(character.total_value)}</b></span></div>
      <div class="last-result ${status === "error" ? "error" : status === "warning" ? "warning" : ""}"><b>${escapeHtml(character.last_run_message || (failed.length ? `Не готово: ${failed.join(", ")}` : "Готовий до запуску"))}</b><small>${formattedTime(character.last_run_at)}</small></div>
      <div class="card-actions"><button class="ghost launch">Запустити клієнт</button><button class="secondary check">Перевірити</button><button class="primary run">Перевиставити</button><a class="ghost" href="/windows">Налаштувати</a></div>`;
    card.querySelector(".launch").onclick = () => launchCharacter(character);
    card.querySelector(".check").onclick = () => runCharacter(character, true);
    card.querySelector(".run").onclick = () => runCharacter(character, false);
    card.querySelectorAll("button").forEach(button => button.disabled = state.job.running || state.queue.running);
    root.append(card);
  });
  if (!state.characters.length) root.innerHTML = '<div class="dashboard-empty"><h3>Персонажів ще немає</h3><p>Додайте персонажа, виберіть йому стратегію та створіть BAT у розділі «Вікна».</p><a class="primary" href="/windows">Додати персонажа</a></div>';
}

function renderJob() {
  const job = state.job; const pill = $("statusPill");
  pill.className = `status ${job.error || state.queue.error ? "error" : job.running || state.queue.running ? "running" : job.stage === "done" || state.queue.stage === "done" ? "done" : "idle"}`;
  pill.querySelector("span").textContent = job.error || state.queue.error ? "помилка" : job.running || state.queue.running ? "працює" : "готово";
  $("jobTitle").textContent = job.error ? `Помилка: ${job.error}` : job.running ? (job.message || job.stage) : (job.message || "Бот очікує");
  $("jobLog").textContent = job.log.length ? job.log.join("\n") : "Очікування…";
  $("jobPreview").innerHTML = job.preview.map(item => `<span><b>${escapeHtml(item.name)}</b> · ${item.side === "buy" ? "скупка" : "продаж"} ${item.quantity} · ${money(item.price)}${item.owned !== undefined ? ` · є ${item.owned}/${item.maximum}` : ""}</span>`).join("");
  $("stopJob").disabled = !job.running;
}

async function launchCharacter(character) {
  try { await api(`/api/characters/${character.id}/launch`, { method: "POST" }); toast(`${character.character_name}: клієнт запущено`); }
  catch (error) { toast(error.message, true); }
}

async function runCharacter(character, dryRun) {
  try { state.job = await api("/api/run", { method: "POST", body: JSON.stringify({ dry_run: dryRun, character_id: character.id }) }); render(); toast(dryRun ? "Перевірку запущено" : "Перевиставлення запущено"); }
  catch (error) { toast(error.message, true); await load(true); }
}

$("refreshDashboard").onclick = () => load();
$("startAll").onclick = async () => { try { state.queue = await api("/api/queue/start", { method: "POST", body: JSON.stringify({ character_ids: null }) }); render(); toast("Чергу запущено"); } catch (error) { toast(error.message, true); await load(true); } };
$("stopAll").onclick = async () => { try { if (state.queue.running) state.queue = await api("/api/queue/stop", { method: "POST" }); if (state.job.running) state.job = await api("/api/stop", { method: "POST" }); render(); } catch (error) { toast(error.message, true); } };
$("stopJob").onclick = async () => { try { state.job = await api("/api/stop", { method: "POST" }); render(); } catch (error) { toast(error.message, true); } };

setInterval(() => load(true), 1000);
load();
