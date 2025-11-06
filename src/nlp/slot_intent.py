"""의도/슬롯 추출 모듈."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional, Protocol

logger = logging.getLogger(__name__)

_CALC_KEYWORDS = {
    "얼마",
    "한도",
    "계산",
    "원리금",
    "상환",
    "dti",
    "dsr",
    "ltv",
    "금리",
    "이자",
    "대출",
}
_STRONG_CALC_KEYWORDS = {
    "얼마",
    "한도",
    "계산",
    "원리금",
    "상환",
    "ltv",
    "dti",
    "dsr",
}
_INFO_KEYWORDS = {
    "무엇",
    "설명",
    "정의",
    "이란",
    "조건",
    "요건",
    "필요",
    "절차",
    "방법",
    "가능한가",
    "알려줘",
}
_QUESTION_TOKENS = {"?", "어떻게", "왜", "언제", "어디", "무엇"}
INFO_PATTERNS = [
    r"(가능(?:한가|할까|해)?)",
    r"(무엇|뭔|뭐|정의|이란|란|뜻|의미)",
    r"(방법|어떻게|절차|순서|흐름|하는\s*법|가이드)",
    r"(설명|알려\s*줘|알고\s*싶|궁금)",
    r"(요건|조건|자격|필요|필수|서류|준비물)",
    r"(발급|제출|온라인|비대면|대체|대리|위임)",
    r"(예외|확인|증빙|증명|서류명|구비\s*서류)",
    r"(후순위|2\s*순위|담보\s*순위|영향|중복\s*신청)",
]
INFO_REGEX = re.compile("|".join(INFO_PATTERNS), re.IGNORECASE)
_IMPACT_QUESTION_PATTERN = re.compile(
    r"영향\s*이\s*있(나요|을까요|어\?)\s*$", re.IGNORECASE
)
_CALC_SLOT_KEYS = {
    "loan_amount",
    "principal",
    "remaining_principal",
    "outstanding_principal",
    "interest_rate",
    "rate",
    "months",
    "term_months",
    "fee_rate",
    "annual_income",
    "annual_debt_service",
    "total_debt_payment",
    "monthly_debt_payment",
    "target_dsr",
    "target_dti",
    "collateral_value",
    "property_value",
}
CALC_INTENT_PATTERNS = [
    r"계산\s*해줘|계산해\s*줘|계산\s*해|계산해주세요",
    r"얼마\s*(가능|받을\s*수|나올까|될까|나오나요)",
    r"한도\s*(알려줘|계산|조회|확인)",
    r"ltv\s*계산|dti\s*계산|dsr\s*계산",
]
CALC_INTENT_REGEX = re.compile("|".join(CALC_INTENT_PATTERNS), re.IGNORECASE)
INFO_EXCEPTIONS = re.compile(
    r"기준|조건|정의|의미|이란|절차|방법|서류|발급|제출|온라인|대체|예외|영향|중복\s*신청|확인|증빙",
    re.IGNORECASE,
)

_INCOME_KEYWORDS = ("연소득", "연 봉", "연봉", "연간소득", "소득")
_DEBT_KEYWORDS = ("부채", "상환")
_COLLATERAL_KEYWORDS = (
    "집값",
    "주택가격",
    "주택 가격",
    "담보",
    "시세",
    "매매가",
    "아파트값",
)
_LOAN_KEYWORDS = ("대출", "대출금", "대출액", "원금", "잔금", "대출잔액", "잔액")
_MONTH_KEYWORDS = ("월", "매월", "월별")
_RATE_INTEREST_KEYWORDS = ("금리", "이자", "연이율", "연 이자율")
_RATE_DSR_KEYWORDS = ("dsr", "디에스알")
_RATE_DTI_KEYWORDS = ("dti", "디티아이")
_RATE_LTV_KEYWORDS = ("ltv", "담보비율", "담보 비율", "담보인정", "담보 인정")
_RATE_PREPAYMENT_KEYWORDS = ("중도상환", "수수료")


def _iter_amount_spans(message: str) -> list[tuple[int, int, str]]:
    spans: list[tuple[int, int, str]] = []
    length = len(message)
    index = 0
    while index < length:
        char = message[index]
        if char.isdigit():
            start = index
            last_valid = index + 1
            index += 1
            while index < length:
                current = message[index]
                if current.isdigit() or current in {",", "."}:
                    last_valid = index + 1
                    index += 1
                    continue
                if current.isspace():
                    index += 1
                    continue
                if current in _AMOUNT_UNIT_CHARS:
                    last_valid = index + 1
                    index += 1
                    continue
                break
            if last_valid > start:
                spans.append((start, last_valid, message[start:last_valid]))
            index = last_valid
        else:
            index += 1
    # remove duplicates
    unique: dict[tuple[int, int], str] = {}
    for start, end, token in spans:
        unique.setdefault((start, end), token)
    ordered = sorted(unique.items(), key=lambda item: item[0][0])
    return [(start, end, token) for (start, end), token in ordered]


def _parse_basic_number(fragment: str) -> float:
    fragment = fragment.strip()
    if not fragment:
        return 1.0
    try:
        return float(fragment)
    except ValueError:
        pass
    total = 0.0
    remaining = fragment
    for unit, multiplier in (("천", 1000.0), ("백", 100.0), ("십", 10.0)):
        while unit in remaining:
            idx = remaining.find(unit)
            head = remaining[:idx]
            number = float(head) if head else 1.0
            total += number * multiplier
            remaining = remaining[idx + len(unit) :]
    if remaining:
        try:
            total += float(remaining)
        except ValueError:
            pass
    return total


def _normalize_amount_token(raw: str) -> Optional[int]:
    cleaned = raw.replace(",", "").replace(" ", "").strip()
    if cleaned.endswith("원"):
        cleaned = cleaned[: -len("원")]
    if not cleaned:
        return None

    total = 0.0
    remaining = cleaned

    for unit, multiplier in (("억", 100_000_000.0), ("만", 10_000.0)):
        while unit in remaining:
            idx = remaining.find(unit)
            head = remaining[:idx]
            part = _parse_basic_number(head)
            total += part * multiplier
            remaining = remaining[idx + len(unit) :]

    if remaining:
        total += _parse_basic_number(remaining)

    if total <= 0:
        return None
    return int(round(total))


_RATE_PATTERN = re.compile(r"(?P<rate>\d+(?:\.\d+)?)\s*%")
_TERM_YEAR_PATTERN = re.compile(r"(?P<years>\d+)\s*년")
_TERM_MONTH_PATTERN = re.compile(r"(?P<months>\d+)\s*개월?")

_AMOUNT_UNIT_CHARS = {"억", "만", "천", "백", "십", "원"}


class LLMClient(Protocol):
    """LLM 보조 호출용 최소 인터페이스."""

    def invoke(self, prompt: str, *, temperature: float = 0.0) -> str: ...


@dataclass
class RuleAnalysis:
    intent: str
    slots: Dict[str, Any]
    confidence: Dict[str, Any]


def extract_intent_and_slots(message: str) -> dict[str, object]:
    """규칙 기반 분석 + (선택적) LLM 보조 결과를 반환한다."""

    message = (message or "").strip()
    if not message:
        return {
            "intent": "info",
            "slots": {},
            "confidence": {"score": 0.0, "source": "empty", "signals": {}},
        }

    rule = _rule_based_analysis(message)
    llm = _call_llm_router(message, rule)
    merged = _merge_results(rule, llm)
    return merged


def _rule_based_analysis(message: str) -> RuleAnalysis:
    lowered = message.lower()
    tokens = set(lowered.split())
    has_number = bool(re.search(r"\d", message))

    calc_hits = sum(1 for kw in _CALC_KEYWORDS if kw in lowered)
    calc_intent = CALC_INTENT_REGEX.search(message) is not None
    if calc_intent:
        calc_hits += 2
    info_hits = sum(1 for _ in INFO_REGEX.finditer(message))
    question_hits = sum(1 for kw in _QUESTION_TOKENS if kw in lowered or kw in tokens)
    has_strong_calc_keyword = any(kw in lowered for kw in _STRONG_CALC_KEYWORDS)

    slots = _extract_slots(message)

    has_calc_slots = any(key in slots for key in _CALC_SLOT_KEYS)
    info_regex_hit = info_hits > 0
    impact_question = _IMPACT_QUESTION_PATTERN.search(message) is not None
    info_exception = INFO_EXCEPTIONS.search(message) is not None

    info_priority = False
    if info_regex_hit and not has_calc_slots:
        info_priority = True
    if impact_question:
        info_priority = True
    if info_exception:
        info_priority = True
    if info_priority and info_hits == 0:
        info_hits = 1

    calc_condition = False
    if not info_priority:
        if calc_intent and not info_exception:
            calc_condition = True
        elif calc_hits and has_number:
            calc_condition = True
        elif calc_hits > info_hits:
            if has_number or has_strong_calc_keyword or calc_hits >= 2:
                calc_condition = True

    if calc_condition:
        intent = "calc"
        base = 0.55 + min(calc_hits, 3) * 0.1
        if has_number:
            base += 0.1
        if slots:
            base += 0.05
    else:
        intent = "info"
        base = 0.5 + min(info_hits, 2) * 0.1
        if question_hits:
            base += 0.05
        if not has_number:
            base += 0.05

    score = max(0.0, min(base, 0.92))

    confidence = {
        "score": round(score, 3),
        "source": "rule",
        "signals": {
            "calc_hits": calc_hits,
            "info_hits": info_hits,
            "question_hits": question_hits,
            "has_number": has_number,
            "slot_count": len(slots),
        },
    }

    return RuleAnalysis(intent=intent, slots=slots, confidence=confidence)


def _extract_slots(message: str) -> Dict[str, Any]:
    slots: Dict[str, Any] = {}

    def _context_before(span: tuple[int, int], window: int = 12) -> str:
        start = max(0, span[0] - window)
        return message[start : span[0]].lower()

    def _context_after(span: tuple[int, int], window: int = 12) -> str:
        end = min(len(message), span[1] + window)
        return message[span[1] : end].lower()

    def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
        return any(keyword in text for keyword in keywords)

    rate_matches = list(_RATE_PATTERN.finditer(message))
    rate_spans: list[tuple[int, int]] = []
    interest_assigned = False

    for match in rate_matches:
        span = match.span()
        rate_spans.append(span)
        try:
            value = float(match.group("rate"))
        except (TypeError, ValueError):
            continue
        before = _context_before(span)
        after = _context_after(span)
        if _contains_any(before, _RATE_DSR_KEYWORDS):
            slots.setdefault("target_dsr", value)
            slots.setdefault("target_dsr_unit", "percent")
            continue
        if _contains_any(before, _RATE_DTI_KEYWORDS):
            slots.setdefault("target_dti", value)
            slots.setdefault("target_dti_unit", "percent")
            continue
        if _contains_any(before, _RATE_LTV_KEYWORDS):
            slots.setdefault("target_ltv", value)
            slots.setdefault("target_ltv_unit", "percent")
            continue
        if _contains_any(before, _RATE_PREPAYMENT_KEYWORDS) or _contains_any(
            after, _RATE_PREPAYMENT_KEYWORDS
        ):
            slots.setdefault("fee_rate", value)
            slots.setdefault("fee_rate_unit", "percent")
            continue
        if not interest_assigned and (
            _contains_any(before, _RATE_INTEREST_KEYWORDS)
            or _contains_any(after, _RATE_INTEREST_KEYWORDS)
        ):
            slots["interest_rate"] = value
            slots.setdefault("interest_rate_unit", "percent")
            interest_assigned = True
            continue
        if not interest_assigned:
            slots["interest_rate"] = value
            slots.setdefault("interest_rate_unit", "percent")
            interest_assigned = True
        else:
            slots.setdefault("additional_rates", []).append(value)

    year_matches = list(_TERM_YEAR_PATTERN.finditer(message))
    month_matches = list(_TERM_MONTH_PATTERN.finditer(message))
    term_spans = [match.span() for match in year_matches]
    term_spans.extend(match.span() for match in month_matches)

    term_months = 0
    for match in year_matches:
        try:
            term_months += int(match.group("years")) * 12
        except (TypeError, ValueError):
            continue
    for match in month_matches:
        try:
            term_months += int(match.group("months"))
        except (TypeError, ValueError):
            continue
    if term_months:
        slots["term_months"] = term_months

    skip_spans_set = {span for span in rate_spans + term_spans}
    skip_spans = sorted(skip_spans_set)

    def _overlaps(target: tuple[int, int], span: tuple[int, int]) -> bool:
        return target[0] < span[1] and span[0] < target[1]

    for start, end, token in _iter_amount_spans(message):
        span = (start, end)
        if any(_overlaps(span, other) for other in skip_spans):
            continue
        normalized = _normalize_amount_token(token)
        if normalized is None:
            continue
        context = (
            message[max(0, span[0] - 12) : min(len(message), span[1] + 12)]
        ).lower()
        before_word = message[max(0, span[0] - 12) : span[0]].strip().lower().split()
        after_word = (
            message[span[1] : min(len(message), span[1] + 12)].strip().lower().split()
        )
        prev_token = before_word[-1] if before_word else ""
        next_token = after_word[0] if after_word else ""
        assigned = False

        loan_hit = (
            _contains_any(context, _LOAN_KEYWORDS)
            or prev_token in _LOAN_KEYWORDS
            or next_token in _LOAN_KEYWORDS
        )
        collateral_hit = (
            _contains_any(context, _COLLATERAL_KEYWORDS)
            or prev_token in _COLLATERAL_KEYWORDS
            or next_token in _COLLATERAL_KEYWORDS
        )

        if _contains_any(context, _INCOME_KEYWORDS):
            slots.setdefault("annual_income", normalized)
            assigned = True
        elif _contains_any(context, _DEBT_KEYWORDS):
            if _contains_any(context, _MONTH_KEYWORDS):
                slots.setdefault("monthly_debt_payment", normalized)
                slots.setdefault("annual_debt_service", normalized * 12)
            else:
                slots.setdefault("annual_debt_service", normalized)
            assigned = True
        elif "잔액" in context:
            slots.setdefault("principal", normalized)
            slots.setdefault("loan_amount", normalized)
            assigned = True

        if not assigned:
            if loan_hit and not collateral_hit:
                slots.setdefault("loan_amount", normalized)
                slots.setdefault("principal", normalized)
                assigned = True
            elif collateral_hit and not loan_hit:
                if "collateral_value" not in slots:
                    slots["collateral_value"] = normalized
                else:
                    slots.setdefault("additional_amounts", []).append(normalized)
                assigned = True
            elif loan_hit and collateral_hit:
                if prev_token in _COLLATERAL_KEYWORDS:
                    if "collateral_value" not in slots:
                        slots["collateral_value"] = normalized
                    else:
                        slots.setdefault("additional_amounts", []).append(normalized)
                elif prev_token in _LOAN_KEYWORDS or next_token in _LOAN_KEYWORDS:
                    slots.setdefault("loan_amount", normalized)
                    slots.setdefault("principal", normalized)
                else:
                    if "collateral_value" not in slots:
                        slots["collateral_value"] = normalized
                    else:
                        slots.setdefault("additional_amounts", []).append(normalized)
                assigned = True

        if not assigned:
            if "loan_amount" not in slots:
                slots.setdefault("loan_amount", normalized)
            else:
                slots.setdefault("additional_amounts", []).append(normalized)
    if "additional_amounts" in slots:
        slots["additional_amounts"] = sorted(
            {int(value) for value in slots["additional_amounts"]}, reverse=True
        )

    return slots


def _call_llm_router(message: str, rule: RuleAnalysis) -> Optional[dict[str, Any]]:
    client = _load_llm_client()
    if not client:
        return None

    prompt = _build_llm_prompt(message, rule)
    try:
        raw = client.invoke(prompt, temperature=0.0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM router 호출 실패: %s", exc)
        return None

    parsed = _parse_llm_response(raw)
    return parsed


def _load_llm_client() -> Optional[LLMClient]:
    try:
        from src.llm.client import get_router_client  # type: ignore
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM router client 초기화 실패: %s", exc)
        return None

    try:
        client = get_router_client()  # type: ignore[call-arg]
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM router client 인스턴스 생성 실패: %s", exc)
        return None
    return client


def _build_llm_prompt(message: str, rule: RuleAnalysis) -> str:
    return (
        "당신은 금융 상담 챗봇의 라우터입니다.\n"
        "사용자 발화의 intent(calc/info)와 핵심 슬롯(loan_amount, interest_rate, term_months 등)을 JSON으로 추출하세요.\n"
        "가능하면 아래 규칙 기반 결과를 참고하되, 확신이 없으면 score를 0.5 이하로 설정합니다.\n"
        '응답 예시: {"intent": "calc", "slots": {"loan_amount": 300000000}, "confidence": {"score": 0.82}}\n'
        f"규칙 기반 intent: {rule.intent}\n"
        f"규칙 기반 confidence: {rule.confidence}\n"
        f"사용자 발화: {message}\n"
    )


def _parse_llm_response(raw: str) -> Optional[dict[str, Any]]:
    if not raw:
        return None
    candidate = raw.strip()
    if not candidate:
        return None

    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        logger.debug("LLM 응답이 JSON 포맷이 아님: %s", raw)
        return None

    intent = data.get("intent")
    slots = data.get("slots") or {}
    confidence = data.get("confidence") or {}

    if not isinstance(slots, dict):
        slots = {}
    if not isinstance(confidence, dict):
        confidence = {}

    result = {
        "intent": intent if isinstance(intent, str) else None,
        "slots": slots,
        "confidence": confidence,
    }
    return result


def _merge_results(rule: RuleAnalysis, llm: Optional[dict[str, Any]]) -> dict[str, Any]:
    final_intent = rule.intent
    final_slots = dict(rule.slots)
    final_confidence = dict(rule.confidence)
    final_confidence["source"] = "rule"

    if not llm:
        return {
            "intent": final_intent,
            "slots": final_slots,
            "confidence": final_confidence,
        }

    llm_intent = llm.get("intent")
    llm_slots = llm.get("slots") or {}
    llm_conf = llm.get("confidence") or {}
    if isinstance(llm_slots, dict):
        final_slots.update({k: v for k, v in llm_slots.items() if v is not None})

    rule_score = float(rule.confidence.get("score") or 0.0)
    llm_score = float(llm_conf.get("score") or 0.0)

    if isinstance(llm_intent, str):
        if llm_intent == rule.intent:
            final_intent = rule.intent
            blended = min(0.99, (rule_score * 0.6) + (llm_score * 0.4) + 0.05)
            final_confidence["score"] = round(max(rule_score, blended), 3)
            final_confidence["source"] = "hybrid"
        elif llm_score >= rule_score + 0.15:
            final_intent = llm_intent
            final_confidence["score"] = round(min(0.95, llm_score), 3)
            final_confidence["source"] = "llm_override"
            final_confidence["reason"] = "llm_override"
        else:
            final_confidence["score"] = round(rule_score, 3)
            final_confidence["source"] = "rule_preferred"
    else:
        final_confidence["score"] = round(rule_score, 3)

    final_confidence["llm_score"] = round(llm_score, 3)
    final_confidence["rule_score"] = round(rule_score, 3)

    return {
        "intent": final_intent,
        "slots": final_slots,
        "confidence": final_confidence,
    }


__all__ = ["extract_intent_and_slots"]
