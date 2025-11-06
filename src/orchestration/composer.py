# orchestration/composer.py
# 템플릿 파일이 없어도 동작하도록 폴백 포함

from __future__ import annotations

from pathlib import Path
from typing import Iterable, Any, Dict, List
import re

from jinja2 import Template

from .state import OrchestrationState

# 폴더 위치가 바뀌어도 찾도록 후보 경로를 순회
def _find_prompts_dir() -> Path:
    here = Path(__file__).resolve()
    candidates = [
        here.parents[2] / "prompts",   # 프로젝트 루트/prompts (권장)
        here.parents[1] / "prompts",   # src/prompts
        here.parent / "prompts",       # orchestration/prompts
        here.parents[2] / "config" / "prompts",  # config/prompts (운영 설정)
    ]
    for p in candidates:
        if p.exists():
            return p
    return candidates[0]  # 없으면 루트 기준으로 반환(파일 없을 시 폴백 템플릿 사용)


PROMPTS = _find_prompts_dir()

# 폴백 템플릿(파일 없을 때 사용)
_FALLBACK = """\
{% if mode == "calc" -%}
계산 결과 요약
{{ calc.summary or summary }}
{% if calc.repayment %}
- 월 상환액: {{ calc.repayment.monthly_payment | default("-") }}
- 상환 기간: {{ calc.repayment.term_months | default("-") }}개월
{% endif %}
{% if calc.policy %}
- 정책 한도(LTV): {{ calc.policy.ltv_limit | default("-") }}
- DTI 한도: {{ calc.policy.dti_limit | default("-") }}
{% endif %}
{% if calc.assumptions %}
가정: {{ calc.assumptions | join(", ") }}
{% endif %}
{% else -%}
{{ summary }}
{% if confidence %}
{% set passed = confidence.get("passed") if confidence is mapping else None %}
{% set score = confidence.get("score") if confidence is mapping else None %}
신뢰도: {% if passed is not none %}{{ "충족" if passed else "미충족" }}{% elif score is not none %}{{ score }}{% else %}-{% endif %}
{% if confidence.get("reason") %}사유: {{ confidence.get("reason") }}{% endif %}
{% endif %}
{% endif %}
"""


def _load_template() -> Template:
    path = PROMPTS / "composer_answer.txt"
    if path.exists():
        return Template(path.read_text(encoding="utf-8"))
    return Template(_FALLBACK)


def _collapse_whitespace(value: str) -> str:
    return " ".join(value.split())


def _truncate(text: str, limit: int = 200) -> str:
    clean = _collapse_whitespace(text)
    if len(clean) <= limit:
        return clean
    return clean[: limit - 3] + "..."


def _stringify_sources(sources: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for source in sources or []:
        if isinstance(source, dict):
            label = (
                source.get("name")
                or source.get("title")
                or source.get("url")
                or source.get("doc_source")
            )
            result.append(str(label) if label else str(source))
        else:
            result.append(str(source))
    return result


def _summarize_documents(documents: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, doc in enumerate(documents or []):
        metadata = doc.get("metadata") if isinstance(doc, dict) else None
        title = None
        url = None
        snippet = None
        if isinstance(doc, dict):
            title = doc.get("title") or doc.get("name")
            snippet = doc.get("snippet") or doc.get("summary") or doc.get("text")
            url = doc.get("url")
        if isinstance(metadata, dict):
            title = title or metadata.get("doc_title") or metadata.get("title")
            url = url or metadata.get("url") or metadata.get("doc_source")
            snippet = snippet or metadata.get("snippet")
        if not title and snippet:
            title = _truncate(str(snippet))
        if not title and url:
            title = str(url)
        if not title:
            continue
        if snippet:
            snippet = _truncate(str(snippet))
        if url:
            url = str(url)
        items.append(
            {
                "title": str(title),
                "snippet": snippet,
                "url": url,
                "source": url or title,
            }
        )
    return items


def _summarize_web_results(results: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for idx, item in enumerate(results or []):
        if not isinstance(item, dict):
            continue
        title = item.get("title") or item.get("name") or f"웹 결과 {idx + 1}"
        snippet = item.get("snippet") or item.get("description") or item.get("summary")
        url = item.get("url")
        if snippet:
            snippet = _truncate(str(snippet))
        if url:
            url = str(url)
        items.append(
            {
                "title": str(title),
                "snippet": snippet,
                "url": url,
                "source": url or title,
            }
        )
    return items


def _build_calc_context(state: OrchestrationState) -> dict[str, Any]:
    calc = state.calc or {}
    policy = calc.get("policy") if isinstance(calc, dict) else {}
    repayment = calc.get("repayment") if isinstance(calc, dict) else {}
    assumptions = calc.get("assumptions") if isinstance(calc, dict) else []
    sources = calc.get("sources") if isinstance(calc, dict) else state.sources
    term_months = None
    if isinstance(repayment, dict) and "term_months" in repayment:
        term_months = repayment.get("term_months")
    elif isinstance(calc, dict) and "term_months" in calc:
        term_months = calc.get("term_months")
    try:
        term_months_value = int(term_months) if term_months is not None else None
    except (TypeError, ValueError):
        term_months_value = None
    term_years_value = None
    if term_months_value and term_months_value > 0:
        term_years_value = round(term_months_value / 12, 2)
    return {
        "summary": calc.get("summary") or state.response_message,
        "limit": calc.get("limit") or calc.get("limit_amount"),
        "monthly_payment": repayment.get("monthly_payment") if isinstance(repayment, dict) else None,
        "term_months": term_months_value,
        "term_years": term_years_value,
        "policy": policy if isinstance(policy, dict) else {},
        "repayment": repayment if isinstance(repayment, dict) else {},
        "assumptions": assumptions if isinstance(assumptions, (list, tuple)) else [],
        "sources": _stringify_sources(sources),
        "raw": calc,
    }


def _build_info_context(state: OrchestrationState) -> dict[str, Any]:
    documents = _summarize_documents(state.documents)
    web_results = _summarize_web_results(state.web_results)
    return {
        "documents": documents,
        "web": web_results,
        "sources": _stringify_sources(state.sources),
    }


_INFO_SECTION_PATTERN = re.compile(r"\s*-\s*\*\*(.+?)\*\*:\s*", re.MULTILINE)


def _format_section_body(body: str | None) -> str:
    text = (body or "").strip()
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("•", "• ")
    placeholder = "\u2027"
    text = re.sub(r"(\d),(?=\d)", lambda m: f"{m.group(1)}{placeholder}", text)

    raw_segments: list[str] = []
    for block in re.split(r"\n+", text):
        block = block.strip()
        if not block:
            continue
        parts = re.split(r"\s*[-•]\s*", block)
        if len(parts) > 1:
            raw_segments.extend(p.strip() for p in parts if p.strip())
        else:
            raw_segments.append(block)

    if not raw_segments:
        raw_segments = [text]

    lines: list[str] = []
    for segment in raw_segments:
        if not segment:
            continue
        segment_safe = segment
        if ":" in segment_safe:
            title, rest = segment_safe.split(":", 1)
            title = title.strip().replace(placeholder, ",")
            rest_items_raw = [item.strip() for item in re.split(r",\s*", rest) if item.strip()]
            rest_items = [item.replace(placeholder, ",") for item in rest_items_raw]
            lines.append(f"- {title}")
            for item in rest_items:
                lines.append(f"  - {item}")
        else:
            pieces_raw = [item.strip() for item in re.split(r",\s*", segment_safe) if item.strip()]
            pieces = [item.replace(placeholder, ",") for item in pieces_raw]
            if len(pieces) > 1:
                base_candidate = pieces[0]
                match = re.search(r"^(.*?)(?=\b[\w·]+\s*\d)", base_candidate)
                if match and match.group(1).strip():
                    base_line = match.group(1).strip()
                    remainder = base_candidate[match.end():].strip()
                    sub_items = []
                    if remainder:
                        sub_items.append(remainder)
                    sub_items.extend(pieces[1:])
                    lines.append(f"- {base_line}")
                    for item in sub_items:
                        lines.append(f"  - {item}")
                else:
                    lines.append(f"- {base_candidate}")
                    for item in pieces[1:]:
                        lines.append(f"  - {item}")
            else:
                lines.append(f"- {segment}")

    return "\n".join(lines)


def _format_informational_summary(summary: str | None) -> str:
    if not summary:
        return ""
    text = str(summary).strip()
    matches = list(_INFO_SECTION_PATTERN.finditer(text))
    if not matches:
        return text

    sections: list[str] = []
    intro = text[: matches[0].start()].strip()
    if intro:
        sections.append(intro)

    for idx, match in enumerate(matches):
        title = match.group(1).strip()
        next_start = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
        body = text[match.end() : next_start].strip()
        formatted_body = _format_section_body(body)
        if formatted_body:
            sections.append(f"## {title}\n{formatted_body}")
        else:
            sections.append(f"## {title}")

    return "\n\n".join(sections)


def render_answer(state: OrchestrationState) -> str:
    template = _load_template()
    mode = state.mode or state.intent or "info"
    summary = state.response_message or state.answer or "요청하신 정보를 정리했어요."
    if mode == "info":
        summary = _format_informational_summary(summary)
    calc_context = _build_calc_context(state)
    info_context = _build_info_context(state)
    context = {
        "mode": mode,
        "state": state,
        "summary": summary,
        "calc": calc_context,
        "info": info_context,
        "confidence": state.confidence,
        "errors": state.errors,
    }
    return template.render(**context)
