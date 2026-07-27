"""
Ponto de entrada raiz da aplicação.
Executa o pipeline principal localizado no pacote src.
"""

import asyncio
from src.main import run_pipeline

if __name__ == "__main__":
    asyncio.run(run_pipeline())
