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

def get_top_tickers():
    """네이버 증권에서 코스피/코스닥 시장의 시가총액 상위 종목을 수집합니다."""
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
                # 레버리지, ETF, ETN, 스팩, 우선주 등 제외 필터링
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

def get_naver_details(code):
    """네이버 증권에서 해당 종목의 증권사 목표가, 투자의견 점수, PER, PBR, 배당수익률을 실시간 수집합니다."""
    url = f"https://finance.naver.com/item/main.naver?code={code}"
    headers = {'User-Agent': 'Mozilla/5.0'}
    target_price = 0
    opinion_score = 0.0
    per = 0.0
    pbr = 0.0
    div = 0.0
    try:
        res = requests.get(url, headers=headers, timeout=5)
        soup = BeautifulSoup(res.text, 'html.parser')
        
        # 1. 증권사 목표가 추출
        cns_div = soup.find('div', class_='r_cns')
        if cns_div and cns_div.find('em'):
            target_price = int(cns_div.find('em').text.replace(',', ''))
        
        # 2. 투자의견 점수 추출
        opinion_td = soup.select_one('table.r_cns_table td.num')
        if opinion_td:
            try: opinion_score = float(opinion_td.text.strip())
            except: pass
            
        # 3. PER 추출 (id="_per")
        per_em = soup.find('em', id='_per')
        if per_em:
            try: per = float(per_em.text.replace(',', '').strip())
            except: pass
            
        # 4. PBR 추출 (id="_pbr")
        pbr_em = soup.find('em', id='_pbr')
        if pbr_em:
            try: pbr = float(pbr_em.text.replace(',', '').strip())
            except: pass
            
        # 5. 배당수익률 추출 (id="_dvd")
        dvd_em = soup.find('em', id='_dvd')
        if dvd_em:
            try: div = float(dvd_em.text.replace(',', '').replace('%', '').strip())
            except: pass
    except Exception as e:
        print(f"네이버 상세 데이터 추출 실패 ({code}): {e}")
        
    return target_price, opinion_score, per, pbr, div

def calculate_rsi(series, period=14):
    """지정된 기간(기본 14일) 동안의 RSI(상대강도지수)를 연산합니다."""
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ema_up = up.ewm(com=period-1, adjust=False).mean()
    ema_down = down.ewm(com=period-1, adjust=False).mean()
    rs = ema_up / ema_down
    return 100 - (100 / (1 + rs))

def run_master_quant_strategy():
    """추세, 수급, 증권사 합의, 대가의 재무 분석 지표를 결합한 종합 퀀트 전략을 실행합니다."""
    all_candidates = get_top_tickers()
    if not all_candidates:
        return "네이버 명단수집 실패", None

    survivors = []
    for code, name, suffix in all_candidates:
        try:
            ticker_symbol = f"{code}{suffix}"
            df = yf.Ticker(ticker_symbol).history(period="3mo")
            
            # 야후 파이낸스의 '빈칸 버그'를 완벽하게 청소합니다.
            df = df.dropna()
            if len(df) < 30: continue
                
            df['MA20'] = df['Close'].rolling(window=20).mean()
            df['MA60'] = df['Close'].rolling(window=60).mean()
            df['RSI'] = calculate_rsi(df['Close'])
            df['Vol5'] = df['Volume'].rolling(window=5).mean()
            
            # 한 번 더 결측치를 비워주고 순수 숫자로만 변환
            df = df.dropna()
            if len(df) < 5: continue
            
            current_price = float(df['Close'].iloc[-1])
            ma20 = float(df['MA20'].iloc[-1])
            ma60 = float(df['MA60'].iloc[-1])
            rsi = float(df['RSI'].iloc[-1])
            current_vol = float(df['Volume'].iloc[-1])
            avg_vol_5d = float(df['Vol5'].iloc[-2])
            
            chart_score = 0
            if current_price > ma20 and ma20 > ma60: chart_score += 20
            if 40 <= rsi <= 65: chart_score += 10
                
            vol_ratio = current_vol / avg_vol_5d if avg_vol_5d > 0 else 1.0
            volume_score = 0
            if vol_ratio >= 2.0: volume_score += 35
            elif vol_ratio >= 1.5: volume_score += 25
            elif vol_ratio >= 1.2: volume_score += 15

            if chart_score + volume_score >= 15:
                survivors.append({
                    'code': code, 'name': name, 'price': current_price,
                    'rsi': rsi, 'vol_ratio': vol_ratio,
                    'base_score': chart_score + volume_score
                })
        except:
            continue

    if not survivors:
        return "차트/거래량 1차 통과 종목 없음", None

    scored_stocks = []
    for cand in survivors:
        # 네이버 금융에서 목표가, 투자의견 점수, PER, PBR, 배당수익률 실시간 일괄 수집
        target_price, opinion_score, per, pbr, div = get_naver_details(cand['code'])
        
        # 워런 버핏 & 그레이엄 조건 만족 여부 검사 (가치투자 밸류에이션)
        is_guru = False
        if (0 < per <= 10.0) and (0 < pbr <= 1.0) and (div >= 3.5):
            is_guru = True
        
        consensus_score = 0
        upside = 0.0
        if target_price > cand['price']:
            upside = ((target_price - cand['price']) / cand['price']) * 100
            if upside >= 25: consensus_score += 20
            elif upside >= 12: consensus_score += 15
        if opinion_score >= 4.0: consensus_score += 15
        elif opinion_score >= 3.6: consensus_score += 10
            
        # 머니터링 가치투자 조건 만족 시 특별 가산점 부여
        guru_score = 20 if is_guru else 0
        total_score = cand['base_score'] + consensus_score + guru_score
        
        # 안전하게 수집된 종목을 결과에 담습니다
        scored_stocks.append({
            'name': cand['name'], 'price': int(cand['price']),
            'target': target_price, 'upside': round(upside, 1) if target_price > 0 else 0,
            'opinion': opinion_score, 'vol_ratio': round(cand['vol_ratio'], 1),
            'rsi': round(cand['rsi'], 1), 'score': total_score,
            'per': per, 'pbr': pbr, 'div': div, 'is_guru': is_guru
        })

    if scored_stocks:
        df_res = pd.DataFrame(scored_stocks)
        return "성공", df_res.sort_values(by='score', ascending=False).head(3)
    
    return "네이버 목표가 데이터 수집 오류", None

# 가동 및 전송
status, df_picks = run_master_quant_strategy()
kst_time = datetime.utcnow() + timedelta(hours=9)
now = kst_time.strftime("%Y-%m-%d %H:%M")

if df_picks is not None and not df_picks.empty:
    one_pick = df_picks.iloc[0]
    market_comment = ""
    if one_pick['score'] < 50:
        market_comment = "⚠️ [주의] 현재 시장 거래량이 메마르고 전반적인 추세가 꺾인 변동성 장세입니다. 소액 트레이딩이나 관망을 권장합니다.\n\n"
    else:
        market_comment = "✅ [양호] 현재 시장에서 수급과 추세가 비교적 견고하게 유지되고 있는 알짜 종목들입니다.\n\n"
        
    msg = f"🌟 [마스터 퀀트 + 머니터링 결합] 매일 오전 8시 종합 추천\n"
    msg += f"(수급/추세 + 워런 버핏 가치투자 종합 평가 | {now})\n\n"
    msg += market_comment
    msg += f"👑 오늘의 최우선 관심 종목: ★ {one_pick['name']} ★\n\n"
    
    msg += f"📋 [상세 분석 종목 순위]\n"
    for idx, row in df_picks.reset_index(drop=True).iterrows():
        rank = idx + 1
        p_str = format(row['price'], ',')
        t_str = format(row['target'], ',') if row['target'] > 0 else "측정불가"
        up_str = f"+{row['upside']}%" if row['target'] > 0 else "-"
        
        # 머니터링 통과 시 특별 뱃지 부여
        guru_badge = " 🏛️[머니터링 가치주]" if row['is_guru'] else ""
        
        msg += f"{rank}위. {row['name']} ({p_str}원) - 💯 {row['score']}점{guru_badge}\n"
        msg += f"  ▪ 증권사 목표가: {t_str}원 (상승여력: {up_str})\n"
        msg += f"  ▪ 수급(거래량): {row['vol_ratio']}배 / 차트(RSI): {row['rsi']}\n"
        # 재무 지표 브리핑
        msg += f"  ▪ 재무가치: PER {row['per']}배 / PBR {row['pbr']}배 / 배당 {row['div']}%\n\n"
else:
    msg = f"🌟 [마스터 퀀트 브리핑]\n({now} 기준)\n\n분석 오류 ({status}). 잠시 후 다시 시도해 주세요."

try:
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={"chat_id": CHAT_ID, "text": msg}, timeout=10)
except Exception as e:
    print("전송 실패:", e)
