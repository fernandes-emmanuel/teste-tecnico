"""
Definição dos Schemas Pydantic para validação estrita dos dados extraídos.
Alinhado ao schema_saida.json e Pydantic v2.
"""

from typing import Optional, List
from pydantic import BaseModel, Field


class ItemLicitacao(BaseModel):
    """
    Representa um item individual licitado.
    """
    lote: Optional[str] = Field(
        default=None,
        description="Identificador do lote/grupo ao qual o item pertence (ex: 'G1', '1'). Null se não houver lote."
    )
    item: int = Field(
        ...,
        ge=1,
        description="Número sequencial do item dentro da licitação."
    )
    objeto: str = Field(
        ...,
        description="Descrição completa do item licitado, incluindo categoria e especificações técnicas."
    )
    quantidade: int = Field(
        ...,
        ge=1,
        description="Quantidade solicitada do item."
    )
    unidade_fornecimento: str = Field(
        ...,
        description="Unidade de fornecimento do item (ex: 'Unidade', 'Caixa 50,00 UN', 'Pacote 500,00 FL')."
    )


class ExtractedItemsResponse(BaseModel):
    """
    Modelo de resposta estruturada para o agente LLM.
    """
    itens: List[ItemLicitacao] = Field(
        default_factory=list,
        description="Lista de itens de licitação extraídos do documento."
    )


class LicitacaoProcessada(BaseModel):
    """
    Representa o registro consolidado de uma licitação processada.
    """
    arquivo_json: str = Field(
        ...,
        description="Nome do arquivo JSON de origem (ex: '2024-08-15-09-33-44-conlicitacao-xxxx.json')."
    )
    numero_pregao: str = Field(
        ...,
        description="Número do pregão eletrônico (ex: 'PE/90007/2024')."
    )
    orgao: str = Field(
        ...,
        description="Nome completo do órgão público responsável pela licitação."
    )
    cidade: str = Field(
        ...,
        description="Município do órgão licitante."
    )
    estado: str = Field(
        ...,
        pattern=r"^[A-Z]{2}$",
        description="UF do órgão licitante (sigla de 2 caracteres)."
    )
    anexos_processados: List[str] = Field(
        default_factory=list,
        description="Lista de nomes dos arquivos de anexo que foram efetivamente processados para extração."
    )
    itens_extraidos: List[ItemLicitacao] = Field(
        default_factory=list,
        description="Lista de itens extraídos dos anexos da licitação."
    )
