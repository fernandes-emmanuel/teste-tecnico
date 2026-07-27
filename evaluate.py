"""
Script de Avaliação de Acurácia e Conformidade do Arquivo resultado.json.
- Valida conformidade estrutural com o schema_saida.json
- Compara resultado.json contra gabarito de referência (se fornecido)
- Mede acurácia por item usando Levenshtein (objeto >= 85%), lote, item, quantidade e unidade_fornecimento.
"""

import argparse
import json
from pathlib import Path
from typing import List, Dict, Any, Tuple
import sys

from pydantic import ValidationError
from src.models import LicitacaoProcessada


def levenshtein_distance(s1: str, s2: str) -> int:
    """Calcula a distância de Levenshtein entre duas strings."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)

    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row

    return previous_row[-1]


def text_similarity(s1: str, s2: str) -> float:
    """Retorna a similaridade normalizada [0.0 - 1.0] baseada em Levenshtein."""
    s1_clean = (s1 or "").strip().lower()
    s2_clean = (s2 or "").strip().lower()
    if not s1_clean and not s2_clean:
        return 1.0
    if not s1_clean or not s2_clean:
        return 0.0

    max_len = max(len(s1_clean), len(s2_clean))
    dist = levenshtein_distance(s1_clean, s2_clean)
    return 1.0 - (dist / max_len)


def compare_items(item_pred: Dict[str, Any], item_gt: Dict[str, Any]) -> Tuple[bool, Dict[str, bool]]:
    """
    Compara um item predito com o item gabarito:
    - item: exato (int)
    - quantidade: exato (int)
    - lote: exato ("G1", 1 ou null)
    - objeto: similaridade Levenshtein >= 85%
    - unidade_fornecimento: exato (case-insensitive string)
    """
    pred_lote = str(item_pred.get("lote")).strip() if item_pred.get("lote") is not None else None
    gt_lote = str(item_gt.get("lote")).strip() if item_gt.get("lote") is not None else None

    matches = {
        "item": int(item_pred.get("item", -1)) == int(item_gt.get("item", -2)),
        "quantidade": int(item_pred.get("quantidade", -1)) == int(item_gt.get("quantidade", -2)),
        "lote": pred_lote == gt_lote,
        "objeto": text_similarity(str(item_pred.get("objeto", "")), str(item_gt.get("objeto", ""))) >= 0.85,
        "unidade_fornecimento": str(item_pred.get("unidade_fornecimento", "")).strip().lower() == str(item_gt.get("unidade_fornecimento", "")).strip().lower()
    }
    
    all_correct = all(matches.values())
    return all_correct, matches


def evaluate_predictions(resultado_path: Path, gabarito_path: Path = None) -> None:
    """Executa a avaliação estatística e validação do resultado.json."""
    print("=" * 70)
    print("AVALIAÇÃO DE RESULTADO - PIPELINE DE EXTRAÇÃO DE LICITAÇÕES")
    print("=" * 70)

    if not resultado_path.exists():
        print(f"[ERRO] Arquivo {resultado_path} não encontrado!")
        sys.exit(1)

    with open(resultado_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, list):
        print("[ERRO] resultado.json deve ser uma lista de licitações.")
        sys.exit(1)

    print("\n1. VALIDAÇÃO ESTRUTURAL E SCHEMA (Pydantic v2)")
    print("-" * 50)
    
    total_biddings = len(data)
    valid_biddings = 0
    total_items = 0
    items_with_lote = 0

    for idx, raw_bidding in enumerate(data, start=1):
        try:
            bidding_model = LicitacaoProcessada.model_validate(raw_bidding)
            valid_biddings += 1
            total_items += len(bidding_model.itens_extraidos)
            for it in bidding_model.itens_extraidos:
                if it.lote is not None:
                    items_with_lote += 1
        except ValidationError as val_err:
            print(f"  [ERRO] Licitação #{idx} ({raw_bidding.get('arquivo_json', 'N/A')}) falhou no schema: {val_err}")

    pct_valid = (valid_biddings / total_biddings * 100) if total_biddings > 0 else 0.0
    pct_lote = (items_with_lote / total_items * 100) if total_items > 0 else 0.0

    print(f"  [OK] Licitações Validadas no Schema: {valid_biddings}/{total_biddings} ({pct_valid:.1f}%)")
    print(f"  [OK] Total de Itens Extraídos: {total_items}")
    print(f"  [OK] Itens com Lote/Grupo identificado: {items_with_lote} ({pct_lote:.1f}% dos itens)")

    # 2. Avaliação de Acurácia contra Gabarito (se fornecido)
    if gabarito_path and gabarito_path.exists():
        print(f"\n2. AVALIAÇÃO DE ACURÁCIA CONTRA GABARITO ({gabarito_path.name})")
        print("-" * 50)
        with open(gabarito_path, "r", encoding="utf-8") as f:
            gabarito_data = json.load(f)
        
        gt_dict = {b["arquivo_json"]: b for b in gabarito_data}
        
        correct_items = 0
        evaluated_items = 0
        field_matches = {"item": 0, "quantidade": 0, "lote": 0, "objeto": 0, "unidade_fornecimento": 0}

        for pred_bidding in data:
            fname = pred_bidding.get("arquivo_json")
            if fname in gt_dict:
                gt_bidding = gt_dict[fname]
                pred_items = pred_bidding.get("itens_extraidos", [])
                gt_items = gt_bidding.get("itens_extraidos", [])

                for p_item, g_item in zip(pred_items, gt_items):
                    evaluated_items += 1
                    is_correct, matches = compare_items(p_item, g_item)
                    if is_correct:
                        correct_items += 1
                    for k, v in matches.items():
                        if v:
                            field_matches[k] += 1

        if evaluated_items > 0:
            print(f"  [METRICA] Acurácia Geral de Itens: {correct_items}/{evaluated_items} ({correct_items/evaluated_items*100:.2f}%)")
            print("  Exact Match por Campo:")
            for field, count in field_matches.items():
                print(f"   - {field}: {count}/{evaluated_items} ({count/evaluated_items*100:.2f}%)")
    else:
        print("\n[INFO] Para calcular a acurácia contra o gabarito, execute:")
        print("       python evaluate.py --gabarito caminho/do/gabarito.json")

    print("\n" + "=" * 70)
    print("AVALIAÇÃO CONCLUÍDA COM SUCESSO!")
    print("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Script de Avaliação de resultado.json")
    parser.add_argument("--resultado", type=str, default="output/resultado.json", help="Caminho para o arquivo resultado.json")
    parser.add_argument("--gabarito", type=str, default=None, help="Caminho opcional para o arquivo de gabarito")
    args = parser.parse_args()

    evaluate_predictions(Path(args.resultado), Path(args.gabarito) if args.gabarito else None)
