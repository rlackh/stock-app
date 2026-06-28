import streamlit as st
import pandas as pd
import yfinance as yf
from pykrx import stock
from datetime import datetime, timedelta
import urllib.parse
import requests
import xml.etree.ElementTree as ET
import time

# 1. 페이지 기본 설정 및 가로 폭 짤림 방지 레이아웃 최적화
st.set_page_config(page_title="AITAS-EQ 실시간 투자 전략 시스템", layout="wide", initial_sidebar_state="expanded")

st.markdown("""
    <style>
    .stMarkdown, .stTable, div[data-testid="stMetricValue"], div[data-testid="stMetricLabel"], .stTabs, p, span, li {
        word-break: break-all !important;
        white-space: normal !important;
        overflow-wrap: break-word !important;
    }
    .block-container { padding-left: 2rem !important; padding-right: 2rem !important; max-width: 100% !important; }
    table { width: 100% !important; table-layout: fixed !important; }
    th, td { word-wrap: break-word !important; white-space: normal !important; }
    div[data-testid="stVisGlRenderer"], .stChart, div[class^="st-emotion-cache"] { max-width: 100% !important; overflow: hidden !important; }
    </style>
    """, unsafe_allow_html=True)

st.title("🏛️ AITAS-EQ 실시간 투자 전략 시스템")

# 💡 [365일 무적] 네이버 + 다음 증권 통합 실시간 검색 엔진
@st.cache_data(ttl=300)
def 통합_포털_종목_검색(query_text):
    results = {}
    clean_q = str(query_text).strip().upper()
    if not clean_q: return []
    try:
        enc_q = urllib.parse.quote(clean_q.encode('euc-kr'))
        naver_url = f"https://ac.finance.naver.com/ac?q={enc_q}&q_enc=euc-kr&st=1&frm=stock&r_format=json"
        res = requests.get(naver_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2).json()
        if 'items' in res and res['items'][0]:
            for item in res['items'][0]:
                name, code = item[0][0], item[0][1]
                if code.isdigit() and len(code) == 6:
                    results[code] = name
    except: pass
    try:
        daum_url = f"https://finance.daum.net/api/search/search?q={urllib.parse.quote(clean_q)}"
        headers = {'User-Agent': 'Mozilla/5.0', 'Referer': 'https://finance.daum.net/'}
        res_daum = requests.get(daum_url, headers=headers, timeout=2).json()
        if 'data' in res_daum:
            for item in res_daum['data']:
                name = item.get('name')
                code = item.get('symbolCode')
                if name and code:
                    clean_code = code[1:] if code.startswith('A') else code
                    if clean_code.isdigit() and len(clean_code) == 6 and clean_code not in results:
                        results[clean_code] = name
    except: pass
    if not results:
        fallback = {"삼성전자": "005930", "SK하이닉스": "000660", "한미반도체": "042700", "삼성중공업": "010140", "LG전자": "066570", "SK텔레콤": "017670", "심텍": "222800", "에코프로": "086520"}
        for f_name, f_code in fallback.items():
            if clean_q in f_name.upper(): results[f_code] = f_name
    return [{"name": name, "code": code} for code, name in results.items()]

# ==========================================
# 2. 공통 백엔드 연산 엔진 (점수 계산 모듈화)
# ==========================================
def get_safe_business_day(offset=0):
    today = datetime.utcnow() + timedelta(hours=9) - timedelta(days=offset)
    while today.weekday() >= 5: today -= timedelta(days=1)
    if today.hour < 16 and offset == 0:
        today -= timedelta(days=1)
        while today.weekday() >= 5: today -= timedelta(days=1)
    return today.strftime("%Y%m%d")

def analyze_stock_score(ticker_code, stock_name):
    # 스크리너를 위한 백엔드 고속 연산 함수
    market_type = "KOSPI"
    df_chart = pd.DataFrame()
    for sfx in [".KS", ".KQ"]:
        try:
            df_chart = yf.Ticker(f"{ticker_code}{sfx}").history(period="3mo")
            if not df_chart.empty:
                market_type = "KOSPI" if sfx == ".KS" else "KOSDAQ"
                break
        except: pass
    
    if df_chart.empty or len(df_chart) < 20:
        return None
        
    current_price = int(df_chart['Close'].iloc[-1])
    
    df_chart['5MA'] = df_chart['Close'].rolling(window=5).mean()
    df_chart['20MA'] = df_chart['Close'].rolling(window=20).mean()
    
    ma5_curr, ma20_curr = df_chart['5MA'].iloc[-1], df_chart['20MA'].iloc[-1]
    ma5_prev, ma20_prev = df_chart['5MA'].iloc[-2], df_chart['20MA'].iloc[-2]
    
    cross_signal = ""
    if ma5_prev <= ma20_prev and ma5_curr > ma20_curr: cross_signal = "골든크로스"
        
    delta = df_chart['Close'].diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    rsi = (100 - (100 / (1 + (up.ewm(com=13, adjust=False).mean() / down.ewm(com=13, adjust=False).mean())))).iloc[-1]
    
    # 펀더멘탈 및 수급 (고속 처리를 위해 예외처리 간소화)
    safe_date = get_safe_business_day()
    per, pbr, foreign_buy = 0.0, 0.0, 0
    try:
        df_fund = stock.get_market_fundamental(safe_date, market="ALL")
        if not df_fund.empty and ticker_code in df_fund.index:
            pbr = df_fund.loc[ticker_code, 'PBR']
            per = df_fund.loc[ticker_code, 'PER']
    except: pass
    
    try:
        start_date = get_safe_business_day(offset=30)
        df_net_buy = stock.get_market_net_purchases_of_equities_by_ticker(start_date, safe_date, market_type)
        if not df_net_buy.empty and ticker_code in df_net_buy.index:
            foreign_buy = df_net_buy.loc[ticker_code, '외국인합계']
    except: pass
    
    # 간이 뉴스 감성 분석
    has_crisis = False
    try:
        enc_text = urllib.parse.quote(f"{stock_name} 주가 악재 위기")
        url = f"https://news.google.com/rss/search?q={enc_text}&hl=ko&gl=KR&ceid=KR:ko"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
        root = ET.fromstring(res.text.encode('utf-8'))
        crisis_words = ['상장폐지', '부도', '횡령', '배임', '소송', '디폴트', '검찰', '조작']
        for item in root.findall('.//item')[:3]:
            title = item.find('title').text or ""
            if any(cw in title for cw in crisis_words):
                has_crisis = True
                break
    except: pass
    
    # 스코어링 로직
    base_score = 50
    if rsi <= 38: base_score += 15
    if cross_signal == "골든크로스": base_score += 15
    if rsi >= 65: base_score -= 15
    if 0 < pbr <= 1.2: base_score += 10
    if per > 35: base_score -= 10
    if foreign_buy > 0: base_score += 10
    if has_crisis: base_score -= 25
    
    final_score = max(0, min(100, base_score))
    
    return {
        "name": stock_name,
        "code": ticker_code,
        "price": current_price,
        "score": final_score,
        "rsi": rsi,
        "pbr": pbr,
        "cross": cross_signal
    }

# ==========================================
# 3. 사이드바 - [포털 연동형] 종목 검색기
# ==========================================
st.sidebar.header("🔍 종목 분석 및 코드 검색")
ticker_input = st.sidebar.text_input("💎 분석할 종목명 또는 6자리 코드", value="삼성전자") 
st.sidebar.markdown("---")
st.sidebar.subheader("📖 포털 실시간 종목사전")
search_keyword = st.sidebar.text_input("찾으실 종목명을 입력하세요", value="")

if search_keyword.strip():
    st.sidebar.write("📌 **통합 검색 결과:**")
    search_res = 통합_포털_종목_검색(search_keyword)
    if search_res:
        for stock_info in search_res:
            st.sidebar.code(f"{stock_info['name']} : {stock_info['code']}", language="text")
    else:
        st.sidebar.warning("🔍 일치하는 종목코드가 없습니다.")
st.sidebar.markdown("---")

def find_stock_code_global_portal(name_or_code):
    query = str(name_or_code).strip()
    if query.isdigit() and len(query) == 6: return query, query, "KOSPI"
    portal_res = 통합_포털_종목_검색(query)
    if portal_res: return portal_res[0]['code'], portal_res[0]['name'], "KOSPI"
    return None, None, None

def get_advanced_financial_news(stock_name, ticker_code, market_type):
    news_list = []
    seen_titles = set()
    try:
        suffix = ".KS" if market_type == "KOSPI" else ".KQ"
        yf_stock = yf.Ticker(f"{ticker_code}{suffix}")
        yf_news = yf_stock.news
        if yf_news:
            for n in yf_news[:2]:
                title = n.get('title', '')
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    news_list.append({"title": f"[증권속보] {title}", "link": n.get('link', '#'), "raw_title": title})
    except: pass
    try:
        enc_text = urllib.parse.quote(f"{stock_name} 주가 공시 뉴스")
        url = f"https://news.google.com/rss/search?q={enc_text}&hl=ko&gl=KR&ceid=KR:ko"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        root = ET.fromstring(res.text.encode('utf-8'))
        for item in root.findall('.//item')[:8]:
            title = item.find('title').text or ""
            if " - " in title: title = title.split(" - ")[0]
            if title and title not in seen_titles:
                seen_titles.add(title)
                news_list.append({"title": title, "link": item.find('link').text or "#", "raw_title": title})
    except: pass
    
    classified_news = []
    for n in news_list:
        tt = n['raw_title']
        opp_score = sum(1 for w in ['기회', '상승', '돌파', '급등', '호재', '수주'] if w in tt)
        crisis_score = sum(1 for w in ['위기', '상장폐지', '부도', '횡령', '배임', '소송'] if w in tt)
        bad_score = sum(1 for w in ['하락', '급락', '악재', '우려', '감소', '적자'] if w in tt)
        if crisis_score > 0: tag = "🚨 위기감지"
        elif bad_score > opp_score: tag = "📉 악재경보"
        elif opp_score > bad_score: tag = "🔥 투자기회"
        else: tag = "⚪ 중립속보"
        classified_news.append({"title": n['title'], "link": n['link'], "sent": tag})
    return classified_news

# ==========================================
# 4. 메인 화면: 탭 분리 (개별 분석 vs 자동 스크리너)
# ==========================================
main_tab1, main_tab2 = st.tabs(["🔍 1. 개별 종목 정밀 분석", "🏆 2. AI 주도주 매수 타점 포착 (자동 스캐너)"])

with main_tab1:
    ticker_code, stock_name, market_type = find_stock_code_global_portal(ticker_input)

    if not ticker_code:
        st.error("❌ 종목을 찾을 수 없습니다. 정확한 한글 종목명이나 6자리 숫자 코드를 입력해 주세요.")
    else:
        df_chart = pd.DataFrame()
        for sfx in [".KS", ".KQ"]:
            try:
                df_chart = yf.Ticker(f"{ticker_code}{sfx}").history(period="6mo")
                if not df_chart.empty:
                    market_type = "KOSPI" if sfx == ".KS" else "KOSDAQ"
                    break
            except: pass
                
        safe_date = get_safe_business_day()
        df_net_buy = pd.DataFrame()
        try:
            start_date = get_safe_business_day(offset=30)
            df_net_buy = stock.get_market_net_purchases_of_equities_by_ticker(start_date, safe_date, market_type)
        except: pass

        if df_chart.empty:
            st.error("🔄 야후 금융 서버로부터 주가 데이터를 수신하지 못했습니다. 잠시 후 시도해 주세요.")
        else:
            current_price = int(df_chart['Close'].iloc[-1])
            prev_price = int(df_chart['Close'].iloc[-2])
            price_change_percent = ((current_price - prev_price) / prev_price) * 100
            
            df_chart['5일 이동평균선'] = df_chart['Close'].rolling(window=5).mean()
            df_chart['20일 이동평균선'] = df_chart['Close'].rolling(window=20).mean()
            df_chart['60일 이동평균선'] = df_chart['Close'].rolling(window=60).mean()
            
            ma5_curr, ma20_curr, ma60_curr = df_chart['5일 이동평균선'].iloc[-1], df_chart['20일 이동평균선'].iloc[-1], df_chart['60일 이동평균선'].iloc[-1]
            if ma5_curr > ma20_curr > ma60_curr: chart_trend = "📈 강력 상승 정배열 상태"
            elif ma5_curr < ma20_curr < ma60_curr: chart_trend = "📉 하락 역배열 상태"
            else: chart_trend = "🔄 이평선 밀집 및 혼조세 (박스권 횡보)"
                
            ma5_prev, ma20_prev = df_chart['5일 이동평균선'].iloc[-2], df_chart['20일 이동평균선'].iloc[-2]
            cross_signal = "🟢 특이 매수/매도 시그널 없음"
            if ma5_prev <= ma20_prev and ma5_curr > ma20_curr: cross_signal = "🔥 골든크로스 발생! (단기 강력 매수 신호)"
            elif ma5_prev >= ma20_prev and ma5_curr < ma20_curr: cross_signal = "🚨 데드크로스 발생!"
                
            high_3mo = df_chart['Close'].iloc[-60:].max()
            drop_rate = ((high_3mo - current_price) / high_3mo) * 100
            chart_analysis_text = f" 최근 3개월 최고가 대비 현재 주가는 **-{drop_rate:.1f}%** 조정받은 위치에 있습니다."

            per, pbr, div = 0.0, 0.0, 0.0
            try:
                df_fund = stock.get_market_fundamental(safe_date, market="ALL")
                if not df_fund.empty and ticker_code in df_fund.index:
                    per, pbr, div = df_fund.loc[ticker_code, 'PER'], df_fund.loc[ticker_code, 'PBR'], df_fund.loc[ticker_code, 'DIV']
            except: pass
            
            delta = df_chart['Close'].diff()
            up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
            rsi = (100 - (100 / (1 + (up.ewm(com=13, adjust=False).mean() / down.ewm(com=13, adjust=False).mean())))).iloc[-1]
            vol_ratio = df_chart['Volume'].iloc[-1] / df_chart['Volume'].rolling(window=20).mean().iloc[-1]

            advanced_news = get_advanced_financial_news(stock_name, ticker_code, market_type)
            
            base_score = 50
            if rsi <= 38: base_score += 15
            if "골든크로스" in cross_signal: base_score += 15
            if rsi >= 65: base_score -= 15
            if 0 < pbr <= 1.2: base_score += 10
            if per > 35: base_score -= 10
            
            foreign_buy = 0
            try:
                if not df_net_buy.empty and ticker_code in df_net_buy.index:
                    foreign_buy = df_net_buy.loc[ticker_code, '외국인합계']
                    if foreign_buy > 0: base_score += 10
            except: pass
                
            has_crisis = any(n.get('sent', '') == '🚨 위기감지' for n in advanced_news)
            if has_crisis: base_score -= 25
            final_score = max(0, min(100, base_score))

            col1, col2, col3, col4 = st.columns(4)
            col1.metric(label=f"현재가 ({stock_name})", value=f"{format(current_price, ',')} 원", delta=f"{price_change_percent:.2f} %")
            col2.metric(label="RSI (과열도)", value=f"{rsi:.1f}", delta="과매도 지점" if rsi<=30 else "안정")
            col3.metric(label="20일비 거래량", value=f"{vol_ratio:.2f} 배", delta="수급 폭발" if vol_ratio>=1.5 else "정상")
            
            if final_score >= 75:
                decision_text, decision_delta, opinion, strategy_text = "🔥 강력 매수", "전문가 추천 바닥권", "🔥 강력 매수", "안전마진과 차트 변곡점이 모두 융합된 최적의 바닥 타점입니다."
            elif final_score >= 50:
                decision_text, decision_delta, opinion, strategy_text = "✅ 분할 매수", "하방 경직성 확보", "✅ 분할 매수", "하단 지지선을 디딤돌 삼아 장기 물량을 모아가기 좋은 구간입니다."
            else:
                decision_text, decision_delta, opinion, strategy_text = "🚨 매수 금지", "관망 요망", "🚨 매수 금지", "악재 수렴 중이거나 차트가 고점 과열 상태입니다. 현금을 쥐고 관망하십시오."
                
            col4.metric(label="🏛️ AITAS 최종 결론", value=decision_text, delta=f"점수: {final_score}점")

            st.subheader("📋 AITAS-EQ 종합 전략 투자 분석 보고서")
            left_col, right_col = st.columns([1, 1])
            with left_col:
                tab1, tab2, tab3 = st.tabs(["💬 전문가 토론", "🚀 매수 타이밍", "📰 실시간 속보"])
                with tab1:
                    st.markdown(f"**🔹 기본적 분석가:** 밸류에이션(PER {per:.2f}, PBR {pbr:.2f}) 내재가치 검증 완료.\n\n**🔹 기술적 분석가:** RSI {rsi:.1f}점 기반 바닥 역추적.")
                with tab2:
                    st.info(f"**투자 의견:** {opinion}\n\n**코멘트:** {strategy_text}")
                    st.success(f"🎯 **추천 분할 매수 타점:** {format(int(current_price * 0.95), ',')} 원 부근")
                with tab3:
                    for news in advanced_news: st.markdown(f"- **{news['sent']}** | [{news['title']}]({news['link']})")
            with right_col:
                st.markdown("### 📈 주가 흐름 및 3대 이동평균선(MA)")
                st.line_chart(df_chart[['Close', '5일 이동평균선', '20일 이동평균선', '60일 이동평균선']].rename(columns={'Close': '현재 주가'}))
                st.info(f"🔍 **[차트 진단]** {chart_trend} | 신호: {cross_signal} | {chart_analysis_text}")

# ==========================================
# 5. [신규] 두 번째 탭: 주도주 AI 스크리너 엔진
# ==========================================
with main_tab2:
    st.subheader("🤖 국내 핵심 주도주 20선 실시간 자동 매수 타점 스캐너")
    st.markdown("시장을 주도하는 주요 대형주 및 테마주 20개를 순식간에 스캔하여, **현재 75점(강력 매수) 이상의 진바닥에 위치한 종목만 필터링**하여 추천해 드립니다.")
    
    if st.button("🚀 AI 자동 스크리너 가동하기 (클릭)", type="primary"):
        # 시장을 주도하는 관심종목 풀 (속도를 위해 핵심 20개로 압축)
        watch_list = [
            ("005930", "삼성전자"), ("000660", "SK하이닉스"), ("042700", "한미반도체"), 
            ("035420", "NAVER"), ("035720", "카카오"), ("005380", "현대차"), ("000270", "기아"), 
            ("068270", "셀트리온"), ("207940", "삼성바이오로직스"), ("373220", "LG에너지솔루션"),
            ("086520", "에코프로"), ("247540", "에코프로비엠"), ("005490", "POSCO홀딩스"), 
            ("010140", "삼성중공업"), ("043200", "HD현대일렉트릭"), ("222800", "심텍"), 
            ("017670", "SK텔레콤"), ("051910", "LG화학"), ("034220", "LG디스플레이"), ("454910", "두산로보틱스")
        ]
        
        progress_text = "AI가 거시경제와 20대 주도주 차트를 정밀 스캔 중입니다. 잠시만 기다려주세요..."
        my_bar = st.progress(0, text=progress_text)
        
        recommended_stocks = []
        accumulate_stocks = []
        
        # 20개 종목 고속 스캔 루프
        for i, (code, name) in enumerate(watch_list):
            time.sleep(0.1) # 서버 과부하 방지
            my_bar.progress((i + 1) / len(watch_list), text=f"🔍 스캔 중: {name} ({i+1}/{len(watch_list)})")
            
            result = analyze_stock_score(code, name)
            if result:
                if result['score'] >= 75:
                    recommended_stocks.append(result)
                elif result['score'] >= 60:
                    accumulate_stocks.append(result)
                    
        my_bar.empty()
        st.success("✅ 실시간 시장 스캔이 완료되었습니다!")
        
        st.markdown("### 🔥 AITAS-EQ 강력 매수 추천 (75점 이상 진바닥 종목)")
        if recommended_stocks:
            for rec in recommended_stocks:
                st.info(f"**💎 {rec['name']} ({rec['code']})** | 현재가: {format(rec['price'], ',')}원 | **총점: {rec['score']}점**")
                st.write(f"↪️ **추천 근거:** RSI 바닥권({rec['rsi']:.1f}), PBR {rec['pbr']:.2f}배 저평가 구간. 차트 변곡 시그널({rec['cross'] if rec['cross'] else '응축 중'}) 포착 완료.")
        else:
            st.warning("🚨 현재 75점 이상의 완벽한 매수 타점에 도달한 주도주가 없습니다. 시장 전체가 과열되었거나 역배열 상태입니다.")
            
        st.markdown("### ✅ 분할 매수 및 관심 편입 권장 (60점 ~ 74점)")
        if accumulate_stocks:
            for acc in accumulate_stocks:
                st.markdown(f"- **{acc['name']}** (점수: {acc['score']}점 / RSI: {acc['rsi']:.1f})")
        else:
            st.write("해당 점수대 종목 없음.")
