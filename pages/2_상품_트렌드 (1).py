import os
from datetime import date, timedelta, datetime

import pandas as pd
import requests
import streamlit as st


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
def fetch_keyword_trends(category_id: str, keywords: tuple[str, ...]) -> pd.DataFrame:
    if not all(api_headers().get(k) for k in ("X-NCP-APIGW-API-KEY-ID", "X-NCP-APIGW-API-KEY")):
        raise RuntimeError("Streamlit Secrets에 네이버 Client ID와 Client Secret을 먼저 저장해주세요.")

    end = date.today() - timedelta(days=1)
    start = end - timedelta(days=27)
    rows = []

    # 쇼핑인사이트 키워드 API는 한 요청에 최대 5개 그룹으로 나누어 호출합니다.
    for pos in range(0, len(keywords), 5):
        batch = keywords[pos : pos + 5]
        body = {
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "timeUnit": "date",
            "category": category_id.strip(),
            "keyword": [{"name": k, "param": [k]} for k in batch],
        }
        response = requests.post(
            API_BASE + SHOPPING_KEYWORD_PATH,
            headers=api_headers(),
            json=body,
            timeout=20,
        )
        if response.status_code != 200:
            detail = response.text[:300]
            raise RuntimeError(f"네이버 API 오류 ({response.status_code}): {detail}")

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
            rows.append(
                {
                    "상품명": result.get("title") or result.get("keywords", ["키워드"])[0],
                    "이전지수": round(old_avg, 1),
                    "최근지수": round(new_avg, 1),
                    "검색증감률": round(growth, 1),
                    "추이": values,
                }
            )
    return pd.DataFrame(rows)


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
    keywords_text = st.text_area(
        "비교할 상품·검색어 (한 줄에 하나)",
        "남성 반팔티\n라이더 티셔츠\n바이크 액세서리\n남성 액세서리\n경량 바람막이\n와이드 데님\n러닝 벨트\n미니 크로스백\n무선 보조배터리",
        height=210,
    )
    category_id = st.text_input("네이버 카테고리 ID", "50000000", help="현재 값은 패션의류 대분류입니다.")
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
            raw = fetch_keyword_trends(category_id, keywords)
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
    chart = result.set_index("상품명")["검색증감률"].sort_values(ascending=False)
    st.bar_chart(chart)

    st.subheader("상품 트렌드 분석 결과")
    view = result[["상품명", "카테고리", "등급", "플랫폼 순위", "네이버 검색 트렌드", "점수", "URL"]].copy()
    st.dataframe(
        view,
        use_container_width=True,
        hide_index=True,
        column_config={"URL": st.column_config.LinkColumn("상품 URL", display_text="네이버쇼핑 보기")},
    )
    st.caption("※ 네이버 쇼핑인사이트는 실제 판매수량이 아닌 검색·클릭 상대지수입니다. 판매량은 쇼핑몰 CSV를 추가 연결해야 검증할 수 있습니다.")
except Exception as exc:
    st.error(str(exc))
    st.info("API HUB의 쇼핑인사이트 권한, Secrets 이름, 카테고리 ID를 확인해주세요.")
