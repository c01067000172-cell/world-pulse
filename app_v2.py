import html
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote_plus

import feedparser
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
import yfinance as yf
from dotenv import load_dotenv

load_dotenv()
st.set_page_config(page_title="WORLD PULSE", page_icon="🌐", layout="wide")

ASSETS = {
    "S&P 500": "^GSPC", "NASDAQ": "^IXIC", "KOSPI": "^KS11", "NIKKEI": "^N225",
    "달러/원": "KRW=X", "금": "GC=F", "WTI": "CL=F", "비트코인": "BTC-USD",
}
TOPICS = {
    "전체": "global economy markets",
    "미국": "US economy stock market",
    "한국": "South Korea economy stock market",
    "중국": "China economy markets",
    "유럽": "Europe economy markets",
    "전쟁·외교": "war geopolitics sanctions",
    "AI·반도체": "AI semiconductor industry",
    "에너지": "oil gas energy market",
}
POSITIVE = {
    "surge": 2, "rally": 2, "growth": 1, "gain": 1, "rise": 1, "cut": 1,
    "호재": 2, "급등": 2, "상승": 1, "성장": 1, "인하": 1, "회복": 1,
}
NEGATIVE = {
    "war": 2, "crisis": 2, "crash": 2, "sanction": 1, "tariff": 1, "fall": 1,
    "전쟁": 2, "위기": 2, "폭락": 2, "제재": 1, "관세": 1, "하락": 1, "침체": 1,
}
IMPACT_MAP = [
    (["ai", "인공지능", "semiconductor", "반도체", "chip"], "AI·반도체", "반도체·데이터센터", "전통 IT·고평가 성장주"),
    (["oil", "crude", "opec", "원유", "유가"], "에너지", "정유·에너지", "항공·운송·화학"),
    (["war", "missile", "전쟁", "미사일", "defense"], "지정학", "방산·금", "항공·여행·위험자산"),
    (["rate", "fed", "금리", "연준", "inflation", "물가"], "통화정책", "은행·보험", "부동산·고평가 성장주"),
    (["tariff", "trade", "관세", "무역", "sanction", "제재"], "무역", "내수·대체생산", "수출·물류"),
    (["battery", "ev", "전기차", "배터리"], "미래차", "배터리 소재·충전", "내연기관 부품"),
]

st.markdown("""
<style>
  .stApp { background:#070b12; color:#edf4ff; }
  [data-testid="stSidebar"] { background:#0b111b; border-right:1px solid #1d2a3a; }
  [data-testid="stMetric"] { background:#0d1521; border:1px solid #1d2a3a;
    padding:14px; border-radius:8px; }
  .headline { border-bottom:1px solid #1d2a3a; padding:11px 4px; }
  .tag { display:inline-block; border:1px solid #29415f; color:#8bbcff;
    padding:2px 7px; border-radius:4px; font-size:12px; margin-right:7px; }
  .bull {color:#ff5c72}.bear {color:#44a7ff}.neutral {color:#a7b3c4}
  h1,h2,h3 {letter-spacing:-.02em}
</style>
""", unsafe_allow_html=True)

def safe_text(value):
    return html.escape(re.sub(r"<[^>]+>", "", str(value or "")))

@st.cache_data(ttl=300, show_spinner=False)
def load_market_data(period="5d"):
    rows, histories = [], {}
    for name, ticker in ASSETS.items():
        try:
            frame = yf.download(ticker, period=period, interval="1d", progress=False, auto_adjust=True)
            close = frame["Close"].dropna()
            if isinstance(close, pd.DataFrame):
                close = close.iloc[:, 0]
            latest, previous = float(close.iloc[-1]), float(close.iloc[-2])
            change = (latest / previous - 1) * 100
            rows.append({"자산": name, "티커": ticker, "현재가": latest, "등락률": change})
            histories[name] = close
        except Exception:
            rows.append({"자산": name, "티커": ticker, "현재가": None, "등락률": None})
    return pd.DataFrame(rows), histories

@st.cache_data(ttl=600, show_spinner=False)
def load_news(topic, limit=30):
    query = TOPICS.get(topic, TOPICS["전체"])
    url = f"https://news.google.com/rss/search?q={quote_plus(query)}&hl=ko&gl=KR&ceid=KR:ko"
    feed = feedparser.parse(url)
    news = []
    for item in feed.entries[:limit]:
        title = safe_text(item.get("title", ""))
        raw = title.lower()
        bull = sum(weight for word, weight in POSITIVE.items() if word in raw)
        bear = sum(weight for word, weight in NEGATIVE.items() if word in raw)
        impact = min(100, 35 + 12 * max(bull, bear) + (10 if any(x in raw for x in ["fed", "중앙은행", "정부", "전쟁"]) else 0))
        direction = "상승 요인" if bull > bear else "하락 요인" if bear > bull else "중립"
        category, winners, losers = "글로벌 경제", "시장 대표주", "변동성 취약 업종"
        for keys, mapped, good, bad in IMPACT_MAP:
            if any(key in raw for key in keys):
                category, winners, losers = mapped, good, bad
                break
        short_title = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
        summary = f"{short_title[:95]}{'…' if len(short_title) > 95 else ''}"
        news.append({
            "title": title, "link": item.get("link", "#"),
            "published": item.get("published", ""), "direction": direction, "impact": impact,
            "category": category, "winners": winners, "losers": losers, "summary": summary,
        })
    return news

@st.cache_data(ttl=60, show_spinner=False)
def load_symbol(ticker):
    frame = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
    close = frame["Close"].dropna()
    if isinstance(close, pd.DataFrame):
        close = close.iloc[:, 0]
    return close

with st.sidebar:
    st.markdown("## WORLD PULSE")
    st.caption("글로벌 시장 이슈 터미널")
    topic = st.radio("뉴스 분야", list(TOPICS), index=0)
    period = st.selectbox("차트 기간", ["5d", "1mo", "3mo", "6mo", "1y"], index=1)
    auto = st.toggle("5분마다 자동 새로고침", value=True)
    st.divider()
    st.markdown("#### 관심 종목")
    custom_ticker = st.text_input("종목 코드", value="AAPL", help="예: AAPL, TSLA, 005930.KS")
    if st.button("지금 새로고침", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.caption("빨강: 상승 · 파랑: 하락 (한국식 표시)")
    st.caption("뉴스 점수는 제목 기반 1차 분석이며 투자 판단의 단독 근거가 아닙니다.")

if auto:
    st.markdown("<meta http-equiv='refresh' content='300'>", unsafe_allow_html=True)

market, histories = load_market_data(period)
news = load_news(topic)
now = datetime.now(timezone.utc).astimezone()
st.title("세계 시장 상황판")
st.caption(f"{now:%Y-%m-%d %H:%M} 기준 · 시장 데이터는 지연될 수 있음")

cols = st.columns(4)
for idx, row in market.head(4).iterrows():
    value = "연결 대기" if pd.isna(row["현재가"]) else f"{row['현재가']:,.2f}"
    delta = None if pd.isna(row["등락률"]) else f"{row['등락률']:+.2f}%"
    cols[idx].metric(row["자산"], value, delta)

left, right = st.columns([1.65, 1], gap="large")
with left:
    st.subheader("시장 흐름")
    selected = st.selectbox("차트 자산", list(histories), label_visibility="collapsed")
    if selected:
        series = histories[selected]
        color = "#ff496c" if len(series) < 2 or series.iloc[-1] >= series.iloc[0] else "#3797ff"
        fig = go.Figure(go.Scatter(x=series.index, y=series.values, mode="lines", line=dict(color=color, width=2)))
        fig.update_layout(height=330, margin=dict(l=8,r=8,t=20,b=8), paper_bgcolor="#0d1521",
                          plot_bgcolor="#0d1521", font_color="#a9b9ce", showlegend=False,
                          xaxis=dict(gridcolor="#1a2839"), yaxis=dict(gridcolor="#1a2839"))
        st.plotly_chart(fig, use_container_width=True)

    st.subheader("전체 자산")
    shown = market.copy()
    shown["현재가"] = shown["현재가"].map(lambda x: "-" if pd.isna(x) else f"{x:,.2f}")
    shown["등락률"] = shown["등락률"].map(lambda x: "-" if pd.isna(x) else f"{x:+.2f}%")
    st.dataframe(shown[["자산", "현재가", "등락률"]], hide_index=True, use_container_width=True)
    st.subheader("관심 종목 1분 흐름")
    st.caption("미국: AAPL·TSLA / 한국: 005930.KS·000660.KS")
    try:
        custom = load_symbol(custom_ticker.strip().upper())
        if custom.empty:
            st.warning("해당 종목의 오늘 분봉 데이터가 없습니다.")
        else:
            delta = (custom.iloc[-1] / custom.iloc[0] - 1) * 100
            st.metric(custom_ticker.upper(), f"{custom.iloc[-1]:,.2f}", f"{delta:+.2f}%")
            micro = go.Figure(go.Scatter(x=custom.index, y=custom.values, mode="lines",
                line=dict(color="#ff496c" if delta >= 0 else "#3797ff", width=2)))
            micro.update_layout(height=230, margin=dict(l=8,r=8,t=8,b=8), paper_bgcolor="#0d1521",
                plot_bgcolor="#0d1521", font_color="#a9b9ce", showlegend=False,
                xaxis=dict(gridcolor="#1a2839"), yaxis=dict(gridcolor="#1a2839"))
            st.plotly_chart(micro, use_container_width=True)
    except Exception:
        st.warning("종목 코드를 확인하거나 잠시 후 다시 시도하세요.")

with right:
    bulls = sum(n["direction"] == "상승 요인" for n in news)
    bears = sum(n["direction"] == "하락 요인" for n in news)
    mood = "위험 선호" if bulls > bears else "위험 회피" if bears > bulls else "혼조"
    st.subheader("이슈 온도")
    a, b, c = st.columns(3)
    a.metric("시장 분위기", mood)
    b.metric("상승 요인", bulls)
    c.metric("하락 요인", bears)
    st.subheader(f"{topic} 주요 이슈")
    for item in sorted(news, key=lambda x: x["impact"], reverse=True)[:12]:
        css = "bull" if item["direction"] == "상승 요인" else "bear" if item["direction"] == "하락 요인" else "neutral"
        st.markdown(
            f'<div class="headline"><span class="tag">{item["impact"]}점</span>'
            f'<span class="{css}">{item["direction"]}</span><br>'
            f'<a href="{item["link"]}" target="_blank" style="color:#edf4ff;text-decoration:none">'
            f'{item["title"]}</a></div>', unsafe_allow_html=True)
        with st.expander("시장 영향 분석"):
            st.write(item["summary"])
            x, y = st.columns(2)
            x.success(f"수혜 가능: {item['winners']}")
            y.error(f"부담 가능: {item['losers']}")
            st.caption(f"분류: {item['category']} · 영향도 {item['impact']}점 · {item['direction']}")

st.divider()
st.caption("데이터: Yahoo Finance 비공식 데이터 · Google News RSS. 업종 영향 분석은 규칙 기반 참고 정보이며 실제 주문 기능은 포함하지 않습니다.")
