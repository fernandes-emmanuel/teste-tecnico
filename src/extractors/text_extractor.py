"""
Módulo de Triagem e Extração de Texto e Tabelas para Markdown.
Processa arquivos PDF (pdfplumber), DOCX (python-docx), ODT/ODS (OpenDocument XML) e utiliza OCR leve (pytesseract/docling) em PDFs escaneados.
Inclui parser direto para tabelas e relações de itens com captura de Lote/Grupo e higienização estrita de regex.
"""

from pathlib import Path
from typing import List, Optional
import os
import re
import zipfile
import xml.etree.ElementTree as ET

import pdfplumber
import docx
from src.models import ItemLicitacao


def clean_unidade_fornecimento(unit_str: str) -> str:
    """
    Higieniza o campo unidade_fornecimento removendo metadados de adesão, quantidades máximas e ruídos de tabela.
    """
    if not unit_str:
        return "Unidade"
    cleaned = re.split(r"\s+(?:Quantidade|Valor|Critério|Tratamento|Aplicabilidade|Situação|Local|Item)\b", unit_str, flags=re.IGNORECASE)[0]
    cleaned = cleaned.strip().rstrip(".").rstrip(",")
    return cleaned if cleaned else "Unidade"


def clean_objeto_description(text: str) -> str:
    """
    Higieniza a descrição do objeto removendo títulos duplicados de cabeçalho e metadados de fim de página.
    """
    if not text:
        return ""
    text = text.strip()
    words = text.split()
    n = len(words)

    # Remove títulos duplicados no início do texto (ex: "Caderno Caderno Material...")
    for k in range(min(10, n // 2), 0, -1):
        p1 = " ".join(words[:k]).lower().translate(str.maketrans("", "", ".,/;-"))
        p2 = " ".join(words[k:2*k]).lower().translate(str.maketrans("", "", ".,/;-"))
        if p1 and p1 == p2:
            text = " ".join(words[k:])
            break

    # Trunca metadados de processo anexados no final do objeto
    text = re.split(r"\s+(?:Quantidade|Valor|Critério|Tratamento|Aplicabilidade|Situação|Local|PREGÃO|Quantidade Máxima para Adesões)\b", text, flags=re.IGNORECASE)[0]
    return re.sub(r"\s+", " ", text).strip().rstrip(",").rstrip(";")


def is_relevant_attachment(filename: str) -> bool:
    """
    Verifica se o arquivo é um anexo relevante para extração de itens.
    Foca em editais, termos de referência, relações de itens e documentos pdf/docx/odt/ods.
    Ignora arquivos irrelevantes como minutas de contrato, planilhas XLS ou atas vazias.
    """
    fn_lower = filename.lower()

    valid_exts = (".pdf", ".docx", ".odt", ".ods")
    if not any(fn_lower.endswith(ext) for ext in valid_exts):
        return False

    irrelevant_keywords = ("contrato", "planilha_custos", "ata_registro", "declaracao", "proposta_de_preco")
    if any(kw in fn_lower for kw in irrelevant_keywords) and not any(k in fn_lower for k in ("edital", "termo", "relacao")):
        return False

    return True


def resolve_local_filepath(folder_path: Path, item_nome: str, item_caminho: Optional[str] = None) -> Path:
    """
    Mapeia a referência de nome/caminho do JSON para o arquivo físico presente na pasta de anexos.
    """
    if not folder_path.exists() or not folder_path.is_dir():
        raise FileNotFoundError(f"Pasta de anexos não encontrada: {folder_path}")

    if item_caminho:
        caminho_filename = Path(item_caminho).name
        target = folder_path / caminho_filename
        if target.exists():
            return target

    target_nome = folder_path / item_nome
    if target_nome.exists():
        return target_nome

    base_nome = Path(item_nome).stem.lower()
    for child in folder_path.iterdir():
        if child.is_file():
            if child.name.lower() == item_nome.lower() or base_nome in child.name.lower():
                return child

    raise FileNotFoundError(f"Arquivo anexo '{item_nome}' não foi encontrado em {folder_path}")


def table_to_markdown(table: List[List[Optional[str]]]) -> str:
    """
    Converte uma estrutura de tabela 2D em formato de tabela Markdown.
    """
    if not table or not any(table):
        return ""

    cleaned_table: List[List[str]] = []
    for row in table:
        cleaned_row = [str(cell).strip().replace("\n", " ") if cell is not None else "" for cell in row]
        if any(cleaned_row):
            cleaned_table.append(cleaned_row)

    if not cleaned_table:
        return ""

    max_cols = max(len(row) for row in cleaned_table)
    for row in cleaned_table:
        while len(row) < max_cols:
            row.append("")

    header = cleaned_table[0]
    separator = ["---"] * max_cols

    md_lines = [
        "| " + " | ".join(header) + " |",
        "| " + " | ".join(separator) + " |"
    ]

    for row in cleaned_table[1:]:
        md_lines.append("| " + " | ".join(row) + " |")

    return "\n".join(md_lines)


def parse_pdf_attachment_items(pdf_path: Path) -> List[ItemLicitacao]:
    """
    Parser determinístico de tabelas em anexos PDF (relacaoitens*.pdf ou edital.pdf).
    """
    extracted_items: List[ItemLicitacao] = []
    grupo_mapping: dict = {}

    try:
        with pdfplumber.open(pdf_path) as pdf:
            full_text_pages = [p.extract_text() or "" for p in pdf.pages]
            full_text = "\n".join(full_text_pages)

        comp_match = re.search(r"Composição dos Grupos\s*\n(.*?)(?=\n\s*\d+\s*-|\Z)", full_text, re.DOTALL | re.IGNORECASE)
        if comp_match:
            comp_text = comp_match.group(1)
            group_blocks = re.findall(r"(G\d+)\s*\n(.*?)(?=\n\s*G\d+|\Z)", comp_text, re.DOTALL)
            for g_name, items_blob in group_blocks:
                item_nums = re.findall(r"Item\s*(\d+)", items_blob, re.IGNORECASE)
                for num in item_nums:
                    grupo_mapping[int(num)] = g_name

        item_blocks = re.split(r"\n(?=\d+\s*-\s*)", full_text)

        for block in item_blocks:
            block_str = block.strip()
            if not block_str:
                continue

            item_match = re.search(r"^(\d+)\s*-\s*(.+)", block_str)
            qty_match = re.search(r"Quantidade(?:\s*Total)?:\s*(\d+)", block_str, re.IGNORECASE)
            unit_match = re.search(r"Unidade de Fornecimento:\s*(.+)", block_str, re.IGNORECASE)
            grupo_match = re.search(r"(?:Grupo|Lote):\s*(G?\d+)", block_str, re.IGNORECASE)

            if item_match and qty_match:
                try:
                    num_item = int(item_match.group(1))
                    objeto_desc = item_match.group(2).strip()

                    lines = block_str.splitlines()
                    if len(lines) > 1:
                        additional_lines = []
                        for line in lines[1:]:
                            line_clean = line.strip()
                            if any(line_clean.startswith(k) for k in ("Descrição Detalhada:", "Tratamento", "Aplicabilidade", "Quantidade", "Critério", "Valor", "Unidade", "Intervalo", "Local", "PREGÃO")):
                                if line_clean.startswith("Descrição Detalhada:"):
                                    desc_val = line_clean.replace("Descrição Detalhada:", "").strip()
                                    if desc_val:
                                        additional_lines.append(desc_val)
                                continue
                            if line_clean and not line_clean.isdigit():
                                additional_lines.append(line_clean)
                        if additional_lines:
                            objeto_desc = (objeto_desc + " " + " ".join(additional_lines)).strip()

                    qtd = int(qty_match.group(1))
                    raw_unidade = unit_match.group(1).strip() if unit_match else "Unidade"
                    unidade_clean = clean_unidade_fornecimento(raw_unidade)

                    lote_val = None
                    if grupo_match:
                        raw_g = grupo_match.group(1).upper()
                        lote_val = raw_g if raw_g.startswith("G") else f"G{raw_g}"
                    elif num_item in grupo_mapping:
                        lote_val = grupo_mapping[num_item]

                    extracted_items.append(
                        ItemLicitacao(
                            lote=lote_val,
                            item=num_item,
                            objeto=clean_objeto_description(objeto_desc),
                            quantidade=qtd,
                            unidade_fornecimento=unidade_clean
                        )
                    )
                except Exception:
                    continue

    except Exception:
        pass

    return extracted_items


def extract_pdf_to_markdown(pdf_path: Path) -> str:
    """
    Extrai tabelas e textos sequenciais de um PDF usando pdfplumber.
    """
    markdown_sections: List[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        for page_idx, page in enumerate(pdf.pages, start=1):
            page_text = page.extract_text() or ""
            tables = page.extract_tables()

            if tables:
                markdown_sections.append(f"### Página {page_idx}")
                for table in tables:
                    md_table = table_to_markdown(table)
                    if md_table:
                        markdown_sections.append(md_table)

                if page_text.strip():
                    markdown_sections.append(f"**Contexto da Página {page_idx}:**\n{page_text.strip()}")
            elif page_text.strip():
                markdown_sections.append(f"### Página {page_idx}\n{page_text.strip()}")

    full_md = "\n\n".join(markdown_sections)

    if not full_md.strip():
        full_md = extract_ocr_to_markdown(pdf_path)

    return full_md


def extract_docx_to_markdown(docx_path: Path) -> str:
    """
    Extrai tabelas e parágrafos de um documento DOCX mantendo a ordem sequencial em Markdown.
    """
    doc = docx.Document(docx_path)
    markdown_parts: List[str] = []

    for element in doc.element.body:
        if element.tag.endswith("p"):
            para = docx.text.paragraph.Paragraph(element, doc)
            text = para.text.strip()
            if text:
                markdown_parts.append(text)
        elif element.tag.endswith("tbl"):
            table = docx.table.Table(element, doc)
            table_data: List[List[Optional[str]]] = []
            for row in table.rows:
                row_data = [cell.text.strip() for cell in row.cells]
                table_data.append(row_data)
            md_table = table_to_markdown(table_data)
            if md_table:
                markdown_parts.append(md_table)

    return "\n\n".join(markdown_parts)


def extract_odt_to_markdown(filepath: Path) -> str:
    """
    Extrai texto e tabelas de arquivos .odt (OpenDocument Text) para Markdown.
    """
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            xml_bytes = z.read('content.xml')
        root = ET.fromstring(xml_bytes)

        namespaces = {
            'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0',
            'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0'
        }

        lines = []
        for elem in root.iter():
            if elem.tag.endswith('p') or elem.tag.endswith('h'):
                text = "".join(elem.itertext()).strip()
                if text:
                    lines.append(text)
            elif elem.tag.endswith('table'):
                table_lines = []
                for row in elem.findall('.//table:table-row', namespaces):
                    row_cells = ["".join(cell.itertext()).strip() for cell in row.findall('.//table:table-cell', namespaces)]
                    if any(row_cells):
                        table_lines.append("| " + " | ".join(row_cells) + " |")
                if table_lines:
                    lines.extend(table_lines)
        return "\n".join(lines)
    except Exception as err:
        print(f"[ODT Extractor Warning] Erro ao ler {filepath.name}: {err}")
        return ""


def extract_ods_to_markdown(filepath: Path) -> str:
    """
    Extrai tabelas de planilhas .ods (OpenDocument Spreadsheet) para Markdown.
    """
    try:
        with zipfile.ZipFile(filepath, 'r') as z:
            xml_bytes = z.read('content.xml')
        root = ET.fromstring(xml_bytes)

        namespaces = {
            'table': 'urn:oasis:names:tc:opendocument:xmlns:table:1.0',
            'text': 'urn:oasis:names:tc:opendocument:xmlns:text:1.0'
        }

        lines = []
        for table in root.findall('.//table:table', namespaces):
            table_name = table.attrib.get('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}name', '')
            if table_name:
                lines.append(f"\n### Tabela: {table_name}")

            for row in table.findall('.//table:table-row', namespaces):
                row_cells = []
                for cell in row.findall('.//table:table-cell', namespaces):
                    repeat = int(cell.attrib.get('{urn:oasis:names:tc:opendocument:xmlns:table:1.0}number-columns-repeated', 1))
                    cell_text = "".join(cell.itertext()).strip()
                    if repeat > 1 and not cell_text:
                        continue
                    row_cells.append(cell_text)
                if any(row_cells):
                    lines.append("| " + " | ".join(row_cells) + " |")
        return "\n".join(lines)
    except Exception as err:
        print(f"[ODS Extractor Warning] Erro ao ler {filepath.name}: {err}")
        return ""


def extract_ocr_to_markdown(pdf_path: Path) -> str:
    """
    OCR Fallback resiliente e leve para PDFs escaneados.
    Tenta docling se disponível; caso contrário utiliza pytesseract ou pdfplumber layout.
    """
    try:
        from docling.document_converter import DocumentConverter
        converter = DocumentConverter()
        result = converter.convert(str(pdf_path))
        return result.document.export_to_markdown()
    except Exception:
        pass

    try:
        import pytesseract
        with pdfplumber.open(pdf_path) as pdf:
            text_parts = []
            for i, page in enumerate(pdf.pages, 1):
                txt = page.extract_text(layout=True) or ""
                if not txt.strip():
                    img = page.to_image(resolution=150).original
                    txt = pytesseract.image_to_string(img, lang="por")
                if txt.strip():
                    text_parts.append(f"### Página {i} (OCR)\n{txt.strip()}")
            return "\n\n".join(text_parts)
    except Exception as err:
        try:
            with pdfplumber.open(pdf_path) as pdf:
                text_parts = []
                for i, page in enumerate(pdf.pages, 1):
                    txt = page.extract_text(layout=True) or ""
                    if txt.strip():
                        text_parts.append(f"### Página {i} (Layout)\n{txt.strip()}")
                return "\n\n".join(text_parts)
        except Exception:
            raise RuntimeError(f"OCR Fallback falhou para o arquivo {pdf_path.name}: {err}")


def extract_attachment_to_markdown(filepath: Path) -> str:
    """
    Extrai o conteúdo de um arquivo PDF, DOCX, ODT ou ODS para formato Markdown.
    """
    if not filepath.exists():
        raise FileNotFoundError(f"Arquivo não existe: {filepath}")

    if filepath.stat().st_size == 0:
        raise ValueError(f"Arquivo corrompido ou vazio (0 bytes): {filepath.name}")

    ext = filepath.suffix.lower()

    if ext == ".pdf":
        return extract_pdf_to_markdown(filepath)
    elif ext == ".docx":
        return extract_docx_to_markdown(filepath)
    elif ext == ".odt":
        return extract_odt_to_markdown(filepath)
    elif ext == ".ods":
        return extract_ods_to_markdown(filepath)
    else:
        raise ValueError(f"Formato de arquivo não suportado: {ext}")
