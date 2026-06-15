sv = open("demo_server.py", encoding="utf-8").read()

# --- 1) 특허 분기 교체 ---
old_pat = '''    connector = config["connector"]
    # 특허
    if "kipris" in connector:
        from data_sources import KiprisConnector
        ps = PatentDataSource(KiprisConnector())
    elif "uspto" in connector:
        ps = PatentDataSource(USPTOConnector())
    else:
        ps = PatentDataSource(MockPatentConnector({}))'''

new_pat = '''    connector = config["connector"]
    # API 스위치: True=직접호출(kipris/tavily), False=Agent raw 재사용
    import os as _os
    api_mode = config.get("api_direct")
    if api_mode is None:
        _ev = _os.environ.get("EVAL_API", "")
        api_mode = _ev.lower() in ("1", "true", "yes") if _ev else None
    # 특허
    if api_mode is True:
        from data_sources import KiprisConnector
        ps = PatentDataSource(KiprisConnector())
    elif api_mode is False:
        from data_sources import AgentPatentConnector
        ps = PatentDataSource(AgentPatentConnector(company, eval_year))
    elif "kipris" in connector:
        from data_sources import KiprisConnector
        ps = PatentDataSource(KiprisConnector())
    elif "uspto" in connector:
        ps = PatentDataSource(USPTOConnector())
    elif "agent" in connector:
        from data_sources import AgentPatentConnector
        ps = PatentDataSource(AgentPatentConnector(company, eval_year))
    else:
        ps = PatentDataSource(MockPatentConnector({}))'''

assert old_pat in sv, "특허 분기 못 찾음"
sv = sv.replace(old_pat, new_pat)

# --- 2) 시장 분기 교체 ---
old_mkt = '''    # 시장
    if "agent" in connector:
        from data_sources import AgentMarketConnector
        ms = MarketDataSource(AgentMarketConnector(company, eval_year))
    elif "tavily" in connector:
        ms = MarketDataSource(TavilyMarketConnector())'''

new_mkt = '''    # 시장
    if api_mode is True:
        ms = MarketDataSource(TavilyMarketConnector())
    elif api_mode is False:
        from data_sources import AgentMarketConnector
        ms = MarketDataSource(AgentMarketConnector(company, eval_year))
    elif "agent" in connector:
        from data_sources import AgentMarketConnector
        ms = MarketDataSource(AgentMarketConnector(company, eval_year))
    elif "tavily" in connector:
        ms = MarketDataSource(TavilyMarketConnector())'''

assert old_mkt in sv, "시장 분기 못 찾음"
sv = sv.replace(old_mkt, new_mkt)

# --- 3) config에 api_direct 키 ---
old_cfg = '"connector": os.environ.get("EVAL_CONNECTOR", "mock"),'
new_cfg = '''"connector": os.environ.get("EVAL_CONNECTOR", "mock"),
    "api_direct": ((os.environ.get("EVAL_API","").lower() in ("1","true","yes")) if os.environ.get("EVAL_API") else None),'''
if old_cfg in sv:
    sv = sv.replace(old_cfg, new_cfg)
    print("config api_direct 키 추가")
else:
    print("주의: config connector 라인 못 찾음 (환경변수로만 동작)")

open("demo_server.py", "w", encoding="utf-8").write(sv)
print("demo_server.py API 스위치 패치 완료")

import py_compile
try:
    py_compile.compile("demo_server.py", doraise=True)
    print("문법 OK")
except Exception as e:
    print("문법 에러:", e)
    import shutil; shutil.copy("demo_server.py.bak_4api", "demo_server.py")
    print("롤백함")
