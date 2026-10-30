# -*- coding: utf-8 -*-
"""内置 Router 引擎测试（不联网）：路径解析 / 状态 / 参数透传 / 错误映射 / 对话框。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import router_engine  # noqa: E402
from app.router_client import RouterWorker  # noqa: E402

SCENARIO = {"text": "HCC 切除后想预测 1 年复发", "disease": "HCC",
            "time_zero": "surgery", "outcome": "RFS", "horizon_months": 12}

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-38s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-38s %s: %s" % (name, type(exc).__name__, exc))


def case_engine_available():
    st = router_engine.engine_status()
    assert st["available"], st
    path = Path(st["path"])
    assert (path / "hccfit" / "strategies.py").is_file(), path
    assert st["strategies"] == ["fast", "balanced", "auto"], st["strategies"]
    assert router_engine.find_router_dir() is not None
    return "引擎可用：%s" % path.name


def case_search_passthrough():
    import hccfit.strategies as strategies
    captured = {}
    orig = strategies.run

    def fake(scenario, strategy="auto", k=5, deadline_ms=120000, thinking=False,
             require_fulltext=False, request_id=None, api_key=None):
        captured.update(scenario=dict(scenario), strategy=strategy, k=k,
                        deadline_ms=deadline_ms, api_key=api_key)
        return {"papers": [], "strategy_used": strategy, "meta": {}}

    strategies.run = fake
    try:
        out = router_engine.search(SCENARIO, strategy="fast", k=99,
                                   sciencepal_key="sp-x", filter_agent_id="agent-1")
        router_engine.search(SCENARIO, strategy="fast", k=0, sciencepal_key="sp-x")
    finally:
        strategies.run = orig
    assert out["strategy_used"] == "fast", out
    assert captured["k"] == 1, captured          # k 被夹到 1..8
    assert captured["api_key"] == "sp-x", captured
    from hccfit import config
    assert config.SCIENCEPAL_FILTER_AGENT_ID == "agent-1"
    return "scenario/strategy/k/api_key 透传；k 夹到 1..8"


def case_scope_error():
    import hccfit.strategies as strategies
    from hccfit.scope import ScopeError
    orig = strategies.run

    def boom(*_a, **_k):
        raise ScopeError("out_of_scope", "只接受已确认 HCC")

    strategies.run = boom
    try:
        try:
            router_engine.search({"text": "肝内胆管癌", "disease": "iCCA"}, "fast", 3)
        except router_engine.RouterEngineError as exc:
            assert "out_of_scope" in str(exc), exc
        else:
            raise AssertionError("越界诊断应抛 RouterEngineError")
    finally:
        strategies.run = orig
    return "ScopeError → RouterEngineError（带 code）"


def case_worker_builtin():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    calls = {}
    orig = router_engine.search

    def fake(scenario, strategy="fast", k=8, sciencepal_key="", filter_agent_id="",
             base_url="", deadline_ms=None, require_fulltext=False):
        calls.update(strategy=strategy, k=k, key=sciencepal_key,
                     agent=filter_agent_id, fulltext=require_fulltext)
        return {"papers": [{"pmid": "1"}], "strategy_used": strategy}

    router_engine.search = fake
    got = []
    worker = RouterWorker(SCENARIO, "balanced", 2, "sp-k", engine="builtin",
                          filter_agent_id="agent-9")
    worker.done.connect(got.append)
    try:
        worker.run()
    finally:
        router_engine.search = orig
    assert got and got[0]["papers"], got
    assert calls == {"strategy": "balanced", "k": 2, "key": "sp-k",
                     "agent": "agent-9", "fulltext": False}, calls
    return "后台线程走内置引擎，参数不出错"


def case_worker_http():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import app.router_client as rc
    captured = {}

    class FakeClient:
        def __init__(self, base_url, token, sciencepal_key=""):
            captured.update(url=base_url, token=token, key=sciencepal_key)

        def search(self, scenario, strategy, k):
            captured.update(strategy=strategy, k=k)
            return {"papers": [], "strategy_used": strategy}

    orig = rc.RouterClient
    rc.RouterClient = FakeClient
    got = []
    worker = RouterWorker(SCENARIO, "fast", 4, engine="http",
                          base_url="http://127.0.0.1:8080", token="tok")
    worker.done.connect(got.append)
    try:
        worker.run()
    finally:
        rc.RouterClient = orig
    assert captured["url"] == "http://127.0.0.1:8080" and captured["token"] == "tok"
    assert got and got[0]["strategy_used"] == "fast", got
    return "外部 HTTP 核心代码仍保留（界面不暴露）"


def case_dialog():
    from PySide6.QtWidgets import QApplication
    from app.router_dialog import RouterDialog
    QApplication.instance() or QApplication([])
    dlg = RouterDialog(SCENARIO, "sp_live_abcdefghijklmnop")
    assert not hasattr(dlg, "engine"), "界面不应再有检索方式下拉框"
    assert not hasattr(dlg, "external_row"), "界面不应再有外部服务地址栏"
    assert not hasattr(dlg, "filter_agent"), "界面不应再要求 filter agent ID"
    assert "内置引擎可用" in dlg.engine_msg.text(), dlg.engine_msg.text()
    assert "已配置" in dlg.engine_msg.text(), dlg.engine_msg.text()
    assert dlg.search_btn.isEnabled()
    dlg.close()
    return "只有内置引擎；Key 来自主窗口；无 filter agent 输入"


def _dialog_with_papers():
    from PySide6.QtWidgets import QApplication
    from app.router_dialog import RouterDialog
    QApplication.instance() or QApplication([])
    dlg = RouterDialog(SCENARIO, "sp_live_abcdefghijklmnop")
    papers = [
        {"pmid": "111", "title": "Deep Segmentation Feature-Based Radiomics Improves "
                                "Recurrence Prediction in Early-Stage Hepatocellular "
                                "Carcinoma After Curative Resection: A Multicenter Study",
         "year": 2023, "journal": "Eur Radiol", "doi": "10.1/x",
         "readability": {"level": "abstract_only"}, "why": ["匹配"], "risk": ["回顾性"]},
        {"pmid": "222", "title": "Second paper", "year": 2021, "journal": "Ann Surg",
         "doi": "10.2/y",
         "readability": {"level": "fulltext"}, "why": [], "risk": []},
    ]
    dlg._on_done({"papers": papers, "warnings": [], "strategy_used": "fast"})
    return dlg


def _open_with(dlg):
    from app import router_dialog as rd
    opened = []
    orig = rd.open_external_url
    rd.open_external_url = lambda url: (opened.append(url) or (True, "test"))
    try:
        dlg.open_fulltext()
    finally:
        rd.open_external_url = orig
    return opened


def case_open_selected_row():
    dlg = _dialog_with_papers()
    try:
        dlg.table.clearSelection()
        dlg.table.selectRow(1)                 # 选第二篇
        opened = _open_with(dlg)
    finally:
        dlg.close()
    assert opened == ["https://pubmed.ncbi.nlm.nih.gov/222/"], opened
    return "选哪篇开哪篇（第二篇）"


def case_open_multi_selected():
    from PySide6.QtWidgets import QTableWidgetSelectionRange
    dlg = _dialog_with_papers()
    try:
        dlg.table.clearSelection()
        dlg.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 5), True)
        opened = _open_with(dlg)
    finally:
        dlg.close()
    assert opened == ["https://pubmed.ncbi.nlm.nih.gov/111/",
                      "https://pubmed.ncbi.nlm.nih.gov/222/"], opened
    return "多选两篇同时打开"


def case_open_checked_fallback():
    from PySide6.QtCore import Qt
    dlg = _dialog_with_papers()
    try:
        dlg.table.clearSelection()
        for row in range(dlg.table.rowCount()):     # 只勾第二篇
            cell = dlg.table.item(row, 0)
            cell.setCheckState(Qt.CheckState.Unchecked)
        dlg.table.item(1, 0).setCheckState(Qt.CheckState.Checked)
        opened = _open_with(dlg)
    finally:
        dlg.close()
    assert opened == ["https://pubmed.ncbi.nlm.nih.gov/222/"], opened
    return "没选行时按勾选打开"


def case_copy_doi_multi():
    from PySide6.QtWidgets import QApplication, QTableWidgetSelectionRange
    dlg = _dialog_with_papers()
    try:
        dlg.table.clearSelection()
        dlg.table.setRangeSelected(QTableWidgetSelectionRange(0, 0, 1, 5), True)
        dlg.copy_doi()
    finally:
        dlg.close()
    got = QApplication.clipboard().text()
    assert got == "10.1/x\n10.2/y", repr(got)
    return "复制 DOI 支持多选（换行分隔）"



def case_button_click_opens_selected():
    """回归：clicked 会带 checked=False，不能被当成 papers=False。"""
    from app import router_dialog as rd
    dlg = _dialog_with_papers()
    opened = []
    orig = rd.open_external_url
    rd.open_external_url = lambda u: (opened.append(u) or (True, "test"))
    try:
        dlg.table.clearSelection()
        dlg.table.selectRow(1)
        dlg.full_btn.click()
    finally:
        rd.open_external_url = orig
        dlg.close()
    assert opened == ["https://pubmed.ncbi.nlm.nih.gov/222/"], opened
    return "真实按钮点击也能打开选中行"



def case_browser_exit_detected():
    import os
    from app import links
    called = {"start": False}
    orig_launch, orig_start = links._launch_default_browser, os.startfile
    links._launch_default_browser = lambda u: (False, links.EXIT_PREFIX + "（exit=21）")
    os.startfile = lambda u: called.__setitem__("start", True)
    try:
        ok, how = links.open_external_url("https://example.test/x")
    finally:
        links._launch_default_browser = orig_launch
        os.startfile = orig_start
    assert not ok and "exit=21" in how, (ok, how)
    assert called["start"] is False, "秒退后不应再用 startfile 假成功"
    return "浏览器秒退时明确报错，不再假成功"


def case_open_external_fallback():
    import os
    import webbrowser
    from PySide6.QtGui import QDesktopServices
    from app import links
    orig_launch = links._launch_default_browser
    orig_start, orig_qt, orig_wb = os.startfile, QDesktopServices.openUrl, webbrowser.open
    links._launch_default_browser = lambda _u: (False, "no browser")
    os.startfile = lambda _u: (_ for _ in ()).throw(OSError("blocked"))
    QDesktopServices.openUrl = staticmethod(lambda _u: False)
    webbrowser.open = lambda _u: True
    try:
        ok, how = links.open_external_url("https://example.test/x")
    finally:
        links._launch_default_browser = orig_launch
        os.startfile = orig_start
        QDesktopServices.openUrl = orig_qt
        webbrowser.open = orig_wb
    assert ok and how == "webbrowser", (ok, how)
    return "默认浏览器/startfile/Qt 失败后仍能打开"


def case_build_cmdline():
    from app.links import build_cmdline
    cmd = r'"C:\Program Files\Edge\msedge.exe" --single-argument %1'
    got = build_cmdline(cmd, "https://example.test/x")
    assert got == r'"C:\Program Files\Edge\msedge.exe" --single-argument "https://example.test/x"', got
    cmd2 = r'"C:\b.exe" "%1"'
    assert build_cmdline(cmd2, "https://a") == r'"C:\b.exe" "https://a"'
    return "命令行模板 %1 替换正确"


def case_titles_wrap():
    from PySide6.QtWidgets import QHeaderView
    from PySide6.QtCore import Qt
    dlg = _dialog_with_papers()
    try:
        assert dlg.table.textElideMode() == Qt.TextElideMode.ElideNone
        dlg.show()
        from PySide6.QtCore import QTimer
        QTimer.singleShot(0, dlg._fit_rows)
        dlg._fit_rows()
        h = dlg.table.rowHeight(0)
        assert 44 <= h <= 160, h          # 合理换行高度，不是被固定成 48
        full = dlg.table.item(0, 1).text()
        assert "Multicenter Study" in full      # 完整标题保留在 item 里
    finally:
        dlg.close()
    return "标题完整、行高随内容换行"


def case_save_candidates():
    import os
    import shutil
    dlg = _dialog_with_papers()
    data = BASE / "tests" / "_tmp_router_ui"
    shutil.rmtree(data, ignore_errors=True)
    os.environ["HCCFIT_DATA_DIR"] = str(data)
    try:
        dlg.save_candidates()
        assert dlg.result() == 1, dlg.result()
        path = Path(dlg.candidate_path)
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "First" in text or "Deep Segmentation" in text, text[:200]
        assert "PMID: 111" in text
    finally:
        os.environ.pop("HCCFIT_DATA_DIR", None)
        shutil.rmtree(data, ignore_errors=True)
        dlg.close()
    return "保存候选落盘并 accept()"


def main():
    check("内置引擎可用", case_engine_available)
    check("检索参数透传", case_search_passthrough)
    check("越界诊断报错", case_scope_error)
    check("后台线程：内置引擎", case_worker_builtin)
    check("后台线程：外部 HTTP", case_worker_http)
    check("对话框只有内置引擎", case_dialog)
    check("打开链接（选中行）", case_open_selected_row)
    check("打开链接（按钮点击）", case_button_click_opens_selected)
    check("打开链接（多选）", case_open_multi_selected)
    check("打开链接（勾选兜底）", case_open_checked_fallback)
    check("复制 DOI（多选）", case_copy_doi_multi)
    check("打开链接多级兜底", case_open_external_fallback)
    check("浏览器秒退检测", case_browser_exit_detected)
    check("默认浏览器命令行", case_build_cmdline)
    check("标题换行完整显示", case_titles_wrap)
    check("保存候选", case_save_candidates)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
