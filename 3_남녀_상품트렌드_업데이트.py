import os
from datetime import date, timedelta, datetime

import pandas as pd
import requests
import streamlit as st
import altair as alt


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
    sales_csv = st.file_uploader("신상마켓·쇼핑몰 판매자료 CSV", type=["csv"], help="상품명, 플랫폼, 현재순위, 이전순위, 판매수량, URL 열을 사용합니다.")
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
            sales = pd.read_csv(sales_csv)
            st.subheader("신상마켓·제휴 쇼핑몰 실제 판매자료")
            st.dataframe(sales, use_container_width=True, hide_index=True)
            st.success("업로드한 실제 판매자료를 불러왔습니다. 판매수량과 순위는 이 자료를 기준으로 확인하세요.")
        except Exception as csv_exc:
            st.warning(f"판매자료 CSV를 읽지 못했습니다: {csv_exc}")
    st.markdown("**연결 대상 쇼핑몰:** 신상마켓, 네이버쇼핑, 무신사, 29CM, 에이블리, 지그재그, W컨셉, 브랜디, 하이버, 쿠팡")
    st.caption("※ 네이버 쇼핑인사이트는 실제 판매수량이 아닌 검색·클릭 상대지수입니다. 판매량은 쇼핑몰 CSV를 추가 연결해야 검증할 수 있습니다.")
except Exception as exc:
    st.error(str(exc))
    st.info("API HUB의 쇼핑인사이트 권한, Secrets 이름, 카테고리 ID를 확인해주세요.")
