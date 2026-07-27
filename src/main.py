"""
Orquestrador Principal do Pipeline de Extração Híbrida de Licitações.
Varre downloads/, executa triagem de anexos, aciona parsers tabulares nativos e agente LLM de resgate,
valida com Pydantic e gera os artefatos na pasta output/ (output/resultado.json e output/relatorio_falhas.md).
Possui suporte a salvamento incremental e retomada de execução (Checkpoint/Resume).
Arquitetura Otimizada: Código Nativo Primeiro em TODOS os anexos ➔ LLM Rescuer de Socorro ➔ Fallback em data.itens.
"""

import asyncio
import os
from pathlib import Path
import json
import logging
import re
from typing import List, Dict, Any, Set

from src.models import LicitacaoProcessada, ItemLicitacao
from src.extractors import (
    is_relevant_attachment,
    resolve_local_filepath,
    extract_attachment_to_markdown,
    parse_pdf_attachment_items,
    clean_unidade_fornecimento,
    clean_objeto_description,
)
from src.agents import LLMAgent
from src.utils import log_failure

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("LicitacaoPipeline")


def parse_fallback_itens(raw_itens_list: List[str]) -> List[ItemLicitacao]:
    """
    Parser determinístico de fallback para o campo data.itens do JSON original.
    """
    items: List[ItemLicitacao] = []
    if not raw_itens_list:
        return items

    full_text = "\n".join(raw_itens_list)
    blocks = re.split(r"-{5,}|\n(?=\d+\s*-\s*)", full_text)

    for block in blocks:
        block_str = block.strip()
        if not block_str:
            continue

        item_match = re.search(r"^(\d+)\s*-\s*(.+)", block_str, re.MULTILINE)
        qty_match = re.search(r"Quantidade:\s*(\d+)", block_str, re.IGNORECASE)
        unit_match = re.search(r"Unidade de fornecimento:\s*(.+)", block_str, re.IGNORECASE)

        if item_match and qty_match:
            try:
                num_item = int(item_match.group(1))
                objeto_desc = item_match.group(2).strip()

                lines = block_str.splitlines()
                if len(lines) > 1:
                    desc_lines = []
                    for line in lines[1:]:
                        if not any(line.strip().startswith(k) for k in ("Tratamento", "Aplicabilidade", "Quantidade:", "Unidade de fornecimento:")):
                            desc_lines.append(line.strip())
                    if desc_lines:
                        objeto_desc = (objeto_desc + " " + " ".join(desc_lines)).strip()

                qtd = int(qty_match.group(1))
                raw_unidade = unit_match.group(1).strip() if unit_match else "Unidade"
                unidade_clean = clean_unidade_fornecimento(raw_unidade)
                objeto_clean = clean_objeto_description(objeto_desc)

                items.append(
                    ItemLicitacao(
                        lote=None,
                        item=num_item,
                        objeto=objeto_clean,
                        quantidade=qtd,
                        unidade_fornecimento=unidade_clean
                    )
                )
            except Exception:
                continue

    return items


async def process_single_bidding(
    json_path: Path,
    llm_agent: LLMAgent,
    downloads_dir: Path,
    report_path: str = "output/relatorio_falhas.md"
) -> LicitacaoProcessada:
    """
    Processa uma única licitação e seus anexos.
    Arquitetura de Alto Desempenho:
    1. CAMADA 1: Varre TODOS os anexos com parsers nativos determinísticos por código (Custo Zero, Velocidade Máxima).
    2. CAMADA 2: Caso NENHUM anexo tenha gerado itens, aciona a IA (LLM Rescuer) para interpretar editais não-estruturados.
    3. CAMADA 3: Caso a IA falhe ou não tenha chave/API, aciona a rede de segurança (data.itens).
    """
    json_filename = json_path.name
    folder_name = json_path.stem
    attachments_folder = downloads_dir / folder_name

    logger.info(f"Processando licitação: {json_filename}")

    with open(json_path, "r", encoding="utf-8") as f:
        json_data: Dict[str, Any] = json.load(f)

    data_section = json_data.get("data", {})

    numero_pregao = str(data_section.get("numero_pregao", ""))
    orgao = str(data_section.get("orgao", ""))
    cidade = str(data_section.get("cidade", ""))
    estado = str(data_section.get("estado", "")).upper()
    anexos_list = data_section.get("anexos", [])
    raw_itens = data_section.get("itens", [])

    anexos_processados: List[str] = []
    itens_extraidos: List[ItemLicitacao] = []
    relevant_filepaths: List[Path] = []

    # --- PASSO 1: CAMADA 1 (Extração Nativa por Código em todos os anexos) ---
    for anexo in anexos_list:
        nome_anexo = anexo.get("nome", "")
        caminho_anexo = anexo.get("caminho")

        if not is_relevant_attachment(nome_anexo):
            continue

        try:
            target_filepath = resolve_local_filepath(attachments_folder, nome_anexo, caminho_anexo)

            # Valida I/O e 0 bytes
            if target_filepath.stat().st_size == 0:
                raise ValueError(f"Arquivo corrompido ou vazio (0 bytes): {target_filepath.name}")

            relevant_filepaths.append(target_filepath)
            anexos_processados.append(nome_anexo)

            # Extração por código nativo (PDFs)
            if target_filepath.suffix.lower() == ".pdf":
                pdf_items = parse_pdf_attachment_items(target_filepath)
                if pdf_items:
                    itens_extraidos.extend(pdf_items)

        except Exception as err:
            logger.warning(f"Falha no anexo '{nome_anexo}' de {json_filename}: {err}")
            log_failure(
                json_filename=json_filename,
                attachment_name=nome_anexo,
                error_message=str(err),
                report_path=report_path
            )

    # --- PASSO 2: CAMADA 2 (Agente LLM de Resgate para casos sem tabela padronizada) ---
    if not itens_extraidos and llm_agent.client and (time_now := asyncio.get_event_loop().time()) > 0:
        for filepath in relevant_filepaths:
            try:
                markdown_content = extract_attachment_to_markdown(filepath)
                extracted_llm = await llm_agent.extract_items(markdown_content)
                if extracted_llm:
                    itens_extraidos.extend(extracted_llm)
                    break  # Se a IA já extraiu os itens de um anexo, encerra a busca para esta licitação
            except Exception as err:
                logger.warning(f"Falha na camada LLM para {filepath.name}: {err}")

    # --- PASSO 3: CAMADA 3 (Rede de Segurança - data.itens do JSON original) ---
    if not itens_extraidos and raw_itens:
        logger.info(f"Executando fallback para data.itens em {json_filename}")
        itens_extraidos = parse_fallback_itens(raw_itens)
    else:
        # Aplica higienização estrita nos itens extraídos dos anexos
        cleaned_list: List[ItemLicitacao] = []
        for it in itens_extraidos:
            cleaned_list.append(
                ItemLicitacao(
                    lote=it.lote,
                    item=it.item,
                    objeto=clean_objeto_description(it.objeto),
                    quantidade=it.quantidade,
                    unidade_fornecimento=clean_unidade_fornecimento(it.unidade_fornecimento)
                )
            )
        itens_extraidos = cleaned_list

    result = LicitacaoProcessada(
        arquivo_json=json_filename,
        numero_pregao=numero_pregao,
        orgao=orgao,
        cidade=cidade,
        estado=estado,
        anexos_processados=anexos_processados,
        itens_extraidos=itens_extraidos
    )

    return result


def save_incremental_results(output_path: Path, results: List[LicitacaoProcessada]) -> None:
    """Escreve a lista completa atualizada em formato JSON de forma atômica."""
    output_data = [item.model_dump(mode="json") for item in results]
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, ensure_ascii=False, indent=2)


async def run_pipeline(
    downloads_dir_path: str = "downloads",
    output_path: str = "output/resultado.json",
    report_path: str = "output/relatorio_falhas.md",
    resume: bool = True
) -> None:
    """
    Executa o pipeline com salvamento incremental e capacidade de retomada de onde parou.
    """
    downloads_dir = Path(downloads_dir_path)
    if not downloads_dir.exists():
        raise FileNotFoundError(f"Diretório downloads não encontrado: {downloads_dir_path}")

    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)

    report_file = Path(report_path)
    report_file.parent.mkdir(parents=True, exist_ok=True)

    if os.getenv("RESET_OUTPUT", "").lower() in ("true", "1") or not resume:
        logger.info("Solicitado reset de saída. Limpando resultado.json e relatorio_falhas.md...")
        if out_file.exists():
            out_file.unlink()
        if report_file.exists():
            report_file.unlink()

    processed_results: List[LicitacaoProcessada] = []
    already_processed_files: Set[str] = set()

    if out_file.exists() and out_file.stat().st_size > 0:
        try:
            with open(out_file, "r", encoding="utf-8") as f:
                existing_data = json.load(f)
                if isinstance(existing_data, list):
                    for item in existing_data:
                        parsed = LicitacaoProcessada.model_validate(item)
                        processed_results.append(parsed)
                        already_processed_files.add(parsed.arquivo_json)
            logger.info(f"Retomando execução: {len(already_processed_files)} licitações já processadas foram carregadas de '{output_path}'.")
        except Exception as err:
            logger.warning(f"Não foi possível carregar o checkpoint de '{output_path}': {err}. Iniciando do zero.")
            processed_results = []
            already_processed_files = set()

    all_json_files = sorted(list(downloads_dir.glob("*.json")))
    pending_json_files = [f for f in all_json_files if f.name not in already_processed_files]

    logger.info(f"Total de licitações: {len(all_json_files)} | Já processadas: {len(already_processed_files)} | Pendentes: {len(pending_json_files)}")

    if not pending_json_files:
        logger.info("Todas as licitações do dataset já foram processadas! Nada a fazer.")
        return

    llm_agent = LLMAgent()

    for idx, json_file in enumerate(pending_json_files, start=1):
        try:
            processed = await process_single_bidding(json_file, llm_agent, downloads_dir, report_path=report_path)
            processed_results.append(processed)

            save_incremental_results(out_file, processed_results)
            logger.info(f"[{idx}/{len(pending_json_files)}] Salvo incrementalmente em '{out_file}' (Total acumulado: {len(processed_results)})")

        except Exception as err:
            logger.error(f"Erro ao processar {json_file.name}: {err}")

    logger.info(f"Pipeline finalizado com sucesso! {len(processed_results)} licitações salvas em '{out_file}'")


if __name__ == "__main__":
    asyncio.run(run_pipeline())
