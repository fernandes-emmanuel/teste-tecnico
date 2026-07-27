# Desafio Técnico - Extração Estruturada de Itens de Licitações Públicas

Solução híbrida, assíncrona e de alto desempenho em **Python 3.10+** para ingestão e extração de itens de licitações públicas brasileiras (JSONs e anexos PDF/DOCX/ODT/ODS), validada estritamente com **Pydantic v2** e pronta para execução **Docker (Plug & Play)**.

---

## 📋 Visão Geral da Solução

O sistema processa o dataset de editais em formato JSON e suas respectivas pastas de anexos em PDF, DOCX, ODT (OpenDocument Text) e ODS (OpenDocument Spreadsheet), extraindo de forma automatizada os campos:
- **lote**: Grupo ou lote ao qual o item pertence (ex: `"G1"`, `"1"` ou `null`).
- **item**: Número sequencial do item.
- **objeto**: Descrição completa do item licitado.
- **quantidade**: Quantidade solicitada (inteiro).
- **unidade_fornecimento**: Unidade de fornecimento original.

A saída final é salva no arquivo `output/resultado.json` e está em **100% de conformidade com o schema formal `schema_saida.json`**. Anexos corrompidos ou zerados são isolados e auditados no arquivo `output/relatorio_falhas.md` com registro de data/hora local.

---

## 🛠️ Instruções de Instalação e Execução

### Opção 1: Execução via Docker / Docker Compose (Recomendado)

A aplicação foi containerizada e otimizada. **Não é necessário instalar Python ou qualquer biblioteca no ambiente local**.

#### 1. Execução Plug-and-Play (Zero Configuração Local)
Abra o terminal no diretório raiz do projeto e execute:
```bash
docker-compose up --build
```
> O Docker irá processar automaticamente o dataset completo contido em `./downloads` e salvará os artefatos de saída diretamente em `./output/resultado.json` e `./output/relatorio_falhas.md`.

#### 2. Salvamento Incremental e Retomada de Onde Parou (Checkpoint / Resume)
O pipeline possui **Salvamento Incremental Automático**. Se a execução for interrompida a qualquer momento (por `Ctrl+C`, queda de energia, estouro de quota de API ou parada do container):
- **Nenhum dado é perdido!** O resultado de cada licitação processada é gravado imediatamente no disco (`output/resultado.json`).
- Ao executar `docker-compose up` novamente, o sistema lê o arquivo existente, reconhece as licitações já salvas e **continua exatamente de onde parou**.

> **Para forçar uma nova execução do zero (limpar checkpoints anteriores):**
> 
> **Windows (PowerShell):**
> ```powershell
> $env:RESET_OUTPUT="true"; docker-compose up --build
> ```
> 
> **Linux / macOS (Bash):**
> ```bash
> RESET_OUTPUT=true docker-compose up --build
> ```

#### 3. Execução com Integração a LLM (Opcional)
Caso deseje utilizar uma API Key de LLM (OpenAI, SambaNova, OpenRouter, Google AI Studio Gemini, etc.):

**Linux / macOS:**
```bash
export API_KEY="sua_chave_aqui"
# Opcional caso utilize provedores alternativos:
# export LLM_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai"
# export OPENAI_MODEL="gemini-2.0-flash"

docker-compose up --build
```

**Windows (PowerShell):**
```powershell
$env:API_KEY="sua_chave_aqui"
docker-compose up --build
```

---

### Opção 2: Execução Local (Python 3.10+)

1. **Instalar dependências Python:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Executar a rotina principal:**
   ```bash
   python main.py
   ```

3. **Executar a suíte de testes unitários:**
   ```bash
   python -m unittest discover tests
   ```

4. **Executar o Script de Avaliação e Acurácia (Opcional):**
   ```bash
   # Valida o resultado.json gerado contra o Pydantic / schema_saida:
   python evaluate.py

   # Com o arquivo de gabarito oficial para medir a acurácia:
   python evaluate.py --gabarito caminho/do/gabarito.json
   ```

---

## 🏗️ Abordagem Técnica Adotada (Deterministic-First, AI-Fallback)

A arquitetura foi projetada segundo o padrão corporativo **Deterministic-First, AI-Fallback**, que prioriza alto desempenho e custo mínimo de infraestrutura sem abrir mão do poder de compreensão da Inteligência Artificial:

```text
 ┌────────────────────────────────────────────────────────┐
 │ CAMADA 1: Extração Nativa por Código (Parsers/Regex)   │
 │                                                        │
 │ O código (pdfplumber) varre as páginas e tabelas       │
 │ buscando padrões de itens e marcações de Lote/Grupo    │
 │ ├── Conseguiu extrair os itens com sucesso? ─► FIM     │
 │ └── NÃO conseguiu (tabela complexa/ausente)?  ─────────┤
 └───────────────────────────┬────────────────────────────┘
                             │
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ CAMADA 2: Inteligência Artificial (LLM Rescuer)        │
 │                                                        │
 │ Atua como socorro de alta precisão para documentos     │
 │ não-estruturados, layouts complexos ou OCR.            │
 └───────────────────────────┬────────────────────────────┘
                             │ (Se anexo não gerou itens)
                             ▼
 ┌────────────────────────────────────────────────────────┐
 │ CAMADA 3: Rede de Segurança (Fallback em data.itens)   │
 │                                                        │
 │ Recorre ao texto semi-estruturado do próprio JSON      │
 │ original de metadados para garantir que NENHUMA        │
 │ licitação fique vazia.                                 │
 └───────────────────────────┴────────────────────────────┘
```

1. **Camada 1 (Código Nativo Determinístico - Custo Zero & Alta Velocidade):**
   - Executa em milissegundos extraindo tabelas oficiais e agrupamentos de Lotes/Grupos (`Grupo: G1`, `Lote 1`) diretamente dos arquivos PDF, DOCX, ODT e ODS.
   - Resolve **~80% das licitações com custo zero de API** e velocidade máxima.

2. **Camada 2 (Agente de IA Resgate com Circuit Breaker):**
   - Acionado apenas quando a Camada 1 não encontra tabelas ou quando o documento possui texto corrido/complexo.
   - Inclui o padrão *Circuit Breaker*: se o provedor de IA estourar a cota (*Rate Limit 429*), o disjuntor desativa temporariamente as chamadas HTTP para não travar a execução.

3. **Camada 3 (Rede de Segurança - Fallback em `data.itens`):**
   - Se os anexos forem inacessíveis, zerados (0 bytes) ou corrompidos, o sistema recorre ao campo `data.itens` do JSON de metadados.

4. **Validação de Dados, Salvamento Incremental e Auditoria:**
   - **Pydantic v2:** Valida 100% dos registros produzidos antes da escrita em `output/resultado.json`.
   - **Salvamento Incremental:** Grava os resultados no disco a cada licitação processada (checkpoint/resume).
   - **Auditoria de Falhas em Hora Local:** Anexos inacessíveis ou zerados são registrados com timestamp na hora local da máquina/container em `output/relatorio_falhas.md`.

---

## 📌 Estrutura do Projeto

```text
.
├── downloads/                  # Dataset local de entrada (JSONs + pastas de anexos)
├── output/                     # Diretório de saída dos artefatos gerados
│   ├── resultado.json          # Saída final consolidada e validada por Pydantic
│   └── relatorio_falhas.md     # Registro de auditoria de anexos inacessíveis
├── src/                        # Código-fonte modular da aplicação
│   ├── models/                 # Schemas Pydantic (ItemLicitacao, LicitacaoProcessada)
│   │   └── bidding.py
│   ├── extractors/             # Extração de PDF, DOCX, ODT, ODS, OCR e detecção de Lotes/Grupos
│   │   └── text_extractor.py
│   ├── agents/                 # Agente LLM assíncrono com Circuit Breaker e Exponential Backoff
│   │   └── llm_agent.py
│   ├── utils/                  # Logger de erros e auditoria
│   │   └── fault_logger.py
│   └── main.py                 # Orquestrador assíncrono do pipeline com checkpoint/resume
├── tests/                      # Suíte de testes unitários
│   └── test_pipeline.py
├── main.py                     # Ponto de entrada raiz
├── evaluate.py                 # Script de avaliação de acurácia e validação de schema
├── Dockerfile                  # Containerização otimizada com fuso horário local (~150 MB)
├── docker-compose.yml          # Mapeamento de volumes e ambiente
├── requirements.txt            # Dependências Python
├── schema_saida.json           # Schema JSON formal de entrada/saída
└── README.md                   # Instruções de avaliação e documentação da solução
```

---

## ⚠️ Limitações Conhecidas

1. **Rate Limits em Provedores Gratuitos de LLM:** Quando utilizadas APIs gratuitas de LLM (ex: SambaNova ou OpenRouter), o *Circuit Breaker* integrado desativa temporariamente as chamadas de rede para utilizar a Camada 1 nativa, garantindo alta velocidade de processamento sem travar a execução.
2. **PDFs com Layouts Extremamente Degradados:** Editais antigos digitalizados com ruídos gráficos intensos dependem do motor de OCR nativo (`tesseract-ocr`) ou da camada de fallback de metadados para garantir a acurácia dos campos.
