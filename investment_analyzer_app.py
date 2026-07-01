import os
import requests
import re
import sys
import pandas as pd
import yfinance as yf
import numpy as np
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

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

def get_macro_safety_score():
    """
    1. 실시간 매크로 위험 스코어러 (Macro Risk-Off Filter)
    - 미국 10년물 국채 금리(^TNX)와 원/달러 환율(USDKRW=X)의 20일 이동평균 이탈도를 추적합니다.
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
            status_text.text(f"🔍 실시간 차트/거래대금 스캔 중... ({name} - {i+1}/{len(candidates)})")
            
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
            
            # 💡 [핵심 도입 2]: 당일 거래대금 100억 원 하한 필터
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
# 🛠️ DUAL-MODE 실행 분기 레이어
# ==========================================
if is_streamlit:
    # 1) 스트림릿 대시보드 모드로 가동할 때
    @st.cache_data(ttl=600)
    def cached_strategy_run():
        return run_advanced_portfolio_strategy()
        
    st.title("🏛️ AITAS-EQ 리스크 패리티 포트폴리오 시스템")
    st.write("20년 경력 운용역의 실시간 매크로 위험 감지기 및 리스크 패리티 자산배분기 관제탑입니다.")
    
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
                
                import plotly.graph_objects as go
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
