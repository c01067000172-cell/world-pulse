import os
from datetime import date, timedelta, datetime

import pandas as pd
import requests
import streamlit as st
import altair as alt
from io import StringIO


st.set_page_config(page_title="실시간 상품 트렌드", page_icon="🔥", layout="wide")

API_BASE = "https://naverapihub.apigw.ntruss.com"
SHOPPING_KEYWORD_PATH = "/shopping/v1/category/keywords"


def get_secret(name: str) -> str:
    try:
        return str(st.secrets.get(name, ""))
    except Exception:
        return os.getenv(name, "")


def api_headers() -> dict:
    return {
        "X-NCP-APIGW-API-KEY-ID": get_secret("NAVER_CLIENT_ID"),
        "X-NCP-APIGW-API-KEY": get_secret("NAVER_CLIENT_SECRET"),
        "Content-Type": "application/json",
    }


@st.cache_data(ttl=21600, show_spinner=False)
def fetch_keyword_trends(category_ids: tuple[str, ...], keywords: tuple[str, ...]) -> pd.DataFrame:
    if not all(api_headers().get(k) for k in ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")):
        raise RuntimeError("Streamlit Secrets에 네이버 Client ID와 Client Secret을 먼저 저장해주세요.")

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=27)
    rows = []

    # 쇼핑인사이트 키워드 API는 한 요청에 최대 5개 그룹으로 나누어 호출합니다.
    for category_id in category_ids:
        for pos in range(0, len(keywords), 5):
            batch = keywords[pos : pos + 5]
            body = {
                "startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": "date",
                "category": category_id, "keyword": [{"name": k, "param": [k]} for k in batch],
            }
            response = requests.post(API_BASE + SHOPPING_KEYWORD_PATH, headers=api_headers(), json=body, timeout=20)
            if response.status_code != 200:
                raise RuntimeError(f"네이버 API 오류 ({response.status_code}): {response.text[:300]}")
            for result in response.json().get("results", []):
                points = result.get("data", [])
                values = [float(p.get("ratio", 0)) for p in points]
                if not values:
                    continue
                half = max(1, len(values) // 2)
                old_avg = sum(values[:half]) / half
                new_values = values[half:] or values[-1:]
                new_avg = sum(new_values) / len(new_values)
                growth = ((new_avg - old_avg) / old_avg * 100) if old_avg > 0 else (100.0 if new_avg > 0 else 0.0)
                rows.append({
                    "상품명": result.get("title") or result.get("keywords", ["키워드"])[0],
                    "이전지수": round(old_avg, 1), "최근지수": round(new_avg, 1),
                    "검색증감률": round(growth, 1), "추이": values, "카테고리ID": category_id,
                })
    if not rows:
        return pd.DataFrame()
    # 같은 검색어가 여러 카테고리에 있으면 최근지수가 가장 높은 결과만 사용합니다.
    return pd.DataFrame(rows).sort_values("최근지수", ascending=False).drop_duplicates("상품명")


def score_and_classify(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    ranked = df.copy().sort_values(["검색증감률", "최근지수"], ascending=False).reset_index(drop=True)
    ranked["이전순위"] = ranked["이전지수"].rank(method="min", ascending=False).astype(int)
    ranked["현재순위"] = ranked["최근지수"].rank(method="min", ascending=False).astype(int)
    ranked["순위변화"] = ranked["이전순위"] - ranked["현재순위"]

    # API로 검증 가능한 검색 상승분을 점수화하고, 확인되지 않은 판매량은 점수에 넣지 않습니다.
    ranked["점수"] = ranked.apply(
        lambda r: min(
            100,
            25
            + (25 if r["순위변화"] > 0 else 10)
            + (15 if r["검색증감률"] > 5 else 5)
            + (15 if r["이전지수"] == 0 and r["최근지수"] > 0 else 0)
            + (20 if r["최근지수"] >= ranked["최근지수"].median() else 10),
        ),
        axis=1,
    ).astype(int)
    ranked["카테고리"] = ranked.apply(
        lambda r: "신규 진입" if r["이전지수"] == 0 and r["최근지수"] > 0 else ("급상승" if r["검색증감률"] >= 10 else "스테디셀러"),
        axis=1,
    )
    ranked["등급"] = pd.qcut(ranked["점수"].rank(method="first", ascending=False), 3, labels=["S", "A", "B"]) if len(ranked) >= 3 else "S"
    ranked["플랫폼 순위"] = ranked.apply(lambda r: f"네이버 키워드: {r['이전순위']}위→{r['현재순위']}위", axis=1)
    ranked["네이버 검색 트렌드"] = ranked.apply(lambda r: f"{r['이전지수']}→{r['최근지수']} ({r['검색증감률']:+.1f}%)", axis=1)
    ranked["URL"] = ranked["상품명"].map(lambda x: f"https://search.shopping.naver.com/search/all?query={requests.utils.quote(str(x))}")
    return ranked


def demo_data() -> pd.DataFrame:
    names = ["경량 바람막이", "와이드 데님", "러닝 벨트", "미니 크로스백", "무선 보조배터리", "캠핑 랜턴", "기능성 티셔츠", "텀블러", "차량용 방향제"]
    growth = [82, 61, 45, 32, 24, 18, 9, 4, 2]
    return pd.DataFrame({"상품명": names, "이전지수": [22,25,31,35,42,45,58,71,76], "최근지수": [40,40.3,45,46.2,52.1,53.1,63.2,73.8,77.5], "검색증감률": growth, "추이": [[] for _ in names]})


PRODUCT_COLUMNS = [
    "검색어", "상품명", "플랫폼", "현재순위", "이전순위", "판매수량",
    "현재리뷰수", "이전리뷰수", "상품URL",
]


def product_csv_template() -> bytes:
    sample = pd.DataFrame(columns=PRODUCT_COLUMNS)
    return sample.to_csv(index=False, encoding="utf-8-sig").encode("utf-8-sig")


def load_product_evidence(uploaded_file) -> pd.DataFrame:
    try:
        df = pd.read_csv(uploaded_file, encoding="utf-8-sig")
    except UnicodeDecodeError:
        uploaded_file.seek(0)
        df = pd.read_csv(uploaded_file, encoding="cp949")
    missing = [c for c in PRODUCT_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("판매근거 CSV에 필요한 열이 없습니다: " + ", ".join(missing))
    for col in ["현재순위", "이전순위", "판매수량", "현재리뷰수", "이전리뷰수"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)
    for col in ["검색어", "상품명", "플랫폼", "상품URL"]:
        df[col] = df[col].fillna("").astype(str).str.strip()
    df = df[df["상품명"].ne("") & df["상품URL"].str.startswith(("http://", "https://"))].copy()
    return df


def select_real_products(products: pd.DataFrame, trends: pd.DataFrame) -> pd.DataFrame:
    if products.empty:
        return products
    trend_map = trends.set_index("상품명")["검색증감률"].to_dict() if not trends.empty else {}
    products = products.copy()
    products["검색증감률"] = products["검색어"].map(trend_map).fillna(0)
    products["리뷰증가"] = (products["현재리뷰수"] - products["이전리뷰수"]).clip(lower=0)
    products["순위상승"] = (products["이전순위"] - products["현재순위"]).clip(lower=0)
    products["플랫폼수"] = products.groupby("상품명")["플랫폼"].transform("nunique")

    # 확인 가능한 실제 근거가 하나도 없는 상품은 최종 추천에서 제외합니다.
    evidence = (products["판매수량"] > 0) | (products["현재순위"] > 0) | (products["현재리뷰수"] > 0)
    products = products[evidence].copy()
    if products.empty:
        return products

    def relative_score(series: pd.Series, maximum: int) -> pd.Series:
        top = float(series.max())
        return (series / top * maximum).round(1) if top > 0 else pd.Series(0.0, index=series.index)

    # 총 100점: 판매량 35 + 베스트순위 20 + 리뷰증가 15 + 다중플랫폼 15 + 검색상승 15
    products["판매점수"] = relative_score(products["판매수량"], 35)
    products["순위점수"] = products["현재순위"].apply(lambda x: max(0, 21 - x) if x > 0 else 0).clip(upper=20)
    products["리뷰점수"] = relative_score(products["리뷰증가"], 15)
    products["교차검증점수"] = products["플랫폼수"].apply(lambda x: 15 if x >= 2 else 5)
    products["검색점수"] = products["검색증감률"].apply(lambda x: 15 if x >= 30 else (10 if x >= 10 else (5 if x > 0 else 0)))
    products["총점"] = products[["판매점수", "순위점수", "리뷰점수", "교차검증점수", "검색점수"]].sum(axis=1).round(1)

    products["판매근거"] = products.apply(
        lambda r: " · ".join(filter(None, [
            f"판매 {int(r['판매수량']):,}개" if r["판매수량"] > 0 else "",
            f"베스트 {int(r['현재순위'])}위" if r["현재순위"] > 0 else "",
            f"리뷰 +{int(r['리뷰증가']):,}" if r["리뷰증가"] > 0 else "",
        ])), axis=1,
    )
    products["분류"] = products.apply(
        lambda r: "신규 진입" if r["이전순위"] == 0 and r["현재순위"] > 0 else ("급상승" if r["순위상승"] > 0 or r["검색증감률"] >= 10 else "스테디셀러"),
        axis=1,
    )
    products = products.sort_values(["총점", "판매수량", "현재리뷰수"], ascending=False)
    # 같은 검색어에서 가장 근거가 강한 실제 상품 1개만 선택합니다.
    products = products.drop_duplicates("검색어").head(9).reset_index(drop=True)
    products["등급"] = ["S" if i < 3 else ("A" if i < 6 else "B") for i in range(len(products))]
    return products



# =========================
# 상용 서비스형 UI
# =========================
import html

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Noto+Sans+KR:wght@400;500;600;700;800&display=swap');

:root{
  --brand:#03c75a;
  --text:#111827;
  --muted:#6b7280;
  --line:#e5e7eb;
  --soft:#f7f8fa;
  --card:#ffffff;
}

html, body, [class*="css"] {
  font-family: "Noto Sans KR", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.stApp { background:#fff; color:var(--text); }
.block-container { max-width:1180px; padding-top:1.1rem; padding-bottom:5.5rem; }

#MainMenu, footer { visibility:hidden; }
header[data-testid="stHeader"] { background:transparent; }

.brand-row{
  display:flex; align-items:center; justify-content:space-between;
  gap:16px; margin:4px 0 18px;
}
.brand{
  display:flex; align-items:center; gap:11px;
  font-weight:800; font-size:22px; letter-spacing:-.7px;
}
.brand-logo{
  width:34px; height:34px; border-radius:10px; background:var(--brand);
  color:#fff; display:flex; align-items:center; justify-content:center;
  font-size:20px; font-weight:900;
}
.brand-actions{display:flex; gap:8px; align-items:center;}
.top-chip{
  border:1px solid var(--line); border-radius:999px; padding:8px 13px;
  color:#374151; font-size:13px; background:#fff;
}
.pro-chip{background:#111827;color:#fff;border-color:#111827;}

.hero{
  border:1px solid var(--line); border-radius:24px;
  padding:28px 30px; margin-bottom:18px;
  background:linear-gradient(180deg,#ffffff 0%,#fbfffd 100%);
}
.hero-kicker{
  color:var(--brand); font-size:13px; font-weight:800; margin-bottom:7px;
}
.hero-title{
  font-size:32px; line-height:1.25; font-weight:800;
  letter-spacing:-1.2px; margin:0 0 10px;
}
.hero-desc{
  color:var(--muted); font-size:15px; line-height:1.7; margin:0;
}

.section-title{
  font-size:20px; font-weight:800; letter-spacing:-.6px; margin:28px 0 12px;
}
.section-sub{color:var(--muted);font-size:13px;margin-top:-6px;margin-bottom:14px;}

.card{
  background:#fff; border:1px solid var(--line); border-radius:18px;
  padding:20px; box-shadow:0 1px 2px rgba(0,0,0,.02);
}
.metric-grid{
  display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin:14px 0 8px;
}
.metric-card{
  border:1px solid var(--line); border-radius:16px; padding:17px 18px; background:#fff;
}
.metric-label{font-size:12px;color:var(--muted);margin-bottom:5px;}
.metric-value{font-size:24px;font-weight:800;letter-spacing:-.8px;}
.metric-note{font-size:12px;color:var(--brand);font-weight:700;margin-top:3px;}

.ad-box{
  border:1px solid var(--line); border-radius:18px; padding:18px 20px;
  min-height:92px; display:flex; align-items:center; justify-content:space-between;
  gap:18px; background:#fafafa; margin:12px 0 20px;
}
.ad-mark{font-size:10px;color:#9ca3af;border:1px solid #d1d5db;border-radius:999px;padding:2px 6px;}
.ad-title{font-size:17px;font-weight:800;margin:4px 0 2px;}
.ad-copy{font-size:13px;color:var(--muted);}
.ad-cta{white-space:nowrap;background:#111827;color:#fff;padding:10px 16px;border-radius:999px;font-weight:700;font-size:13px;}

.trend-list{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
.trend-card{border:1px solid var(--line);border-radius:16px;padding:17px;background:#fff;}
.rank{font-size:12px;color:#9ca3af;font-weight:700;}
.trend-name{font-size:17px;font-weight:800;margin:5px 0 8px;}
.trend-meta{font-size:12px;color:var(--muted);}
.up{color:#ef4444;font-weight:800;}
.score-badge{
  display:inline-block;margin-top:10px;background:#ecfdf3;color:#057a44;
  font-size:11px;font-weight:800;padding:5px 8px;border-radius:999px;
}

.product-grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;}
.product-card{
  border:1px solid var(--line); border-radius:18px; padding:18px;
  background:#fff; min-height:190px; display:flex; flex-direction:column;
}
.product-platform{font-size:11px;color:var(--muted);}
.product-name{font-size:17px;font-weight:800;line-height:1.45;margin:7px 0 9px;}
.product-evidence{font-size:12px;color:#4b5563;line-height:1.6;min-height:38px;}
.product-bottom{display:flex;align-items:center;justify-content:space-between;margin-top:auto;padding-top:14px;}
.product-score{font-size:20px;font-weight:900;}
.product-link{color:var(--brand)!important;text-decoration:none;font-weight:800;font-size:13px;}

.pro-box{
  border-radius:20px; background:#111827; color:white; padding:24px;
  margin:22px 0;
}
.pro-box h3{margin:0 0 8px;font-size:22px;}
.pro-box p{margin:0;color:#d1d5db;font-size:13px;line-height:1.65;}
.pro-features{display:flex;gap:8px;flex-wrap:wrap;margin-top:14px;}
.pro-feature{background:#262d38;border-radius:999px;padding:7px 10px;font-size:11px;}

.footer-note{
  border-top:1px solid var(--line); margin-top:32px; padding:20px 0 5px;
  color:#9ca3af; font-size:11px; line-height:1.7;
}

/* Streamlit 기본 입력 컴포넌트 */
div[data-testid="stTextArea"] textarea,
div[data-testid="stSelectbox"] > div > div,
div[data-testid="stMultiSelect"] > div > div {
  border-radius:14px!important;
}
.stButton > button, .stDownloadButton > button {
  border-radius:12px!important; min-height:44px; font-weight:800!important;
}
.stButton > button[kind="primary"] {
  background:var(--brand)!important; border-color:var(--brand)!important;
}
div[data-testid="stFileUploader"] {
  border:1px solid var(--line); border-radius:14px; padding:8px;
}
div[data-testid="stDataFrame"] {border:1px solid var(--line);border-radius:14px;overflow:hidden;}

.mobile-nav{display:none;}

@media (max-width: 900px){
  .block-container{padding:0.7rem 1rem 5.5rem;}
  .brand-actions .top-chip{display:none;}
  .hero{padding:22px 18px;border-radius:18px;}
  .hero-title{font-size:25px;}
  .metric-grid{grid-template-columns:repeat(2,1fr);}
  .trend-list,.product-grid{grid-template-columns:1fr;}
  .ad-box{align-items:flex-start;flex-direction:column;}
  .ad-cta{width:100%;text-align:center;}
  .mobile-nav{
    display:grid; grid-template-columns:repeat(5,1fr);
    position:fixed;left:0;right:0;bottom:0;z-index:9999;
    background:rgba(255,255,255,.97);border-top:1px solid var(--line);
    padding:8px 4px max(8px, env(safe-area-inset-bottom));
  }
  .mobile-nav div{text-align:center;font-size:10px;color:#6b7280;}
  .mobile-nav b{display:block;font-size:18px;line-height:1.2;color:#111827;margin-bottom:2px;}
}
</style>
""", unsafe_allow_html=True)

st.markdown("""
<div class="brand-row">
  <div class="brand"><div class="brand-logo">T</div>TrendPick</div>
  <div class="brand-actions">
    <span class="top-chip">광고문의</span>
    <span class="top-chip">로그인</span>
    <span class="top-chip pro-chip">PRO 시작하기</span>
  </div>
</div>
""", unsafe_allow_html=True)

st.markdown("""
<div class="hero">
  <div class="hero-kicker">AI SHOPPING INTELLIGENCE</div>
  <div class="hero-title">지금 검색만 뜨는 상품이 아니라<br>실제로 팔리는 상품을 찾습니다.</div>
  <p class="hero-desc">검색 상승 · 판매량 · 베스트 순위 · 리뷰 증가 · 다중 플랫폼 근거를 교차 검증해
  판매 가능성이 높은 상품을 빠르게 선별합니다.</p>
</div>
""", unsafe_allow_html=True)

# 상단 서비스 메뉴
nav = st.radio(
    "서비스 메뉴",
    ["🔥 실시간 트렌드", "🏆 실제 판매 TOP", "✨ AI 추천", "👕 가상피팅", "💎 PRO"],
    horizontal=True,
    label_visibility="collapsed",
)

st.markdown("""
<div class="ad-box">
  <div>
    <span class="ad-mark">AD</span>
    <div class="ad-title">브랜드·쇼핑몰 광고 영역</div>
    <div class="ad-copy">메인 화면 네이티브 광고 슬롯입니다. 향후 CPC·CPM·고정형 광고를 연결할 수 있습니다.</div>
  </div>
  <div class="ad-cta">광고 상품 보기</div>
</div>
""", unsafe_allow_html=True)

st.markdown('<div class="section-title">분석 설정</div>', unsafe_allow_html=True)
st.markdown('<div class="section-sub">사이드바 없이 메인 화면에서 바로 설정하고 분석합니다.</div>', unsafe_allow_html=True)

presets = {
    "남성·여성 전체": "여성 반팔티\n여성 블라우스\n여성 원피스\n여성 가디건\n여성 청바지\n여성 크로스백\n여성 운동화\n남성 반팔티\n남성 셔츠\n남성 바람막이\n남성 청바지\n남성 백팩\n남성 스니커즈\n라이더 티셔츠\n바이크 액세서리",
    "여성 인기상품": "여성 반팔티\n여성 블라우스\n여성 원피스\n여성 가디건\n여성 청바지\n여성 크로스백\n여성 운동화\n여성 샌들\n여성 자켓",
    "남성 인기상품": "남성 반팔티\n남성 셔츠\n남성 바람막이\n남성 청바지\n남성 백팩\n남성 스니커즈\n라이더 티셔츠\n카고 팬츠\n남성 자켓",
    "직접 입력": "",
}
category_map = {"패션의류": "50000000", "패션잡화": "50000001"}

c1, c2, c3 = st.columns([1, 1, 1.2])
with c1:
    mode = st.radio("데이터", ["데모로 보기", "네이버 실시간"], horizontal=True)
with c2:
    preset = st.selectbox("상품군", list(presets))
with c3:
    selected_categories = st.multiselect("조회 카테고리", list(category_map), default=list(category_map))

keywords_text = st.text_area(
    "비교할 상품·검색어",
    presets[preset],
    height=135,
    placeholder="한 줄에 하나씩 입력",
)
category_ids = tuple(category_map[x] for x in selected_categories)

b1, b2 = st.columns([1, 1])
with b1:
    run = st.button("지금 분석하기", type="primary", use_container_width=True)
with b2:
    st.download_button(
        "판매근거 CSV 양식",
        product_csv_template(),
        "상품_판매근거_입력양식.csv",
        "text/csv",
        use_container_width=True,
    )

sales_csv = st.file_uploader(
    "실제 판매근거 CSV",
    type=["csv"],
    help="상품별 판매수량·베스트순위·리뷰수 자료를 연결하면 구체적인 실제 판매 상품을 선별합니다.",
)

keywords = tuple(dict.fromkeys(k.strip() for k in keywords_text.splitlines() if k.strip()))[:25]

# 첫 접속부터 빈 화면처럼 보이지 않도록 데모는 즉시 표시.
# 실시간 모드만 '분석하기' 버튼을 눌렀을 때 API를 호출합니다.
try:
    if mode == "데모로 보기":
        raw = demo_data()
    elif run:
        if not keywords:
            st.warning("비교할 상품명을 한 개 이상 입력해주세요.")
            st.stop()
        if not category_ids:
            st.warning("조회 카테고리를 한 개 이상 선택해주세요.")
            st.stop()
        with st.spinner("네이버 쇼핑 검색 추이를 분석하는 중입니다..."):
            raw = fetch_keyword_trends(category_ids, keywords)
    else:
        raw = demo_data()

    result = score_and_classify(raw)
    if result.empty:
        st.warning("조회된 데이터가 없습니다.")
        st.stop()

    analyzed = len(result)
    rising = int((result["카테고리"] == "급상승").sum())
    positive = int((result["검색증감률"] > 0).sum())
    max_growth = float(result["검색증감률"].max())

    st.markdown(f"""
    <div class="metric-grid">
      <div class="metric-card"><div class="metric-label">분석 상품</div><div class="metric-value">{analyzed}개</div><div class="metric-note">실시간 후보군</div></div>
      <div class="metric-card"><div class="metric-label">급상승</div><div class="metric-value">{rising}개</div><div class="metric-note">우선 확인 대상</div></div>
      <div class="metric-card"><div class="metric-label">검색 상승</div><div class="metric-value">{positive}개</div><div class="metric-note">상승 흐름 감지</div></div>
      <div class="metric-card"><div class="metric-label">최고 상승률</div><div class="metric-value">{max_growth:.1f}%</div><div class="metric-note">현재 최고치</div></div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown('<div class="section-title">지금 뜨는 검색어</div>', unsafe_allow_html=True)
    st.markdown('<div class="section-sub">상승률과 검색지수를 기준으로 빠르게 확인합니다.</div>', unsafe_allow_html=True)

    top_trends = result.sort_values(["검색증감률", "최근지수"], ascending=False).head(6)
    cards = ['<div class="trend-list">']
    for idx, row in top_trends.reset_index(drop=True).iterrows():
        name = html.escape(str(row["상품명"]))
        category = html.escape(str(row["카테고리"]))
        cards.append(f"""
        <div class="trend-card">
          <div class="rank">TOP {idx+1}</div>
          <div class="trend-name">{name}</div>
          <div class="trend-meta">{category} · 현재지수 {row["최근지수"]:.1f}</div>
          <div class="up">▲ {row["검색증감률"]:+.1f}%</div>
          <span class="score-badge">AI 점수 {int(row["점수"])}점</span>
        </div>
        """)
    cards.append("</div>")
    st.markdown("".join(cards), unsafe_allow_html=True)

    st.markdown('<div class="section-title">검색 상승 추이</div>', unsafe_allow_html=True)
    chart_data = result[["상품명", "검색증감률"]].sort_values("검색증감률", ascending=False).head(12)
    chart = alt.Chart(chart_data).mark_bar(cornerRadiusEnd=6).encode(
        x=alt.X("검색증감률:Q", title="검색 증감률 (%)"),
        y=alt.Y("상품명:N", sort="-x", title=None, axis=alt.Axis(labelLimit=150, labelAngle=0)),
        color=alt.condition(
            alt.datum.검색증감률 >= 0,
            alt.value("#03c75a"),
            alt.value("#9ca3af"),
        ),
        tooltip=["상품명", alt.Tooltip("검색증감률:Q", format=".1f")],
    ).properties(height=max(280, len(chart_data) * 32))
    st.altair_chart(chart, use_container_width=True)

    selected = pd.DataFrame()
    if sales_csv is not None:
        try:
            sales = load_product_evidence(sales_csv)
            selected = select_real_products(sales, result)
        except Exception as csv_exc:
            st.warning(f"판매자료 CSV를 읽지 못했습니다: {csv_exc}")

    st.markdown('<div class="section-title">실제로 팔리는 상품</div>', unsafe_allow_html=True)
    if selected.empty:
        st.markdown("""
        <div class="card">
          <b>아직 실제 판매근거가 연결되지 않았습니다.</b><br>
          <span style="color:#6b7280;font-size:13px;">
          검색 상승만으로 특정 상품을 베스트라고 단정하지 않습니다.
          판매수량·베스트 순위·리뷰 증가 자료를 연결하면 각 검색어에서 근거가 가장 강한 상품을 선별합니다.
          </span>
        </div>
        """, unsafe_allow_html=True)
    else:
        phtml = ['<div class="product-grid">']
        for _, row in selected.iterrows():
            name = html.escape(str(row["상품명"]))
            platform = html.escape(str(row["플랫폼"]))
            evidence = html.escape(str(row["판매근거"]))
            cls = html.escape(str(row["분류"]))
            url = html.escape(str(row["상품URL"]), quote=True)
            phtml.append(f"""
            <div class="product-card">
              <div class="product-platform">{platform} · {cls}</div>
              <div class="product-name">{name}</div>
              <div class="product-evidence">{evidence}</div>
              <div class="product-bottom">
                <div><span style="font-size:11px;color:#9ca3af;">판매 신뢰점수</span><br><span class="product-score">{row["총점"]:.0f}</span></div>
                <a class="product-link" href="{url}" target="_blank">상품 보기 →</a>
              </div>
            </div>
            """)
        phtml.append("</div>")
        st.markdown("".join(phtml), unsafe_allow_html=True)

    st.markdown("""
    <div class="pro-box">
      <h3>TrendPick PRO</h3>
      <p>무료 사용자는 핵심 트렌드를 확인하고, PRO에서는 더 많은 순위·판매근거·AI 예측·알림 기능을 제공하는 구조로 확장합니다.</p>
      <div class="pro-features">
        <span class="pro-feature">TOP 100 전체보기</span>
        <span class="pro-feature">판매근거 상세</span>
        <span class="pro-feature">AI 상품 예측</span>
        <span class="pro-feature">급상승 알림</span>
        <span class="pro-feature">가상피팅 연동</span>
      </div>
    </div>
    """, unsafe_allow_html=True)

    st.markdown("""
    <div class="footer-note">
      연결 대상: 신상마켓 · 네이버쇼핑 · 무신사 · 29CM · 에이블리 · 지그재그 · W컨셉 · 브랜디 · 하이버 · 쿠팡<br>
      네이버 쇼핑인사이트는 실제 판매수량이 아닌 검색·클릭 상대지수입니다. 실제 판매 상품 선정은 별도 판매근거 데이터로 검증합니다.
    </div>
    <div class="mobile-nav">
      <div><b>⌂</b>홈</div>
      <div><b>↗</b>트렌드</div>
      <div><b>★</b>베스트</div>
      <div><b>AI</b>추천</div>
      <div><b>☺</b>MY</div>
    </div>
    """, unsafe_allow_html=True)

except Exception as exc:
    st.error(str(exc))
    st.info("API HUB 쇼핑인사이트 권한, Streamlit Secrets, 카테고리 ID를 확인해주세요.")
