"""Workday calculation engine and state machine."""

from datetime import datetime, timedelta
from enum import Enum
from typing import Any
import pytz


class WorkdayStage(str, Enum):
    NOT_STARTED = "not_started"
    WORKING_MORNING = "working_morning"
    LUNCH_BREAK = "lunch_break"
    WORKING_AFTERNOON = "working_afternoon"
    COMPLETED = "completed"
    CUSTOM = "custom"


def parse_time_to_dt(time_str: str, base_date: datetime, tz: pytz.BaseTzInfo) -> datetime:
    """Parse 'HH:mm' into a timezone-aware datetime on base_date."""
    parts = time_str.strip().split(":")
    hour = int(parts[0])
    minute = int(parts[1]) if len(parts) > 1 else 0
    dt = datetime(base_date.year, base_date.month, base_date.day, hour, minute, 0)
    return tz.localize(dt)


def format_seconds_to_hm(seconds: int) -> str:
    """Format seconds into readable '+08h00min' or '-00h15min'."""
    is_neg = seconds < 0
    sec = abs(seconds)
    hours = sec // 3600
    minutes = (sec % 3600) // 60
    prefix = "-" if is_neg else ""
    return f"{prefix}{hours:02d}h{minutes:02d}min"


class WorkdayStatus:
    """Encapsulates the complete live workday state and metrics."""

    def __init__(
        self,
        date_str: str,
        stage: WorkdayStage,
        entries: list[str],
        worked_seconds: int,
        target_seconds: int,
        lunch_duration_seconds: int,
        estimated_departure: str | None = None,
        next_alert_label: str | None = None,
        next_alert_seconds: int | None = None,
        progress_percentage: float = 0.0,
        continuous_work_seconds: int = 0,
        continuous_limit_seconds: int = 21600,
    ):
        self.date_str = date_str
        self.stage = stage
        self.entries = entries
        self.worked_seconds = worked_seconds
        self.target_seconds = target_seconds
        self.lunch_duration_seconds = lunch_duration_seconds
        self.estimated_departure = estimated_departure
        self.next_alert_label = next_alert_label
        self.next_alert_seconds = next_alert_seconds
        self.progress_percentage = min(100.0, max(0.0, progress_percentage))
        self.continuous_work_seconds = continuous_work_seconds
        self.continuous_limit_seconds = continuous_limit_seconds

    @property
    def balance_seconds(self) -> int:
        return self.worked_seconds - self.target_seconds

    @property
    def worked_formatted(self) -> str:
        return format_seconds_to_hm(self.worked_seconds)

    @property
    def continuous_work_formatted(self) -> str:
        return format_seconds_to_hm(self.continuous_work_seconds)

    @property
    def balance_formatted(self) -> str:
        prefix = "+" if self.balance_seconds >= 0 else ""
        return f"{prefix}{format_seconds_to_hm(self.balance_seconds)}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date_str,
            "stage": self.stage.value,
            "entries": self.entries,
            "worked_seconds": self.worked_seconds,
            "worked_formatted": self.worked_formatted,
            "target_seconds": self.target_seconds,
            "balance_seconds": self.balance_seconds,
            "balance_formatted": self.balance_formatted,
            "lunch_duration_seconds": self.lunch_duration_seconds,
            "lunch_duration_formatted": format_seconds_to_hm(self.lunch_duration_seconds),
            "estimated_departure": self.estimated_departure,
            "next_alert_label": self.next_alert_label,
            "next_alert_seconds": self.next_alert_seconds,
            "progress_percentage": round(self.progress_percentage, 1),
            "continuous_work_seconds": self.continuous_work_seconds,
            "continuous_work_formatted": self.continuous_work_formatted,
            "continuous_limit_seconds": self.continuous_limit_seconds,
        }


class WorkdayEngine:
    """Calculates live workday status and detects alert triggers."""

    def __init__(
        self,
        target_hours: float = 8.0,
        lunch_minutes: int = 60,
        lunch_advance_warning: int = 10,
        lunch_final_warning: int = 1,
        end_work_advance_warning: int = 15,
        end_work_final_warning: int = 1,
        continuous_work_limit_hours: float = 6.0,
        continuous_work_advance_warning: int = 10,
        continuous_work_final_warning: int = 1,
        timezone_name: str = "America/Sao_Paulo",
    ):
        self.target_seconds = int(target_hours * 3600)
        self.lunch_seconds = lunch_minutes * 60
        self.lunch_advance_warning = lunch_advance_warning
        self.lunch_final_warning = lunch_final_warning
        self.end_work_advance_warning = end_work_advance_warning
        self.end_work_final_warning = end_work_final_warning
        self.continuous_limit_seconds = int(continuous_work_limit_hours * 3600)
        self.continuous_work_advance_warning = continuous_work_advance_warning
        self.continuous_work_final_warning = continuous_work_final_warning
        self.tz = pytz.timezone(timezone_name)

    def calculate_status(self, entries: list[str], current_dt: datetime | None = None) -> WorkdayStatus:
        """Calculate the current workday status given the list of clock-in times today (e.g. ['08:00', '12:00'])."""
        if current_dt is None:
            current_dt = datetime.now(self.tz)
        elif current_dt.tzinfo is None:
            current_dt = self.tz.localize(current_dt)

        date_str = current_dt.strftime("%d/%m/%Y")
        sorted_times = sorted(entries)
        count = len(sorted_times)

        # 0 entries: NOT STARTED
        if count == 0:
            return WorkdayStatus(
                date_str=date_str,
                stage=WorkdayStage.NOT_STARTED,
                entries=sorted_times,
                worked_seconds=0,
                target_seconds=self.target_seconds,
                lunch_duration_seconds=0,
                next_alert_label="Aguardando primeira batida (Entrada)",
                next_alert_seconds=None,
                progress_percentage=0.0,
                continuous_work_seconds=0,
                continuous_limit_seconds=self.continuous_limit_seconds,
            )

        dts = [parse_time_to_dt(t, current_dt, self.tz) for t in sorted_times]

        # 1 entry: WORKING MORNING (clocked in at dts[0])
        if count == 1:
            worked = max(0, int((current_dt - dts[0]).total_seconds()))
            continuous_worked = worked

            # Standard planned lunch at 12:00
            planned_lunch = parse_time_to_dt("12:00", current_dt, self.tz)
            if dts[0] > planned_lunch:
                planned_lunch = dts[0] + timedelta(hours=4)

            secs_to_lunch = max(0, int((planned_lunch - current_dt).total_seconds()))
            progress = (worked / self.target_seconds) * 100.0

            return WorkdayStatus(
                date_str=date_str,
                stage=WorkdayStage.WORKING_MORNING,
                entries=sorted_times,
                worked_seconds=worked,
                target_seconds=self.target_seconds,
                lunch_duration_seconds=0,
                estimated_departure=(dts[0] + timedelta(seconds=self.target_seconds + self.lunch_seconds)).strftime("%H:%M"),
                next_alert_label="Início previsto do almoço",
                next_alert_seconds=secs_to_lunch,
                progress_percentage=progress,
                continuous_work_seconds=continuous_worked,
                continuous_limit_seconds=self.continuous_limit_seconds,
            )

        # 2 entries: LUNCH BREAK (started lunch at dts[1])
        if count == 2:
            morning_worked = max(0, int((dts[1] - dts[0]).total_seconds()))
            lunch_elapsed = max(0, int((current_dt - dts[1]).total_seconds()))
            lunch_end_planned = dts[1] + timedelta(seconds=self.lunch_seconds)
            secs_to_lunch_end = max(0, int((lunch_end_planned - current_dt).total_seconds()))
            progress = (morning_worked / self.target_seconds) * 100.0

            # Estimated departure assuming 1h lunch
            remaining_work = self.target_seconds - morning_worked
            departure_est = lunch_end_planned + timedelta(seconds=remaining_work)

            return WorkdayStatus(
                date_str=date_str,
                stage=WorkdayStage.LUNCH_BREAK,
                entries=sorted_times,
                worked_seconds=morning_worked,
                target_seconds=self.target_seconds,
                lunch_duration_seconds=lunch_elapsed,
                estimated_departure=departure_est.strftime("%H:%M"),
                next_alert_label="Término do intervalo de 1h",
                next_alert_seconds=secs_to_lunch_end,
                progress_percentage=progress,
                continuous_work_seconds=0,
                continuous_limit_seconds=self.continuous_limit_seconds,
            )

        # 3 entries: WORKING AFTERNOON (returned from lunch at dts[2])
        if count == 3:
            morning_worked = max(0, int((dts[1] - dts[0]).total_seconds()))
            lunch_actual = max(0, int((dts[2] - dts[1]).total_seconds()))
            afternoon_worked = max(0, int((current_dt - dts[2]).total_seconds()))
            total_worked = morning_worked + afternoon_worked
            continuous_worked = afternoon_worked

            remaining_work = max(0, self.target_seconds - morning_worked)
            departure_dt = dts[2] + timedelta(seconds=remaining_work)
            secs_to_departure = max(0, int((departure_dt - current_dt).total_seconds()))
            progress = (total_worked / self.target_seconds) * 100.0

            return WorkdayStatus(
                date_str=date_str,
                stage=WorkdayStage.WORKING_AFTERNOON,
                entries=sorted_times,
                worked_seconds=total_worked,
                target_seconds=self.target_seconds,
                lunch_duration_seconds=lunch_actual,
                estimated_departure=departure_dt.strftime("%H:%M"),
                next_alert_label="Fim da jornada (8h)",
                next_alert_seconds=secs_to_departure,
                progress_percentage=progress,
                continuous_work_seconds=continuous_worked,
                continuous_limit_seconds=self.continuous_limit_seconds,
            )

        # 4+ entries: COMPLETED or custom multi-interval
        total_worked = 0
        for i in range(0, count - 1, 2):
            total_worked += max(0, int((dts[i + 1] - dts[i]).total_seconds()))

        # If odd number of entries (currently in an extra shift)
        if count % 2 == 1:
            current_shift_worked = max(0, int((current_dt - dts[-1]).total_seconds()))
            total_worked += current_shift_worked
            continuous_worked = current_shift_worked
            stage = WorkdayStage.CUSTOM
        else:
            continuous_worked = 0
            stage = WorkdayStage.COMPLETED

        lunch_actual = max(0, int((dts[2] - dts[1]).total_seconds())) if count >= 3 else 0
        progress = (total_worked / self.target_seconds) * 100.0

        return WorkdayStatus(
            date_str=date_str,
            stage=stage,
            entries=sorted_times,
            worked_seconds=total_worked,
            target_seconds=self.target_seconds,
            lunch_duration_seconds=lunch_actual,
            estimated_departure=sorted_times[-1],
            next_alert_label="Jornada diária finalizada",
            next_alert_seconds=0,
            progress_percentage=progress,
            continuous_work_seconds=continuous_worked,
            continuous_limit_seconds=self.continuous_limit_seconds,
        )

    def evaluate_alert_triggers(self, status: WorkdayStatus, current_dt: datetime | None = None) -> list[dict[str, Any]]:
        """Evaluate if any alert should be triggered based on current status.

        Returns list of dicts: {'key': str, 'title': str, 'message': str, 'level': str}
        """
        triggers = []
        entries = status.entries
        count = len(entries)

        # 1. Trigger for each new entry registered
        for i, t in enumerate(entries):
            entry_key = f"entry_{i}_{t}"
            labels = ["Entrada", "Saída Almoço", "Retorno Almoço", "Saída Final"]
            label = labels[i] if i < len(labels) else f"Batida #{i+1}"
            triggers.append({
                "key": entry_key,
                "title": f"Ponto Registrado: {label} ({t})",
                "message": f"Batida de ponto às <b>{t}</b> confirmada no TiqueTaque ({label}).\nTotal de batidas hoje: {count}.",
                "level": "success",
            })

        # 2. Alertas de Almoço (quando em LUNCH_BREAK)
        if status.stage == WorkdayStage.LUNCH_BREAK:
            secs_left = status.next_alert_seconds or 0
            warn_adv_threshold = self.lunch_advance_warning * 60
            warn_final_threshold = self.lunch_final_warning * 60

            # Alerta prévio (ex: 10 minutos antes)
            if warn_final_threshold < secs_left <= warn_adv_threshold:
                mins_left = max(1, (secs_left + 30) // 60)
                triggers.append({
                    "key": "lunch_warning",
                    "title": f"⚠️ Aviso de Almoço ({mins_left} min)",
                    "message": f"Seu intervalo de almoço completará 1h em aproximadamente <b>{mins_left} minutos</b>. Prepare-se para registrar o retorno!",
                    "level": "warning",
                })
            # Alerta final (ex: 1 minuto antes)
            elif 0 < secs_left <= warn_final_threshold:
                triggers.append({
                    "key": "lunch_warning_final",
                    "title": "🚨 Alerta Final: 1 Minuto para Fim do Almoço!",
                    "message": "Falta apenas <b>1 minuto</b> para completar seu intervalo de 1h de almoço!\nRegistre o retorno agora no TiqueTaque para manter a pontualidade.",
                    "level": "warning",
                })
            # Alerta de almoço concluído/estourado
            elif secs_left == 0 and status.lunch_duration_seconds >= self.lunch_seconds:
                triggers.append({
                    "key": "lunch_overtime",
                    "title": "ℹ️ Intervalo de Almoço Concluído (1h)",
                    "message": "Seu intervalo de almoço de 1h já foi atingido! Lembre-se de registrar o ponto de retorno.",
                    "level": "info",
                })

        # 3. Alertas de Fim de Expediente de 8h (quando em WORKING_AFTERNOON)
        if status.stage == WorkdayStage.WORKING_AFTERNOON:
            secs_left = status.next_alert_seconds or 0
            warn_adv_threshold = self.end_work_advance_warning * 60
            warn_final_threshold = self.end_work_final_warning * 60

            # Alerta prévio (ex: 15 minutos antes)
            if warn_final_threshold < secs_left <= warn_adv_threshold:
                mins_left = max(1, (secs_left + 30) // 60)
                triggers.append({
                    "key": "work_end_warning",
                    "title": f"ℹ️ Fim de Expediente Próximo ({mins_left} min)",
                    "message": f"Sua jornada diária de 8h será atingida em aproximadamente <b>{mins_left} minutos</b> (previsto para às <b>{status.estimated_departure}</b>).",
                    "level": "info",
                })
            # Alerta final (ex: 1 minuto antes)
            elif 0 < secs_left <= warn_final_threshold:
                triggers.append({
                    "key": "work_end_warning_final",
                    "title": "🚨 Alerta Final: 1 Minuto para Fim do Expediente!",
                    "message": f"Falta apenas <b>1 minuto</b> para você completar sua jornada de 8h (às <b>{status.estimated_departure}</b>)!\nPrepare-se para registrar sua saída final.",
                    "level": "warning",
                })

        # 4. Regra das 6 Horas Contínuas sem Pausas (Art. 71 da CLT)
        # Aplica-se sempre que o colaborador estiver em turno de trabalho ativo (1, 3, etc. batidas)
        if count % 2 == 1 and status.continuous_work_seconds > 0:
            secs_to_6h = self.continuous_limit_seconds - status.continuous_work_seconds
            clt_adv_threshold = self.continuous_work_advance_warning * 60
            clt_final_threshold = self.continuous_work_final_warning * 60
            shift_id = f"shift_{count}"

            # Alerta prévio CLT (10 minutos antes de 6h contínuas)
            if clt_final_threshold < secs_to_6h <= clt_adv_threshold:
                mins_left = max(1, (secs_to_6h + 30) // 60)
                triggers.append({
                    "key": f"clt_6h_warning_{shift_id}",
                    "title": f"⚠️ Alerta CLT: {mins_left} min para Limite de 6h Contínuas",
                    "message": (
                        f"Você está trabalhando ininterruptamente há <b>{status.continuous_work_formatted}</b>.\n\n"
                        f"Pelo <b>Artigo 71 da CLT</b>, não é permitido trabalhar mais de 6 horas sem intervalo de repouso/alimentação.\n"
                        f"Faltam aproximadamente <b>{mins_left} minutos</b> para atingir o limite legal. Planeje sua pausa!"
                    ),
                    "level": "warning",
                })
            # Alerta crítico final CLT (1 minuto antes de 6h contínuas)
            elif 0 <= secs_to_6h <= clt_final_threshold:
                triggers.append({
                    "key": f"clt_6h_final_{shift_id}",
                    "title": "🚨 Alerta Crítico CLT: Falta 1 Minuto para Limite de 6h!",
                    "message": (
                        f"Falta apenas <b>1 minuto</b> para você atingir o limite legal de 6 horas de trabalho contínuo sem intervalo!\n\n"
                        f"<b>Registre sua pausa imediatamente</b> no TiqueTaque para garantir o cumprimento do Art. 71 da CLT."
                    ),
                    "level": "error",
                })
            # Alerta de limite excedido (> 6h contínuas sem intervalo)
            elif secs_to_6h < 0:
                triggers.append({
                    "key": f"clt_6h_exceeded_{shift_id}",
                    "title": "🛑 Limite Legal de 6h Contínuas Ultrapassado (CLT)",
                    "message": (
                        f"Atenção: você ultrapassou o limite legal de 6 horas ininterruptas de trabalho sem intervalo "
                        f"(tempo contínuo: <b>{status.continuous_work_formatted}</b>).\n\n"
                        f"Por favor, interrompa as atividades e registre o seu intervalo imediatamente!"
                    ),
                    "level": "error",
                })

        # 5. Alarme de jornada concluída
        if status.stage == WorkdayStage.COMPLETED:
            triggers.append({
                "key": "workday_completed",
                "title": "🎉 Jornada Finalizada com Sucesso!",
                "message": (
                    f"Expediente de hoje concluído!\n"
                    f"• Total trabalhado: <b>{status.worked_formatted}</b>\n"
                    f"• Saldo do dia: <b>{status.balance_formatted}</b>\n"
                    f"• Almoço: <b>{format_seconds_to_hm(status.lunch_duration_seconds)}</b>"
                ),
                "level": "success",
            })

        return triggers
