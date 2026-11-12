"""Robô de FIIs da Flying Academy.

Junta, para cada fundo imobiliário negociado na B3:
- CVM, informe mensal (geral, complemento, ativo e passivo): nome, segmento, data de início,
  patrimônio, valor patrimonial da cota, taxa de administração, dívidas e onde o dinheiro está aplicado
  (imóveis, papel ou cotas de outros FIIs), que decide se o fundo é tijolo, papel, híbrido ou FoF.
- CVM, informe trimestral (imóveis): quantidade de imóveis, vacância física e inadimplência.
- Yahoo Finance: rendimentos pagos por cota e cotação, para o retorno total em 5 anos
  e a constância do rendimento.
Grava dados/fiis.json. Roda no GitHub Actions, só com a biblioteca padrão do Python.
"""
import csv, io, json, os, re, sys, time, unicodedata, urllib.request, zipfile
from datetime import datetime, timezone, date

BASE = os.environ.get("CVM_FII", "https://dados.cvm.gov.br/dados/FII/DOC")
ANO_FIM = int(os.environ.get("ANO_FIM", datetime.now().year))
SAIDA = os.environ.get("SAIDA_FII", "dados/fiis.json")
YAHOO = os.environ.get("YAHOO", "1") == "1"
csv.field_size_limit(10**8)
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
RESUMO = []


def log(*a):
    print(*a, flush=True)


def baixa(url, tentativas=3, ua="robo-flying-academy/1.0"):
    if not url.startswith("http"):
        if not os.path.exists(url):
            return None
        with open(url, "rb") as f:
            return f.read()
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": ua})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            log(f"  tentativa {t+1} falhou ({e}), {url}")
        except Exception as e:  # noqa
            log(f"  tentativa {t+1} falhou ({e}), {url}")
        time.sleep(4 * (t + 1))
    return None


def sem_acento(s):
    return unicodedata.normalize("NFD", s or "").encode("ascii", "ignore").decode().upper().strip()


def num(v):
    v = (v or "").strip()
    if not v:
        return None
    if "," in v and "." in v:
        v = v.replace(".", "").replace(",", ".")
    elif "," in v:
        v = v.replace(",", ".")
    try:
        return float(v)
    except ValueError:
        return None


def le_csv(dados):
    try:
        texto = dados.decode("utf-8")
    except UnicodeDecodeError:
        texto = dados.decode("latin-1")
    return list(csv.DictReader(io.StringIO(texto.lstrip("﻿")), delimiter=";"))


def arquivos(z, trecho):
    """todas as linhas dos arquivos do zip cujo nome contém o trecho"""
    out = []
    for n in z.namelist():
        if trecho.lower() in n.lower() and n.lower().endswith(".csv"):
            out += le_csv(z.read(n))
    return out


class Col:
    """acha colunas pelo nome sem depender de maiúsculas, acentos ou do sufixo _Classe (Resolução CVM 175)"""
    def __init__(self, linhas):
        self.k = list(linhas[0].keys()) if linhas else []
        self.cache = {}

    def __call__(self, *padroes):
        chave = padroes
        if chave not in self.cache:
            achou = None
            for p in padroes:
                for k in self.k:
                    if k and re.fullmatch(p, sem_acento(k).replace(" ", "_"), re.I):
                        achou = k
                        break
                if achou:
                    break
            self.cache[chave] = achou
        return self.cache[chave]


def ultimo_por_fundo(linhas, c):
    """fica com o informe mais recente (e a última versão) de cada fundo"""
    kc, kd, kv = c(r"CNPJ_FUNDO.*"), c(r"DATA_REFERENCIA"), c(r"VERSAO")
    melhor = {}
    for l in linhas:
        cn = (l.get(kc) or "").strip()
        if not cn:
            continue
        chave = ((l.get(kd) or ""), int(num(l.get(kv)) or 0))
        if cn not in melhor or chave > melhor[cn][0]:
            melhor[cn] = (chave, l)
    return {k: v[1] for k, v in melhor.items()}


def ticker_de_isin(isin):
    isin = (isin or "").strip().upper()
    m = re.match(r"^BR([A-Z0-9]{4})CTF", isin)
    return m.group(1) + "11" if m else None


# ---------------- informe mensal ----------------
def informes_mensais():
    geral, comp, ap = [], [], []
    for ano in range(ANO_FIM - 6, ANO_FIM + 1):
        d = baixa(f"{BASE}/INF_MENSAL/DADOS/inf_mensal_fii_{ano}.zip")
        if not d:
            log(f"informe mensal {ano}: não encontrado")
            continue
        z = zipfile.ZipFile(io.BytesIO(d))
        g, c, a = arquivos(z, "geral"), arquivos(z, "complemento"), arquivos(z, "ativo_passivo")
        log(f"informe mensal {ano}: {len(g)} geral, {len(c)} complemento, {len(a)} ativo/passivo")
        geral += g
        comp += c
        ap += a
    for nome, L in (("geral", geral), ("complemento", comp), ("ativo_passivo", ap)):
        RESUMO.append(f"- colunas do informe mensal {nome}: `{', '.join(list(L[0].keys())[:60]) if L else 'nenhuma'}`")
    return geral, comp, ap


# ---------------- informe trimestral (imóveis) ----------------
def informe_trimestral():
    for ano in (ANO_FIM, ANO_FIM - 1):
        d = baixa(f"{BASE}/INF_TRIMESTRAL/DADOS/inf_trimestral_fii_{ano}.zip")
        if not d:
            continue
        z = zipfile.ZipFile(io.BytesIO(d))
        nomes = [n for n in z.namelist() if "imovel" in n.lower() and "renda" not in n.lower() and "contrato" not in n.lower()]
        linhas = []
        for n in nomes:
            linhas += le_csv(z.read(n))
        if not linhas:
            # nomes diferentes: pega qualquer arquivo de imóvel
            linhas = arquivos(z, "imovel")
        RESUMO.append(f"- arquivos do informe trimestral {ano}: `{', '.join(z.namelist())}`")
        RESUMO.append(f"- colunas de imóveis: `{', '.join(list(linhas[0].keys())[:40]) if linhas else 'nenhuma'}`")
        if not linhas:
            continue
        c = Col(linhas)
        kc, kd, kv = c(r"CNPJ_FUNDO.*"), c(r"DATA_REFERENCIA"), c(r"VERSAO")
        kn = c(r"NOME_IMOVEL", r"NOME.*")
        kvac = c(r"PERCENTUAL_VACANCIA.*", r".*VACANCIA.*")
        kina = c(r"PERCENTUAL_INADIMPLENCIA.*", r".*INADIMPLENCIA.*")
        krec = c(r"PERCENTUAL_RECEITAS?_FII.*", r".*RECEITA.*")
        karea = c(r"AREA.*")
        ultimo = {}
        for l in linhas:
            cn = (l.get(kc) or "").strip()
            ref = ((l.get(kd) or ""), int(num(l.get(kv)) or 0))
            if not cn:
                continue
            if cn not in ultimo or ref > ultimo[cn][0]:
                ultimo[cn] = (ref, [])
            if ref == ultimo[cn][0]:
                ultimo[cn][1].append(l)
        res = {}
        for cn, (ref, ls) in ultimo.items():
            nomes_im = {sem_acento(l.get(kn) or str(i)) for i, l in enumerate(ls)}
            pesos, vac, ina = [], [], []
            for l in ls:
                p = num(l.get(krec)) if krec else None
                if p is None and karea:
                    p = num(l.get(karea))
                p = p if p and p > 0 else 1.0
                v = num(l.get(kvac)) if kvac else None
                i = num(l.get(kina)) if kina else None
                pesos.append(p)
                vac.append(v)
                ina.append(i)

            def media(vals):
                par = [(v, p) for v, p in zip(vals, pesos) if v is not None]
                if not par:
                    return None
                m = sum(v * p for v, p in par) / sum(p for _, p in par)
                return m * 100 if m <= 1.0001 and max(v for v, _ in par) <= 1.0001 else m  # aceita fração ou percentual
            res[cn] = {"ref": ref[0], "imoveis": len(nomes_im), "vacF": media(vac), "inad": media(ina)}
        log(f"informe trimestral {ano}: imóveis de {len(res)} fundos")
        return res
    return {}


# ---------------- Yahoo: rendimentos e retorno total ----------------
def yahoo(tk):
    u = f"https://query1.finance.yahoo.com/v8/finance/chart/{tk}.SA?range=7y&interval=1mo&events=div&includeAdjustedClose=true"
    d = baixa(u, tentativas=2, ua=UA)
    if not d:
        return None
    try:
        j = json.loads(d)
        r = j["chart"]["result"][0]
    except Exception:  # noqa
        return None
    ts = r.get("timestamp") or []
    fe = ((r.get("indicators") or {}).get("quote") or [{}])[0].get("close") or []
    pts = [(datetime.fromtimestamp(t, timezone.utc).date().isoformat(), c) for t, c in zip(ts, fe) if c]
    div = sorted((datetime.fromtimestamp(v["date"], timezone.utc).date().isoformat(), float(v["amount"]))
                 for v in ((r.get("events") or {}).get("dividends") or {}).values() if v.get("amount"))
    return pts, div


def ajusta(pts, div):
    """conta do TradingView: a cada provento, multiplica tudo o que veio antes por (preço anterior − provento) ÷ preço anterior"""
    out = [[d, c] for d, c in pts]
    for dd, v in sorted(div, reverse=True):
        idx = max((i for i, (d, _) in enumerate(pts) if d < dd), default=None)
        if idx is None:
            continue
        f = 1 - v / pts[idx][1]
        if 0 < f < 1:
            for k in range(idx + 1):
                out[k][1] *= f
    return out


def mercado(tk):
    y = yahoo(tk)
    if not y or len(y[0]) < 12:
        return None
    pts, div = y
    adj = ajusta(pts, div)
    hoje = pts[-1][0]
    alvo = f"{int(hoje[:4]) - 5}{hoje[4:]}"
    base = [p for p in adj if p[0] <= alvo]
    rt = None
    if base:
        anos = (date.fromisoformat(hoje) - date.fromisoformat(base[-1][0])).days / 365.25
        if anos >= 4.5:
            rt = ((adj[-1][1] / base[-1][1]) ** (1 / anos) - 1) * 100
    rend = {}
    for d, v in div:
        rend[d[:4]] = rend.get(d[:4], 0) + v
    return {"rt": rt, "rend": {k: round(v, 4) for k, v in sorted(rend.items())}, "px": pts[-1][1]}


def main():
    geral, comp, ap = informes_mensais()
    if not geral:
        log("Nenhum informe mensal baixado. Parando.")
        sys.exit(1)
    cg, cc, ca = Col(geral), Col(comp), Col(ap)
    G, C, A = ultimo_por_fundo(geral, cg), ultimo_por_fundo(comp, cc), ultimo_por_fundo(ap, ca)

    # histórico mensal do complemento: taxa de administração em 12 meses e rendimento por cota pela CVM (reserva do Yahoo)
    kcc, kdc = cc(r"CNPJ_FUNDO.*"), cc(r"DATA_REFERENCIA")
    ktx = cc(r"PERCENTUAL_DESPESAS_TAXA_ADMINISTRACAO.*")
    kdy = cc(r"PERCENTUAL_DIVIDEND_YIELD.*")
    kvp = cc(r"VALOR_PATRIMONIAL_COTAS?.*")
    hist = {}
    for l in comp:
        cn = (l.get(kcc) or "").strip()
        dref = (l.get(kdc) or "")[:7]
        if cn and dref:
            hist.setdefault(cn, {})[dref] = l  # a versão mais nova sobrescreve
    tri = informe_trimestral()

    kc = cg(r"CNPJ_FUNDO.*")
    out, sem_tk = [], 0
    for cn, g in G.items():
        isin = g.get(cg(r"CODIGO_ISIN.*")) or ""
        tk = ticker_de_isin(isin)
        if not tk:
            sem_tk += 1
            continue
        if sem_acento(g.get(cg(r"MERCADO_NEGOCIACAO_BOLSA.*")) or "S")[:1] == "N":
            continue
        c, a = C.get(cn, {}), A.get(cn, {})
        pl = num(c.get(cc(r"PATRIMONIO_LIQUIDO.*")))
        vp = num(c.get(kvp))
        if not pl or pl <= 0:
            continue

        def v(*p):
            k = ca(*p)
            return (num(a.get(k)) or 0) if k else 0
        imov = v(r"DIREITOS_BENS_IMOVEIS.*") or (v(r"TERRENOS") + v(r"IMOVEIS_RENDA_ACABADOS") + v(r"IMOVEIS_RENDA_CONSTRUCAO") + v(r"IMOVEIS_VENDA_ACABADOS") + v(r"IMOVEIS_VENDA_CONSTRUCAO"))
        imov += v(r"ACOES_SOCIEDADES_ATIVIDADES_FII.*") + v(r"COTAS_SOCIEDADES_ATIVIDADES_FII.*")
        papel = v(r"CRI") + v(r"LCI") + v(r"LIG") + v(r"LETRAS_HIPOTECARIAS") + v(r"DEBENTURES") + v(r"CERTIFICADOS?_DEPOSITO.*") + v(r"NOTAS_PROMISSORIAS")
        fof = v(r"FII")
        tot = imov + papel + fof
        if tot <= 0:
            tipo = "Outro"
        elif fof / tot >= 0.5:
            tipo = "FoF"
        elif imov / tot >= 0.75:
            tipo = "Tijolo"
        elif papel / tot >= 0.75:
            tipo = "Papel"
        else:
            tipo = "Híbrido"
        passivo = v(r"TOTAL_PASSIVO")
        div = max(0.0, passivo - v(r"RENDIMENTOS_DISTRIBUIR") - v(r"TAXA_ADMINISTRACAO_PAGAR") - v(r"TAXA_PERFORMANCE_PAGAR"))

        h = hist.get(cn, {})
        meses = sorted(h)[-12:]
        taxa = None
        if ktx and meses:
            vals = [num(h[m].get(ktx)) for m in meses]
            vals = [x for x in vals if x is not None]
            if vals:
                taxa = sum(vals) / len(vals) * 12
                if taxa < 0.05:  # veio como fração
                    taxa *= 100
        rend_cvm = {}
        if kdy and kvp:
            for m, l in h.items():
                dy, vpc = num(l.get(kdy)), num(l.get(kvp))
                if dy is not None and vpc:
                    dy = dy if dy < 0.5 else dy / 100
                    rend_cvm[m[:4]] = rend_cvm.get(m[:4], 0) + dy * vpc

        ini = (g.get(cg(r"DATA_FUNCIONAMENTO.*")) or "")[:10]
        t = tri.get(cn, {})
        out.append({
            "tk": tk, "cnpj": cn,
            "n": (g.get(cg(r"NOME_FUNDO.*")) or "").strip(),
            "tipo": tipo,
            "seg": (g.get(cg(r"SEGMENTO_ATUACAO.*")) or "").strip(),
            "mandato": (g.get(cg(r"MANDATO.*")) or "").strip(),
            "gestao": (g.get(cg(r"TIPO_GESTAO.*")) or "").strip(),
            "adm": (g.get(cg(r"NOME_ADMINISTRADOR.*")) or "").strip(),
            "ini": ini or None,
            "ref": (c.get(cc(r"DATA_REFERENCIA")) or "")[:10],
            "pl": round(pl / 1e6, 2), "vp": vp,
            "cotistas": num(c.get(cc(r"TOTAL_NUMERO_COTISTAS.*"))),
            "taxa": round(taxa, 3) if taxa is not None else None,
            "div": round(div / pl * 100, 2),
            "mix": {"imoveis": round(imov / tot * 100, 1) if tot else None, "papel": round(papel / tot * 100, 1) if tot else None, "fii": round(fof / tot * 100, 1) if tot else None},
            "imoveis": t.get("imoveis"), "vacF": None if t.get("vacF") is None else round(t["vacF"], 2),
            "inad": None if t.get("inad") is None else round(t["inad"], 2), "refTri": t.get("ref"),
            "rendCvm": {k: round(x, 4) for k, x in sorted(rend_cvm.items())},
        })
    log(f"{len(out)} FIIs com ticker ({sem_tk} sem código de negociação)")

    if YAHOO:
        ok = 0
        for i, f in enumerate(out):
            m = mercado(f["tk"])
            if m:
                f.update(m)
                ok += 1
            time.sleep(0.35)
            if i % 50 == 0:
                log(f"  Yahoo: {i}/{len(out)}")
        log(f"Yahoo: cotação e rendimentos de {ok} fundos")

    os.makedirs(os.path.dirname(SAIDA) or ".", exist_ok=True)
    with open(SAIDA, "w", encoding="utf-8") as fo:
        json.dump({"gerado": datetime.now(timezone.utc).isoformat(timespec="seconds"), "fonte": "CVM (informes mensal e trimestral de FII) e Yahoo Finance", "fii": out}, fo, ensure_ascii=False, separators=(",", ":"))
    log(f"Gravado {SAIDA}")

    conf = ["HGLG11", "KNRI11", "MXRF11", "XPML11", "KNCR11", "VISC11", "BTLG11", "HGRU11"]
    linhas = ["## FIIs", f"{len(out)} fundos com ticker.", "", "| Fundo | Tipo | PL (R$ mi) | VP/cota | Taxa a.a. | Dívida % PL | Imóveis | Vacância | Inadimpl. | Retorno 5a | Rend. por ano |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for f in out:
        if f["tk"] in conf:
            linhas.append(f"| {f['tk']} | {f['tipo']} | {f['pl']} | {f['vp']} | {f['taxa']} | {f['div']} | {f['imoveis']} | {f['vacF']} | {f['inad']} | {None if f.get('rt') is None else round(f['rt'], 1)} | {dict(list((f.get('rend') or {}).items())[-6:])} |")
    linhas += ["", "### Colunas encontradas (para conferência)"] + RESUMO
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as s:
            s.write("\n".join(linhas) + "\n")
    else:
        print("\n".join(linhas))


if __name__ == "__main__":
    main()
