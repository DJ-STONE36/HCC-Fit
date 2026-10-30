# -*- coding: utf-8 -*-
"""
input_normalization.py — 单场景自由输入的规则层（v0.2）

设计约束（来自交接表）：
- 医生只写一段话；本模块把这段话拆成字段，不要求医生用标准词；
- 每个字段保存：原话、整理值、状态、来源片段、确认状态；
- 原话不覆盖；未知不补造；
- "肝癌 / 肝脏肿瘤 / 恶性肿瘤" 永远不自动归一为 HCC；
- 否定、疑似、既往、时间、冲突都保留，不自动选一个；
- 只用规则/术语表/医生确认，不调用任何模型（AI 候选层已于 2026-10-30 移除）；
- 原话里已经有足够信息时不再追问（见 field_sufficient）；
- 分句 = 硬标点切分 + 空格软切分，数字+单位、否定、拉丁复合名不拆（见 split_sentences）。

本模块不依赖 PySide6，可单独测试。
"""
from __future__ import annotations

import csv
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

# ---------------- 常量 ----------------

FIELD_ORDER = ("disease", "treatment", "stage", "postop_duration",
               "outcome", "start_point", "horizon", "intent", "subject")

FIELD_LABEL = {
    "disease": "疾病",
    "treatment": "治疗",
    "stage": "当前阶段",
    "postop_duration": "已术后时长",
    "outcome": "预测结局",
    "start_point": "预测起点",
    "horizon": "预测范围",
    "intent": "想问的问题",
    "subject": "患者主体",
}

STATUS_LABEL = {
    "normalized": "已归一",
    "ambiguous": "待确认",
    "suspected": "疑似",
    "negated": "否定",
    "historical": "既往史",
    "conflict": "冲突",
    "unknown": "未知",
    "out_of_scope": "范围外",
}

HISTORY_CUES = ("既往", "曾", "以前", "病史", "多年前", "之前")
SUSPECT_CUES = ("疑似", "考虑", "待排", "可能", "不除外", "疑为", "高度怀疑")
NEG_CUES = ("非", "不是", "排除", "未见", "否认", "无", "未发现")

_CN_NUM = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
           "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

HORIZON_MONTHS = {
    "一年": 12, "1年": 12, "12个月": 12,
    "两年": 24, "2年": 24, "24个月": 24,
    "三年": 36, "3年": 36, "36个月": 36,
    "五年": 60, "5年": 60, "60个月": 60,
}

UNIT_MAP = {
    "year": ("year", 12),
    "years": ("year", 12),
    "month": ("month", 1),
    "months": ("month", 1),
}

# ---------------- 数据模型 ----------------


@dataclass
class Term:
    concept_id: str
    canonical: str
    type: str
    synonyms: list[str]
    auto_status: str = "normalized"
    scope: str = "hcc"
    note: str = ""


@dataclass
class FieldValue:
    field: str
    raw: str = ""
    normalized: str = ""
    status: str = "unknown"
    source_span: list[int] | None = None
    source_sentence: str = ""
    confirm: str = "pending"          # pending / user_confirmed / user_edited / user_unknown
    candidates: list[dict] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    concept_id: str = ""              # 规则选中（或医生确认）的术语 id；自由编辑后为空

    def to_dict(self):
        d = asdict(self)
        return d


@dataclass
class Followup:
    id: str
    field: str
    question: str
    options: list[str]
    required: bool = True
    answer: str = ""
    answer_status: str = "pending"     # pending / answered / unknown

    def to_dict(self):
        return asdict(self)


@dataclass
class Conflict:
    field: str
    message: str
    spans: list[list[int]] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


@dataclass
class InputSession:
    input_id: str
    raw_text: str
    fields: dict[str, FieldValue] = field(default_factory=dict)
    data_available: list[FieldValue] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    followups: list[Followup] = field(default_factory=list)
    confirmation: dict = field(default_factory=lambda: {"status": "pending", "confirmed_at": None})
    version: int = 1
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    def field(self, name: str) -> FieldValue:
        return self.fields.get(name) or FieldValue(field=name)

    def to_dict(self):
        return {
            "input_id": self.input_id,
            "raw_text": self.raw_text,
            "version": self.version,
            "created_at": self.created_at,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
            "data_available": [d.to_dict() for d in self.data_available],
            "conflicts": [c.to_dict() for c in self.conflicts],
            "followups": [f.to_dict() for f in self.followups],
            "confirmation": dict(self.confirmation),
            "sufficient": sufficient_fields(self),
        }

    def pending_required(self) -> list[Followup]:
        return [f for f in self.followups if f.required and f.answer_status == "pending"]

    def can_confirm(self) -> bool:
        if self.conflicts:
            return False
        d = self.fields.get("disease")
        if d and d.status in ("out_of_scope", "ambiguous", "unknown", "negated", "historical"):
            return False
        return not self.pending_required()

    def confirm(self, status="user_confirmed"):
        self.confirmation = {"status": status,
                             "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
        for f in self.fields.values():
            if f.confirm == "pending":
                f.confirm = status
        self.version += 1

    # ---------------- 落盘 ----------------
    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        session = cls(input_id=data["input_id"], raw_text=data["raw_text"])
        session.version = data.get("version", 1)
        session.created_at = data.get("created_at", session.created_at)
        session.confirmation = data.get("confirmation") or {"status": "pending", "confirmed_at": None}
        for name, raw in (data.get("fields") or {}).items():
            session.fields[name] = FieldValue(**raw)
        session.data_available = [FieldValue(**d) for d in data.get("data_available") or []]
        session.conflicts = [Conflict(**c) for c in data.get("conflicts") or []]
        session.followups = [Followup(**f) for f in data.get("followups") or []]
        return session


# ---------------- 信息充分性（决定要不要追问） ----------------

# 明确写出的治疗大类（“刚切完”也落在 resection 上：大类已知，不追问大类）
EXPLICIT_TREATMENT_CONCEPTS = frozenset({
    "treatment.resection", "treatment.tace", "treatment.ablation",
    "treatment.system", "treatment.transplant",
})
NO_TREATMENT_CONCEPTS = frozenset({"treatment.none", "treatment.no_surgery"})
EXPLICIT_START_VALUES = ("手术日", "今天", "治疗日")
EXPLICIT_START_CONCEPTS = ("start.surgery", "start.today")
OS_RAW_RE = re.compile(r"OS|总(?:的)?生存|overall\s+survival", re.I)


def _user_settled(f: FieldValue) -> bool:
    """医生在场景卡里改/确认过，且状态是已归一。"""
    return (f.confirm in ("user_edited", "user_confirmed")
            and f.status == "normalized" and bool(f.normalized))


def field_sufficient(session: InputSession, name: str) -> bool:
    """该字段的信息是否足够：足够就不再生成追问。

    阈值按交接要求：
    - 疾病：只有明确 HCC 才算足够（“肝癌”不等于 HCC）；
    - 治疗：明确切除/TACE/消融/系统治疗/移植；“刚切完”不追问大类；
    - 结局：明确 OS/复发（RFS），只说“生存/预后”不算；
    - 起点：明确手术日/治疗日/今天；
    - 问题：原话已经写清楚想问什么。
    """
    f = session.fields.get(name)
    if f is None or f.status == "conflict":
        return False
    if name == "disease":
        return f.status == "normalized" and f.normalized == "HCC"
    if name == "treatment":
        if f.status in ("negated", "normalized") and f.concept_id in NO_TREATMENT_CONCEPTS:
            return True
        if f.concept_id in EXPLICIT_TREATMENT_CONCEPTS:
            return True
        return _user_settled(f) and "类型未明确" not in (f.normalized or "")
    if name == "outcome":
        if f.concept_id == "outcome.os":
            # “总的生存期”这类原话就算明确 OS；只看原文/来源句，不看 canonical（canonical 永远含“总生存”）
            text = "%s %s" % (f.raw or "", f.source_sentence or "")
            return bool(OS_RAW_RE.search(text))
        if f.concept_id == "outcome.recurrence":
            return True
        return _user_settled(f) and f.normalized not in ("未明确", "预测需求未明确")
    if name == "start_point":
        return (f.status == "normalized"
                and (f.normalized in EXPLICIT_START_VALUES
                     or f.concept_id in EXPLICIT_START_CONCEPTS))
    if name == "intent":
        return f.status == "normalized" and f.normalized not in ("", "未明确")
    if name in ("horizon",):
        return f.status == "normalized" and bool(f.normalized)
    if name == "stage":
        return f.status in ("normalized", "ambiguous") and bool(f.normalized)
    if name == "subject":
        return f.status == "normalized"
    return f.status == "normalized"


def sufficient_fields(session: InputSession) -> dict[str, bool]:
    return {name: field_sufficient(session, name) for name in FIELD_ORDER}


# ---------------- 术语表 ----------------


def load_terms(path) -> list[Term]:
    terms = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            if not row.get("concept_id"):
                continue
            syns = [s.strip() for s in (row.get("synonyms") or "").split("|") if s.strip()]
            terms.append(Term(
                concept_id=row["concept_id"].strip(),
                canonical=row["canonical"].strip(),
                type=row["type"].strip(),
                synonyms=syns,
                auto_status=(row.get("auto_status") or "normalized").strip(),
                scope=(row.get("scope") or "hcc").strip(),
                note=(row.get("note") or "").strip(),
            ))
    return terms


def terms_by_concept(terms) -> dict[str, Term]:
    return {t.concept_id: t for t in terms}


# ---------------- 句子 / 上下文 ----------------

HARD_BREAK_RE = re.compile(r"[。！？；\n\r]+")
SOFT_BREAK_RE = re.compile(r"[ \t\u3000]+")

# 空格软切分的“保护边界”：这些位置的空格是短语内部空格，不能当分隔符
_NUM_TAIL_RE = re.compile(r"[0-9０-９一二三四五六七八九十两半]$")
_UNIT_HEADS = ("年", "岁", "个月", "月", "星期", "周", "天", "日", "小时", "分钟",
               "次", "例", "cm", "CM", "mm", "MM", "ml", "mL", "kg", "g", "mg",
               "dL", "%", "％")
_NEG_TAILS = ("非", "无", "未", "不", "否认", "排除", "除外", "没有", "未见", "未发现",
              "不是", "并非")
_LATIN_TAIL_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*$")
_LATIN_HEAD_RE = re.compile(r"[A-Za-z0-9.]+")


def _join_on_space(left: str, right: str, gap: str) -> bool:
    """判断 left <gap> right 是否应保持为同一短语（True = 不切）。"""
    if not left or not right:
        return False
    if "\t" in gap or "\u3000" in gap or len(gap) > 1:
        return False                      # 制表符/全角空格/多个空格 = 明确分隔
    # 1) 数字（含中文数字）+ 单位：3 年、58 岁、1.5 cm、30 %
    if _NUM_TAIL_RE.search(left) and right.startswith(_UNIT_HEADS):
        return True
    # 2) 否定词 + 内容：非 HCC、无 腹水、未见 复发
    if left.endswith(_NEG_TAILS):
        if re.match(r"[\u4e00-\u9fffA-Za-z0-9]", right):
            return True
    # 3) 拉丁复合名：Child-Pugh A、hepatocellular carcinoma、BCLC B、ECOG 0
    if _LATIN_TAIL_RE.search(left):
        head = _LATIN_HEAD_RE.match(right)
        if head:
            return True
    return False


def _soft_split(text: str, start: int, end: int):
    """在 [start, end) 内按空格软切分，并合并受保护的边界。"""
    pieces, pos = [], start
    for m in SOFT_BREAK_RE.finditer(text, start, end):
        pieces.append((pos, m.start(), m.group(0)))
        pos = m.end()
    pieces.append((pos, end, ""))
    merged: list[tuple[int, int]] = []
    for s, e, _gap_after in pieces:
        if s >= e:
            continue
        if merged:
            prev_s, prev_e = merged[-1]
            gap = text[prev_e:s]
            if _join_on_space(text[prev_s:prev_e].strip(), text[s:e].strip(), gap):
                merged[-1] = (prev_s, e)
                continue
        merged.append((s, e))
    out = []
    for s, e in merged:
        seg = text[s:e]
        lead = len(seg) - len(seg.lstrip())
        trail = len(seg.rstrip())
        if trail <= lead:
            continue
        out.append((seg.strip(), s + lead, s + trail))
    return out


def split_sentences(text: str):
    """保留字符偏移的分句：硬标点切分 + 空格软切分。

    硬切：[。！？；换行]
    软切：单个空格/连续空格；但数字+单位（3 年、58 岁）、否定+内容（非 HCC）、
         拉丁复合名（Child-Pugh A、hepatocellular carcinoma）之间的空格保留。
    返回 [(句子原文, start, end)]，start/end 指向实际内容（不含首尾空白）。
    """
    text = text or ""
    out = []
    pos = 0
    for m in HARD_BREAK_RE.finditer(text):
        stop = m.end()
        if stop > pos:
            out.extend(_soft_split(text, pos, stop))
        pos = stop
    if pos < len(text):
        out.extend(_soft_split(text, pos, len(text)))
    return out


def sentence_at(text: str, start: int, end: int):
    for seg, s, e in split_sentences(text):
        if s <= start and end <= e:
            return seg, s, e
    return text, 0, len(text)


def _context(text, start, end):
    return text[max(0, start - 14):start], text[end:end + 10]


def _has(cues, s):
    return any(c in s for c in cues)


def _negated_prefix(prefix: str) -> bool:
    tail = prefix[-8:]
    return bool(re.search(r"(非|不是|排除|未见|否认|无|未发现)\s*$", tail))


# ---------------- 规则抽取 ----------------


class RuleExtractor:
    def __init__(self, terms_path):
        self.terms = load_terms(terms_path)
        self.by_id = terms_by_concept(self.terms)

    # ---- 匹配 ----
    def _matches(self, text):
        raw = []
        low = text.lower()
        for term in self.terms:
            for syn in term.synonyms:
                needle = syn.lower()
                i = 0
                while True:
                    at = low.find(needle, i)
                    if at < 0:
                        break
                    raw.append((at, at + len(syn), term))
                    i = at + len(needle)
        raw.sort(key=lambda x: (x[0], -(x[1] - x[0])))
        # 同一 type 内跳过被更长词覆盖的匹配；不同 type 允许同 span
        chosen, used = [], {}
        for s, e, term in raw:
            spans = used.setdefault(term.type, [])
            if any(not (e <= a or s >= b) for a, b in spans):
                continue
            spans.append((s, e))
            chosen.append((s, e, term))
        return chosen

    def _status_for(self, text, start, end, term):
        prefix, suffix = _context(text, start, end)
        if term.type == "disease":
            if _has(HISTORY_CUES, prefix):
                return "historical"
            if _has(SUSPECT_CUES, prefix):
                return "suspected"
            if _negated_prefix(prefix):
                return "negated"
        return term.auto_status

    def extract(self, raw_text: str) -> InputSession:
        text = (raw_text or "").strip()
        session = InputSession(input_id="in_" + time.strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8],
                               raw_text=text)
        for name in FIELD_ORDER:
            session.fields[name] = FieldValue(field=name)

        by_field: dict[str, list[dict]] = {}
        for start, end, term in self._matches(text):
            sentence, _, _ = sentence_at(text, start, end)
            status = self._status_for(text, start, end, term)
            by_field.setdefault(term.type, []).append({
                "concept_id": term.concept_id,
                "canonical": term.canonical,
                "raw": text[start:end],
                "status": status,
                "span": [start, end],
                "sentence": sentence,
                "scope": term.scope,
                "note": term.note,
            })
            if term.type == "data":
                session.data_available.append(FieldValue(
                    field="data", raw=text[start:end], normalized=term.canonical,
                    status=status if status != "normalized" else "normalized",
                    source_span=[start, end], source_sentence=sentence,
                    candidates=[{"source": "rule", "concept_id": term.concept_id}],
                    concept_id=term.concept_id,
                ))

        self._resolve_disease(session, by_field.get("disease") or [])
        self._resolve_treatment(session, by_field.get("treatment") or [])
        self._resolve_stage(session, by_field.get("stage") or [])
        self._resolve_outcome(session, by_field.get("outcome") or [])
        self._resolve_time(session, by_field.get("start") or [], by_field.get("horizon") or [], text)
        self._resolve_intent(session, text)
        self._resolve_subject(session, text)
        self._resolve_extra_data(session, text)
        self._dedupe_data(session)
        self._rebuild_followups(session)
        return session

    # ---- 疾病 ----
    def _resolve_disease(self, session, cands):
        f = session.fields["disease"]
        f.candidates = [dict(c, source="rule") for c in cands]
        if not cands:
            return
        scopes = {c["scope"] for c in cands}
        concepts = {c["concept_id"] for c in cands}
        # 明确非 HCC 范围
        for c in cands:
            if c["scope"] == "non_hcc":
                self._set(f, c, "out_of_scope")
                f.flags.append("首版仅支持 HCC")
                if "disease.hcc" in concepts:
                    self._conflict(session, "disease", "同时出现 HCC 与范围外诊断", cands)
                return
        hcc_all = [c for c in cands if c["concept_id"] == "disease.hcc"]
        hcc_pos = [c for c in hcc_all if c["status"] != "negated"]
        hcc_neg = [c for c in hcc_all if c["status"] == "negated"]
        # 阳性 HCC 与否定 HCC 同时出现
        if hcc_pos and hcc_neg:
            self._conflict(session, "disease", "HCC 既有阳性又有否定表达", cands)
            f.status = "conflict"
            f.normalized = "HCC 阳性/否定冲突"
            f.raw = cands[0]["raw"]
            f.source_span = cands[0]["span"]
            f.source_sentence = cands[0]["sentence"]
            return
        hcc = hcc_all
        if hcc:
            c = hcc[0]
            status = c["status"]
            if status == "suspected":
                self._set(f, c, "suspected", normalized="HCC 疑似")
                f.flags.append("疑似不能当确诊")
            elif status == "historical":
                self._set(f, c, "historical", normalized="HCC 既往史；当前病灶未定")
                f.flags.append("历史诊断不等于本次")
            elif status == "negated":
                self._set(f, c, "negated", normalized="HCC 被否定")
            else:
                self._set(f, c, "normalized", normalized="HCC")
            # 同时出现“肝癌”上位词：保留提示，不改变 HCC 结论
            for other in cands:
                if other["concept_id"] == "disease.liver_cancer_unspecified":
                    f.flags.append("原话同时出现“肝癌”，已由明确 HCC 表达覆盖")
            return
        # 无明确 HCC：按上位词/未知/否定取
        for c in cands:
            if c["status"] in ("negated", "suspected", "historical"):
                self._set(f, c, c["status"], normalized=c["canonical"])
                if c["status"] == "suspected":
                    f.flags.append("疑似不能当确诊")
                return
        c = cands[0]
        status = "ambiguous" if c["concept_id"] == "disease.liver_cancer_unspecified" else (
            "unknown" if c["concept_id"] == "disease.liver_lesion_unspecified" else "ambiguous")
        self._set(f, c, status)
        f.flags.append("不自动归为 HCC")

    # ---- 治疗 ----
    def _resolve_treatment(self, session, cands):
        f = session.fields["treatment"]
        f.candidates = [dict(c, source="rule") for c in cands]
        if not cands:
            return
        ids = {c["concept_id"] for c in cands}
        none = ids & {"treatment.none", "treatment.no_surgery"}
        active = ids - {"treatment.none", "treatment.no_surgery"}
        if none and active:
            self._conflict(session, "treatment", "治疗状态冲突（如“未手术”与“已术后”）", cands)
            f.status = "conflict"
            f.normalized = "治疗状态冲突，待确认"
            f.raw = cands[0]["raw"]
            f.source_span = cands[0]["span"]
            f.source_sentence = cands[0]["sentence"]
            return
        if ids & {"treatment.none", "treatment.no_surgery"}:
            c = (ids and [x for x in cands if x["concept_id"] in none][0])
            self._set(f, c, "normalized" if c["concept_id"] == "treatment.none" else "negated")
            return
        # 术后 + 具体治疗：保留具体治疗，备注术后
        specifics = [c for c in cands if c["concept_id"] not in ("treatment.postop",)]
        if specifics:
            c = specifics[0]
            self._set(f, c, c["status"])
            if any(x["concept_id"] == "treatment.postop" for x in cands):
                f.flags.append("已术后，治疗类型/时间仍需确认")
            return
        c = cands[0]
        self._set(f, c, "ambiguous" if c["concept_id"] == "treatment.postop" else c["status"],
                  normalized="治疗后，治疗类型未明确" if c["concept_id"] == "treatment.postop" else None)

    # ---- 阶段 ----
    def _resolve_stage(self, session, cands):
        f = session.fields["stage"]
        f.candidates = [dict(c, source="rule") for c in cands]
        if not cands:
            return
        ids = {c["concept_id"] for c in cands}
        if "stage.pre_surgery" in ids and ("stage.post_surgery" in ids or "stage.post_pathology_pending" in ids):
            self._conflict(session, "stage", "术前与术后表达冲突", cands)
            f.status = "conflict"
            f.normalized = "术前/术后冲突"
            f.raw = cands[0]["raw"]
            f.source_span = cands[0]["span"]
            f.source_sentence = cands[0]["sentence"]
            return
        if "stage.post_pathology_pending" in ids:
            c = [x for x in cands if x["concept_id"] == "stage.post_pathology_pending"][0]
            self._set(f, c, "normalized", normalized="治疗后，病理未取得")
            f.flags.append("病理未取得 ≠ 病理阴性")
            return
        c = cands[0]
        self._set(f, c, "ambiguous" if c["concept_id"] == "stage.post_surgery" else c["status"])

    # ---- 结局 ----
    def _resolve_outcome(self, session, cands):
        f = session.fields["outcome"]
        f.candidates = [dict(c, source="rule") for c in cands]
        if not cands:
            return
        ids = {c["concept_id"] for c in cands}
        if "outcome.prognosis_unclear" in ids and len(ids) == 1:
            c = cands[0]
            self._set(f, c, "unknown", normalized="预测需求未明确")
            f.flags.append("不生成寿命或概率")
            return
        c = cands[0]
        self._set(f, c, "ambiguous" if c["status"] == "normalized" else c["status"])
        if c["concept_id"] == "outcome.os":
            f.flags.append("“生存”需确认是总生存还是其他")
        if "outcome.recurrence" in ids and "outcome.prognosis_unclear" in ids:
            f.flags.append("原话同时问到复发与生存期，建议分别核查")

    # ---- 时间 ----
    def _resolve_time(self, session, starts, horizons, text):
        f_start = session.fields["start_point"]
        f_h = session.fields["horizon"]
        f_d = session.fields["postop_duration"]
        # 术后/切除后 X（年/月/周/天）：记为已术后时长；兼容“刚开完刀”“根治性切除一个月”
        m = re.search(
            r"(?:术后|手术后|切除术后|切除后|根治性切除后|根治性手术后|根治性切除|刚开完刀|刚做完手术)"
            r"\s*(\d+|[一二两三四五六七八九十]+)\s*(年|个月|月|周|天)",
            text)
        if m:
            num_raw, unit = m.group(1), m.group(2)
            num = int(num_raw) if num_raw.isdigit() else _CN_NUM.get(num_raw, 0)
            if num > 0:
                if unit == "年":
                    norm = "%d个月" % (num * 12)
                elif unit in ("个月", "月"):
                    norm = "%d个月" % num
                else:
                    norm = "%d%s" % (num, unit)
                sess, _s, _e = sentence_at(text, m.start(), m.end())
                f_d.raw = m.group(0)
                f_d.normalized = norm
                f_d.status = "normalized"
                f_d.source_span = [m.start(), m.end()]
                f_d.source_sentence = sess
                f_d.confirm = "pending"
                tail = text[m.end():m.end() + 10]
                head = text[max(0, m.start() - 6):m.start()]
                now_cue = any(k in tail or k in head or k in sess
                              for k in ("未来", "以后", "接下来", "现在", "目前", "当前", "这次", "复查"))
                f_start.raw = m.group(0)
                f_start.source_span = [m.start(), m.end()]
                f_start.source_sentence = sess
                if now_cue:
                    f_start.normalized = "今天"
                    f_start.status = "normalized"
                    f_start.concept_id = "start.today"
                else:
                    f_start.normalized = "时间含义未明（已术后 vs 预测术后）"
                    f_start.status = "ambiguous"
        # 兜底：写了“一个月复查/随访”也算术后时长（例：刚开完刀，一个月复查）
        if f_d.status != "normalized":
            m2 = re.search(r"(\d+|[一二两三四五六七八九十]+)\s*(个月|月|周|天)\s*(?:复查|随访)", text)
            if m2:
                num_raw, unit = m2.group(1), m2.group(2)
                num = int(num_raw) if num_raw.isdigit() else _CN_NUM.get(num_raw, 0)
                if num > 0:
                    sess, _s, _e = sentence_at(text, m2.start(), m2.end())
                    f_d.raw = m2.group(0)
                    f_d.normalized = ("%d个月" % num) if unit in ("个月", "月") else ("%d%s" % (num, unit))
                    f_d.status = "normalized"
                    f_d.source_span = [m2.start(), m2.end()]
                    f_d.source_sentence = sess
        # 有术后时长但阶段没识别：补成术后
        if f_d.status == "normalized" and session.fields["stage"].status == "unknown":
            st = session.fields["stage"]
            st.raw = f_d.raw
            st.normalized = "术后"
            st.status = "ambiguous"
            st.source_span = f_d.source_span
            st.source_sentence = f_d.source_sentence

        # 明确起点
        for c in starts:
            if c["status"] == "normalized":
                self._set(f_start, c, "normalized")
                break
        # 范围：跳过“术后三年”里的“三年”
        for c in horizons:
            prefix = text[max(0, c["span"][0] - 2):c["span"][0]]
            if "术后" in prefix and "未来" not in prefix and "以后" not in prefix:
                continue
            self._set(f_h, c, "normalized")
            break
        # 范围兜底：术语表只有 12/36/60 个月，这里容忍“3 年”“6 个月”等带空格/任意月数写法
        if f_h.status != "normalized":
            for m in re.finditer(r"(\d+)\s*(年|个月|月)", text):
                prefix = text[max(0, m.start() - 4):m.start()]
                if re.search(r"术后|手术后|切除后|治疗后", prefix):
                    continue
                months = int(m.group(1)) * (12 if m.group(2) == "年" else 1)
                if months <= 0 or months > 120:
                    continue
                sess, _s, _e = sentence_at(text, m.start(), m.end())
                f_h.raw = m.group(0)
                f_h.normalized = "%d个月" % months
                f_h.status = "normalized"
                f_h.source_span = [m.start(), m.end()]
                f_h.source_sentence = sess
                break
        # “未来/以后” + 术后时长 → 从今天起算
        if f_d.status == "normalized" and re.search(r"未来|以后|接下来", text) and f_start.status != "normalized":
            f_start.raw = f_d.raw
            f_start.normalized = "今天"
            f_start.status = "normalized"
            f_start.concept_id = "start.today"
            f_start.source_span = f_d.source_span
            f_start.source_sentence = f_d.source_sentence

    # ---- 意图 / 主体 ----
    INTENT_STRONG = re.compile(r"多长时间|多久|几个月|几年|生存期|能活|预后|复发|转移|生存率|生存")
    INTENT_CUES = re.compile(r"想|希望|要|需要|评估|预测|预期|预计|看|是否|能不能|会不会|可以|问|了解")
    INTENT_NOISE = re.compile(r"挺好|良好|正常|稳定|不错|恢复好")

    def _resolve_intent(self, session, text):
        """挑真正的“想问的问题”：疑问句/带时间-结局词优先，状态陈述降权。"""
        f = session.fields["intent"]
        clauses = []
        for seg, _s, _e in split_sentences(text):
            for m in re.finditer(r"[^，,；;]+", seg):
                c = m.group(0).strip()
                if c:
                    clauses.append(c)
        scored = []
        for c in clauses:
            core = c.strip("。！？?! 	")
            if not core:
                continue
            score = 0
            if c.rstrip().endswith(("？", "?")):
                score += 3
            if re.search(r"吗|呢", core):
                score += 2
            if self.INTENT_STRONG.search(core):
                score += 3
            if self.INTENT_CUES.search(core):
                score += 1
            if self.INTENT_NOISE.search(core):
                score -= 3
            scored.append((score, len(core), core))
        best = max((x[0] for x in scored), default=0)
        if best <= 0:
            f.status = "unknown"
            f.normalized = "未明确"
            return
        if best >= 6:
            floor = max(6, best - 1)
            top = sorted([x for x in scored if x[0] >= floor], key=lambda x: x[1], reverse=True)
            chosen = [x[2] for x in top[:2]]
        else:
            top = sorted([x for x in scored if x[0] == best], key=lambda x: x[1], reverse=True)
            chosen = [top[0][2]]
        q = "；".join(chosen)
        f.raw = q
        f.normalized = q
        f.status = "normalized"
        f.source_sentence = q

    def _resolve_subject(self, session, text):
        f = session.fields["subject"]
        m = re.search(r"家属|父亲|母亲|妻子|丈夫|孩子|朋友|家人", text)
        if m:
            sess, s, e = sentence_at(text, m.start(), m.end())
            f.raw = m.group(0)
            f.normalized = "家属代问"
            f.status = "ambiguous"
            f.source_span = [m.start(), m.end()]
            f.source_sentence = sess
        elif text:
            f.normalized = "患者本人"
            f.status = "normalized"

    def _resolve_extra_data(self, session, text):
        """术语表之外的定量信息：肿瘤大小、BCLC/分期范围（作为“已有资料”记录）。"""
        def add(raw, normalized, concept_id, span):
            sess, _s, _e = sentence_at(text, span[0], span[1])
            session.data_available.append(FieldValue(
                field="data", raw=raw, normalized=normalized, status="normalized",
                source_span=[span[0], span[1]], source_sentence=sess,
                candidates=[{"source": "rule", "concept_id": concept_id}],
                concept_id=concept_id))

        m = re.search(r"(\d+(?:\.\d+)?)\s*(cm|公分|厘米)", text, re.I)
        if m:
            add(m.group(0), "肿瘤大小 %s cm" % m.group(1), "data.tumor_size",
                (m.start(), m.end()))

        m = re.search(r"(BCLC|巴塞罗那分期)\s*([0-9A-Ca-c])\s*期", text, re.I)
        if m:
            add(m.group(0), "BCLC %s 期" % m.group(2).upper(), "data.bclc",
                (m.start(), m.end()))

        m = re.search(r"([零0-9]+)\s*到\s*([A-Ca-c])\s*期", text)
        if m:
            lo = m.group(1).replace("零", "0")
            add(m.group(0), "分期 %s-%s 期" % (lo, m.group(2).upper()),
                "data.stage_range", (m.start(), m.end()))

    def _dedupe_data(self, session):
        seen, out = set(), []
        for d in session.data_available:
            key = (d.normalized, d.status)
            if key in seen:
                continue
            seen.add(key)
            out.append(d)
        session.data_available = out

    # ---- 工具 ----
    def _set(self, f: FieldValue, c: dict, status=None, normalized=None):
        f.raw = c.get("raw") or f.raw
        f.normalized = normalized or c.get("canonical") or f.normalized
        f.status = status or c.get("status") or "unknown"
        f.source_span = c.get("span")
        f.source_sentence = c.get("sentence") or ""
        f.concept_id = c.get("concept_id") or ""
        if f.status == "conflict":
            f.concept_id = ""

    def _conflict(self, session, field_name, message, cands):
        spans = [c.get("span") for c in cands if c.get("span")]
        session.conflicts.append(Conflict(field=field_name, message=message, spans=spans))

    # ---- 追问（只问信息不足的字段） ----
    @staticmethod
    def _rebuild_followups(session: InputSession):
        session.followups = []
        d = session.fields["disease"]
        if not field_sufficient(session, "disease"):
            if d.status in ("unknown", "ambiguous"):
                session.followups.append(Followup(
                    "q_disease", "disease",
                    "请确认疾病类型：是 HCC、其他类型，还是尚未明确？",
                    ["HCC", "其他类型", "尚未明确", "不知道"]))
            elif d.status == "suspected":
                session.followups.append(Followup(
                    "q_disease", "disease",
                    "“疑似 HCC”是否已有确诊依据？目前按疑似记录，不能当确诊。",
                    ["已确诊 HCC", "仍疑似", "已排除 HCC", "不知道"]))
            elif d.status == "historical":
                session.followups.append(Followup(
                    "q_disease", "disease",
                    "HCC 是既往史还是本次诊断？当前病灶的性质是什么？",
                    ["既往 HCC，本次未定", "本次 HCC", "本次非 HCC", "不知道"]))
            elif d.status == "negated":
                session.followups.append(Followup(
                    "q_disease", "disease",
                    "已排除 HCC 的依据是什么？当前诊断是什么？",
                    ["其他肝脏肿瘤", "良性病变", "仍未明确", "不知道"]))
            elif d.status == "out_of_scope":
                session.followups.append(Followup(
                    "q_disease", "disease",
                    "首版只支持 HCC；该诊断不在范围内，需要说明范围限制。",
                    ["返回修改", "仅查看范围说明"]))
            elif d.status == "conflict":
                session.followups.append(Followup(
                    "q_disease", "disease",
                    "HCC 的表达前后冲突，请确认以哪一句为准。",
                    ["以阳性为准", "以否定为准", "都不确定", "不知道"]))

        t = session.fields["treatment"]
        if t.status in ("ambiguous", "conflict") and not field_sufficient(session, "treatment"):
            session.followups.append(Followup(
                "q_treatment", "treatment",
                "请确认治疗类型（切除/TACE/消融/系统治疗）、是否完成、是否根治性？",
                ["已切除（根治性）", "已切除（是否根治不明）", "TACE/消融/系统治疗", "尚未治疗", "不知道"]))
        s = session.fields["start_point"]
        if s.status in ("unknown", "ambiguous") and not field_sufficient(session, "start_point"):
            session.followups.append(Followup(
                "q_start", "start_point",
                "预测范围从手术日、治疗日开始，还是从今天起算？",
                ["手术日", "治疗日", "今天", "不知道"]))
        h = session.fields["horizon"]
        if not field_sufficient(session, "horizon"):
            session.followups.append(Followup(
                "q_horizon", "horizon",
                "想预测多长时间内的结局？（这决定模型/时点是否适用）",
                ["1 年", "3 年", "5 年", "自定义输入", "不知道"]))
        o = session.fields["outcome"]
        if o.status in ("unknown", "ambiguous", "conflict") and not field_sufficient(session, "outcome"):
            session.followups.append(Followup(
                "q_outcome", "outcome",
                "想预测的是总生存（OS）、复发（RFS/早期复发），还是其他结局？",
                ["总生存 OS", "复发 RFS", "其他", "不知道"]))
        it = session.fields["intent"]
        if not field_sufficient(session, "intent"):
            session.followups.append(Followup(
                "q_intent", "intent",
                "你想回答的核心问题是什么？（例如：这个模型能不能用于术前判断 1 年内复发）",
                ["自定义输入", "不知道"]))
        sub = session.fields["subject"]
        if sub.status == "ambiguous" and not field_sufficient(session, "subject"):
            session.followups.append(Followup(
                "q_subject", "subject",
                "这段信息是患者本人提供，还是家属代问？",
                ["患者本人", "家属代问", "不知道"], required=False))
        if session.conflicts:
            fields = "、".join(sorted({c.field for c in session.conflicts}))
            session.followups.append(Followup(
                "q_conflict", "conflict",
                "以下字段存在前后冲突：%s。请确认以哪一句为准。" % fields,
                ["逐项确认", "不知道"]))

    # ---- 确认后映射到 Agent v0.6 的 scenario ----
    @staticmethod
    def to_agent_scenario(session: InputSession, paper_text=""):
        d = session.fields["disease"]
        t = session.fields["treatment"]
        s = session.fields["stage"]
        o = session.fields["outcome"]
        start = session.fields["start_point"]
        h = session.fields["horizon"]
        intent = session.fields["intent"]
        postop = session.fields["postop_duration"]

        parts = []
        if d.normalized and d.status != "unknown":
            parts.append("疾病：%s" % d.normalized)
        if t.normalized and t.status != "unknown":
            parts.append("治疗：%s" % t.normalized)
        if s.normalized and s.status != "unknown":
            parts.append("阶段：%s" % s.normalized)
        if postop.status == "normalized":
            parts.append("已术后 %s" % postop.normalized)
        if session.data_available:
            got = [x.normalized for x in session.data_available if x.status == "normalized"]
            miss = [x.normalized for x in session.data_available if x.status != "normalized"]
            if got:
                parts.append("已有资料：%s" % "、".join(got))
            if miss:
                parts.append("缺失资料：%s" % "、".join(miss))
        patient = "；".join(parts) or "未明确"
        if len(patient) < 8:
            patient = (patient + "（需补充患者信息）").strip()

        task = intent.normalized if intent.status == "normalized" else "评估该患者在该时点的模型适用性"
        if len(task) < 8:
            task = (task + "（需补充具体问题）").strip()

        # 时点枚举
        if s.status == "normalized" and "术前" in s.normalized:
            tp = "pre_surgery"
        elif s.status in ("normalized", "ambiguous") and ("术后" in (s.normalized or "") or "病理" in (s.normalized or "")):
            tp = "post_surgery"
        elif t.normalized in ("TACE", "系统治疗", "消融"):
            tp = "pre_treatment"
        else:
            tp = "未明确"

        outcome = o.normalized or "未明确"
        if h.status == "normalized":
            outcome = "%s（%s）" % (outcome, h.normalized)
        given = [x.normalized for x in session.data_available if x.status == "normalized"]
        missing = [x.normalized for x in session.data_available if x.status != "normalized"]

        scenario = {
            "task_zh": task,
            "patient_summary_zh": patient,
            "use_timepoint": tp,
            "outcome_type": outcome,
            "inputs_given": "、".join(given),
            "inputs_missing": "、".join(missing),
        }
        if paper_text:
            scenario["paper_text"] = paper_text
        return scenario

