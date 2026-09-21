// ==UserScript==
// @name         TiqueTaque Instant Sync Hook & Timer HUD
// @namespace    https://github.com/resendegu/tique-taque-sync
// @version      1.0.0
// @description  Intercepta batidas de ponto no tiquetaque.app e notifica o TiqueTaque Sync instantaneamente.
// @author       TiqueTaque Sync Community
// @match        https://tiquetaque.app/*
// @grant        GM_xmlhttpRequest
// @connect      ponto.example.com
// @connect      localhost
// @run-at       document-end
// ==/UserScript==

(function () {
    'use strict';

    // Endpoints do seu servidor TiqueTaque Sync (Altere para o domínio do seu servidor)
    const SYNC_SERVER_URL = "https://ponto.example.com/api/webhook/clock-in";
    const FALLBACK_LOCAL_URL = "http://localhost:8000/api/webhook/clock-in";

    console.log("[TiqueTaque Sync] Userscript carregado com sucesso.");

    // 1. Interceptar cliques no botão oficial de registro de ponto
    function attachButtonInterceptor() {
        const btn = document.getElementById("btn-remote-record");
        if (btn && !btn.dataset.hookAttached) {
            btn.dataset.hookAttached = "true";
            btn.addEventListener("click", () => {
                const now = new Date();
                const timeStr = now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", hour12: false });
                console.log("[TiqueTaque Sync] Botão 'Registrar ponto' clicado às " + timeStr);
                notifyServer(timeStr);
                showHUDNotification(`Ponto capturado: ${timeStr}`);
            });
        }
    }

    // 2. Interceptar XHR/Fetch no caso de chamadas diretas da aplicação
    const originalFetch = window.fetch;
    window.fetch = async function (...args) {
        const response = await originalFetch.apply(this, args);
        try {
            const url = typeof args[0] === "string" ? args[0] : (args[0] && args[0].url) || "";
            if (url.includes("/employees/day-records/add-times") && response.ok) {
                const now = new Date();
                const timeStr = now.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit", hour12: false });
                console.log("[TiqueTaque Sync] API add-times confirmada às " + timeStr);
                notifyServer(timeStr);
                showHUDNotification(`Ponto registrado com sucesso: ${timeStr}`);
            }
        } catch (e) {
            console.error("[TiqueTaque Sync] Erro no hook fetch:", e);
        }
        return response;
    };

    // 3. Enviar notificação para o servidor TiqueTaque Sync
    function notifyServer(timeStr) {
        const payload = JSON.stringify({
            time_str: timeStr,
            source: "userscript_hook"
        });

        // Tentar enviar via GM_xmlhttpRequest ou fetch padrão
        if (typeof GM_xmlhttpRequest !== "undefined") {
            GM_xmlhttpRequest({
                method: "POST",
                url: SYNC_SERVER_URL,
                headers: { "Content-Type": "application/json" },
                data: payload,
                onerror: function () {
                    // Fallback para localhost
                    GM_xmlhttpRequest({
                        method: "POST",
                        url: FALLBACK_LOCAL_URL,
                        headers: { "Content-Type": "application/json" },
                        data: payload
                    });
                }
            });
        } else {
            fetch(SYNC_SERVER_URL, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: payload
            }).catch(() => {
                fetch(FALLBACK_LOCAL_URL, {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: payload
                }).catch(() => {});
            });
        }
    }

    // 4. Injetar mini HUD flutuante no canto superior direito
    function showHUDNotification(text) {
        let hud = document.getElementById("tqtq-sync-hud");
        if (!hud) {
            hud = document.createElement("div");
            hud.id = "tqtq-sync-hud";
            hud.style.position = "fixed";
            hud.style.top = "20px";
            hud.style.right = "20px";
            hud.style.zIndex = "99999";
            hud.style.background = "linear-gradient(135deg, #1e1b4b 0%, #31104b 100%)";
            hud.style.color = "#ffffff";
            hud.style.padding = "10px 18px";
            hud.style.borderRadius = "12px";
            hud.style.boxShadow = "0 8px 30px rgba(0,0,0,0.5), 0 0 20px rgba(139,92,246,0.4)";
            hud.style.border = "1px solid rgba(139,92,246,0.5)";
            hud.style.fontFamily = "system-ui, sans-serif";
            hud.style.fontSize = "13px";
            hud.style.fontWeight = "600";
            hud.style.display = "flex";
            hud.style.alignItems = "center";
            hud.style.gap = "8px";
            hud.style.transition = "all 0.3s ease";
            document.body.appendChild(hud);
        }

        hud.innerHTML = `<span>⚡</span> <span>${text}</span>`;
        hud.style.opacity = "1";
        hud.style.transform = "translateY(0)";

        setTimeout(() => {
            if (hud) {
                hud.style.opacity = "0";
                hud.style.transform = "translateY(-10px)";
            }
        }, 4000);
    }

    // Observer para capturar o botão mesmo se a SPA demorar para renderizar
    const observer = new MutationObserver(attachButtonInterceptor);
    observer.observe(document.body, { childList: true, subtree: true });
    attachButtonInterceptor();
})();
