/**
 * TiqueTaque Sync — Settings screen logic.
 *
 * Secret fields are never sent by the API; they arrive as `<key>_is_set` flags.
 * Leaving one blank on save means "keep the stored value".
 */

const SECRET_FIELDS = ["tiquetaque_code", "telegram_bot_token", "slack_webhook_url"];

const NUMBER_FIELDS = [
    "work_hours_per_day",
    "lunch_duration_minutes",
    "lunch_warning_advance_minutes",
    "lunch_warning_final_minutes",
    "end_work_warning_advance_minutes",
    "end_work_warning_final_minutes",
    "continuous_work_limit_hours",
    "continuous_work_warning_advance_minutes",
    "continuous_work_warning_final_minutes",
    "poll_interval_seconds",
    "alert_ticker_interval_seconds",
    "port",
];

const TEXT_FIELDS = ["tiquetaque_email", "timezone", "telegram_chat_id"];
const BOOL_FIELDS = ["telegram_enabled", "slack_enabled", "open_browser_on_start"];

let envManagedKeys = [];

document.addEventListener("DOMContentLoaded", () => {
    loadSettings();
    loadAutostart();
    setupEventListeners();
});

// ==============================================================================
// Loading
// ==============================================================================
async function loadSettings() {
    try {
        const res = await fetch("/api/settings");
        if (!res.ok) throw new Error("HTTP " + res.status);
        applySettings(await res.json());
    } catch (e) {
        showToast("Não foi possível carregar as configurações", "error");
    }
}

function applySettings(payload) {
    const values = payload.settings || {};
    envManagedKeys = payload.env_managed_keys || [];

    [...NUMBER_FIELDS, ...TEXT_FIELDS].forEach((key) => {
        const el = document.getElementById(key);
        if (el && values[key] !== null && values[key] !== undefined) el.value = values[key];
    });

    BOOL_FIELDS.forEach((key) => {
        const el = document.getElementById(key);
        if (el) el.checked = Boolean(values[key]);
    });

    SECRET_FIELDS.forEach((key) => {
        const el = document.getElementById(key);
        if (!el) return;
        el.value = "";
        el.placeholder = values[`${key}_is_set`] ? "•••••••• (salvo)" : el.dataset.emptyPlaceholder || "";
    });

    const accountState = document.getElementById("account-state");
    if (accountState) {
        accountState.textContent = payload.is_configured ? "Configurada" : "Pendente";
    }

    const banner = document.getElementById("setup-banner");
    if (banner) banner.classList.toggle("hidden", Boolean(payload.is_configured));

    const configPath = document.getElementById("config-path");
    if (configPath) configPath.textContent = payload.config_file || "—";
    const dataPath = document.getElementById("data-path");
    if (dataPath) dataPath.textContent = payload.data_dir || "—";

    if (payload.autostart) renderAutostart(payload.autostart);

    renderEnvWarning();
    syncChannelFieldStates();
}

function renderEnvWarning() {
    const box = document.getElementById("env-warning");
    if (!box) return;

    if (!envManagedKeys.length) {
        box.classList.add("hidden");
        box.textContent = "";
        return;
    }

    box.classList.remove("hidden");
    box.innerHTML =
        "<strong>Atenção:</strong> estes campos estão definidos por variáveis de ambiente " +
        "(ou pelo arquivo <code>.env</code>) e continuarão prevalecendo sobre o que for salvo aqui: " +
        `<code>${envManagedKeys.join("</code>, <code>")}</code>.`;

    envManagedKeys.forEach((key) => {
        const el = document.getElementById(key);
        if (el) {
            el.disabled = true;
            el.title = "Definido por variável de ambiente";
        }
    });
}

async function loadAutostart() {
    try {
        const res = await fetch("/api/autostart");
        if (res.ok) renderAutostart(await res.json());
    } catch (e) {
        /* the settings payload already carries a copy */
    }
}

function renderAutostart(state) {
    const toggle = document.getElementById("autostart-toggle");
    const detail = document.getElementById("autostart-detail");

    if (toggle) {
        toggle.checked = Boolean(state.enabled);
        toggle.disabled = !state.supported;
    }

    if (detail) {
        if (!state.supported) {
            detail.textContent = state.detail || "Não suportado nesta plataforma.";
        } else if (state.enabled && state.location) {
            detail.textContent = `${state.mechanism} — ${state.location}`;
        } else {
            detail.textContent = `${state.mechanism} — desativado.`;
        }
    }
}

// ==============================================================================
// Events
// ==============================================================================
function setupEventListeners() {
    const form = document.getElementById("settings-form");
    if (form) form.addEventListener("submit", onSubmit);

    const reset = document.getElementById("btn-reset");
    if (reset) {
        reset.addEventListener("click", () => {
            loadSettings();
            showToast("Alterações descartadas", "info");
        });
    }

    document.querySelectorAll("[data-test-channel]").forEach((btn) => {
        btn.addEventListener("click", () => testChannel(btn.dataset.testChannel, btn));
    });

    ["telegram_enabled", "slack_enabled"].forEach((key) => {
        const el = document.getElementById(key);
        if (el) el.addEventListener("change", syncChannelFieldStates);
    });

    const autostartToggle = document.getElementById("autostart-toggle");
    if (autostartToggle) autostartToggle.addEventListener("change", onAutostartToggle);
}

function syncChannelFieldStates() {
    document.querySelectorAll(".channel-fields").forEach((block) => {
        const source = document.getElementById(block.dataset.for);
        block.classList.toggle("collapsed", !(source && source.checked));
    });
}

async function onSubmit(event) {
    event.preventDefault();

    const btn = document.getElementById("btn-save");
    const payload = collectPayload();

    btn.disabled = true;
    btn.textContent = "Salvando...";

    try {
        const res = await fetch("/api/settings", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        const data = await res.json();

        if (!res.ok) {
            showToast(describeError(data), "error");
            return;
        }

        applySettings(data);
        showToast(
            data.restart_required
                ? "Configurações salvas. Reinicie o app para aplicar host/porta."
                : "Configurações salvas e aplicadas!",
            "success"
        );
    } catch (e) {
        showToast("Erro de rede ao salvar", "error");
    } finally {
        btn.disabled = false;
        btn.textContent = "Salvar configurações";
    }
}

function collectPayload() {
    const payload = {};

    NUMBER_FIELDS.forEach((key) => {
        const el = document.getElementById(key);
        if (!el || el.disabled || el.value === "") return;
        const parsed = Number(el.value);
        if (!Number.isNaN(parsed)) payload[key] = parsed;
    });

    TEXT_FIELDS.forEach((key) => {
        const el = document.getElementById(key);
        if (el && !el.disabled) payload[key] = el.value.trim();
    });

    BOOL_FIELDS.forEach((key) => {
        const el = document.getElementById(key);
        if (el && !el.disabled) payload[key] = el.checked;
    });

    // Blank secret inputs are omitted so the backend keeps the stored value.
    SECRET_FIELDS.forEach((key) => {
        const el = document.getElementById(key);
        if (el && !el.disabled && el.value.trim()) payload[key] = el.value.trim();
    });

    return payload;
}

function describeError(data) {
    if (!data || !data.detail) return "Não foi possível salvar";
    if (typeof data.detail === "string") return data.detail;
    if (Array.isArray(data.detail)) {
        const first = data.detail[0];
        const field = (first.loc || []).slice(-1)[0];
        return `Campo inválido: ${field} — ${first.msg}`;
    }
    return "Não foi possível salvar";
}

async function onAutostartToggle(event) {
    const toggle = event.target;
    const desired = toggle.checked;
    toggle.disabled = true;

    try {
        const res = await fetch("/api/autostart", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ enabled: desired }),
        });
        const data = await res.json();

        if (!res.ok) {
            toggle.checked = !desired;
            showToast(data.detail || "Falha ao alterar o autostart", "error");
            return;
        }

        renderAutostart(data);
        showToast(desired ? "O app iniciará junto com o sistema" : "Autostart desativado", "success");
    } catch (e) {
        toggle.checked = !desired;
        showToast("Erro ao configurar o autostart", "error");
    } finally {
        toggle.disabled = false;
    }
}

async function testChannel(channel, btn) {
    const original = btn.textContent;
    btn.disabled = true;
    btn.textContent = "Enviando...";

    try {
        const res = await fetch(`/api/test-notification?channel=${channel}`, { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Teste enviado para o ${channel}!`, "success");
        } else {
            showToast(data.detail || `Falha ao testar ${channel}. Salve as credenciais antes.`, "error");
        }
    } catch (e) {
        showToast(`Erro de conexão ao testar ${channel}`, "error");
    } finally {
        btn.disabled = false;
        btn.textContent = original;
    }
}

// ==============================================================================
// Toasts (same behaviour as the dashboard)
// ==============================================================================
function showToast(message, type = "info") {
    const container = document.getElementById("toast-container");
    if (!container) return;

    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateX(100%)";
        setTimeout(() => toast.remove(), 300);
    }, 4000);
}
