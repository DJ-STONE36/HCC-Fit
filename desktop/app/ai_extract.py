# -*- coding: utf-8 -*-
"""可选 AI 候选层（OpenAI-compatible / SiliconFlow）。

只负责把一段话提成候选 concept_id；不决定最终状态，不覆盖规则结论。
API Key 只存在于内存，不落盘。
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from .input_normalization import AIProvider, InputSession, load_terms, terms_by_concept


class OpenAICompatibleProvider(AIProvider):
    def __init__(self, base_url: str, api_key: str, model: str,
                 terms_path, timeout: int = 30):
        self.base_url = (base_url or "").rstrip("/")
        self.api_key = api_key or ""
        self.model = model or ""
        self.timeout = timeout
        self.terms = load_terms(terms_path)
        self.by_id = terms_by_concept(self.terms)

    def _prompt(self, raw_text: str) -> str:
        allowed = "\n".join(
            "- %s | %s | %s" % (t.concept_id, t.type, t.canonical)
            for t in self.terms if t.type in
            ("disease", "treatment", "stage", "outcome", "start", "horizon", "data")
        )
        return (
            "你是临床输入整理助手，只做“候选抽取”，不做诊断、不补造。\n"
            "从医生原话里抽取字段候选；每个候选必须是下面的 concept_id 之一。\n"
            "规则：\n"
            "1) 肝癌/原发性肝癌 → 只能选 disease.liver_cancer_unspecified，"
            "绝不能选 disease.hcc；\n"
            "2) 肝脏肿瘤/肝占位 → disease.liver_lesion_unspecified；\n"
            "3) 恶性肿瘤 → disease.malignancy_unspecified；\n"
            "4) 只有原话明确出现 HCC/肝细胞癌/hepatocellular carcinoma 才可选 disease.hcc；\n"
            "5) 否定（非/排除）、疑似、既往史不要当成阳性；拿不准就不输出；\n"
            "6) 不要输出概率，不要输出诊断结论。\n"
            "允许的 concept_id：\n%s\n\n"
            "只输出一个 JSON：{\"fields\":[{\"field\":\"disease\",\"concept_id\":\"...\","
            "\"raw\":\"原话片段\"}]}\n"
            "医生原话：\n%s" % (allowed, raw_text)
        )

    def _post(self, payload: dict) -> dict:
        req = urllib.request.Request(
            self.base_url + "/chat/completions",
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + self.api_key,
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _call(self, raw_text: str):
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": "只输出 JSON，不要解释。"},
                {"role": "user", "content": self._prompt(raw_text)},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            return self._post(payload)
        except urllib.error.HTTPError as exc:
            if exc.code not in (400, 422):
                raise
            payload.pop("response_format", None)
            return self._post(payload)

    def extract(self, raw_text: str, session: InputSession) -> list[dict]:
        if not (self.base_url and self.api_key and self.model):
            return []
        data = self._call(raw_text)
        text = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        try:
            obj = json.loads(text)
        except Exception:
            m = re.search(r"\{.*\}", text, flags=re.S)
            if not m:
                return []
            try:
                obj = json.loads(m.group(0))
            except Exception:
                return []
        out = []
        for item in obj.get("fields") or []:
            cid = item.get("concept_id")
            term = self.by_id.get(cid)
            if not term:
                continue
            field = "start_point" if term.type == "start" else term.type
            if field not in ("disease", "treatment", "stage", "outcome",
                             "start_point", "horizon", "intent"):
                continue
            out.append({
                "field": field,
                "concept_id": cid,
                "raw": str(item.get("raw") or "")[:80],
            })
        return out
