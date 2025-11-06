from __future__ import annotations

from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.orchestration import composer
from src.orchestration.state import OrchestrationState


def test_render_answer_uses_fallback_when_template_missing(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(composer, "PROMPTS", tmp_path)

    state = OrchestrationState(
        user_query="대출 한도가 궁금해",
        mode="info",
        sources=["https://example.com"],
    )
    state.answer = "대출 한도는 최대 3억원입니다."
    state.response_message = state.answer
    state.confidence = {"passed": True, "reason": None, "thresholds": {}}

    rendered = composer.render_answer(state)

    assert rendered.strip().startswith("대출 한도는 최대 3억원입니다.")
    assert "충족" in rendered


def test_render_answer_custom_template(monkeypatch, tmp_path: Path) -> None:
    template = """요약 {{ summary }}\n월상환 {{ calc.repayment.monthly_payment }}"""
    (tmp_path / "composer_answer.txt").write_text(template, encoding="utf-8")

    monkeypatch.setattr(composer, "PROMPTS", tmp_path)

    state = OrchestrationState(
        user_query="한도 얼마?",
        mode="calc",
    )
    state.calc = {
        "summary": "예상 한도는 3억원입니다.",
        "repayment": {"monthly_payment": 980000, "term_months": 360},
    }
    state.response_message = state.calc["summary"]

    rendered = composer.render_answer(state)
    assert "예상 한도는 3억원입니다." in rendered
    assert "980000" in rendered


def test_render_answer_formats_informational_sections(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(composer, "PROMPTS", tmp_path)

    message = (
        "청년주택담보대출은 무주택 서민을 대상으로 주택도시기금을 통해 낮은 금리로 제공되는 주택담보대출 상품입니다. "
        "- **대출 한도 및 기간**: 최대 대출한도 일반 2억원, 생애최초 2.4억원, 신혼·2자녀 3.2억원, 대출 만기 10~30년, 거치기간 1년 또는 비거치 선택 가능 "
        "- **신청 조건**: 민법상 성년 대한민국 국민, 세대주 및 세대원 전원 무주택, CB점수 350점 이상, 순자산 가액 4.88억원 이하"
    )

    state = OrchestrationState(user_query="청년주택담보대출", mode="info")
    state.response_message = message

    rendered = composer.render_answer(state)

    assert "## 대출 한도 및 기간" in rendered
    assert "- 최대 대출한도" in rendered
    assert "## 신청 조건" in rendered
    assert "- 민법상 성년 대한민국 국민" in rendered
