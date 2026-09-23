"""Regressão das duas rajadas de notificação observadas em produção.

Evidência que motivou estes testes (cluster do usuário, 22-23/09/2026):

* pod subiu às 17:45 BRT e mandou 4 notificações em 1 segundo — as batidas
  daquela hora, reanunciadas porque o SQLite (efêmero) perdeu a deduplicação;
* à 00:00 BRT saíram 8 de uma vez — a jornada inteira do dia anterior, porque a
  chave de deduplicação inclui a data e tudo virou "inédito".

O mesmo vale para o app de desktop: basta o serviço reiniciar.
"""

import asyncio
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytz

from tests import _bootstrap  # noqa: F401  (isola config/dados antes dos imports)

from tiquetaque_sync.engine.database import Database
from tiquetaque_sync.engine.scheduler import SyncScheduler
from tiquetaque_sync.engine.workday import WorkdayEngine

TZ = pytz.timezone("America/Sao_Paulo")
BATIDAS = ["08:02", "12:00", "13:00"]


class DispatcherFalso:
    def __init__(self):
        self.enviados = []

    async def dispatch(self, title, message, level="info"):
        self.enviados.append(title)
        return {"fake": True}


class TestRajadasDeAlerta(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(self._tmp.cleanup)

        self.dispatcher = DispatcherFalso()
        self.scheduler = SyncScheduler(
            client=None,
            engine=WorkdayEngine(timezone_name="America/Sao_Paulo"),
            database=Database(Path(self._tmp.name) / "t.db"),
            dispatcher=self.dispatcher,
            timezone_name="America/Sao_Paulo",
        )

    def _despachar_em(self, momento, batidas=BATIDAS):
        status = self.scheduler.engine.calculate_status(batidas, current_dt=momento)
        gatilhos = self.scheduler.engine.evaluate_alert_triggers(status, current_dt=momento)
        self.dispatcher.enviados.clear()
        asyncio.run(self.scheduler._dispatch_triggers(gatilhos, status, momento))
        return list(self.dispatcher.enviados), status

    def test_reinicio_no_meio_do_dia_nao_reanuncia_batidas_passadas(self):
        enviados, _ = self._despachar_em(TZ.localize(datetime(2026, 9, 23, 15, 0)))
        self.assertEqual(enviados, [], "batidas antigas foram reanunciadas após o reinício")

    def test_batida_recente_ainda_e_anunciada(self):
        enviados, _ = self._despachar_em(
            TZ.localize(datetime(2026, 9, 23, 8, 4)), batidas=["08:02"]
        )
        self.assertEqual(len(enviados), 1, "a batida recém-feita deveria ser anunciada")

    def test_alerta_silenciado_fica_registrado(self):
        """Silenciar não é ignorar: o alerta é gravado para nunca mais voltar."""
        _, status = self._despachar_em(TZ.localize(datetime(2026, 9, 23, 15, 0)))
        for indice, batida in enumerate(BATIDAS):
            with self.subTest(batida=batida):
                self.assertTrue(
                    self.scheduler.db.is_alert_dispatched(status.date_str, f"entry_{indice}_{batida}")
                )

    def test_virada_do_dia_descarta_o_cache_sem_notificar(self):
        """À 00:00 o cache é de ontem: nada é anunciado e o estado é limpo."""
        ontem = TZ.localize(datetime(2026, 9, 22, 18, 0))
        self.scheduler._last_status = self.scheduler.engine.calculate_status(
            BATIDAS, current_dt=ontem
        )

        meia_noite = TZ.localize(datetime(2026, 9, 23, 0, 0, 12))
        self.dispatcher.enviados.clear()
        with mock.patch("tiquetaque_sync.engine.scheduler.datetime") as fake:
            fake.now.return_value = meia_noite
            asyncio.run(self.scheduler._evaluate_active_alerts())

        self.assertEqual(self.dispatcher.enviados, [], "a jornada de ontem foi reanunciada")
        self.assertIsNone(self.scheduler._last_status, "o cache vencido deveria ser descartado")


if __name__ == "__main__":
    unittest.main()
