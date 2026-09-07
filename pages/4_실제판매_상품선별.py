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


st.title("🔥 실시간 상품 트렌드")
st.caption(f"{datetime.now():%Y-%m-%d %H:%M} 기준 · 네이버 API HUB 쇼핑인사이트 연동")

with st.sidebar:
    st.header("상품 데이터")
    mode = st.radio("자료 선택", ["네이버 실시간", "데모로 보기"])
    preset = st.selectbox("상품군", ["남성·여성 전체", "여성 인기상품", "남성 인기상품", "직접 입력"])
    presets = {
        "남성·여성 전체": "여성 반팔티\n여성 블라우스\n여성 원피스\n여성 가디건\n여성 청바지\n여성 크로스백\n여성 운동화\n남성 반팔티\n남성 셔츠\n남성 바람막이\n남성 청바지\n남성 백팩\n남성 스니커즈\n라이더 티셔츠\n바이크 액세서리",
        "여성 인기상품": "여성 반팔티\n여성 블라우스\n여성 원피스\n여성 가디건\n여성 청바지\n여성 크로스백\n여성 운동화\n여성 샌들\n여성 자켓",
        "남성 인기상품": "남성 반팔티\n남성 셔츠\n남성 바람막이\n남성 청바지\n남성 백팩\n남성 스니커즈\n라이더 티셔츠\n카고 팬츠\n남성 자켓",
        "직접 입력": "",
    }
    keywords_text = st.text_area(
        "비교할 상품·검색어 (한 줄에 하나)",
        presets[preset],
        height=210,
    )
    category_map = {"패션의류": "50000000", "패션잡화": "50000001"}
    selected_categories = st.multiselect("조회 카테고리", list(category_map), default=list(category_map))
    category_ids = tuple(category_map[x] for x in selected_categories)
    st.download_button("판매근거 CSV 양식 받기", product_csv_template(), "상품_판매근거_입력양식.csv", "text/csv", use_container_width=True)
    sales_csv = st.file_uploader("쇼핑몰 상품별 판매근거 CSV", type=["csv"], help="검색 결과 상품의 판매수량·베스트순위·리뷰수를 입력한 자료입니다.")
    run = st.button("지금 분석하기", type="primary", use_container_width=True)
    st.caption("API 키는 Streamlit Secrets에서만 읽으며 화면이나 코드에 표시하지 않습니다.")

keywords = tuple(dict.fromkeys(k.strip() for k in keywords_text.splitlines() if k.strip()))[:25]

try:
    if mode == "데모로 보기":
        raw = demo_data()
    elif run:
        if not keywords:
            st.warning("비교할 상품명을 한 개 이상 입력해주세요.")
            st.stop()
        with st.spinner("네이버 쇼핑 검색 추이를 분석하는 중입니다..."):
            if not category_ids:
                st.warning("조회 카테고리를 한 개 이상 선택해주세요.")
                st.stop()
            raw = fetch_keyword_trends(category_ids, keywords)
    else:
        st.info("왼쪽에서 상품명을 확인한 뒤 ‘지금 분석하기’를 눌러주세요.")
        st.stop()

    result = score_and_classify(raw)
    if result.empty:
        st.warning("조회된 데이터가 없습니다. 카테고리 ID와 상품명을 확인해주세요.")
        st.stop()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("분석 상품", f"{len(result)}개")
    c2.metric("급상승", f"{(result['카테고리'] == '급상승').sum()}개")
    c3.metric("검색 상승", f"{(result['검색증감률'] > 0).sum()}개")
    c4.metric("최고 상승률", f"{result['검색증감률'].max():.1f}%")

    st.subheader("네이버 검색 추이")
    chart_data = result[["상품명", "검색증감률"]].sort_values("검색증감률", ascending=False).head(15)
    chart = alt.Chart(chart_data).mark_bar(cornerRadiusEnd=4).encode(
        x=alt.X("검색증감률:Q", title="검색 증감률 (%)"),
        y=alt.Y("상품명:N", sort="-x", title=None, axis=alt.Axis(labelLimit=180, labelAngle=0)),
        color=alt.condition(alt.datum.검색증감률 >= 0, alt.value("#ff4b4b"), alt.value("#4da3ff")),
        tooltip=["상품명", alt.Tooltip("검색증감률:Q", format=".1f")],
    ).properties(height=max(280, len(chart_data) * 34))
    st.altair_chart(chart, use_container_width=True)

    st.subheader("상품 트렌드 분석 결과")
    view = result[["상품명", "카테고리", "등급", "플랫폼 순위", "네이버 검색 트렌드", "점수", "URL"]].copy()
    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        column_config={"URL": st.column_config.LinkColumn("상품 URL", display_text="네이버쇼핑 보기")},
    )
    if sales_csv is not None:
        try:
            sales = load_product_evidence(sales_csv)
            selected = select_real_products(sales, result)
            st.subheader("🏆 판매 근거로 선별한 실제 상품 TOP 9")
            if selected.empty:
                st.warning("판매수량·베스트순위·리뷰수 중 확인 가능한 근거가 있는 상품이 없습니다. 최종 추천을 만들지 않았습니다.")
            else:
                final_view = selected[["검색어", "상품명", "플랫폼", "분류", "등급", "판매근거", "검색증감률", "총점", "상품URL"]]
                st.dataframe(final_view, use_container_width=True, hide_index=True,
                    column_config={"상품URL": st.column_config.LinkColumn("상품", display_text="상품 보기")})
                st.success("각 급상승 검색어에서 실제 판매 근거 점수가 가장 높은 구체적인 상품을 선별했습니다.")
        except Exception as csv_exc:
            st.warning(f"판매자료 CSV를 읽지 못했습니다: {csv_exc}")
    else:
        st.warning("현재는 검색 상승만 확인된 상태입니다. 구체적인 베스트 상품은 판매근거 CSV를 올리기 전에는 선정하지 않습니다.")
    st.markdown("**연결 대상 쇼핑몰:** 신상마켓, 네이버쇼핑, 무신사, 29CM, 에이블리, 지그재그, W컨셉, 브랜디, 하이버, 쿠팡")
    st.caption("※ 네이버 쇼핑인사이트는 실제 판매수량이 아닌 검색·클릭 상대지수입니다. 판매량은 쇼핑몰 CSV를 추가 연결해야 검증할 수 있습니다.")
except Exception as exc:
    st.error(str(exc))
    st.info("API HUB의 쇼핑인사이트 권한, Secrets 이름, 카테고리 ID를 확인해주세요.")
