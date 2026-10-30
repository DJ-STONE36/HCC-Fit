# -*- coding: utf-8 -*-
"""疾病范围门禁：本服务只接受已确认的 HCC。

为什么需要：
- 旧实现 `infer_from_text()` / `normalize_case()` 默认 disease=HCC；
- 肝内胆管癌、肝转移、肝癌未明确都会被当成 HCC 检索，属于临床语义错误；
- 输入归一化层可以允许“肝癌，类型未明确”，但**检索服务**不能。
"""

from __future__ import annotations


class ScopeError(ValueError):
    """疾病不在 HCC 范围内，或尚未确认 HCC。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


HCC_ALIASES = {
    "hcc", "hepatocellular carcinoma", "hepatocellular cancer",
    "肝细胞癌", "肝细胞肝癌",
}

NON_HCC_TERMS = (
    "肝内胆管癌", "胆管癌", "胆管细胞癌", "icca", "cholangiocarcinoma",
    "结肠癌肝转移", "肝转移", "转移性肝癌", "metastatic liver",
)

AMBIGUOUS_TERMS = (
    "肝癌", "原发性肝癌", "肝脏肿瘤", "肝肿瘤", "肝占位", "肝肿块",
    "恶性肿瘤", "癌症",
)


def normalize_disease(scenario: dict) -> str:
    """返回 'HCC'；否则抛 ScopeError。

    规则：
    - 显式 disease 优先；只接受 HCC 同义词；
    - 文本出现 iCCA/肝转移 → out_of_scope；
    - 文本只有“肝癌/肝脏肿瘤/恶性肿瘤” → ambiguous_disease；
    - 没有任何疾病表达 → ambiguous_disease（不能默认 HCC）。
    """
    text = str((scenario or {}).get("text") or "").strip()
    explicit = str((scenario or {}).get("disease") or "").strip().lower()
    low = text.lower()

    if explicit:
        if explicit in HCC_ALIASES:
            # 显式 HCC 与文本里的非 HCC 同时出现：冲突
            if any(t in low for t in NON_HCC_TERMS):
                raise ScopeError(
                    "conflicting_disease",
                    "disease=HCC 与原话中的非 HCC 诊断冲突，请先确认疾病。",
                )
            return "HCC"
        raise ScopeError(
            "out_of_scope",
            "本服务只支持 HCC；收到的 disease=%s。" % explicit,
        )

    if any(t in low for t in NON_HCC_TERMS):
        raise ScopeError(
            "out_of_scope",
            "本服务只支持 HCC；原话含肝内胆管癌/肝转移等非 HCC 诊断。",
        )

    if any(t in low for t in AMBIGUOUS_TERMS):
        raise ScopeError(
            "ambiguous_disease",
            "原话只写了“肝癌/肝脏肿瘤/恶性肿瘤”，未确认 HCC；"
            "请先确认是 HCC，再调用本服务。",
        )

    if any(t in low for t in HCC_ALIASES):
        return "HCC"

    raise ScopeError(
        "ambiguous_disease",
        "未识别到明确 HCC；请提供 disease=HCC 或原话明确写 HCC/肝细胞癌。",
    )
