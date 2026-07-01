import os
import requests
import re
import sys
import pandas as pd
import yfinance as yf
import numpy as np
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
import urllib.parse
import xml.etree.ElementTree as ET

# 💡 Streamlit 실행 여부 감지 레이어 (Dual-Mode 지원)
try:
    import streamlit as st
    from streamlit.runtime.scriptrunner import get_script_run_ctx
    is_streamlit = get_script_run_ctx() is not None
except ImportError:
    is_streamlit = False

# 텔레그램 보안 설정값 (깃허브 Secrets 및 로컬 환경변수 연동)
TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

# ==========================================
# 📊 1. 공통 금융 매크로 및 실시간 검색 모듈
# ==========================================
def get_macro_safety_score():
    """
    미국 10년물 국채 금리(^TNX)와 원/달러 환율(USDKRW=X)의 20일 이동평균 이탈도를 추적하여 시장 환경 점수를 산출합니다.
    """
    try:
        fx_data = yf.Ticker("USDKRW=X").history(period="1mo", timeout=5)
        bond_data = yf.Ticker("^TNX").history(period="1mo", timeout=5)
        
        if fx_data.empty or bond_data.empty:
            return 70, "매크로 API 수신 불안정 (기본 안전 점수 부여)"
            
        fx_data = fx_data.dropna()
        bond_data = bond_data.dropna()
        
        curr_fx = float(fx_data['Close'].iloc[-1])
        ma20_fx = float(fx_data['Close'].rolling(window=20).mean().iloc[-1])
        
        curr_bond = float(bond_data['Close'].iloc[-1])
        ma20_bond = float(bond_data['Close'].rolling(window=20).mean().iloc[-1])
        
        safety_score = 100
        
        if curr_fx > ma20_fx:
            safety_score -= 25
            if curr_fx > fx_data['Close'].rolling(window=5).mean().iloc[-1]:
                safety_score -= 10
                
        if curr_bond > ma20_bond:
            safety_score -= 25
            if curr_bond > bond_data['Close'].rolling(window=5).mean().iloc[-1]:
                safety_score -= 10
                
        if safety_score >= 80:
            comment = "🟢 [글로벌 자금 유입기] 환율과 금리가 하방 안정세를 보이며, 기관/외인 자금 수급 유입에 아주 우호적인 바다입니다."
        elif safety_score >= 50:
            comment = "🟡 [변동성 박스권 장세] 환율 또는 금리 중 하나의 변동성이 존재하므로, 무리한 배팅을 자제하고 철저한 분할 진입이 권장됩니다."
        else:
            comment = "🚨 [매크로 위험 경보] 글로벌 유동성이 수축하고 외인 환차손 회피 투매가 출회될 수 있는 폭풍우 장세입니다. 보수적 운용 및 현금 확보를 적극 권장합니다."
            
        return safety_score, f"현재 환율: {curr_fx:.1f}원 (20MA 대비 {'높음' if curr_fx > ma20_fx else '낮음'}) | 미10년 국채금리: {curr_bond/10:.2f}% (20MA 대비 {'높음' if curr_bond > ma20_bond else '낮음'})\n{comment}"
    except Exception as e:
        return 70, f"매크로 분석 레이어 임시 지연: {str(e)} (기본 안전 점수 우회 부여)"

if is_streamlit:
    @st.cache_data(ttl=120)
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
                    code = item[0][1]
                    name = item[0][0]
                    if code.isdigit() and len(code) == 6: results[code] = name
        except: pass
        if not results:
            fallback = {"삼성전자": "005930", "SK하이닉스": "000660", "한미반도체": "042700", "HD현대일렉트릭": "043200"}
            for f_name, f_code in fallback.items():
                if clean_q in f_name.upper(): results[f_code] = f_name
        return [{"name": name, "code": code} for code, name in results.items()]
else:
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
                    code = item[0][1]
                    name = item[0][0]
                    if code.isdigit() and len(code) == 6: results[code] = name
        except: pass
        return [{"name": name, "code": code} for code, name in results.items()]

def find_stock_code_global_portal(name_or_code):
    query = str(name_or_code).strip()
    if query.isdigit() and len(query) == 6: return query, query
    portal_res = 통합_포털_종목_검색(query)
    if portal_res: return portal_res[0]['code'], portal_res[0]['name']
    return "005930", "삼성전자"

def get_market_candidates():
    """네이버 금융에서 양대 시장 시총 상위 40개씩, 총 80개 후보 종목 수집"""
    headers = {'User-Agent': 'Mozilla/5.0'}
    candidates = []
    for sosok, suffix in [(0, '.KS'), (1, '.KQ')]:
        url = f"https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}"
        try:
            res = requests.get(url, headers=headers, timeout=10)
            res.encoding = 'euc-kr'
            matches = re.findall(r'href="/item/main\.naver\?code=(\d{6})"\s*class="tltle">([^<]+)</a>', res.text)
            
            count = 0
            for code, name in matches:
                name_clean = name.strip()
                forbidden = ['KODEX', 'TIGER', '레버리지', '인버스', 'ETN', '스팩', '선물', 'KBSTAR', 'SOL', 'ACE', 'HANARO']
                if any(w in name_clean for w in forbidden) or name_clean.endswith(('우', '우B', '우(전환)', '종종', '신')):
                    continue
                candidates.append((code, name_clean, suffix))
                count += 1
                if count >= 40:
                    break
        except:
            pass
    return candidates

# ==========================================
# 📈 2. 개별 종목 실시간 차트 분석용 연산 엔진
# ==========================================
def analyze_stock_live(ticker_code, stock_name):
    """
    특정 종목코드에 대한 실시간 현재가, 이평선 분석, RSI, 이격도 데이터를 수집합니다.
    """
    df_chart = pd.DataFrame()
    current_price = 0
    try:
        naver_live = f"https://finance.naver.com/item/main.naver?code={ticker_code}"
        res_live = requests.get(naver_live, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
        soup = BeautifulSoup(res_live.text, 'html.parser')
        no_today = soup.find('p', class_='no_today')
        if no_today: current_price = int(no_today.find('span', class_='blind').text.replace(',', ''))
    except: pass

    for sfx in [".KS", ".KQ"]:
        try:
            ticker_obj = yf.Ticker(f"{ticker_code}{sfx}")
            df_chart = ticker_obj.history(period="3mo", timeout=1.5)
            if not df_chart.empty: break
        except: pass
    
    if df_chart.empty:
        dates = pd.date_range(end=datetime.today(), periods=30)
        df_chart = pd.DataFrame({'Open': [current_price or 50000]*30, 'High': [current_price or 50000]*30, 'Low': [current_price or 50000]*30, 'Close': [current_price or 50000]*30, 'Volume': [100000]*30}, index=dates)
        
    if current_price == 0: current_price = int(df_chart['Close'].iloc[-1])
    df_chart['5MA'] = df_chart['Close'].rolling(window=5).mean().fillna(current_price)
    df_chart['20MA'] = df_chart['Close'].rolling(window=20).mean().fillna(current_price)
    df_chart['60MA'] = df_chart['Close'].rolling(window=60).mean().fillna(current_price)
    df_chart['Vol5'] = df_chart['Volume'].rolling(window=5).mean().fillna(100000)
    
    delta = df_chart['Close'].diff()
    up, down = delta.clip(lower=0), -1 * delta.clip(upper=0)
    up_ewm = up.ewm(com=13, adjust=False).mean().iloc[-1]
    down_ewm = down.ewm(com=13, adjust=False).mean().iloc[-1]
    rsi = 50.0 if down_ewm == 0 else (100 - (100 / (1 + (up_ewm / down_ewm))))
    
    ma20_last = df_chart['20MA'].iloc[-1]
    gap_ma20 = ((current_price - ma20_last) / ma20_last) * 100 if ma20_last > 0 else 0.0
    
    current_vol = df_chart['Volume'].iloc[-1]
    avg_vol_5d = df_chart['Vol5'].iloc[-2] if len(df_chart) > 1 else 100000
    vol_ratio = (current_vol / avg_vol_5d) if avg_vol_5d > 0 else 1.0
    
    return {
        "name": stock_name, "code": ticker_code, "price": current_price, 
        "rsi": rsi, "vol_ratio": vol_ratio, "ma20_gap": gap_ma20, "df": df_chart
    }

def get_live_news(stock_name):
    """실시간 속보 뉴스를 감성 분석 및 아웃링크 처리합니다."""
    news_list = []
    try:
        enc_text = urllib.parse.quote(f"{stock_name}")
        url = f"https://news.google.com/rss/search?q={enc_text}&hl=ko&gl=KR&ceid=KR:ko"
        res = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=2)
        root = ET.fromstring(res.text.encode('utf-8'))
        for item in root.findall('.//item')[:5]:
            title = item.find('title').text or ""
            link = item.find('link').text or "#"
            if " - " in title: title = title.split(" - ")[0]
            news_list.append({"title": title, "link": link})
    except: pass
    
    classified = []
    for n in news_list:
        t = n['title']
        if any(w in t for w in ['위기', '부도', '소송', '수사', '유상증자', '횡령', '배임', '디폴트']): tag, color = "🚨 [위기감지]", "#ff0000"
        elif any(w in t for w in ['하락', '급락', '악재', '우려', '부진', '감소', '적자']): tag, color = "📉 [악재경보]", "#ff6600"
        elif any(w in t for w in ['기회', '상승', '돌파', '급등', '호재', '수주', '대박', '어닝', '흑자']): tag, color = "🔥 [투자기회]", "#118822"
        else: tag, color = "⚪ [중립속보]", "#555555"
        classified.append({"tag": tag, "color": color, "title": t, "link": n['link']})
    return classified

# ==========================================
# 🔥 3. 주식 거래량 폭발/급증 스캐너 엔진
# ==========================================
def find_volume_surging_stocks():
    """
    네이버 양대 시장 상위 종목군을 전수 분석하여,
    당일 거래량이 직전 5일 평균 거래량 대비 폭발적으로 증가한 주도주를 골라냅니다.
    동시에 100억 원 거래대금 하한 필터를 적용하여 유동성이 확보된 알짜 대장주만 출력합니다.
    """
    candidates = get_market_candidates()
    surging_list = []
    
    progress_bar = None
    status_text = None
    if is_streamlit:
        status_text = st.empty()
        progress_bar = st.progress(0)
        
    for i, (code, name, suffix) in enumerate(candidates):
        if is_streamlit and progress_bar and status_text:
            progress_bar.progress((i + 1) / len(candidates))
            status_text.text(f"⚡ 실시간 거래량 폭발 스캔 중... ({name} - {i+1}/{len(candidates)})")
            
        try:
            ticker_symbol = f"{code}{suffix}"
            df = yf.Ticker(ticker_symbol).history(period="1mo", timeout=1.5)
            df = df.dropna()
            if len(df) < 10: continue
            
            df['Vol5'] = df['Volume'].rolling(window=5).mean()
            df = df.dropna()
            if len(df) < 2: continue
            
            current_price = float(df['Close'].iloc[-1])
            current_vol = float(df['Volume'].iloc[-1])
            avg_vol_5d = float(df['Vol5'].iloc[-2])
            
            # 💡 당일 거래대금 연산 (최소 100억 원 이상 조건)
            transaction_value = current_price * current_vol
            if transaction_value < 10_000_000_000:
                continue
                
            vol_ratio = current_vol / avg_vol_5d if avg_vol_5d > 0 else 1.0
            
            # 거래량이 평소 대비 최소 1.5배 이상 스파이크가 일어난 경우만 픽
            if vol_ratio >= 1.5:
                surging_list.append({
                    'code': code,
                    'name': name,
                    'price': int(current_price),
                    'vol_ratio': round(vol_ratio, 2),
                    'today_vol': int(current_vol),
                    'avg_vol_5d': int(avg_vol_5d),
                    't_value_b': round(transaction_value / 100000000, 1)
                })
        except:
            continue
            
    if is_streamlit and progress_bar and status_text:
        progress_bar.empty()
        status_text.empty()

    if not surging_list:
        return "조건에 부합하는 거래량 폭발 종목 없음", None
        
    # 거래량 배율이 높은 순으로 탑 10 정렬
    df_surging = pd.DataFrame(surging_list)
    df_surging = df_surging.sort_values(by='vol_ratio', ascending=False).head(10)
    return "성공", df_surging


# ==========================================
# 🛠️ DUAL-MODE 실행 분기 레이어 (Streamlit vs CLI)
# ==========================================
if is_streamlit:
    st.markdown("""
        <style>
        .stMarkdown, .stTable, div[data-testid="stMetricValue"], div[data-testid="stMetricLabel"], p, span, li {
            word-break: break-all !important;
            white-space: normal !important;
        }
        .block-container { padding: 1.5rem 2rem; max-width: 100% !important; }
        .report-box { padding: 1.2rem; border-radius: 8px; background-color: #f8f9fa; border-left: 5px solid #0f52ba; margin-bottom: 1rem; }
        .price-card { padding: 0.8rem; border-radius: 6px; text-align: center; color: white; font-weight: bold; font-size: 1.1rem; margin-bottom: 0.5rem; }
        .vol-card { padding: 1.2rem; border-radius: 10px; background-color: #fcfcfd; border: 1px solid #e2e8f0; border-top: 4px solid #ff4d4d; margin-bottom: 1rem; box-shadow: 0 4px 6px rgba(0,0,0,0.02); }
        .guide-box { padding: 1.2rem; border-radius: 8px; background-color: #f0f7ff; border-left: 5px solid #1a73e8; margin-top: 1rem; }
        .guide-title { font-weight: bold; color: #1a73e8; font-size: 1.1rem; margin-bottom: 0.5rem; }
        </style>
        """, unsafe_allow_html=True)

    # 1) 스트림릿 대시보드 모드로 가동할 때
    st.title("🏛️ AITAS-EQ 실시간 투자 전략 관제 시스템")
    st.write("20년 경력 운용역의 실시간 종목 분석기 및 수급 대량 거래량 돌파 포착 장치입니다.")
    
    # 탭 분리: 개별 정밀 차트 분석 vs 거래량 폭발 주도주
    tab_live, tab_vol_surge = st.tabs(["🔍 개별 종목 실시간 차트 분석", "🔥 실시간 거래량 폭발 주도주"])

    # ------------------------------------------
    # 탭 1: 개별 종목 실시간 차트 분석 & AI 타점 판독 (보존 및 차트 보는 법 가이드 추가)
    # ------------------------------------------
    with tab_live:
        st.sidebar.header("🎯 실시간 종목 관제")
        ticker_input = st.sidebar.text_input("분석할 종목명 또는 6자리 코드 입력", value="SK하이닉스")
        ticker_code, stock_name = find_stock_code_global_portal(ticker_input)
        
        res_live = analyze_stock_live(ticker_code, stock_name)
        
        col_l, col_r = st.columns([3, 2])
        
        with col_l:
            st.subheader(f"📈 {stock_name} ({ticker_code}) HTS급 실시간 차트 분석")
            df = res_live['df']
            
            import plotly.graph_objects as go
            
            # 💡 날짜를 문자열 포맷(YYYY-MM-DD)으로 변환하여 주말 공백을 제거하고 하루하루 표시합니다.
            x_dates = df.index.strftime('%Y-%m-%d')
            
            fig = go.Figure(data=[go.Candlestick(
                x=x_dates, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
                increasing_line_color='#e61919', decreasing_line_color='#1919e6', name="주가"
            )])
            fig.add_trace(go.Scatter(x=x_dates, y=df['5MA'], line=dict(color='orange', width=1.5), name='5일선'))
            fig.add_trace(go.Scatter(x=x_dates, y=df['20MA'], line=dict(color='purple', width=1.5), name='20일선'))
            fig.add_trace(go.Scatter(x=x_dates, y=df['60MA'], line=dict(color='green', width=1.5), name='60일선'))
            
            # 💡 Y축 가격에 '100,000' 천 단위 컴마를 적용하고, X축은 일별 카테고리 형태로 설정합니다.
            fig.update_layout(
                xaxis_rangeslider_visible=False, 
                height=410, 
                margin=dict(l=10, r=10, t=10, b=10),
                xaxis=dict(
                    type='category',
                    tickangle=-45,
                    tickmode='auto',
                    nticks=15,
                    tickfont=dict(size=10)
                ),
                yaxis=dict(
                    tickformat=','
                )
            )
            st.plotly_chart(fig, use_container_width=True)
            
            # 이평선 실시간 배열 추적
            c5, c20, c60 = df['5MA'].iloc[-1], df['20MA'].iloc[-1], df['60MA'].iloc[-1]
            curr_p = res_live['price']
            
            if c5 > c20 > c60:
                status = "🔥 [적극 매수 권장] 완벽한 급등형 정배열 차트"
                status_color = "#118822"
                why_text = "5일선, 20일선, 60일선이 나란히 부채꼴로 펼쳐지는 강세장 차트입니다. 단기 수급이 장기 매물벽을 완전히 뚫어냈으므로 장대양봉 후 눌림목 지지를 줄 때 매수해야 하는 정석 타점입니다."
            elif c5 < c20 < c60:
                status = "🚨 [매수 금지 위기] 하향 역배열 및 낙뢰 차트"
                status_color = "#ff0000"
                why_text = "주가 위로 겹겹이 쌓인 장기 이평선들이 매물 감옥을 형성하고 있습니다. 기술적 반등이 나와도 탈출하려는 기존 주주들의 투매 물량이 쏟아지니 차트가 돌아설 때까지 절대 사면 안 됩니다."
            else:
                status = "🔄 [관망 후 선점 가능] 이평선 수렴형 에너지 응축 구간"
                status_color = "#0f52ba"
                why_text = "단기선과 장기선이 한 군데로 꼬이면서 폭발적 상방 혹은 하방 변곡점을 준비하는 지점입니다. 거래량이 붙으며 전고점 캔들을 장악하는 날이 바로 선행 매수의 최적기입니다."

            st.markdown(f"""
            <div class='report-box' style='border-left-color: {status_color};'>
                <h4 style='margin-top:0; color:{status_color};'>{status}</h4>
                <strong>20년 운용역 차트 종합 판정:</strong><br>{why_text}<br><br>
                <strong>보조 지표 결과:</strong> RSI {res_live['rsi']:.1f}점 / 20일선 이격도 {res_live['ma20_gap']:.1f}% / 수급 비율 {res_live['vol_ratio']:.1f}배
            </div>
            """, unsafe_allow_html=True)

            # 🏛️ 20년 경력 운용역의 HTS 차트 도해 및 기술적 지표 해독 비법서 (교육용 세션)
            with st.expander("🏛️ 20년 경력 운용역의 차트 해독 비법서 (HTS 차트 보는 방법)", expanded=True):
                st.markdown("""
                스마트폰이나 HTS로 차트를 켜놓고도 정작 어디를 보고 매수 결정을 내려야 할지 몰라 헤매셨다면, 딱 **4가지 핵심 프레임**만 머릿속에 기억하십시오. 
                
                ---
                
                ### ① 봉차트(캔들스틱)의 시각적 언어 해석법
                캔들의 빨간색(양봉)과 파란색(음봉)은 당일 시장 참가자들의 치열한 심리 전쟁 결과입니다.
                *   **몸통의 길이:** 몸통이 길수록(장대양봉/장대음봉) 한쪽 방향의 지배력이 매우 강력함을 뜻합니다.
                *   **윗꼬리가 긴 캔들:** 장 초반 급등했다가 매도 투매물량을 맞고 주저앉은 모양새입니다. 특히 고가권에서 대량 거래량과 함께 발생한 긴 윗꼬리는 세력이 개인에게 물량을 떠넘긴 **'설거지 신호'**일 확률이 높습니다.
                *   **밑꼬리가 긴 캔들:** 장중 폭락했으나 마감 직전 누군가 막대한 자금력으로 주가를 아래에서 전부 받아내며 끌어올린 흔적입니다. **'저점 매집 신호'**로 해석됩니다.
                
                ### ② 이동평균선(이평선)의 배열 구조와 지지선 역할
                이동평균선은 해당 기간 동안 투자자들이 주식을 산 **'평균 매입 단가'**입니다.
                *   **5일선(주황색):** 일주일 동안의 평균 단가이며 주가의 '단기 엔진 방향'을 뜻합니다. 급등주는 5일선을 절대로 깨지 않고 타고 갑니다.
                *   **20일선(보라색):** 한 달간의 평균 단가이자 시장의 **'생명선'**입니다. 주가가 조정을 받을 때 20일선 부근에서 튕겨 올라가는 지지력(눌림목 타점)을 보여주어야 추세가 살아있다고 판단합니다.
                *   **정배열(Bullish):** 주가 > 5일선 > 20일선 > 60일선 순서로 부채꼴 모양 우상향하는 정배열 상태에서는 이평선이 강력한 지지대 역할을 하므로 매수하기 가장 안전합니다.
                *   **역배열(Bearish):** 반대로 이평선이 거꾸로 뒤집힌 하락세에서는 올라갈 때마다 위에 물려있던 매물 벽이 폭탄으로 쏟아지니 매수를 절대 피해야 합니다.
                
                ### ③ 거래량 분석: 주가 상승의 유일한 휘발유
                거래량은 자금력이 막강한 기관과 외국인 세력(주포)들이 절대 감출 수 없는 유일한 흔적입니다.
                *   주가가 횡보하다가 5일 평균 거래량 대비 **1.5배~3배 이상 급증하는 돌파 거래량**이 실리면서 양봉을 그리는 날은 세력이 개입하여 시세를 상방으로 제어하기 시작한 **'첫 신호탄'**입니다.
                
                ### ④ 보조 지표(RSI 및 이격도)의 균형 잡기
                *   **RSI (상대강도지수):** 30에 가까워지면 시장 참가자들이 과도한 공포에 질려 던진 **'과매도(바닥 매수 기회)'**, 70에 가까워지면 탐욕에 찌든 **'과열(매도 및 차익실현 준비)'**을 의미합니다.
                *   **이격도(MA Gap):** 현재 주가가 생명선인 20일선과 얼마나 동떨어져 있는지 측정하는 지표입니다. 이격도가 +10% 이상 벌어지면 이평선으로 회귀하려는 본능 때문에 주가가 급락하기 쉬우므로, 반드시 이격도가 좁혀지는 **눌림목(이격도 0% 내외) 영역**에서 매수해야 합니다.
                """)
            
        with col_r:
            st.subheader("💰 실시간 보정 타점 가이드")
            st.markdown("정밀 크롤링된 실시간 현재가에 기반하여 리스크 비율이 수학적으로 계산된 가격 가이드라인입니다.")
            
            # 실시간 현재가 기반 수학적 정밀 보정
            live_buy_price = int(curr_p * 0.985)
            live_stop_price = int(curr_p * 0.94)
            
            st.markdown(f"<div class='price-card' style='background-color:#118822;'>🎯 실시간 추천 매수진입가<br>{format(live_buy_price, ',')} 원 이하 (눌림목 유효)</div>", unsafe_allow_html=True)
            st.markdown(f"<div class='price-card' style='background-color:#ff0000;'>🚨 실시간 기계적 손절가<br>{format(live_stop_price, ',')} 원 (바닥 이탈 기준선)</div>", unsafe_allow_html=True)
            
            st.metric(label="현재 시장 연동 실시간 주가", value=f"{format(curr_p, ',')} 원")
            st.metric(label="20일선 이격도 (MA Gap)", value=f"{res_live['ma20_gap']:.1f} %")
            st.metric(label="RSI (과열 지표)", value=f"{res_live['rsi']:.1f} 점")
            
            st.markdown("---")
            st.subheader("🔬 실시간 뉴스 속보 감성 필터")
            news_data = get_live_news(stock_name)
            if news_data:
                for n in news_data:
                    st.markdown(f"<span style='color:{n['color']}; font-weight:bold;'>{n['tag']}</span> [<span style='text-decoration:underline;'>{n['title']}</span>]({n['link']})", unsafe_allow_html=True)
            else:
                st.write("⚪ 연동된 실시간 속보 기사가 존재하지 않습니다.")

    # ------------------------------------------
    # 탭 2: 주식 거래량 폭발 움직임이 있는 종목들 (수급 분출 TOP 10)
    # ------------------------------------------
    with tab_vol_surge:
        st.subheader("🔥 실시간 거래량 폭발 주도주 랭킹 TOP 10")
        st.write("당일 거래금액이 최소 **100억 원 이상** 수혈되며, 지난 5일 평균 거래량 대비 **가장 강력한 급증 돌파**가 일어난 실시간 알짜 종목들입니다.")
        
        # 10분 캐싱을 통해 Streamlit 리프레시 속도를 0.1초 수준으로 최적화
        @st.cache_data(ttl=600)
        def cached_volume_surge_scan():
            return find_volume_surging_stocks()
            
        m_score, m_report = get_macro_safety_score()
        status_msg, df_surge = cached_volume_surge_scan()
        
        col_m1, col_m2 = st.columns([1, 2])
        with col_m1:
            st.metric(label="📊 글로벌 매크로 안전성 스코어", value=f"{m_score} 점")
        with col_m2:
            st.info(f"**매크로 모니터링 분석 리포트:**\n{m_report}")
            
        st.markdown("---")
        
        if df_surge is not None and not df_surge.empty:
            cols = st.columns(2)
            
            for idx, row in df_surge.reset_index(drop=True).iterrows():
                target_col = cols[0] if idx % 2 == 0 else cols[1]
                
                with target_col:
                    st.markdown(f"""
                    <div class='vol-card'>
                        <h3 style='margin:0; color:#ff4d4d;'>🏅 {idx+1}위. {row['name']} ({row['code']})</h3>
                        <h4 style='margin: 8px 0; color:#0f52ba;'>⚡ 거래량 분출 비율: [ {row['vol_ratio']}배 폭발 ]</h4>
                        <hr style='margin:8px 0; border:none; border-top:1px solid #e2e8f0;'>
                        <ul style='font-size:0.92rem; padding-left:20px; color:#333; margin:0;'>
                            <li>현재가: <b>{format(row['price'], ',')}원</b></li>
                            <li>당일 거래대금: <b>{row['t_value_b']}억 원</b> (100억 기준 통과)</li>
                            <li>당일 거래량: <b>{format(row['today_vol'], ',')} 주</b></li>
                            <li>5일 평균 거래량: <b>{format(row['avg_vol_5d'], ',')} 주</b></li>
                        </ul>
                    </div>
                    """, unsafe_allow_html=True)
                    
            # 텔레그램 경보 수동 발송 장치
            st.markdown("---")
            st.subheader("📡 수동 거래량 폭발 경보 전송 제어")
            if st.button("📨 현재 거래량 급증 TOP 10 텔레그램 발송", type="primary"):
                if not TOKEN or not CHAT_ID:
                    st.error("❌ 텔레그램 토큰(TELEGRAM_TOKEN) 또는 채널 ID(TELEGRAM_CHAT_ID) 환경변수가 세팅되지 않았습니다.")
                else:
                    kst_time = datetime.utcnow() + timedelta(hours=9)
                    now_str = kst_time.strftime("%Y-%m-%d %H:%M")
                    msg = f"🔥 [AITAS-EQ] 실시간 거래량 폭발 돌파 TOP 10\\n({now_str} 기준)\\n\\n"
                    msg += f"📊 매크로 스코어: 💯 {m_score}점\\n{m_report}\\n\\n"
                    msg += "----------------------------------------\\n\\n"
                    
                    for idx, row in df_surge.reset_index(drop=True).iterrows():
                        msg += f"🏅 {idx+1}위. ★ {row['name']} ★\\n"
                        msg += f"  ▪ 수급 분출 배율: [ {row['vol_ratio']}배 폭발 ]\\n"
                        msg += f"  ▪ 당일 거래대금: {row['t_value_b']}억 원\\n"
                        msg += f"  ▪ 현재 가격: {format(row['price'], ',')}원\\n\\n"
                        
                    try:
                        res = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
                        if res.status_code == 200:
                            st.success("✅ 텔레그램 수급 경보가 성공적으로 발송되었습니다!")
                        else:
                            st.error(f"❌ 발송 실패 (상태 코드: {res.status_code}) - {res.text}")
                    except Exception as e:
                        st.error(f"❌ 전송 에러 발생: {e}")
                        
        else:
            st.warning(f"⚠️ 현재 조건에 부합하는 수급 분출 종목이 없습니다. ({status_msg})")

else:
    # 2) 깃허브 액션 배포(CLI 콘솔 모드)로 가동할 때 자동으로 텔레그램 메시지 전송
    m_score, m_report = get_macro_safety_score()
    status_msg, df_surge = find_volume_surging_stocks()
    kst_time = datetime.utcnow() + timedelta(hours=9)
    now_str = kst_time.strftime("%Y-%m-%d %H:%M")
    
    msg = f"🔥 [AITAS-EQ] 실시간 거래량 폭발 돌파 TOP 10\\n({now_str} 기준)\\n\\n"
    msg += f"📊 [1] 실시간 매크로 스코어: 💯 {m_score}점 / 100점\\n{m_report}\\n\\n"
    msg += "----------------------------------------\\n\\n"
    
    if df_surge is not None and not df_surge.empty:
        msg += "🏆 [2] 당일 거래대금 100억↑ 거래량 폭발 TOP 10\\n\\n"
        for idx, row in df_surge.reset_index(drop=True).iterrows():
            msg += f"🏅 {idx+1}위. ★ {row['name']} ★\\n"
            msg += f"  ▪ 수급 분출 배율: [ {row['vol_ratio']}배 폭발 ]\\n"
            msg += f"  ▪ 당일 거래대금: {row['t_value_b']}억 원\\n"
            msg += f"  ▪ 현재 가격: {format(row['price'], ',')}원\\n\\n"
    else:
        msg += f"⚠️ 현재 조건에 부합하는 종목이 없습니다. (상태: {status_msg})"
        
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("[시스템] 텔레그램 거래량 돌파 리포트 발송 완료!")
    except Exception as e:
        print("전송 에러:", e)
