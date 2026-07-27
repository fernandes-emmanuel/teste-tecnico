"""
Módulo de Auditoria e Registro de Falhas.
Gerencia a escrita resiliente de erros em relatorio_falhas.md na pasta de saída.
"""

from datetime import datetime
import os
from pathlib import Path
import threading

_LOG_LOCK = threading.Lock()


def log_failure(
    json_filename: str,
    attachment_name: str,
    error_message: str,
    report_path: str = "output/relatorio_falhas.md"
) -> None:
    """
    Registra uma falha de processamento de anexo no arquivo relatorio_falhas.md

    Args:
        json_filename (str): Nome do arquivo JSON de origem.
        attachment_name (str): Nome do anexo problemático.
        error_message (str): Descrição ou mensagem da exceção capturada.
        report_path (str): Caminho para o arquivo markdown de relatório (padrão: output/relatorio_falhas.md).
    """
    
    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    entry = (
        f"- **Data/Hora:** {timestamp}\n"
        f"- **Arquivo JSON Origem:** {json_filename}\n"
        f"- **Anexo Falho:** {attachment_name}\n"
        f"- **Motivo:** {error_message}\n\n"
    )

    report_file = Path(report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)

    with _LOG_LOCK:
        try:
            with open(report_file, "a", encoding="utf-8") as f:
                f.write(entry)
        except Exception as err:
            print(f"[ERROR LOGGER] Falha ao escrever em {report_path}: {err}")
