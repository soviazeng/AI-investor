# -*- coding: utf-8 -*-
"""
Research Thesis Tracker V0.2
Run:
  pip install -r requirements.txt
  streamlit run app.py

Optional LLM:
  Set OPENAI_API_KEY in environment, or enter it in the sidebar.
  This demo uses an OpenAI-compatible Chat Completions endpoint.
Data feeds:
  Upload CSV with columns: date, indicator, value, source (source optional).
  Without uploaded data, built-in sample observations are used.
"""
import os, json, re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import streamlit as st

st.set_page_config(page_title="投研假设动态跟踪 V0.2", page_icon="📈", layout="wide")
DB = Path("thesis_tracker_v2.json")

SAMPLE_REPORT = """研究观点：未来一个季度碳酸锂价格中枢偏强。
核心逻辑：供给端复产进度偏慢，新增产能释放不及预期；动力与储能电池需求保持韧性；产业链库存有望逐步去化。
关键假设：
1. 供给端增量低于预期。监控指标：周度碳酸锂产量，单位万吨；若低于8.3则支持该假设，超过8.3则需要重新评估。
2. 下游需求保持韧性。监控指标：下游排产指数，单位点；若不低于100则支持该假设。
3. 库存进入去化阶段。监控指标：产业链库存，单位万吨；若低于11.8则支持该假设。
4. 新增产能释放不出现超预期。监控指标：新增产能爬坡率，单位%；若不高于70则支持该假设。
证伪条件：库存持续上升、供给快速释放、下游排产明显下滑。
"""

SEED = {
 "thesis":{"title":"未来一个季度碳酸锂价格中枢偏强","asset":"碳酸锂","direction":"看多","horizon":"未来3个月",
           "summary":"供给端扰动叠加需求韧性，库存有望逐步去化。","owner":"研究员A","conclusion":"尚未复核"},
 "assumptions":[
  {"id":1,"name":"供给端增量低于预期","description":"主要产区复产进度偏慢，新增产能释放不及预期。",
   "indicator":"周度碳酸锂产量","unit":"万吨","operator":"<=","threshold":8.3,"weight":35,"latest":8.1,"status":"支持","evidence":"样本产量低于预期","updated_at":date.today().isoformat(),"falsifier":"产量持续高于8.3万吨"},
  {"id":2,"name":"下游需求保持韧性","description":"动力与储能电池排产保持稳定。","indicator":"下游排产指数","unit":"点","operator":">=","threshold":100,"weight":30,"latest":106,"status":"支持","evidence":"样本排产指数保持强势","updated_at":date.today().isoformat(),"falsifier":"排产指数跌破100"},
  {"id":3,"name":"库存进入去化阶段","description":"产业链总库存连续下降。","indicator":"产业链库存","unit":"万吨","operator":"<=","threshold":11.8,"weight":25,"latest":12.4,"status":"反证","evidence":"库存仍处于较高水平","updated_at":date.today().isoformat(),"falsifier":"库存连续上升"},
  {"id":4,"name":"新增产能释放不超预期","description":"新增产能爬坡速度可控。","indicator":"新增产能爬坡率","unit":"%","operator":"<=","threshold":70,"weight":10,"latest":55,"status":"支持","evidence":"爬坡进度暂未超预期","updated_at":date.today().isoformat(),"falsifier":"爬坡率超过70%"}
 ],
 "observations":[
  {"date":date.today().isoformat(),"indicator":"周度碳酸锂产量","value":8.1,"source":"内置模拟数据"},
  {"date":date.today().isoformat(),"indicator":"下游排产指数","value":106,"source":"内置模拟数据"},
  {"date":date.today().isoformat(),"indicator":"产业链库存","value":12.4,"source":"内置模拟数据"},
  {"date":date.today().isoformat(),"indicator":"新增产能爬坡率","value":55,"source":"内置模拟数据"}
 ],
 "events":[]
}

def load_db():
    if DB.exists():
        try: return json.loads(DB.read_text(encoding="utf-8"))
        except Exception: pass
    return json.loads(json.dumps(SEED, ensure_ascii=False))

def save_db():
    DB.write_text(json.dumps(st.session_state.db, ensure_ascii=False, indent=2), encoding="utf-8")

def read_uploaded(file):
    name = file.name.lower()
    raw = file.getvalue()
    if name.endswith(".txt") or name.endswith(".md"):
        return raw.decode("utf-8", errors="ignore")
    if name.endswith(".pdf"):
        try:
            import fitz
            doc = fitz.open(stream=raw, filetype="pdf")
            return "\n".join(p.get_text() for p in doc)
        except Exception as e:
            raise ValueError(f"PDF解析失败：{e}")
    if name.endswith(".docx"):
        try:
            from docx import Document
            import io
            doc = Document(io.BytesIO(raw))
            return "\n".join(p.text for p in doc.paragraphs)
        except Exception as e:
            raise ValueError(f"DOCX解析失败：{e}")
    raise ValueError("当前支持 TXT、MD、PDF、DOCX 文件。")

def call_llm(text, api_key, base_url, model):
    from openai import OpenAI
    client = OpenAI(api_key=api_key, base_url=base_url or None)
    prompt = """你是资深买方研究助理。请从研究材料中抽取结构化研究观点和可验证假设。
只输出严格JSON，不要Markdown。格式：
{
 "thesis":{"title":"","asset":"","direction":"看多/看空/中性","horizon":"","summary":"","falsification_conditions":[]},
 "assumptions":[
  {"name":"","description":"","indicator":"","unit":"","operator":">=或<=","threshold":0,
   "weight":25,"falsifier":"","evidence_keywords":[]}
 ]
}
规则：不要编造材料未提供的阈值；没有阈值时 threshold=null、operator=""，并在description注明待研究员补充。权重合计尽量100。假设应可监控、可证伪。"""
    resp = client.chat.completions.create(
        model=model,
        messages=[{"role":"system","content":prompt},{"role":"user","content":text}],
        temperature=0.1,
        response_format={"type":"json_object"}
    )
    content = resp.choices[0].message.content
    return json.loads(content)

def fallback_extract(text):
    """无API key时的可运行演示解析器：基于样例/文本模式抽取，不等同真实LLM。"""
    title_match = re.search(r"(?:研究观点|核心观点|观点标题)[:：]\s*(.+)", text)
    title = title_match.group(1).strip() if title_match else "待命名研究观点"
    asset = "碳酸锂" if "碳酸锂" in text else "待识别标的"
    direction = "看多" if any(x in text for x in ["偏强","看多","上涨","上行"]) else ("看空" if any(x in text for x in ["看空","偏弱","下跌","下行"]) else "中性")
    horizon_match = re.search(r"(未来[^。\n，,；;]{1,15})", text)
    horizon = horizon_match.group(1) if horizon_match else "待补充"
    patterns = [
      ("供给端增量低于预期","周度碳酸锂产量","万吨",8.3,"<=","产量持续高于阈值"),
      ("下游需求保持韧性","下游排产指数","点",100,">=","排产指数跌破阈值"),
      ("库存进入去化阶段","产业链库存","万吨",11.8,"<=","库存持续上升"),
      ("新增产能释放不超预期","新增产能爬坡率","%",70,"<=","爬坡率超过阈值")
    ]
    found=[]
    for name, ind, unit, threshold, op, falsifier in patterns:
        if ind in text or name in text:
            found.append({"name":name,"description":name,"indicator":ind,"unit":unit,
                          "operator":op,"threshold":threshold,"weight":round(100/max(1,4)),
                          "falsifier":falsifier,"evidence_keywords":[ind]})
    if not found:
        for line in text.splitlines():
            line=line.strip().lstrip("0123456789.、- ")
            if len(line)>8 and any(k in line for k in ["假设","预计","判断","如果","若"]):
                found.append({"name":line[:35],"description":line,"indicator":"","unit":"",
                              "operator":"","threshold":None,"weight":0,
                              "falsifier":"待研究员补充证伪条件","evidence_keywords":[]})
    return {"thesis":{"title":title,"asset":asset,"direction":direction,"horizon":horizon,
                      "summary":text[:500],"falsification_conditions":[]},
            "assumptions":found}

def evaluate(item, value):
    if item.get("threshold") is None or item.get("operator") not in [">=","<="]:
        return "待验证"
    ok = value >= float(item["threshold"]) if item["operator"] == ">=" else value <= float(item["threshold"])
    return "支持" if ok else "反证"

def map_observations():
    obs = st.session_state.db["observations"]
    result=[]
    for a in st.session_state.db["assumptions"]:
        matches=[o for o in obs if o["indicator"].strip().lower()==a["indicator"].strip().lower()]
        if matches:
            latest=max(matches,key=lambda x:x["date"])
            result.append((a,latest))
    return result

def weighted_status():
    aa=st.session_state.db["assumptions"]
    total=sum(float(a.get("weight",0)) for a in aa) or 1
    vals={s:sum(float(a.get("weight",0)) for a in aa if a.get("status")==s)/total*100 for s in ["支持","反证","待验证"]}
    return vals

def make_report():
    db=st.session_state.db; t=db["thesis"]; vals=weighted_status()
    lines=[f"# {t['title']}", "", f"- 标的：{t['asset']}  - 方向：{t['direction']}  - 周期：{t['horizon']}",
           f"- 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')}", "", "## 核心逻辑", t["summary"], "",
           "## 假设跟踪状态", f"- 支持权重：{vals['支持']:.1f}%",
           f"- 反证权重：{vals['反证']:.1f}%", f"- 待验证权重：{vals['待验证']:.1f}%", ""]
    for a in db["assumptions"]:
        lines += [f"### {a['name']}", f"- 描述：{a.get('description','')}",
                  f"- 监控指标：{a.get('indicator','')}；阈值：{a.get('operator','')} {a.get('threshold','待补充')} {a.get('unit','')}",
                  f"- 最新值：{a.get('latest','暂无')}；状态：**{a.get('status','待验证')}**；权重：{a.get('weight',0)}%",
                  f"- 证据：{a.get('evidence','暂无')}", f"- 证伪条件：{a.get('falsifier','待补充')}", ""]
    lines += ["## 研究员最近复核", t.get("conclusion","尚未复核"), "", "## 重要提示",
              "本报告由Demo规则生成，数据可能为模拟或用户上传数据，不构成投资建议。"]
    return "\n".join(lines)

if "db" not in st.session_state: st.session_state.db=load_db()
db=st.session_state.db
st.title("📈 投研假设与研究结论动态跟踪系统")
st.caption("V0.2 · 研究材料解析 / 假设提取 / 指标映射 / 数据监控 / 证伪预警 / 报告生成")
page=st.sidebar.radio("导航",["研究总览","AI解析研究材料","假设与指标映射","数据接入与监控","证伪预警与结论复核","研究报告"])
st.sidebar.divider()
st.sidebar.subheader("可选大模型配置")
api_key=st.sidebar.text_input("OpenAI API Key",value=os.getenv("OPENAI_API_KEY",""),type="password")
base_url=st.sidebar.text_input("API Base URL（可选）",value=os.getenv("OPENAI_BASE_URL",""))
model=st.sidebar.text_input("模型名称",value=os.getenv("OPENAI_MODEL","gpt-4o-mini"))
st.sidebar.caption("未配置Key时，使用内置规则解析器和模拟数据。")

vals=weighted_status()
if page=="研究总览":
    t=db["thesis"]
    c=st.columns(4)
    c[0].metric("研究标的",t["asset"]); c[1].metric("研究方向",t["direction"])
    c[2].metric("假设数量",len(db["assumptions"])); c[3].metric("证据记录",len(db["observations"]))
    st.subheader(t["title"]); st.write(t["summary"])
    m=st.columns(3)
    m[0].metric("支持权重",f"{vals['支持']:.1f}%")
    m[1].metric("反证权重",f"{vals['反证']:.1f}%")
    m[2].metric("待验证权重",f"{vals['待验证']:.1f}%")
    st.bar_chart(pd.DataFrame({"权重占比":vals}).T)
    if vals["反证"]>=30: st.error("反证权重较高：建议重新评估核心逻辑。")
    elif vals["待验证"]>=40: st.warning("关键假设仍有较多待验证项，结论置信度有限。")
    else: st.success("当前证据倾向支持原观点；请结合数据质量和研究员判断。")
    st.subheader("假设状态")
    st.dataframe(pd.DataFrame(db["assumptions"])[["name","indicator","latest","operator","threshold","weight","status","updated_at"]],use_container_width=True,hide_index=True)
    st.subheader("指标历史")
    if db["observations"]:
        hist=pd.DataFrame(db["observations"])
        hist["date"]=pd.to_datetime(hist["date"])
        pivot=hist.pivot_table(index="date",columns="indicator",values="value",aggfunc="last")
        st.line_chart(pivot)
    else: st.info("暂无指标数据。")

elif page=="AI解析研究材料":
    st.subheader("上传研究报告，自动提取观点与可验证假设")
    uploaded=st.file_uploader("上传 TXT / MD / PDF / DOCX",type=["txt","md","pdf","docx"])
    pasted=st.text_area("或直接粘贴研究报告内容",value=SAMPLE_REPORT,height=230)
    if uploaded:
        try: source_text=read_uploaded(uploaded)
        except Exception as e: st.error(str(e)); source_text=pasted
    else: source_text=pasted
    st.caption(f"当前材料长度：{len(source_text)} 字符")
    if st.button("解析研究材料",type="primary"):
        if not source_text.strip(): st.warning("请先上传或粘贴研究材料。")
        else:
            try:
                if api_key:
                    with st.spinner("正在调用大模型解析……"):
                        parsed=call_llm(source_text,api_key,base_url.strip() or None,model)
                    st.session_state.parse_mode="LLM"
                else:
                    parsed=fallback_extract(source_text)
                    st.session_state.parse_mode="规则演示"
                st.session_state.parsed=parsed
                st.session_state.source_text=source_text
            except Exception as e:
                st.error(f"大模型解析失败：{e}。已切换到本地规则演示解析。")
                st.session_state.parsed=fallback_extract(source_text)
                st.session_state.parse_mode="规则演示"
    if "parsed" in st.session_state:
        parsed=st.session_state.parsed
        st.success(f"解析完成 · 模式：{st.session_state.get('parse_mode','未知')}")
        st.markdown("**抽取的研究观点**")
        st.json(parsed.get("thesis",{}))
        st.markdown("**抽取的研究假设**")
        st.dataframe(pd.DataFrame(parsed.get("assumptions",[])),use_container_width=True,hide_index=True)
        st.warning("请研究员核验指标口径、单位、阈值和权重。模型抽取结果不能直接视为事实。")
        if st.button("确认并导入为当前研究"):
            th=parsed.get("thesis",{})
            db["thesis"].update({k:v for k,v in th.items() if k in ["title","asset","direction","horizon","summary"] and v})
            imported=[]
            for i,a in enumerate(parsed.get("assumptions",[]),1):
                imported.append({"id":max([x["id"] for x in db["assumptions"]],default=0)+i,
                    "name":a.get("name","新假设"),"description":a.get("description",""),
                    "indicator":a.get("indicator",""),"unit":a.get("unit",""),
                    "operator":a.get("operator",""),"threshold":a.get("threshold"),
                    "weight":a.get("weight",0),"latest":None,"status":"待验证",
                    "evidence":"由研究材料解析导入，等待指标数据","updated_at":date.today().isoformat(),
                    "falsifier":a.get("falsifier","待补充")})
            if imported: db["assumptions"]=imported
            db["events"].append({"time":datetime.now().isoformat(timespec="minutes"),"event":"导入研究材料解析结果","count":len(imported)})
            save_db(); st.success("已导入当前研究。"); st.rerun()

elif page=="假设与指标映射":
    st.subheader("研究假设—指标映射")
    st.caption("此处建立假设与可观测指标之间的映射。一个假设可在后续版本映射多个指标。")
    for a in db["assumptions"]:
        with st.expander(a["name"],expanded=True):
            x,y,z=st.columns([2,1,1])
            with x: indicator=st.text_input("监控指标名称",a.get("indicator",""),key=f"ind{a['id']}")
            with y: unit=st.text_input("单位",a.get("unit",""),key=f"unit{a['id']}")
            with z: weight=st.number_input("权重%",0,100,int(a.get("weight",0)),key=f"w{a['id']}")
            p,q=st.columns(2)
            with p: op=st.selectbox("判断条件",["",">=","<="],index=["",">=","<="].index(a.get("operator","") if a.get("operator","") in ["",">=","<="] else ""),key=f"op{a['id']}")
            with q:
                thr_default=float(a["threshold"]) if a.get("threshold") is not None else 0.0
                threshold=st.number_input("阈值（未确定可先填0）",value=thr_default,key=f"th{a['id']}")
            desc=st.text_area("假设描述",a.get("description",""),key=f"desc{a['id']}")
            falsifier=st.text_input("证伪条件",a.get("falsifier",""),key=f"fals{a['id']}")
            if st.button("保存映射",key=f"save{a['id']}"):
                a.update({"indicator":indicator,"unit":unit,"weight":weight,"operator":op,
                          "threshold":threshold if op else None,"description":desc,"falsifier":falsifier})
                a["status"]="待验证"; save_db(); st.success("映射已保存。")
    st.divider()
    st.subheader("指标映射完整性检查")
    missing=[a["name"] for a in db["assumptions"] if not a.get("indicator") or not a.get("operator") or a.get("threshold") is None]
    if missing: st.warning("以下假设尚未配置完整指标/阈值："+"、".join(missing))
    else: st.success("所有假设均已配置监控指标和阈值。")

elif page=="数据接入与监控":
    st.subheader("导入指标数据")
    st.markdown("CSV格式要求：`date,indicator,value,source`。例如：2026-11-17,产业链库存,11.6,行业数据库。")
    template=pd.DataFrame([{"date":date.today().isoformat(),"indicator":a.get("indicator",""),"value":a.get("latest") or 0,"source":"手工/数据源"} for a in db["assumptions"] if a.get("indicator")])
    st.download_button("下载CSV模板",template.to_csv(index=False).encode("utf-8-sig"),"indicator_template.csv","text/csv")
    csvfile=st.file_uploader("上传指标CSV",type=["csv"])
    if csvfile and st.button("导入CSV数据"):
        try:
            frame=pd.read_csv(csvfile)
            required={"date","indicator","value"}
            if not required.issubset(frame.columns): st.error("缺少必需列：date、indicator、value")
            else:
                for _,r in frame.iterrows():
                    db["observations"].append({"date":str(r["date"])[:10],"indicator":str(r["indicator"]).strip(),
                        "value":float(r["value"]),"source":str(r["source"]) if "source" in frame.columns else "CSV导入"})
                save_db(); st.success(f"成功导入 {len(frame)} 条记录。"); st.rerun()
        except Exception as e: st.error(f"CSV导入失败：{e}")
    st.divider()
    st.subheader("执行监控")
    if st.button("匹配最新指标并更新假设状态",type="primary"):
        changed=0
        for a,o in map_observations():
            old=a.get("status")
            a["latest"]=float(o["value"]); a["updated_at"]=o["date"]
            a["status"]=evaluate(a,float(o["value"]))
            a["evidence"]=f"{o['source']}；观测日期 {o['date']}；指标值 {o['value']} {a.get('unit','')}"
            if old!=a["status"]:
                db["events"].append({"time":datetime.now().isoformat(timespec="minutes"),"event":"状态变化",
                    "assumption":a["name"],"from":old,"to":a["status"],"value":o["value"]})
            changed+=1
        save_db(); st.success(f"已更新 {changed} 项假设。"); st.rerun()
    if db["observations"]:
        st.dataframe(pd.DataFrame(db["observations"]).sort_values("date",ascending=False),use_container_width=True,hide_index=True)
    else: st.info("请先导入指标CSV。")

elif page=="证伪预警与结论复核":
    st.subheader("证伪预警")
    if db["events"]:
        st.dataframe(pd.DataFrame(db["events"][-30:][::-1]),use_container_width=True,hide_index=True)
    else: st.info("暂无状态变化事件。")
    for a in db["assumptions"]:
        if a.get("status")=="反证":
            st.error(f"反证信号：{a['name']}｜{a.get('indicator')}={a.get('latest')} {a.get('unit','')}；条件 {a.get('operator')} {a.get('threshold')}。证伪条件：{a.get('falsifier','待补充')}")
        elif a.get("status")=="待验证":
            st.warning(f"待验证：{a['name']}｜请补充指标映射或最新观测。")
    st.divider()
    st.subheader("研究员复核")
    with st.form("review"):
        decision=st.selectbox("复核结论",["维持原观点","增强观点","下调观点强度","观点反转","继续观察"])
        rationale=st.text_area("复核理由",height=120)
        reviewer=st.text_input("复核人",db["thesis"].get("owner",""))
        if st.form_submit_button("保存复核"):
            db["thesis"]["conclusion"]=f"{decision}：{rationale}"
            db["events"].append({"time":datetime.now().isoformat(timespec="minutes"),"event":"研究结论复核",
                                 "decision":decision,"reviewer":reviewer,"rationale":rationale})
            save_db(); st.success("复核记录已保存。")

elif page=="研究报告":
    st.subheader("动态研究跟踪报告")
    report=make_report()
    st.markdown(report)
    st.download_button("下载 Markdown 研究报告",report.encode("utf-8"),"research_tracking_report.md","text/markdown")
    st.download_button("导出全部数据 JSON",json.dumps(db,ensure_ascii=False,indent=2).encode("utf-8"),
                       "thesis_tracker_data.json","application/json")

st.sidebar.divider()
if st.sidebar.button("恢复内置示例数据"):
    st.session_state.db=json.loads(json.dumps(SEED,ensure_ascii=False)); save_db(); st.rerun()
