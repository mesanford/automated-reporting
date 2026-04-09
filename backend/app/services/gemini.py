import os
import json
from importlib import import_module
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv()

api_key = os.getenv("GOOGLE_API_KEY")
_genai = None
_genai_load_error = None

SYSTEM_PERSONA = """You are a senior performance marketing analyst with 10+ years of experience 
managing multi-million dollar cross-channel ad budgets. You write concise, data-first reports 
for marketing directors and CMOs. Rules: always cite specific dollar amounts and percentages 
directly from the data provided; never fabricate benchmarks; be direct and avoid hedging 
language; every recommendation must reference at least one specific metric from the data."""


def _get_genai_client():
    global _genai, _genai_load_error

    if _genai is not None:
        return _genai
    if _genai_load_error is not None:
        raise RuntimeError(_genai_load_error)

    try:
        module = import_module("google.generativeai")
        module.configure(api_key=api_key)
        _genai = module
        return _genai
    except Exception as exc:
        _genai_load_error = f"Failed to load google.generativeai: {exc}"
        raise RuntimeError(_genai_load_error) from exc

def generate_analysis(gemini_input: Dict[str, Any]) -> str:
    if not api_key:
        return "Gemini API key not configured. Please set GOOGLE_API_KEY in your .env file."

    try:
        genai = _get_genai_client()
    except RuntimeError as exc:
        return str(exc)

    model = genai.GenerativeModel('gemini-2.5-pro')

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
        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        return f"Error generating AI analysis: {str(e)}"
