"""Allowlisted API provenance, independently recorded from model-generated prose."""
from __future__ import annotations

import math
import re
from datetime import date
from typing import Mapping

PRICING_DATE = "2026-09-06"
# Standard USD per million tokens; estimates, not billing records.
RATES = {"gpt-5.6-luna": (.2, .02, 1.2), "gpt-5.6-terra": (2, .2, 12),
         "gpt-5.6-sol": (4, .4, 20), "gpt-6-astra": (10, 1, 50)}
SCOPES = {"abstract", "full_text", "abstract_introduction", "stored_reviews"}
FIELDS = {"stage", "model", "requestedModel", "effort", "inputTokens", "outputTokens",
          "reasoningTokens", "cachedInputTokens", "cacheWriteTokens", "totalTokens", "estimatedCostUsd",
          "pricingDate", "sourceScope", "pdfPages", "paperIds", "outcome"}


def attr(value, name):
    return value.get(name) if isinstance(value, Mapping) else getattr(value, name, None)


def count(value):
    return value if type(value) is int and 0 <= value <= 100_000_000 else None


def record(response, *, model, effort, stage, paper_ids, scope, pages):
    usage = attr(response, "usage")
    input_n, output_n = count(attr(usage, "input_tokens")), count(attr(usage, "output_tokens"))
    cached = count(attr(attr(usage, "input_tokens_details"), "cached_tokens"))
    writes = count(attr(attr(usage, "input_tokens_details"), "cache_write_tokens"))
    actual = attr(response, "model")
    actual = actual if isinstance(actual, str) and re.fullmatch(r"[a-zA-Z0-9._:-]{1,100}", actual) else None
    rates = RATES.get(actual) if actual else None
    cost = None
    if rates and input_n is not None and output_n is not None and cached is not None and cached <= input_n:
        # Missing cache-write usage cannot establish whether a write surcharge applied.
        if writes is not None and cached + writes <= input_n:
            in_rate, cache_rate, out_rate = rates
            long = input_n > 272_000
            cost = round(((input_n - cached + writes * .25) * in_rate * (2 if long else 1)
                          + cached * cache_rate * (2 if long else 1)
                          + output_n * out_rate * (1.5 if long else 1)) / 1_000_000, 6)
    value = dict(stage=stage, model=actual, requestedModel=model, effort=effort,
                 inputTokens=input_n, outputTokens=output_n,
                 reasoningTokens=count(attr(attr(usage, "output_tokens_details"), "reasoning_tokens")),
                 cachedInputTokens=cached, cacheWriteTokens=writes,
                 totalTokens=count(attr(usage, "total_tokens")), estimatedCostUsd=cost,
                 pricingDate=PRICING_DATE, sourceScope=scope, pdfPages=pages, paperIds=list(paper_ids),
                 outcome="completed" if attr(response, "status") == "completed" else "unconfirmed")
    validate([value])
    return value


def validate(calls):
    if not isinstance(calls, list) or len(calls) > 5000:
        raise ValueError("Invalid research usage list")
    for item in calls:
        if not isinstance(item, Mapping) or set(item) != FIELDS:
            raise ValueError("Invalid research usage fields")
        for field in ("requestedModel", "model"):
            if item[field] is None and field == "model":
                continue
            if not isinstance(item[field], str) or not re.fullmatch(r"[a-zA-Z0-9._:-]{1,100}", item[field]):
                raise ValueError("Invalid research model")
        if item["effort"] not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Invalid research effort")
        if item["stage"] not in {"screen", "paper", "weekly", "monthly"} or item["sourceScope"] not in SCOPES:
            raise ValueError("Invalid research stage")
        if item["outcome"] not in {"completed", "unconfirmed"}:
            raise ValueError("Invalid research record")
        if not isinstance(item["pricingDate"], str) or date.fromisoformat(item["pricingDate"]).isoformat() != item["pricingDate"]:
            raise ValueError("Invalid research pricing date")
        for field in ("inputTokens", "outputTokens", "reasoningTokens", "cachedInputTokens", "cacheWriteTokens", "totalTokens", "pdfPages"):
            if item[field] is not None and count(item[field]) is None:
                raise ValueError("Invalid research token/page count")
        pages = item["pdfPages"]
        if item["sourceScope"] == "full_text" and (pages is None or not 1 <= pages <= 50):
            raise ValueError("Full text must have a verified page count at most 50")
        if item["sourceScope"] == "abstract_introduction" and (pages is None or pages <= 50):
            raise ValueError("Limited source must have a verified page count above 50")
        if item["inputTokens"] is not None and item["outputTokens"] is not None and item["totalTokens"] is not None:
            if item["inputTokens"] + item["outputTokens"] != item["totalTokens"]:
                raise ValueError("Inconsistent research total")
        for part, total in (("reasoningTokens", "outputTokens"), ("cachedInputTokens", "inputTokens"), ("cacheWriteTokens", "inputTokens")):
            if item[part] is not None and item[total] is not None and item[part] > item[total]:
                raise ValueError("Inconsistent research component count")
        cost = item["estimatedCostUsd"]
        if cost is not None and (type(cost) not in {int, float} or not math.isfinite(cost) or not 0 <= cost < 100000):
            raise ValueError("Invalid research cost")
        if not isinstance(item["paperIds"], list) or not 0 <= len(item["paperIds"]) <= 100:
            raise ValueError("Invalid research paper IDs")
        if any(not isinstance(p, str) or not re.fullmatch(r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z-]+(?:\.[A-Z]{2})?/[0-9]{7})(?:v[0-9]+)?", p) for p in item["paperIds"]):
            raise ValueError("Invalid research paper ID")
    return calls


def describe(calls, *, english=False):
    if not calls:
        return "Model / effort / tokens: not recorded." if english else "モデル・effort・トークン数：未記録。"
    validate(calls)
    texts = []
    for c in calls:
        stage = {"screen": "一次判定", "paper": "論文解析", "weekly": "週次統合", "monthly": "月次統合"}[c["stage"]]
        if english:
            stage = c["stage"]
        n = lambda key: f"{c[key]:,}" if c[key] is not None else ("unknown" if english else "未記録")
        model = c["model"] or c["requestedModel"] + (" (requested)" if english else "（指定値）")
        label = f"{stage}: {model} / {c['effort']} · "
        label += (f"tokens input {n('inputTokens')} / output {n('outputTokens')} (reasoning {n('reasoningTokens')}, included)" if english
                  else f"トークン 入力 {n('inputTokens')} / 出力 {n('outputTokens')}（うち推論 {n('reasoningTokens')}）")
        if c["sourceScope"] == "abstract_introduction":
            label += (f" · {c['pdfPages']} pages (>50): abstract + Introduction ONLY; full text not analyzed" if english
                      else f" · 全{c['pdfPages']}ページ（50ページ超）：abstractとIntroductionのみ。全文未解析")
        elif c["sourceScope"] == "full_text":
            label += f" · PDF {c['pdfPages']} pages"
        elif c["sourceScope"] == "stored_reviews":
            label += (f" · stored daily reviews; shared chunk of {len(c['paperIds'])} papers, not per-paper tokens" if english
                      else f" · 保存済み日次レビュー使用／{len(c['paperIds'])}論文の共有処理。トークン数は論文単独ではなく共有分")
        if c["outcome"] != "completed":
            label += " · " + ("unconfirmed attempt" if english else "未完了の試行")
        if c["estimatedCostUsd"] is not None:
            label += f" · {'estimated' if english else '推定'} ${c['estimatedCostUsd']:.4f}"
        texts.append(label)
    return "\n".join(texts)


def overview(calls, *, english=False):
    if calls is None:
        return describe(None, english=english)
    validate(calls)
    if not calls:
        return "No API calls recorded." if english else "API呼び出しの記録なし。"
    groups = {}
    for c in calls:
        key = (c["stage"], c["model"] or c["requestedModel"] + " (requested)", c["effort"])
        groups.setdefault(key, []).append(c)
    lines = []
    for (stage, model, effort), values in groups.items():
        def total(field):
            known = [v[field] for v in values if v[field] is not None]
            result = f"{sum(known):,}" if known else ("unknown" if english else "未記録")
            if len(known) != len(values):
                result += " + ?"
            return result
        lines.append(f"{stage}: {model} / {effort} · {len(values)} calls · " +
                     (f"tokens input {total('inputTokens')} / output {total('outputTokens')} / total {total('totalTokens')}" if english
                      else f"トークン 入力 {total('inputTokens')} / 出力 {total('outputTokens')} / 合計 {total('totalTokens')}"))
    lines.append("Counts include reported retries; unknown usage is not zero. Reasoning is included in output."
                 if english else "記録できた再試行分を含みます。未記録は0ではありません。推論トークンは出力に含まれます。")
    return "\n".join(lines)
