"""
Módulo do Agente LLM para Extração Estruturada (Structured Outputs).
Suporta OpenAI, SambaNova, Google AI Studio (Gemini), OpenRouter e LLMs locais.
Inclui o padrão Circuit Breaker para desativar chamadas ao LLM em erros de cota (429) ou autenticação (401/403) com avisos explícitos no console.
"""

import asyncio
import logging
import os
import time
from typing import List, Optional
import json
import re

from openai import AsyncOpenAI, APIError, RateLimitError, NotFoundError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_fixed,
)

from src.models import ItemLicitacao, ExtractedItemsResponse

logger = logging.getLogger("LicitacaoPipeline")

SYSTEM_PROMPT = (
    "Você é um extrator de dados. Analise a tabela em Markdown e os parágrafos "
    "seguintes para extrair os itens. Se não houver lote explícito no texto de contexto, "
    "retorne null. A unidade de fornecimento deve ter correspondência exata ao texto original."
)


class LLMAgent:
    """
    Agente LLM encarregado de extrair itens de licitação a partir de textos Markdown.
    Implementa Circuit Breaker com alertas explícitos no console em caso de 429 ou erro de autenticação (401/403).
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ) -> None:
        raw_key = api_key or os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY")

        # Se a chave for um placeholder genérico ou vazia, desativa o cliente para evitar requisições desnecessárias
        if raw_key and any(ph in raw_key.lower() for ph in ("sua_chave", "your_key", "placeholder", "xxx", "dummy")) or (raw_key and len(raw_key.strip()) < 10):
            self.api_key = None
            logger.warning("[LLMAgent] ⚠️ Chave de API não configurada ou placeholder ('sua_chave_aqui') detectada. LLM permanecerá DESATIVADO nesta sessão. O pipeline utilizará extração nativa por código (Camada 1) e fallback de segurança (Camada 3).")
        else:
            self.api_key = raw_key.strip() if raw_key else None

        raw_base_url = base_url or os.getenv("LLM_BASE_URL")

        if raw_base_url:
            self.base_url = raw_base_url.rstrip("/")
            if "googleapis.com" in self.base_url and not self.base_url.endswith("/openai"):
                self.base_url = self.base_url + "/openai"
        else:
            self.base_url = None

        self.model = model or os.getenv("OPENAI_MODEL", "gpt-4o-mini")

        if self.api_key:
            default_headers = {}
            if self.base_url and "openrouter.ai" in self.base_url:
                default_headers = {
                    "HTTP-Referer": "https://github.com/licitacao-extractor",
                    "X-Title": "Licitacao Extractor"
                }

            self.client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                default_headers=default_headers if default_headers else None,
                max_retries=1
            )
            logger.info(f"[LLMAgent] cliente de IA ativado com sucesso para o modelo '{self.model}'.")
        else:
            self.client = None

        # Circuit Breaker state
        self.circuit_broken_until: float = 0.0
        self.consecutive_rate_limits: int = 0

    @retry(
        wait=wait_fixed(1.0),
        stop=stop_after_attempt(1),
        retry=retry_if_exception_type((RateLimitError, APIError)),
        reraise=True
    )
    async def extract_items_with_retry(self, markdown_content: str) -> List[ItemLicitacao]:
        """
        Executa a chamada assíncrona ao LLM com no máximo 1 retentativa rápida em falhas temporárias.
        """
        if not self.client:
            return []

        max_chars = 120000
        truncated_content = markdown_content[:max_chars]

        # 1. Structured Outputs nativos para API oficial da OpenAI
        if not self.base_url or "api.openai.com" in self.base_url:
            try:
                completion = await self.client.beta.chat.completions.parse(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": f"Extraia os itens do seguinte conteúdo Markdown:\n\n{truncated_content}"}
                    ],
                    response_format=ExtractedItemsResponse,
                    temperature=0.0,
                )
                response_payload = completion.choices[0].message.parsed
                if response_payload:
                    return response_payload.itens
            except Exception as err:
                err_msg = str(err)
                if any(code in err_msg for code in ("401", "403", "invalid_api_key", "Unauthorized", "Forbidden")):
                    raise err  # Relança para ser tratado pelo Circuit Breaker permanente

        # 2. Chamada universal compatível com SambaNova, OpenRouter, Gemini, etc.
        prompt_instruction = (
            f"{SYSTEM_PROMPT}\n\n"
            "Formato de resposta obrigatório: Responda APENAS em JSON válido no formato:\n"
            '{"itens": [{"lote": "G1" ou null, "item": 1, "objeto": "descrição", "quantidade": 10, "unidade_fornecimento": "Unidade"}]}'
        )

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": prompt_instruction},
                {"role": "user", "content": f"Extraia os itens do seguinte conteúdo Markdown:\n\n{truncated_content}"}
            ],
            temperature=0.0,
        )

        raw_json = response.choices[0].message.content or "{}"

        cleaned_json = re.sub(r"^```(?:json)?\s*", "", raw_json.strip(), flags=re.MULTILINE)
        cleaned_json = re.sub(r"\s*```$", "", cleaned_json, flags=re.MULTILINE).strip()

        try:
            parsed_dict = json.loads(cleaned_json)
            if isinstance(parsed_dict, list):
                parsed_dict = {"itens": parsed_dict}
            parsed_response = ExtractedItemsResponse.model_validate(parsed_dict)
            return parsed_response.itens
        except Exception as parse_err:
            raise APIError(message=f"Falha ao interpretar JSON retornado pelo modelo {self.model}: {parse_err}", request=None, body=None)

    async def extract_items(self, markdown_content: str) -> List[ItemLicitacao]:
        """
        Ponto de entrada seguro para extração de itens.
        Aciona o Circuit Breaker se detectar erros de cota (429) ou autenticação (401/403) exibindo aviso explícito no console.
        """
        if not self.client or not markdown_content.strip():
            return []

        # Se o Circuit Breaker estiver ativo, ignora a API de LLM e avança imediatamente para a próxima camada
        now = time.time()
        if now < self.circuit_broken_until:
            return []

        try:
            results = await self.extract_items_with_retry(markdown_content)
            self.consecutive_rate_limits = 0
            return results
        except Exception as err:
            err_msg = str(err)
            if any(code in err_msg for code in ("401", "403", "invalid_api_key", "Unauthorized", "Forbidden")):
                logger.warning(f"[LLMAgent Circuit Breaker] ⛔ ERRO DE AUTENTICAÇÃO LLM (401/403): Chave de API inválida ou não autorizada. O LLM foi DESATIVADO permanentemente nesta sessão. O pipeline utilizará extração nativa por código e fallback.")
                self.circuit_broken_until = float("inf")
            elif "429" in err_msg or "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower():
                self.consecutive_rate_limits += 1
                if self.consecutive_rate_limits >= 2:
                    self.circuit_broken_until = time.time() + 60.0
                    logger.warning("[LLMAgent Circuit Breaker] ⚡ RATE LIMIT (429) EXCEDIDO 2x SEGUIDAS: Pausando chamadas ao LLM por 60s e direcionando para extração nativa por código.")
                else:
                    logger.warning("[LLMAgent Warning] Rate limit (429) detectado no LLM. Avançando para fallback nativo...")
            elif "404" in err_msg or "not found" in err_msg.lower():
                logger.warning(f"[LLMAgent Warning] Modelo '{self.model}' não encontrado (404). Desativando LLM nesta sessão.")
                self.circuit_broken_until = float("inf")
            else:
                logger.warning(f"[LLMAgent Warning] Chamada ao LLM falhou: {err}")
            return []
