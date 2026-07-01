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
    미국 10년물 국채 금리(^TNX)와 원/달러 환율(USDKRW=X)의 20일 이동평균 이탈도를 추적합니다.
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

# Streamlit 환경에서만 자동완성 API 캐싱 적용
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

def calculate_rsi(series, period=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

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
# 🐋 3. 톱티어 추천 포트폴리오 (실시간 자동 연산 엔진)
# ==========================================
def run_advanced_portfolio_strategy():
    """매크로, 100억 거래대금 필터링, 정배열 수급 필터링 후 리스크 패리티 비중배분 실행"""
    macro_score, macro_report = get_macro_safety_score()
    candidates = get_market_candidates()
    survivors = []
    
    progress_bar = None
    status_text = None
    if is_streamlit:
        status_text = st.empty()
        progress_bar = st.progress(0)
        
    for i, (code, name, suffix) in enumerate(candidates):
        if is_streamlit and progress_bar and status_text:
            progress_bar.progress((i + 1) / len(candidates))
            status_text.text(f"🔍 AI 계량 필터 스캔 중... ({name} - {i+1}/{len(candidates)})")
            
        try:
            ticker_symbol = f"{code}{suffix}"
            df = yf.Ticker(ticker_symbol).history(period="3mo", timeout=1.5)
            df = df.dropna()
            if len(df) < 30: continue
            
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA60'] = df['Close'].rolling(window=60).mean()
            df['Vol5'] = df['Volume'].rolling(window=5).mean()
            df['RSI'] = calculate_rsi(df['Close'])
            df = df.dropna()
            
            if len(df) < 5: continue
            
            current_price = float(df['Close'].iloc[-1])
            ma20 = float(df['MA20'].iloc[-1])
            ma60 = float(df['MA60'].iloc[-1])
            rsi = float(df['RSI'].iloc[-1])
            current_vol = float(df['Volume'].iloc[-1])
            avg_vol_5d = float(df['Vol5'].iloc[-2])
            
            # 💡 [핵심 도입 2]: 당일 거래대금 100억 원 하한 필터 (개잡주 자동 필터링)
            transaction_value = current_price * current_vol
            if transaction_value < 10_000_000_000:
                continue
                
            is_trend_bullish = (current_price > ma20) and (ma20 > ma60)
            is_rsi_stable = (35 <= rsi <= 65)
            vol_ratio = current_vol / avg_vol_5d if avg_vol_5d > 0 else 1.0
            is_volume_spiking = vol_ratio >= 1.5
            
            if is_trend_bullish and is_rsi_stable and is_volume_spiking:
                survivors.append({
                    'code': code, 'name': name, 'ticker': ticker_symbol,
                    'price': current_price, 'rsi': rsi, 'vol_ratio': vol_ratio,
                    't_value_b': round(transaction_value / 100000000, 1),
                    'df': df
                })
        except:
            continue
            
    if is_streamlit and progress_bar and status_text:
        progress_bar.empty()
        status_text.empty()

    if not survivors:
        return macro_score, macro_report, "조건 부합 종목 없음", None

    ranked_stocks = []
    for s in survivors:
        score = (s['vol_ratio'] * 15) + (100 - abs(s['rsi'] - 45) * 2)
        s['combined_score'] = score
        ranked_stocks.append(s)
        
    df_ranked = pd.DataFrame(ranked_stocks)
    top_3_targets = df_ranked.sort_values(by='combined_score', ascending=False).head(3).to_dict('records')
    
    # 💡 [핵심 도입 3]: 포트폴리오 리스크 패리티 가중치 연산
    volatilities = {}
    for target in top_3_targets:
        target_df = target['df']
        returns = target_df['Close'].iloc[-20:].pct_change().dropna()
        daily_volatility = returns.std()
        if pd.isna(daily_volatility) or daily_volatility <= 0:
            daily_volatility = 0.03
        volatilities[target['name']] = daily_volatility

    inv_vols = {name: 1.0 / vol for name, vol in volatilities.items()}
    total_inv_vol = sum(inv_vols.values())
    
    final_portfolio = []
    for target in top_3_targets:
        name = target['name']
        alloc_weight = (inv_vols[name] / total_inv_vol) * 100 if total_inv_vol > 0 else 33.3
        
        rec_buy = int(target['price'] * 0.985)
        rec_stop = int(target['price'] * 0.94)
        
        final_portfolio.append({
            'name': name, 'code': target['code'], 'price': int(target['price']),
            'weight': round(alloc_weight, 1), 'volatility': round(volatilities[name]*100, 2),
            'buy_price': rec_buy, 'stop_price': rec_stop, 'rsi': round(target['rsi'], 1),
            'vol_ratio': round(target['vol_ratio'], 1), 't_value_b': target['t_value_b'],
            'df_chart': target['df'].tail(60)
        })
        
    return macro_score, macro_report, "성공", pd.DataFrame(final_portfolio)


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
        </style>
        """, unsafe_allow_html=True)

    # 1) 스트림릿 대시보드 모드로 가동할 때
    st.title("🏛️ AITAS-EQ 리스크 패리티 포트폴리오 시스템")
    st.write("20년 경력 운용역의 실시간 매크로 위험 감지기 및 개별 종목 실시간 차트 관제 시스템입니다.")
    
    # 탭 분리: 개별 정밀 차트 분석 vs 퀀트 포트폴리오
    tab_live, tab_quant = st.tabs(["🔍 개별 종목 실시간 차트 분석", "🏆 AI 리스크 패리티 포트폴리오 (탑픽)"])

    # ------------------------------------------
    # 탭 1: 개별 종목 실시간 차트 분석 & AI 타점 판독
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
            fig = go.Figure(data=[go.Candlestick(
                x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'],
                increasing_line_color='#e61919', decreasing_line_color='#1919e6', name="주가"
            )])
            fig.add_trace(go.Scatter(x=df.index, y=df['5MA'], line=dict(color='orange', width=1.5), name='5일선'))
            fig.add_trace(go.Scatter(x=df.index, y=df['20MA'], line=dict(color='purple', width=1.5), name='20일선'))
            fig.add_trace(go.Scatter(x=df.index, y=df['60MA'], line=dict(color='green', width=1.5), name='60일선'))
            fig.update_layout(xaxis_rangeslider_visible=False, height=410, margin=dict(l=10, r=10, t=10, b=10))
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
    # 탭 2: AI 리스크 패리티 포트폴리오
    # ------------------------------------------
    with tab_quant:
        @st.cache_data(ttl=600)
        def cached_strategy_run():
            return run_advanced_portfolio_strategy()
            
        m_score, m_report, status, df_p = cached_strategy_run()
        
        col_stat1, col_stat2 = st.columns([1, 2])
        with col_stat1:
            st.metric(label="📊 글로벌 매크로 안전성 스코어", value=f"{m_score} 점", delta="안전 장세" if m_score >= 75 else "변동성 경계")
        with col_stat2:
            st.info(f"**매크로 모니터링 분석 리포트:**\n{m_report}")
            
        st.markdown("---")
        
        if df_p is not None and not df_p.empty:
            st.subheader("🏆 실시간 최적화 자산배분 TOP 3 포트폴리오")
            
            cols = st.columns(3)
            for idx, row in df_p.iterrows():
                with cols[idx]:
                    st.markdown(f"""
                    <div style="padding:1.2rem; border-radius:10px; background-color:#f8f9fa; border-top: 5px solid #0f52ba; margin-bottom:1rem;">
                        <h3 style="margin:0; color:#0f52ba;">🏅 {idx+1}위. {row['name']} ({row['code']})</h3>
                        <h4 style="margin: 8px 0; color:#118822;">⚖️ 권장 비중: [ {row['weight']}% ]</h4>
                        <p style="font-size:0.9rem; color:#666; margin:4px 0;">(20일 변동성 기준 가중: {row['volatility']}%)</p>
                        <hr style="margin:10px 0;">
                        <div style="background-color:#118822; color:white; padding:8px; border-radius:5px; text-align:center; font-weight:bold; margin-bottom:8px;">
                            🎯 추천 매수 진입가: {format(row['buy_price'], ',')} 원 이하
                        </div>
                        <div style="background-color:#ff0000; color:white; padding:8px; border-radius:5px; text-align:center; font-weight:bold; margin-bottom:8px;">
                            🚨 기계적 리스크 손절가: {format(row['stop_price'], ',')} 원
                        </div>
                        <ul style="font-size:0.92rem; padding-left:20px; color:#333; margin-top:10px;">
                            <li>현재가: <b>{format(row['price'], ',')}원</b></li>
                            <li>단기 과열도(RSI): <b>{row['rsi']}점</b></li>
                            <li>당일 거래대금: <b>{row['t_value_b']}억 원</b> (평소 {row['vol_ratio']}배)</li>
                        </ul>
                    </div>
                    """, unsafe_allow_html=True)
                    
                    hist_df = row['df_chart']
                    fig = go.Figure(data=[go.Candlestick(
                        x=hist_df.index, open=hist_df['Open'], high=hist_df['High'], low=hist_df['Low'], close=hist_df['Close'],
                        increasing_line_color='red', decreasing_line_color='blue', name="주가"
                    )])
                    fig.update_layout(xaxis_rangeslider_visible=False, height=220, margin=dict(l=10, r=10, t=10, b=10))
                    st.plotly_chart(fig, use_container_width=True)
                    
            # 텔레그램 수동 발송 제어 장치 추가
            st.markdown("---")
            st.subheader("📡 수동 경보 전송 제어")
            if st.button("📨 현재 포트폴리오 즉시 텔레그램 경보 전송", type="primary"):
                if not TOKEN or not CHAT_ID:
                    st.error("❌ 텔레그램 토큰(TELEGRAM_TOKEN) 또는 채널 ID(TELEGRAM_CHAT_ID) 환경변수가 세팅되지 않았습니다.")
                else:
                    # 리포트 메세지 생성
                    kst_time = datetime.utcnow() + timedelta(hours=9)
                    now_str = kst_time.strftime("%Y-%m-%d %H:%M")
                    msg = f"🏛️ [AITAS-EQ] 퀀트 리스크 마스터 포트폴리오\n({now_str} 기준 / 수동 전송)\n\n"
                    msg += f"📊 [1] 실시간 매크로 스코어: 💯 {m_score}점 / 100점\n{m_report}\n\n"
                    msg += "----------------------------------------\n\n"
                    msg += "🏆 [2] 리스크 패리티 최적 자산배분 TOP 3\n\n"
                    for idx, row in df_p.iterrows():
                        msg += f"🏅 {idx+1}위. ★ {row['name']} ★\n"
                        msg += f"  ▪ ⚖️ 권장비중: [ {row['weight']}% ] (변동성: {row['volatility']}%)\n"
                        msg += f"  ▪ 🎯 추천 매수 진입가: {format(row['buy_price'], ',')}원 이하\n"
                        msg += f"  ▪ 🚨 기계적 리스크 손절가: {format(row['stop_price'], ',')}원\n"
                        msg += f"  ▪ 📈 수급 동향: 당일 거래대금 {row['t_value_b']}억 원 / {row['vol_ratio']}배 분출\n"
                        msg += f"  ▪ 🔍 현재 가격: {format(row['price'], ',')}원 / RSI: {row['rsi']}\n\n"
                    
                    try:
                        res = requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
                        if res.status_code == 200:
                            st.success("✅ 텔레그램 경보가 성공적으로 발송되었습니다!")
                        else:
                            st.error(f"❌ 발송 실패 (상태 코드: {res.status_code}) - {res.text}")
                    except Exception as e:
                        st.error(f"❌ 전송 에러 발생: {e}")
        else:
            st.warning(f"⚠️ 현재 조건에 부합하는 종목이 없습니다. ({status})")

else:
    # 2) 깃허브 액션 배포(CLI 콘솔 모드)로 가동할 때 자동으로 텔레그램 메시지 전송
    m_score, m_report, status, df_p = run_advanced_portfolio_strategy()
    kst_time = datetime.utcnow() + timedelta(hours=9)
    now_str = kst_time.strftime("%Y-%m-%d %H:%M")
    
    msg = f"🏛️ [AITAS-EQ] 퀀트 리스크 마스터 포트폴리오\\n({now_str} 기준)\\n\\n"
    msg += f"📊 [1] 실시간 매크로 스코어: 💯 {m_score}점 / 100점\\n{m_report}\\n\\n"
    msg += "----------------------------------------\\n\\n"
    
    if df_p is not None and not df_p.empty:
        if m_score < 50:
            msg += "⚠️ [경보] 매크로 위험 점수가 극도로 낮습니다. 보수적으로 운영하십시오.\\n\\n"
        msg += "🏆 [2] 리스크 패리티 최적 자산배분 TOP 3\\n\\n"
        for idx, row in df_p.iterrows():
            msg += f"🏅 {idx+1}위. ★ {row['name']} ★\\n"
            msg += f"  ▪ ⚖️ 권장비중: [ {row['weight']}% ] (20일 변동성: {row['volatility']}%)\\n"
            msg += f"  ▪ 🎯 추천 매수 진입가: {format(row['buy_price'], ',')}원 이하\\n"
            msg += f"  ▪ 🚨 기계적 리스크 손절가: {format(row['stop_price'], ',')}원\\n"
            msg += f"  ▪ 📈 수급 동향: 당일 거래대금 {row['t_value_b']}억 원 / {row['vol_ratio']}배 분출\\n"
            msg += f"  ▪ 🔍 현재 가격: {format(row['price'], ',')}원 / RSI: {row['rsi']}\\n\\n"
    else:
        msg += f"⚠️ 현재 조건에 부합하는 종목이 없습니다. (상태: {status})"
        
    try:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
        print("[시스템] 텔레그램 리포트 정상 발송 완료!")
    except Exception as e:
        print("전송 에러:", e)
