"""
Módulo do Agente LLM para Extração Estruturada (Structured Outputs).
Suporta OpenAI, SambaNova, Google AI Studio (Gemini), OpenRouter e LLMs locais.
Inclui o padrão Circuit Breaker para pausar chamadas ao LLM quando a cota/rate limit (429) estoura.
"""

import asyncio
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
    wait_exponential,
)

from src.models import ItemLicitacao, ExtractedItemsResponse

SYSTEM_PROMPT = (
    "Você é um extrator de dados. Analise a tabela em Markdown e os parágrafos "
    "seguintes para extrair os itens. Se não houver lote explícito no texto de contexto, "
    "retorne null. A unidade de fornecimento deve ter correspondência exata ao texto original."
)


class LLMAgent:
    """
    Agente LLM encarregado de extrair itens de licitação a partir de textos Markdown.
    Implementa Circuit Breaker para fallback imediato à Camada 2 em caso de 429 persistente.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ) -> None:
        self.api_key = api_key or os.getenv("API_KEY") or os.getenv("OPENAI_API_KEY")
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
                default_headers=default_headers if default_headers else None
            )
        else:
            self.client = None

        # Circuit Breaker state
        self.circuit_broken_until: float = 0.0
        self.consecutive_rate_limits: int = 0

    @retry(
        wait=wait_exponential(multiplier=1.5, min=2, max=10),
        stop=stop_after_attempt(2),
        retry=retry_if_exception_type((RateLimitError, APIError)),
        reraise=True
    )
    async def extract_items_with_retry(self, markdown_content: str) -> List[ItemLicitacao]:
        """
        Executa a chamada assíncrona ao LLM.
        """
        if not self.client:
            raise RuntimeError("API_KEY não configurada. Defina a variável de ambiente API_KEY.")

        # Pausa preventiva leve entre requisições
        await asyncio.sleep(1.0)

        max_chars = 120000
        truncated_content = markdown_content[:max_chars]

        # 1. Structured Outputs nativos apenas para API oficial da OpenAI
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
            except Exception:
                pass

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
        Aciona o Circuit Breaker se detectar erros de cota/rate limit (429) persistentes.
        """
        if not markdown_content.strip():
            return []

        # Se o Circuit Breaker estiver ativo, ignora a API de LLM e vai direto para a Camada 2
        now = time.time()
        if now < self.circuit_broken_until:
            return []

        try:
            results = await self.extract_items_with_retry(markdown_content)
            # Se teve sucesso, reseta contadores do Circuit Breaker
            self.consecutive_rate_limits = 0
            return results
        except (NotFoundError, RateLimitError, APIError, Exception) as err:
            err_msg = str(err)
            if "429" in err_msg or "rate limit" in err_msg.lower() or "too many requests" in err_msg.lower():
                self.consecutive_rate_limits += 1
                if self.consecutive_rate_limits >= 2:
                    # Ativa o Circuit Breaker por 60 segundos
                    self.circuit_broken_until = time.time() + 60.0
                    print(f"[LLMAgent Circuit Breaker] ⚡ Cota/Rate Limit (429) excedido 2x seguidas no provedor. Pausando chamadas ao LLM por 60s e direcionando direto para extração nativa por código (Camada 2).")
                else:
                    print(f"[LLMAgent Warning] Rate limit (429) detectado. Avançando para extração nativa por código (Camada 2)...")
            elif "404" in err_msg or "not found" in err_msg.lower():
                print(f"[LLMAgent Warning] Modelo '{self.model}' não encontrado (404). Desativando LLM nesta sessão.")
                self.circuit_broken_until = time.time() + 3600.0  # Desativa por 1 hora se modelo não existir
            else:
                print(f"[LLMAgent Warning] Chamada ao LLM falhou: {err}")
            return []
