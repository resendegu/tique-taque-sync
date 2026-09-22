"""Ponto de entrada do executável empacotado (PyInstaller).

Sem argumentos abre a janela; com argumentos se comporta como a CLI normal —
é isso que permite o próprio .exe se re-invocar como serviço em background
(`TiqueTaqueSync.exe start --no-browser`), usado pelo autostart e pela janela.
"""

import multiprocessing
import sys

from tiquetaque_sync.cli import main_gui

if __name__ == "__main__":
    # Necessário em executáveis congelados no Windows: sem isto, qualquer
    # processo filho reabriria a janela em vez de rodar o trabalho.
    multiprocessing.freeze_support()
    sys.exit(main_gui())
