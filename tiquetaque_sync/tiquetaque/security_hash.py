"""Reverse-engineered security hash algorithm for TiqueTaque clock-in operations."""

import hashlib


def generate_clock_in_hash(employee_id: str, full_name: str, date_str: str, time_str: str) -> str:
    """Generate the 'X-Check' security header value required when registering a point.

    Reverse engineered from TiqueTaque bundle function g_e:
      const o = timeStr.split("").reverse().join("") + fullName.trim().toLowerCase() + dateStr + employeeId;
      crypto.subtle.digest("SHA-256", new TextEncoder().encode(o));

    Args:
        employee_id: The 24-char hex employee ID (e.g. '600000000000000000000000')
        full_name: Employee's full name (e.g. 'Jane Doe')
        date_str: Date formatted as 'DD-MM-YYYY' (e.g. '21-09-2026')
        time_str: Time formatted as 'HH:mm' (e.g. '14:51')

    Returns:
        Hex-encoded SHA-256 hash string for 'X-Check' header.
    """
    reversed_time = time_str[::-1]
    normalized_name = full_name.strip().lower()
    raw_payload = f"{reversed_time}{normalized_name}{date_str}{employee_id}"
    return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()


def generate_device_fingerprint(
    language: str = "pt-BR",
    user_agent: str = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    resolution: str = "1920x1080"
) -> str:
    """Generate the 'twd' device fingerprint header value.

    Reverse engineered from TiqueTaque bundle function pY:
      const r = navigator.language;
      const e = navigator.userAgent.replace(/[0-9.]/g, "").toLowerCase();
      const t = `${window.screen.width}x${window.screen.height}`;
      crypto.subtle.digest("SHA-256", encode(`${r}${e}${t}`));
    """
    import re
    cleaned_ua = re.sub(r"[0-9.]", "", user_agent).lower()
    raw_payload = f"{language}{cleaned_ua}{resolution}"
    return hashlib.sha256(raw_payload.encode("utf-8")).hexdigest()
