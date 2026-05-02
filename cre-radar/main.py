import yfinance as yf
import pandas as pd
import matplotlib.pyplot as plt
import requests
from datetime import datetime
import os, json
from pytrends.request import TrendReq

# --- CONFIGURA ---
TELEGRAM_TOKEN = "SEU_TOKEN_AQUI"
TELEGRAM_CHAT_ID = "SEU_CHAT_ID"
CSV_PATH = "historico_sinais.csv"
CARTEIRA_PATH = "carteira.json"

# --- PARÂMETROS ---
BTC_ENTRADA = 50000
BTC_ALVO_VENDA = 120000
BTC_STOP_TRAIL = 0.20

NIVEIS = {
    'VIX': 45, 'VIX_SAIDA': 18, 'TED': 0.75, 'HY': 6.50,
    'USDBRL': 6.80, 'IFNC_1M': -15, 'IMOB_1M': -20,
    'IFIX_1M': -10, 'VALE_1M': -15, 'COBRE_1M': -15,
    'GOOGLE_TRENDS': 80
}

TICKERS = {
    'VIX': '^VIX', 'TED': '^IRX', 'HY': '^BAMLH0A0HYM2',
    'USDBRL': 'USDBRL=X', 'IFNC': 'IFNC.SA', 'IMOB': 'IMOB.SA',
    'IFIX': 'IFIX.SA', 'VALE': 'VALE3.SA', 'COBRE': 'HG=F',
    'SP500': '^GSPC', 'BTC': 'BTC-USD'
}

# --- FUNÇÕES CARTEIRA ---
def carrega_carteira():
    if os.path.exists(CARTEIRA_PATH):
        with open(CARTEIRA_PATH, 'r') as f: return json.load(f)
    return {"btc": [], "total_investido": 0, "total_btc": 0}

def salva_carteira(carteira):
    with open(CARTEIRA_PATH, 'w') as f: json.dump(carteira, f, indent=2)

def add_compra(qtd, preco, data=None):
    carteira = carrega_carteira()
    data = data or datetime.now().strftime('%Y-%m-%d')
    carteira["btc"].append({"qtd": qtd, "preco": preco, "data": data})
    carteira["total_investido"] += qtd * preco
    carteira["total_btc"] += qtd
    salva_carteira(carteira)
    return f"Compra registrada: {qtd} BTC a ${preco:,.0f}"

def add_venda(qtd, preco):
    carteira = carrega_carteira()
    if qtd > carteira["total_btc"]: return "Erro: Qtd maior que saldo"

    # FIFO: vende os mais antigos primeiro
    vendido = 0
    lucro_total = 0
    novas_posicoes = []
    for lote in carteira["btc"]:
        if vendido >= qtd:
            novas_posicoes.append(lote)
            continue
        vende_lote = min(qtd - vendido, lote["qtd"])
        lucro_total += vende_lote * (preco - lote["preco"])
        vendido += vende_lote
        if lote["qtd"] > vende_lote:
            lote["qtd"] -= vende_lote
            novas_posicoes.append(lote)

    carteira["btc"] = novas_posicoes
    carteira["total_btc"] -= qtd
    carteira["total_investido"] = sum(l["qtd"]*l["preco"] for l in carteira["btc"])
    salva_carteira(carteira)
    return f"Venda: {qtd} BTC a ${preco:,.0f} | Lucro: ${lucro_total:,.0f}"

# --- FUNÇÕES DADOS ---
def baixar_dados(periodo='20y'):
    dados = {}
    for nome, ticker in TICKERS.items():
        try:
            df = yf.download(ticker, period=periodo, interval='1d', progress=False, auto_adjust=True)
            dados[nome] = df['Close'].dropna()
        except: dados[nome] = pd.Series(dtype=float)
    return dados

def pega_google_trends():
    try:
        pytrends = TrendReq(hl='pt-BR', tz=180)
        pytrends.build_payload(['bitcoin'], timeframe='today 3-m')
        df = pytrends.interest_over_time()
        return df['bitcoin'].iloc[-1] if not df.empty else 0
    except: return 0

def calcula_sinais_diarios(d):
    idx = d['VIX'].index
    df = pd.DataFrame(index=idx)
    df['data'] = df.index
    df['s_vix'] = d['VIX'] > NIVEIS['VIX']
    df['s_ted'] = d['TED'].pct_change(5) > 5
    df['s_usdbrl'] = d['USDBRL'] > NIVEIS['USDBRL']
    for nome in ['IFNC', 'IMOB', 'IFIX', 'VALE', 'COBRE']:
        ret_1m = d[nome].pct_change(21) * 100 if len(d[nome]) > 21 else 0
        df[f's_{nome.lower()}'] = ret_1m < NIVEIS[f'{nome}_1M']
    spx_1m = d['SP500'].pct_change(21) * 100
    df['s_hy'] = spx_1m < -10

    colunas_sinal = [c for c in df.columns if c.startswith('s_')]
    df['total_sinais'] = df[colunas_sinal].sum(axis=1)
    df['btc_price'] = d['BTC']
    df['gatilho_3sinais'] = df['total_sinais'] >= 3
    df['gatilho_compra_btc'] = (df['total_sinais'] >= 3) & (df['btc_price'] < BTC_ENTRADA)

    # Saída
    df['s_vix_baixo'] = d['VIX'] < NIVEIS['VIX_SAIDA']
    df['s_btc_alto'] = d['BTC'] > BTC_ALVO_VENDA * 0.92
    df['btc_max_pos110k'] = d['BTC'].where(d['BTC'] > BTC_ALVO_VENDA * 0.92).cummax()
    df['stop_trail'] = df['btc_max_pos110k'] * (1 - BTC_STOP_TRAIL)
    df['s_stop_trail'] = (d['BTC'] < df['stop_trail']) & (df['stop_trail'].notna())
    return df

def manda_telegram(texto):
    if TELEGRAM_TOKEN == "SEU_TOKEN_AQUI": return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    requests.post(url, data={"chat_id": TELEGRAM_CHAT_ID, "text": texto})

def resumo_carteira(btc_atual):
    c = carrega_carteira()
    if c["total_btc"] == 0: return "Carteira zerada"

    preco_medio = c["total_investido"] / c["total_btc"] if c["total_btc"] > 0 else 0
    valor_atual = c["total_btc"] * btc_atual
    pnl = valor_atual - c["total_investido"]
    pnl_pct = (pnl / c["total_investido"] * 100) if c["total_investido"] > 0 else 0

    texto = f"📊 CARTEIRA\n"
    texto += f"Saldo: {c['total_btc']:.4f} BTC\n"
    texto += f"Preço médio: ${preco_medio:,.0f}\n"
    texto += f"Investido: ${c['total_investido']:,.0f}\n"
    texto += f"Atual: ${valor_atual:,.0f}\n"
    texto += f"P&L: ${pnl:,.0f} ({pnl_pct:+.1f}%)\n"
    texto += f"BTC agora: ${btc_atual:,.0f}"
    return texto

def main():
    global d
    d = baixar_dados('20y')
    df_sinais = calcula_sinais_diarios(d)

    hoje = df_sinais.iloc[-1]
    btc_hoje = hoje['btc_price']
    trends_hoje = pega_google_trends()

    # Alertas de mercado
    if hoje['gatilho_3sinais']:
        sinais_ativos = [c.replace('s_', '').upper() for c in df_sinais.columns if c.startswith('s_') and hoje[c] and 'baixa' not in c and 'alto' not in c and 'trail' not in c]
        texto = f"🚨 3 SINAIS: {int(hoje['total_sinais'])}\n" + ", ".join(sinais_ativos) + f"\nBTC: ${btc_hoje:,.0f}"
        manda_telegram(texto); print("\n" + texto)

    if hoje['gatilho_compra_btc']:
        texto = f"🟢 COMPRA BTC\n3+ Sinais + BTC ${btc_hoje:,.0f} < {BTC_ENTRADA:,}\nAlvo: ${BTC_ALVO_VENDA:,}"
        manda_telegram(texto); print("\n" + texto)

    if hoje['s_vix_baixo'] and hoje['s_btc_alto'] and (trends_hoje > NIVEIS['GOOGLE_TRENDS']):
        texto = f"🔴 VENDA EUFORIA\nVIX {d['VIX'].iloc[-1]:.1f} | BTC ${btc_hoje:,.0f} | Trends {trends_hoje}/100\nREALIZA PARCIAL"
        manda_telegram(texto); print("\n" + texto)

    if hoje['s_stop_trail']:
        texto = f"🔴 VENDA STOP TRAIL\nBTC ${btc_hoje:,.0f} caiu 20%\nMáxima: ${hoje['btc_max_pos110k']:,.0f}\nREALIZA TUDO"
        manda_telegram(texto); print("\n" + texto)

    # Resumo carteira todo dia
    resumo = resumo_carteira(btc_hoje)
    manda_telegram(resumo)
    print("\n" + resumo)

if __name__ == "__main__":
    # Exemplos de uso manual:
    # print(add_compra(0.5, 48000)) # Registra compra
    # print(add_venda(0.2, 115000)) # Registra venda
    main()