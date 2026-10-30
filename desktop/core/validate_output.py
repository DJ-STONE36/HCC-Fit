#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
validate_output.py — 交接包输出的最小 JSON Schema 校验器（只用标准库）

为什么不用 jsonschema 包：交接包里不能假设对方装了什么。
支持的关键字：type / const / enum / required / properties / additionalProperties /
items / minItems / maxItems / minLength / maxLength / pattern / minimum / maximum。

用法：
    python validate_output.py --schema <schema.json> <instance.json> [more.json ...]
    python validate_output.py --selftest
退出码：0 = 全部通过；1 = 有错误；2 = 用法错误。

规则：不合格就 fail closed —— 有错误不要手工「修一下再传」，改输入重跑。
"""

import json
import re
import sys
from datetime import date, datetime

TOOL_VERSION = "0.1.0"


def _type_ok(value, expected):
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _check_format(value, fmt):
    try:
        if fmt == "date":
            date.fromisoformat(value)
        elif fmt == "date-time":
            datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return f"不符合 {fmt} 格式: {value!r}"
    return None


def validate(instance, schema, path="$"):
    errors = []
    if not isinstance(schema, dict):
        return errors

    if "const" in schema and instance != schema["const"]:
        errors.append(f"{path}: 必须等于 {schema['const']!r}，实际 {instance!r}")

    if "enum" in schema and instance not in schema["enum"]:
        errors.append(f"{path}: 不在允许值内 {schema['enum']}，实际 {instance!r}")

    if "type" in schema:
        expected = schema["type"]
        allowed = expected if isinstance(expected, list) else [expected]
        if not any(_type_ok(instance, t) for t in allowed):
            errors.append(f"{path}: 类型应为 {allowed}，实际 {type(instance).__name__}")
            return errors  # 类型都不对，后续检查无意义

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < schema["minLength"]:
            errors.append(f"{path}: 长度 < {schema['minLength']}")
        if "maxLength" in schema and len(instance) > schema["maxLength"]:
            errors.append(f"{path}: 长度 > {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], instance):
            errors.append(f"{path}: 不匹配 {schema['pattern']}，实际 {instance!r}")
        if "format" in schema:
            msg = _check_format(instance, schema["format"])
            if msg:
                errors.append(f"{path}: {msg}")

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        if "minimum" in schema and instance < schema["minimum"]:
            errors.append(f"{path}: {instance} < minimum {schema['minimum']}")
        if "maximum" in schema and instance > schema["maximum"]:
            errors.append(f"{path}: {instance} > maximum {schema['maximum']}")

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < schema["minItems"]:
            errors.append(f"{path}: 元素数 {len(instance)} < minItems {schema['minItems']}")
        if "maxItems" in schema and len(instance) > schema["maxItems"]:
            errors.append(f"{path}: 元素数 {len(instance)} > maxItems {schema['maxItems']}")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for i, item in enumerate(instance):
                errors.extend(validate(item, item_schema, f"{path}[{i}]"))

    if isinstance(instance, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in instance:
                errors.append(f"{path}: 缺少必填字段 {key!r}")
        extra_schema = schema.get("additionalProperties", True)
        for key, value in instance.items():
            if key in props:
                errors.extend(validate(value, props[key], f"{path}.{key}"))
            elif extra_schema is False:
                errors.append(f"{path}: 不允许的字段 {key!r}")
            elif isinstance(extra_schema, dict):
                errors.extend(validate(value, extra_schema, f"{path}.{key}"))
        names = schema.get("propertyNames")
        if isinstance(names, dict) and "pattern" in names:
            for key in instance:
                if not re.search(names["pattern"], key):
                    errors.append(f"{path}: 字段名 {key!r} 不匹配 {names['pattern']}")

    return errors


def _load(path):
    with open(path, "r", encoding="utf-8-sig") as fh:
        return json.load(fh)


def selftest():
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["a", "b"],
        "properties": {
            "a": {"type": "string", "pattern": "^x"},
            "b": {"type": "integer", "minimum": 1, "maximum": 3},
            "c": {"type": ["string", "null"]},
            "d": {"type": "array", "minItems": 1, "items": {"enum": ["p", "q"]}},
        },
    }
    good = {"a": "x1", "b": 2, "c": None, "d": ["p"]}
    bad = {"a": "y", "b": 9, "e": 1, "d": []}
    ok = validate(good, schema) == []
    fails = validate(bad, schema)
    # 期望命中：pattern、maximum、多余字段、minItems —— 共 4 类
    hit = (
        any("不匹配" in e for e in fails)
        and any("maximum" in e for e in fails)
        and any("不允许的字段" in e for e in fails)
        and any("minItems" in e for e in fails)
    )
    print(f"selftest: valid_case={'PASS' if ok else 'FAIL'} invalid_case={'PASS' if hit else 'FAIL'}")
    print("  捕获错误数:", len(fails))
    for e in fails:
        print("   -", e)
    return 0 if (ok and hit) else 1


def main(argv):
    if "--version" in argv:
        print(TOOL_VERSION)
        return 0
    if "--selftest" in argv:
        return selftest()
    if "--schema" not in argv:
        print(__doc__)
        return 2
    i = argv.index("--schema")
    try:
        schema_path = argv[i + 1]
        targets = [a for a in argv[i + 2:] if not a.startswith("--")]
    except IndexError:
        print("用法: python validate_output.py --schema <schema.json> <instance.json> ...")
        return 2
    if not targets:
        print("缺少待校验文件")
        return 2

    schema = _load(schema_path)
    total = 0
    for t in targets:
        try:
            instance = _load(t)
        except Exception as exc:  # noqa: BLE001
            print(f"FAIL {t}: 读不到或不是合法 JSON -> {exc}")
            total += 1
            continue
        errs = validate(instance, schema)
        if errs:
            total += len(errs)
            print(f"FAIL {t}  ({len(errs)} 处)")
            for e in errs[:40]:
                print("  -", e)
            if len(errs) > 40:
                print(f"  ... 其余 {len(errs) - 40} 处省略")
        else:
            print(f"OK   {t}")
    return 0 if total == 0 else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
