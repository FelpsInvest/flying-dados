"""Robô de fundamentos da Flying Academy.

Baixa os dados abertos da CVM (DFP e cadastro), calcula por empresa e por ano:
lucro líquido dos controladores, LPA (já corrigido por desdobramentos e grupamentos),
receita, EBIT, patrimônio líquido e, no último ano, dívida líquida/EBITDA.
Junta os códigos de negociação (FCA) e grava dados/fundamentos.json.

Roda no GitHub Actions, só com a biblioteca padrão do Python.
"""
import csv, io, json, os, re, sys, time, unicodedata, urllib.request, zipfile
from datetime import datetime, timezone

BASE = os.environ.get("CVM_BASE", "https://dados.cvm.gov.br/dados/CIA_ABERTA")
ANO_FIM = int(os.environ.get("ANO_FIM", datetime.now().year))
ANO_INI = int(os.environ.get("ANO_INI", 2010))
SAIDA = os.environ.get("SAIDA", "dados/fundamentos.json")
csv.field_size_limit(10**8)


def log(*a):
    print(*a, flush=True)


def baixa(url, tentativas=4):
    if not url.startswith("http"):
        if not os.path.exists(url):
            return None
        with open(url, "rb") as f:
            return f.read()
    for t in range(tentativas):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "robo-flying-academy/1.0"})
            with urllib.request.urlopen(req, timeout=180) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            log(f"  tentativa {t+1} falhou ({e}), {url}")
        except Exception as e:  # noqa
            log(f"  tentativa {t+1} falhou ({e}), {url}")
        time.sleep(5 * (t + 1))
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
    texto = dados.decode("latin-1")
    return list(csv.DictReader(io.StringIO(texto), delimiter=";"))


def col(linha, *padroes):
    """acha a coluna pelo nome, sem depender de maiúsculas ou pequenas mudanças"""
    for p in padroes:
        for k in linha.keys():
            if k and re.fullmatch(p, k.strip(), re.I):
                return k
    return None


def arquivo(z, trecho):
    for n in z.namelist():
        if trecho.lower() in n.lower():
            return le_csv(z.read(n))
    return []


def ultima_versao(linhas):
    """mantém só a versão mais nova de cada documento"""
    if not linhas:
        return linhas
    c_cnpj, c_ver = col(linhas[0], "CNPJ_CIA"), col(linhas[0], "VERSAO")
    melhor = {}
    for l in linhas:
        try:
            v = int(l.get(c_ver) or 0)
        except ValueError:
            v = 0
        k = l[c_cnpj]
        if v > melhor.get(k, -1):
            melhor[k] = v
    return [l for l in linhas if int(l.get(c_ver) or 0) == melhor[l[c_cnpj]]]


def ultimo(l):
    o = sem_acento(l.get("ORDEM_EXERC", ""))
    return o.startswith("ULTIMO")


def penultimo(l):
    return sem_acento(l.get("ORDEM_EXERC", "")).startswith("PENULTIMO")


def valor(l):
    v = num(l.get("VL_CONTA"))
    if v is None:
        return None
    return v * 1000 if sem_acento(l.get("ESCALA_MOEDA", "")) == "MIL" else v


def por_empresa(linhas):
    g = {}
    for l in linhas:
        g.setdefault(l["CNPJ_CIA"], []).append(l)
    return g


R_CONTROL = re.compile(r"ATRIBUID[OA]S? (AOS?|A) SOCIOS DA (EMPRESA )?CONTROLADORA")
R_LUCRO = re.compile(r"^LUCRO\s*/?\s*(OU\s+)?PREJUIZO\s+(LIQUIDO\s+)?(CONSOLIDADO\s+)?(DO|DOS)\s+(PERIODO|EXERCICIO)")


def lucro_de(rows, quem):
    """lucro dos controladores; se a linha vier zerada, usa o lucro total do período"""
    rs = [l for l in rows if quem(l)]
    cand = [l for l in rs if R_CONTROL.search(sem_acento(l["DS_CONTA"])) and l["CD_CONTA"].startswith("3.")]
    cand.sort(key=lambda l: len(l["CD_CONTA"]))
    for l in cand:
        v = valor(l)
        if v:
            return v
    cand = [l for l in rs if R_LUCRO.search(sem_acento(l["DS_CONTA"])) and l["CD_CONTA"].count(".") == 1]
    cand.sort(key=lambda l: l["CD_CONTA"], reverse=True)
    for l in cand:
        v = valor(l)
        if v:
            return v
    return None


def lpa_de(rows, quem):
    rs = [l for l in rows if quem(l) and l["CD_CONTA"].startswith("3.99")]
    basico = [l for l in rs if "BASIC" in sem_acento(l["DS_CONTA"]) or l["CD_CONTA"].startswith("3.99.01")]
    folhas = [l for l in (basico or rs) if l["CD_CONTA"].count(".") >= 3]
    for pref in (lambda d: "ON" in d.split() or "ORDIN" in d, lambda d: True):
        for l in sorted(folhas, key=lambda l: l["CD_CONTA"]):
            v = num(l.get("VL_CONTA"))
            if v and pref(sem_acento(l["DS_CONTA"])):
                return v
    return None


def conta(rows, quem, cod, regex=None):
    for l in rows:
        if quem(l) and l["CD_CONTA"] == cod and (not regex or re.search(regex, sem_acento(l["DS_CONTA"]))):
            return valor(l)
    return None


def soma_da(rows):
    s, achou = 0.0, False
    for l in rows:
        if ultimo(l) and l["CD_CONTA"].startswith("6.01.01.") and re.search(r"DEPRECIA|AMORTIZA|EXAUST", sem_acento(l["DS_CONTA"])):
            v = valor(l)
            if v is not None:
                s += abs(v)
                achou = True
    return s if achou else None


def processa_dfp(ano):
    dados = baixa(f"{BASE}/DOC/DFP/DADOS/dfp_cia_aberta_{ano}.zip")
    if not dados:
        log(f"DFP {ano}: não disponível")
        return {}
    z = zipfile.ZipFile(io.BytesIO(dados))
    pega = lambda nome: por_empresa(ultima_versao(arquivo(z, nome)))
    dre_c, dre_i = pega(f"DRE_con_{ano}"), pega(f"DRE_ind_{ano}")
    bpa_c, bpa_i = pega(f"BPA_con_{ano}"), pega(f"BPA_ind_{ano}")
    bpp_c, bpp_i = pega(f"BPP_con_{ano}"), pega(f"BPP_ind_{ano}")
    dfc_c, dfc_i = pega(f"DFC_MI_con_{ano}"), pega(f"DFC_MI_ind_{ano}")
    cap = {}
    for l in ultima_versao(arquivo(z, f"composicao_capital_{ano}")):
        ct = col(l, r"QT_ACAO_TOTAL_CAP_INTEGR")
        tt = col(l, r"QT_ACAO_TOTAL_TESOURO")
        tot, tes = num(l.get(ct)) if ct else None, num(l.get(tt)) if tt else None
        if tot:
            cap[l["CNPJ_CIA"]] = tot - (tes or 0)
    saida = {}
    for cnpj in set(dre_c) | set(dre_i):
        dre = dre_c.get(cnpj) or dre_i.get(cnpj)
        con = cnpj in dre_c
        if con and not lucro_de(dre, ultimo) and cnpj in dre_i and lucro_de(dre_i[cnpj], ultimo):
            dre, con = dre_i[cnpj], False
        bpa = (bpa_c if con else bpa_i).get(cnpj, [])
        bpp = (bpp_c if con else bpp_i).get(cnpj, [])
        dfc = (dfc_c if con else dfc_i).get(cnpj, [])
        r01 = next((l for l in dre if l["CD_CONTA"] == "3.01" and ultimo(l)), None)
        r05 = next((l for l in dre if l["CD_CONTA"] == "3.05" and ultimo(l)), None)
        fin = bool(
            (r01 and re.search(r"INTERMEDIA|PREMIO|SEGURO|RESSEGURO|PREVIDENCIA|CAPITALIZA", sem_acento(r01["DS_CONTA"])))
            or (r05 and "FINANCEIRO" not in sem_acento(r05["DS_CONTA"]))
        )
        divb = sum(v for v in [conta(bpp, ultimo, "2.01.04", "EMPRESTIMO"), conta(bpp, ultimo, "2.02.01", "EMPRESTIMO")] if v)
        caixa = sum(v for v in [conta(bpa, ultimo, "1.01.01"), conta(bpa, ultimo, "1.01.02")] if v)
        ebit = None if fin else conta(dre, ultimo, "3.05")
        da = None if fin else soma_da(dfc)
        saida[cnpj] = {
            "nome": dre[0].get("DENOM_CIA", "").strip(),
            "con": con,
            "lucro": lucro_de(dre, ultimo),
            "lucro_ant": lucro_de(dre, penultimo),
            "eps": lpa_de(dre, ultimo),
            "eps_ant": lpa_de(dre, penultimo),
            "receita": None if fin else conta(dre, ultimo, "3.01"),
            "ebit": ebit,
            "da": da,
            "pl": conta(bpp, ultimo, "2.03"),
            "divb": divb,
            "caixa": caixa,
            "fin": fin,
            "acoes": cap.get(cnpj),
            "lpa_desc": next((l["DS_CONTA"] for l in dre if l["CD_CONTA"] == "3.99"), ""),
        }
    log(f"DFP {ano}: {len(saida)} empresas")
    return saida


def processa_fca():
    for ano in range(ANO_FIM, ANO_FIM - 3, -1):
        dados = baixa(f"{BASE}/DOC/FCA/DADOS/fca_cia_aberta_{ano}.zip")
        if not dados:
            continue
        z = zipfile.ZipFile(io.BytesIO(dados))
        vm = arquivo(z, f"valor_mobiliario_{ano}")
        if not vm:
            continue
        c = lambda *p: col(vm[0], *p)
        cc, ct, cv, cm = c(r"CNPJ_Companhia", r"CNPJ_CIA"), c(r"Codigo_Negociacao"), c(r"Valor_Mobiliario"), c(r"Mercado")
        cf, cs = c(r"Data_Fim_Negociacao"), c(r"Segmento")
        cil, cin = c(r"Data_Inicio_Listagem"), c(r"Data_Inicio_Negociacao")
        res = {}
        for l in vm:
            tk = sem_acento(l.get(ct, "")).replace(" ", "")
            if not re.fullmatch(r"[A-Z][A-Z0-9]{3}\d{1,2}", tk):
                continue
            if cv and re.search(r"DEBENTURE|NOTA|BONUS|DIREITO|RECIBO", sem_acento(l.get(cv, ""))):
                continue
            if cm and l.get(cm) and "BALCAO" in sem_acento(l.get(cm)):
                continue
            e = res.setdefault(l[cc], {"tk": set(), "seg": "", "ini": []})
            e["tk"].add(tk)
            seg = (l.get(cs) or "").strip() if cs else ""
            if seg and len(seg) > len(e["seg"]):
                e["seg"] = seg
            for k in (cil, cin):
                d = (l.get(k) or "").strip() if k else ""
                if re.match(r"\d{4}-\d{2}-\d{2}", d):
                    e["ini"].append(d)
        log(f"FCA {ano}: {len(res)} empresas com código de negociação")
        return res
    log("FCA: nenhum arquivo encontrado")
    return {}


def processa_cadastro():
    dados = baixa(f"{BASE}/CAD/DADOS/cad_cia_aberta.csv")
    if not dados:
        return {}
    res = {}
    for l in le_csv(dados):
        cc = col(l, r"CNPJ_CIA")
        if not cc:
            continue
        res[l[cc]] = {
            "nome": (l.get("DENOM_COMERC") or l.get("DENOM_SOCIAL") or "").strip(),
            "setor": (l.get("SETOR_ATIV") or "").strip(),
            "sit": sem_acento(l.get("SIT", "")),
            "reg": (l.get("DT_REG") or "").strip(),
        }
    log(f"Cadastro: {len(res)} companhias")
    return res


def serie_lpa(anos, por_ano, acoes_ult):
    """LPA de cada ano na base de ações de hoje.
    A DFP de cada ano traz o LPA do ano e do ano anterior já corrigidos pelos desdobramentos
    daquele ano. Encadeando essas razões de trás para a frente, a série inteira fica comparável."""
    lpa = {}
    u = anos[-1]
    d = por_ano[u]
    base = None
    if d["eps"]:
        base = d["eps"]
        if acoes_ult and d["lucro"]:
            r = (d["lucro"] / d["eps"]) / acoes_ult
            if 300 < r < 3000:
                base = d["eps"] * 1000
            elif 0.0003 < r < 0.003:
                base = d["eps"] / 1000
    elif acoes_ult and d["lucro"] is not None:
        base = d["lucro"] / acoes_ult
    if base is None:
        return {}
    lpa[u] = base
    fator = base / d["eps"] if d["eps"] else None
    for i in range(len(anos) - 1, 0, -1):
        a, b = anos[i], anos[i - 1]
        da, db = por_ano[a], por_ano[b]
        if fator is None:
            break
        # LPA do ano anterior, na base da DFP do ano atual
        if da.get("eps_ant") is not None:
            lpa[b] = da["eps_ant"] * fator
        elif db.get("eps") is not None:
            lpa[b] = db["eps"] * fator
        else:
            break
        # fator para levar a DFP do ano anterior para a base de hoje
        if db.get("eps") and da.get("eps_ant") and (db["eps"] > 0) == (da["eps_ant"] > 0):
            k = da["eps_ant"] / db["eps"]
            fator = fator * (k if 0.02 < k < 50 else 1)
    return lpa


def main():
    anos_dfp = {}
    for ano in range(ANO_INI, ANO_FIM + 1):
        d = processa_dfp(ano)
        if d:
            anos_dfp[ano] = d
    if not anos_dfp:
        log("Nenhuma DFP baixada. Parando.")
        sys.exit(1)
    fca = processa_fca()
    cad = processa_cadastro()
    todos = sorted(anos_dfp)
    emp = []
    fora = {}
    for cnpj in sorted(set().union(*[set(v) for v in anos_dfp.values()])):
        f = fca.get(cnpj)
        nome = cad.get(cnpj, {}).get("nome") or next(anos_dfp[a][cnpj]["nome"] for a in todos if cnpj in anos_dfp[a])
        if not f or not f["tk"]:
            fora[nome] = "sem código de negociação no FCA"
            continue
        anos = [a for a in todos if cnpj in anos_dfp[a] and anos_dfp[a][cnpj]["lucro"] is not None]
        if len(anos) < 2:
            fora[nome] = f"só {len(anos)} ano(s) com lucro identificado"
            continue
        por_ano = {a: anos_dfp[a][cnpj] for a in anos}
        ult = por_ano[anos[-1]]
        lpa = serie_lpa(anos, por_ano, ult.get("acoes"))
        mi = lambda v: None if v is None else round(v / 1e6, 2)
        a = {str(x): [mi(por_ano[x]["lucro"]), None if x not in lpa else round(lpa[x], 4), mi(por_ano[x]["receita"]), mi(por_ano[x]["ebit"]), mi(por_ano[x]["pl"])] for x in anos}
        ebitda = None if ult["fin"] or ult["ebit"] is None else ult["ebit"] + (ult["da"] or 0)
        dl = None
        if not ult["fin"] and ebitda:
            divliq = (ult["divb"] or 0) - (ult["caixa"] or 0)
            dl = round(divliq / ebitda, 2) if ebitda > 0 else (round(divliq / ebitda, 2) if divliq < 0 else 99)
        c = cad.get(cnpj, {})
        ini = sorted(f["ini"])
        emp.append({
            "cnpj": cnpj,
            "n": c.get("nome") or ult["nome"],
            "setor": c.get("setor", ""),
            "seg": f["seg"],
            "ipo": ini[0][:10] if ini else None,
            "fin": 1 if ult["fin"] else 0,
            "con": 1 if ult["con"] else 0,
            "tk": sorted(f["tk"]),
            "ult": anos[-1],
            "acoes": ult.get("acoes"),
            "dl": dl,
            "ebitda": mi(ebitda),
            "divliq": mi((ult["divb"] or 0) - (ult["caixa"] or 0)) if not ult["fin"] else None,
            "da_ok": 1 if ult["da"] else 0,
            "a": a,
        })
    os.makedirs(os.path.dirname(SAIDA) or ".", exist_ok=True)
    out = {
        "gerado": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "fonte": "CVM, dados abertos (DFP, FCA e cadastro)",
        "colunas": ["lucro_mi", "lpa", "receita_mi", "ebit_mi", "pl_mi"],
        "emp": emp,
    }
    with open(SAIDA, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
    log(f"Gravado {SAIDA}: {len(emp)} empresas, {os.path.getsize(SAIDA)//1024} KB")
    # conferência: alguns nomes conhecidos, para comparar com sites como Status Invest e Investidor 10
    resumo = ["| Ticker | Último ano | Lucro (R$ mi) | LPA | Dív. líq./EBITDA | Segmento | Listagem |", "|---|---|---|---|---|---|---|"]
    for t in ["CXSE3", "ITUB3", "WEGE3", "PETR4", "BBAS3", "EGIE3", "RADL3", "TAEE11"]:
        e = next((x for x in emp if t in x["tk"]), None)
        if e:
            u = e["a"][str(e["ult"])]
            resumo.append(f"| {t} | {e['ult']} | {u[0]} | {u[1]} | {e['dl']} | {e['seg']} | {e['ipo']} |")
        else:
            resumo.append(f"| {t} | não encontrado | | | | | |")
    resumo += ["", "**Escala do LPA declarada (linha 3.99) em algumas empresas:**", ""]
    for t in ["CXSE3", "ITUB3", "WEGE3", "ABEV3", "TAEE11"]:
        e = next((x for x in emp if t in x["tk"]), None)
        if e:
            resumo.append(f"- {t}: {anos_dfp[e['ult']][e['cnpj']].get('lpa_desc', '')}")
    alvo = re.compile(r"ITAUSA|B3 S|BRASIL BOLSA|KLABIN|AMBEV|VALE|SUZANO|GERDAU|ELETROBRAS|AXIA|EQUATORIAL|TOTVS|EMBRAER")
    resumo += ["", "**Empresas conhecidas que ficaram de fora:**", ""]
    resumo += [f"- {n}: {m}" for n, m in sorted(fora.items()) if alvo.search(sem_acento(n))] or ["- nenhuma"]
    resumo += ["", f"Total fora do arquivo: {len(fora)}"]
    texto = "\n".join(resumo)
    log(texto)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("## Conferência dos fundamentos\n\n" + texto + "\n")


if __name__ == "__main__":
    main()
