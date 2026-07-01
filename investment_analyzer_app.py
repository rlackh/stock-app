import os
import requests
import re
import pandas as pd
import yfinance as yf
from bs4 import BeautifulSoup
from datetime import datetime, timedelta

# 텔레그램 보안 설정값 (깃허브 Secrets 환경변수 연동)
TOKEN = os.environ.get("TELEGRAM_TOKEN")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

def get_macro_safety_score():
    """
    1. 실시간 매크로 위험 스코어러 (Macro Risk-Off Filter)
    - 미국 10년물 국채 금리(^TNX)와 원/달러 환율(USDKRW=X)의 20일 이동평균 이탈도를 추적합니다.
    - 점수가 낮을수록(50점 미만) 매수 포지션을 수축하고 관망을 권장합니다.
    """
    print("[시스템] 글로벌 매크로 환경 스캔 중...")
    try:
        # 안전한 수집을 위한 최근 30일 데이터 요청
        fx_data = yf.Ticker("USDKRW=X").history(period="1mo", timeout=5)
        bond_data = yf.Ticker("^TNX").history(period="1mo", timeout=5)
        
        if fx_data.empty or bond_data.empty:
            return 70, "매크로 API 수신 불안정 (기본 안전 점수 부여)"
            
        # 결측치 청소
        fx_data = fx_data.dropna()
        bond_data = bond_data.dropna()
        
        # 현재가 및 20일 이동평균 연산
        curr_fx = float(fx_data['Close'].iloc[-1])
        ma20_fx = float(fx_data['Close'].rolling(window=20).mean().iloc[-1])
        
        curr_bond = float(bond_data['Close'].iloc[-1])
        ma20_bond = float(bond_data['Close'].rolling(window=20).mean().iloc[-1])
        
        # 위험 감지 점수 연산 (기본 100점 시작)
        safety_score = 100
        
        # 원/달러 환율이 20일 평원을 넘어 과열 추세이면 위험도 증가
        if curr_fx > ma20_fx:
            safety_score -= 25
            if curr_fx > fx_data['Close'].rolling(window=5).mean().iloc[-1]:
                safety_score -= 10 # 최근 단기 급등 시 추가 감점
                
        # 미국 10년물 국채금리가 20일 평균을 넘어 상승 추세이면 자산 가치 할인율 증가 (위험)
        if curr_bond > ma20_bond:
            safety_score -= 25
            if curr_bond > bond_data['Close'].rolling(window=5).mean().iloc[-1]:
                safety_score -= 10 # 최근 단기 급등 시 추가 감점
                
        # 종합 코멘트 매칭
        if safety_score >= 80:
            comment = "🟢 [글로벌 자금 유입기] 환율과 금리가 하방 안정세를 보이며, 기관/외인 자금 수급 유입에 아주 우호적인 바다입니다."
        elif safety_score >= 50:
            comment = "🟡 [변동성 박스권 장세] 환율 또는 금리 중 하나의 변동성이 존재하므로, 무리한 배팅을 자제하고 철저한 분할 진입이 권장됩니다."
        else:
            comment = "🚨 [매크로 위험 경보] 글로벌 유동성이 수축하고 외인 환차손 회피 투매가 출회될 수 있는 폭풍우 장세입니다. 보수적 운용 및 현금 확보를 적극 권장합니다."
            
        return safety_score, f"현재 환율: {curr_fx:.1f}원 (20MA 대비 {'높음' if curr_fx > ma20_fx else '낮음'}) | 미10년 국채금리: {curr_bond/10:.2f}% (20MA 대비 {'높음' if curr_bond > ma20_bond else '낮음'})\n{comment}"
    except Exception as e:
        print(f"[경고] 매크로 스캔 오류: {e}")
        return 70, "매크로 분석 레이어 임시 지연 (기본 안전 점수 우회 부여)"

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
                # 잡동사니 및 변동성 왜곡 자산 제외
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
    """지정 기간(기본 14일) 동안의 RSI(상대강도지수) 산출"""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def run_advanced_portfolio_strategy():
    """매크로, 100억 거래대금 필터링, 정배열 수급 필터링 후 리스크 패리티 비중배분 실행"""
    # 1단계. 실시간 매크로 판독
    macro_score, macro_report = get_macro_safety_score()
    
    # 2단계. 후보군 탐색 및 기술적/수급 분석
    candidates = get_market_candidates()
    survivors = []
    
    print(f"[시스템] 총 {len(candidates)}개 후보 기업 스캔 및 필터 작동 중...")
    for code, name, suffix in candidates:
        try:
            ticker_symbol = f"{code}{suffix}"
            df = yf.Ticker(ticker_symbol).history(period="3mo", timeout=1.5)
            df = df.dropna()
            if len(df) < 30: continue
            
            # 기술적 선행 지표 연산
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
            
            # 💡 [핵심 도입 2]: 당일 거래대금 100억 원 하한 필터 (종가 * 거래량)
            # 10,000,000,000 KRW 미만의 종목은 호가 왜곡 및 변동성 장난 방지를 위해 완전 배제
            transaction_value = current_price * current_vol
            if transaction_value < 10_000_000_000:
                continue
                
            # 기술적 수급 통과 조건 정의
            is_trend_bullish = (current_price > ma20) and (ma20 > ma60)
            is_rsi_stable = (35 <= rsi <= 65) # 고점 과열이나 패닉 하락 방지
            vol_ratio = current_vol / avg_vol_5d if avg_vol_5d > 0 else 1.0
            is_volume_spiking = vol_ratio >= 1.5 # 평소 대비 1.5배 수급 유입
            
            if is_trend_bullish and is_rsi_stable and is_volume_spiking:
                survivors.append({
                    'code': code, 'name': name, 'ticker': ticker_symbol,
                    'price': current_price, 'rsi': rsi, 'vol_ratio': vol_ratio,
                    't_value_b': round(transaction_value / 100000000, 1), # 억 단위 기록
                    'df': df # 리스크 패리티 연산에 전달용
                })
        except:
            continue
            
    if not survivors:
        return macro_score, macro_report, "차트 및 100억 수급 필터를 통과한 종목 없음", None

    # 점수화하여 최적의 TOP 3 종목 선정 (수급 및 RSI 이격 점수 합성 정렬)
    ranked_stocks = []
    for s in survivors:
        score = (s['vol_ratio'] * 15) + (100 - abs(s['rsi'] - 45) * 2) # 변동성 분출 및 안정적 이격도 조합
        s['combined_score'] = score
        ranked_stocks.append(s)
        
    df_ranked = pd.DataFrame(ranked_stocks)
    top_3_targets = df_ranked.sort_values(by='combined_score', ascending=False).head(3).to_dict('records')
    
    # 💡 [핵심 도입 3]: 포트폴리오 리스크 패리티 가중치 연산 (Risk-Parity Allocation)
    # 종목들의 최근 20거래일 수익률 표준편차(변동성)의 역수로 자금을 분산 분할합니다.
    volatilities = {}
    for target in top_3_targets:
        target_df = target['df']
        # 최근 20거래일간의 일일 수익률 계산
        returns = target_df['Close'].iloc[-20:].pct_change().dropna()
        daily_volatility = returns.std() # 일별 표준편차
        volatilities[target['name']] = daily_volatility if daily_volatility > 0 else 0.05
        
    # 변동성의 역수(1/Std) 연산
    inv_vols = {name: 1.0 / vol for name, vol in volatilities.items()}
    total_inv_vol = sum(inv_vols.values())
    
    # 최종 투자 비중 할당 및 결과 결합
    final_portfolio = []
    for target in top_3_targets:
        name = target['name']
        alloc_weight = (inv_vols[name] / total_inv_vol) * 100
        
        # 실시간 가격에 기반한 정밀 타점 계산
        rec_buy = int(target['price'] * 0.985) # 20일선 수렴 지지선 타점
        rec_stop = int(target['price'] * 0.94)  # 바닥 탈출 성벽 손절라인
        
        final_portfolio.append({
            'name': name, 'code': target['code'], 'price': int(target['price']),
            'weight': round(alloc_weight, 1), 'volatility': round(volatilities[name]*100, 2),
            'buy_price': rec_buy, 'stop_price': rec_stop, 'rsi': round(target['rsi'], 1),
            'vol_ratio': round(target['vol_ratio'], 1), 't_value_b': target['t_value_b']
        })
        
    return macro_score, macro_report, "성공", pd.DataFrame(final_portfolio)

# 🚀 전략 구동 및 메인 리포트 빌드
macro_score, macro_report, status, df_portfolio = run_advanced_portfolio_strategy()
kst_time = datetime.utcnow() + timedelta(hours=9)
now = kst_time.strftime("%Y-%m-%d %H:%M")

msg = f"🏛️ [AITAS-EQ] 퀀트 리스크 마스터 포트폴리오\n({now} 기준)\n\n"
msg += f"📊 [1] 실시간 매크로 스코어: 💯 {macro_score}점 / 100점\n"
msg += f"{macro_report}\n\n"
msg += "----------------------------------------\n\n"

if df_portfolio is not None and not df_portfolio.empty:
    # 매크로 지표가 위험할 경우 포트폴리오 가동 경보 브리핑 추가
    if macro_score < 50:
        msg += "⚠️ [경보] 매크로 위험 점수가 극도로 낮습니다. 아래 계산된 리스크 패리티 안전 비중에 맞춰 전체 투자 원금의 50% 이하(현금 확보 우위)로만 진입하시는 것을 강력히 권고합니다.\n\n"
    else:
        msg += "✅ [시장 안정] 매크로 점수가 양호하여 아래 계산된 자산 배분 비중 그대로 포트폴리오를 구성하셔도 안전합니다.\n\n"
        
    msg += "🏆 [2] 리스크 패리티 최적 자산배분 TOP 3\n\n"
    for idx, row in df_portfolio.iterrows():
        p_str = format(row['price'], ',')
        b_str = format(row['buy_price'], ',')
        s_str = format(row['stop_price'], ',')
        
        msg += f"🏅 {idx+1}위. ★ {row['name']} ★\n"
        msg += f"  ▪ ⚖️ 리스크 패리티 권장비중: [ {row['weight']}% ] (20일 변동성: {row['volatility']}%)\n"
        msg += f"  ▪ 🎯 추천 매수 진입가: {b_str}원 이하\n"
        msg += f"  ▪ 🚨 기계적 리스크 손절가: {s_str}원 (칼손절)\n"
        msg += f"  ▪ 📈 수급 동향: 당일 거래대금 {row['t_value_b']}억 원 / 평소대비 {row['vol_ratio']}배 폭발\n"
        msg += f"  ▪ 🔍 현재 가격: {p_str}원 / RSI: {row['rsi']}\n\n"
        
    msg += "💡 리스크 패리티(Risk-Parity)는 주식의 단순 가격이 아닌 '변동성 크기'를 역수로 나누어 가중치를 부여하므로, 변동성 폭풍 속에서 계좌를 극대화하고 드로우다운(MDD)을 방어하는 헤지펀드용 수학 공식입니다."
else:
    msg += f"⚠️ 현재 조건에 부합하는 종목을 검출하지 못했습니다.\n(수동 진단 상태: {status})\n\n💡 계좌 잔고를 보존하며 안전하게 현금 관망을 고수하십시오."

# 텔레그램 메인 엔진 발송
try:
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
    print("[시스템] 포트폴리오 리포트 발송 완료!")
except Exception as e:
    print("텔레그램 전송 실패:", e)
