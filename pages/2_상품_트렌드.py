import io
import os
import re
from datetime import date, datetime, timedelta

import pandas as pd
import plotly.express as px
import requests
import streamlit as st

st.set_page_config(page_title="상품 트렌드 | WORLD PULSE", page_icon="🔥", layout="wide")

NAVER_SHOP_URL = "https://openapi.naver.com/v1/search/shop.json"
NAVER_TREND_URL = "https://openapi.naver.com/v1/datalab/shopping/category/keywords"
DEFAULT_KEYWORDS = ["남성 반팔티", "라이더 티셔츠", "바이크 액세서리", "남성 액세서리"]

st.markdown("""
<style>
  .stApp {background:#070b12;color:#edf4ff}
  [data-testid="stSidebar"] {background:#0b111b;border-right:1px solid #1d2a3a}
  [data-testid="stMetric"] {background:#0d1521;border:1px solid #1d2a3a;padding:13px;border-radius:9px}
  .product-card {background:#0d1521;border:1px solid #1d2a3a;border-radius:10px;padding:15px;margin:7px 0}
  .rank-up {color:#ff5c72;font-weight:700}.rank-down {color:#44a7ff}.tag {color:#8bbcff}
</style>
""", unsafe_allow_html=True)


def clean(value):
    return re.sub(r"<[^>]+>", "", str(value or "")).strip()


def key_of(name):
    return re.sub(r"[^0-9a-z가-힣]", "", clean(name).lower())


def naver_headers():
    client_id, secret = os.getenv("NAVER_CLIENT_ID", ""), os.getenv("NAVER_CLIENT_SECRET", "")
    try:
        client_id = st.secrets.get("NAVER_CLIENT_ID", client_id)
        secret = st.secrets.get("NAVER_CLIENT_SECRET", secret)
    except FileNotFoundError:
        pass
    return {"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": secret, "Content-Type": "application/json"}


def naver_ready():
    h = naver_headers()
    return bool(h["X-Naver-Client-Id"] and h["X-Naver-Client-Secret"])


@st.cache_data(ttl=300, show_spinner=False)
def search_naver(query, client_id, secret):
    r = requests.get(NAVER_SHOP_URL, headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": secret},
                     params={"query": query, "display": 30, "sort": "sim"}, timeout=20)
    r.raise_for_status()
    rows = []
    for rank, item in enumerate(r.json().get("items", []), 1):
        rows.append({"상품명": clean(item.get("title")), "브랜드": clean(item.get("brand") or item.get("maker")),
                     "플랫폼": "네이버쇼핑", "현재순위": rank, "이전순위": None, "판매수량": None,
                     "신상품": False, "위탁가능": False, "원가": None, "판매가": float(item.get("lprice") or 0),
                     "URL": item.get("link", ""), "검색어": query})
    return pd.DataFrame(rows)


@st.cache_data(ttl=1800, show_spinner=False)
def naver_trend(keyword, category, client_id, secret):
    payload = {"startDate": (date.today()-timedelta(days=28)).isoformat(), "endDate": date.today().isoformat(),
               "timeUnit": "date", "category": category,
               "keyword": [{"name": keyword[:20], "param": [keyword[:20]]}]}
    r = requests.post(NAVER_TREND_URL,
                      headers={"X-Naver-Client-Id": client_id, "X-Naver-Client-Secret": secret, "Content-Type": "application/json"},
                      json=payload, timeout=20)
    r.raise_for_status()
    results = r.json().get("results", [])
    return pd.DataFrame(results[0].get("data", [])) if results else pd.DataFrame(columns=["period", "ratio"])


def demo_data():
    names = ["빈티지 라이더 반팔티", "카본 바이크 키링", "오버핏 워크 셔츠", "메탈 체인 팔찌",
             "방수 라이딩 슬링백", "레트로 볼캡", "쿨링 이너웨어", "바이크 장갑", "와이드 카고팬츠"]
    platforms = ["네이버쇼핑", "쿠팡", "알리익스프레스", "신상마켓", "G마켓"]
    rows = []
    for i, name in enumerate(names):
        for j in range(2 if i < 6 else 1):
            current = i+j+1
            rows.append({"상품명": name, "브랜드": "DEMO", "플랫폼": platforms[(i+j)%5], "현재순위": current,
                         "이전순위": current+5 if i < 6 else current, "판매수량": 1240-i*83+j*20,
                         "신상품": i < 3, "위탁가능": True, "원가": 10000+i*1000,
                         "판매가": 27900+i*2200, "URL": "https://example.com", "검색증가율": [72,51,34,28,19,15,5,2,-3][i]})
    return pd.DataFrame(rows)


def normalize_upload(upload):
    df = pd.read_csv(upload)
    aliases = {"product_name":"상품명", "brand":"브랜드", "platform":"플랫폼", "rank":"현재순위",
               "previous_rank":"이전순위", "sold_count":"판매수량", "is_new":"신상품",
               "wholesale_available":"위탁가능", "cost_price":"원가", "sale_price":"판매가", "url":"URL"}
    df = df.rename(columns=aliases)
    for col in ["브랜드", "플랫폼", "이전순위", "판매수량", "신상품", "위탁가능", "원가", "판매가", "URL"]:
        if col not in df: df[col] = None
    if "상품명" not in df or "현재순위" not in df:
        raise ValueError("상품명(product_name)과 현재순위(rank) 열이 필요합니다.")
    for col in ["현재순위", "이전순위", "판매수량", "원가", "판매가"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["신상품", "위탁가능"]:
        df[col] = df[col].astype(str).str.lower().isin(["true","1","yes","y","가능"])
    return df


def analyze(df):
    x = df.copy()
    x["상품키"] = x["상품명"].map(key_of)
    platform_counts = x.groupby("상품키")["플랫폼"].nunique()
    x["교차플랫폼수"] = x["상품키"].map(platform_counts)
    x["순위상승률"] = ((x["이전순위"]-x["현재순위"])/x["이전순위"]*100).fillna(0).clip(lower=0)
    if "검색증가율" not in x: x["검색증가율"] = 0.0
    x["검색증가율"] = pd.to_numeric(x["검색증가율"], errors="coerce").fillna(0)
    margin = ((x["판매가"]-x["원가"])/x["판매가"]).replace([float("inf"),-float("inf")], 0).fillna(0)
    x["점수"] = ((x["교차플랫폼수"] >= 2)*25 + (x["현재순위"] < x["이전순위"])*25 +
                 x["신상품"].fillna(False).astype(bool)*15 + (x["검색증가율"] > 0)*15 +
                 x["위탁가능"].fillna(False).astype(bool)*10 + (margin >= .25)*10)
    x["분류"] = "스테디셀러"
    x.loc[(x["순위상승률"] >= 20) | (x["검색증가율"] >= 20), "분류"] = "급상승"
    x.loc[x["신상품"].fillna(False).astype(bool), "분류"] = "신규 진입"
    best = x.sort_values(["점수","판매수량"], ascending=False, na_position="last").drop_duplicates("상품키")
    graded = []
    for category, group in best.groupby("분류"):
        group = group.sort_values(["점수","판매수량"], ascending=False, na_position="last").copy()
        n = len(group)
        group["등급"] = ["S" if i/n < 1/3 else "A" if i/n < 2/3 else "B" for i in range(n)]
        graded.append(group)
    return pd.concat(graded, ignore_index=True) if graded else best


st.title("🔥 실시간 상품 트렌드")
st.caption(f"{datetime.now():%Y-%m-%d %H:%M} 기준 · 검색량과 판매량을 구분해 표시합니다")

with st.sidebar:
    st.markdown("## 상품 데이터")
    mode = st.radio("자료 선택", ["데모로 보기", "네이버 실시간", "판매자료 CSV"])
    keywords = st.text_area("찾을 상품 (한 줄에 하나)", "\n".join(DEFAULT_KEYWORDS))
    category_id = st.text_input("네이버 카테고리 ID", "50000000", help="기본값은 패션의류")
    uploaded = st.file_uploader("신상마켓·알리·쿠팡 CSV", type="csv")
    st.caption("실제 판매수량은 쇼핑몰에서 받은 자료가 있어야 표시됩니다.")

template = pd.DataFrame([{"product_name":"예시 반팔티","brand":"브랜드","platform":"신상마켓","rank":2,
                          "previous_rank":8,"sold_count":320,"is_new":True,"wholesale_available":True,
                          "cost_price":12000,"sale_price":29900,"url":"https://상품주소"}])
st.download_button("CSV 입력양식 받기", template.to_csv(index=False).encode("utf-8-sig"), "상품자료_입력양식.csv", "text/csv")

try:
    if mode == "데모로 보기":
        raw = demo_data()
    elif mode == "판매자료 CSV":
        if uploaded is None:
            st.info("왼쪽에서 CSV 파일을 올려주세요. 양식은 위 버튼으로 받을 수 있습니다.")
            st.stop()
        raw = normalize_upload(uploaded)
    else:
        if not naver_ready():
            st.warning("네이버 Client ID와 Secret을 Streamlit Secrets에 먼저 입력하세요.")
            st.code('NAVER_CLIENT_ID="발급값"\nNAVER_CLIENT_SECRET="발급값"', language="toml")
            st.stop()
        h = naver_headers()
        frames = [search_naver(k.strip(), h["X-Naver-Client-Id"], h["X-Naver-Client-Secret"])
                  for k in keywords.splitlines() if k.strip()][:5]
        raw = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        raw["검색증가율"] = 0.0
    result = analyze(raw)
except Exception as e:
    st.error(f"자료를 불러오지 못했습니다: {e}")
    st.stop()

sales_total = int(pd.to_numeric(raw.get("판매수량"), errors="coerce").fillna(0).sum())
c1,c2,c3,c4 = st.columns(4)
c1.metric("수집 상품", f"{raw['상품명'].nunique():,}개")
c2.metric("급상승", f"{(result['분류']=='급상승').sum():,}개")
c3.metric("2개 이상 플랫폼", f"{(result['교차플랫폼수']>=2).sum():,}개")
c4.metric("확인 판매수량", f"{sales_total:,}개" if sales_total else "자료 없음")

tab1,tab2,tab3,tab4 = st.tabs(["🔥 실시간 급상승", "🛒 많이 팔림", "🏆 TOP 9", "📈 네이버 검색 추이"])
with tab1:
    rising = result.sort_values(["순위상승률","검색증가율","점수"], ascending=False).head(20)
    st.dataframe(rising[["상품명","플랫폼","현재순위","이전순위","순위상승률","검색증가율","점수","URL"]],
                 hide_index=True, use_container_width=True, column_config={"URL":st.column_config.LinkColumn("상품")})
with tab2:
    sold = result.dropna(subset=["판매수량"]).sort_values("판매수량", ascending=False).head(20)
    if sold.empty: st.info("판매량 자료가 없습니다. 쇼핑몰 CSV를 올리면 표시됩니다.")
    else:
        fig = px.bar(sold.head(10), x="판매수량", y="상품명", orientation="h", color="플랫폼", color_discrete_sequence=px.colors.qualitative.Set2)
        fig.update_layout(height=430, paper_bgcolor="#0d1521", plot_bgcolor="#0d1521", font_color="#dbeafe", yaxis={"categoryorder":"total ascending"})
        st.plotly_chart(fig, use_container_width=True)
with tab3:
    picks=[]
    for cat in ["신규 진입","급상승","스테디셀러"]:
        for grade in ["S","A","B"]:
            q=result[(result["분류"]==cat)&(result["등급"]==grade)].sort_values("점수",ascending=False)
            if not q.empty: picks.append(q.iloc[0])
    top9=pd.DataFrame(picks)
    if top9.empty: st.info("선정할 자료가 부족합니다.")
    else: st.dataframe(top9[["상품명","분류","등급","점수","플랫폼","현재순위","검색증가율","판매수량","URL"]], hide_index=True, use_container_width=True,
                              column_config={"URL":st.column_config.LinkColumn("상품")})
with tab4:
    if not naver_ready():
        st.info("네이버 API를 연결하면 여기에 최근 28일 검색 클릭 추이가 표시됩니다.")
    else:
        chosen=st.selectbox("검색어", [x.strip() for x in keywords.splitlines() if x.strip()])
        h=naver_headers(); trend=naver_trend(chosen,category_id,h["X-Naver-Client-Id"],h["X-Naver-Client-Secret"])
        if trend.empty: st.warning("검색 추이 자료가 없습니다.")
        else:
            fig=px.line(trend,x="period",y="ratio",markers=True,labels={"period":"날짜","ratio":"검색 클릭 상대비율"})
            fig.update_traces(line_color="#ff5c72"); fig.update_layout(paper_bgcolor="#0d1521",plot_bgcolor="#0d1521",font_color="#dbeafe")
            st.plotly_chart(fig,use_container_width=True)

st.caption("주의: 네이버 검색결과 순서는 판매량 순위가 아닙니다. 실제 판매량은 각 쇼핑몰의 판매자료 CSV가 있어야 계산됩니다.")
