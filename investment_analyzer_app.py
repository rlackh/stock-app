import streamlit as st
import pandas as pd
import yfinance as yf
from pykrx import stock
from datetime import datetime, timedelta
import urllib.parse
import requests
import xml.etree.ElementTree as ET

# 1. 페이지 기본 설정 (컴퓨터/태블릿/스마트폰 화면 자동 최적화)
st.set_page_config(page_title="AITAS-EQ 실시간 투자 전략 시스템", layout="wide", initial_sidebar_state="expanded")

st.title("🏛️ AITAS-EQ 실시간 개별 종목 투자 전략 시스템")
st.markdown("텔레그램 알림 종목 또는 6자리 코드를 입력하시면, 실시간 수급·차트·뉴스 로직을 결합하여 분석합니다.")

# 2. 사이드바 - 종목명 및 종목코드 통합 검색 창
st.sidebar.header("🔍 종목 검색 및 분석 조건")
ticker_input = st.sidebar.text_input("종목명 또는 6자리 종목코드를 입력하세요 (예: 삼성전자 또는 005930)", value="005930")

def get_safe_business_day(offset=0):
    """주말 및 휴장일을 피해 안전한 영업일 날짜를 계산하는 엔진"""
    today = datetime.utcnow() + timedelta(hours=9) - timedelta(days=offset)
    while today.weekday() >= 5:
        today -= timedelta(days=1)
    if today.hour < 16 and offset == 0:
        today -= timedelta(days=1)
        while today.weekday() >= 5:
            today -= timedelta(days=1)
    return today.strftime("%Y%m%d")

@st.cache_data(ttl=3600)
def load_fallback_db():
    """국내 서버 전면 차단 시 작동하는 코스피/코스닥 주요 대형주 초고속 마스터 맵"""
    return {
        "삼성전자": "005930", "SK하이닉스": "000660", "하이닉스": "000660",
        "NAVER": "035420", "네이버": "035420", "카카오": "035720",
        "현대차": "005380", "기아": "000270", "셀트리온": "068270",
        "LG에너지솔루션": "373220", "LG엔솔": "373220", "삼성바이오로직스": "207940",
        "삼바": "207940", "삼성전자우": "005935",
        "에코프로": "086520", "에코프로비엠": "247540", "포스코홀딩스": "005490",
        "POSCO홀딩스": "005490", "삼성SDI": "006400", "LG화학": "051910",
        "신한지주": "055550", "KB금융": "105560", "하나금융지주": "086790"
    }

@st.cache_data(ttl=60)
def find_stock_code_global(name_or_code):
    """[핵심 기능] 숫자가 입력되면 즉시 종목코드로 인식하고, 한글이 입력되면 코드로 상호 변환하는 무적의 검색 엔진"""
    query = str(name_or_code).strip().replace(" ", "").upper()
    
    # [기능 추가] 사용자가 6자리 숫자로 된 종목코드를 직접 입력한 경우
    if query.isdigit() and len(query) == 6:
        # 코스피(.KS) 시장인지 코스닥(.KQ) 시장인지 야후 파이낸스 글로벌 서버에 즉시 질의
        for suffix in [".KS", ".KQ"]:
            try:
                t = yf.Ticker(f"{query}{suffix}")
                if not t.history(period="1d").empty:
                    # 야후 검색에서 종목 한글명 혹은 영문명 추출 시도
                    stock_name = t.info.get('shortName', query)
                    market_type = "KOSPI" if suffix == ".KS" else "KOSDAQ"
                    return query, stock_name, market_type
            except:
                pass
        return query, query, "KOSPI"  # 판별 실패 시 기본 코스피 패스

    # 사용자가 한글/영문 종목명을 입력한 경우 (기존 검색 로직 유지)
    fallback_db = load_fallback_db()
    if query in fallback_db:
        code = fallback_db[query]
        return code, query, "KOSPI" if query not in ["에코프로", "에코프로비엠"] else "KOSDAQ"

    # 야후 파이낸스 글로벌 통합 검색 API 가동
    try:
        search_url = f"https://query2.finance.yahoo.com/v1/finance/search?q={urllib.parse.quote(query)}&quotesCount=10"
        headers = {'User-Agent': 'Mozilla/5.0'}
        s_res = requests.get(search_url, headers=headers, timeout=3).json()
        if 'quotes' in s_res and s_res['quotes']:
            for q in s_res['quotes']:
                symbol = q.get('symbol', '')
                if symbol.endswith('.KS') or symbol.endswith('.KQ'):
                    code = symbol.split('.')[0]
                    name = q.get('shortname', query)
                    market_type = "KOSPI" if symbol.endswith('.KS') else "KOSDAQ"
                    return code, name, market_type
    except:
        pass

    # 네이버 자동완성 API (최종 백업)
    try:
        query_enc = urllib.parse.quote(query)
        url = f"https://ac.finance.naver.com/ac?q={query_enc}&q_enc=utf-8&st=1&frm=stock&r_format=json&r_enc=utf-8"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=1.5).json()
        if res and 'items' in res and res['items'] and res['items'][0]:
            first_item = res['items'][0][0]
            mkt = 'KOSDAQ' if 'KOSDAQ' in first_item[4].upper() else 'KOSPI'
            return first_item[1], first_item[0], mkt
    except:
        pass
        
    return None, None, None

def get_naver_news(stock_name):
    news_list = []
    try:
        enc_text = urllib.parse.quote(stock_name + " 주가")
        url = f"https://news.naver.com/rss?keyword={enc_text}"
        root = ET.fromstring(requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2).text.encode('utf-8'))
        pos_words = ['상승', '돌파', '급등', '호재', '최고', '수혜', '흑자', '계약', '실적대박']
        neg_words = ['하락', '급락', '악재', '우려', '감소', '적자', '쇼크', '이탈', '순매도']
        for item in root.findall('.//item')[:5]:
            title = item.find('title').text or ""
            link = item.find('link').text or "#"
            score = sum(1 for pw in pos_words if pw in title) - sum(1 for nw in neg_words if nw in title)
            sent = "🟢 호재 성향" if score > 0 else ("🔴 악재 성향" if score < 0 else "⚪ 중립 기사")
            news_list.append({"title": title, "link": link, "sent": sent})
    except: pass
    return news_list

# 통합 검색 엔진 가동
ticker_code, stock_name, market_type = find_stock_code_global(ticker_input)

if not ticker_code:
    st.error("❌ 종목을 찾을 수 없습니다. 정확한 종목명이나 6자리 숫자 코드를 입력해 주세요.")
else:
    suffix = ".KS" if market_type == "KOSPI" else ".KQ"
    yf_ticker = f"{ticker_code}{suffix}"
    
    try:
        df_chart = yf.Ticker(yf_ticker).history(period="6mo")
    except:
        df_chart = pd.DataFrame()
    
    safe_date = get_safe_business_day()
    df_net_buy = pd.DataFrame()
    
    try:
        start_date = get_safe_business_day(offset=30)
        df_net_buy = stock.get_market_net_purchases_of_equities_by_ticker(start_date, safe_date, market_type)
    except: pass

    if df_chart.empty:
        st.error("🔄 데이터 동기화에 실패했습니다. 잠시 후 다시 검색해 주세요.")
    else:
        current_price = int(df_chart['Close'].iloc[-1])
        prev_price = int(df_chart['Close'].iloc[-2])
        price_change_percent = ((current_price - prev_price) / prev_price) * 100
        
        df_chart['MA5'] = df_chart['Close'].rolling(window=5).mean()
        df_chart['MA20'] = df_chart['Close'].rolling(window=20).mean()
        df_chart['MA60'] = df_chart['Close'].rolling(window=60).mean()
        
        ma5_curr, ma20_curr, ma60_curr = df_chart['MA5'].iloc[-1], df_chart['MA20'].iloc[-1], df_chart['MA60'].iloc[-1]
        
        if ma5_curr > ma20_curr > ma60_curr:
            chart_trend = "📈 강력 상승 정배열 상태 (정기적인 매수세 유입 중)"
        elif ma5_curr < ma20_curr < ma60_curr:
            chart_trend = "📉 하락 역배열 상태 (보수적 관점 유지 필요)"
        else:
            chart_trend = "🔄 이평선 밀집 및 혼조세 (박스권 횡보 구간)"
            
        ma5_prev, ma20_prev = df_chart['MA5'].iloc[-2], df_chart['MA20'].iloc[-2]
        cross_signal = "🟢 특이 매수/매도 시그널 없음"
        if ma5_prev <= ma20_prev and ma5_curr > ma20_curr:
            cross_signal = "🔥 골든크로스 발생! (단기 강력 매수 신호)"
        elif ma5_prev >= ma20_prev and ma5_curr < ma20_curr:
            cross_signal = "🚨 데드크로스 발생! (당분간 리스크 관리 권장)"
            
        high_3mo = df_chart['Close'].iloc[-60:].max()
        drop_rate = ((high_3mo - current_price) / high_3mo) * 100
        chart_analysis_text = f" 최근 3개월 최고가({format(int(high_3mo), ',')}원) 대비 현재 주가는 **-{drop_rate:.1f}%** 조정받은 위치에 있습니다."

        per, pbr, div = 0.0, 0.0, 0.0
        try:
            df_fund = stock.get_market_fundamental(safe_date, market="ALL")
            if not df_fund.empty and ticker_code in df_fund.index:
                per, pbr, div = df_fund.loc[ticker_code, 'PER'], df_fund.loc[ticker_code, 'PBR'], df_fund.loc[ticker_code, 'DIV']
        except: pass
        if per == 0.0:
            try:
                info = yf.Ticker(yf_ticker).info
                per, pbr, div = info.get('trailingPE', 0.0), info.get('priceToBook', 0.0), (info.get('dividendYield', 0.0) or 0.0) * 100.0
            except: pass
        
        delta = df_chart['Close'].diff()
        up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
        rsi = (100 - (100 / (1 + (up.ewm(com=13, adjust=False).mean() / down.ewm(com=13, adjust=False).mean())))).iloc[-1]
        vol_ratio = df_chart['Volume'].iloc[-1] / df_chart['Volume'].rolling(window=20).mean().iloc[-1]

        # 대시보드 화면 표기
        col1, col2, col3, col4 = st.columns(4)
        col1.metric(label=f"현재가 ({stock_name} / {ticker_code})", value=f"{format(current_price, ',')} 원", delta=f"{price_change_percent:.2f} %")
        col2.metric(label="RSI (차트 과열도)", value=f"{rsi:.1f}", delta="과매도 지점" if rsi<=30 else "안정")
        col3.metric(label="20일 평균 대비 거래량", value=f"{vol_ratio:.2f} 배", delta="수급 폭발" if vol_ratio>=1.5 else "정상")
        is_guru = (0 < per <= 15.0) and (pbr <= 1.8) and (div >= 1.5)
        col4.metric(label="가치주 요건 검증", value="🏛️ 통과" if is_guru else "❌ 미부합", delta="계산 완료")

        st.subheader("📋 AITAS-EQ 종합 전략 투자 분석 보고서")
        left_col, right_col = st.columns([1, 1])
        
        with left_col:
            tab1, tab2, tab3 = st.tabs(["💬 5인 전문가 토론", "🚀 실전 매수 타이밍", "📰 AI 뉴스 속보"])
            with tab1:
                st.markdown(f"### 💬 전문가 그룹의 핵심 논쟁")
                st.markdown(f"**🔹 거시경제 분석가:** 현재 거시 기조 속에서 {stock_name}의 업황 방어력을 진단해야 합니다.")
                st.markdown(f"**🔹 기본적 분석가:** 밸류에이션(PER {per:.2f}배, PBR {pbr:.2f}배) 자산 가치와 배당률({div:.2f}%)의 하방 경직성을 점검하십시오.")
                st.markdown(f"**🔹 기술적 분석가:** RSI {rsi:.1f} 점으로 단기 추세 왜곡을 바로잡는 변곡점입니다.")
                st.markdown(f"**🔹 리스크 관리자:** 기관 및 외인의 자금 이동 추이를 실시간 거래대금과 비교 분석해야 안전합니다.")
            with tab2:
                st.markdown("### 🎯 실전 매수/매도 타이밍 제안")
                base_score = 40
                if rsi < 40: base_score += 20
                if vol_ratio >= 1.5: base_score += 20
                if is_guru: base_score += 20
                st.markdown(f"#### **📊 AITAS-EQ 투자 매력도 점수: `{base_score}점 / 100점`**")
                if base_score >= 80: opinion, strategy_text = "🔥 강력 매수", "가격 메리트와 기술적 바닥 시그널이 융합된 최적의 타이밍입니다."
                elif base_score >= 60: opinion, strategy_text = "✅ 분할 매수", "안전마진이 확보된 영역으로, 하단 지지선을 디딤돌 삼아 모아가기 좋습니다."
                else: opinion, strategy_text = "⚠️ 관망 및 보유", "밸류에이션 매력도가 낮거나 단기 매수세가 과열되었습니다. 추격 매수를 금합니다."
                st.info(f"**최종 투자 의견:** {opinion}\n\n**전략 코멘트:** {strategy_text}")
                support_price, target_price, stop_loss = int(current_price * 0.95), int(current_price * 1.25), int(current_price * 0.90)
                st.success(f"🎯 **추천 분할 매수 타점:** {format(support_price, ',')} 원 부근")
                st.warning(f"📈 **1차 목표 이익 실현가:** {format(target_price, ',')} 원")
                st.error(f"🚨 **원칙적 리스크 손절선:** {format(stop_loss, ',')} 원")
            with tab3:
                st.markdown(f"### 📰 {stock_name} 관련 실시간 뉴스 속보")
                news_data = get_naver_news(stock_name)
                if news_data:
                    for news in news_data: st.markdown(f"- **{news['sent']}** | [{news['title']}]({news['link']})")
                else: st.write("🔍 최신 뉴스를 불러오는 중이거나 장 마감 후 정산 중입니다.")

        with right_col:
            st.markdown("### 📈 주가 흐름 및 세력(외인/기관) 수급 트렌드")
            st.caption("🔹 최근 3개월 주가 추이")
            st.line_chart(df_chart['Close'])
            
            st.info(f"🔍 **[AITAS 차트 진단 리포트]**\n\n* **현재 추세:** {chart_trend}\n* **이평선 변곡 신호:** {cross_signal}\n* **가격 조정 상태:** {chart_analysis_text}")
            
            st.caption("🔹 최근 1달간 세력(외인/기관) 매수 누적 금액 현황")
            if not df_net_buy.empty and ticker_code in df_net_buy.index:
                foreign_buy = df_net_buy.loc[ticker_code, '외국인합계'] / 100000000
                institution_buy = df_net_buy.loc[ticker_code, '기관합계'] / 100000000
                c1, c2 = st.columns(2)
                c1.metric(label="👨‍🎤 외국인 한달 누적", value=f"{foreign_buy:.1f} 억 원", delta="매수 우위" if foreign_buy>0 else "매도 우위")
                c2.metric(label="🏢 기관 한달 누적", value=f"{institution_buy:.1f} 억 원", delta="매수 우위" if institution_buy>0 else "매도 우위")
            else:
                st.warning("⚠️ 세력 수급 금액은 평일 장중에 실시간으로 집계되어 표기됩니다.")
