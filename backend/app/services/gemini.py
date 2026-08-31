import json
from importlib import import_module
from typing import Any, Callable, Dict, Iterator, List
from dotenv import load_dotenv

load_dotenv()

from app.services.secrets_manager import get_secret  # noqa: E402

api_key = get_secret("GOOGLE_API_KEY")
_genai = None
_genai_load_error = None

SYSTEM_PERSONA = """You are a senior performance marketing analyst with 10+ years of experience
managing multi-million dollar cross-channel ad budgets. You write concise, data-first reports
for marketing directors and CMOs. Rules: always cite specific dollar amounts and percentages
directly from the data provided; never fabricate benchmarks; be direct and avoid hedging
language; every recommendation must reference at least one specific metric from the data."""

CHAT_SYSTEM_PERSONA = """You are a senior performance marketing analyst answering questions
about the user's cross-channel ad performance. You have function-calling tools that read the
user's own report data — ALWAYS call them to get numbers, never fabricate or estimate.

Rules:
- Start by calling list_reports (or list_connections) to orient yourself if you don't yet
  know what data is available.
- Cite specific dollar amounts and percentages from tool results in every answer.
- If a tool returns no data or an error, say so plainly — do not guess.
- Use Markdown: short headings, bullet points, and inline metrics.
- When a chart would be clearer than text (trends over time, comparisons across platforms,
  top-N rankings), call render_chart with the rows you've already gathered. It renders
  inline in the conversation.
- Be concise. Marketing directors want the answer, not a preamble."""


def _get_genai_client():
    global _genai, _genai_load_error

    if _genai is not None:
        return _genai
    if _genai_load_error is not None:
        raise RuntimeError(_genai_load_error)

    try:
        module = import_module("google.genai")
        _genai = module.Client(api_key=api_key)
        return _genai
    except Exception as exc:
        _genai_load_error = f"Failed to load google.genai: {exc}"
        raise RuntimeError(_genai_load_error) from exc

def generate_analysis(gemini_input: Dict[str, Any]) -> str:
    if not api_key:
        return "Gemini API key not configured. Please set GOOGLE_API_KEY in your .env file."

    try:
        client = _get_genai_client()
    except RuntimeError as exc:
        return str(exc)

    data_json = json.dumps(gemini_input, indent=2, default=str)

    prompt = f"""{SYSTEM_PERSONA}

Analyze the following structured cross-channel ad performance data and produce a report 
in Markdown using EXACTLY these five sections in this order:

## 1. Executive Summary
2–3 sentences. State total spend, total conversions, and blended CPA. Include the 
period-over-period or year-over-year delta for the single most important metric.
Note the comparison type: {gemini_input.get('comparison_type', 'period_over_period')} 
(current: {gemini_input.get('current_period', 'N/A')}, vs prior: {gemini_input.get('prior_period', 'N/A')}).

## 2. Platform Efficiency Ranking
Rank each platform from most to least efficient by CPA. For each, state: spend, CPA, 
CTR, and spend share (%). Call out the largest efficiency gap between platforms.
For each platform, reference its delta vs prior period using platform_deltas in the data.

## 3. Trend Analysis
Using the period_deltas in the data, identify what moved materially (>5%) and explain 
the likely cause. Flag any metric moving in the wrong direction.
Distinguish between cross-channel blended trends and individual platform trends.

## 4. Campaign Highlights
Call out the top_campaign and worst_campaign by name. State each campaign's CPA and 
explain what that number means for the account.

## 5. Recommendations
Provide exactly 4 recommendations. Each must:
- Begin with **Action:**
- Reference a specific metric and its value from the data
- State the expected outcome

---
DATA:
{data_json}
"""

    try:
        response = client.models.generate_content(
            model='gemini-3.1-pro-preview',
            contents=prompt
        )
        return response.text
    except Exception as e:
        return f"Error generating AI analysis: {str(e)}"


def generate_optimizations(performance_data: Dict[str, Any]) -> str:
    """
    Generates optimization recommendations for Google Ads using Gemini.
    Returns a JSON string of recommendations.
    """
    if not api_key:
        return json.dumps({"error": "Gemini API key not configured."})

    try:
        client = _get_genai_client()
    except RuntimeError as exc:
        return json.dumps({"error": str(exc)})

    # Note: we use pydantic if available, but for maximum compatibility we can also use response_schema dict
    try:
        from google.genai import types
        schema = {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "campaign_name": {"type": "STRING"},
                    "ad_group_name": {"type": "STRING", "description": "Optional ad group name"},
                    "change_type": {
                        "type": "STRING",
                        "description": "e.g., 'budget_increase', 'budget_decrease', 'pause_underperforming', 'enable_campaign'"
                    },
                    "original_value": {
                        "type": "OBJECT",
                        "description": "JSON representation of current state, e.g. {'budget': 100.0} or {'status': 'ENABLED'}"
                    },
                    "proposed_value": {
                        "type": "OBJECT",
                        "description": "JSON representation of proposed state, e.g. {'budget': 120.0} or {'status': 'PAUSED'}"
                    },
                    "reasoning": {"type": "STRING", "description": "Detailed explanation citing metrics"}
                },
                "required": ["campaign_name", "change_type", "original_value", "proposed_value", "reasoning"]
            }
        }
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.2
        )
    except ImportError:
        # Fallback if types not available
        config = {"response_mime_type": "application/json"}

    prompt = f"""You are a senior performance marketing analyst. 
Based on the following Google Ads performance data, suggest concrete optimizations.

All actions will initially require human approval. Provide a JSON array of recommendation objects.
Each recommendation must contain:
- campaign_name
- ad_group_name (optional)
- change_type (e.g., budget_increase, budget_decrease, pause_underperforming)
- original_value (JSON object, e.g., {{"budget": 50}})
- proposed_value (JSON object, e.g., {{"budget": 60}})
- reasoning (Detailed explanation of why this change is recommended, citing specific metrics from the data)

Make sure the output is strictly valid JSON.

DATA:
{json.dumps(performance_data, indent=2, default=str)}
"""

    try:
        # If using google-genai SDK
        if isinstance(config, dict):
            # Fallback for older or different SDK structures
            response = client.models.generate_content(
                model='gemini-3.1-pro-preview',
                contents=prompt,
                config=config
            )
        else:
            response = client.models.generate_content(
                model='gemini-3.1-pro-preview',
                contents=prompt,
                config=config
            )
        return response.text
    except Exception as e:
        return json.dumps({"error": f"Error generating optimizations: {str(e)}"})


# ── Conversational analytics ────────────────────────────────────────────────

CHAT_MODEL = "gemini-3.1-pro-preview"
MAX_TOOL_ROUNDS = 6


def _history_to_contents(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert persisted Message rows into google.genai `contents`.

    Each history entry is a dict: {role, content, tool_name, tool_payload}.
    - user / assistant text → {role, parts: [{text}]}
    - tool (function response) → {role: 'user', parts: [{function_response: {...}}]}
      (google.genai expects function responses on the user side of the turn)
    - assistant function_call → {role: 'model', parts: [{function_call: {...}}]}
    """
    contents: List[Dict[str, Any]] = []
    for msg in history:
        role = msg.get("role")
        if role == "user":
            contents.append({"role": "user", "parts": [{"text": msg.get("content") or ""}]})
        elif role == "assistant":
            if msg.get("tool_name"):
                contents.append({
                    "role": "model",
                    "parts": [{
                        "function_call": {
                            "name": msg["tool_name"],
                            "args": msg.get("tool_payload") or {},
                        }
                    }],
                })
            else:
                contents.append({"role": "model", "parts": [{"text": msg.get("content") or ""}]})
        elif role == "tool":
            contents.append({
                "role": "user",
                "parts": [{
                    "function_response": {
                        "name": msg.get("tool_name") or "",
                        "response": msg.get("tool_payload") or {},
                    }
                }],
            })
    return contents


def stream_chat(
    history: List[Dict[str, Any]],
    tool_declarations: List[Dict[str, Any]],
    tool_executor: Callable[[str, Dict[str, Any]], Any],
) -> Iterator[Dict[str, Any]]:
    """Run a conversational turn with tool-calling and stream events.

    Yields dicts of shape:
      {"type": "token",       "text": str}
      {"type": "tool_call",   "name": str, "args": dict, "call_id": str}
      {"type": "tool_result", "name": str, "result": Any, "call_id": str}
      {"type": "done",        "final_text": str}
      {"type": "error",       "message": str}
    """
    if not api_key:
        yield {"type": "error", "message": "Gemini API key not configured (GOOGLE_API_KEY)."}
        return

    try:
        client = _get_genai_client()
        from google.genai import types  # noqa: WPS433
    except Exception as exc:  # noqa: BLE001
        yield {"type": "error", "message": f"Gemini client unavailable: {exc}"}
        return

    contents = _history_to_contents(history)
    config = types.GenerateContentConfig(
        system_instruction=CHAT_SYSTEM_PERSONA,
        tools=[types.Tool(function_declarations=tool_declarations)],
        temperature=0.3,
    )

    final_text_parts: List[str] = []

    for round_idx in range(MAX_TOOL_ROUNDS):
        try:
            stream = client.models.generate_content_stream(
                model=CHAT_MODEL,
                contents=contents,
                config=config,
            )
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": f"Gemini stream failed: {exc}"}
            return

        round_text: List[str] = []
        function_calls: List[Any] = []

        try:
            for chunk in stream:
                # Text deltas
                text = getattr(chunk, "text", None)
                if text:
                    round_text.append(text)
                    yield {"type": "token", "text": text}

                # Function calls (may appear once the chunk completes)
                candidates = getattr(chunk, "candidates", None) or []
                for cand in candidates:
                    content = getattr(cand, "content", None)
                    if not content:
                        continue
                    for part in getattr(content, "parts", None) or []:
                        fc = getattr(part, "function_call", None)
                        if fc and getattr(fc, "name", None):
                            function_calls.append(fc)
        except Exception as exc:  # noqa: BLE001
            yield {"type": "error", "message": f"Gemini stream interrupted: {exc}"}
            return

        if round_text:
            final_text_parts.append("".join(round_text))

        if not function_calls:
            yield {"type": "done", "final_text": "".join(final_text_parts)}
            return

        # Append model's function_call parts to contents, then execute and append responses.
        model_parts = []
        response_parts = []
        for fc in function_calls:
            args = dict(getattr(fc, "args", None) or {})
            name = fc.name
            call_id = getattr(fc, "id", None) or f"{name}-{round_idx}"
            yield {"type": "tool_call", "name": name, "args": args, "call_id": call_id}

            result = tool_executor(name, args)
            yield {"type": "tool_result", "name": name, "result": result, "call_id": call_id}

            model_parts.append({"function_call": {"name": name, "args": args}})
            response_parts.append({"function_response": {"name": name, "response": _wrap_response(result)}})

        contents.append({"role": "model", "parts": model_parts})
        contents.append({"role": "user", "parts": response_parts})

    yield {"type": "done", "final_text": "".join(final_text_parts)}


def _wrap_response(result: Any) -> Dict[str, Any]:
    """google.genai expects function_response.response to be a dict."""
    if isinstance(result, dict):
        return result
    return {"result": result}

