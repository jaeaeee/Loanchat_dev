"""Tests for mortgage slot extraction heuristics."""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


from src.nlp.slot_intent import extract_intent_and_slots


def test_interest_rates_not_misclassified_as_amounts() -> None:
    message = "주택담보대출 금리 4.2%, 30년 원리금균등으로 4억 원 빌리면 월 납입액이 얼마일까?"
    slots = extract_intent_and_slots(message)["slots"]

    assert slots["interest_rate"] == 4.2
    assert slots["interest_rate_unit"] == "percent"
    assert slots["term_months"] == 360
    assert slots["loan_amount"] == 400_000_000


def test_rate_change_question_does_not_create_fake_principal() -> None:
    message = "변동금리 3.5%에서 5%로 오르면 월 상환액이 얼마나 늘어나?"
    slots = extract_intent_and_slots(message)["slots"]

    assert slots["interest_rate"] == 3.5
    assert slots["interest_rate_unit"] == "percent"
    assert "loan_amount" not in slots


def test_prepayment_fee_percentage_not_treated_as_amount() -> None:
    message = "중도상환수수료 1.2%가 남은 대출잔액 1억 5천만 원에 적용되면 수수료는 얼마야?"
    slots = extract_intent_and_slots(message)["slots"]

    assert slots["fee_rate"] == 1.2
    assert slots["fee_rate_unit"] == "percent"
    assert slots["principal"] == 150_000_000
    assert slots["loan_amount"] == 150_000_000


def test_dsr_question_extracts_income_debt_and_target() -> None:
    message = "연소득 6000만원, 기존 부채 월 80만, 금리 4.5%, 30년, DSR 40% 기준 대출 한도는?"
    slots = extract_intent_and_slots(message)["slots"]

    assert slots["annual_income"] == 60_000_000
    assert slots["monthly_debt_payment"] == 800_000
    assert slots["annual_debt_service"] == 9_600_000
    assert slots["target_dsr"] == 40.0


def test_compound_korean_amount_parsed_correctly() -> None:
    message = "집값 1억5천만 원"
    slots = extract_intent_and_slots(message)["slots"]

    assert slots["collateral_value"] == 150_000_000


def test_informational_definition_detected() -> None:
    analysis = extract_intent_and_slots("청년주택담보대출이란")
    assert analysis.get("intent") == "info"


def test_informational_documents_question_detected() -> None:
    analysis = extract_intent_and_slots("온라인으로 제출 가능한 서류가 있나요?")
    assert analysis.get("intent") == "info"


def test_informational_regex_variants_detected() -> None:
    phrases = [
        "필요 서류와 절차를 알려줘",
        "자격 요건이 궁금합니다",
        "상환 조건은 어떻게 되는지 설명",
    ]
    for phrase in phrases:
        analysis = extract_intent_and_slots(phrase)
        assert analysis.get("intent") == "info"


def test_informational_without_numeric_slots_but_calc_keywords() -> None:
    analysis = extract_intent_and_slots("LTV 정의 알려줘")
    assert analysis.get("intent") == "info"


def test_informational_impact_question_even_with_calc_keywords() -> None:
    analysis = extract_intent_and_slots("금리 인상이 DSR에 영향이 있나요?")
    assert analysis.get("intent") == "info"
