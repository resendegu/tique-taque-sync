/**
 * TiqueTaque Sync — Frontend Dashboard Logic
 */

let countdownTimerInterval = null;
let currentRemainingSeconds = null;

// ==============================================================================
// 1. Clock & UI Initialization
// ==============================================================================
document.addEventListener("DOMContentLoaded", () => {
    initClock();
    fetchStatus();
    fetchConfig();
    setupEventListeners();

    // Auto-refresh data every 30s
    setInterval(fetchStatus, 30000);
});

function initClock() {
    const timeEl = document.getElementById("live-time");
    const dateEl = document.getElementById("live-date");

    function update() {
        const now = new Date();
        const timeStr = now.toLocaleTimeString("pt-BR", { hour12: false });
        const dateStr = now.toLocaleDateString("pt-BR", {
            weekday: "long",
            day: "numeric",
            month: "long",
            year: "numeric"
        });

        if (timeEl) timeEl.textContent = timeStr;
        if (dateEl) dateEl.textContent = dateStr.charAt(0).toUpperCase() + dateStr.slice(1);
    }

    update();
    setInterval(update, 1000);
}

// ==============================================================================
// 2. Event Listeners
// ==============================================================================
function setupEventListeners() {
    // Sync Button
    const btnSync = document.getElementById("btn-sync");
    if (btnSync) {
        btnSync.addEventListener("click", async () => {
            const icon = document.getElementById("sync-icon");
            if (icon) icon.classList.add("spinning");
            btnSync.disabled = true;

            try {
                const res = await fetch("/api/sync", { method: "POST" });
                const data = await res.json();
                if (res.ok) {
                    showToast("Sincronização com o TiqueTaque concluída!", "success");
                    await fetchStatus();
                } else {
                    showToast(`Falha na sincronização: ${data.detail || "Erro"}`, "error");
                }
            } catch (err) {
                showToast("Erro de rede ao sincronizar", "error");
            } finally {
                if (icon) icon.classList.remove("spinning");
                btnSync.disabled = false;
            }
        });
    }

    // Test Notification Buttons
    const btnTestTg = document.getElementById("btn-test-telegram");
    if (btnTestTg) {
        btnTestTg.addEventListener("click", () => testNotification("telegram"));
    }

    const btnTestSlack = document.getElementById("btn-test-slack");
    if (btnTestSlack) {
        btnTestSlack.addEventListener("click", () => testNotification("slack"));
    }

    // Clock-In Modal
    const modal = document.getElementById("clock-in-modal");
    const btnOpenModal = document.getElementById("btn-clock-in-modal");
    const btnCloseModal = document.getElementById("btn-close-modal");
    const btnCancelModal = document.getElementById("btn-cancel-clock-in");
    const btnConfirmClockIn = document.getElementById("btn-confirm-clock-in");
    const modalTime = document.getElementById("modal-current-time");

    function openModal() {
        const now = new Date();
        if (modalTime) {
            modalTime.textContent = now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", hour12: false });
        }
        modal.classList.remove("hidden");
    }

    function closeModal() {
        modal.classList.add("hidden");
    }

    if (btnOpenModal) btnOpenModal.addEventListener("click", openModal);
    if (btnCloseModal) btnCloseModal.addEventListener("click", closeModal);
    if (btnCancelModal) btnCancelModal.addEventListener("click", closeModal);

    if (btnConfirmClockIn) {
        btnConfirmClockIn.addEventListener("click", async () => {
            const customTimeInput = document.getElementById("input-custom-time");
            const customTime = customTimeInput ? customTimeInput.value : null;

            btnConfirmClockIn.disabled = true;
            btnConfirmClockIn.textContent = "Registrando...";

            try {
                const payload = customTime ? { time_str: customTime } : {};
                const res = await fetch("/api/clock-in", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                if (res.ok) {
                    showToast("Ponto registrado com sucesso no TiqueTaque!", "success");
                    closeModal();
                    await fetchStatus();
                } else {
                    showToast(`Erro ao bater ponto: ${data.detail || "Erro inesperado"}`, "error");
                }
            } catch (err) {
                showToast("Erro ao conectar com a API", "error");
            } finally {
                btnConfirmClockIn.disabled = false;
                btnConfirmClockIn.textContent = "Registrar Ponto";
            }
        });
    }
}

// ==============================================================================
// 3. API Data Fetching & Rendering
// ==============================================================================
async function fetchStatus() {
    try {
        const res = await fetch("/api/status");
        if (!res.ok) throw new Error("Status query failed");
        const data = await res.json();
        renderDashboard(data);
    } catch (e) {
        console.error("fetchStatus error:", e);
    }
}

async function fetchConfig() {
    try {
        const res = await fetch("/api/config");
        if (!res.ok) return;
        const config = await res.json();

        // Update channel status indicators
        const tgEl = document.getElementById("telegram-status");
        if (tgEl) {
            tgEl.textContent = config.telegram_enabled ? "Ativo" : "Desativado";
            tgEl.className = config.telegram_enabled ? "channel-status" : "channel-status disabled";
        }

        const slackEl = document.getElementById("slack-status");
        if (slackEl) {
            slackEl.textContent = config.slack_enabled ? "Ativo" : "Desativado";
            slackEl.className = config.slack_enabled ? "channel-status" : "channel-status disabled";
        }

        const scheduleEl = document.getElementById("schedule-desc");
        if (scheduleEl && config.schedule_description) {
            scheduleEl.textContent = config.schedule_description;
        }

        // Nudge the user to the settings screen until credentials are filled in.
        const banner = document.getElementById("setup-banner");
        if (banner) banner.classList.toggle("hidden", config.is_configured !== false);

        const targetBadge = document.getElementById("target-badge");
        if (targetBadge && config.work_hours) {
            targetBadge.textContent = `Meta: ${String(Math.floor(config.work_hours)).padStart(2, "0")}h${String(Math.round((config.work_hours % 1) * 60)).padStart(2, "0")}`;
        }
    } catch (e) {
        console.error("fetchConfig error:", e);
    }
}

function renderDashboard(data) {
    // 1. Status Pill
    const pill = document.getElementById("status-pill");
    const pillLabel = document.getElementById("status-label");
    const stageMap = {
        "not_started": { label: "Fora de Expediente", cls: "status-loading" },
        "working_morning": { label: "Em Expediente (Manhã)", cls: "status-working" },
        "lunch_break": { label: "Em Intervalo de Almoço", cls: "status-lunch" },
        "working_afternoon": { label: "Em Expediente (Tarde)", cls: "status-working" },
        "completed": { label: "Jornada Concluída", cls: "status-completed" },
    };

    const currentStage = stageMap[data.stage] || { label: data.stage, cls: "status-loading" };
    if (pill) pill.className = `status-pill ${currentStage.cls}`;
    if (pillLabel) pillLabel.textContent = currentStage.label;

    // 2. Metrics
    const workedEl = document.getElementById("metric-worked");
    if (workedEl) workedEl.textContent = data.worked_formatted || "00h00min";

    const progressBar = document.getElementById("progress-bar");
    const progressText = document.getElementById("progress-text");
    const pct = data.progress_percentage || 0;
    if (progressBar) progressBar.style.width = `${pct}%`;
    if (progressText) {
        const targetLabel = formatSeconds(data.target_seconds);
        progressText.textContent = `${pct}% concluído (${data.worked_formatted} / ${targetLabel})`;
    }

    // 3. Departure & Balance
    const departureEl = document.getElementById("metric-departure");
    if (departureEl) departureEl.textContent = data.estimated_departure || "--:--";

    const balanceEl = document.getElementById("metric-balance");
    if (balanceEl) {
        balanceEl.textContent = data.balance_formatted || "00h00min";
        balanceEl.style.color = (data.balance_seconds >= 0) ? "var(--accent-emerald)" : "var(--accent-amber)";
    }

    const lunchInfo = document.getElementById("lunch-duration-info");
    if (lunchInfo) {
        lunchInfo.textContent = `Almoço hoje: ${data.lunch_duration_formatted || "00h00min"}`;
    }

    // 4. Alert Countdown
    const alertDesc = document.getElementById("alert-description");
    if (alertDesc) alertDesc.textContent = data.next_alert_label || "Aguardando próxima ação";

    startCountdownTicker(data.next_alert_seconds);

    // 5. Timeline of Entries
    renderTimeline(data.entries || []);
}

function formatSeconds(totalSeconds) {
    if (!totalSeconds && totalSeconds !== 0) return "--h--";
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    return `${String(hours).padStart(2, "0")}h${String(minutes).padStart(2, "0")}`;
}

function startCountdownTicker(seconds) {
    if (countdownTimerInterval) {
        clearInterval(countdownTimerInterval);
        countdownTimerInterval = null;
    }

    const countdownEl = document.getElementById("metric-countdown");
    if (seconds === null || seconds === undefined || seconds < 0) {
        if (countdownEl) countdownEl.textContent = "--:--:--";
        return;
    }

    currentRemainingSeconds = Math.max(0, seconds);

    function tick() {
        if (currentRemainingSeconds <= 0) {
            if (countdownEl) countdownEl.textContent = "00:00:00";
            return;
        }

        const hrs = Math.floor(currentRemainingSeconds / 3600);
        const mins = Math.floor((currentRemainingSeconds % 3600) / 60);
        const secs = currentRemainingSeconds % 60;

        const formatted = `${String(hrs).padStart(2, "0")}:${String(mins).padStart(2, "0")}:${String(secs).padStart(2, "0")}`;
        if (countdownEl) countdownEl.textContent = formatted;

        currentRemainingSeconds--;
    }

    tick();
    countdownTimerInterval = setInterval(tick, 1000);
}

function renderTimeline(entries) {
    const container = document.getElementById("timeline-container");
    const countBadge = document.getElementById("entries-count-badge");
    if (countBadge) countBadge.textContent = `${entries.length} ${entries.length === 1 ? "batida" : "batidas"}`;

    if (!container) return;

    if (entries.length === 0) {
        container.innerHTML = `
            <div class="timeline-empty">
                Nenhum ponto registrado hoje no TiqueTaque até o momento.
            </div>
        `;
        return;
    }

    const labels = ["1ª Batida — Entrada", "2ª Batida — Saída Almoço", "3ª Batida — Retorno Almoço", "4ª Batida — Saída Final"];

    let html = "";
    entries.forEach((timeStr, idx) => {
        const title = labels[idx] || `Batida #${idx + 1}`;
        html += `
            <div class="timeline-item">
                <div class="timeline-left">
                    <div class="timeline-icon-box">${idx + 1}</div>
                    <div>
                        <div class="timeline-time">${timeStr}</div>
                        <div class="timeline-tag">${title}</div>
                    </div>
                </div>
                <div class="timeline-right">
                    <span class="badge-source">Web</span>
                    <span class="badge-status status-approved">Registrado</span>
                </div>
            </div>
        `;
    });

    container.innerHTML = html;
}

// ==============================================================================
// 4. Test Notification Helper
// ==============================================================================
async function testNotification(channel) {
    try {
        const res = await fetch(`/api/test-notification?channel=${channel}`, { method: "POST" });
        const data = await res.json();
        if (res.ok && data.success) {
            showToast(`Mensagem de teste enviada com sucesso para o ${channel.toUpperCase()}!`, "success");
        } else {
            showToast(`Falha ao testar ${channel.toUpperCase()}: ${data.detail || "Verifique as configurações"}`, "error");
        }
    } catch (e) {
        showToast(`Erro de conexão ao testar ${channel}`, "error");
    }
}

// ==============================================================================
// 5. Toast Notification System
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
