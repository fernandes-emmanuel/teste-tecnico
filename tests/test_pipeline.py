"""
Testes unitários e de integração para o pipeline de extração de licitações usando unittest.
"""

import unittest
from pathlib import Path
import tempfile

from src.models import ItemLicitacao, LicitacaoProcessada
from src.extractors import is_relevant_attachment, table_to_markdown
from src.utils import log_failure
from src.main import parse_fallback_itens


class TestLicitacaoPipeline(unittest.TestCase):

    def test_models_validation(self):
        """Testa a validação estrita dos schemas Pydantic."""
        item = ItemLicitacao(
            lote="G1",
            item=1,
            objeto="Cadeira Giratória Executiva Ergonomia NBR",
            quantidade=10,
            unidade_fornecimento="Unidade"
        )
        self.assertEqual(item.item, 1)
        self.assertEqual(item.lote, "G1")

        licitacao = LicitacaoProcessada(
            arquivo_json="2024-08-15-test.json",
            numero_pregao="PE/90001/2024",
            orgao="Órgão Teste",
            cidade="Brasília",
            estado="DF",
            anexos_processados=["edital.pdf"],
            itens_extraidos=[item]
        )
        self.assertEqual(licitacao.estado, "DF")
        self.assertEqual(len(licitacao.itens_extraidos), 1)

    def test_is_relevant_attachment(self):
        """Testa a lógica de triagem de anexos."""
        self.assertTrue(is_relevant_attachment("edital.pdf"))
        self.assertTrue(is_relevant_attachment("termo_referencia.pdf"))
        self.assertTrue(is_relevant_attachment("relacaoitens123.pdf"))
        self.assertTrue(is_relevant_attachment("modelo_proposta.docx"))
        self.assertFalse(is_relevant_attachment("imagem.png"))
        self.assertFalse(is_relevant_attachment("contrato_minuta.pdf"))

    def test_table_to_markdown(self):
        """Testa a conversão de tabela 2D para Markdown."""
        raw_table = [
            ["Item", "Descrição", "Qtd"],
            ["1", "Notebook Core i7", "5"]
        ]
        md = table_to_markdown(raw_table)
        self.assertIn("| Item | Descrição | Qtd |", md)
        self.assertIn("| 1 | Notebook Core i7 | 5 |", md)

    def test_fault_logger(self):
        """Testa a gravação de falhas em relatorio_falhas.md."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            log_file = Path(tmp_dir) / "relatorio_falhas_test.md"
            log_failure(
                json_filename="test_origem.json",
                attachment_name="anexo_corrompido.pdf",
                error_message="Arquivo corrompido (0 bytes)",
                report_path=str(log_file)
            )

            self.assertTrue(log_file.exists())
            content = log_file.read_text(encoding="utf-8")
            self.assertIn("test_origem.json", content)
            self.assertIn("anexo_corrompido.pdf", content)
            self.assertIn("Arquivo corrompido (0 bytes)", content)

    def test_parse_fallback_itens(self):
        """Testa o parser de fallback de itens."""
        raw_itens = [
            "1 - CADERNO UNIVERSITÁRIO\nCADERNO 100 FOLHAS CAPA DURA\nQuantidade: 20\nUnidade de fornecimento: Unidade\n----------\n2 - CANETA ESFEROGRÁFICA\nCANETA AZUL 1.0MM\nQuantidade: 50\nUnidade de fornecimento: Caixa 50 UN"
        ]
        parsed = parse_fallback_itens(raw_itens)
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].item, 1)
        self.assertEqual(parsed[0].quantidade, 20)
        self.assertEqual(parsed[1].item, 2)
        self.assertEqual(parsed[1].quantidade, 50)


if __name__ == "__main__":
    unittest.main()
