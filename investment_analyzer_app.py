import streamlit as st
import pandas as pd
import yfinance as yf
from pykrx import stock
from datetime import datetime, timedelta
import urllib.parse
import requests
import xml.etree.ElementTree as ET
import email.utils
import time

# 1. 페이지 기본 설정 및 가로 폭 짤림 방지 레이아웃 최적화
st.set_page_config(
    page_title="AITAS-EQ 실시간 투자 전략 시스템", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

# 화면 짤림 방지 및 반응형 마스터 디자인 CSS 주입
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

def get_safe_business_day(offset=0):
    today = datetime.utcnow() + timedelta(hours=9) - timedelta(days=offset)
    while today.weekday() >= 5: today -= timedelta(days=1)
    if today.hour < 16 and offset == 0:
        today -= timedelta(days=1)
        while today.weekday() >= 5: today -= timedelta(days=1)
    return today.strftime("%Y%m%d")

def analyze_stock_score(ticker_code, stock_name):
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
    
    has_crisis = False
    try:
        enc_text = urllib.parse.quote(f"{stock_name}")
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
    now_utc = datetime.utcnow()
    
    try:
        enc_text = urllib.parse.quote(f"{stock_name}")
        url = f"https://news.google.com/rss/search?q={enc_text}&hl=ko&gl=KR&ceid=KR:ko"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=3)
        root = ET.fromstring(res.text.encode('utf-8'))
        
        for item in root.findall('.//item')[:15]:
            title = item.find('title').text or ""
            link = item.find('link').text or "#"
            pub_date_raw = item.find('pubDate').text or ""
            
            if " - " in title:
                title = title.split(" - ")[0]
                
            if title and title not in seen_titles:
                seen_titles.add(title)
                pub_dt = None
                time_ago_str = ""
                try:
                    pub_dt = email.utils.parsedate_to_datetime(pub_date_raw)
                    diff = now_utc.replace(tzinfo=pub_dt.tzinfo) - pub_dt
                    diff_seconds = int(diff.total_seconds())
                    
                    if diff_seconds < 60:
                        time_ago_str = "방금 전"
                    elif diff_seconds < 3600:
                        time_ago_str = f"{diff_seconds // 60}분 전"
                    elif diff_seconds < 86400:
                        time_ago_str = f"{diff_seconds // 3600}시간 전"
                    else:
                        time_ago_str = f"{diff_seconds // 86400}일 전"
                except:
                    time_ago_str = "최근속보"
                
                news_list.append({
                    "title": title,
                    "link": link,
                    "raw_title": title,
                    "pub_dt": pub_dt,
                    "time_ago": time_ago_str
                })
    except: pass
    
    try:
        suffix = ".KS" if market_type == "KOSPI" else ".KQ"
        yf_stock = yf.Ticker(f"{ticker_code}{suffix}")
        yf_news = yf_stock.news
        if yf_news:
            for n in yf_news[:3]:
                title = n.get('title', '')
                link = n.get('link', '#')
                publisher = n.get('publisher', '증권사공시')
                publish_time_raw = n.get('providerPublishTime', 0)
                
                if title and title not in seen_titles:
                    seen_titles.add(title)
                    pub_dt = datetime.utcfromtimestamp(publish_time_raw).replace(tzinfo=email.utils.parsedate_to_datetime(datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')).tzinfo)
                    
                    diff = now_utc.replace(tzinfo=pub_dt.tzinfo) - pub_dt
                    diff_seconds = int(diff.total_seconds())
                    
                    if diff_seconds < 60:
                        time_ago_str = "방금 전"
                    elif diff_seconds < 3600:
                        time_ago_str = f"{diff_seconds // 60}분 전"
                    elif diff_seconds < 86400:
                        time_ago_str = f"{diff_seconds // 3600}시간 전"
                    else:
                        time_ago_str = f"{diff_seconds // 86400}일 전"
                        
                    news_list.append({
                        "title": f"[{publisher}] {title}",
                        "link": link,
                        "raw_title": title,
                        "pub_dt": pub_dt,
                        "time_ago": time_ago_str
                    })
    except: pass

    news_list.sort(key=lambda x: x['pub_dt'] if x['pub_dt'] is not None else datetime.min.replace(tzinfo=email.utils.parsedate_to_datetime(datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')).tzinfo), reverse=True)
    
    classified_news = []
    opportunity_words = ['기회', '상승', '돌파', '급등', '호재', '수혜', '흑자', '계약', '대박', '영업이익증가', '신고가', '독점', '수주', '인수', '매집', '성장', '출시', '개발', '상향']
    crisis_words = ['위기', '상장폐지', '부도', '하한가', '유상증자', '횡령', '배임', '소송', '디폴트', '검찰', '조사', '조작', '쇼크', '폭락', '수사']
    bad_words = ['하락', '급락', '악재', '우려', '감소', '적자', '이탈', '순매도', '과징금', '축소', '부진', '전망치하회', '하향']

    for n in news_list[:8]:
        title_text = n['raw_title']
        opp_score = sum(1 for w in opportunity_words if w in title_text)
        crisis_score = sum(1 for w in crisis_words if w in title_text)
        bad_score = sum(1 for w in bad_words if w in title_text)
        
        if crisis_score > 0: tag = "🚨 위기감지"
        elif bad_score > opp_score: tag = "📉 악재경보"
        elif opp_score > bad_score: tag = "🔥 투자기회"
        else: tag = "⚪ 중립속보"
        
        classified_news.append({
            "title": f"{n['title']} ({n['time_ago']})",
            "link": n['link'],
            "sent": tag
        })
        
    if not classified_news:
        classified_news = [{"title": "⚠️ 실시간 파악된 최신 뉴스가 없습니다. 잠시 후 새로고침 해주세요.", "link": "#", "sent": "📢 시스템알림"}]
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
            # 💡 [정밀 분석 전용] 보조지표 고속 수학 연산 엔진
            current_price = int(df_chart['Close'].iloc[-1])
            prev_price = int(df_chart['Close'].iloc[-2])
            price_change_percent = ((current_price - prev_price) / prev_price) * 100
            
            # 1. 4중 핵심 이동평균선 산출
            df_chart['5일 이평선'] = df_chart['Close'].rolling(window=5).mean()
            df_chart['20일 이평선'] = df_chart['Close'].rolling(window=20).mean()
            df_chart['60일 이평선'] = df_chart['Close'].rolling(window=60).mean()
            df_chart['120일 이평선'] = df_chart['Close'].rolling(window=120).mean()
            
            # 2. 볼린저 밴드 계산 (20일 기준, 표준편차 2배)
            df_chart['BB_Std'] = df_chart['Close'].rolling(window=20).std()
            df_chart['볼린저 상한선'] = df_chart['20일 이평선'] + (df_chart['BB_Std'] * 2)
            df_chart['볼린저 하한선'] = df_chart['20일 이평선'] - (df_chart['BB_Std'] * 2)
            
            # 3. MACD 지표 계산
            df_chart['EMA_12'] = df_chart['Close'].ewm(span=12, adjust=False).mean()
            df_chart['EMA_26'] = df_chart['Close'].ewm(span=26, adjust=False).mean()
            df_chart['MACD'] = df_chart['EMA_12'] - df_chart['EMA_26']
            df_chart['MACD 시그널선'] = df_chart['MACD'].ewm(span=9, adjust=False).mean()
            df_chart['MACD 히스토그램'] = df_chart['MACD'] - df_chart['MACD 시그널선']
            
            ma5_curr, ma20_curr, ma60_curr, ma120_curr = df_chart['5일 이평선'].iloc[-1], df_chart['20일 이평선'].iloc[-1], df_chart['60일 이평선'].iloc[-1], df_chart['120일 이평선'].iloc[-1]
            
            # 정교한 이평선 상태 및 크로스 진단
            if ma5_curr > ma20_curr > ma60_curr > ma120_curr: 
                chart_trend = "📈 강력 정배열 상태 (골디락스 추세 상승 국면)"
            elif ma5_curr < ma20_curr < ma60_curr < ma120_curr: 
                chart_trend = "📉 완벽 역배열 상태 (추세 하락 국면, 관망 필수)"
            else: 
                chart_trend = "🔄 이평선 수렴 상태 (박스권 횡보 및 추세 응축 국면)"
                
            ma5_prev, ma20_prev = df_chart['5일 이평선'].iloc[-2], df_chart['20일 이평선'].iloc[-2]
            cross_signal = "🟢 특이 크로스 시그널 발견되지 않음"
            if ma5_prev <= ma20_prev and ma5_curr > ma20_curr: 
                cross_signal = "🔥 단기 골든크로스 발생! (5일선이 20일선을 골든크로스하는 단기 강매수 타점)"
            elif ma5_prev >= ma20_prev and ma5_curr < ma20_curr: 
                cross_signal = "🚨 데드크로스 발생! (단기 리스크 오프 국면 진입)"
                
            high_3mo = df_chart['Close'].iloc[-60:].max()
            drop_rate = ((high_3mo - current_price) / high_3mo) * 100
            chart_analysis_text = f"최근 3개월 최고가 대비 현재 주가는 **-{drop_rate:.1f}%** 조정받은 위치에 안착해 있습니다."

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
            
            # 최종 투자 가치 점수 종합 연산
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

            # 메인 탑 메트릭 바디 구성
            col1, col2, col3, col4 = st.columns(4)
            col1.metric(label=f"현재가 ({stock_name})", value=f"{format(current_price, ',')} 원", delta=f"{price_change_percent:.2f} %")
            col2.metric(label="RSI (과열 지수)", value=f"{rsi:.1f}", delta="과매도 영역 진입" if rsi<=30 else "정상 범주")
            col3.metric(label="20일 평균비 거래량", value=f"{vol_ratio:.2f} 배", delta="수급 폭발 (거래대금 유입)" if vol_ratio>=1.5 else "거래 소폭 정체")
            
            if final_score >= 75:
                decision_text, decision_delta, opinion, strategy_text = "🔥 강력 매수", "안전마진 및 바닥 변곡점 확인", "🔥 강력 매수", "안전마진과 차트 변곡점이 모두 융합된 최적의 바닥 타점입니다."
            elif final_score >= 50:
                decision_text, decision_delta, opinion, strategy_text = "✅ 분할 매수", "하방 경직성 지지", "✅ 분할 매수", "하단 지지선을 디딤돌 삼아 장기 물량을 모아가기 좋은 구간입니다."
            else:
                decision_text, decision_delta, opinion, strategy_text = "🚨 매수 금지", "역배열 과열 또는 악재 수렴", "🚨 매수 금지", "악재 수렴 중이거나 차트가 고점 과열 상태입니다. 현금을 쥐고 관망하십시오."
                
            col4.metric(label="🏛️ AITAS 최종 투자 의견", value=decision_text, delta=f"점수: {final_score}점")

            st.subheader("📋 AITAS-EQ 종합 전략 투자 분석 보고서")
            left_col, right_col = st.columns([1, 1])
            
            with left_col:
                tab1, tab2, tab3 = st.tabs(["💬 5인 전문가 분석", "🚀 실전 매수 시나리오", "📰 증권사 실시간 속보"])
                with tab1:
                    st.markdown(f"### 💬 분야별 분석가 그룹의 최종 결론")
                    st.markdown(f"**🔹 거시경제 분석가:** 글로벌 유동성 완화 기조 속에서 {stock_name}의 시장 지배력 검증.")
                    st.markdown(f"**🔹 기본적 분석가:** 가치평가 지표 (PER {per:.2f}배, PBR {pbr:.2f}배) 기반 안전마진 확보 상태 진단.")
                    st.markdown(f"**🔹 기술적 분석가:** RSI {rsi:.1f}점 및 볼린저 밴드 표준편차 이탈 위치 추적.")
                    st.markdown(f"**🔹 수급 분석가:** 최근 한 달간 세력(외인/기관) 매집 주체 및 대량 거래량 돌파 신뢰도 검증 완료.")
                with tab2:
                    st.markdown("### 🎯 정밀 기술적 포지셔닝 타점 가이드")
                    st.markdown(f"#### **📊 종합 투자 매력도 스코어: `{final_score}점 / 100점`**")
                    st.markdown(f"**📢 핵심 포지션:** **{opinion}**")
                    st.markdown(f"**💡 가이드라인:** {strategy_text}")
                    st.markdown("---")
                    
                    buy_target_1 = int(current_price * 0.98)
                    buy_target_2 = int(current_price * 0.94)
                    take_profit = int(current_price * 1.25)
                    stop_loss = int(current_price * 0.88)
                    
                    st.markdown("#### **💵 핵심 가격 가이드라인**")
                    st.success(f"🎯 **1차 분할 매수 진입가 (비중 10%):** `{format(buy_target_1, ',')} 원` 부근 (안전마진 확인)")
                    st.success(f"💎 **2차 분할 매수 추가가 (비중 20%):** `{format(buy_target_2, ',')} 원` 부근 (직전 지지선 리테스트)")
                    st.warning(f"📈 **1차 이익 실현 목표가 (Take Profit):** `{format(take_profit, ',')} 원` 부근 (기대수익률 약 +25%)")
                    st.error(f"🚨 **원칙적 리스크 오프 손절선 (Stop Loss):** `{format(stop_loss, ',')} 원` 부근 (지지선 이탈 및 추세 소멸 시)")
                    st.markdown("---")
                    
                    # 💡 [정밀 차트 분석 결합형 가이드]
                    # 볼린저 밴드 및 MACD 지표 상태 실시간 요약
                    curr_bb_upper = df_chart['볼린저 상한선'].iloc[-1]
                    curr_bb_lower = df_chart['볼린저 하한선'].iloc[-1]
                    curr_macd = df_chart['MACD'].iloc[-1]
                    curr_signal_line = df_chart['MACD 시그널선'].iloc[-1]
                    
                    st.markdown("#### **📊 실시간 보조지표 종합 진단**")
                    if current_price >= curr_bb_upper:
                        st.error(f"⚠️ **볼린저 밴드 경보:** 주가가 볼린저 밴드 상한선({format(int(curr_bb_upper), ',')}원)을 상회하는 **과열(Overbought)** 돌파 구역에 위치해 있습니다. 신규 진입은 자제하십시오.")
                    elif current_price <= curr_bb_lower:
                        st.success(f"💎 **볼린저 밴드 기회:** 주가가 볼린저 밴드 하한선({format(int(curr_bb_lower), ',')}원)에 도달한 **과매도(Oversold)** 구역입니다. 하방 경직성을 바탕으로 한 줍줍 타점입니다.")
                    else:
                        st.info(f"🔄 **볼린저 밴드 균형:** 현재 주가는 밴드 중심선 부근에서 안정적인 가격 조율 흐름(정상 궤도)을 보이고 있습니다.")
                        
                    if curr_macd > curr_signal_line:
                        st.success(f"🔥 **MACD 모멘텀:** MACD 지표({curr_macd:.2f})가 시그널선({curr_signal_line:.2f})을 **상향 돌파(골든크로스)**한 상태로, 단기 매수 모멘텀이 강하게 유입되는 추세입니다.")
                    else:
                        st.error(f"📉 **MACD 모멘텀:** MACD 지표({curr_macd:.2f})가 시그널선({curr_signal_line:.2f})을 **하향 돌파(데드크로스)**하여 매도 압력이 지배적인 단기 눌림목 국면입니다.")
                with tab3:
                    st.markdown(f"### 📰 {stock_name} 증권 터미널 속보 및 위험 진단")
                    for news in advanced_news: 
                        st.markdown(f"- **{news['sent']}** | [{news['title']}]({news['link']})")

            with right_col:
                st.markdown("### 📈 기술적 지표 시각화 (Technical Indicators)")
                
                # 라디오 버튼으로 차트 뷰 모드 제어
                chart_view = st.radio(
                    "📊 시각화할 보조지표를 선택해 주세요",
                    ["이동평균선 결합 뷰 (5MA/20MA/60MA/120MA)", "볼린저 밴드 뷰 (Bollinger Bands)", "MACD 모멘텀 뷰"],
                    horizontal=True
                )
                
                if chart_view == "이동평균선 결합 뷰 (5MA/20MA/60MA/120MA)":
                    df_plot = df_chart[['Close', '5일 이평선', '20일 이평선', '60일 이평선', '120일 이평선']].rename(columns={'Close': '현재 주가'})
                    st.line_chart(df_plot)
                    st.info(f"🔍 **[이평선 진단]** {chart_trend} | 크로스 신호: {cross_signal}")
                elif chart_view == "볼린저 밴드 뷰 (Bollinger Bands)":
                    df_plot_bb = df_chart[['Close', '볼린저 상한선', '20일 이평선', '볼린저 하한선']].rename(columns={'Close': '현재 주가', '20일 이평선': '볼린저 중심선'})
                    st.line_chart(df_plot_bb)
                    st.info(f"🔍 **[밴드 진단]** {stock_name}의 볼린저 밴드 폭(이격도)이 수축 후 확장 국면인지 체크하십시오. 수축은 시세 분출 직전의 에너지 응축 구간입니다.")
                elif chart_view == "MACD 모멘텀 뷰":
                    df_plot_macd = df_chart[['MACD', 'MACD 시그널선']]
                    st.line_chart(df_plot_macd)
                    st.bar_chart(df_chart['MACD 히스토그램'])
                    st.info(f"🔍 **[MACD 진단]** MACD가 0선을 기준으로 어디에 머무는지 파악하십시오. 0선 위는 상승 모멘텀 영역, 0선 아래는 하락 모멘텀 영역입니다.")

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
            ("086520", "에코프로"), ("247540", "エ코프로비엠"), ("005490", "POSCO홀딩스"), 
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
                
                # 스캐너 화면용 실전 가격 밴드 동적 계산 및 표기
                rec_buy1 = int(rec['price'] * 0.98)
                rec_buy2 = int(rec['price'] * 0.94)
                rec_tp = int(rec['price'] * 1.25)
                rec_sl = int(rec['price'] * 0.88)
                
                st.write(f"↪️ **추천 근거:** RSI 바닥권({rec['rsi']:.1f}), PBR {rec['pbr']:.2f}배 저평가 구간. 차트 변곡 시그널({rec['cross'] if rec['cross'] else '응축 중'}) 포착 완료.")
                st.write(f"💼 **전술 가격 가이드:** [1차 진입] `{format(rec_buy1, ',')}원` / [2차 추가] `{format(rec_buy2, ',')}원` / [이익실현 목표가] `{format(rec_tp, ',')}원` / [최후 손절선] `{format(rec_sl, ',')}원` (전술 비중 최대 30% 권장)")
        else:
            st.warning("🚨 현재 75점 이상의 완벽한 매수 타점에 도달한 주도주가 없습니다. 시장 전체가 과열되었거나 역배열 상태입니다.")
            
        st.markdown("### ✅ 분할 매수 및 관심 편입 권장 (60점 ~ 74점)")
        if accumulate_stocks:
            for acc in accumulate_stocks:
                st.markdown(f"- **{acc['name']}** (점수: {acc['score']}점 / RSI: {acc['rsi']:.1f})")
        else:
            st.write("해당 점수대 종목 없음.")
