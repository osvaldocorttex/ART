import sqlite3
from datetime import date, datetime, timedelta
import hashlib
import hmac
from html import escape
from pathlib import Path
import secrets
import socket
import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit.components.v1 as components

st.set_page_config(page_title="Controle ART", layout="wide", page_icon="🚛")

DB = "controle_viagens.db"

# =========================
# 1. FUNÇÕES DE APOIO
# =========================
def format_br(valor, prefixo="", casas_decimais=2):
    if valor is None or valor == "": return ""
    try:
        if casas_decimais == 0: return f"{int(float(valor)):,}".replace(",", ".")
        s = f"{float(valor):,.{casas_decimais}f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return f"{prefixo}{s}"
    except: return valor

def brl(valor): return format_br(valor, "R$ ")

def calcular_qtd_estadia(data_chegada, hora_chegada, data_descarregamento, hora_descarregamento):
    """Conta estadias nos marcos de 08:00 apos as primeiras 24h da chegada."""
    try:
        dt_chegada = pd.to_datetime(data_chegada, errors="coerce")
        dt_desc = pd.to_datetime(data_descarregamento, errors="coerce")
        if pd.isna(dt_chegada) or pd.isna(dt_desc):
            return 0

        txt_hora_chegada = str(hora_chegada or "").strip()
        txt_hora_desc = str(hora_descarregamento or "").strip()
        hora_chegada_ok = datetime.strptime(txt_hora_chegada, "%H:%M").time() if txt_hora_chegada else datetime.strptime("00:00", "%H:%M").time()
        hora_desc_ok = datetime.strptime(txt_hora_desc, "%H:%M").time() if txt_hora_desc else datetime.strptime("00:00", "%H:%M").time()

        dt_chegada_full = datetime.combine(dt_chegada.date(), hora_chegada_ok)
        dt_desc_full = datetime.combine(dt_desc.date(), hora_desc_ok)
        if dt_desc_full <= dt_chegada_full:
            return 0

        apos_24h = dt_chegada_full + timedelta(hours=24)
        primeiro_marco = datetime.combine(apos_24h.date(), datetime.strptime("08:00", "%H:%M").time())
        if primeiro_marco < apos_24h:
            primeiro_marco += timedelta(days=1)

        if dt_desc_full < primeiro_marco:
            return 0

        dias_passados = (dt_desc_full.date() - primeiro_marco.date()).days
        if dt_desc_full.time() < primeiro_marco.time():
            dias_passados -= 1
        return max(0, dias_passados + 1)
    except Exception:
        return 0

def format_pct(valor, casas_decimais=2):
    try:
        s = f"{float(valor):.{casas_decimais}f}".replace(".", ",")
        s = s.rstrip("0").rstrip(",")
        return f"{s}%"
    except:
        return f"{valor}%"

def format_pct_parametros(valores, casas_decimais=2):
    try:
        serie = pd.to_numeric(pd.Series(valores), errors="coerce").dropna()
        if serie.empty:
            return format_pct(0, casas_decimais)
        unicos = sorted({round(float(v), casas_decimais) for v in serie.tolist()})
        if len(unicos) == 1:
            return format_pct(unicos[0], casas_decimais)
        return f"{format_pct(unicos[0], casas_decimais)} a {format_pct(unicos[-1], casas_decimais)}"
    except Exception:
        return format_pct(0, casas_decimais)

def pct_parametro_relatorio(valores):
    serie = pd.to_numeric(pd.Series(valores), errors="coerce").dropna()
    if serie.empty:
        return 0.0
    unicos = sorted({round(float(v), 2) for v in serie.tolist()})
    if len(unicos) == 1:
        return float(unicos[0])
    return float(serie.mean())

def normalizar_tipo_combustivel(valor):
    txt = str(valor or "").upper().strip()
    if not txt:
        return ""
    return " ".join(txt.split())

def alerta_gravado(mensagem="✅ Gravado com sucesso!"):
    limpar_cache_app()
    st.success(mensagem)

def focar_campo_por_rotulo(rotulo):
    rotulo_js = str(rotulo or "").replace("\\", "\\\\").replace("'", "\\'")
    components.html(
        f"""
        <script>
        setTimeout(function() {{
            const doc = window.parent.document;
            const labels = Array.from(doc.querySelectorAll('label'));
            const alvo = labels.find((label) => (label.innerText || '').trim().includes('{rotulo_js}'));
            if (!alvo) return;
            const container = alvo.closest('[data-testid="stWidgetLabel"]')?.parentElement || alvo.parentElement;
            const campo = container?.querySelector('input, textarea, select, [contenteditable="true"]');
            if (campo) {{
                campo.focus();
                if (typeof campo.select === 'function') campo.select();
            }}
        }}, 350);
        </script>
        """,
        height=0,
        width=0,
    )

def conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA synchronous=NORMAL")
    c.execute("PRAGMA temp_store=MEMORY")
    c.execute("PRAGMA cache_size=-64000")
    c.execute("PRAGMA mmap_size=268435456")
    return c


def normalizar_estacao(nome):
    return " ".join(str(nome or "").strip().upper().split())


def nome_estacao_padrao():
    try:
        nome = socket.gethostname()
    except Exception:
        nome = ""
    return normalizar_estacao(nome) or "ESTACAO"


def gerar_hash_senha(senha):
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", str(senha).encode("utf-8"), salt.encode("utf-8"), 150000)
    return f"{salt}${digest.hex()}"


def conferir_senha(senha, senha_hash):
    try:
        salt, digest_salvo = str(senha_hash or "").split("$", 1)
        digest = hashlib.pbkdf2_hmac("sha256", str(senha).encode("utf-8"), salt.encode("utf-8"), 150000).hex()
        return hmac.compare_digest(digest, digest_salvo)
    except Exception:
        return False


def existem_usuarios_sistema():
    with conn() as c:
        row = c.execute("SELECT COUNT(*) AS total FROM usuarios_sistema").fetchone()
    return int(row["total"] or 0) > 0


def estacao_cadastrada(nome_estacao):
    estacao = normalizar_estacao(nome_estacao)
    if not estacao:
        return None
    with conn() as c:
        return c.execute(
            """SELECT e.id, e.nome_estacao, e.usuario_id, u.usuario
               FROM estacoes_trabalho e
               LEFT JOIN usuarios_sistema u ON u.id = e.usuario_id
               WHERE e.nome_estacao=? AND e.ativo=1""",
            (estacao,),
        ).fetchone()


def cadastrar_estacao_trabalho(nome_estacao, usuario_id=None):
    estacao = normalizar_estacao(nome_estacao)
    if not estacao:
        return
    with conn() as c:
        c.execute(
            """INSERT INTO estacoes_trabalho (nome_estacao, usuario_id, data_cadastro, ativo)
               VALUES (?, ?, ?, 1)
               ON CONFLICT(nome_estacao) DO UPDATE SET
                   usuario_id=COALESCE(excluded.usuario_id, estacoes_trabalho.usuario_id),
                   ativo=1""",
            (estacao, usuario_id, datetime.now().isoformat(timespec="seconds")),
        )


def cadastrar_usuario_sistema(usuario, senha, nome_estacao=None, is_admin=False, liberar_estacao=False):
    usuario_limpo = str(usuario or "").strip()
    if not usuario_limpo or not senha:
        return False, "Informe usuário e senha."
    if len(str(senha)) < 4:
        return False, "A senha deve ter pelo menos 4 caracteres."
    estacao = normalizar_estacao(nome_estacao)
    if liberar_estacao and not estacao:
        return False, "Informe o nome da estação de trabalho."

    try:
        with conn() as c:
            cur = c.execute(
                """INSERT INTO usuarios_sistema (usuario, senha_hash, data_cadastro, ativo, is_admin)
                   VALUES (?, ?, ?, 1, ?)""",
                (
                    usuario_limpo,
                    gerar_hash_senha(senha),
                    datetime.now().isoformat(timespec="seconds"),
                    1 if is_admin else 0,
                ),
            )
            usuario_id = int(cur.lastrowid)
            if liberar_estacao:
                c.execute(
                    """INSERT INTO estacoes_trabalho (nome_estacao, usuario_id, data_cadastro, ativo)
                       VALUES (?, ?, ?, 1)
                       ON CONFLICT(nome_estacao) DO UPDATE SET usuario_id=excluded.usuario_id, ativo=1""",
                    (estacao, usuario_id, datetime.now().isoformat(timespec="seconds")),
                )
        return True, "Usuário cadastrado com sucesso."
    except sqlite3.IntegrityError:
        return False, "Este usuário já está cadastrado."


def validar_usuario_sistema(usuario, senha):
    usuario_limpo = str(usuario or "").strip().lower()
    with conn() as c:
        row = c.execute(
            "SELECT id, usuario, senha_hash, is_admin FROM usuarios_sistema WHERE lower(usuario)=? AND ativo=1",
            (usuario_limpo,),
        ).fetchone()
    if row and conferir_senha(senha, row["senha_hash"]):
        return row
    return None


def alterar_senha_usuario_sistema(usuario_id, nova_senha):
    if len(str(nova_senha or "")) < 4:
        return False, "A senha deve ter pelo menos 4 caracteres."
    with conn() as c:
        c.execute(
            "UPDATE usuarios_sistema SET senha_hash=? WHERE id=?",
            (gerar_hash_senha(nova_senha), int(usuario_id)),
        )
    return True, "Senha alterada com sucesso."


def contar_admins_ativos(exceto_usuario_id=None):
    sql = "SELECT COUNT(*) AS total FROM usuarios_sistema WHERE ativo=1 AND is_admin=1"
    params = []
    if exceto_usuario_id is not None:
        sql += " AND id<>?"
        params.append(int(exceto_usuario_id))
    with conn() as c:
        row = c.execute(sql, params).fetchone()
    return int(row["total"] or 0)


def atualizar_usuario_sistema(usuario_id, usuario, nova_senha=None, is_admin=False, ativo=True):
    usuario_limpo = str(usuario or "").strip()
    if not usuario_limpo:
        return False, "Informe o nome do usuário."
    usuario_id = int(usuario_id)
    ativo_int = 1 if ativo else 0
    admin_int = 1 if is_admin else 0
    if (ativo_int == 0 or admin_int == 0) and contar_admins_ativos(exceto_usuario_id=usuario_id) == 0:
        return False, "Não é possível remover ou desativar o último administrador."
    try:
        with conn() as c:
            if nova_senha:
                if len(str(nova_senha)) < 4:
                    return False, "A senha deve ter pelo menos 4 caracteres."
                c.execute(
                    "UPDATE usuarios_sistema SET usuario=?, senha_hash=?, is_admin=?, ativo=? WHERE id=?",
                    (usuario_limpo, gerar_hash_senha(nova_senha), admin_int, ativo_int, usuario_id),
                )
            else:
                c.execute(
                    "UPDATE usuarios_sistema SET usuario=?, is_admin=?, ativo=? WHERE id=?",
                    (usuario_limpo, admin_int, ativo_int, usuario_id),
                )
        return True, "Usuário alterado com sucesso."
    except sqlite3.IntegrityError:
        return False, "Este nome de usuário já está cadastrado."


def deletar_usuario_sistema(usuario_id):
    usuario_id = int(usuario_id)
    if contar_admins_ativos(exceto_usuario_id=usuario_id) == 0:
        return False, "Não é possível deletar o último administrador."
    with conn() as c:
        c.execute("DELETE FROM estacoes_trabalho WHERE usuario_id=?", (usuario_id,))
        c.execute("DELETE FROM usuarios_sistema WHERE id=?", (usuario_id,))
    return True, "Usuário deletado com sucesso."


def listar_usuarios_sistema():
    with conn() as c:
        rows = c.execute(
            """SELECT id, usuario, is_admin, ativo, data_cadastro
               FROM usuarios_sistema
               ORDER BY usuario"""
        ).fetchall()
    return rows


def listar_estacoes_trabalho():
    with conn() as c:
        rows = c.execute(
            """SELECT e.id, e.nome_estacao, COALESCE(u.usuario, '') AS usuario, e.ativo, e.data_cadastro
               FROM estacoes_trabalho e
               LEFT JOIN usuarios_sistema u ON u.id = e.usuario_id
               ORDER BY e.nome_estacao"""
        ).fetchall()
    return rows


def atualizar_estacao_trabalho(estacao_id, nome_estacao, usuario_id=None, ativo=True):
    estacao = normalizar_estacao(nome_estacao)
    if not estacao:
        return False, "Informe o nome da estação."
    try:
        with conn() as c:
            c.execute(
                "UPDATE estacoes_trabalho SET nome_estacao=?, usuario_id=?, ativo=? WHERE id=?",
                (estacao, usuario_id, 1 if ativo else 0, int(estacao_id)),
            )
        return True, "Estação alterada com sucesso."
    except sqlite3.IntegrityError:
        return False, "Esta estação já está cadastrada."


def deletar_estacao_trabalho(estacao_id):
    with conn() as c:
        c.execute("DELETE FROM estacoes_trabalho WHERE id=?", (int(estacao_id),))
    return True, "Estação deletada com sucesso."


def proteger_abertura_sistema():
    if st.session_state.get("acesso_liberado"):
        return

    primeiro_acesso = not existem_usuarios_sistema()

    st.markdown("### Acesso ao sistema")

    if primeiro_acesso:
        st.caption("Crie o usuário administrador para acessar o sistema.")
        with st.form("form_primeiro_acesso_sistema"):
            usuario = st.text_input("Usuário administrador")
            senha = st.text_input("Senha", type="password")
            enviar = st.form_submit_button("Cadastrar e abrir sistema", type="primary")

        if not enviar:
            st.stop()

        ok, msg = cadastrar_usuario_sistema(usuario, senha, is_admin=True, liberar_estacao=False)
        if ok:
            st.session_state.acesso_liberado = True
            st.session_state.usuario_logado = str(usuario or "").strip()
            st.session_state.usuario_admin = True
            st.success(msg)
            st.rerun()
        st.error(msg)
        st.stop()

    st.caption("Informe usuário e senha para abrir o sistema.")
    with st.form("form_acesso_sistema"):
        usuario = st.text_input("Usuário")
        senha = st.text_input("Senha", type="password")
        enviar = st.form_submit_button("Abrir sistema", type="primary")

    if not enviar:
        st.stop()

    row_usuario = validar_usuario_sistema(usuario, senha)
    if not row_usuario:
        st.error("Usuário ou senha inválidos.")
        st.stop()

    usuario_admin = bool(int(row_usuario["is_admin"] or 0) == 1)
    st.session_state.acesso_liberado = True
    st.session_state.usuario_logado = row_usuario["usuario"]
    st.session_state.usuario_admin = usuario_admin
    st.rerun()

def salvar_anexos_pedido_fornecedor(arquivos):
    if not arquivos:
        return []
    pasta_destino = Path("uploads") / "pedidos_fornecedor"
    pasta_destino.mkdir(parents=True, exist_ok=True)
    anexos_salvos = []
    for arq in arquivos:
        nome_original = Path(str(getattr(arq, "name", "") or "anexo")).name
        timestamp = datetime.now().strftime("%d%m%y%H%M%S%f")
        nome_final = f"{Path(nome_original).stem}-{timestamp}{Path(nome_original).suffix}"
        caminho_final = pasta_destino / nome_final
        with caminho_final.open("wb") as f_out:
            f_out.write(arq.getbuffer())
        anexos_salvos.append(
            {
                "nome_arquivo": nome_original,
                "caminho_arquivo": str(caminho_final),
            }
        )
    return anexos_salvos


def salvar_documento_anotacao(arquivo):
    if not arquivo:
        return None
    pasta_destino = Path("uploads") / "anotacoes"
    pasta_destino.mkdir(parents=True, exist_ok=True)
    nome_original = Path(str(getattr(arquivo, "name", "") or "documento")).name
    timestamp = datetime.now().strftime("%d%m%y%H%M%S%f")
    nome_final = f"{Path(nome_original).stem}-{timestamp}{Path(nome_original).suffix}"
    caminho_final = pasta_destino / nome_final
    with caminho_final.open("wb") as f_out:
        f_out.write(arquivo.getbuffer())
    return {
        "nome_arquivo": nome_original,
        "caminho_arquivo": str(caminho_final),
    }


def salvar_documentos_anotacao(arquivos):
    if not arquivos:
        return []
    if not isinstance(arquivos, list):
        arquivos = [arquivos]
    documentos_salvos = []
    for arquivo in arquivos:
        documento_salvo = salvar_documento_anotacao(arquivo)
        if documento_salvo:
            documentos_salvos.append(documento_salvo)
    return documentos_salvos


def carregar_fornecedores_para_pecas():
    with conn() as c:
        rows = c.execute("SELECT id, codigo, nome FROM fornecedores ORDER BY nome ASC").fetchall()
    labels = [f"{r['codigo']} - {r['nome']}" for r in rows]
    mapa = {f"{r['codigo']} - {r['nome']}": int(r["id"]) for r in rows}
    return labels, mapa


def carregar_pecas_manutencao(manutencao_id):
    with conn() as c:
        rows = c.execute(
            """SELECT mp.id, mp.fornecedor_id, f.codigo, f.nome, mp.data_compra, mp.num_nf, mp.descricao_peca, mp.valor_peca
               FROM manutencoes_pecas mp
               LEFT JOIN fornecedores f ON f.id = mp.fornecedor_id
               WHERE mp.manutencao_id=?
               ORDER BY mp.id ASC""",
            (int(manutencao_id),),
        ).fetchall()
    dados = []
    for row in rows:
        fornecedor_label = f"{row['codigo']} - {row['nome']}" if row["codigo"] and row["nome"] else ""
        dados.append(
            {
                "Fornecedor": fornecedor_label,
                "Data Compra": pd.to_datetime(row["data_compra"], errors="coerce") if row["data_compra"] else pd.NaT,
                "N. NF": row["num_nf"] or "",
                "Descricao da Peca": row["descricao_peca"] or "",
                "Valor da Peca": float(row["valor_peca"] or 0),
                "Excluir": False,
            }
        )
    return pd.DataFrame(dados, columns=["Fornecedor", "Data Compra", "N. NF", "Descricao da Peca", "Valor da Peca", "Excluir"])


def dataframe_pecas_vazio():
    return pd.DataFrame(
        {
            "Fornecedor": pd.Series(dtype="object"),
            "Data Compra": pd.Series(dtype="datetime64[ns]"),
            "N. NF": pd.Series(dtype="object"),
            "Descricao da Peca": pd.Series(dtype="object"),
            "Valor da Peca": pd.Series(dtype="float64"),
            "Excluir": pd.Series(dtype="bool"),
        }
    )


def normalizar_data_iso(valor):
    if valor is None or valor == "":
        return None
    try:
        dt = pd.to_datetime(valor, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date().isoformat()
    except Exception:
        return None


def texto_celula_editor(valor):
    if valor is None:
        return ""
    try:
        if pd.isna(valor):
            return ""
    except Exception:
        pass
    txt = str(valor).strip()
    return "" if txt.lower() in ("nan", "nat", "none") else txt


def preparar_pecas_manutencao(df_pecas, mapa_fornecedores):
    itens = []
    if df_pecas is None or df_pecas.empty:
        return itens, 0.0, None
    for _, row in df_pecas.iterrows():
        if bool(row.get("Excluir", False)):
            continue
        fornecedor_label = texto_celula_editor(row.get("Fornecedor"))
        data_compra = normalizar_data_iso(row.get("Data Compra"))
        num_nf = texto_celula_editor(row.get("N. NF"))
        descricao = texto_celula_editor(row.get("Descricao da Peca"))
        valor_raw = row.get("Valor da Peca")
        try:
            valor = 0.0 if pd.isna(valor_raw) else float(valor_raw or 0)
        except Exception:
            valor = 0.0

        if not any([fornecedor_label, data_compra, num_nf, descricao, valor > 0]):
            continue
        if not fornecedor_label or fornecedor_label not in mapa_fornecedores:
            return [], 0.0, "Selecione o fornecedor em todas as linhas de peças preenchidas."
        itens.append(
            {
                "fornecedor_id": mapa_fornecedores[fornecedor_label],
                "data_compra": data_compra,
                "num_nf": num_nf,
                "descricao_peca": descricao,
                "valor_peca": valor,
            }
        )
    return itens, sum(item["valor_peca"] for item in itens), None


def salvar_pecas_manutencao(c, manutencao_id, itens):
    c.execute("DELETE FROM manutencoes_pecas WHERE manutencao_id=?", (int(manutencao_id),))
    for item in itens:
        c.execute(
            """INSERT INTO manutencoes_pecas
               (manutencao_id, fornecedor_id, data_compra, num_nf, descricao_peca, valor_peca)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                int(manutencao_id),
                item["fornecedor_id"],
                item["data_compra"],
                item["num_nf"],
                item["descricao_peca"],
                item["valor_peca"],
            ),
        )


def excluir_peca_manutencao(peca_id):
    with conn() as c:
        peca = c.execute(
            "SELECT manutencao_id FROM manutencoes_pecas WHERE id=?",
            (int(peca_id),),
        ).fetchone()
        if not peca:
            return False
        manutencao_id = int(peca["manutencao_id"])
        c.execute("DELETE FROM manutencoes_pecas WHERE id=?", (int(peca_id),))
        total_pecas = c.execute(
            "SELECT COALESCE(SUM(valor_peca), 0) FROM manutencoes_pecas WHERE manutencao_id=?",
            (manutencao_id,),
        ).fetchone()[0]
        c.execute("UPDATE manutencoes SET valor_pecas=? WHERE id=?", (float(total_pecas or 0), manutencao_id))
    return True


def excluir_manutencao_completa(manutencao_id):
    anexos = []
    with conn() as c:
        anexos = c.execute(
            """SELECT caminho_arquivo
               FROM manutencoes_anexos
               WHERE manutencao_id=?""",
            (int(manutencao_id),),
        ).fetchall()
        c.execute("DELETE FROM manutencoes_anexos WHERE manutencao_id=?", (int(manutencao_id),))
        c.execute("DELETE FROM manutencoes_pecas WHERE manutencao_id=?", (int(manutencao_id),))
        c.execute("DELETE FROM manutencoes WHERE id=?", (int(manutencao_id),))

    for anexo in anexos:
        caminho = str(anexo["caminho_arquivo"] or "").strip()
        if not caminho:
            continue
        try:
            Path(caminho).unlink(missing_ok=True)
        except Exception:
            pass


def codigo_fornecedor_oficina(oficina_id):
    return f"OFICINA-{int(oficina_id):04d}"


def importar_oficinas_para_fornecedores():
    with conn() as c:
        oficinas = c.execute(
            """SELECT id, nome, cnpj, endereco, numero, complemento, bairro, cidade, estado, cep,
                      inscricao_estadual, telefone_contato, email, responsavel
               FROM oficinas
               ORDER BY nome ASC"""
        ).fetchall()
        inseridos = 0
        atualizados = 0

        for oficina in oficinas:
            codigo = codigo_fornecedor_oficina(oficina["id"])
            nome = str(oficina["nome"] or "").strip()
            cnpj = str(oficina["cnpj"] or "").strip()
            existente = c.execute(
                """SELECT id
                   FROM fornecedores
                   WHERE codigo = ?
                      OR origem_oficina_id = ?
                      OR (? <> '' AND cnpj = ?)
                      OR UPPER(TRIM(nome)) = UPPER(TRIM(?))
                   ORDER BY CASE WHEN codigo = ? THEN 0 ELSE 1 END, id
                   LIMIT 1""",
                (codigo, int(oficina["id"]), cnpj, cnpj, nome, codigo),
            ).fetchone()

            dados = (
                nome,
                cnpj,
                oficina["inscricao_estadual"] or "",
                oficina["endereco"] or "",
                oficina["numero"] or "",
                oficina["complemento"] or "",
                oficina["cidade"] or "",
                oficina["estado"] or "",
                oficina["bairro"] or "",
                oficina["cep"] or "",
                oficina["telefone_contato"] or "",
                oficina["email"] or "",
                oficina["responsavel"] or "",
                int(oficina["id"]),
            )

            if existente:
                c.execute(
                    """UPDATE fornecedores
                       SET nome=?, cnpj=?, insc_est=?, endereco=?, numero=?, complemento=?,
                           cidade=?, estado=?, bairro=?, cep=?, telefone=?, email=?,
                           responsavel=?, origem_oficina_id=?
                       WHERE id=?""",
                    (*dados, int(existente["id"])),
                )
                atualizados += 1
            else:
                c.execute(
                    """INSERT INTO fornecedores
                       (codigo, nome, cnpj, insc_est, endereco, numero, complemento, cidade,
                        estado, bairro, cep, telefone, email, responsavel, origem_oficina_id, pix)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '')""",
                    (codigo, *dados),
                )
                inseridos += 1
    return inseridos, atualizados


def movimentos_fornecedor(c, fornecedor_id):
    fornecedor_id = int(fornecedor_id)
    usos = []
    qtd_manut = c.execute("SELECT COUNT(*) FROM manutencoes WHERE oficina_id=?", (fornecedor_id,)).fetchone()[0]
    if qtd_manut:
        usos.append(f"Manutenção: {qtd_manut}")
    qtd_pecas = c.execute("SELECT COUNT(*) FROM manutencoes_pecas WHERE fornecedor_id=?", (fornecedor_id,)).fetchone()[0]
    if qtd_pecas:
        usos.append(f"Peças de manutenção: {qtd_pecas}")
    qtd_cp = c.execute("SELECT COUNT(*) FROM contas_pagar WHERE fornecedor=?", (str(fornecedor_id),)).fetchone()[0]
    if qtd_cp:
        usos.append(f"Contas a pagar: {qtd_cp}")
    return usos


def cache_data_compat(ttl=90, show_spinner=False):
    cache_data_fn = getattr(st, "cache_data", None)
    if callable(cache_data_fn):
        return cache_data_fn(ttl=ttl, show_spinner=show_spinner)
    cache_fn = getattr(st, "cache", None)
    if callable(cache_fn):
        return cache_fn(ttl=ttl)
    def _decorator(func):
        return func
    return _decorator


@cache_data_compat(ttl=90, show_spinner=False)
def _carregar_bootstrap_app():
    with conn() as c:
        p_row = c.execute("SELECT * FROM parametros WHERE id=1").fetchone()
        p_real_local = dict(p_row) if p_row else {}
        lista_cidades_local = [cid["nome"] for cid in c.execute("SELECT nome FROM cidades ORDER BY nome ASC").fetchall()]
        veiculos_db_local = [dict(v) for v in c.execute("SELECT placa, descricao FROM veiculos ORDER BY descricao ASC").fetchall()]
        oficinas_db_local = [dict(o) for o in c.execute("SELECT id, nome FROM oficinas ORDER BY nome ASC").fetchall()]
        fornecedores_db_local = [dict(f) for f in c.execute("SELECT id, codigo, nome FROM fornecedores ORDER BY nome ASC").fetchall()]
        obrigacoes_db_local = [dict(o) for o in c.execute("SELECT id, descricao_obrigacao FROM obrigacoes ORDER BY descricao_obrigacao ASC").fetchall()]
        last_v = c.execute("SELECT diesel, consumo, arla, consumo_arla FROM viagens ORDER BY id DESC LIMIT 1").fetchone()
        last_v_local = dict(last_v) if last_v else None
    return {
        "p_real": p_real_local,
        "lista_cidades": lista_cidades_local,
        "veiculos_db": veiculos_db_local,
        "oficinas_db": oficinas_db_local,
        "fornecedores_db": fornecedores_db_local,
        "obrigacoes_db": obrigacoes_db_local,
        "last_v": last_v_local,
    }


def limpar_cache_bootstrap():
    try:
        _carregar_bootstrap_app.clear()
    except Exception:
        pass


@cache_data_compat(ttl=90, show_spinner=False)
def _carregar_historico_parametros_raw():
    with conn() as c:
        return pd.read_sql(
            """SELECT COALESCE(NULLIF(TRIM(veiculo_placa), ''), 'GERAL') AS veiculo_placa,
                      vigencia_data, consumo, manut, pneu, depre, motora_fixo, motora_pct,
                      seguro, seguro_vida_motorista, financiamento, pagto_ipva, cmp_custo_escritorio, vl_custo_rastreador, imposto_pct, valor_frete_mensal_fixo,
                      qtde_pneu, vl_gasto_pneu_km
               FROM parametros_historico
               ORDER BY veiculo_placa ASC, date(vigencia_data) ASC, id ASC""",
            c,
        )


@cache_data_compat(ttl=5, show_spinner=False)
def _carregar_viagens_periodo_raw(data_ini_iso: str, data_fim_iso: str):
    with conn() as c:
        return pd.read_sql(
            "SELECT * FROM viagens WHERE data BETWEEN ? AND ? ORDER BY data ASC, id ASC",
            c,
            params=(data_ini_iso, data_fim_iso),
        )

def limpar_cache_viagens():
    try:
        _carregar_viagens_periodo_raw.clear()
    except Exception:
        pass


@cache_data_compat(ttl=120, show_spinner=False)
def _carregar_rotas_ref_exec_raw():
    with conn() as c:
        return pd.read_sql("SELECT origem, destino, valor_ton FROM rotas", c)


def limpar_cache_app():
    funcoes_cache = [
        "_carregar_bootstrap_app",
        "_carregar_historico_parametros_raw",
        "_carregar_viagens_periodo_raw",
        "_carregar_rotas_ref_exec_raw",
    ]
    for nome_func in funcoes_cache:
        func = globals().get(nome_func)
        clear_func = getattr(func, "clear", None)
        if callable(clear_func):
            try:
                clear_func()
            except Exception:
                pass
    cache_data_fn = getattr(st, "cache_data", None)
    clear_cache_data = getattr(cache_data_fn, "clear", None)
    if callable(clear_cache_data):
        try:
            clear_cache_data()
        except Exception:
            pass


limpar_cache_app()

# =========================
# 2. INICIALIZAÇÃO DO BANCO
# =========================
# --- INÍCIO DO BLOCO INIT_DB ---

if 'simulacao_ativa' not in st.session_state:
    st.session_state.simulacao_ativa = False
if 'p_simulado' not in st.session_state:
    st.session_state.p_simulado = {}


def init_db():
    with conn() as c:
        # 1. Criação das tabelas base (se não existirem)
        c.execute("""CREATE TABLE IF NOT EXISTS usuarios_sistema (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            usuario TEXT NOT NULL UNIQUE,
            senha_hash TEXT NOT NULL,
            data_cadastro TEXT,
            ativo INTEGER DEFAULT 1,
            is_admin INTEGER DEFAULT 0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS estacoes_trabalho (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome_estacao TEXT NOT NULL UNIQUE,
            usuario_id INTEGER,
            data_cadastro TEXT,
            ativo INTEGER DEFAULT 1
        )""")

        cursor_usuarios = c.execute("PRAGMA table_info(usuarios_sistema)")
        colunas_usuarios = [coluna[1] for coluna in cursor_usuarios.fetchall()]
        if "is_admin" not in colunas_usuarios:
            c.execute("ALTER TABLE usuarios_sistema ADD COLUMN is_admin INTEGER DEFAULT 0")
        c.execute(
            """UPDATE usuarios_sistema
               SET is_admin=1
               WHERE id = (SELECT id FROM usuarios_sistema ORDER BY id LIMIT 1)
                 AND NOT EXISTS (SELECT 1 FROM usuarios_sistema WHERE is_admin=1)"""
        )

        c.execute("""CREATE TABLE IF NOT EXISTS parametros (
            id INTEGER PRIMARY KEY CHECK (id=1),
            consumo REAL DEFAULT 2.5, manut REAL DEFAULT 0.25, 
            pneu REAL DEFAULT 0.12, depre REAL DEFAULT 0.30, 
            motora_fixo REAL DEFAULT 2500.0, motora_pct REAL DEFAULT 10.0,
            seguro REAL DEFAULT 2750.0, seguro_vida_motorista REAL DEFAULT 0.0, financiamento REAL DEFAULT 0.0, pagto_ipva REAL DEFAULT 0.0,
            meta_faturamento REAL DEFAULT 50000.0,
            valor_frete_mensal_fixo REAL DEFAULT 0.0,
            qtde_pneu REAL DEFAULT 1.0,
            vl_gasto_pneu_km REAL DEFAULT 0.12,
            data_filtro_ini TEXT DEFAULT '2026-01-01',
            data_filtro_fim TEXT DEFAULT '2026-12-31',
            filtro_placa_default TEXT DEFAULT 'Todas as placas',
            cmp_valor_litro_diesel REAL DEFAULT 0.0,
            cmp_consumo_001 REAL DEFAULT 2.5,
            cmp_consumo_002 REAL DEFAULT 2.5,
            cmp_consumo_003 REAL DEFAULT 2.5,
            cmp_custo_escritorio REAL DEFAULT 0.0,
            vl_custo_rastreador REAL DEFAULT 0.0,
            imposto_pct REAL DEFAULT 0.0)""")
        
        c.execute("INSERT OR IGNORE INTO parametros (id) VALUES (1)")
        c.execute("CREATE TABLE IF NOT EXISTS veiculos (id INTEGER PRIMARY KEY AUTOINCREMENT, descricao TEXT, modelo TEXT, ano TEXT, cor TEXT, placa TEXT UNIQUE, renavan TEXT, observacao TEXT, quantidade_eixo INTEGER DEFAULT 0)")
        c.execute("CREATE TABLE IF NOT EXISTS viagens (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, cliente TEXT, origem TEXT, destino TEXT, km REAL, toneladas REAL, valor_ton REAL, valor_km REAL DEFAULT 0.0, tipo_cobranca TEXT DEFAULT 'TONELADA', pedagio REAL, qtd_pedagio INTEGER DEFAULT 0, gasto_extra REAL DEFAULT 0.0, pagto_estadia REAL DEFAULT 0.0, valor_adicional_frete REAL DEFAULT 0.0, descricao_valor_adicional_frete TEXT, descricao_gasto_extra TEXT, diesel REAL, consumo REAL, arla REAL DEFAULT 0.0, consumo_arla REAL DEFAULT 0.0, hora_carregamento TEXT, data_chegada TEXT, hora_chegada TEXT, data_descarregamento TEXT, hora_descarregamento TEXT, obs TEXT, nf TEXT, veiculo_placa TEXT)")
        c.execute("CREATE TABLE IF NOT EXISTS cidades (id INTEGER PRIMARY KEY AUTOINCREMENT, nome TEXT UNIQUE)")
        c.execute("CREATE TABLE IF NOT EXISTS rotas (id INTEGER PRIMARY KEY AUTOINCREMENT, origem TEXT, destino TEXT, nome_empresa_origem TEXT, nome_empresa_destino TEXT, km REAL, valor_ton REAL DEFAULT 0.0, UNIQUE(origem, destino))")
        c.execute("CREATE TABLE IF NOT EXISTS abastecimentos (id INTEGER PRIMARY KEY AUTOINCREMENT, data TEXT, local TEXT, doc_nf TEXT, km_inicial REAL, tipo_combustivel TEXT, qtde_litros REAL, valor_unit REAL, desconto REAL DEFAULT 0.0, total_gasto REAL, veiculo_placa TEXT)")
        c.execute("""CREATE TABLE IF NOT EXISTS oficinas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT,
            cnpj TEXT,
            endereco TEXT,
            numero TEXT,
            complemento TEXT,
            bairro TEXT,
            cidade TEXT,
            estado TEXT,
            cep TEXT,
            inscricao_estadual TEXT,
            telefone_contato TEXT,
            email TEXT,
            responsavel TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS fornecedores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            codigo TEXT UNIQUE,
            nome TEXT,
            cnpj TEXT,
            insc_est TEXT,
            endereco TEXT,
            numero TEXT,
            complemento TEXT,
            cidade TEXT,
            estado TEXT,
            bairro TEXT,
            cep TEXT,
            telefone TEXT,
            email TEXT,
            responsavel TEXT,
            origem_oficina_id INTEGER,
            pix TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS obrigacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao_obrigacao TEXT UNIQUE
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS contas_pagar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            fornecedor TEXT,
            categoria TEXT,
            veiculo_placa TEXT,
            n_documento TEXT,
            data_emissao TEXT,
            data_vencimento TEXT NOT NULL,
            valor REAL NOT NULL DEFAULT 0.0,
            data_pagamento TEXT,
            forma_pagamento TEXT DEFAULT 'PIX',
            observacao TEXT,
            data_cadastro TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS contas_receber (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT NOT NULL,
            cliente TEXT,
            categoria TEXT,
            veiculo_placa TEXT,
            n_documento TEXT,
            data_emissao TEXT,
            data_vencimento TEXT NOT NULL,
            valor REAL NOT NULL DEFAULT 0.0,
            data_recebimento TEXT,
            forma_recebimento TEXT DEFAULT 'PIX',
            observacao TEXT,
            data_cadastro TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS me_lembra (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data_ativacao TEXT,
            data_vencimento TEXT,
            data_alerta TEXT,
            descricao TEXT,
            descricao_veiculo TEXT,
            frota TEXT,
            dias_alerta INTEGER DEFAULT 30,
            popup_ativo INTEGER DEFAULT 1
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS me_lembra_descricoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            descricao TEXT UNIQUE,
            frota TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS me_lembra_frotas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            frota TEXT UNIQUE
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS anotacoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT,
            descricao TEXT,
            documento_nome TEXT,
            documento_arquivo TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS anotacoes_anexos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            anotacao_id INTEGER,
            nome_arquivo TEXT,
            caminho_arquivo TEXT,
            data_inclusao TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS controle_trocas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tipo_servico TEXT,
            data_servico TEXT,
            veiculo_placa TEXT,
            descricao_veiculo TEXT,
            km_atual REAL,
            km_proxima REAL,
            detalhes TEXT,
            data_vencimento TEXT,
            dias_alerta INTEGER DEFAULT 30
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS tipos_servico_troca (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT UNIQUE
        )""")
        default_servicos_troca = [
            "Troca de Óleo Motor", "Troca de Óleo Câmbio", "Troca de Óleo Diferencial",
            "Filtro de Ar", "Filtro de Combustível", "Revisão Geral",
            "Troca Pneu Dianteiro", "Troca Pneu Tração", "Troca Pneu Truck", "Outros"
        ]
        for serv in default_servicos_troca:
            c.execute("INSERT OR IGNORE INTO tipos_servico_troca (nome) VALUES (?)", (serv,))
        c.execute("""CREATE TABLE IF NOT EXISTS parametros_historico (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            veiculo_placa TEXT DEFAULT 'GERAL',
            vigencia_data TEXT,
            consumo REAL,
            manut REAL,
            pneu REAL,
            depre REAL,
            motora_fixo REAL,
            motora_pct REAL,
            seguro REAL,
            seguro_vida_motorista REAL,
            financiamento REAL,
            pagto_ipva REAL,
            cmp_custo_escritorio REAL,
            vl_custo_rastreador REAL,
            imposto_pct REAL,
            valor_frete_mensal_fixo REAL,
            qtde_pneu REAL,
            vl_gasto_pneu_km REAL,
            UNIQUE(veiculo_placa, vigencia_data)
        )""")
        
        # Tabela de Manutenção
        c.execute("""CREATE TABLE IF NOT EXISTS manutencoes (
            id INTEGER PRIMARY KEY AUTOINCREMENT, 
            data_entrada TEXT, data_fim TEXT, veiculo_placa TEXT, 
            oficina_id INTEGER, defeito TEXT, servico TEXT, 
            valor_mo REAL, valor_pecas REAL, garantia TEXT, 
            km_servico REAL, mecanico TEXT, obs TEXT)""")
        c.execute("""CREATE TABLE IF NOT EXISTS manutencoes_pecas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manutencao_id INTEGER,
            fornecedor_id INTEGER,
            data_compra TEXT,
            num_nf TEXT,
            descricao_peca TEXT,
            valor_peca REAL DEFAULT 0.0
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS manutencoes_anexos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            manutencao_id INTEGER,
            nome_arquivo TEXT,
            caminho_arquivo TEXT,
            data_inclusao TEXT
        )""")
        c.execute("""CREATE TABLE IF NOT EXISTS praca_pedagio (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rota TEXT,
            praca_pedagio TEXT,
            rodovia TEXT,
            concessionaria TEXT,
            sentido_viagem TEXT,
            quantidade_eixos REAL DEFAULT 1.0,
            valor_por_eixo REAL DEFAULT 0.0,
            valor_todos_eixos REAL DEFAULT 0.0
        )""")
# --- ATUALIZAÇÃO AUTOMÁTICA DAS COLUNAS MANUTENÇÃO ---
        cursor = c.execute("PRAGMA table_info(manutencoes)")
        colunas_existentes = [coluna[1] for coluna in cursor.fetchall()]
        
        novas_colunas = {
            'num_os': 'TEXT',
            'num_nf': 'TEXT',
            'data_vencimento_garantia': 'TEXT',
            'observacao_adicional': 'TEXT',
            'custo_transporte': 'REAL DEFAULT 0.0',
            'custo_transporte_ida': 'REAL DEFAULT 0.0',
            'custo_transporte_retorno': 'REAL DEFAULT 0.0',
            'pedido_fornecedor_arquivo': 'TEXT'
        }

        for col, tipo in novas_colunas.items():
            if col not in colunas_existentes:
                c.execute(f"ALTER TABLE manutencoes ADD COLUMN {col} {tipo}")

        cursor_anot = c.execute("PRAGMA table_info(anotacoes)")
        colunas_anot = [coluna[1] for coluna in cursor_anot.fetchall()]
        novas_colunas_anot = {
            "documento_nome": "TEXT",
            "documento_arquivo": "TEXT",
        }
        for col, tipo in novas_colunas_anot.items():
            if col not in colunas_anot:
                c.execute(f"ALTER TABLE anotacoes ADD COLUMN {col} {tipo}")

        cursor_of = c.execute("PRAGMA table_info(oficinas)")
        colunas_of = [coluna[1] for coluna in cursor_of.fetchall()]
        novas_colunas_of = {
            "numero": "TEXT",
            "complemento": "TEXT",
            "estado": "TEXT",
            "cep": "TEXT",
            "inscricao_estadual": "TEXT",
            "telefone_contato": "TEXT",
            "email": "TEXT",
        }
        for col, tipo in novas_colunas_of.items():
            if col not in colunas_of:
                c.execute(f"ALTER TABLE oficinas ADD COLUMN {col} {tipo}")

        cursor_veic = c.execute("PRAGMA table_info(veiculos)")
        colunas_veic = [coluna[1] for coluna in cursor_veic.fetchall()]
        if "observacao" not in colunas_veic:
            c.execute("ALTER TABLE veiculos ADD COLUMN observacao TEXT")
        if "quantidade_eixo" not in colunas_veic:
            c.execute("ALTER TABLE veiculos ADD COLUMN quantidade_eixo INTEGER DEFAULT 0")

        cursor_pp = c.execute("PRAGMA table_info(praca_pedagio)")
        colunas_pp = [coluna[1] for coluna in cursor_pp.fetchall()]
        if "sentido_viagem" not in colunas_pp:
            c.execute("ALTER TABLE praca_pedagio ADD COLUMN sentido_viagem TEXT")
            colunas_pp.append("sentido_viagem")
        if "quantidade_eixos" not in colunas_pp:
            c.execute("ALTER TABLE praca_pedagio ADD COLUMN quantidade_eixos REAL DEFAULT 1.0")
            colunas_pp.append("quantidade_eixos")
        if "valor_todos_eixos" not in colunas_pp:
            c.execute("ALTER TABLE praca_pedagio ADD COLUMN valor_todos_eixos REAL DEFAULT 0.0")
            colunas_pp.append("valor_todos_eixos")
        if "ida" in colunas_pp or "volta" in colunas_pp:
            expr_sentido = "''"
            if "sentido_viagem" in colunas_pp and "ida" in colunas_pp and "volta" in colunas_pp:
                expr_sentido = "COALESCE(NULLIF(TRIM(sentido_viagem), ''), NULLIF(TRIM(ida), ''), NULLIF(TRIM(volta), ''), '')"
            elif "sentido_viagem" in colunas_pp and "ida" in colunas_pp:
                expr_sentido = "COALESCE(NULLIF(TRIM(sentido_viagem), ''), NULLIF(TRIM(ida), ''), '')"
            elif "sentido_viagem" in colunas_pp and "volta" in colunas_pp:
                expr_sentido = "COALESCE(NULLIF(TRIM(sentido_viagem), ''), NULLIF(TRIM(volta), ''), '')"
            elif "sentido_viagem" in colunas_pp:
                expr_sentido = "COALESCE(NULLIF(TRIM(sentido_viagem), ''), '')"
            elif "ida" in colunas_pp and "volta" in colunas_pp:
                expr_sentido = "COALESCE(NULLIF(TRIM(ida), ''), NULLIF(TRIM(volta), ''), '')"
            elif "ida" in colunas_pp:
                expr_sentido = "COALESCE(NULLIF(TRIM(ida), ''), '')"
            elif "volta" in colunas_pp:
                expr_sentido = "COALESCE(NULLIF(TRIM(volta), ''), '')"

            c.execute("DROP TABLE IF EXISTS praca_pedagio_nova")
            c.execute("""CREATE TABLE praca_pedagio_nova (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rota TEXT,
                praca_pedagio TEXT,
                rodovia TEXT,
                concessionaria TEXT,
                sentido_viagem TEXT,
                quantidade_eixos REAL DEFAULT 1.0,
                valor_por_eixo REAL DEFAULT 0.0,
                valor_todos_eixos REAL DEFAULT 0.0
            )""")
            c.execute(f"""
                INSERT INTO praca_pedagio_nova (id, rota, praca_pedagio, rodovia, concessionaria, sentido_viagem, quantidade_eixos, valor_por_eixo, valor_todos_eixos)
                SELECT id, rota, praca_pedagio, rodovia, concessionaria, {expr_sentido},
                       COALESCE(quantidade_eixos, 1.0),
                       COALESCE(valor_por_eixo, 0.0),
                       COALESCE(valor_todos_eixos, COALESCE(valor_por_eixo, 0.0) * COALESCE(quantidade_eixos, 1.0))
                FROM praca_pedagio
            """)
            c.execute("DROP TABLE praca_pedagio")
            c.execute("ALTER TABLE praca_pedagio_nova RENAME TO praca_pedagio")
            colunas_pp = ["id", "rota", "praca_pedagio", "rodovia", "concessionaria", "sentido_viagem", "quantidade_eixos", "valor_por_eixo", "valor_todos_eixos"]

        c.execute(
            """UPDATE praca_pedagio
               SET valor_todos_eixos = COALESCE(valor_por_eixo, 0.0) * COALESCE(quantidade_eixos, 1.0)
               WHERE valor_todos_eixos IS NULL OR ABS(valor_todos_eixos - (COALESCE(valor_por_eixo, 0.0) * COALESCE(quantidade_eixos, 1.0))) > 0.000001"""
        )

        # --- ATUALIZAÇÃO AUTOMÁTICA DAS COLUNAS DE VIAGENS ---
        cursor_viagens = c.execute("PRAGMA table_info(viagens)")
        colunas_viagens = [coluna[1] for coluna in cursor_viagens.fetchall()]
        novas_colunas_viagens = {
            "qtd_viagens": "INTEGER DEFAULT 1",
            "valor_km": "REAL DEFAULT 0.0",
            "tipo_cobranca": "TEXT DEFAULT 'TONELADA'",
            "qtd_pedagio": "INTEGER DEFAULT 0",
            "gasto_extra": "REAL DEFAULT 0.0",
            "pagto_estadia": "REAL DEFAULT 0.0",
            "valor_adicional_frete": "REAL DEFAULT 0.0",
            "descricao_valor_adicional_frete": "TEXT",
            "descricao_gasto_extra": "TEXT",
            "arla": "REAL DEFAULT 0.0",
            "consumo_arla": "REAL DEFAULT 0.0",
            "hora_carregamento": "TEXT",
            "data_chegada": "TEXT",
            "hora_chegada": "TEXT",
            "data_descarregamento": "TEXT",
            "hora_descarregamento": "TEXT",
        }
        for col, tipo in novas_colunas_viagens.items():
            if col not in colunas_viagens:
                c.execute(f"ALTER TABLE viagens ADD COLUMN {col} {tipo}")

        cursor_abastecimentos = c.execute("PRAGMA table_info(abastecimentos)")
        colunas_abastecimentos = [coluna[1] for coluna in cursor_abastecimentos.fetchall()]
        if "veiculo_placa" not in colunas_abastecimentos:
            c.execute("ALTER TABLE abastecimentos ADD COLUMN veiculo_placa TEXT")
        if "desconto" not in colunas_abastecimentos:
            c.execute("ALTER TABLE abastecimentos ADD COLUMN desconto REAL DEFAULT 0.0")

        # --- ATUALIZAÇÃO AUTOMÁTICA DAS COLUNAS DE ROTAS ---
        cursor_rotas = c.execute("PRAGMA table_info(rotas)")
        colunas_rotas = [coluna[1] for coluna in cursor_rotas.fetchall()]
        if "valor_km" not in colunas_rotas:
            c.execute("ALTER TABLE rotas ADD COLUMN valor_km REAL DEFAULT 0.0")
        if "nome_empresa_origem" not in colunas_rotas:
            c.execute("ALTER TABLE rotas ADD COLUMN nome_empresa_origem TEXT")
        if "nome_empresa_destino" not in colunas_rotas:
            c.execute("ALTER TABLE rotas ADD COLUMN nome_empresa_destino TEXT")

        # --- ATUALIZAÇÃO AUTOMÁTICA DAS COLUNAS DE ME LEMBRA ---
        cursor_ml = c.execute("PRAGMA table_info(me_lembra)")
        colunas_ml = [coluna[1] for coluna in cursor_ml.fetchall()]
        if "descricao_veiculo" not in colunas_ml:
            c.execute("ALTER TABLE me_lembra ADD COLUMN descricao_veiculo TEXT")
        if "frota" not in colunas_ml:
            c.execute("ALTER TABLE me_lembra ADD COLUMN frota TEXT")
        if "data_alerta" not in colunas_ml:
            c.execute("ALTER TABLE me_lembra ADD COLUMN data_alerta TEXT")
        if "dias_alerta" not in colunas_ml:
            c.execute("ALTER TABLE me_lembra ADD COLUMN dias_alerta INTEGER DEFAULT 30")
        if "popup_ativo" not in colunas_ml:
            c.execute("ALTER TABLE me_lembra ADD COLUMN popup_ativo INTEGER DEFAULT 1")

        cursor_mld = c.execute("PRAGMA table_info(me_lembra_descricoes)")
        colunas_mld = [coluna[1] for coluna in cursor_mld.fetchall()]
        if "frota" not in colunas_mld:
            c.execute("ALTER TABLE me_lembra_descricoes ADD COLUMN frota TEXT")

        cursor_cp = c.execute("PRAGMA table_info(contas_pagar)")
        colunas_cp = [coluna[1] for coluna in cursor_cp.fetchall()]
        colunas_base_cp = {
            "descricao": "TEXT",
            "fornecedor": "TEXT",
            "categoria": "TEXT",
            "n_documento": "TEXT",
            "data_emissao": "TEXT",
            "data_vencimento": "TEXT",
            "valor": "REAL DEFAULT 0.0",
            "data_pagamento": "TEXT",
            "forma_pagamento": "TEXT DEFAULT 'PIX'",
            "observacao": "TEXT",
            "data_cadastro": "TEXT",
        }
        for nome_coluna, definicao in colunas_base_cp.items():
            if nome_coluna not in colunas_cp:
                c.execute(f"ALTER TABLE contas_pagar ADD COLUMN {nome_coluna} {definicao}")
        if "veiculo_placa" not in colunas_cp:
            c.execute("ALTER TABLE contas_pagar ADD COLUMN veiculo_placa TEXT")
        if "tipo_lancamento" not in colunas_cp:
            c.execute("ALTER TABLE contas_pagar ADD COLUMN tipo_lancamento TEXT DEFAULT 'NAO_MENSAL'")
        if "dia_vencimento_mensal" not in colunas_cp:
            c.execute("ALTER TABLE contas_pagar ADD COLUMN dia_vencimento_mensal INTEGER")
        if "data_vencimento_base" not in colunas_cp:
            c.execute("ALTER TABLE contas_pagar ADD COLUMN data_vencimento_base TEXT")
        if "competencia_paga_mensal" not in colunas_cp:
            c.execute("ALTER TABLE contas_pagar ADD COLUMN competencia_paga_mensal TEXT")
        if "parcelas_pagas" not in colunas_cp:
            c.execute("ALTER TABLE contas_pagar ADD COLUMN parcelas_pagas TEXT")
        if "dias_alerta" not in colunas_cp:
            c.execute("ALTER TABLE contas_pagar ADD COLUMN dias_alerta INTEGER DEFAULT 7")

        cursor_cr = c.execute("PRAGMA table_info(contas_receber)")
        colunas_cr = [coluna[1] for coluna in cursor_cr.fetchall()]
        colunas_base_cr = {
            "descricao": "TEXT",
            "cliente": "TEXT",
            "categoria": "TEXT",
            "n_documento": "TEXT",
            "data_emissao": "TEXT",
            "data_vencimento": "TEXT",
            "valor": "REAL DEFAULT 0.0",
            "data_recebimento": "TEXT",
            "forma_recebimento": "TEXT DEFAULT 'PIX'",
            "observacao": "TEXT",
            "data_cadastro": "TEXT",
        }
        for nome_coluna, definicao in colunas_base_cr.items():
            if nome_coluna not in colunas_cr:
                c.execute(f"ALTER TABLE contas_receber ADD COLUMN {nome_coluna} {definicao}")
        if "veiculo_placa" not in colunas_cr:
            c.execute("ALTER TABLE contas_receber ADD COLUMN veiculo_placa TEXT")

        cursor_forn = c.execute("PRAGMA table_info(fornecedores)")
        colunas_forn = [coluna[1] for coluna in cursor_forn.fetchall()]
        novas_colunas_forn = {
            "numero": "TEXT",
            "complemento": "TEXT",
            "estado": "TEXT",
            "email": "TEXT",
            "responsavel": "TEXT",
            "origem_oficina_id": "INTEGER",
            "pix": "TEXT",
        }
        for col, tipo in novas_colunas_forn.items():
            if col not in colunas_forn:
                c.execute(f"ALTER TABLE fornecedores ADD COLUMN {col} {tipo}")

        cursor_trocas = c.execute("PRAGMA table_info(controle_trocas)")
        colunas_trocas = [coluna[1] for coluna in cursor_trocas.fetchall()]
        if "descricao_veiculo" not in colunas_trocas:
            c.execute("ALTER TABLE controle_trocas ADD COLUMN descricao_veiculo TEXT")
        if "dias_alerta" not in colunas_trocas:
            c.execute("ALTER TABLE controle_trocas ADD COLUMN dias_alerta INTEGER DEFAULT 30")
        # Preenche descrição do veículo para registros antigos com base na placa
        c.execute(
            """UPDATE controle_trocas
               SET descricao_veiculo = (
                   SELECT v.descricao
                   FROM veiculos v
                   WHERE UPPER(TRIM(v.placa)) = UPPER(TRIM(controle_trocas.veiculo_placa))
                   LIMIT 1
               )
               WHERE (descricao_veiculo IS NULL OR TRIM(descricao_veiculo) = '')
                 AND veiculo_placa IS NOT NULL
                 AND TRIM(veiculo_placa) <> ''"""
        )

        # --- ATUALIZAÇÃO AUTOMÁTICA DAS COLUNAS DE PARÂMETROS ---
        cursor_param = c.execute("PRAGMA table_info(parametros)")
        colunas_param = [coluna[1] for coluna in cursor_param.fetchall()]
        if "qtde_pneu" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN qtde_pneu REAL DEFAULT 1.0")
        if "vl_gasto_pneu_km" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN vl_gasto_pneu_km REAL DEFAULT 0.12")
        if "cmp_valor_litro_diesel" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN cmp_valor_litro_diesel REAL DEFAULT 0.0")
        if "cmp_consumo_001" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN cmp_consumo_001 REAL DEFAULT 2.5")
        if "cmp_consumo_002" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN cmp_consumo_002 REAL DEFAULT 2.5")
        if "cmp_consumo_003" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN cmp_consumo_003 REAL DEFAULT 2.5")
        if "cmp_custo_escritorio" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN cmp_custo_escritorio REAL DEFAULT 0.0")
        if "vl_custo_rastreador" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN vl_custo_rastreador REAL DEFAULT 0.0")
        if "pagto_ipva" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN pagto_ipva REAL DEFAULT 0.0")
        if "imposto_pct" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN imposto_pct REAL DEFAULT 0.0")
        if "valor_frete_mensal_fixo" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN valor_frete_mensal_fixo REAL DEFAULT 0.0")
        if "seguro_vida_motorista" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN seguro_vida_motorista REAL DEFAULT 0.0")
        if "filtro_placa_default" not in colunas_param:
            c.execute("ALTER TABLE parametros ADD COLUMN filtro_placa_default TEXT DEFAULT 'Todas as placas'")

        cursor_hist_param = c.execute("PRAGMA table_info(parametros_historico)")
        colunas_hist_param = [coluna[1] for coluna in cursor_hist_param.fetchall()]
        if "veiculo_placa" not in colunas_hist_param:
            c.execute("ALTER TABLE parametros_historico ADD COLUMN veiculo_placa TEXT DEFAULT 'GERAL'")
            colunas_hist_param.append("veiculo_placa")
        coluna_seguro_vida_hist_criada = False
        if "seguro_vida_motorista" not in colunas_hist_param:
            c.execute("ALTER TABLE parametros_historico ADD COLUMN seguro_vida_motorista REAL DEFAULT 0.0")
            coluna_seguro_vida_hist_criada = True
        if "vl_custo_rastreador" not in colunas_hist_param:
            c.execute("ALTER TABLE parametros_historico ADD COLUMN vl_custo_rastreador REAL DEFAULT 0.0")
        if coluna_seguro_vida_hist_criada:
            c.execute(
                """UPDATE parametros_historico
                   SET seguro_vida_motorista = (
                       SELECT COALESCE(seguro_vida_motorista, 0.0)
                       FROM parametros
                       WHERE id=1
                   )
                   WHERE COALESCE(seguro_vida_motorista, 0.0) = 0.0"""
            )
        c.execute(
            """UPDATE parametros_historico
               SET veiculo_placa = 'GERAL'
               WHERE veiculo_placa IS NULL OR TRIM(veiculo_placa) = ''"""
        )

        indices_hist_param = c.execute("PRAGMA index_list(parametros_historico)").fetchall()
        precisa_recriar_hist_param = False
        for indice in indices_hist_param:
            if int(indice[2] or 0) != 1:
                continue
            nome_indice = indice[1]
            cols_indice = [col[2] for col in c.execute(f"PRAGMA index_info({nome_indice})").fetchall()]
            if cols_indice == ["vigencia_data"]:
                precisa_recriar_hist_param = True
                break
        if precisa_recriar_hist_param:
            c.execute("DROP TABLE IF EXISTS parametros_historico_nova")
            c.execute("""CREATE TABLE parametros_historico_nova (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                veiculo_placa TEXT DEFAULT 'GERAL',
                vigencia_data TEXT,
                consumo REAL,
                manut REAL,
                pneu REAL,
                depre REAL,
                motora_fixo REAL,
                motora_pct REAL,
                seguro REAL,
                seguro_vida_motorista REAL,
                financiamento REAL,
                pagto_ipva REAL,
                cmp_custo_escritorio REAL,
                vl_custo_rastreador REAL,
                imposto_pct REAL,
                valor_frete_mensal_fixo REAL,
                qtde_pneu REAL,
                vl_gasto_pneu_km REAL,
                UNIQUE(veiculo_placa, vigencia_data)
            )""")
            c.execute("""INSERT OR REPLACE INTO parametros_historico_nova (
                            id, veiculo_placa, vigencia_data, consumo, manut, pneu, depre,
                            motora_fixo, motora_pct, seguro, seguro_vida_motorista, financiamento,
                            pagto_ipva, cmp_custo_escritorio, vl_custo_rastreador, imposto_pct,
                            valor_frete_mensal_fixo, qtde_pneu, vl_gasto_pneu_km
                         )
                         SELECT id, COALESCE(NULLIF(TRIM(veiculo_placa), ''), 'GERAL'), vigencia_data,
                                consumo, manut, pneu, depre, motora_fixo, motora_pct, seguro,
                                seguro_vida_motorista, financiamento, pagto_ipva, cmp_custo_escritorio,
                                vl_custo_rastreador, imposto_pct, valor_frete_mensal_fixo,
                                qtde_pneu, vl_gasto_pneu_km
                         FROM parametros_historico""")
            c.execute("DROP TABLE parametros_historico")
            c.execute("ALTER TABLE parametros_historico_nova RENAME TO parametros_historico")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_parametros_hist_placa_vigencia ON parametros_historico(veiculo_placa, vigencia_data)")

        # Índices para acelerar filtros e ordenação (especialmente no celular)
        c.execute("CREATE INDEX IF NOT EXISTS idx_viagens_data ON viagens(data)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_viagens_placa_data ON viagens(veiculo_placa, data)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_viagens_origem_destino ON viagens(origem, destino)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_abastecimentos_data ON abastecimentos(data)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_abastecimentos_placa_data ON abastecimentos(veiculo_placa, data)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_manutencoes_oficina ON manutencoes(oficina_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_manutencoes_placa ON manutencoes(veiculo_placa)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_manutencoes_data ON manutencoes(data_entrada)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_manutencoes_pecas_forn ON manutencoes_pecas(fornecedor_id)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_contas_pagar_venc ON contas_pagar(data_vencimento)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_contas_receber_venc ON contas_receber(data_vencimento)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_me_lembra_venc ON me_lembra(data_vencimento)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_estacoes_trabalho_ativo ON estacoes_trabalho(nome_estacao, ativo)")
        c.execute("PRAGMA optimize")


# Executa a função para garantir que o banco está atualizado
init_db()
proteger_abertura_sistema()
# --- FIM DO BLOCO INIT_DB ---

# =========================
# 3. LEITURA DE PARÂMETROS E VARIAVEIS GLOBAIS
# =========================
bootstrap = _carregar_bootstrap_app()
p_real = bootstrap["p_real"]
if st.session_state.simulacao_ativa:
    if not st.session_state.p_simulado:
        st.session_state.p_simulado = p_real.copy()
    # Merge garante fallback para campos novos que ainda não existam na simulação.
    p = {**p_real, **st.session_state.p_simulado}
else:
    p = p_real

data_ini_carregar = datetime.strptime(p.get('data_filtro_ini', '2026-01-01'), '%Y-%m-%d').date()
data_fim_carregar = datetime.strptime(p.get('data_filtro_fim', '2026-12-31'), '%Y-%m-%d').date()

lista_cidades = bootstrap["lista_cidades"]
veiculos_db = bootstrap["veiculos_db"]
apenas_placas = [v["placa"] for v in veiculos_db]
lista_veiculos_full = [f"{v['placa']} - {v['descricao']}" for v in veiculos_db]
descricao_por_placa = {
    str(v.get("placa") or "").strip().upper(): str(v.get("descricao") or "").strip()
    for v in veiculos_db
}

def rotulo_placa_com_descricao(placa):
    placa_txt = str(placa or "").strip()
    if placa_txt == "Todas as placas":
        return placa_txt
    descricao = descricao_por_placa.get(placa_txt.upper(), "")
    return f"{placa_txt} - {descricao}" if descricao else placa_txt

def placa_de_opcao_veiculo(opcao):
    if not opcao:
        return None
    return str(opcao).split(" - ")[0].strip().upper() or None

oficinas_db = bootstrap["oficinas_db"]
dict_oficinas = {o["nome"]: o["id"] for o in oficinas_db}
fornecedores_db = bootstrap["fornecedores_db"]
dict_fornecedores_manutencao = {f["nome"]: f["id"] for f in fornecedores_db}
obrigacoes_db = bootstrap["obrigacoes_db"]

last_v = bootstrap["last_v"]
diesel_param = float(p.get("cmp_valor_litro_diesel", 0.0))
consumo_param = float(p.get("consumo", 2.5))
v_diesel_sug = diesel_param if diesel_param > 0 else (float(last_v["diesel"]) if last_v else 6.00)
v_cons_sug = consumo_param if consumo_param > 0 else (float(last_v["consumo"]) if last_v else 2.5)
v_arla_sug = float(last_v["arla"]) if last_v else 0.0
v_cons_arla_sug = float(last_v["consumo_arla"]) if last_v else 0.0

# INTERFACE
st.markdown(
    """
    <style>
    html { color-scheme: light only; }
    /* ===== DESIGN SYSTEM ===== */
    :root {
        --navy:      #0b3c5d;
        --blue:      #1b6ca8;
        --teal:      #00a6a6;
        --bg:        #eef4fb;
        --surface:   #ffffff;
        --border:    #cfdce9;
        --text:      #102a43;
        --text-sec:  #37516c;
        --text-muted:#627d98;
        --success:   #10b981;
        --warning:   #f59e0b;
        --danger:    #ef4444;
        --radius:    12px;
        --shadow-sm: 0 1px 3px rgba(11,60,93,0.07), 0 1px 2px rgba(11,60,93,0.04);
        --shadow:    0 4px 12px rgba(11,60,93,0.10), 0 2px 4px rgba(11,60,93,0.06);
        --shadow-lg: 0 10px 28px rgba(11,60,93,0.15), 0 4px 8px rgba(11,60,93,0.08);
    }

    /* ===== APP BACKGROUND ===== */
    .stApp {
        background: linear-gradient(160deg, #e8f2fa 0%, #f4f9ff 45%, #edf7f7 100%) !important;
        font-family: "Inter", "Segoe UI", "Trebuchet MS", sans-serif !important;
    }

    /* ===== MAIN CONTAINER ===== */
    .main .block-container {
        padding-top: 1.0rem !important;
        padding-bottom: 2rem !important;
        max-width: 1440px !important;
    }

    /* ===== PROFESSIONAL HEADER ===== */
    .art-header {
        background: linear-gradient(120deg, #0b3c5d 0%, #1b6ca8 55%, #00a6a6 100%);
        border-radius: 16px;
        padding: 16px 24px;
        margin-bottom: 14px;
        box-shadow: 0 8px 28px rgba(11,60,93,0.32);
        display: flex;
        align-items: center;
        gap: 14px;
        position: relative;
        overflow: hidden;
        animation: art-risein 0.45s ease-out;
    }
    .art-header::before {
        content: '';
        position: absolute;
        right: -40px; top: -50px;
        width: 200px; height: 200px;
        background: rgba(255,255,255,0.06);
        border-radius: 50%;
    }
    .art-header::after {
        content: '';
        position: absolute;
        right: 80px; bottom: -60px;
        width: 140px; height: 140px;
        background: rgba(0,166,166,0.12);
        border-radius: 50%;
    }
    .art-header-icon { font-size: 2.4rem; line-height: 1; position: relative; z-index: 1; }
    .art-header-text { position: relative; z-index: 1; }
    .art-header-text h1 {
        margin: 0;
        color: #ffffff;
        font-size: 1.45rem;
        font-weight: 800;
        letter-spacing: -0.2px;
        line-height: 1.25;
        text-shadow: 0 1px 3px rgba(0,0,0,0.15);
    }
    .art-header-text p {
        margin: 3px 0 0;
        color: rgba(255,255,255,0.78);
        font-size: 0.76rem;
        font-weight: 600;
        letter-spacing: 0.8px;
        text-transform: uppercase;
    }
    @keyframes art-risein {
        from { opacity: 0; transform: translateY(-6px); }
        to   { opacity: 1; transform: translateY(0); }
    }

    /* ===== FILTER BAR ===== */
    .filter-bar-card {
        background: var(--surface);
        border: 1px solid var(--border);
        border-radius: var(--radius);
        padding: 10px 16px 6px;
        margin-bottom: 10px;
        box-shadow: var(--shadow-sm);
        display: flex;
        align-items: center;
        gap: 8px;
    }
    .filter-bar-label {
        font-size: 0.72rem;
        font-weight: 700;
        color: var(--text-muted);
        text-transform: uppercase;
        letter-spacing: 0.7px;
        white-space: nowrap;
        padding-right: 8px;
        border-right: 2px solid var(--border);
        margin-right: 4px;
    }

    /* ===== TABS ===== */
    div[data-testid="stTabs"] [role="tablist"] {
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: 12px !important;
        padding: 4px 5px !important;
        gap: 3px !important;
        box-shadow: var(--shadow-sm) !important;
        margin-bottom: 2px !important;
        flex-wrap: wrap !important;
        overflow: visible !important;
        row-gap: 5px !important;
    }
    div[data-testid="stTabs"] button[data-baseweb="tab"] {
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.80rem !important;
        padding: 7px 13px !important;
        color: var(--text-sec) !important;
        border: none !important;
        transition: all 0.16s ease !important;
        background: transparent !important;
        white-space: nowrap !important;
        flex: 0 0 auto !important;
    }
    div[data-testid="stTabs"] button[data-baseweb="tab"]:hover:not([aria-selected="true"]) {
        background: #eef4fb !important;
        color: var(--blue) !important;
    }
    div[data-testid="stTabs"] button[data-baseweb="tab"][aria-selected="true"] {
        background: linear-gradient(120deg, var(--navy) 0%, var(--blue) 100%) !important;
        color: #ffffff !important;
        box-shadow: 0 3px 10px rgba(27,108,168,0.38) !important;
    }
    div[data-testid="stTabs"] button[data-baseweb="tab"] p {
        font-weight: inherit !important;
        font-size: inherit !important;
        color: inherit !important;
    }
    div[data-testid="stTabs"] [role="tablist"] > div[role="presentation"] {
        display: none !important;
    }

    /* ===== METRIC CARDS ===== */
    div[data-testid="stMetric"] {
        background: var(--surface) !important;
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        padding: 12px 16px !important;
        box-shadow: var(--shadow-sm) !important;
        transition: box-shadow 0.18s ease, transform 0.18s ease !important;
    }
    div[data-testid="stMetric"]:hover {
        box-shadow: var(--shadow) !important;
        transform: translateY(-1px);
    }
    div[data-testid="stMetricLabel"] p,
    div[data-testid="stMetricLabel"] {
        font-size: 0.72rem !important;
        font-weight: 700 !important;
        text-transform: uppercase !important;
        letter-spacing: 0.7px !important;
        color: var(--text-muted) !important;
    }
    div[data-testid="stMetricValue"] {
        font-size: 1.45rem !important;
        font-weight: 800 !important;
        color: var(--text) !important;
        line-height: 1.2 !important;
    }
    div[data-testid="stMetricDelta"] { font-size: 0.78rem !important; }

    /* ===== BUTTONS ===== */
    .stButton > button {
        border-radius: 8px !important;
        font-weight: 600 !important;
        font-size: 0.86rem !important;
        padding: 7px 18px !important;
        transition: all 0.16s ease !important;
        box-shadow: var(--shadow-sm) !important;
        letter-spacing: 0.1px !important;
    }
    .stButton > button:not([kind="secondary"]):not([kind="tertiary"]) {
        background: linear-gradient(120deg, var(--navy) 0%, var(--blue) 100%) !important;
        color: #ffffff !important;
        border: 1.5px solid var(--blue) !important;
    }
    .stButton > button:not([kind="secondary"]):not([kind="tertiary"]):hover {
        box-shadow: 0 4px 14px rgba(27,108,168,0.42) !important;
        transform: translateY(-1px);
    }
    .stButton > button[kind="secondary"] {
        background: var(--surface) !important;
        color: var(--blue) !important;
        border: 1.5px solid var(--border) !important;
    }
    .stButton > button[kind="secondary"]:hover {
        border-color: var(--blue) !important;
        background: #eef4fb !important;
    }

    /* ===== FORM INPUTS ===== */
    div[data-testid="stTextInput"] input,
    div[data-testid="stNumberInput"] input,
    div[data-testid="stDateInput"] input,
    div[data-testid="stTextArea"] textarea {
        border-radius: 8px !important;
        border-color: var(--border) !important;
        font-size: 0.88rem !important;
        background: var(--surface) !important;
        color: var(--text) !important;
        -webkit-text-fill-color: var(--text) !important;
        transition: border-color 0.15s, box-shadow 0.15s !important;
    }
    div[data-testid="stTextInput"] input:focus,
    div[data-testid="stNumberInput"] input:focus,
    div[data-testid="stTextArea"] textarea:focus {
        border-color: var(--blue) !important;
        box-shadow: 0 0 0 2.5px rgba(27,108,168,0.16) !important;
    }
    div[data-testid="stSelectbox"] > div > div {
        border-radius: 8px !important;
        border-color: var(--border) !important;
        font-size: 0.88rem !important;
    }

    /* ===== INPUT LABELS ===== */
    div[data-testid="stTextInput"] label,
    div[data-testid="stNumberInput"] label,
    div[data-testid="stSelectbox"] label,
    div[data-testid="stDateInput"] label,
    div[data-testid="stTextArea"] label,
    div[data-testid="stCheckbox"] label {
        font-size: 0.78rem !important;
        font-weight: 600 !important;
        color: var(--text-sec) !important;
        letter-spacing: 0.1px !important;
    }

    /* ===== EXPANDERS ===== */
    div[data-testid="stExpander"] details {
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        background: var(--surface) !important;
        box-shadow: var(--shadow-sm) !important;
        overflow: hidden !important;
    }
    div[data-testid="stExpander"] summary {
        font-weight: 600 !important;
        color: var(--text-sec) !important;
        padding: 10px 14px !important;
        font-size: 0.88rem !important;
    }
    div[data-testid="stExpander"] details[open] summary {
        color: var(--blue) !important;
        border-bottom: 1px solid var(--border);
        background: #f7fbff;
    }

    /* ===== SECTION HEADERS ===== */
    h2 { color: var(--navy) !important; font-weight: 700 !important; font-size: 1.15rem !important; }
    h3 { color: var(--navy) !important; font-weight: 600 !important; font-size: 1.00rem !important; }

    /* ===== DATAFRAMES ===== */
    div[data-testid="stDataFrame"],
    div[data-testid="stTable"] {
        border: 1px solid var(--border) !important;
        border-radius: var(--radius) !important;
        overflow: hidden !important;
        box-shadow: var(--shadow-sm) !important;
    }

    /* ===== ALERTS ===== */
    div[data-testid="stSuccess"]  { border-radius: 10px !important; border-left: 4px solid var(--success) !important; }
    div[data-testid="stWarning"]  { border-radius: 10px !important; border-left: 4px solid var(--warning) !important; }
    div[data-testid="stError"]    { border-radius: 10px !important; border-left: 4px solid var(--danger)  !important; }
    div[data-testid="stInfo"]     { border-radius: 10px !important; border-left: 4px solid var(--blue)    !important; }

    /* ===== CAPTION ===== */
    div[data-testid="stCaptionContainer"] p,
    small, .caption { color: var(--text-muted) !important; font-size: 0.76rem !important; }

    /* ===== DIVIDER ===== */
    hr { border-color: var(--border) !important; margin: 10px 0 !important; }

    /* ===== SCROLLBAR ===== */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: #eef4fb; border-radius: 99px; }
    ::-webkit-scrollbar-thumb { background: #b0c8df; border-radius: 99px; }
    ::-webkit-scrollbar-thumb:hover { background: var(--blue); }

    /* ===== CHECKBOX & RADIO ===== */
    div[data-testid="stCheckbox"] input:checked + div { border-color: var(--blue) !important; background: var(--blue) !important; }

    /* ===== MOBILE RESPONSIVE ===== */
    @media (max-width: 900px) {
        .main .block-container {
            padding-top: 0.5rem !important;
            padding-bottom: 0.8rem !important;
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
        .art-header { padding: 11px 14px; gap: 10px; }
        .art-header-icon { font-size: 1.8rem; }
        .art-header-text h1 { font-size: 1.05rem; }
        .art-header-text p { font-size: 0.68rem; }
        div[data-testid="stAppViewContainer"] { overflow-x: hidden; }
        div[data-testid="stHorizontalBlock"] {
            gap: 0.4rem !important;
            flex-wrap: wrap;
        }
        div[data-testid="stHorizontalBlock"] > div[data-testid="column"] {
            min-width: 100% !important;
            flex: 1 1 100% !important;
        }
        div[data-testid="stTabs"] [role="tablist"] {
            flex-wrap: wrap !important;
            overflow: visible !important;
            gap: 0.3rem !important;
        }
        div[data-testid="stTabs"] button[data-baseweb="tab"] {
            white-space: normal !important;
            min-height: 2rem !important;
            padding: 0.3rem 0.5rem !important;
            flex: 1 1 46% !important;
        }
        div[data-testid="stTabs"] button[data-baseweb="tab"] p {
            white-space: normal !important;
            font-size: 0.73rem !important;
            line-height: 1.2 !important;
            text-align: center !important;
        }
        div[data-testid="stMetric"] { padding: 8px 10px !important; }
        div[data-testid="stMetricValue"] { font-size: 1.1rem !important; }
        .stButton > button { width: 100% !important; }
        input, select, textarea { font-size: 16px !important; }
        div[data-testid="stDataFrame"],
        div[data-testid="stTable"] { overflow-x: auto !important; }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    """
    <div class="art-header">
        <div class="art-header-icon">🚛</div>
        <div class="art-header-text">
            <h1>ART</h1>
            <p>Sistema de Gestão Operacional &nbsp;·&nbsp; Viagens &amp; Financeiro</p>
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

st.markdown(
    '<div style="background:#fff;border:1px solid #cfdce9;border-radius:12px;padding:4px 14px 2px;'
    'margin-bottom:8px;box-shadow:0 1px 3px rgba(11,60,93,0.07);">'
    '<span style="font-size:0.70rem;font-weight:700;color:#627d98;text-transform:uppercase;'
    'letter-spacing:0.8px;">⚙️&nbsp; Filtros Globais</span></div>',
    unsafe_allow_html=True,
)
c_f1, c_f2, c_f3 = st.columns(3)
if "filtro_ini_top" not in st.session_state:
    st.session_state.filtro_ini_top = data_ini_carregar
if "filtro_fim_top" not in st.session_state:
    st.session_state.filtro_fim_top = data_fim_carregar

c_f1.date_input("Período — Início", format="DD/MM/YYYY", key="filtro_ini_top")
c_f2.date_input("Período — Fim", format="DD/MM/YYYY", key="filtro_fim_top")
opcoes_filtro_placa = ["Todas as placas"] + apenas_placas
placa_default_param = str(p.get("filtro_placa_default") or "Todas as placas").strip()
if placa_default_param not in opcoes_filtro_placa:
    placa_default_param = "Todas as placas"
placa_top_pendente = st.session_state.pop("filtro_placa_top_pendente", None)
if placa_top_pendente in opcoes_filtro_placa:
    st.session_state.filtro_placa_top = placa_top_pendente
if "filtro_placa_top" not in st.session_state:
    st.session_state.filtro_placa_top = placa_default_param
if st.session_state.filtro_placa_top not in opcoes_filtro_placa:
    st.session_state.filtro_placa_top = placa_default_param
c_f3.selectbox("Filtrar por Placa", opcoes_filtro_placa, key="filtro_placa_top", format_func=rotulo_placa_com_descricao)
filtro_ini = st.session_state.filtro_ini_top
filtro_fim = st.session_state.filtro_fim_top
placa_filtro_calculo = (
    st.session_state.filtro_placa_top
    if st.session_state.filtro_placa_top != "Todas as placas"
    else None
)
if placa_filtro_calculo:
    st.info(f"**Filtro por placa ativo:** {rotulo_placa_com_descricao(placa_filtro_calculo)}", icon="🚛")

def fator_rateio_mensal_por_periodo(data_inicio, data_fim):
    if data_inicio is None or data_fim is None or data_fim < data_inicio:
        return 0.0
    return dias_rateio_periodo(data_inicio, data_fim) / 30.0


def dias_rateio_periodo(data_inicio, data_fim, limite_dias=None):
    if data_inicio is None or data_fim is None or data_fim < data_inicio:
        return 0
    dias_periodo = (data_fim - data_inicio).days + 1
    if limite_dias is None:
        return int(dias_periodo)
    return min(int(dias_periodo), int(limite_dias))

PARAM_CAMPOS_HIST = [
    "consumo",
    "manut",
    "pneu",
    "depre",
    "motora_fixo",
    "motora_pct",
    "seguro",
    "seguro_vida_motorista",
    "financiamento",
    "pagto_ipva",
    "cmp_custo_escritorio",
    "vl_custo_rastreador",
    "imposto_pct",
    "valor_frete_mensal_fixo",
    "qtde_pneu",
    "vl_gasto_pneu_km",
]


def carregar_historico_parametros():
    df_hist = _carregar_historico_parametros_raw()
    if df_hist.empty:
        base = {campo: float(p.get(campo, 0.0) or 0.0) for campo in PARAM_CAMPOS_HIST}
        base["vigencia_data"] = "1900-01-01"
        base["veiculo_placa"] = "GERAL"
        df_hist = pd.DataFrame([base])
    if "veiculo_placa" not in df_hist.columns:
        df_hist["veiculo_placa"] = "GERAL"
    df_hist["veiculo_placa"] = df_hist["veiculo_placa"].fillna("GERAL").astype(str).str.strip().str.upper()
    df_hist.loc[df_hist["veiculo_placa"] == "", "veiculo_placa"] = "GERAL"
    df_hist["vigencia_data"] = pd.to_datetime(df_hist["vigencia_data"], errors="coerce").dt.normalize()
    for campo in PARAM_CAMPOS_HIST:
        df_hist[campo] = pd.to_numeric(df_hist.get(campo, 0.0), errors="coerce").fillna(float(p.get(campo, 0.0) or 0.0))
    return df_hist.sort_values(["veiculo_placa", "vigencia_data"]).reset_index(drop=True)


def aplicar_parametros_por_data(df_origem, col_data="data"):
    if df_origem is None or df_origem.empty:
        return df_origem
    df_saida = df_origem.copy()
    df_saida["_data_ref_param"] = pd.to_datetime(df_saida[col_data], errors="coerce").dt.normalize()
    df_saida["_data_ref_param"] = df_saida["_data_ref_param"].fillna(pd.Timestamp("1900-01-01"))
    if st.session_state.simulacao_ativa:
        for campo in PARAM_CAMPOS_HIST:
            df_saida[f"param_{campo}"] = float(p.get(campo, 0.0) or 0.0)
        return df_saida
    df_hist = carregar_historico_parametros()
    rename_hist = {campo: f"hist_{campo}" for campo in PARAM_CAMPOS_HIST}
    hist_merge = df_hist[["veiculo_placa", "vigencia_data"] + PARAM_CAMPOS_HIST].rename(columns=rename_hist).copy()
    ordem_original = pd.Series(range(len(df_saida)), index=df_saida.index)
    df_saida["_ordem_param"] = ordem_original.values
    if "veiculo_placa" in df_saida.columns:
        df_saida["_placa_param"] = df_saida["veiculo_placa"].fillna("").astype(str).str.strip().str.upper()
    else:
        df_saida["_placa_param"] = "GERAL"
    df_saida.loc[df_saida["_placa_param"] == "", "_placa_param"] = "GERAL"

    hist_geral = hist_merge[hist_merge["veiculo_placa"] == "GERAL"].drop(columns=["veiculo_placa"]).sort_values("vigencia_data")
    df_geral = pd.merge_asof(
        df_saida.sort_values("_data_ref_param"),
        hist_geral,
        left_on="_data_ref_param",
        right_on="vigencia_data",
        direction="backward",
    )
    _cols_drop_geral = []
    for campo in PARAM_CAMPOS_HIST:
        df_geral[f"geral_{campo}"] = pd.to_numeric(df_geral.get(f"hist_{campo}", 0.0), errors="coerce").fillna(float(p.get(campo, 0.0) or 0.0))
        if f"hist_{campo}" in df_geral.columns:
            _cols_drop_geral.append(f"hist_{campo}")
    if "vigencia_data" in df_geral.columns:
        _cols_drop_geral.append("vigencia_data")
    if _cols_drop_geral:
        df_geral = df_geral.drop(columns=_cols_drop_geral)

    hist_placa = hist_merge[hist_merge["veiculo_placa"] != "GERAL"].rename(columns={"veiculo_placa": "_placa_param"}).sort_values(["_placa_param", "vigencia_data"])
    if not hist_placa.empty:
        partes_placa = []
        for placa_ref, df_grupo in df_geral.groupby("_placa_param", sort=False):
            hist_grupo = hist_placa[hist_placa["_placa_param"] == placa_ref].drop(columns=["_placa_param"])
            if hist_grupo.empty:
                partes_placa.append(df_grupo)
                continue
            partes_placa.append(
                pd.merge_asof(
                    df_grupo.sort_values("_data_ref_param"),
                    hist_grupo.sort_values("vigencia_data"),
                    left_on="_data_ref_param",
                    right_on="vigencia_data",
                    direction="backward",
                )
            )
        df_saida = pd.concat(partes_placa, ignore_index=True) if partes_placa else df_geral
    else:
        df_saida = df_geral
    _cols_drop_saida = []
    for campo in PARAM_CAMPOS_HIST:
        df_saida[f"param_{campo}"] = pd.to_numeric(df_saida.get(f"hist_{campo}", pd.NA), errors="coerce")
        df_saida[f"param_{campo}"] = df_saida[f"param_{campo}"].fillna(pd.to_numeric(df_saida.get(f"geral_{campo}", 0.0), errors="coerce"))
        df_saida[f"param_{campo}"] = df_saida[f"param_{campo}"].fillna(float(p.get(campo, 0.0) or 0.0))
        if f"hist_{campo}" in df_saida.columns:
            _cols_drop_saida.append(f"hist_{campo}")
        if f"geral_{campo}" in df_saida.columns:
            _cols_drop_saida.append(f"geral_{campo}")
    if _cols_drop_saida:
        df_saida = df_saida.drop(columns=_cols_drop_saida)
    if "vigencia_data" in df_saida.columns:
        df_saida = df_saida.drop(columns=["vigencia_data"])
    if "_data_ref_param" in df_saida.columns:
        df_saida = df_saida.drop(columns=["_data_ref_param"])
    if "_placa_param" in df_saida.columns:
        df_saida = df_saida.drop(columns=["_placa_param"])
    if "_ordem_param" in df_saida.columns:
        df_saida = df_saida.sort_values("_ordem_param").drop(columns=["_ordem_param"])
    return df_saida


def serie_parametro_diaria(campo, data_inicio, data_fim, veiculo_placa=None):
    if data_inicio is None or data_fim is None or data_fim < data_inicio:
        return pd.Series(dtype=float)
    dias_rateio = dias_rateio_periodo(data_inicio, data_fim)
    if dias_rateio <= 0:
        return pd.Series(dtype=float)
    data_fim_rateio = data_inicio + timedelta(days=dias_rateio - 1)
    idx = pd.date_range(start=data_inicio, end=data_fim_rateio, freq="D")
    base = pd.DataFrame({"data_ref": idx})
    if st.session_state.simulacao_ativa:
        return pd.Series(float(p.get(campo, 0.0) or 0.0), index=idx)
    hist = carregar_historico_parametros()
    placa_ref = str(veiculo_placa or placa_filtro_calculo or "GERAL").strip().upper()
    if not placa_ref or placa_ref == "TODAS AS PLACAS":
        placa_ref = "GERAL"
    hist_geral = hist[hist["veiculo_placa"] == "GERAL"][["vigencia_data", campo]].sort_values("vigencia_data")
    hist_placa = hist[hist["veiculo_placa"] == placa_ref][["vigencia_data", campo]].sort_values("vigencia_data")
    merged = pd.merge_asof(
        base.sort_values("data_ref"),
        hist_geral,
        left_on="data_ref",
        right_on="vigencia_data",
        direction="backward",
    )
    serie_geral = pd.to_numeric(merged[campo], errors="coerce").fillna(float(p.get(campo, 0.0) or 0.0))
    if placa_ref == "GERAL" or hist_placa.empty:
        return serie_geral
    merged_placa = pd.merge_asof(
        base.sort_values("data_ref"),
        hist_placa,
        left_on="data_ref",
        right_on="vigencia_data",
        direction="backward",
    )
    return pd.to_numeric(merged_placa[campo], errors="coerce").fillna(serie_geral)


def valor_mensal_rateado_periodo(campo, data_inicio, data_fim, veiculo_placa=None):
    serie = serie_parametro_diaria(campo, data_inicio, data_fim, veiculo_placa=veiculo_placa)
    if serie.empty:
        return 0.0
    return float(serie.sum() / 30.0)


def valor_anual_rateado_periodo(campo, data_inicio, data_fim, veiculo_placa=None):
    serie = serie_parametro_diaria(campo, data_inicio, data_fim, veiculo_placa=veiculo_placa)
    if serie.empty:
        return 0.0
    return float(serie.sum() / 365.0)


def frete_fixo_mensal_atual():
    if st.session_state.simulacao_ativa:
        return float(p.get("valor_frete_mensal_fixo", 0.0) or 0.0)
    hoje = date.today()
    serie = serie_parametro_diaria("valor_frete_mensal_fixo", hoje, hoje)
    if serie.empty:
        return 0.0
    return float(serie.iloc[0] or 0.0)

def frete_fixo_rateado_periodo(data_inicio, data_fim, veiculo_placa=None):
    return valor_mensal_rateado_periodo("valor_frete_mensal_fixo", data_inicio, data_fim, veiculo_placa=veiculo_placa)

valor_frete_fixo_periodo = frete_fixo_rateado_periodo(filtro_ini, filtro_fim)

# Alerta em popup ao abrir o sistema quando houver item vencido no ME LEMBRA
if "popup_vencido_exibido" not in st.session_state:
    st.session_state.popup_vencido_exibido = False
if "popup_trocas_exibido" not in st.session_state:
    st.session_state.popup_trocas_exibido = False
if "popup_cp_exibido" not in st.session_state:
    st.session_state.popup_cp_exibido = False

if not st.session_state.popup_vencido_exibido:
    try:
        hoje_ml = date.today()
        with conn() as c:
            df_alerta_ml = pd.read_sql(
                """SELECT descricao, descricao_veiculo, frota, data_vencimento, COALESCE(dias_alerta, 30) AS dias_alerta
                   FROM me_lembra
                   WHERE data_vencimento IS NOT NULL
                     AND COALESCE(popup_ativo, 1) = 1
                   ORDER BY date(data_vencimento) ASC""",
                c,
            )

        if not df_alerta_ml.empty:
            _dv_ts = pd.to_datetime(df_alerta_ml["data_vencimento"], errors="coerce")
            df_alerta_ml["dias_para_vencer"] = (_dv_ts - pd.Timestamp(hoje_ml)).dt.days
            df_alerta_ml["data_vencimento"] = _dv_ts.dt.date
            df_alerta_ml["dias_alerta"] = pd.to_numeric(df_alerta_ml["dias_alerta"], errors="coerce").fillna(30).astype(int)
            df_alerta_ml = df_alerta_ml[
                df_alerta_ml["dias_para_vencer"].notna()
                & (
                    (df_alerta_ml["dias_para_vencer"] < 0)
                    | (
                        (df_alerta_ml["dias_para_vencer"] >= 0)
                        & (df_alerta_ml["dias_para_vencer"] <= df_alerta_ml["dias_alerta"])
                    )
                )
            ].copy()
            _dpv = df_alerta_ml["dias_para_vencer"].astype(int)
            df_alerta_ml["status"] = (
                "Vence em até " + df_alerta_ml["dias_alerta"].astype(str) + " dias"
            ).where(_dpv >= 0, "Vencido")
            df_alerta_ml["situacao_popup"] = pd.Series(
                ["Vencido há " + str(abs(d)) + " dia(s)" if d < 0 else ("Vence hoje" if d == 0 else "Faltam " + str(d) + " dia(s) para vencer")
                 for d in _dpv],
                index=df_alerta_ml.index,
            )
            df_alerta_ml["descricao_veiculo"] = df_alerta_ml["descricao_veiculo"].fillna("").astype(str)
            qtd_vencidos_ml = int((df_alerta_ml["dias_para_vencer"] < 0).sum())
            qtd_a_vencer_ml = int((df_alerta_ml["dias_para_vencer"] >= 0).sum())
            qtd_total_alerta_ml = len(df_alerta_ml)
        else:
            qtd_vencidos_ml = 0
            qtd_a_vencer_ml = 0
            qtd_total_alerta_ml = 0

        if qtd_total_alerta_ml > 0:
            if hasattr(st, "dialog"):
                @st.dialog("⚠️ Alerta de Vencimento - ME LEMBRA")
                def popup_alerta_ml():
                    st.markdown(
                        f"""
                        <div style="border:1px solid #f5c2c7;background:#fff5f5;padding:14px;border-radius:12px;margin-bottom:12px;">
                            <div style="font-size:20px;font-weight:700;color:#b42318;">⚠️ Atenção</div>
                            <div style="color:#7a271a;font-size:15px;">
                                Existem <strong>{qtd_total_alerta_ml}</strong> item(ns) dentro do prazo de alerta configurado.
                            </div>
                        </div>
                        <div style="display:flex;gap:10px;flex-wrap:wrap;">
                            <div style="background:#ffe2e0;color:#7a271a;padding:10px 12px;border-radius:10px;font-weight:600;">
                                Vencidos: {qtd_vencidos_ml}
                            </div>
                            <div style="background:#fff4ce;color:#8a5a00;padding:10px 12px;border-radius:10px;font-weight:600;">
                                A vencer no prazo de alerta: {qtd_a_vencer_ml}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.markdown("**Itens que estão vencidos ou vencem dentro do prazo de alerta:**")
                    st.dataframe(
                        df_alerta_ml[["descricao", "descricao_veiculo", "frota", "data_vencimento", "dias_alerta", "situacao_popup", "status"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "descricao": st.column_config.TextColumn("Item"),
                            "descricao_veiculo": st.column_config.TextColumn("Descrição Veículo"),
                            "frota": st.column_config.TextColumn("Frota"),
                            "data_vencimento": st.column_config.DateColumn("Data Vencimento", format="DD/MM/YYYY"),
                            "dias_alerta": st.column_config.NumberColumn("Dias Alerta", format="%d"),
                            "situacao_popup": st.column_config.TextColumn("Situação"),
                            "status": st.column_config.TextColumn("Status"),
                        },
                    )
                    if st.button("OK, ENTENDI", type="primary", use_container_width=True):
                        st.rerun()

                st.session_state.popup_vencido_exibido = True
                popup_alerta_ml()
            else:
                itens_txt = []
                for _, r in df_alerta_ml.head(10).iterrows():
                    venc = r["data_vencimento"].strftime("%d/%m/%Y") if pd.notna(r["data_vencimento"]) else "-"
                    itens_txt.append(f"{r['descricao']} ({venc}) - {r['situacao_popup']}")
                itens_popup = " | ".join(itens_txt)
                components.html(
                    f"""
                    <script>
                        alert("ATENÇÃO: Existem {qtd_total_alerta_ml} item(ns) no ME LEMBRA dentro do prazo de alerta. Vencidos: {qtd_vencidos_ml} | A vencer no prazo de alerta: {qtd_a_vencer_ml}. Itens: {itens_popup}");
                    </script>
                    """,
                    height=0,
                )
                st.session_state.popup_vencido_exibido = True
        else:
            st.session_state.popup_vencido_exibido = True
    except Exception:
        st.session_state.popup_vencido_exibido = True

# Alerta em popup ao abrir o sistema para vencimentos na aba de TROCAS
if not st.session_state.popup_trocas_exibido:
    try:
        hoje_trocas_popup = date.today()
        with conn() as c:
            df_alerta_trocas = pd.read_sql(
                """SELECT
                       ct.tipo_servico,
                       ct.veiculo_placa,
                       COALESCE(NULLIF(TRIM(ct.descricao_veiculo), ''), v.descricao, '') AS descricao_veiculo,
                       ct.data_vencimento,
                       COALESCE(ct.dias_alerta, 30) AS dias_alerta
                   FROM controle_trocas ct
                   LEFT JOIN veiculos v
                     ON UPPER(TRIM(v.placa)) = UPPER(TRIM(ct.veiculo_placa))
                   WHERE data_vencimento IS NOT NULL
                   ORDER BY date(data_vencimento) ASC""",
                c,
            )

        if not df_alerta_trocas.empty:
            df_alerta_trocas["data_vencimento"] = pd.to_datetime(df_alerta_trocas["data_vencimento"], errors="coerce").dt.date
            df_alerta_trocas["dias_alerta"] = pd.to_numeric(df_alerta_trocas["dias_alerta"], errors="coerce").fillna(30).astype(int)
            df_alerta_trocas["dias_para_vencer"] = df_alerta_trocas["data_vencimento"].apply(
                lambda d: (d - hoje_trocas_popup).days if pd.notna(d) else None
            )
            df_alerta_trocas["veiculo"] = df_alerta_trocas.apply(
                lambda r: (
                    f"{str(r.get('veiculo_placa') or '').strip()} - {str(r.get('descricao_veiculo') or '').strip()}"
                    if str(r.get("descricao_veiculo") or "").strip()
                    else str(r.get("veiculo_placa") or "").strip()
                ),
                axis=1,
            )
            df_alerta_trocas = df_alerta_trocas[
                df_alerta_trocas["dias_para_vencer"].notna()
                & (
                    (df_alerta_trocas["dias_para_vencer"] < 0)
                    | (df_alerta_trocas["dias_para_vencer"] <= df_alerta_trocas["dias_alerta"])
                )
            ].copy()
            df_alerta_trocas["status"] = df_alerta_trocas.apply(
                lambda r: f"Já venceu o prazo de {int(r['dias_alerta'])} dias" if int(r["dias_para_vencer"]) < -int(r["dias_alerta"]) else (
                    "Vencido" if int(r["dias_para_vencer"]) < 0 else f"Vence em até {int(r['dias_alerta'])} dias"
                ),
                axis=1,
            )
            df_alerta_trocas["situacao_popup"] = df_alerta_trocas.apply(
                lambda r: (
                    "Vence hoje" if int(r["dias_para_vencer"]) == 0 else (
                        f"Faltam {int(r['dias_para_vencer'])} dia(s) para vencer" if int(r["dias_para_vencer"]) > 0 else (
                            f"Já venceu o prazo de {int(r['dias_alerta'])} dias (há {abs(int(r['dias_para_vencer']))} dia(s))"
                            if abs(int(r["dias_para_vencer"])) > int(r["dias_alerta"])
                            else f"Vencido há {abs(int(r['dias_para_vencer']))} dia(s)"
                        )
                    )
                ),
                axis=1,
            )
            qtd_vencidos_trocas = int((df_alerta_trocas["dias_para_vencer"] < 0).sum())
            qtd_venc_alerta_trocas = int((df_alerta_trocas["dias_para_vencer"] >= 0).sum())
            qtd_total_alerta_trocas = len(df_alerta_trocas)
        else:
            qtd_vencidos_trocas = 0
            qtd_venc_alerta_trocas = 0
            qtd_total_alerta_trocas = 0

        if qtd_total_alerta_trocas > 0:
            if hasattr(st, "dialog"):
                @st.dialog("⚠️ Alerta de Vencimento - TROCAS")
                def popup_alerta_trocas():
                    st.markdown(
                        f"""
                        <div style="border:1px solid #f5c2c7;background:#fff5f5;padding:14px;border-radius:12px;margin-bottom:12px;">
                            <div style="font-size:20px;font-weight:700;color:#b42318;">⚠️ Atenção</div>
                            <div style="color:#7a271a;font-size:15px;">
                                Existem <strong>{qtd_total_alerta_trocas}</strong> item(ns) no controle de trocas dentro do prazo de alerta configurado.
                            </div>
                        </div>
                        <div style="display:flex;gap:10px;flex-wrap:wrap;">
                            <div style="background:#ffe2e0;color:#7a271a;padding:10px 12px;border-radius:10px;font-weight:600;">
                                Vencidos: {qtd_vencidos_trocas}
                            </div>
                            <div style="background:#fff4ce;color:#8a5a00;padding:10px 12px;border-radius:10px;font-weight:600;">
                                A vencer no prazo de alerta: {qtd_venc_alerta_trocas}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.markdown("**Itens de trocas que estão vencidos ou vencem dentro do prazo de alerta:**")
                    st.dataframe(
                        df_alerta_trocas[["tipo_servico", "veiculo", "data_vencimento", "dias_alerta", "situacao_popup", "status"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "tipo_servico": st.column_config.TextColumn("Tipo de Serviço"),
                            "veiculo": st.column_config.TextColumn("Veículo"),
                            "data_vencimento": st.column_config.DateColumn("Data Vencimento", format="DD/MM/YYYY"),
                            "dias_alerta": st.column_config.NumberColumn("Dias Alerta", format="%d"),
                            "situacao_popup": st.column_config.TextColumn("Situação"),
                            "status": st.column_config.TextColumn("Status"),
                        },
                    )
                    if st.button("OK, ENTENDI", type="primary", use_container_width=True, key="btn_ok_popup_trocas"):
                        st.rerun()

                st.session_state.popup_trocas_exibido = True
                popup_alerta_trocas()
            else:
                itens_trocas_txt = []
                for _, r in df_alerta_trocas.head(10).iterrows():
                    venc = r["data_vencimento"].strftime("%d/%m/%Y") if pd.notna(r["data_vencimento"]) else "-"
                    itens_trocas_txt.append(f"{r['tipo_servico']} - {r['veiculo']} ({venc}) - {r['situacao_popup']}")
                itens_trocas_popup = " | ".join(itens_trocas_txt)
                components.html(
                    f"""
                    <script>
                        alert("ATENÇÃO: Existem {qtd_total_alerta_trocas} item(ns) no CONTROLE DE TROCAS dentro do prazo de alerta configurado. Vencidos: {qtd_vencidos_trocas} | A vencer no prazo de alerta: {qtd_venc_alerta_trocas}. Itens: {itens_trocas_popup}");
                    </script>
                    """,
                    height=0,
                )
                st.session_state.popup_trocas_exibido = True
        else:
            st.session_state.popup_trocas_exibido = True
    except Exception:
        st.session_state.popup_trocas_exibido = True

# Alerta em popup ao abrir o sistema para vencimentos na aba CONTAS A PAGAR
if not st.session_state.popup_cp_exibido:
    try:
        def _cp_popup_parse_valor(txt):
            if txt is None:
                return 0.0
            s = str(txt).strip().replace("R$", "").replace(" ", "")
            if not s:
                return 0.0
            if "," in s and "." in s:
                s = s.replace(".", "").replace(",", ".")
            elif "," in s:
                s = s.replace(",", ".")
            try:
                return float(s)
            except Exception:
                return 0.0

        def _cp_popup_extrair_parcelas(vencimentos_texto):
            parcelas = []
            if not vencimentos_texto:
                return parcelas
            for idx, item in enumerate(str(vencimentos_texto).split(";")):
                trecho = item.strip()
                if not trecho:
                    continue
                numero = None
                restante = trecho
                if ":" in trecho:
                    pref, possivel_restante = trecho.split(":", 1)
                    if pref.strip().isdigit():
                        numero = int(pref.strip())
                        restante = possivel_restante
                data_txt = restante
                valor_txt = ""
                if "|" in restante:
                    data_txt, valor_txt = restante.split("|", 1)
                data_parcela = pd.to_datetime(str(data_txt).strip(), dayfirst=True, errors="coerce")
                if pd.isna(data_parcela):
                    continue
                if numero is None:
                    numero = idx + 1
                parcelas.append(
                    {
                        "parcela": numero,
                        "data": data_parcela.date(),
                        "valor": _cp_popup_parse_valor(valor_txt),
                    }
                )
            return parcelas

        def _cp_popup_parse_parcelas_pagas(parcelas_pagas_texto):
            pagas = set()
            if not parcelas_pagas_texto:
                return pagas
            for token in str(parcelas_pagas_texto).replace(",", ";").split(";"):
                t = token.strip()
                if not t:
                    continue
                if "@" in t:
                    t = t.split("@", 1)[0].strip()
                if t.isdigit():
                    pagas.add(int(t))
            return pagas

        def _cp_popup_proximo_mensal(hoje_ref, dia_venc, data_base):
            data_base_dt = pd.to_datetime(data_base, errors="coerce")
            if pd.notna(data_base_dt):
                data_base_date = data_base_dt.date()
                if data_base_date >= hoje_ref:
                    return data_base_date
                dia_ref = int(data_base_date.day)
            else:
                dia_ref = int(dia_venc or 1)
            dia_ref = max(1, min(31, dia_ref))
            ano = hoje_ref.year
            mes = hoje_ref.month
            dias_mes = int(pd.Period(f"{ano}-{mes:02d}").days_in_month)
            venc_atual = date(ano, mes, min(dia_ref, dias_mes))
            if venc_atual < hoje_ref:
                if mes == 12:
                    ano += 1
                    mes = 1
                else:
                    mes += 1
                dias_mes = int(pd.Period(f"{ano}-{mes:02d}").days_in_month)
                venc_atual = date(ano, mes, min(dia_ref, dias_mes))
            return venc_atual

        def _cp_competencia_ref(data_ref):
            dt = pd.to_datetime(data_ref, errors="coerce")
            if pd.isna(dt):
                return ""
            return dt.strftime("%Y-%m")

        with conn() as c:
            df_cp_popup = pd.read_sql(
                """SELECT cp.id,
                          f.nome AS fornecedor,
                          o.descricao_obrigacao,
                          cp.n_nf,
                          cp.valor_total_nf,
                          cp.valor_parcela,
                          cp.vencimentos_parcelas,
                          cp.tipo_lancamento,
                          cp.dia_vencimento_mensal,
                          cp.data_vencimento_base,
                          cp.competencia_paga_mensal,
                          cp.parcelas_pagas,
                          cp.dias_alerta
                   FROM contas_pagar cp
                   LEFT JOIN fornecedores f ON f.id = cp.fornecedor_id
                   LEFT JOIN obrigacoes o ON o.id = cp.obrigacao_id""",
                c,
            )

        hoje_cp = date.today()
        alertas_cp = []
        if not df_cp_popup.empty:
            for _, r in df_cp_popup.iterrows():
                tipo = str(r.get("tipo_lancamento") or "NAO_MENSAL")
                dias_alerta = max(0, int(r.get("dias_alerta") or 7))
                fornecedor = str(r.get("fornecedor") or "-")
                obrigacao = str(r.get("descricao_obrigacao") or "-")
                nf_ref = str(r.get("n_nf") or "-")

                if tipo == "MENSAL_FIXO":
                    prox = _cp_popup_proximo_mensal(
                        hoje_cp,
                        r.get("dia_vencimento_mensal"),
                        r.get("data_vencimento_base"),
                    )
                    comp_prox = _cp_competencia_ref(prox)
                    comp_paga = str(r.get("competencia_paga_mensal") or "").strip()
                    if comp_paga == comp_prox:
                        continue
                    dias = (prox - hoje_cp).days
                    if -30 <= dias <= dias_alerta:
                        alertas_cp.append(
                            {
                                "Fornecedor": fornecedor,
                                "Obrigação": obrigacao,
                                "NF": nf_ref,
                                "Parcela": "-",
                                "Vencimento": prox,
                                "Valor": float(r.get("valor_parcela") or r.get("valor_total_nf") or 0.0),
                                "Dias": dias,
                            }
                        )
                else:
                    parcelas = _cp_popup_extrair_parcelas(r.get("vencimentos_parcelas"))
                    parcelas_pagas = _cp_popup_parse_parcelas_pagas(r.get("parcelas_pagas"))
                    for p_item in parcelas:
                        parcela_num = int(p_item["parcela"]) if p_item["parcela"] is not None else None
                        if parcela_num is not None and parcela_num in parcelas_pagas:
                            continue
                        dias = (p_item["data"] - hoje_cp).days
                        if -30 <= dias <= dias_alerta:
                            alertas_cp.append(
                                {
                                    "Fornecedor": fornecedor,
                                    "Obrigação": obrigacao,
                                    "NF": nf_ref,
                                    "Parcela": parcela_num if parcela_num is not None else "-",
                                    "Vencimento": p_item["data"],
                                    "Valor": float(p_item["valor"] or r.get("valor_parcela") or 0.0),
                                    "Dias": dias,
                                }
                            )

        if alertas_cp:
            df_alerta_cp = pd.DataFrame(alertas_cp).sort_values(by=["Dias", "Vencimento"], ascending=[True, True]).reset_index(drop=True)
            df_alerta_cp["Situação"] = df_alerta_cp["Dias"].apply(
                lambda d: "Vence hoje" if d == 0 else (f"Vence em {d} dia(s)" if d > 0 else f"Vencido há {abs(d)} dia(s)")
            )
            qtd_total_cp = len(df_alerta_cp)
            qtd_vencidos_cp = int((df_alerta_cp["Dias"] < 0).sum())
            qtd_a_vencer_cp = int((df_alerta_cp["Dias"] >= 0).sum())

            if hasattr(st, "dialog"):
                @st.dialog("⚠️ Alerta de Vencimento - CONTAS A PAGAR")
                def popup_alerta_cp():
                    st.markdown(
                        f"""
                        <div style="border:1px solid #f5c2c7;background:#fff5f5;padding:14px;border-radius:12px;margin-bottom:12px;">
                            <div style="font-size:20px;font-weight:700;color:#b42318;">⚠️ Atenção</div>
                            <div style="color:#7a271a;font-size:15px;">
                                Existem <strong>{qtd_total_cp}</strong> parcela(s)/lançamento(s) em alerta de vencimento.
                            </div>
                        </div>
                        <div style="display:flex;gap:10px;flex-wrap:wrap;">
                            <div style="background:#ffe2e0;color:#7a271a;padding:10px 12px;border-radius:10px;font-weight:600;">
                                Vencidos: {qtd_vencidos_cp}
                            </div>
                            <div style="background:#fff4ce;color:#8a5a00;padding:10px 12px;border-radius:10px;font-weight:600;">
                                A vencer: {qtd_a_vencer_cp}
                            </div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                    st.markdown("**Contas a pagar em alerta:**")
                    st.dataframe(
                        df_alerta_cp[["Situação", "Fornecedor", "Obrigação", "NF", "Parcela", "Vencimento", "Valor"]],
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Situação": st.column_config.TextColumn("Situação"),
                            "Vencimento": st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"),
                            "Valor": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
                        },
                    )
                    if st.button("OK, ENTENDI", type="primary", use_container_width=True, key="btn_ok_popup_cp"):
                        st.rerun()

                st.session_state.popup_cp_exibido = True
                popup_alerta_cp()
            else:
                itens_cp_txt = []
                for _, r in df_alerta_cp.head(10).iterrows():
                    venc = r["Vencimento"].strftime("%d/%m/%Y") if pd.notna(r["Vencimento"]) else "-"
                    itens_cp_txt.append(f"{r['Fornecedor']} | {r['Obrigação']} ({venc}) - {r['Situação']}")
                itens_cp_popup = " | ".join(itens_cp_txt)
                components.html(
                    f"""
                    <script>
                        alert("ATENÇÃO: Existem {qtd_total_cp} alerta(s) em CONTAS A PAGAR. Vencidos: {qtd_vencidos_cp} | A vencer: {qtd_a_vencer_cp}. Itens: {itens_cp_popup}");
                    </script>
                    """,
                    height=0,
                )
                st.session_state.popup_cp_exibido = True
        else:
            st.session_state.popup_cp_exibido = True
    except Exception:
        st.session_state.popup_cp_exibido = True

df_db = _carregar_viagens_periodo_raw(filtro_ini.isoformat(), filtro_fim.isoformat())
if not df_db.empty:
    if placa_filtro_calculo and "veiculo_placa" in df_db.columns:
        placa_ref = str(placa_filtro_calculo).strip().upper()
        df_db = df_db[
            df_db["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref
        ].copy()
    df_db["data"] = pd.to_datetime(df_db["data"]).dt.date
    if "tipo_cobranca" not in df_db.columns:
        df_db["tipo_cobranca"] = "TONELADA"
    if "valor_km" not in df_db.columns:
        df_db["valor_km"] = 0.0
    if "gasto_extra" not in df_db.columns:
        df_db["gasto_extra"] = 0.0
    if "pagto_estadia" not in df_db.columns:
        df_db["pagto_estadia"] = 0.0
    if "valor_adicional_frete" not in df_db.columns:
        df_db["valor_adicional_frete"] = 0.0
    if "descricao_valor_adicional_frete" not in df_db.columns:
        df_db["descricao_valor_adicional_frete"] = ""
    if "descricao_gasto_extra" not in df_db.columns:
        df_db["descricao_gasto_extra"] = ""
    df_db["km"] = pd.to_numeric(df_db["km"], errors="coerce").fillna(0.0)
    df_db["toneladas"] = pd.to_numeric(df_db["toneladas"], errors="coerce").fillna(0.0)
    df_db["valor_ton"] = pd.to_numeric(df_db["valor_ton"], errors="coerce").fillna(0.0)
    df_db["valor_km"] = pd.to_numeric(df_db["valor_km"], errors="coerce").fillna(0.0)
    df_db["gasto_extra"] = pd.to_numeric(df_db["gasto_extra"], errors="coerce").fillna(0.0)
    df_db["pagto_estadia"] = pd.to_numeric(df_db["pagto_estadia"], errors="coerce").fillna(0.0)
    df_db["valor_adicional_frete"] = pd.to_numeric(df_db["valor_adicional_frete"], errors="coerce").fillna(0.0)
    df_db["descricao_valor_adicional_frete"] = df_db["descricao_valor_adicional_frete"].fillna("").astype(str)
    df_db["descricao_gasto_extra"] = df_db["descricao_gasto_extra"].fillna("").astype(str)
    df_db["tipo_cobranca"] = df_db["tipo_cobranca"].astype(str).str.upper().str.strip()
    frete_km_db = df_db["km"] * df_db["valor_km"]
    frete_ton_db = df_db["toneladas"] * df_db["valor_ton"]
    df_db["Total Frete"] = frete_ton_db.where(df_db["tipo_cobranca"] != "KM", frete_km_db)

# =========================
# DEFINIÇÃO DAS ABAS
# =========================
aba_home, aba1, aba2, aba3, aba4, aba_cadastro, aba8, aba9, aba11, aba13, aba14, aba17, aba_cr, aba_fluxo, aba18, aba20, aba_usuarios, aba_calc = st.tabs([
    "🏠 Dashboard Executivo",
    "📌 Movimento Viagens", "📋 Viagens Executadas", "📊 Análise", "🛠️ Manutenção", "🗂️ Cadastro",
    "📑 Relatório", "⛽ Abastecimento", "🎯 Metas", "🛢️ Trocas",
    "💵 Frete Líquido no Período", "🧾 Contas a Pagar", "💰 Contas a Receber", "📈 Fluxo de Caixa", "🔔 ME LEMBRA", "📝 Anotações",
    "👤 Usuários", "🧮 Cálculo Rápido"
])

with aba_cadastro:
    aba5, aba6, aba7, aba10, aba12, aba15, aba16, aba19, aba21 = st.tabs([
        "🏢 Oficinas", "🏙️ Cidades", "🛣️ KM Rotas", "⚙️ Parâmetros",
        "🚚 Veículos", "🏭 Fornecedores", "📌 Obrigação", "⛽ Comparativo Diesel", "🛣️ Praça Pedágio"
    ])

with aba_usuarios:
    st.subheader("👤 Usuários")
    if not st.session_state.get("usuario_admin"):
        st.warning("Somente o administrador pode cadastrar usuários, alterar senhas e liberar estações.")
    else:
        usuarios_rows = listar_usuarios_sistema()
        usuarios_opcoes = [f"{r['id']} - {r['usuario']}" for r in usuarios_rows]
        mapa_usuarios = {f"{r['id']} - {r['usuario']}": r for r in usuarios_rows}

        tab_incluir_usuario, tab_alterar_usuario, tab_deletar_usuario = st.tabs([
            "➕ Incluir", "✏️ Alterar", "🗑️ Deletar"
        ])

        with tab_incluir_usuario:
            st.markdown("##### Incluir usuário")
            with st.form("form_admin_cadastrar_usuario"):
                novo_usuario = st.text_input("Usuário", key="admin_novo_usuario")
                nova_senha = st.text_input("Senha", type="password", key="admin_nova_senha")
                novo_admin = st.checkbox("Administrador", value=False, key="admin_novo_is_admin")
                if st.form_submit_button("➕ Incluir usuário", type="primary"):
                    ok, msg = cadastrar_usuario_sistema(
                        novo_usuario,
                        nova_senha,
                        is_admin=novo_admin,
                        liberar_estacao=False,
                    )
                    if ok:
                        alerta_gravado(msg)
                        st.rerun()
                    else:
                        st.error(msg)

        with tab_alterar_usuario:
            st.markdown("##### Alterar usuário")
            if usuarios_opcoes:
                usuario_alt_sel = st.selectbox("Selecione o usuário", usuarios_opcoes, key="admin_usuario_alt_sel")
                row_alt = mapa_usuarios[usuario_alt_sel]
                with st.form("form_admin_alterar_usuario"):
                    usuario_alt_nome = st.text_input("Usuário", value=str(row_alt["usuario"] or ""), key="admin_usuario_alt_nome")
                    senha_alt = st.text_input("Nova senha (deixe em branco para manter)", type="password", key="admin_usuario_alt_senha")
                    admin_alt = st.checkbox("Administrador", value=bool(int(row_alt["is_admin"] or 0) == 1), key="admin_usuario_alt_admin")
                    ativo_alt = st.checkbox("Ativo", value=bool(int(row_alt["ativo"] or 0) == 1), key="admin_usuario_alt_ativo")
                    if st.form_submit_button("✏️ Alterar usuário", type="primary"):
                        ok, msg = atualizar_usuario_sistema(
                            int(row_alt["id"]),
                            usuario_alt_nome,
                            nova_senha=senha_alt,
                            is_admin=admin_alt,
                            ativo=ativo_alt,
                        )
                        if ok:
                            alerta_gravado(msg)
                            st.rerun()
                        else:
                            st.error(msg)
            else:
                st.info("Nenhum usuário cadastrado.")

        with tab_deletar_usuario:
            st.markdown("##### Deletar usuário")
            if usuarios_opcoes:
                usuario_del_sel = st.selectbox("Selecione o usuário para deletar", usuarios_opcoes, key="admin_usuario_del_sel")
                row_del = mapa_usuarios[usuario_del_sel]
                st.warning(f"Confirma deletar o usuário {row_del['usuario']}? As estações liberadas para ele também serão removidas.")
                confirmar_del = st.checkbox("Confirmo que desejo deletar este usuário", key="admin_usuario_del_confirm")
                if st.button("🗑️ Deletar usuário", type="primary", disabled=not confirmar_del):
                    ok, msg = deletar_usuario_sistema(int(row_del["id"]))
                    if ok:
                        alerta_gravado(msg)
                        st.rerun()
                    else:
                        st.error(msg)
            else:
                st.info("Nenhum usuário cadastrado.")

        st.markdown("##### Usuários cadastrados")
        df_usuarios_admin = pd.DataFrame(
            [
                {
                    "ID": int(r["id"]),
                    "Usuário": r["usuario"],
                    "Administrador": "Sim" if int(r["is_admin"] or 0) == 1 else "Não",
                    "Ativo": "Sim" if int(r["ativo"] or 0) == 1 else "Não",
                    "Cadastro": r["data_cadastro"] or "",
                }
                for r in listar_usuarios_sistema()
            ]
        )
        st.dataframe(df_usuarios_admin, use_container_width=True, hide_index=True)

with aba_home:
    st.markdown(
        """
        <style>
        .stApp {
            font-family: "Trebuchet MS", "Gill Sans", "Verdana", sans-serif;
        }
        .dash-shell {
            background: linear-gradient(180deg, #ffffff 0%, #f7fbff 100%);
            border: 1px solid #dbe7f3;
            border-radius: 18px;
            padding: 16px;
            margin-bottom: 12px;
        }
        .dash-hero {
            background: linear-gradient(120deg, #0b3c5d 0%, #1b6ca8 48%, #00a6a6 100%);
            padding: 22px 24px;
            border-radius: 18px;
            color: #ffffff;
            box-shadow: 0 10px 24px rgba(11, 60, 93, 0.28);
            margin-bottom: 14px;
            animation: risein 0.5s ease-out;
        }
        .dash-hero h2 {
            margin: 0 0 4px 0;
            font-size: 29px;
            font-weight: 800;
        }
        .dash-hero p {
            margin: 0 0 8px 0;
            font-size: 14px;
            opacity: 0.95;
        }
        .dash-pill {
            display: inline-block;
            padding: 7px 12px;
            border-radius: 999px;
            font-size: 12px;
            font-weight: 700;
            margin-top: 6px;
            background: rgba(255, 255, 255, 0.16);
            border: 1px solid rgba(255, 255, 255, 0.35);
        }
        .kpi-card {
            background: #ffffff;
            border: 1px solid #d7e5f2;
            border-radius: 14px;
            padding: 12px 14px;
            box-shadow: 0 6px 12px rgba(12, 44, 74, 0.06);
            min-height: 110px;
            animation: risein 0.5s ease-out;
        }
        .kpi-label {
            color: #37516c;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            font-weight: 700;
        }
        .kpi-value {
            color: #102a43;
            font-size: 23px;
            font-weight: 800;
            margin-top: 3px;
            margin-bottom: 5px;
        }
        .kpi-foot {
            color: #486581;
            font-size: 12px;
            font-weight: 600;
        }
        .meta-wrap {
            background: #ffffff;
            border: 1px solid #d7e5f2;
            border-radius: 14px;
            padding: 12px 14px;
            margin: 8px 0 4px 0;
        }
        .meta-track {
            width: 100%;
            height: 12px;
            border-radius: 99px;
            background: #e6eef7;
            overflow: hidden;
            margin-top: 8px;
        }
        .meta-fill {
            height: 100%;
            border-radius: 99px;
            background: linear-gradient(90deg, #0ea5a5 0%, #f59e0b 70%, #ef4444 100%);
            transition: width 0.4s ease;
        }
        .alert-chip {
            display: inline-block;
            border-radius: 10px;
            padding: 8px 11px;
            margin-right: 8px;
            margin-top: 8px;
            font-size: 13px;
            font-weight: 700;
        }
        .chip-a {
            background: #fff4e5;
            color: #9a3412;
            border: 1px solid #fdba74;
        }
        .chip-b {
            background: #fff1f2;
            color: #9f1239;
            border: 1px solid #fda4af;
        }
        @keyframes risein {
            from { opacity: 0; transform: translateY(8px); }
            to { opacity: 1; transform: translateY(0); }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    if df_db.empty:
        st.info("Sem dados de viagens no período selecionado. Cadastre viagens para visualizar os indicadores.")
    else:
        df_dash = df_db.copy()
        origem_norm_dash_kpi = df_dash["origem"].fillna("").astype(str).str.strip().str.upper()
        destino_norm_dash_kpi = df_dash["destino"].fillna("").astype(str).str.strip().str.upper()
        mask_od_diferente_dash = origem_norm_dash_kpi != destino_norm_dash_kpi
        qtd_ignoradas_dash = int((~mask_od_diferente_dash).sum())
        df_dash = df_dash.loc[mask_od_diferente_dash].copy()
        if qtd_ignoradas_dash > 0:
            st.info(
                f"{qtd_ignoradas_dash} viagem(ns) com origem = destino foram desconsideradas na Dashboard para manter o mesmo cálculo da Análise."
            )
        if "qtd_viagens" not in df_dash.columns:
            df_dash["qtd_viagens"] = 1
        if "tipo_cobranca" not in df_dash.columns:
            df_dash["tipo_cobranca"] = "TONELADA"
        if "valor_km" not in df_dash.columns:
            df_dash["valor_km"] = 0.0
        if "gasto_extra" not in df_dash.columns:
            df_dash["gasto_extra"] = 0.0
        if "pagto_estadia" not in df_dash.columns:
            df_dash["pagto_estadia"] = 0.0
        if "valor_adicional_frete" not in df_dash.columns:
            df_dash["valor_adicional_frete"] = 0.0
        if "descricao_valor_adicional_frete" not in df_dash.columns:
            df_dash["descricao_valor_adicional_frete"] = ""
        if "diesel" not in df_dash.columns:
            df_dash["diesel"] = 0.0
        if "consumo" not in df_dash.columns:
            df_dash["consumo"] = 0.0
        if "arla" not in df_dash.columns:
            df_dash["arla"] = 0.0
        if "consumo_arla" not in df_dash.columns:
            df_dash["consumo_arla"] = 0.0

        df_dash["tipo_cobranca"] = df_dash["tipo_cobranca"].fillna("TONELADA").astype(str).str.upper().str.strip()
        df_dash["km"] = pd.to_numeric(df_dash["km"], errors="coerce").fillna(0.0)
        df_dash["toneladas"] = pd.to_numeric(df_dash["toneladas"], errors="coerce").fillna(0.0)
        df_dash["valor_ton"] = pd.to_numeric(df_dash["valor_ton"], errors="coerce").fillna(0.0)
        df_dash["valor_km"] = pd.to_numeric(df_dash["valor_km"], errors="coerce").fillna(0.0)
        df_dash["qtd_viagens"] = pd.to_numeric(df_dash["qtd_viagens"], errors="coerce").fillna(1.0)
        df_dash["qtd_viagens"] = df_dash["qtd_viagens"].apply(lambda x: max(1, int(round(float(x)))))
        df_dash["pedagio"] = pd.to_numeric(df_dash["pedagio"], errors="coerce").fillna(0.0)
        df_dash["gasto_extra"] = pd.to_numeric(df_dash["gasto_extra"], errors="coerce").fillna(0.0)
        df_dash["pagto_estadia"] = pd.to_numeric(df_dash["pagto_estadia"], errors="coerce").fillna(0.0)
        df_dash["valor_adicional_frete"] = pd.to_numeric(df_dash["valor_adicional_frete"], errors="coerce").fillna(0.0)
        df_dash["descricao_valor_adicional_frete"] = df_dash["descricao_valor_adicional_frete"].fillna("").astype(str)
        df_dash["diesel"] = pd.to_numeric(df_dash["diesel"], errors="coerce").fillna(0.0)
        df_dash["consumo"] = pd.to_numeric(df_dash["consumo"], errors="coerce").fillna(0.0)
        df_dash["arla"] = pd.to_numeric(df_dash["arla"], errors="coerce").fillna(0.0)
        df_dash["consumo_arla"] = pd.to_numeric(df_dash["consumo_arla"], errors="coerce").fillna(0.0)

        df_dash["frete_base"] = df_dash.apply(
            lambda r: (r["km"] * r["valor_km"]) if r["tipo_cobranca"] == "KM" else (r["toneladas"] * r["valor_ton"]),
            axis=1,
        )
        df_dash["receita_comissionavel_unit"] = (
            df_dash["frete_base"] + df_dash["pagto_estadia"]
        )
        df_dash["receita_total"] = (
            (df_dash["receita_comissionavel_unit"] + df_dash["valor_adicional_frete"]) * df_dash["qtd_viagens"]
        ).fillna(0.0)
        df_dash["km_total"] = (df_dash["km"] * df_dash["qtd_viagens"]).fillna(0.0)
        df_dash["litros_diesel"] = (df_dash["km_total"] / df_dash["consumo"].where(df_dash["consumo"] > 0)).fillna(0.0)
        df_dash["custo_diesel"] = (df_dash["litros_diesel"] * df_dash["diesel"]).fillna(0.0)
        df_dash["litros_arla"] = (df_dash["km_total"] / df_dash["consumo_arla"].where(df_dash["consumo_arla"] > 0)).fillna(0.0)
        df_dash["custo_arla"] = (df_dash["litros_arla"] * df_dash["arla"]).fillna(0.0)
        with conn() as c:
            df_abs_dash = pd.read_sql(
                """SELECT tipo_combustivel, total_gasto, veiculo_placa
                   FROM abastecimentos
                   WHERE date(data) BETWEEN ? AND ?""",
                c,
                params=(filtro_ini.isoformat(), filtro_fim.isoformat()),
            )
        if placa_filtro_calculo and not df_abs_dash.empty and "veiculo_placa" in df_abs_dash.columns:
            placa_ref_abs_dash = str(placa_filtro_calculo).strip().upper()
            df_abs_dash = df_abs_dash[
                df_abs_dash["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_abs_dash
            ].copy()
        if not df_abs_dash.empty:
            df_abs_dash["tipo_combustivel"] = df_abs_dash["tipo_combustivel"].apply(normalizar_tipo_combustivel)
            df_abs_dash["total_gasto"] = pd.to_numeric(df_abs_dash["total_gasto"], errors="coerce").fillna(0.0)
            custo_arla_real_dash = float(
                df_abs_dash[df_abs_dash["tipo_combustivel"].str.contains("ARLA", na=False)]["total_gasto"].sum()
            )
            total_km_rateio_arla_dash = float(df_dash["km_total"].sum())
            if custo_arla_real_dash > 0 and total_km_rateio_arla_dash > 0:
                df_dash["custo_arla"] = (custo_arla_real_dash * (df_dash["km_total"] / total_km_rateio_arla_dash)).fillna(0.0)
            # else: mantém custo_arla calculado por arla/consumo_arla das viagens
        df_dash["custo_pedagio"] = (df_dash["pedagio"] * df_dash["qtd_viagens"]).fillna(0.0)
        df_dash["custo_extra"] = (df_dash["gasto_extra"] * df_dash["qtd_viagens"]).fillna(0.0)
        df_dash = aplicar_parametros_por_data(df_dash, col_data="data")
        imposto_pct_dash_series = (pd.to_numeric(df_dash["param_imposto_pct"], errors="coerce").fillna(0.0) / 100.0)
        df_dash["custo_imposto"] = (df_dash["receita_total"] * imposto_pct_dash_series).fillna(0.0)
        
        # Adicionar receita de frete fixo rateada PRIMEIRO
        total_receita_bruta = df_dash["receita_total"].sum()
        if total_receita_bruta > 0:
            proporcao_receita_inicial = df_dash["receita_total"] / total_receita_bruta
            df_dash["frete_fixo_rateado"] = valor_frete_fixo_periodo * proporcao_receita_inicial
            df_dash["receita_total"] = df_dash["receita_total"] + df_dash["frete_fixo_rateado"]
        else:
            df_dash["frete_fixo_rateado"] = 0.0
        
        # RECALCULAR proporção de receita DEPOIS de adicionar frete fixo
        total_receita_final = df_dash["receita_total"].sum()
        if total_receita_final > 0:
            proporcao_receita = df_dash["receita_total"] / total_receita_final
        else:
            proporcao_receita = pd.Series([0.0] * len(df_dash), index=df_dash.index)
        
        # Adicionar custos baseados em KM (consistente com Análise)
        df_dash["custo_pneu"] = df_dash["km_total"] * pd.to_numeric(df_dash["param_pneu"], errors="coerce").fillna(0.0)
        df_dash["custo_manut"] = df_dash["km_total"] * pd.to_numeric(df_dash["param_manut"], errors="coerce").fillna(0.0)
        df_dash["custo_depre"] = df_dash["km_total"] * pd.to_numeric(df_dash["param_depre"], errors="coerce").fillna(0.0)
        
        # Comissão motorista baseada em percentual
        df_dash["custo_comissao"] = (
            (df_dash["receita_comissionavel_unit"] * df_dash["qtd_viagens"])
            * (pd.to_numeric(df_dash["param_motora_pct"], errors="coerce").fillna(0.0) / 100.0)
        )
        
        # Custos fixos rateados proporcionalmente ao receita_total FINAL (com frete fixo)
        if total_receita_final > 0:
            df_dash["custo_mot_fixo_rateado"] = (
                valor_mensal_rateado_periodo("motora_fixo", filtro_ini, filtro_fim) * proporcao_receita
            )
            df_dash["custo_seguro_rateado"] = (
                valor_mensal_rateado_periodo("seguro", filtro_ini, filtro_fim) * proporcao_receita
            )
            df_dash["custo_seguro_vida_motorista_rateado"] = (
                valor_mensal_rateado_periodo("seguro_vida_motorista", filtro_ini, filtro_fim) * proporcao_receita
            )
            df_dash["custo_fin_rateado"] = (
                valor_mensal_rateado_periodo("financiamento", filtro_ini, filtro_fim) * proporcao_receita
            )
            df_dash["custo_ipva_rateado"] = (
                valor_anual_rateado_periodo("pagto_ipva", filtro_ini, filtro_fim) * proporcao_receita
            )
            df_dash["custo_escr_rateado"] = (
                valor_mensal_rateado_periodo("cmp_custo_escritorio", filtro_ini, filtro_fim) * proporcao_receita
            )
            df_dash["custo_rastreador_rateado"] = (
                valor_mensal_rateado_periodo("vl_custo_rastreador", filtro_ini, filtro_fim) * proporcao_receita
            )
            
            # Custos de frete fixo rateados proporcionalmente (obtidos via parametros)
            custo_comissao_frete_fixo_total_periodo = float(
                (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim) / 30.0
                 * (serie_parametro_diaria("motora_pct", filtro_ini, filtro_fim) / 100.0)).sum()
            )
            custo_imposto_frete_fixo_total_periodo = float(
                (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim) / 30.0
                 * (serie_parametro_diaria("imposto_pct", filtro_ini, filtro_fim) / 100.0)).sum()
            )
            
            df_dash["custo_comissao_frete_fixo"] = custo_comissao_frete_fixo_total_periodo * proporcao_receita
            df_dash["custo_imposto_frete_fixo"] = custo_imposto_frete_fixo_total_periodo * proporcao_receita
        else:
            df_dash["custo_mot_fixo_rateado"] = 0.0
            df_dash["custo_seguro_rateado"] = 0.0
            df_dash["custo_seguro_vida_motorista_rateado"] = 0.0
            df_dash["custo_fin_rateado"] = 0.0
            df_dash["custo_ipva_rateado"] = 0.0
            df_dash["custo_escr_rateado"] = 0.0
            df_dash["custo_rastreador_rateado"] = 0.0
            df_dash["custo_comissao_frete_fixo"] = 0.0
            df_dash["custo_imposto_frete_fixo"] = 0.0
        
        df_dash["lucro_operacional"] = (
            df_dash["receita_total"]
            - df_dash["custo_diesel"]
            - df_dash["custo_arla"]
            - df_dash["custo_pedagio"]
            - df_dash["custo_extra"]
            - df_dash["custo_imposto"]
            - df_dash["custo_pneu"]
            - df_dash["custo_manut"]
            - df_dash["custo_depre"]
            - df_dash["custo_comissao"]
            - df_dash["custo_mot_fixo_rateado"]
            - df_dash["custo_seguro_rateado"]
            - df_dash["custo_seguro_vida_motorista_rateado"]
            - df_dash["custo_fin_rateado"]
            - df_dash["custo_ipva_rateado"]
            - df_dash["custo_escr_rateado"]
            - df_dash["custo_rastreador_rateado"]
            - df_dash["custo_comissao_frete_fixo"]
            - df_dash["custo_imposto_frete_fixo"]
        ).fillna(0.0)

        total_receita_dash = float(df_dash["receita_total"].sum())
        total_km_dash = float(df_dash["km_total"].sum())
        total_viagens_dash = int(df_dash["qtd_viagens"].sum())
        total_custo_dash = float(
            (
                df_dash["custo_diesel"]
                + df_dash["custo_arla"]
                + df_dash["custo_pedagio"]
                + df_dash["custo_extra"]
                + df_dash["custo_imposto"]
                + df_dash["custo_pneu"]
                + df_dash["custo_manut"]
                + df_dash["custo_depre"]
                + df_dash["custo_comissao"]
                + df_dash["custo_mot_fixo_rateado"]
                + df_dash["custo_seguro_rateado"]
                + df_dash["custo_seguro_vida_motorista_rateado"]
                + df_dash["custo_fin_rateado"]
                + df_dash["custo_ipva_rateado"]
                + df_dash["custo_escr_rateado"]
                + df_dash["custo_rastreador_rateado"]
                + df_dash["custo_comissao_frete_fixo"]
                + df_dash["custo_imposto_frete_fixo"]
            ).sum()
        )
        total_lucro_dash = float(df_dash["lucro_operacional"].sum())
        
        # Calcular ticket médio antes de adicionar frete fixo (média por viagem)
        ticket_medio_dash = (total_receita_dash / total_viagens_dash) if total_viagens_dash > 0 else 0.0
        
        margem_dash = (total_lucro_dash / total_receita_dash * 100.0) if total_receita_dash > 0 else 0.0

        meta_faturamento = float(p.get("meta_faturamento", 50000.0))
        perc_meta = (total_receita_dash / meta_faturamento * 100.0) if meta_faturamento > 0 else 0.0
        delta_meta = total_receita_dash - meta_faturamento
        perc_meta_lim = max(0.0, min(perc_meta, 100.0))
        custo_pct_receita = (total_custo_dash / total_receita_dash * 100.0) if total_receita_dash > 0 else 0.0

        hoje_dash = date.today().isoformat()
        with conn() as c:
            alertas_ml_dash = c.execute(
                """
                SELECT COUNT(*) AS qtd
                FROM me_lembra
                WHERE data_vencimento IS NOT NULL
                  AND date(data_vencimento) <= date(?, '+30 day')
                """,
                (hoje_dash,),
            ).fetchone()
            alertas_trocas_dash = c.execute(
                """
                SELECT COUNT(*) AS qtd
                FROM controle_trocas
                WHERE data_vencimento IS NOT NULL
                  AND (
                      date(data_vencimento) < date(?)
                      OR date(data_vencimento) <= date(?, '+' || COALESCE(dias_alerta, 30) || ' day')
                  )
                """,
                (hoje_dash, hoje_dash),
            ).fetchone()
        qtd_alertas_ml = int(alertas_ml_dash["qtd"] if alertas_ml_dash else 0)
        qtd_alertas_trocas = int(alertas_trocas_dash["qtd"] if alertas_trocas_dash else 0)
        st.markdown(
            f"""
            <div class="dash-shell">
                <div class="dash-hero">
                    <h2>Painel Executivo ART</h2>
                    <p>Período analisado: <strong>{filtro_ini.strftime('%d/%m/%Y')}</strong> até <strong>{filtro_fim.strftime('%d/%m/%Y')}</strong></p>
                    <span class="dash-pill">Meta atingida: {perc_meta:.1f}%</span>
                    <span class="dash-pill">Margem atual: {margem_dash:.1f}%</span>
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        c_k1, c_k2, c_k3, c_k4 = st.columns(4)
        c_k1.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Receita do Período</div>
                <div class="kpi-value">{brl(total_receita_dash)}</div>
                <div class="kpi-foot">Ticket médio: {brl(ticket_medio_dash)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c_k2.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Lucro Operacional</div>
                <div class="kpi-value">{brl(total_lucro_dash)}</div>
                <div class="kpi-foot">Margem líquida: {margem_dash:.1f}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c_k3.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Custo Operacional</div>
                <div class="kpi-value">{brl(total_custo_dash)}</div>
                <div class="kpi-foot">Peso na receita: {custo_pct_receita:.1f}%</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
        c_k4.markdown(
            f"""
            <div class="kpi-card">
                <div class="kpi-label">Produção</div>
                <div class="kpi-value">{format_br(total_viagens_dash, casas_decimais=0)} viagens</div>
                <div class="kpi-foot">KM rodado: {format_br(total_km_dash, casas_decimais=0)} km</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="meta-wrap">
                <div style="display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;">
                    <div style="font-weight:800;color:#12344d;">Meta de Faturamento</div>
                    <div style="font-weight:700;color:#486581;">Meta: {brl(meta_faturamento)} | Realizado: {brl(total_receita_dash)} | Meta: {brl(delta_meta)}</div>
                </div>
                <div class="meta-track"><div class="meta-fill" style="width:{perc_meta_lim:.2f}%;"></div></div>
                <div style="margin-top:8px;color:#36556f;font-weight:700;font-size:12px;">{perc_meta:.1f}% da meta atingida no período atual</div>
                <span class="alert-chip chip-a">ME LEMBRA em 30 dias: {qtd_alertas_ml}</span>
                <span class="alert-chip chip-b">TROCAS no alerta: {qtd_alertas_trocas}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

        df_tend = df_dash.copy()
        origem_norm_dash = df_tend["origem"].fillna("").astype(str).str.strip().str.upper()
        destino_norm_dash = df_tend["destino"].fillna("").astype(str).str.strip().str.upper()
        df_tend = df_tend.loc[origem_norm_dash != destino_norm_dash].copy()

        if not df_tend.empty:
            df_tend["data_dt"] = pd.to_datetime(df_tend["data"], errors="coerce")
            df_tend["mes_periodo"] = df_tend["data_dt"].dt.to_period("M")
            df_tend["mes"] = df_tend["mes_periodo"].astype(str)

            df_tend_g = (
                df_tend.groupby("mes", as_index=False)
                .agg(
                    receita=("receita_total", "sum"),
                    frete_fixo_rateado=("frete_fixo_rateado", "sum"),
                    custo_diesel=("custo_diesel", "sum"),
                    custo_arla=("custo_arla", "sum"),
                    custo_pedagio=("custo_pedagio", "sum"),
                    custo_extra=("custo_extra", "sum"),
                    custo_pneu=("custo_pneu", "sum"),
                    custo_manut=("custo_manut", "sum"),
                    custo_depre=("custo_depre", "sum"),
                    custo_comissao=("custo_comissao", "sum"),
                    custo_imposto=("custo_imposto", "sum"),
                    custo_mot_fixo_rateado=("custo_mot_fixo_rateado", "sum"),
                    custo_seguro_rateado=("custo_seguro_rateado", "sum"),
                    custo_seguro_vida_motorista_rateado=("custo_seguro_vida_motorista_rateado", "sum"),
                    custo_fin_rateado=("custo_fin_rateado", "sum"),
                    custo_ipva_rateado=("custo_ipva_rateado", "sum"),
                    custo_escr_rateado=("custo_escr_rateado", "sum"),
                    custo_rastreador_rateado=("custo_rastreador_rateado", "sum"),
                    custo_comissao_frete_fixo=("custo_comissao_frete_fixo", "sum"),
                    custo_imposto_frete_fixo=("custo_imposto_frete_fixo", "sum"),
                )
                .sort_values("mes")
            )

            def dias_mes_no_filtro(mes_txt):
                p_mes = pd.Period(mes_txt, freq="M")
                ini_mes = p_mes.start_time.date()
                fim_mes = p_mes.end_time.date()
                ini_ref = max(filtro_ini, ini_mes)
                fim_ref = min(filtro_fim, fim_mes)
                if fim_ref < ini_ref:
                    return 0
                return (fim_ref - ini_ref).days + 1

            df_tend_g["dias_no_filtro"] = df_tend_g["mes"].apply(dias_mes_no_filtro)
            # Calcular custos totais agregando os já rateados em viagens individuais
            df_tend_g["custos"] = (
                df_tend_g["custo_diesel"]
                + df_tend_g["custo_arla"]
                + df_tend_g["custo_pedagio"]
                + df_tend_g["custo_extra"]
                + df_tend_g["custo_pneu"]
                + df_tend_g["custo_manut"]
                + df_tend_g["custo_depre"]
                + df_tend_g["custo_comissao"]
                + df_tend_g["custo_imposto"]
                + df_tend_g["custo_comissao_frete_fixo"]
                + df_tend_g["custo_imposto_frete_fixo"]
                + df_tend_g["custo_mot_fixo_rateado"]
                + df_tend_g["custo_seguro_rateado"]
                + df_tend_g["custo_seguro_vida_motorista_rateado"]
                + df_tend_g["custo_fin_rateado"]
                + df_tend_g["custo_ipva_rateado"]
                + df_tend_g["custo_escr_rateado"]
                + df_tend_g["custo_rastreador_rateado"]
            )
            df_tend_g["lucro"] = df_tend_g["receita"] - df_tend_g["custos"]
        else:
            df_tend_g = pd.DataFrame(columns=["mes", "receita", "custos", "lucro"])

        df_rotas_dash = (
            df_dash.groupby(["origem", "destino"], as_index=False)
            .agg(receita=("receita_total", "sum"), lucro=("lucro_operacional", "sum"), viagens=("qtd_viagens", "sum"), km=("km_total", "sum"))
        )
        if not df_rotas_dash.empty:
            df_rotas_dash["rota"] = df_rotas_dash["origem"].astype(str) + " → " + df_rotas_dash["destino"].astype(str)
            top_rotas = df_rotas_dash.sort_values("receita", ascending=False).head(8).sort_values("receita", ascending=True)
            top_lucro = df_rotas_dash.sort_values("lucro", ascending=False).head(1)
            if not top_lucro.empty:
                r_lucro = top_lucro.iloc[0]
                st.caption(
                    f"Rota destaque de lucro: {r_lucro['rota']} | Lucro: {brl(float(r_lucro['lucro']))} | Receita: {brl(float(r_lucro['receita']))}"
                )
        else:
            top_rotas = pd.DataFrame(columns=["rota", "receita", "lucro"])

        t1, t2, t3 = st.tabs(["📈 Performance", "💸 Custos", "🛣️ Rotas"])

        with t1:
            st.caption("Leitura do gráfico: `Lucro` segue a mesma lógica da aba Análise (com rateio de custos e fixos).")

            df_perf = df_tend_g.copy()
            if df_perf.empty:
                st.info("Sem dados mensais para exibir a evolução no período.")
            else:
                meses_dt = pd.to_datetime(df_perf["mes"] + "-01", errors="coerce")
                df_perf["mes_label"] = meses_dt.dt.strftime("%m/%Y")
                df_perf["margem_pct"] = (df_perf["lucro"] / df_perf["receita"].where(df_perf["receita"] > 0) * 100.0).fillna(0.0)

                def fmt_curto_brl(v):
                    val = float(v or 0.0)
                    av = abs(val)
                    if av >= 1_000_000:
                        return f"R$ {val/1_000_000:.2f} mi"
                    if av >= 1_000:
                        return f"R$ {val/1_000:.1f} mil"
                    return brl(val)

                df_perf["receita_txt"] = df_perf["receita"].apply(fmt_curto_brl)
                df_perf["custos_txt"] = df_perf["custos"].apply(fmt_curto_brl)
                df_perf["lucro_txt"] = df_perf["lucro"].apply(fmt_curto_brl)
                df_perf["receita_hover"] = df_perf["receita"].apply(brl)
                df_perf["custos_hover"] = df_perf["custos"].apply(brl)
                df_perf["lucro_hover"] = df_perf["lucro"].apply(brl)

                tipo_grafico_perf = st.selectbox(
                    "Tipo de gráfico",
                    ["Combinado", "Barras", "Linhas", "Área"],
                    index=1,
                    key="dash_tipo_grafico_perf",
                )

                fig_tend = go.Figure()
                if tipo_grafico_perf == "Barras":
                    fig_tend.add_trace(
                        go.Bar(
                            x=df_perf["mes_label"], y=df_perf["receita"], name="Receita",
                            marker_color="#0077b6", text=df_perf["receita_txt"], textposition="outside",
                            customdata=df_perf["receita_hover"], hovertemplate="Mês: %{x}<br>Receita: %{customdata}<extra></extra>",
                        )
                    )
                    fig_tend.add_trace(
                        go.Bar(
                            x=df_perf["mes_label"], y=df_perf["custos"], name="Custos",
                            marker_color="#f4a261", text=df_perf["custos_txt"], textposition="outside",
                            customdata=df_perf["custos_hover"], hovertemplate="Mês: %{x}<br>Custos: %{customdata}<extra></extra>",
                        )
                    )
                    fig_tend.add_trace(
                        go.Bar(
                            x=df_perf["mes_label"], y=df_perf["lucro"], name="Lucro",
                            marker_color="#2a9d8f", text=df_perf["lucro_txt"], textposition="outside",
                            customdata=df_perf["lucro_hover"], hovertemplate="Mês: %{x}<br>Lucro: %{customdata}<extra></extra>",
                        )
                    )
                elif tipo_grafico_perf == "Linhas":
                    fig_tend.add_trace(
                        go.Scatter(
                            x=df_perf["mes_label"], y=df_perf["receita"], name="Receita",
                            mode="lines+markers+text", text=df_perf["receita_txt"], textposition="top center",
                            customdata=df_perf["receita_hover"], hovertemplate="Mês: %{x}<br>Receita: %{customdata}<extra></extra>",
                            line=dict(color="#0077b6", width=3), marker=dict(size=8),
                        )
                    )
                    fig_tend.add_trace(
                        go.Scatter(
                            x=df_perf["mes_label"], y=df_perf["custos"], name="Custos",
                            mode="lines+markers+text", text=df_perf["custos_txt"], textposition="top center",
                            customdata=df_perf["custos_hover"], hovertemplate="Mês: %{x}<br>Custos: %{customdata}<extra></extra>",
                            line=dict(color="#f4a261", width=3), marker=dict(size=8),
                        )
                    )
                    fig_tend.add_trace(
                        go.Scatter(
                            x=df_perf["mes_label"], y=df_perf["lucro"], name="Lucro",
                            mode="lines+markers+text", text=df_perf["lucro_txt"], textposition="top center",
                            customdata=df_perf["lucro_hover"], hovertemplate="Mês: %{x}<br>Lucro: %{customdata}<extra></extra>",
                            line=dict(color="#2a9d8f", width=3), marker=dict(size=8),
                        )
                    )
                elif tipo_grafico_perf == "Área":
                    fig_tend.add_trace(
                        go.Scatter(
                            x=df_perf["mes_label"], y=df_perf["receita"], name="Receita",
                            mode="lines", stackgroup="one",
                            customdata=df_perf["receita_hover"], hovertemplate="Mês: %{x}<br>Receita: %{customdata}<extra></extra>",
                            line=dict(color="#0077b6", width=2),
                        )
                    )
                    fig_tend.add_trace(
                        go.Scatter(
                            x=df_perf["mes_label"], y=df_perf["custos"], name="Custos",
                            mode="lines", stackgroup="two",
                            customdata=df_perf["custos_hover"], hovertemplate="Mês: %{x}<br>Custos: %{customdata}<extra></extra>",
                            line=dict(color="#f4a261", width=2),
                        )
                    )
                    fig_tend.add_trace(
                        go.Scatter(
                            x=df_perf["mes_label"], y=df_perf["lucro"], name="Lucro",
                            mode="lines", stackgroup="three",
                            customdata=df_perf["lucro_hover"], hovertemplate="Mês: %{x}<br>Lucro: %{customdata}<extra></extra>",
                            line=dict(color="#2a9d8f", width=2),
                        )
                    )
                else:
                    fig_tend.add_trace(
                        go.Bar(
                            x=df_perf["mes_label"],
                            y=df_perf["receita"],
                            name="Receita",
                            marker_color="#0077b6",
                            text=df_perf["receita_txt"],
                            textposition="outside",
                            customdata=df_perf["receita_hover"],
                            hovertemplate="Mês: %{x}<br>Receita: %{customdata}<extra></extra>",
                        )
                    )
                    fig_tend.add_trace(
                        go.Bar(
                            x=df_perf["mes_label"],
                            y=df_perf["custos"],
                            name="Custos",
                            marker_color="#f4a261",
                            text=df_perf["custos_txt"],
                            textposition="outside",
                            customdata=df_perf["custos_hover"],
                            hovertemplate="Mês: %{x}<br>Custos: %{customdata}<extra></extra>",
                        )
                    )
                    fig_tend.add_trace(
                        go.Scatter(
                            x=df_perf["mes_label"],
                            y=df_perf["lucro"],
                            name="Lucro",
                            mode="lines+markers+text",
                            text=df_perf["lucro_txt"],
                            textposition="top center",
                            customdata=df_perf["lucro_hover"],
                            hovertemplate="Mês: %{x}<br>Lucro: %{customdata}<extra></extra>",
                            line=dict(color="#2a9d8f", width=3),
                            marker=dict(size=8),
                        )
                    )
                fig_tend.update_layout(
                    title="Evolução Mensal de Receita, Custos e Lucro",
                    template="plotly_white",
                    barmode="group" if tipo_grafico_perf in ["Combinado", "Barras"] else None,
                    legend=dict(orientation="h", y=1.1, x=0),
                    margin=dict(l=10, r=10, t=70, b=10),
                    yaxis_title="Valor (R$)",
                    hovermode="x unified",
                    height=430,
                )
                st.plotly_chart(fig_tend, use_container_width=True)

                media_receita = float(df_perf["receita"].mean()) if not df_perf.empty else 0.0
                media_custos = float(df_perf["custos"].mean()) if not df_perf.empty else 0.0
                media_lucro = float(df_perf["lucro"].mean()) if not df_perf.empty else 0.0
                media_margem = float(df_perf["margem_pct"].mean()) if not df_perf.empty else 0.0
                r1, r2, r3, r4 = st.columns(4)
                r1.metric("Média Receita/Mês", brl(media_receita))
                r2.metric("Média Custos/Mês", brl(media_custos))
                r3.metric("Média Lucro/Mês", brl(media_lucro))
                r4.metric("Média Margem", f"{media_margem:.2f}%")

                st.markdown("**Resumo Mensal (valores exatos)**")
                st.dataframe(
                    df_perf[["mes_label", "receita", "custos", "lucro", "margem_pct"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "mes_label": st.column_config.TextColumn("Mês"),
                        "receita": st.column_config.NumberColumn("Receita", format="R$ %.2f"),
                        "custos": st.column_config.NumberColumn("Custos", format="R$ %.2f"),
                        "lucro": st.column_config.NumberColumn("Lucro", format="R$ %.2f"),
                        "margem_pct": st.column_config.NumberColumn("Margem (%)", format="%.2f%%"),
                    },
                )

        with t2:
            custos_labels = ["Diesel", "Arla", "Pedágio", "Gasto Extra", "Imposto", "Seguro Vida Motorista", "Rastreador"]
            custos_vals = [
                float(df_dash["custo_diesel"].sum()),
                float(df_dash["custo_arla"].sum()),
                float(df_dash["custo_pedagio"].sum()),
                float(df_dash["custo_extra"].sum()),
                float(df_dash["custo_imposto"].sum()),
                float(df_dash["custo_seguro_vida_motorista_rateado"].sum()),
                float(df_dash["custo_rastreador_rateado"].sum()),
            ]
            fig_custos = go.Figure(
                data=[
                    go.Pie(
                        labels=custos_labels,
                        values=custos_vals,
                        hole=0.56,
                        marker=dict(colors=["#e76f51", "#ffb703", "#219ebc", "#6c757d", "#2a9d8f"]),
                        textinfo="label+percent",
                    )
                ]
            )
            fig_custos.update_layout(
                title="Composição dos Custos Operacionais",
                template="plotly_white",
                margin=dict(l=10, r=10, t=60, b=10),
                height=390,
            )
            st.plotly_chart(fig_custos, use_container_width=True)

        with t3:
            if top_rotas.empty:
                st.info("Ainda não há dados de rota suficientes para comparação.")
            else:
                fig_rotas = go.Figure()
                fig_rotas.add_trace(
                    go.Bar(
                        x=top_rotas["receita"],
                        y=top_rotas["rota"],
                        orientation="h",
                        marker=dict(color="#00a6a6"),
                        name="Receita",
                    )
                )
                fig_rotas.add_trace(
                    go.Scatter(
                        x=top_rotas["lucro"],
                        y=top_rotas["rota"],
                        mode="markers",
                        marker=dict(color="#0b3c5d", size=11),
                        name="Lucro",
                    )
                )
                fig_rotas.update_layout(
                    title="Top Rotas por Receita e Ponto de Lucro",
                    template="plotly_white",
                    xaxis_title="R$",
                    yaxis_title="",
                    margin=dict(l=10, r=10, t=60, b=10),
                    height=390,
                )
                st.plotly_chart(fig_rotas, use_container_width=True)

# Abas 1 a 12 permanecem iguais
with aba1:
    if "msg_frete" in st.session_state:
        st.success(st.session_state.pop("msg_frete"))

    if not lista_cidades: st.warning("Cadastre cidades primeiro.")
    elif not lista_veiculos_full: st.warning("Cadastre um veículo primeiro.")
    else:
        col_sel1, col_sel2, col_sel3 = st.columns(3); o_v = col_sel1.selectbox("Origem", lista_cidades, index=None, placeholder="Origem"); d_dest = col_sel2.selectbox("Destino", lista_cidades, index=None, placeholder="Destino"); veic_sel = col_sel3.selectbox("Veículo", lista_veiculos_full, index=None, placeholder="Veículo")
        km_sug, vt_sug, vk_sug, trecho_existe = 0.0, 0.0, 0.0, False
        if o_v and d_dest:
            with conn() as c:
                rota = c.execute("SELECT km, valor_ton, valor_km FROM rotas WHERE (origem=? AND destino=?) OR (origem=? AND destino=?)", (o_v, d_dest, d_dest, o_v)).fetchone()
                if rota: km_sug, vt_sug, vk_sug, trecho_existe = rota['km'], rota['valor_ton'], rota['valor_km'], True
            if not trecho_existe:
                st.warning("Rota Não cadastrada")
        ca, cb, cc, cd = st.columns(4)
        d_v = ca.date_input("Data carregamento", format="DD/MM/YYYY")
        hora_carregamento_v = cb.text_input("Hora carregamento", placeholder="HH:MM")
        cl_v = cc.text_input("Cliente", value="CONTATTO")
        nf_v = cd.text_input("N.NF")
        ct2, ct3, ct4, ct5 = st.columns(4)
        data_chegada_tmp_v = ct2.date_input("Data Chegada Descarregamento", value=None, format="DD/MM/YYYY", key="cad_data_chegada")
        deixar_data_chegada_em_branco_v = ct2.checkbox("Salvar data chegada em branco", value=True, key="cad_data_chegada_em_branco")
        if deixar_data_chegada_em_branco_v:
            data_chegada_v = None
            ct2.caption("Você pode preencher depois na edição.")
        else:
            data_chegada_v = data_chegada_tmp_v
        hora_chegada_v = ct3.text_input("Hora Chegada Descarregamento", placeholder="HH:MM")
        data_descarregamento_tmp_v = ct4.date_input("Data descarregamento", value=None, format="DD/MM/YYYY", key="cad_data_descarregamento")
        deixar_data_descarregamento_em_branco_v = ct4.checkbox("Salvar data descarregamento em branco", value=True, key="cad_data_descarregamento_em_branco")
        if deixar_data_descarregamento_em_branco_v:
            data_descarregamento_v = None
            ct4.caption("Você pode preencher depois na edição.")
        else:
            data_descarregamento_v = data_descarregamento_tmp_v
        hora_descarregamento_tmp_v = ct5.text_input("Hora descarregamento", placeholder="HH:MM")
        deixar_hora_descarregamento_em_branco_v = ct5.checkbox("Salvar hora descarregamento em branco", value=True, key="cad_hora_descarregamento_em_branco")
        hora_descarregamento_v = "" if deixar_hora_descarregamento_em_branco_v else hora_descarregamento_tmp_v
        c0, c1, c2, c3, c4, c5, c6, c7, c8, c9, c10 = st.columns(11)
        tipo_cobranca_v = c0.selectbox("Tipo Cálculo", ["TONELADA", "KM"], key="cad_tipo_calc")
        modo_ton = (tipo_cobranca_v == "TONELADA")
        km_v = c1.number_input("KM", value=km_sug, disabled=True)
        kg_txt = c2.text_input("KG", key="cad_frete_kg", disabled=not modo_ton)
        components.html(
            """
            <script>
            (function () {
              const doc = window.parent.document;
              const input = doc.querySelector('input[aria-label="KG"]');
              if (!input || input.dataset.kgMaskAttached === "1") return;
              input.dataset.kgMaskAttached = "1";
              input.dataset.kgMaskUpdating = "0";

              const proto = window.parent.HTMLInputElement.prototype;
              const valueSetter = Object.getOwnPropertyDescriptor(proto, "value").set;

              function formatKg(value) {
                const digits = (value || "").replace(/\\D/g, "");
                if (!digits) return "";
                return Number(digits).toLocaleString("pt-BR");
              }

              input.addEventListener("input", function () {
                if (input.dataset.kgMaskUpdating === "1") return;
                const formatted = formatKg(input.value);
                if (formatted === input.value) return;

                input.dataset.kgMaskUpdating = "1";
                valueSetter.call(input, formatted);
                input.dispatchEvent(new Event("input", { bubbles: true }));
                input.dataset.kgMaskUpdating = "0";
              });
            })();
            </script>
            """,
            height=0,
        )
        # Valor/Tonelada vem da aba de rotas; aqui fica apenas para consulta.
        vt_v = c3.number_input("Val Ton", value=vt_sug, disabled=True)
        vk_v = c4.number_input("Val KM", value=vk_sug, step=0.01, disabled=modo_ton)
        pd_v = c5.number_input("Pedágio", value=0.0)
        qtd_pedagio_volta_padrao = 0
        if o_v and d_dest:
            rota_pp_ref = f"{str(o_v).strip()} → {str(d_dest).strip()}"
            with conn() as c:
                qtd_row_pp = c.execute(
                    """SELECT COUNT(*) AS qtd
                       FROM praca_pedagio
                       WHERE TRIM(COALESCE(rota, '')) = ?
                         AND UPPER(TRIM(COALESCE(sentido_viagem, ''))) = 'VOLTA'""",
                    (rota_pp_ref,),
                ).fetchone()
            qtd_pedagio_volta_padrao = int(qtd_row_pp["qtd"] if qtd_row_pp else 0)
        qtd_pedagio_v = c6.number_input("Qtde Pedágio", min_value=0, value=int(qtd_pedagio_volta_padrao), step=1)
        vaf_v = c7.number_input("Vl Adic Frete", min_value=0.0, value=0.0, step=0.01)
        desc_vaf_v = c8.text_input("Desc Vl Adic Frete")
        gx_v = c9.number_input("Gasto Extra", min_value=0.0, value=0.0, step=0.01)
        est_v = c10.number_input("Pagto Estadia", min_value=0.0, value=0.0, step=0.01)
        desc_gx_v = st.text_input("Descrição Gasto Extra")
        cf1, cf2, cf3, cf4 = st.columns(4)
        di_v = cf1.number_input("VL/L/Diesel", value=v_diesel_sug)
        cons_v = cf2.number_input("Gasto Diesel P/KM", value=v_cons_sug)
        arla_v = cf3.number_input("VL/Litro Arla", min_value=0.0, value=v_arla_sug, step=0.01)
        cons_arla_v = cf4.number_input("Gasto Arla P/KM", min_value=0.0, value=v_cons_arla_sug, step=0.01)

        kg_digitos = "".join(ch for ch in str(kg_txt or "") if ch.isdigit())
        kg_v = float(kg_digitos) if kg_digitos else 0.0
        tn_v_calc = (kg_v / 1000.0) if modo_ton else 0.0
        total_frete_prev = ((km_v * vk_v) if not modo_ton else (tn_v_calc * vt_v)) + vaf_v + est_v
        st.info(f"Total Frete Previsto ({tipo_cobranca_v}): {brl(total_frete_prev)}")
        if modo_ton:
            st.caption("Tipo TONELADA: informe apenas o KG. O Valor/Ton é lido da rota cadastrada.")
        else:
            st.caption("Tipo KM: informe apenas o Valor KM.")

        if st.button("💾 Gravar", key="btn_salvar_frete"):
            condicao_valor = (vk_v > 0) if not modo_ton else (kg_v > 0 and vt_v > 0)
            if trecho_existe and o_v and d_dest and veic_sel and condicao_valor:
                placa = veic_sel.split(" - ")[0]
                with conn() as c:
                    c.execute(
                        """INSERT INTO viagens
                           (data, cliente, origem, destino, km, toneladas, valor_ton, valor_km, tipo_cobranca, pedagio, qtd_pedagio, gasto_extra, pagto_estadia, valor_adicional_frete, descricao_valor_adicional_frete, descricao_gasto_extra, diesel, consumo, arla, consumo_arla, hora_carregamento, data_chegada, hora_chegada, data_descarregamento, hora_descarregamento, nf, veiculo_placa, qtd_viagens)
                           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            d_v.isoformat(),
                            cl_v,
                            o_v,
                            d_dest,
                            km_v,
                            tn_v_calc if modo_ton else 0.0,
                            vt_v if modo_ton else 0.0,
                            vk_v if not modo_ton else 0.0,
                            tipo_cobranca_v,
                            pd_v,
                            int(qtd_pedagio_v or 0),
                            gx_v,
                            est_v,
                            vaf_v,
                            desc_vaf_v.strip(),
                            desc_gx_v.strip(),
                            di_v,
                            cons_v,
                            arla_v,
                            cons_arla_v,
                            str(hora_carregamento_v or "").strip(),
                            data_chegada_v.isoformat() if data_chegada_v else None,
                            str(hora_chegada_v or "").strip(),
                            data_descarregamento_v.isoformat() if data_descarregamento_v else None,
                            str(hora_descarregamento_v or "").strip(),
                            nf_v,
                            placa,
                            1,
                        ),
                    )
                limpar_cache_viagens()
                st.session_state.msg_frete = "✅ Gravado com sucesso!"
                st.rerun()
            else:
                st.warning("Preencha os valores obrigatórios da modalidade selecionada para salvar.")

with aba2:
    if not df_db.empty:
        df_rotas_ref_exec = _carregar_rotas_ref_exec_raw()
        mapa_valor_ton_exec = {}
        if not df_rotas_ref_exec.empty:
            df_rotas_ref_exec["origem"] = df_rotas_ref_exec["origem"].fillna("").astype(str).str.strip().str.upper()
            df_rotas_ref_exec["destino"] = df_rotas_ref_exec["destino"].fillna("").astype(str).str.strip().str.upper()
            df_rotas_ref_exec["valor_ton"] = pd.to_numeric(df_rotas_ref_exec["valor_ton"], errors="coerce").fillna(0.0)
            mapa_valor_ton_exec = dict(
                zip(zip(df_rotas_ref_exec["origem"], df_rotas_ref_exec["destino"]), df_rotas_ref_exec["valor_ton"])
            )
            for (o, d), v in list(mapa_valor_ton_exec.items()):
                if (d, o) not in mapa_valor_ton_exec:
                    mapa_valor_ton_exec[(d, o)] = v

        def buscar_valor_ton_rota_exec(origem, destino):
            chave = (str(origem or "").strip().upper(), str(destino or "").strip().upper())
            return mapa_valor_ton_exec.get(chave)

        st.markdown(
            """
            <style>
            /* Melhor leitura e rolagem da grade do histórico */
            div[data-testid="stDataFrame"] {
                padding-bottom: 10px;
                font-size: 12px !important;
                --gdg-font-size: 12px;
            }
            div[data-testid="stDataFrame"] ::-webkit-scrollbar {
                height: 12px;
                width: 12px;
            }
            div[data-testid="stDataFrame"] ::-webkit-scrollbar-thumb {
                background: #b8b8b8;
                border-radius: 10px;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )

        # 1. Criar uma cópia para não afetar o original
        df_exibir = df_db.copy()
        
        # 2. GARANTIR QUE AS COLUNAS EXISTAM (Evita o KeyError)
        if "qtd_viagens" not in df_exibir.columns:
            df_exibir["qtd_viagens"] = 1  # Se não existir no banco, assume 1 para todos
        if "tipo_cobranca" not in df_exibir.columns:
            df_exibir["tipo_cobranca"] = "TONELADA"
        if "valor_km" not in df_exibir.columns:
            df_exibir["valor_km"] = 0.0
        if "valor_ton" not in df_exibir.columns:
            df_exibir["valor_ton"] = 0.0
        if "diesel" not in df_exibir.columns:
            df_exibir["diesel"] = 0.0
        if "consumo" not in df_exibir.columns:
            df_exibir["consumo"] = 0.0
        if "arla" not in df_exibir.columns:
            df_exibir["arla"] = 0.0
        if "consumo_arla" not in df_exibir.columns:
            df_exibir["consumo_arla"] = 0.0
        if "gasto_extra" not in df_exibir.columns:
            df_exibir["gasto_extra"] = 0.0
        if "pagto_estadia" not in df_exibir.columns:
            df_exibir["pagto_estadia"] = 0.0
        if "valor_adicional_frete" not in df_exibir.columns:
            df_exibir["valor_adicional_frete"] = 0.0
        if "descricao_valor_adicional_frete" not in df_exibir.columns:
            df_exibir["descricao_valor_adicional_frete"] = ""
        if "qtd_pedagio" not in df_exibir.columns:
            df_exibir["qtd_pedagio"] = 0
        if "descricao_gasto_extra" not in df_exibir.columns:
            df_exibir["descricao_gasto_extra"] = ""
        if "hora_carregamento" not in df_exibir.columns:
            df_exibir["hora_carregamento"] = ""
        if "data_chegada" not in df_exibir.columns:
            df_exibir["data_chegada"] = ""
        if "hora_chegada" not in df_exibir.columns:
            df_exibir["hora_chegada"] = ""
        if "data_descarregamento" not in df_exibir.columns:
            df_exibir["data_descarregamento"] = ""
        if "hora_descarregamento" not in df_exibir.columns:
            df_exibir["hora_descarregamento"] = ""

        df_exibir["diesel"] = pd.to_numeric(df_exibir["diesel"], errors="coerce").fillna(0.0)
        df_exibir["consumo"] = pd.to_numeric(df_exibir["consumo"], errors="coerce").fillna(0.0)
        df_exibir["arla"] = pd.to_numeric(df_exibir["arla"], errors="coerce").fillna(0.0)
        df_exibir["consumo_arla"] = pd.to_numeric(df_exibir["consumo_arla"], errors="coerce").fillna(0.0)
        df_exibir["toneladas"] = pd.to_numeric(df_exibir["toneladas"], errors="coerce").fillna(0.0)
        df_exibir["valor_ton"] = pd.to_numeric(df_exibir["valor_ton"], errors="coerce").fillna(0.0)
        df_exibir["km"] = pd.to_numeric(df_exibir["km"], errors="coerce").fillna(0.0)
        df_exibir["valor_km"] = pd.to_numeric(df_exibir["valor_km"], errors="coerce").fillna(0.0)
        df_exibir["pedagio"] = pd.to_numeric(df_exibir["pedagio"], errors="coerce").fillna(0.0)
        df_exibir["qtd_pedagio"] = pd.to_numeric(df_exibir["qtd_pedagio"], errors="coerce").fillna(0).astype(int)
        df_exibir["qtd_viagens"] = pd.to_numeric(df_exibir["qtd_viagens"], errors="coerce").fillna(1.0)
        df_exibir["qtd_viagens"] = df_exibir["qtd_viagens"].apply(lambda x: max(1, int(round(float(x)))))
        df_exibir["gasto_extra"] = pd.to_numeric(df_exibir["gasto_extra"], errors="coerce").fillna(0.0)
        df_exibir["pagto_estadia"] = pd.to_numeric(df_exibir["pagto_estadia"], errors="coerce").fillna(0.0)
        df_exibir["valor_adicional_frete"] = pd.to_numeric(df_exibir["valor_adicional_frete"], errors="coerce").fillna(0.0)
        df_exibir["descricao_valor_adicional_frete"] = df_exibir["descricao_valor_adicional_frete"].fillna("").astype(str)
        df_exibir["descricao_gasto_extra"] = df_exibir["descricao_gasto_extra"].fillna("").astype(str)
        df_exibir["tipo_cobranca"] = df_exibir["tipo_cobranca"].astype(str).str.upper().str.strip()
        df_exibir["origem"] = df_exibir["origem"].fillna("").astype(str).str.strip()
        df_exibir["destino"] = df_exibir["destino"].fillna("").astype(str).str.strip()

        df_exibir["valor_ton_rota"] = [
            buscar_valor_ton_rota_exec(o, d) for o, d in zip(df_exibir["origem"], df_exibir["destino"])
        ]

        frete_km_exec = df_exibir["km"] * df_exibir["valor_km"]
        frete_ton_exec = df_exibir["toneladas"] * df_exibir["valor_ton"]
        df_exibir["Total Frete"] = frete_ton_exec.where(df_exibir["tipo_cobranca"] != "KM", frete_km_exec)
        df_exibir["Litros Diesel"] = (
            (df_exibir["km"] * df_exibir["qtd_viagens"])
            / df_exibir["consumo"].where(df_exibir["consumo"] > 0)
        ).fillna(0.0)
        df_exibir["Diesel/KM"] = (df_exibir["Litros Diesel"] * df_exibir["diesel"]).fillna(0.0)
        df_exibir["Litros Arla"] = (
            (df_exibir["km"] * df_exibir["qtd_viagens"])
            / df_exibir["consumo_arla"].where(df_exibir["consumo_arla"] > 0)
        ).fillna(0.0)
        df_exibir["Gasto Arla"] = (df_exibir["Litros Arla"] * df_exibir["arla"]).fillna(0.0)
        df_exibir["peso_kg"] = (pd.to_numeric(df_exibir["toneladas"], errors="coerce").fillna(0.0) * 1000.0)
        df_exibir["qtd_estadia_calc"] = df_exibir.apply(
            lambda rr: calcular_qtd_estadia(
                rr.get("data_chegada"),
                rr.get("hora_chegada"),
                rr.get("data_descarregamento"),
                rr.get("hora_descarregamento"),
            ),
            axis=1,
        )

        # Mantém valor numérico para cálculos e exibe máscara BRL no grid.
        df_exibir["Receita Comissionável"] = (
            pd.to_numeric(df_exibir["Total Frete"], errors="coerce").fillna(0.0)
            + pd.to_numeric(df_exibir["pagto_estadia"], errors="coerce").fillna(0.0)
        )
        df_exibir["Total Frete Valor"] = (
            (
                df_exibir["Receita Comissionável"]
                + pd.to_numeric(df_exibir["valor_adicional_frete"], errors="coerce").fillna(0.0)
            )
            * df_exibir["qtd_viagens"]
        )
        df_exibir["Frete Líquido"] = (
            df_exibir["Total Frete Valor"]
            - df_exibir["Diesel/KM"]
            - df_exibir["Gasto Arla"]
            - (df_exibir["pedagio"] * df_exibir["qtd_viagens"])
            - (df_exibir["gasto_extra"] * df_exibir["qtd_viagens"])
        ).fillna(0.0)
        df_exibir["Total Frete"] = df_exibir["Total Frete Valor"].apply(brl)
        
        # Criar colunas de ação da grade
        df_exibir["Editar"] = False
        df_exibir["Excluir"] = False 

        # Filtros da aba Viagens Executadas
        origens_disponiveis = sorted([o for o in df_exibir["origem"].dropna().unique().tolist() if str(o).strip()])
        destinos_disponiveis = sorted([d for d in df_exibir["destino"].dropna().unique().tolist() if str(d).strip()])
        opcoes_origem = ["Todas as origens"] + origens_disponiveis
        opcoes_destino = ["Todos os destinos"] + destinos_disponiveis
        opcoes_estadia = ["Todos", "Com Estadia", "Sem Estadia"]

        if "filtro_origem_exec" not in st.session_state or st.session_state.filtro_origem_exec not in opcoes_origem:
            st.session_state.filtro_origem_exec = "Todas as origens"
        if "filtro_destino_exec" not in st.session_state or st.session_state.filtro_destino_exec not in opcoes_destino:
            st.session_state.filtro_destino_exec = "Todos os destinos"
        if "filtro_estadia_exec" not in st.session_state or st.session_state.filtro_estadia_exec not in opcoes_estadia:
            st.session_state.filtro_estadia_exec = "Todos"

        f_exec_1, f_exec_2, f_exec_3 = st.columns(3)
        f_exec_1.selectbox("Filtro Origem", options=opcoes_origem, key="filtro_origem_exec")
        f_exec_2.selectbox("Filtro Destino", options=opcoes_destino, key="filtro_destino_exec")
        f_exec_3.selectbox("Filtro Estadia", options=opcoes_estadia, key="filtro_estadia_exec")

        if st.session_state.filtro_origem_exec != "Todas as origens":
            df_exibir = df_exibir[df_exibir["origem"] == st.session_state.filtro_origem_exec].copy()
        if st.session_state.filtro_destino_exec != "Todos os destinos":
            df_exibir = df_exibir[df_exibir["destino"] == st.session_state.filtro_destino_exec].copy()
        if st.session_state.filtro_estadia_exec == "Com Estadia":
            df_exibir = df_exibir[pd.to_numeric(df_exibir["qtd_estadia_calc"], errors="coerce").fillna(0).astype(int) > 0].copy()
        elif st.session_state.filtro_estadia_exec == "Sem Estadia":
            df_exibir = df_exibir[pd.to_numeric(df_exibir["qtd_estadia_calc"], errors="coerce").fillna(0).astype(int) <= 0].copy()

        # 3. CÁLCULOS DOS TOTAIS
        tot_km = float((df_exibir["km"] * df_exibir["qtd_viagens"]).sum())
        tot_frete = float(df_exibir["Total Frete Valor"].sum())
        tot_pedagio = float((df_exibir["pedagio"] * df_exibir["qtd_viagens"]).sum())
        tot_extra = float((df_exibir["gasto_extra"] * df_exibir["qtd_viagens"]).sum())
        tot_estadia = float((df_exibir["pagto_estadia"] * df_exibir["qtd_viagens"]).sum())
        tot_litros_diesel = float(df_exibir["Litros Diesel"].sum())
        tot_valor_diesel = float(df_exibir["Diesel/KM"].sum())
        tot_litros_arla = float(df_exibir["Litros Arla"].sum())
        tot_valor_arla = float(df_exibir["Gasto Arla"].sum())
        tot_viagens = int(df_exibir["qtd_viagens"].sum())
        tot_qtd_estadias_calc = int(pd.to_numeric(df_exibir["qtd_estadia_calc"], errors="coerce").fillna(0).sum())
        datas_exec = pd.to_datetime(df_exibir["data"], errors="coerce").dropna()
        if datas_exec.empty:
            dias_gastos = 0
        else:
            data_min_exec = datas_exec.min().date()
            data_max_exec = datas_exec.max().date()
            dias_gastos = (data_max_exec - data_min_exec).days + 1
        tot_faturamento_liquido_periodo = float(df_exibir["Frete Líquido"].sum())
        valor_km_rodado_bruto = (tot_frete / tot_km) if tot_km > 0 else 0.0
        valor_km_rodado_liquido = (tot_faturamento_liquido_periodo / tot_km) if tot_km > 0 else 0.0

        # EXIBIÇÃO DAS MÉTRICAS
        st.markdown(
            """
            <style>
            /* Evita corte dos cabeçalhos das métricas na aba Viagens Executadas */
            @media (max-width: 1800px) {
                div[data-testid="stMetricLabel"] p {
                    font-size: 0.78rem !important;
                }
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
        m1, m2, m3, m4, m5, m6 = st.columns(6)
        m1.metric("Total KM", format_br(tot_km, casas_decimais=0))
        m2.metric("Total Frete", brl(tot_frete))
        m3.metric("Total Pedágio", brl(tot_pedagio))
        m4.metric("Total Gasto Extra", brl(tot_extra))
        m5.metric("Total Pagto Estadia", brl(tot_estadia))
        m6.metric("Total Litros Diesel", f"{tot_litros_diesel:.2f} L")

        m7, m8, m9, m10, m11, m12 = st.columns(6)
        m7.metric("Total Gasto Diesel", brl(tot_valor_diesel))
        m8.metric("Qtd. Viagens", tot_viagens)
        m9.metric("Total Litros Arla", f"{tot_litros_arla:.2f} L")
        m10.metric("Total Gasto Arla", brl(tot_valor_arla))
        m11.metric("Faturamento Liquido", brl(tot_faturamento_liquido_periodo))
        m12.metric("Dias Gastos", dias_gastos)
        m13, m14, m15, _, _, _ = st.columns(6)
        m13.metric("Total Estadia no Período", int(tot_qtd_estadias_calc))
        m14.metric(
            "Valor KM Rodado Bruto",
            (f"R$ {valor_km_rodado_bruto:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".") + "/KM") if tot_km > 0 else "-",
        )
        m15.metric(
            "Valor KM Rodado Líquido",
            (f"R$ {valor_km_rodado_liquido:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".") + "/KM") if tot_km > 0 else "-",
        )

        st.markdown("---")
        tab_grid_exec, tab_lucro_exec = st.tabs(["📋 Grid de Viagens", "📈 Lucro e Viabilidade"])

        with tab_grid_exec:
            colunas_tab = ["id", "data", "veiculo_placa", "nf", "cliente", "origem", "destino", "tipo_cobranca", "km", "peso_kg", "valor_ton", "valor_km", "diesel", "consumo", "arla", "consumo_arla", "Diesel/KM", "Gasto Arla", "Total Frete Valor", "Total Frete", "Frete Líquido", "pedagio", "qtd_pedagio", "gasto_extra", "pagto_estadia", "valor_adicional_frete", "descricao_valor_adicional_frete", "descricao_gasto_extra", "hora_carregamento", "data_chegada", "hora_chegada", "data_descarregamento", "hora_descarregamento", "qtd_estadia_calc", "qtd_viagens"]
            df_ed = df_exibir[colunas_tab].copy()
            colunas_duas_casas_exec = [
                "diesel",
                "consumo",
                "arla",
                "consumo_arla",
                "Diesel/KM",
                "Gasto Arla",
                "Total Frete Valor",
                "Total Frete",
                "Frete Líquido",
            ]
            for col_fmt in colunas_duas_casas_exec:
                if col_fmt in df_ed.columns:
                    df_ed[col_fmt] = df_ed[col_fmt].apply(lambda v: format_br(v, casas_decimais=2))

            st.dataframe(
                df_ed.rename(
                    columns={
                        "data": "Data",
                        "veiculo_placa": "Placa",
                        "nf": "NF",
                        "cliente": "Cliente",
                        "origem": "Origem",
                        "destino": "Destino",
                        "tipo_cobranca": "Tipo Cobrança",
                        "km": "KM",
                        "peso_kg": "KG",
                        "valor_ton": "Val Ton",
                        "valor_km": "Val KM",
                        "diesel": "VL/L/Diesel",
                        "consumo": "Gasto Diesel P/KM",
                        "arla": "VL/Litro Arla",
                        "consumo_arla": "Gasto Arla P/KM",
                        "pedagio": "Pedágio",
                        "qtd_pedagio": "Qtd Pedágio",
                        "gasto_extra": "Gasto Extra",
                        "pagto_estadia": "Pagto Estadia",
                        "valor_adicional_frete": "Adicional Frete",
                        "descricao_valor_adicional_frete": "Descrição Adicional Frete",
                        "descricao_gasto_extra": "Descrição Gasto Extra",
                        "hora_carregamento": "Hora Carregamento",
                        "data_chegada": "Data Chegada",
                        "hora_chegada": "Hora Chegada",
                        "data_descarregamento": "Data Descarregamento",
                        "hora_descarregamento": "Hora Descarregamento",
                        "qtd_estadia_calc": "Qtd Estadia (Calc)",
                        "qtd_viagens": "Qtd",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                height=470,
            )

            if "viagens_exec_id_editando" not in st.session_state:
                st.session_state.viagens_exec_id_editando = None

            df_sel_exec = df_exibir.copy()
            df_sel_exec["rotulo_edicao"] = (
                "ID "
                + df_sel_exec["id"].astype(str)
                + " | "
                + pd.to_datetime(df_sel_exec["data"], errors="coerce").dt.strftime("%d/%m/%Y").fillna("-")
                + " | "
                + df_sel_exec["origem"].fillna("").astype(str)
                + " → "
                + df_sel_exec["destino"].fillna("").astype(str)
                + " | NF "
                + df_sel_exec["nf"].fillna("").astype(str)
            )
            id_viagem_sel = st.selectbox(
                "Escolha uma viagem para editar",
                options=df_sel_exec["id"].tolist(),
                format_func=lambda x: df_sel_exec.loc[df_sel_exec["id"] == x, "rotulo_edicao"].iloc[0],
                key="viagem_exec_select_editar",
            )
            c_v1, c_v2 = st.columns(2)
            if c_v1.button("✏️ Editar Registro", key="btn_viagem_exec_abrir_form", use_container_width=True):
                st.session_state.viagens_exec_id_editando = int(id_viagem_sel)
                st.rerun()
            if c_v2.button("❌ Cancelar Edição", key="btn_viagem_exec_cancelar_form", use_container_width=True):
                st.session_state.viagens_exec_id_editando = None
                st.rerun()

            if st.session_state.viagens_exec_id_editando is not None:
                reg_sel = df_exibir[df_exibir["id"] == st.session_state.viagens_exec_id_editando]
                if reg_sel.empty:
                    st.session_state.viagens_exec_id_editando = None
                    st.warning("Registro selecionado não foi encontrado.")
                else:
                    r = reg_sel.iloc[0]
                    st.markdown("### ✏️ Editar Viagem Executada")
                    with st.form("form_edicao_viagem_exec"):
                        ce1, ce2, ce3, ce4 = st.columns(4)
                        data_ed = ce1.date_input("Data carregamento", value=pd.to_datetime(r["data"], errors="coerce").date(), format="DD/MM/YYYY")
                        hora_carregamento_ed = ce2.text_input("Hora carregamento", value=str(r.get("hora_carregamento", "") or ""), placeholder="HH:MM")
                        cliente_ed = ce3.text_input("Cliente", value=str(r["cliente"] or ""))
                        nf_ed = ce4.text_input("N.NF", value=str(r["nf"] or ""))
                        ce_data1, ce_data2, ce_data3, ce_data4 = st.columns(4)
                        data_chegada_default = pd.to_datetime(r.get("data_chegada"), errors="coerce")
                        data_chegada_value = None if pd.isna(data_chegada_default) else data_chegada_default.date()
                        data_chegada_tmp_ed = ce_data1.date_input(
                            "Data Chegada Descarregamento",
                            value=data_chegada_value,
                            format="DD/MM/YYYY",
                            key=f"edit_data_chegada_{int(r['id'])}",
                        )
                        deixar_data_chegada_em_branco_ed = ce_data1.checkbox(
                            "Salvar em branco",
                            value=pd.isna(pd.to_datetime(r.get("data_chegada"), errors="coerce")),
                            key=f"edit_data_chegada_em_branco_{int(r['id'])}",
                        )
                        if deixar_data_chegada_em_branco_ed:
                            data_chegada_ed = None
                            ce_data1.caption("Data chegada ficará em branco.")
                        else:
                            data_chegada_ed = data_chegada_tmp_ed
                        hora_chegada_ed = ce_data2.text_input("Hora Chegada Descarregamento", value=str(r.get("hora_chegada", "") or ""), placeholder="HH:MM")
                        data_descarregamento_default = pd.to_datetime(r.get("data_descarregamento"), errors="coerce")
                        data_descarregamento_value = None if pd.isna(data_descarregamento_default) else data_descarregamento_default.date()
                        data_descarregamento_tmp_ed = ce_data3.date_input(
                            "Data descarregamento",
                            value=data_descarregamento_value,
                            format="DD/MM/YYYY",
                            key=f"edit_data_descarregamento_{int(r['id'])}",
                        )
                        deixar_data_descarregamento_em_branco_ed = ce_data3.checkbox(
                            "Salvar em branco",
                            value=pd.isna(pd.to_datetime(r.get("data_descarregamento"), errors="coerce")),
                            key=f"edit_data_descarregamento_em_branco_{int(r['id'])}",
                        )
                        if deixar_data_descarregamento_em_branco_ed:
                            data_descarregamento_ed = None
                            ce_data3.caption("Data descarregamento ficará em branco.")
                        else:
                            data_descarregamento_ed = data_descarregamento_tmp_ed
                        hora_descarregamento_tmp_ed = ce_data4.text_input("Hora descarregamento", value=str(r.get("hora_descarregamento", "") or ""), placeholder="HH:MM")
                        deixar_hora_descarregamento_em_branco_ed = ce_data4.checkbox(
                            "Salvar em branco",
                            value=not str(r.get("hora_descarregamento", "") or "").strip(),
                            key=f"edit_hora_descarregamento_em_branco_{int(r['id'])}",
                        )
                        hora_descarregamento_ed = "" if deixar_hora_descarregamento_em_branco_ed else hora_descarregamento_tmp_ed
                        qtd_estadia_form_calc = calcular_qtd_estadia(
                            data_chegada_ed,
                            hora_chegada_ed,
                            data_descarregamento_ed,
                            hora_descarregamento_ed,
                        )
                        st.number_input(
                            "Qtd Estadia (Calculada)",
                            min_value=0,
                            value=int(qtd_estadia_form_calc),
                            step=1,
                            disabled=True,
                        )
                        st.caption("Regra aplicada: as primeiras 24h da chegada não contam; depois, a estadia começa no próximo marco de 08:00 e soma 1 a cada novo dia às 08:00.")

                        ce4, ce5, ce6 = st.columns(3)
                        origem_atual_ed = str(r["origem"] or "").strip()
                        destino_atual_ed = str(r["destino"] or "").strip()
                        opcoes_origem_ed = list(lista_cidades) if isinstance(lista_cidades, list) else []
                        opcoes_destino_ed = list(lista_cidades) if isinstance(lista_cidades, list) else []
                        if origem_atual_ed and origem_atual_ed not in opcoes_origem_ed:
                            opcoes_origem_ed = [origem_atual_ed] + opcoes_origem_ed
                        if destino_atual_ed and destino_atual_ed not in opcoes_destino_ed:
                            opcoes_destino_ed = [destino_atual_ed] + opcoes_destino_ed
                        idx_origem_ed = opcoes_origem_ed.index(origem_atual_ed) if origem_atual_ed in opcoes_origem_ed else 0
                        idx_destino_ed = opcoes_destino_ed.index(destino_atual_ed) if destino_atual_ed in opcoes_destino_ed else 0
                        origem_ed = ce4.selectbox("Origem", options=opcoes_origem_ed, index=idx_origem_ed, key=f"exec_origem_ed_{int(r['id'])}")
                        destino_ed = ce5.selectbox("Destino", options=opcoes_destino_ed, index=idx_destino_ed, key=f"exec_destino_ed_{int(r['id'])}")
                        placa_atual_ed = str(r["veiculo_placa"] or "").strip()
                        opcoes_veiculo_ed = list(lista_veiculos_full) if isinstance(lista_veiculos_full, list) else []
                        rotulo_placa_manual = f"{placa_atual_ed} - (placa manual)" if placa_atual_ed else None
                        if rotulo_placa_manual and rotulo_placa_manual not in opcoes_veiculo_ed:
                            opcoes_veiculo_ed = [rotulo_placa_manual] + opcoes_veiculo_ed
                        idx_veiculo_ed = 0
                        for i_opt, opt_veic in enumerate(opcoes_veiculo_ed):
                            if str(opt_veic).split(" - ")[0].strip().upper() == placa_atual_ed.upper():
                                idx_veiculo_ed = i_opt
                                break
                        if opcoes_veiculo_ed:
                            veic_sel_ed = ce6.selectbox(
                                "Veículo",
                                options=opcoes_veiculo_ed,
                                index=idx_veiculo_ed,
                                key=f"exec_veic_ed_{int(r['id'])}",
                            )
                            placa_ed = str(veic_sel_ed).split(" - ")[0].strip()
                        else:
                            placa_ed = ce6.text_input("Placa", value=placa_atual_ed)

                        ce7, ce8, ce9, ce10 = st.columns(4)
                        tipo_cobranca_ed = ce7.selectbox("Tipo Cálculo", ["TONELADA", "KM"], index=0 if str(r["tipo_cobranca"]).upper() != "KM" else 1)
                        km_ed = ce8.number_input("KM", min_value=0.0, value=float(r["km"] or 0.0), step=1.0)
                        kg_ed = ce9.number_input("KG", min_value=0.0, value=float(r["peso_kg"] or 0.0), step=1.0)
                        qtd_viagens_ed = ce10.number_input("Qtd", min_value=1, value=int(r["qtd_viagens"] or 1), step=1)

                        ce11, ce12, ce13, ce14, ce15 = st.columns(5)
                        valor_ton_ed = ce11.number_input("Val Ton", min_value=0.0, value=float(r["valor_ton"] or 0.0), step=0.0001, format="%.4f")
                        valor_km_ed = ce12.number_input("Val KM", min_value=0.0, value=float(r["valor_km"] or 0.0), step=0.0001, format="%.4f")
                        pedagio_ed = ce13.number_input("Pedágio", min_value=0.0, value=float(r["pedagio"] or 0.0), step=0.01)
                        qtd_pedagio_ed = ce14.number_input("Qtd Pedágio", min_value=0, value=int(r.get("qtd_pedagio", 0) or 0), step=1)
                        gasto_extra_ed = ce15.number_input("Gasto Extra", min_value=0.0, value=float(r["gasto_extra"] or 0.0), step=0.01)

                        ce15, ce16, ce17 = st.columns(3)
                        pagto_estadia_ed = ce15.number_input("Pagto Estadia", min_value=0.0, value=float(r["pagto_estadia"] or 0.0), step=0.01)
                        diesel_ed = ce16.number_input("VL/L/Diesel", min_value=0.0, value=float(r["diesel"] or 0.0), step=0.01)
                        consumo_ed = ce17.number_input("Gasto Diesel P/KM", min_value=0.0, value=float(r["consumo"] or 0.0), step=0.01)

                        ce20, ce21 = st.columns(2)
                        valor_adic_frete_ed = ce20.number_input("Valor Adicional no Frete", min_value=0.0, value=float(r.get("valor_adicional_frete", 0.0) or 0.0), step=0.01)
                        desc_valor_adic_frete_ed = ce21.text_input("Descrição Valor Adicional no Frete", value=str(r.get("descricao_valor_adicional_frete", "") or ""))

                        ce18, ce19 = st.columns(2)
                        arla_ed = ce18.number_input("VL/Litro Arla", min_value=0.0, value=float(r["arla"] or 0.0), step=0.01)
                        consumo_arla_ed = ce19.number_input("Gasto Arla P/KM", min_value=0.0, value=float(r["consumo_arla"] or 0.0), step=0.01)
                        desc_gasto_extra_ed = st.text_input("Descrição Gasto Extra", value=str(r["descricao_gasto_extra"] or ""))

                        b1, b2 = st.columns(2)
                        btn_atualizar_viagem = b1.form_submit_button("💾 Atualizar", use_container_width=True, type="primary")
                        btn_excluir_viagem = b2.form_submit_button("🗑️ Excluir Registro", use_container_width=True)

                    if btn_atualizar_viagem:
                        if not origem_ed.strip() or not destino_ed.strip() or not placa_ed.strip():
                            st.warning("Preencha origem, destino e placa para atualizar.")
                        else:
                            tipo_sql = str(tipo_cobranca_ed).upper().strip()
                            toneladas_sql = (kg_ed / 1000.0) if tipo_sql == "TONELADA" else 0.0
                            valor_ton_sql = valor_ton_ed if tipo_sql == "TONELADA" else 0.0
                            valor_km_sql = valor_km_ed if tipo_sql == "KM" else 0.0
                            with conn() as c:
                                c.execute(
                                    """UPDATE viagens SET
                                       data=?, cliente=?, origem=?, destino=?, km=?, toneladas=?, valor_ton=?, valor_km=?, tipo_cobranca=?,
                                       pedagio=?, qtd_pedagio=?, gasto_extra=?, pagto_estadia=?, valor_adicional_frete=?, descricao_valor_adicional_frete=?, descricao_gasto_extra=?, diesel=?, consumo=?, arla=?, consumo_arla=?,
                                       hora_carregamento=?, data_chegada=?, hora_chegada=?, data_descarregamento=?, hora_descarregamento=?,
                                       nf=?, veiculo_placa=?, qtd_viagens=?
                                       WHERE id=?""",
                                    (
                                        data_ed.isoformat(),
                                        cliente_ed.strip(),
                                        origem_ed.strip(),
                                        destino_ed.strip(),
                                        km_ed,
                                        toneladas_sql,
                                        valor_ton_sql,
                                        valor_km_sql,
                                        tipo_sql,
                                        pedagio_ed,
                                        int(qtd_pedagio_ed or 0),
                                        gasto_extra_ed,
                                        pagto_estadia_ed,
                                        valor_adic_frete_ed,
                                        desc_valor_adic_frete_ed.strip(),
                                        desc_gasto_extra_ed.strip(),
                                        diesel_ed,
                                        consumo_ed,
                                        arla_ed,
                                        consumo_arla_ed,
                                        str(hora_carregamento_ed or "").strip(),
                                        data_chegada_ed.isoformat() if data_chegada_ed else None,
                                        str(hora_chegada_ed or "").strip(),
                                        data_descarregamento_ed.isoformat() if data_descarregamento_ed else None,
                                        str(hora_descarregamento_ed or "").strip(),
                                        nf_ed.strip(),
                                        placa_ed.strip(),
                                        int(qtd_viagens_ed),
                                        int(st.session_state.viagens_exec_id_editando),
                                    ),
                                )
                            limpar_cache_viagens()
                            alerta_gravado("✅ Viagem atualizada com sucesso!")
                            st.session_state.viagens_exec_id_editando = None
                            st.rerun()

                    if btn_excluir_viagem:
                        with conn() as c:
                            c.execute("DELETE FROM viagens WHERE id=?", (int(st.session_state.viagens_exec_id_editando),))
                        limpar_cache_viagens()
                        alerta_gravado("✅ Viagem excluída com sucesso!")
                        st.session_state.viagens_exec_id_editando = None
                        st.rerun()

            c_h3, c_h4 = st.columns(2)
            if c_h3.button("🖨️ Imprimir Estadias", use_container_width=True, key="btn_print_viagens_estadias"):
                with conn() as c:
                    df_rotas_print = pd.read_sql(
                        "SELECT origem, destino, nome_empresa_origem, nome_empresa_destino FROM rotas",
                        c,
                    )
                if "nome_empresa_origem" not in df_rotas_print.columns:
                    df_rotas_print["nome_empresa_origem"] = ""
                if "nome_empresa_destino" not in df_rotas_print.columns:
                    df_rotas_print["nome_empresa_destino"] = ""
                df_rotas_print["origem"] = df_rotas_print["origem"].fillna("").astype(str).str.strip().str.upper()
                df_rotas_print["destino"] = df_rotas_print["destino"].fillna("").astype(str).str.strip().str.upper()
                df_rotas_print["nome_empresa_origem"] = df_rotas_print["nome_empresa_origem"].fillna("").astype(str).str.strip()
                df_rotas_print["nome_empresa_destino"] = df_rotas_print["nome_empresa_destino"].fillna("").astype(str).str.strip()
                mapa_empresas_rota = {
                    (str(r["origem"]), str(r["destino"])): (
                        str(r["nome_empresa_origem"] or ""),
                        str(r["nome_empresa_destino"] or ""),
                    )
                    for _, r in df_rotas_print.iterrows()
                }
                for _, r in df_rotas_print.iterrows():
                    origem_rota = str(r["origem"])
                    destino_rota = str(r["destino"])
                    nome_origem_rota = str(r["nome_empresa_origem"] or "")
                    nome_destino_rota = str(r["nome_empresa_destino"] or "")
                    chave_rota = (origem_rota, destino_rota)
                    chave_rota_inversa = (destino_rota, origem_rota)
                    if nome_origem_rota or nome_destino_rota:
                        mapa_empresas_rota[chave_rota] = (nome_origem_rota, nome_destino_rota)
                        if chave_rota_inversa not in mapa_empresas_rota or not any(mapa_empresas_rota[chave_rota_inversa]):
                            mapa_empresas_rota[chave_rota_inversa] = (nome_destino_rota, nome_origem_rota)

                df_ed_estadias = df_ed[
                    pd.to_numeric(df_ed["qtd_estadia_calc"], errors="coerce").fillna(0).astype(int) > 0
                ].copy()
                tem_estadias_para_imprimir = not df_ed_estadias.empty
                if not tem_estadias_para_imprimir:
                    st.warning("Não há estadias com quantidade maior que zero para imprimir.")

                pendencias_estadia = []
                for _, r_pend in df_ed_estadias.iterrows():
                    origem_pend = str(r_pend.get("origem", "") or "").strip().upper()
                    destino_pend = str(r_pend.get("destino", "") or "").strip().upper()
                    nome_emp_origem_pend, nome_emp_destino_pend = mapa_empresas_rota.get((origem_pend, destino_pend), ("", ""))
                    campos_pendentes = []
                    if not nome_emp_origem_pend:
                        campos_pendentes.append("Nome Empresa Origem")
                    if not nome_emp_destino_pend:
                        campos_pendentes.append("Nome Empresa Destino")
                    if pd.isna(pd.to_datetime(r_pend.get("data_descarregamento"), errors="coerce")):
                        campos_pendentes.append("Data Descarregamento")
                    if not str(r_pend.get("hora_descarregamento", "") or "").strip():
                        campos_pendentes.append("Hora Descarregamento")
                    if campos_pendentes:
                        pendencias_estadia.append(
                            f"ID {int(r_pend.get('id', 0) or 0)} - {origem_pend or '-'} x {destino_pend or '-'}: {', '.join(campos_pendentes)}"
                        )
                if pendencias_estadia:
                    st.warning("Campos pendentes no relatório de estadias:\n" + "\n".join(pendencias_estadia))

                total_qtd_estadias_print = int(pd.to_numeric(df_ed_estadias["qtd_estadia_calc"], errors="coerce").fillna(0).sum())
                total_qtd_viagens_print = int(pd.to_numeric(df_ed_estadias["qtd_viagens"], errors="coerce").fillna(1).sum())
                html_estadias = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: sans-serif; margin: 24px; color: #333; }}
                        header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 10px; }}
                        th, td {{ border: 1px solid #999; padding: 5px; text-align: left; white-space: nowrap; }}
                        th {{ background-color: #f2f2f2; }}
                        .formula-estadia {{ margin-top: 14px; padding: 10px 12px; border: 1px solid #999; background: #fafafa; font-size: 12px; line-height: 1.45; }}
                        .formula-estadia strong {{ display: block; margin-bottom: 4px; }}
                        .resumo {{ margin-top: 14px; text-align: right; font-size: 15px; font-weight: bold; }}
                        .btn-print {{ background: #007bff; color: white; padding: 12px; border: none; width: 100%; cursor: pointer; font-weight: bold; font-size: 14px; border-radius: 5px; }}
                        @media print {{ .btn-print {{ display: none; }} body {{ margin: 0; }} }}
                    </style>
                </head>
                <body>
                    <button class="btn-print" onclick="window.print()">🖨️ IMPRIMIR ESTADIAS</button>
                    <header>
                        <h2 style="margin:0;">Relatório de Estadias</h2>
                        <p style="margin:6px 0;">Período: <b>{filtro_ini.strftime('%d/%m/%Y')}</b> até <b>{filtro_fim.strftime('%d/%m/%Y')}</b></p>
                    </header>
                    <div class="formula-estadia">
                        <strong>Como é feito o cálculo da Qtde Estadia</strong>
                        As primeiras 24 horas após a chegada não contam estadia.<br>
                        Depois dessas 24 horas, a contagem começa no próximo horário de 08:00.<br>
                        Qtde Estadia = 1 no primeiro marco válido de 08:00 e soma +1 a cada novo dia às 08:00.
                    </div>
                    <table>
                        <thead>
                            <tr>
                                <th>Nome Empresa Origem</th>
                                <th>Nome Empresa Destino</th>
                                <th>Origem</th>
                                <th>Destino</th>
                                <th>Data Chegada</th>
                                <th>Hora Chegada</th>
                                <th>Data Descarregamento</th>
                                <th>Hora Descarregamento</th>
                                <th>Qtde Estadia</th>
                            </tr>
                        </thead>
                        <tbody>
                """
                for _, r in df_ed_estadias.iterrows():
                    origem_key = str(r.get("origem", "") or "").strip().upper()
                    destino_key = str(r.get("destino", "") or "").strip().upper()
                    nome_empresa_origem, nome_empresa_destino = mapa_empresas_rota.get((origem_key, destino_key), ("", ""))
                    dt_chegada = pd.to_datetime(r.get("data_chegada"), errors="coerce")
                    dt_descarreg = pd.to_datetime(r.get("data_descarregamento"), errors="coerce")
                    data_chegada_br = dt_chegada.strftime("%d/%m/%Y") if pd.notna(dt_chegada) else "-"
                    data_descarreg_br = dt_descarreg.strftime("%d/%m/%Y") if pd.notna(dt_descarreg) else "-"
                    hora_chegada = str(r.get("hora_chegada", "") or "").strip() or "-"
                    hora_descarreg = str(r.get("hora_descarregamento", "") or "").strip() or "-"
                    qtd_estadia = int(pd.to_numeric(r.get("qtd_estadia_calc"), errors="coerce") or 0)
                    origem_txt = str(r.get("origem", "") or "").strip() or "-"
                    destino_txt = str(r.get("destino", "") or "").strip() or "-"
                    html_estadias += f"""
                        <tr>
                            <td>{nome_empresa_origem or "-"}</td>
                            <td>{nome_empresa_destino or "-"}</td>
                            <td>{origem_txt}</td>
                            <td>{destino_txt}</td>
                            <td>{data_chegada_br}</td>
                            <td>{hora_chegada}</td>
                            <td>{data_descarreg_br}</td>
                            <td>{hora_descarreg}</td>
                            <td>{qtd_estadia}</td>
                        </tr>
                    """
                html_estadias += f"""
                        </tbody>
                    </table>
                    <div class="resumo">TOTAL DE VIAGENS NO PERÍODO: {total_qtd_viagens_print}</div>
                    <div class="resumo">TOTAL DE ESTADIAS NO PERÍODO: {total_qtd_estadias_print}</div>
                    <script>
                        setTimeout(function(){{ window.print(); }}, 600);
                    </script>
                </body>
                </html>
                """
                if tem_estadias_para_imprimir:
                    components.html(html_estadias, height=900, scrolling=True)

            if c_h4.button("🖨️ Imprimir", use_container_width=True, key="btn_print_historico_data"):
                with conn() as c:
                    df_rotas_hist_print = pd.read_sql(
                        "SELECT origem, destino, nome_empresa_origem, nome_empresa_destino FROM rotas",
                        c,
                    )
                if "nome_empresa_origem" not in df_rotas_hist_print.columns:
                    df_rotas_hist_print["nome_empresa_origem"] = ""
                if "nome_empresa_destino" not in df_rotas_hist_print.columns:
                    df_rotas_hist_print["nome_empresa_destino"] = ""
                df_rotas_hist_print["origem"] = df_rotas_hist_print["origem"].fillna("").astype(str).str.strip().str.upper()
                df_rotas_hist_print["destino"] = df_rotas_hist_print["destino"].fillna("").astype(str).str.strip().str.upper()
                df_rotas_hist_print["nome_empresa_origem"] = df_rotas_hist_print["nome_empresa_origem"].fillna("").astype(str).str.strip()
                df_rotas_hist_print["nome_empresa_destino"] = df_rotas_hist_print["nome_empresa_destino"].fillna("").astype(str).str.strip()
                mapa_empresas_rota_hist = {
                    (str(r["origem"]), str(r["destino"])): (
                        str(r["nome_empresa_origem"] or ""),
                        str(r["nome_empresa_destino"] or ""),
                    )
                    for _, r in df_rotas_hist_print.iterrows()
                }
                for _, r in df_rotas_hist_print.iterrows():
                    origem_rota = str(r["origem"])
                    destino_rota = str(r["destino"])
                    nome_origem_rota = str(r["nome_empresa_origem"] or "")
                    nome_destino_rota = str(r["nome_empresa_destino"] or "")
                    chave_rota = (origem_rota, destino_rota)
                    chave_rota_inversa = (destino_rota, origem_rota)
                    if nome_origem_rota or nome_destino_rota:
                        mapa_empresas_rota_hist[chave_rota] = (nome_origem_rota, nome_destino_rota)
                        if chave_rota_inversa not in mapa_empresas_rota_hist or not any(mapa_empresas_rota_hist[chave_rota_inversa]):
                            mapa_empresas_rota_hist[chave_rota_inversa] = (nome_destino_rota, nome_origem_rota)

                total_frete_periodo = float(pd.to_numeric(df_exibir["Total Frete Valor"], errors="coerce").fillna(0.0).sum())
                total_frete_periodo += frete_fixo_rateado_periodo(filtro_ini, filtro_fim)
                if "qtd_viagens" in df_exibir.columns:
                    qtd_imp = pd.to_numeric(df_exibir["qtd_viagens"], errors="coerce").fillna(1.0)
                    qtd_imp = qtd_imp.apply(lambda x: max(1, int(round(float(x)))))
                else:
                    qtd_imp = pd.Series(1, index=df_exibir.index, dtype=int)
                total_gasto_extra_periodo = float((pd.to_numeric(df_exibir["gasto_extra"], errors="coerce").fillna(0.0) * qtd_imp).sum())
                total_estadia_periodo = float((pd.to_numeric(df_exibir["pagto_estadia"], errors="coerce").fillna(0.0) * qtd_imp).sum())
                html_hist = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: sans-serif; margin: 24px; color: #333; }}
                        header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 10px; }}
                        th, td {{ border: 1px solid #999; padding: 5px; text-align: left; white-space: nowrap; }}
                        th {{ background-color: #f2f2f2; }}
                        .resumo {{ margin-top: 14px; text-align: right; font-size: 15px; font-weight: bold; }}
                        .btn-print {{ background: #007bff; color: white; padding: 12px; border: none; width: 100%; cursor: pointer; font-weight: bold; font-size: 14px; border-radius: 5px; }}
                        @media print {{ .btn-print {{ display: none; }} body {{ margin: 0; }} }}
                    </style>
                </head>
                <body>
                    <button class="btn-print" onclick="window.print()">🖨️ IMPRIMIR HISTÓRICO</button>
                    <header>
                        <h2 style="margin:0;">Histórico de Fretes</h2>
                        <p style="margin:6px 0;">Período: <b>{filtro_ini.strftime('%d/%m/%Y')}</b> até <b>{filtro_fim.strftime('%d/%m/%Y')}</b></p>
                    </header>
                    <table>
                        <thead>
                            <tr>
                                <th>Data</th>
                                <th>NF</th>
                                <th>Nome Empresa Origem</th>
                                <th>Nome Empresa Destino</th>
                                <th>Origem</th>
                                <th>Destino</th>
                                <th>Peso (KG)</th>
                                <th>Valor Tonelada</th>
                                <th>Placa</th>
                                <th>Total Frete</th>
                                <th>Gasto Extra</th>
                                <th>Pagto Estadia</th>
                                <th>Descrição Gasto Extra</th>
                            </tr>
                        </thead>
                        <tbody>
                """

                df_ed["_data_br"] = pd.to_datetime(df_ed["data"], errors="coerce").dt.strftime('%d/%m/%Y').fillna("-")
                df_ed["_peso_kg"] = pd.to_numeric(df_ed["peso_kg"], errors="coerce").fillna(0.0)
                df_ed["_valor_ton"] = pd.to_numeric(df_ed["valor_ton"], errors="coerce").fillna(0.0)
                df_ed["_total_frete"] = pd.to_numeric(df_exibir["Total Frete Valor"], errors="coerce").fillna(0.0)
                df_ed["_gasto_extra"] = pd.to_numeric(df_exibir["gasto_extra"], errors="coerce").fillna(0.0)
                df_ed["_pagto_estadia"] = pd.to_numeric(df_exibir["pagto_estadia"], errors="coerce").fillna(0.0)
                for _, r in df_ed.iterrows():
                    data_br = r["_data_br"]
                    nf = str(r["nf"]).strip() if pd.notna(r["nf"]) and str(r["nf"]).strip() else "-"
                    origem = str(r["origem"]).strip() if pd.notna(r["origem"]) else "-"
                    destino = str(r["destino"]).strip() if pd.notna(r["destino"]) else "-"
                    origem_key = origem.strip().upper() if origem != "-" else ""
                    destino_key = destino.strip().upper() if destino != "-" else ""
                    nome_empresa_origem, nome_empresa_destino = mapa_empresas_rota_hist.get((origem_key, destino_key), ("", ""))
                    peso_kg = r["_peso_kg"]
                    valor_ton = r["_valor_ton"]
                    placa = str(r["veiculo_placa"]).strip() if pd.notna(r["veiculo_placa"]) else "-"
                    total_frete = r["_total_frete"]
                    gasto_extra = r["_gasto_extra"]
                    pagto_estadia = r["_pagto_estadia"]
                    desc_gasto_extra = str(r["descricao_gasto_extra"]).strip() if pd.notna(r["descricao_gasto_extra"]) and str(r["descricao_gasto_extra"]).strip() else "-"
                    html_hist += f"""
                        <tr>
                            <td>{data_br}</td>
                            <td>{nf}</td>
                            <td>{nome_empresa_origem or "-"}</td>
                            <td>{nome_empresa_destino or "-"}</td>
                            <td>{origem}</td>
                            <td>{destino}</td>
                            <td>{format_br(peso_kg, casas_decimais=0)}</td>
                            <td>{brl(valor_ton)}</td>
                            <td>{placa}</td>
                            <td>{brl(total_frete)}</td>
                            <td>{brl(gasto_extra)}</td>
                            <td>{brl(pagto_estadia)}</td>
                            <td>{desc_gasto_extra}</td>
                        </tr>
                    """

                html_hist += f"""
                        </tbody>
                    </table>
                    <div class="resumo">TOTAL FRETE NO PERÍODO: {brl(total_frete_periodo)}</div>
                    <div class="resumo">TOTAL GASTO EXTRA NO PERÍODO: {brl(total_gasto_extra_periodo)}</div>
                    <div class="resumo">TOTAL PAGTO ESTADIA NO PERÍODO: {brl(total_estadia_periodo)}</div>
                    <script>
                        setTimeout(function(){{ window.print(); }}, 600);
                    </script>
                </body>
                </html>
                """
                components.html(html_hist, height=900, scrolling=True)

        with tab_lucro_exec:
            df_rank = df_exibir.copy()
            for col in ["km", "qtd_viagens", "pedagio", "gasto_extra", "pagto_estadia", "valor_adicional_frete", "consumo", "diesel", "arla"]:
                if col not in df_rank.columns:
                    df_rank[col] = 0.0 if col != "qtd_viagens" else 1
            df_rank["km"] = pd.to_numeric(df_rank["km"], errors="coerce").fillna(0.0)
            df_rank["qtd_viagens"] = pd.to_numeric(df_rank["qtd_viagens"], errors="coerce").fillna(1.0)
            df_rank["qtd_viagens"] = df_rank["qtd_viagens"].apply(lambda x: max(1, int(round(float(x)))))
            df_rank["pedagio"] = pd.to_numeric(df_rank["pedagio"], errors="coerce").fillna(0.0)
            df_rank["gasto_extra"] = pd.to_numeric(df_rank["gasto_extra"], errors="coerce").fillna(0.0)
            df_rank["pagto_estadia"] = pd.to_numeric(df_rank["pagto_estadia"], errors="coerce").fillna(0.0)
            df_rank["valor_adicional_frete"] = pd.to_numeric(df_rank["valor_adicional_frete"], errors="coerce").fillna(0.0)
            df_rank["consumo"] = pd.to_numeric(df_rank["consumo"], errors="coerce").fillna(0.0)
            df_rank["diesel"] = pd.to_numeric(df_rank["diesel"], errors="coerce").fillna(0.0)
            df_rank["arla"] = pd.to_numeric(df_rank["arla"], errors="coerce").fillna(0.0)

            df_rank["km_total"] = (df_rank["km"] * df_rank["qtd_viagens"]).fillna(0.0)
            df_rank["frete_total"] = pd.to_numeric(df_rank["Total Frete Valor"], errors="coerce").fillna(0.0)
            df_rank["custo_diesel_total"] = pd.to_numeric(df_rank["Diesel/KM"], errors="coerce").fillna(0.0)
            df_rank["custo_arla_total"] = pd.to_numeric(df_rank["Gasto Arla"], errors="coerce").fillna(0.0)
            df_rank["custo_pedagio_total"] = (df_rank["pedagio"] * df_rank["qtd_viagens"]).fillna(0.0)
            df_rank["custo_extra_total"] = (df_rank["gasto_extra"] * df_rank["qtd_viagens"]).fillna(0.0)
            df_rank = aplicar_parametros_por_data(df_rank, col_data="data")
            pct_comissao_series_rank = (pd.to_numeric(df_rank["param_motora_pct"], errors="coerce").fillna(0.0) / 100.0)
            pct_imposto_series_rank = (pd.to_numeric(df_rank["param_imposto_pct"], errors="coerce").fillna(0.0) / 100.0)
            df_rank["frete_comissionavel_total"] = (
                df_rank["frete_total"] - (df_rank["valor_adicional_frete"] * df_rank["qtd_viagens"])
            ).fillna(0.0)
            df_rank["custo_motorista_comissao_total"] = (df_rank["frete_comissionavel_total"] * pct_comissao_series_rank).fillna(0.0)
            df_rank["custo_imposto_total"] = (df_rank["frete_total"] * pct_imposto_series_rank).fillna(0.0)

            df_rank["custo_total_profissional"] = (
                df_rank["custo_diesel_total"]
                + df_rank["custo_arla_total"]
                + df_rank["custo_pedagio_total"]
                + df_rank["custo_extra_total"]
                + df_rank["custo_motorista_comissao_total"]
                + df_rank["custo_imposto_total"]
            ).fillna(0.0)
            df_rank["lucro_liquido_total"] = (df_rank["frete_total"] - df_rank["custo_total_profissional"]).fillna(0.0)
            df_rank["lucro_liquido_por_km"] = (
                df_rank["lucro_liquido_total"] / df_rank["km_total"].where(df_rank["km_total"] > 0)
            ).fillna(0.0)
            df_rank["custo_total_por_km"] = (
                df_rank["custo_total_profissional"] / df_rank["km_total"].where(df_rank["km_total"] > 0)
            ).fillna(0.0)
            df_rank["margem_liquida_pct"] = (
                (df_rank["lucro_liquido_total"] / df_rank["frete_total"].where(df_rank["frete_total"] > 0)) * 100.0
            ).fillna(0.0)
            df_rank["rota"] = (
                df_rank["origem"].fillna("").astype(str).str.strip()
                + " → "
                + df_rank["destino"].fillna("").astype(str).str.strip()
            )
            df_rank["viagem_label"] = (
                df_rank["data"].astype(str)
                + " | "
                + df_rank["cliente"].fillna("").astype(str).str.strip()
                + " | "
                + df_rank["rota"]
            )

            origem_norm = df_rank["origem"].fillna("").astype(str).str.strip().str.upper()
            destino_norm = df_rank["destino"].fillna("").astype(str).str.strip().str.upper()
            mask_od_diferente = origem_norm != destino_norm
            qtd_ignoradas_od_igual = int((~mask_od_diferente).sum())
            df_rank = df_rank.loc[mask_od_diferente].copy()
            if qtd_ignoradas_od_igual > 0:
                st.info(
                    f"{qtd_ignoradas_od_igual} viagem(ns) com origem = destino foram desconsideradas no comparativo."
                )

            if (df_rank["consumo"] <= 0).any():
                st.warning("Há viagens com consumo igual a 0.00; ajuste no grid para ranking mais fiel.")

            total_receita = float(df_rank["frete_total"].sum())
            total_custos = float(df_rank["custo_total_profissional"].sum())
            total_lucro = float(df_rank["lucro_liquido_total"].sum())
            frete_fixo_periodo_rank = frete_fixo_rateado_periodo(filtro_ini, filtro_fim)
            custo_comissao_frete_fixo_rank = float(
                (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim) / 30.0
                 * (serie_parametro_diaria("motora_pct", filtro_ini, filtro_fim) / 100.0)).sum()
            )
            custo_imposto_frete_fixo_rank = float(
                (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim) / 30.0
                 * (serie_parametro_diaria("imposto_pct", filtro_ini, filtro_fim) / 100.0)).sum()
            )
            custo_rastreador_rank = valor_mensal_rateado_periodo("vl_custo_rastreador", filtro_ini, filtro_fim)
            total_receita += frete_fixo_periodo_rank
            total_custos += (custo_comissao_frete_fixo_rank + custo_imposto_frete_fixo_rank + custo_rastreador_rank)
            total_lucro += (frete_fixo_periodo_rank - custo_comissao_frete_fixo_rank - custo_imposto_frete_fixo_rank - custo_rastreador_rank)
            margem_total_pct = (total_lucro / total_receita * 100.0) if total_receita > 0 else 0.0

            g1, g2, g3, g4 = st.columns(4)
            g1.metric("Receita Total", brl(total_receita))
            g2.metric("Custo Total", brl(total_custos))
            g3.metric("Lucro Líquido", brl(total_lucro))
            g4.metric("Margem Líquida", f"{margem_total_pct:.2f}%")
            st.caption("Cálculo atual sem manutenção, pneu, depreciação e demais fixos rateados.")

            df_rank["origem_key"] = df_rank["origem"].fillna("").astype(str).str.strip().str.upper()
            df_rank["destino_key"] = df_rank["destino"].fillna("").astype(str).str.strip().str.upper()
            df_rotas = (
                df_rank.groupby(["origem_key", "destino_key"], as_index=False)
                .agg(
                    qtd_lancamentos=("id", "count"),
                    qtd_viagens=("qtd_viagens", "sum"),
                    km_total=("km_total", "sum"),
                    frete_total=("frete_total", "sum"),
                    custo_diesel_total=("custo_diesel_total", "sum"),
                    custo_arla_total=("custo_arla_total", "sum"),
                    custo_pedagio_total=("custo_pedagio_total", "sum"),
                    custo_extra_total=("custo_extra_total", "sum"),
                    custo_motorista_comissao_total=("custo_motorista_comissao_total", "sum"),
                    custo_imposto_total=("custo_imposto_total", "sum"),
                    custo_total_profissional=("custo_total_profissional", "sum"),
                    lucro_liquido_total=("lucro_liquido_total", "sum"),
                )
            )
            if not df_rotas.empty and (frete_fixo_periodo_rank > 0 or custo_rastreador_rank > 0):
                total_receita_rotas_rank = float(df_rotas["frete_total"].sum())
                if total_receita_rotas_rank > 0:
                    proporcao_rotas_rank = df_rotas["frete_total"] / total_receita_rotas_rank
                    receita_fixa_rateada_rank = frete_fixo_periodo_rank * proporcao_rotas_rank
                    custo_comissao_fixa_rateada_rank = custo_comissao_frete_fixo_rank * proporcao_rotas_rank
                    custo_imposto_fixa_rateada_rank = custo_imposto_frete_fixo_rank * proporcao_rotas_rank
                    custo_rastreador_rateado_rank = custo_rastreador_rank * proporcao_rotas_rank
                    custo_total_fixo_rateado_rank = custo_comissao_fixa_rateada_rank + custo_imposto_fixa_rateada_rank + custo_rastreador_rateado_rank
                    df_rotas["frete_total"] = df_rotas["frete_total"] + receita_fixa_rateada_rank
                    df_rotas["custo_motorista_comissao_total"] = df_rotas["custo_motorista_comissao_total"] + custo_comissao_fixa_rateada_rank
                    df_rotas["custo_imposto_total"] = df_rotas["custo_imposto_total"] + custo_imposto_fixa_rateada_rank
                    df_rotas["custo_total_profissional"] = df_rotas["custo_total_profissional"] + custo_total_fixo_rateado_rank
                    df_rotas["lucro_liquido_total"] = df_rotas["lucro_liquido_total"] + (receita_fixa_rateada_rank - custo_total_fixo_rateado_rank)
            df_rotas["origem"] = df_rotas["origem_key"]
            df_rotas["destino"] = df_rotas["destino_key"]
            df_rotas["rota"] = df_rotas["origem"] + " → " + df_rotas["destino"]
            df_rotas["lucro_liquido_por_km"] = (
                df_rotas["lucro_liquido_total"] / df_rotas["km_total"].where(df_rotas["km_total"] > 0)
            ).fillna(0.0)
            df_rotas["margem_liquida_pct"] = (
                (df_rotas["lucro_liquido_total"] / df_rotas["frete_total"].where(df_rotas["frete_total"] > 0)) * 100.0
            ).fillna(0.0)

            ranking_lucro = df_rotas.sort_values(["lucro_liquido_total", "margem_liquida_pct"], ascending=False)
            top3_lucrativas = ranking_lucro.head(3)
            mais_lucrativa = ranking_lucro.head(1)
            mais_viavel = df_rotas.sort_values("lucro_liquido_por_km", ascending=False).head(1)

            if not top3_lucrativas.empty:
                st.markdown("##### Top 3 Rotas Mais Lucrativas")
                cores_cards = [
                    ("#dcfce7", "#166534"),  # 1º lugar (verde)
                    ("#fef9c3", "#854d0e"),  # 2º lugar (amarelo)
                    ("#fee2e2", "#991b1b"),  # 3º lugar (vermelho)
                ]
                col_top1, col_top2, col_top3 = st.columns(3)
                colunas_top = [col_top1, col_top2, col_top3]
                for idx, (_, r_top) in enumerate(top3_lucrativas.iterrows()):
                    bg, cor_texto = cores_cards[idx]
                    colunas_top[idx].markdown(
                        f"""
                        <div style="background:{bg}; border-left: 6px solid {cor_texto}; border-radius:10px; padding:12px; min-height:128px;">
                            <div style="font-size:12px; color:{cor_texto}; font-weight:700;">{idx + 1}º MAIS LUCRATIVA</div>
                            <div style="font-size:20px; color:{cor_texto}; font-weight:800; margin-top:4px;">{brl(float(r_top["lucro_liquido_total"]))}</div>
                            <div style="font-size:12px; color:{cor_texto};">{str(r_top["origem"])} → {str(r_top["destino"])}</div>
                            <div style="font-size:12px; color:{cor_texto}; margin-top:8px;">Lançamentos: {int(r_top["qtd_lancamentos"])}</div>
                            <div style="font-size:11px; color:{cor_texto}; margin-top:6px;">Viabilidade real: {brl(float(r_top["lucro_liquido_por_km"]))}/km</div>
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )

            a1, a2 = st.columns(2)
            if not mais_lucrativa.empty:
                r1 = mais_lucrativa.iloc[0]
                a1.metric(
                    "Rota Mais Lucrativa",
                    brl(float(r1["lucro_liquido_total"])),
                    f"{str(r1['origem'])}→{str(r1['destino'])}",
                )
            if not mais_viavel.empty:
                r2 = mais_viavel.iloc[0]
                a2.metric(
                    "Rota Mais Viável (Lucro/KM)",
                    brl(float(r2["lucro_liquido_por_km"])) + "/km",
                    f"{str(r2['origem'])}→{str(r2['destino'])}",
                )

            st.caption("Lucro Líquido = Receita - (Diesel + Arla + Pedágio + Extra + Comissão + Imposto + Rastreador).")

            col_rank = [
                "rota",
                "qtd_lancamentos",
                "qtd_viagens",
                "km_total",
                "frete_total",
                "custo_diesel_total",
                "custo_arla_total",
                "custo_pedagio_total",
                "custo_extra_total",
                "custo_motorista_comissao_total",
                "custo_imposto_total",
                "custo_total_profissional",
                "lucro_liquido_total",
                "lucro_liquido_por_km",
                "margem_liquida_pct",
            ]
            st.dataframe(
                df_rotas[col_rank].sort_values(["lucro_liquido_total", "margem_liquida_pct"], ascending=False),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "rota": "Rota",
                    "qtd_lancamentos": st.column_config.NumberColumn("Lançamentos", format="%.0f"),
                    "qtd_viagens": st.column_config.NumberColumn("Qtd", format="%.0f"),
                    "km_total": st.column_config.NumberColumn("KM Total", format="%.0f"),
                    "frete_total": st.column_config.NumberColumn("Frete Total", format="R$ %.2f"),
                    "custo_diesel_total": st.column_config.NumberColumn("Custo Diesel", format="R$ %.2f"),
                    "custo_arla_total": st.column_config.NumberColumn("Custo Arla", format="R$ %.2f"),
                    "custo_pedagio_total": st.column_config.NumberColumn("Pedágio", format="R$ %.2f"),
                    "custo_extra_total": st.column_config.NumberColumn("Gasto Extra", format="R$ %.2f"),
                    "custo_motorista_comissao_total": st.column_config.NumberColumn("Comissão Motorista", format="R$ %.2f"),
                    "custo_imposto_total": st.column_config.NumberColumn("Imposto", format="R$ %.2f"),
                    "custo_total_profissional": st.column_config.NumberColumn("Custo Total", format="R$ %.2f"),
                    "lucro_liquido_total": st.column_config.NumberColumn("Lucro Líquido", format="R$ %.2f"),
                    "lucro_liquido_por_km": st.column_config.NumberColumn("Lucro Líquido/KM", format="R$ %.2f"),
                    "margem_liquida_pct": st.column_config.NumberColumn("Margem Líquida (%)", format="%.2f%%"),
                },
            )

with aba3:
    if not df_db.empty:
        df_ana = df_db.copy()
        origem_norm_ana = df_ana["origem"].fillna("").astype(str).str.strip().str.upper()
        destino_norm_ana = df_ana["destino"].fillna("").astype(str).str.strip().str.upper()
        mask_od_diferente_ana = origem_norm_ana != destino_norm_ana
        qtd_ignoradas_ana = int((~mask_od_diferente_ana).sum())
        df_ana = df_ana.loc[mask_od_diferente_ana].copy()
        if qtd_ignoradas_ana > 0:
            st.info(
                f"{qtd_ignoradas_ana} viagem(ns) com origem = destino foram desconsideradas na aba Análise."
            )
        df_ana["consumo"] = pd.to_numeric(df_ana["consumo"], errors="coerce")
        df_ana["diesel"] = pd.to_numeric(df_ana["diesel"], errors="coerce").fillna(0.0)
        df_ana["pedagio"] = pd.to_numeric(df_ana["pedagio"], errors="coerce").fillna(0.0)
        if "gasto_extra" not in df_ana.columns:
            df_ana["gasto_extra"] = 0.0
        if "pagto_estadia" not in df_ana.columns:
            df_ana["pagto_estadia"] = 0.0
        df_ana["gasto_extra"] = pd.to_numeric(df_ana["gasto_extra"], errors="coerce").fillna(0.0)
        df_ana["pagto_estadia"] = pd.to_numeric(df_ana["pagto_estadia"], errors="coerce").fillna(0.0)
        if "qtd_viagens" not in df_ana.columns:
            df_ana["qtd_viagens"] = 1
        df_ana["qtd_viagens"] = pd.to_numeric(df_ana["qtd_viagens"], errors="coerce").fillna(1.0)
        df_ana["qtd_viagens"] = df_ana["qtd_viagens"].apply(lambda x: max(1, int(round(float(x)))))
        with conn() as c:
            df_abs_ana = pd.read_sql(
                """SELECT tipo_combustivel, qtde_litros, total_gasto, veiculo_placa
                   FROM abastecimentos
                   WHERE date(data) BETWEEN ? AND ?""",
                c,
                params=(filtro_ini.isoformat(), filtro_fim.isoformat()),
            )
        if placa_filtro_calculo and not df_abs_ana.empty and "veiculo_placa" in df_abs_ana.columns:
            placa_ref_abs_ana = str(placa_filtro_calculo).strip().upper()
            df_abs_ana = df_abs_ana[
                df_abs_ana["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_abs_ana
            ].copy()
        if not df_abs_ana.empty:
            df_abs_ana["tipo_combustivel"] = df_abs_ana["tipo_combustivel"].apply(normalizar_tipo_combustivel)
            df_abs_ana["qtde_litros"] = pd.to_numeric(df_abs_ana["qtde_litros"], errors="coerce").fillna(0.0)
            df_abs_ana["total_gasto"] = pd.to_numeric(df_abs_ana["total_gasto"], errors="coerce").fillna(0.0)
            t_litros_diesel_ana = float(
                df_abs_ana[df_abs_ana["tipo_combustivel"].str.contains("DIESEL", na=False)]["qtde_litros"].sum()
            )
            t_litros_arla_ana = float(
                df_abs_ana[df_abs_ana["tipo_combustivel"].str.contains("ARLA", na=False)]["qtde_litros"].sum()
            )
            t_valor_arla_ana = float(
                df_abs_ana[df_abs_ana["tipo_combustivel"].str.contains("ARLA", na=False)]["total_gasto"].sum()
            )
        else:
            t_litros_diesel_ana = 0.0
            t_litros_arla_ana = 0.0
            t_valor_arla_ana = 0.0

        t_rec = float(
            (
                (
                    pd.to_numeric(df_ana["Total Frete"], errors="coerce").fillna(0.0)
                    + pd.to_numeric(df_ana["pagto_estadia"], errors="coerce").fillna(0.0)
                    + pd.to_numeric(df_ana.get("valor_adicional_frete", 0.0), errors="coerce").fillna(0.0)
                )
                * df_ana["qtd_viagens"]
            ).sum()
        )
        frete_fixo_periodo_ana = frete_fixo_rateado_periodo(filtro_ini, filtro_fim)
        t_rec += frete_fixo_periodo_ana

        # Evita distorções no custo diesel quando o consumo está zerado ou inválido.
        consumo_valido = df_ana["consumo"] > 0
        df_ana["custo_diesel"] = 0.0
        df_ana.loc[consumo_valido, "custo_diesel"] = (
            ((df_ana.loc[consumo_valido, "km"] * df_ana.loc[consumo_valido, "qtd_viagens"]) / df_ana.loc[consumo_valido, "consumo"])
            * df_ana.loc[consumo_valido, "diesel"]
        )

        t_die = float(df_ana["custo_diesel"].sum())
        df_ana = aplicar_parametros_por_data(df_ana, col_data="data")
        df_ana["km_total"] = (pd.to_numeric(df_ana["km"], errors="coerce").fillna(0.0) * df_ana["qtd_viagens"]).fillna(0.0)
        df_ana["receita_viagem"] = (
            (
                pd.to_numeric(df_ana["Total Frete"], errors="coerce").fillna(0.0)
                + pd.to_numeric(df_ana["pagto_estadia"], errors="coerce").fillna(0.0)
                + pd.to_numeric(df_ana.get("valor_adicional_frete", 0.0), errors="coerce").fillna(0.0)
            )
            * df_ana["qtd_viagens"]
        ).fillna(0.0)
        df_ana["receita_comissionavel_viagem"] = (
            (
                pd.to_numeric(df_ana["Total Frete"], errors="coerce").fillna(0.0)
                + pd.to_numeric(df_ana["pagto_estadia"], errors="coerce").fillna(0.0)
            )
            * df_ana["qtd_viagens"]
        ).fillna(0.0)
        t_km = float(df_ana["km_total"].sum())
        t_pneu = float((df_ana["km_total"] * pd.to_numeric(df_ana["param_pneu"], errors="coerce").fillna(0.0)).sum())
        t_manut = float((df_ana["km_total"] * pd.to_numeric(df_ana["param_manut"], errors="coerce").fillna(0.0)).sum())
        t_depre = float((df_ana["km_total"] * pd.to_numeric(df_ana["param_depre"], errors="coerce").fillna(0.0)).sum())
        t_ped = float((df_ana["pedagio"] * df_ana["qtd_viagens"]).sum())
        t_extra = float((df_ana["gasto_extra"] * df_ana["qtd_viagens"]).sum())
        t_estadia = float((df_ana["pagto_estadia"] * df_ana["qtd_viagens"]).sum())
        t_comis_viagens = float(
            (df_ana["receita_comissionavel_viagem"] * (pd.to_numeric(df_ana["param_motora_pct"], errors="coerce").fillna(0.0) / 100.0)).sum()
        )
        t_imposto_viagens = float(
            (df_ana["receita_viagem"] * (pd.to_numeric(df_ana["param_imposto_pct"], errors="coerce").fillna(0.0) / 100.0)).sum()
        )
        t_comis_frete_fixo = float(
            (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim) / 30.0
             * (serie_parametro_diaria("motora_pct", filtro_ini, filtro_fim) / 100.0)).sum()
        )
        t_imposto_frete_fixo = float(
            (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim) / 30.0
             * (serie_parametro_diaria("imposto_pct", filtro_ini, filtro_fim) / 100.0)).sum()
        )
        t_comis = t_comis_viagens + t_comis_frete_fixo
        t_imposto = t_imposto_viagens + t_imposto_frete_fixo

        dias_periodo = max(1, dias_rateio_periodo(filtro_ini, filtro_fim))
        t_mot_fixo_rateado = valor_mensal_rateado_periodo("motora_fixo", filtro_ini, filtro_fim)
        t_seguro = valor_mensal_rateado_periodo("seguro", filtro_ini, filtro_fim)
        t_seguro_vida_motorista = valor_mensal_rateado_periodo("seguro_vida_motorista", filtro_ini, filtro_fim)
        t_fin = valor_mensal_rateado_periodo("financiamento", filtro_ini, filtro_fim)
        # IPVA informado como valor anual: rateio proporcional por dia.
        t_ipva = valor_anual_rateado_periodo("pagto_ipva", filtro_ini, filtro_fim)
        t_escritorio = valor_mensal_rateado_periodo("cmp_custo_escritorio", filtro_ini, filtro_fim)
        t_rastreador = valor_mensal_rateado_periodo("vl_custo_rastreador", filtro_ini, filtro_fim)
        t_mot_total = t_mot_fixo_rateado + t_comis

        lucro = t_rec - (
            t_die + t_valor_arla_ana + t_pneu + t_manut + t_depre + t_mot_total +
            t_ped + t_escritorio + t_rastreador + t_extra + t_seguro + t_seguro_vida_motorista + t_fin + t_ipva + t_imposto
        )

        m1, m2, m3, m4, m5, m6, m7, m8 = st.columns(8)
        m1.metric("Total KM no Período", f"{format_br(t_km, casas_decimais=0)} KM")
        m2.metric("Total Litro Diesel", f"{t_litros_diesel_ana:.2f} L")
        m3.metric("Total Valor Diesel", brl(t_die))
        m4.metric("Total Litro Arla", f"{t_litros_arla_ana:.2f} L")
        m5.metric("Valor Total Arla", brl(t_valor_arla_ana))
        m6.metric("Faturamento", brl(t_rec))
        m7.metric("Custo Total no Período", brl(t_rec - lucro))
        m8.metric("Lucro Líquido", brl(lucro))

        df_custos = pd.DataFrame(
            {
                "Categoria": [
                    "Diesel",
                    "Arla",
                    "Pneu",
                    "Manutenção",
                    "Depreciação",
                    "Motorista (Rateado)",
                    "Pedágio",
                    "Escritório",
                    "Rastreador",
                    "Gasto Extra",
                    "Seguro (Rateado)",
                    "Seguro Vida Motorista Mensal (Rateado)",
                    "Financiamento (Rateado)",
                    "Pagto IPVA (Rateado)",
                    "Imposto",
                    "Lucro",
                ],
                "Valor": [t_die, t_valor_arla_ana, t_pneu, t_manut, t_depre, t_mot_total, t_ped, t_escritorio, t_rastreador, t_extra, t_seguro, t_seguro_vida_motorista, t_fin, t_ipva, t_imposto, max(0, lucro)],
            }
        )
        df_custos["Legenda"] = df_custos.apply(
            lambda r: f"{brl(r['Valor'])} - {r['Categoria']}",
            axis=1,
        )
        base_colors = [
            "#60a5fa", "#f59e0b", "#34d399", "#f472b6", "#a78bfa",
            "#f87171", "#22d3ee", "#fbbf24", "#818cf8", "#fb7185", "#10b981"
        ]

        def escurecer_hex(cor_hex, fator=0.72):
            cor_hex = str(cor_hex).lstrip("#")
            if len(cor_hex) != 6:
                return "#999999"
            r = int(cor_hex[0:2], 16)
            g = int(cor_hex[2:4], 16)
            b = int(cor_hex[4:6], 16)
            r = max(0, min(255, int(r * fator)))
            g = max(0, min(255, int(g * fator)))
            b = max(0, min(255, int(b * fator)))
            return f"#{r:02x}{g:02x}{b:02x}"

        cores_topo = [base_colors[i % len(base_colors)] for i in range(len(df_custos))]
        cores_sombra = [escurecer_hex(c, 0.64) for c in cores_topo]

        fig = go.Figure()
        # Em celulares, muitas camadas + margem lateral grande podem ocultar a pizza.
        profundidade = 4
        for i in range(profundidade):
            desloc_y = 0.01 + (i * 0.003)
            fig.add_trace(
                go.Pie(
                    labels=df_custos["Legenda"],
                    values=df_custos["Valor"],
                    hole=0.55,
                    sort=False,
                    direction="clockwise",
                    rotation=30,
                    marker=dict(colors=cores_sombra, line=dict(color="#4b5563", width=0.2)),
                    textinfo="none",
                    hoverinfo="skip",
                    showlegend=False,
                    domain=dict(x=[0.02, 0.98], y=[desloc_y, 0.90 + desloc_y]),
                )
            )

        fig.add_trace(
            go.Pie(
                labels=df_custos["Legenda"],
                values=df_custos["Valor"],
                hole=0.55,
                sort=False,
                direction="clockwise",
                rotation=30,
                marker=dict(colors=cores_topo, line=dict(color="#ffffff", width=1.2)),
                textposition="inside",
                textinfo="percent",
                hovertemplate="%{label}<br>%{percent}<extra></extra>",
                showlegend=True,
                domain=dict(x=[0.02, 0.98], y=[0.05, 0.95]),
            )
        )

        fig.update_layout(
            title="Distribuição de Custos (Efeito 3D)",
            margin=dict(l=10, r=200, t=60, b=30),
            height=520,
            legend=dict(
                orientation="v",
                y=0.5,
                yanchor="middle",
                x=1.02,
                xanchor="left",
                font=dict(size=11),
            ),
        )
        st.plotly_chart(fig, use_container_width=True)
        st.caption(
            f"Rateio dos custos fixos calculado sobre {dias_periodo} dia(s) do filtro. "
            "Custos mensais, como seguro de vida do motorista, são calculados por valor mensal/30*dias. "
            "IPVA rateado por dia (anual/365)."
        )




# ==================================================================================
# ABA 4 - MANUTENÇÃO (ATUALIZADA: FLUXO DE ENTRADA, CONCLUSÃO E HISTÓRICO)
# ==================================================================================
with aba4:
    st.subheader("🛠️ Gestão de Manutenção")
    
    with st.expander("➕ Cadastro de Manutenção", expanded=False):
        if not apenas_placas or not fornecedores_db:
            st.warning("⚠️ Cadastre veículos e fornecedores primeiro.")
        else:
            col_ent, col_con = st.columns(2)
            
            # --- 1. ENTRADA (ABERTURA) ---
            with col_ent:
                st.markdown("### 1️⃣ Abrir Ordem (Entrada)")
                with st.form("f_manut_entrada_v10", clear_on_submit=True):
                    c_e1, c_e2 = st.columns(2)
                    d_e = c_e1.date_input("Data de Entrada", format="DD/MM/YYYY")
                    num_os = c_e2.text_input("Nº O.S.")
    
                    opcoes_veiculo_manut = (
                        [f"{str(v.get('descricao') or '').strip()} - {str(v.get('placa') or '').strip()}" for v in veiculos_db]
                        if veiculos_db else apenas_placas
                    )
                    veic_sel_manut = st.selectbox("Veículo", opcoes_veiculo_manut)
                    if " - " in str(veic_sel_manut):
                        v_m = str(veic_sel_manut).rsplit(" - ", 1)[1].strip()
                    else:
                        v_m = str(veic_sel_manut).strip()
                    k_m = st.number_input("KM Entrada", step=1.0)
                    o_m = st.selectbox("Fornecedor", list(dict_fornecedores_manutencao.keys()))
                    def_m = st.text_area("Relato do Defeito")
                    
                    if st.form_submit_button("💾 Gravar", key="btn_manut_entrada_gravar"):
                        with conn() as c:
                            c.execute("""INSERT INTO manutencoes (data_entrada, veiculo_placa, oficina_id, defeito, km_servico, num_os) 
                                         VALUES (?,?,?,?,?,?)""", (d_e.isoformat(), v_m, dict_fornecedores_manutencao[o_m], def_m, k_m, num_os))
                        alerta_gravado()
                        st.rerun()
    
            # --- 2. CONCLUSÃO (FINALIZAÇÃO) ---
            with col_con:
                with conn() as c:
                    abertas = c.execute(
                        """SELECT m.id, m.veiculo_placa, f.nome, m.data_entrada
                           FROM manutencoes m
                           JOIN fornecedores f ON m.oficina_id = f.id
                           WHERE (m.data_fim IS NULL OR TRIM(m.data_fim) = '')
                             AND (m.servico IS NULL OR TRIM(m.servico) = '')
                           ORDER BY m.data_entrada DESC, m.id DESC"""
                    ).fetchall()
                
                if abertas:
                    st.markdown("### 2️⃣ Finalizar Serviço")
                    opc_ab = {f"{a['veiculo_placa']} | {a['nome']} ({datetime.strptime(a['data_entrada'], '%Y-%m-%d').strftime('%d/%m/%Y')})": a['id'] for a in abertas}
                    sel_ab = st.selectbox("Selecionar Ordem Aberta", list(opc_ab.keys()))
                    fornecedores_pecas, mapa_fornecedores_pecas = carregar_fornecedores_para_pecas()
                    # Botão para excluir ordem aberta (com confirmação)
                    if "excluir_manut_confirm" not in st.session_state:
                        st.session_state.excluir_manut_confirm = None
    
                    if st.button("🗑️ EXCLUIR ORDEM", key=f"btn_excluir_ordem_{sel_ab}"):
                        st.session_state.excluir_manut_confirm = opc_ab[sel_ab]
    
                    if st.session_state.excluir_manut_confirm == opc_ab[sel_ab]:
                        st.warning("Confirma exclusão desta ordem? Esta ação é irreversível.")
                        c_ex1, c_ex2 = st.columns(2)
                        if c_ex1.button("Confirmar exclusão", key=f"btn_confirm_excluir_{sel_ab}"):
                            excluir_manutencao_completa(int(opc_ab[sel_ab]))
                            st.success("Ordem excluída com sucesso.")
                            st.session_state.excluir_manut_confirm = None
                            st.rerun()
                        if c_ex2.button("Cancelar", key=f"btn_cancel_excluir_{sel_ab}"):
                            st.session_state.excluir_manut_confirm = None
                            st.rerun()
                    
                    with st.form("f_manut_concl_v10"):
                        c_s1, c_s2 = st.columns(2)
                        d_f = c_s1.date_input("Data Saída", format="DD/MM/YYYY")
                        num_nf = c_s2.text_input("Nº Nota Fiscal (N.F.)")
                        
                        serv_f = st.text_area("Serviço Realizado")
                        
                        c1, c3, c4, c5 = st.columns(4)
                        v_mo = c1.number_input("Mão de Obra (R$)", min_value=0.0, step=10.0)
                        v_ct_ida = c3.number_input("Custo Transp Ida (R$)", min_value=0.0, step=10.0)
                        v_ct_ret = c4.number_input("Custo Transporte Retorno (R$)", min_value=0.0, step=10.0)
                        dt_gar = c5.date_input("Vencimento Garantia", format="DD/MM/YYYY")
    
                        st.markdown("**Peças compradas de fornecedores**")
                        if not fornecedores_pecas:
                            st.warning("Cadastre fornecedores na aba Fornecedores para lançar peças.")
                        df_pecas_finalizar = st.data_editor(
                            dataframe_pecas_vazio(),
                            key=f"editor_pecas_finalizar_{opc_ab[sel_ab]}",
                            hide_index=True,
                            use_container_width=True,
                            num_rows="dynamic",
                            column_config={
                                "Fornecedor": st.column_config.SelectboxColumn("Nome do Fornecedor", options=fornecedores_pecas),
                                "Data Compra": st.column_config.DateColumn("Data Compra", format="DD/MM/YYYY"),
                                "N. NF": st.column_config.TextColumn("N. NF"),
                                "Descricao da Peca": st.column_config.TextColumn("Descrição da Peça"),
                                "Valor da Peca": st.column_config.NumberColumn("Valor da Peça", min_value=0.0, step=0.01, format="R$ %.2f"),
                                "Excluir": st.column_config.CheckboxColumn("🗑️ Excluir"),
                            },
                        )
                        itens_pecas_finalizar, v_pe, erro_pecas_finalizar = preparar_pecas_manutencao(df_pecas_finalizar, mapa_fornecedores_pecas)
                        
                        # Campo Visual do Total
                        st.info(f"💰 **Total do Serviço: {brl(v_mo + v_pe + v_ct_ida + v_ct_ret)}**")
                        
                        obs_f = st.text_area("Observações Adicionais")
                        pedido_fornecedor_files = st.file_uploader(
                            "Anexar Pedido(s) do Fornecedor",
                            type=["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx", "xls", "xlsx"],
                            accept_multiple_files=True,
                            key="manut_pedido_fornecedor_upload",
                        )
                        
                        if st.form_submit_button("💾 Gravar", key="btn_manut_conclusao_gravar"):
                            if erro_pecas_finalizar:
                                st.warning(erro_pecas_finalizar)
                                st.stop()
                            pedido_fornecedor_anexos = salvar_anexos_pedido_fornecedor(pedido_fornecedor_files)
                            manut_id = int(opc_ab[sel_ab])
                            with conn() as c:
                                c.execute("""UPDATE manutencoes SET data_fim=?, servico=?, valor_mo=?, valor_pecas=?, custo_transporte_ida=?, custo_transporte_retorno=?, num_nf=?, 
                                             data_vencimento_garantia=?, observacao_adicional=?, pedido_fornecedor_arquivo=? 
                                             WHERE id=?""", (d_f.isoformat(), serv_f, v_mo, v_pe, v_ct_ida, v_ct_ret, num_nf, dt_gar.isoformat(), obs_f, (pedido_fornecedor_anexos[0]["caminho_arquivo"] if pedido_fornecedor_anexos else None), manut_id))
                                salvar_pecas_manutencao(c, manut_id, itens_pecas_finalizar)
                                for anexo in pedido_fornecedor_anexos:
                                    c.execute(
                                        """INSERT INTO manutencoes_anexos (manutencao_id, nome_arquivo, caminho_arquivo, data_inclusao)
                                           VALUES (?, ?, ?, ?)""",
                                        (manut_id, anexo["nome_arquivo"], anexo["caminho_arquivo"], datetime.now().isoformat()),
                                    )
                            alerta_gravado()
                            st.rerun()
    
    # --- 3. HISTÓRICO COM EDIÇÃO TOTAL ---
    st.markdown("---")
    st.subheader("📋 Histórico e Auditoria")
    
    # Filtros: fornecedor e placa
    with conn() as c:
        df_placas_manut = pd.read_sql(
            """SELECT DISTINCT UPPER(TRIM(m.veiculo_placa)) as placa,
                      COALESCE(v.descricao, '') as descricao
               FROM manutencoes m
               LEFT JOIN veiculos v ON UPPER(TRIM(v.placa)) = UPPER(TRIM(m.veiculo_placa))
               WHERE m.veiculo_placa IS NOT NULL AND TRIM(m.veiculo_placa) <> ''
               ORDER BY placa ASC""", c
        )

    # Monta dict label -> placa para o selectbox
    mapa_placa_manut = {}
    for _, row_p in df_placas_manut.iterrows():
        label_p = f"{row_p['placa']} - {row_p['descricao']}" if row_p["descricao"] else row_p["placa"]
        mapa_placa_manut[label_p] = row_p["placa"]

    filtro_cols = st.columns(2)
    fornecedores_manut_opcoes = ["Todos os fornecedores"] + list(dict_fornecedores_manutencao.keys())
    fornecedor_manut_selecionado = filtro_cols[0].selectbox("Filtrar por Fornecedor", fornecedores_manut_opcoes, key="filtro_fornecedor_manutencao")
    labels_placas_selecionadas = filtro_cols[1].multiselect(
        "Filtrar por Placa",
        list(mapa_placa_manut.keys()),
        placeholder="Todas as placas",
        key="filtro_placa_manutencao",
    )

    with conn() as c:
        # Construir query com filtros opcionais de fornecedor e placa
        query_base = """SELECT m.*, f.nome as fornecedor_nome FROM manutencoes m
                        JOIN fornecedores f ON m.oficina_id = f.id
                        WHERE m.data_entrada BETWEEN ? AND ?"""
        params = [filtro_ini.isoformat(), filtro_fim.isoformat()]

        if fornecedor_manut_selecionado != "Todos os fornecedores":
            fornecedor_id = dict_fornecedores_manutencao[fornecedor_manut_selecionado]
            query_base += " AND m.oficina_id = ?"
            params.append(fornecedor_id)

        placas_manut_selecionadas = [
            mapa_placa_manut[label]
            for label in labels_placas_selecionadas
            if label in mapa_placa_manut
        ]
        if placas_manut_selecionadas:
            placeholders_placas = ",".join(["?"] * len(placas_manut_selecionadas))
            query_base += f" AND UPPER(TRIM(m.veiculo_placa)) IN ({placeholders_placas})"
            params.extend(placas_manut_selecionadas)

        query_base += " ORDER BY m.data_entrada DESC"
        df_m = pd.read_sql(query_base, c, params=params)

    total_periodo_manut = 0.0
    total_periodo_mo = 0.0
    total_periodo_pecas = 0.0
    total_periodo_transporte = 0.0
    if not df_m.empty:
        total_periodo_mo = pd.to_numeric(df_m["valor_mo"], errors="coerce").fillna(0).sum()
        total_periodo_pecas = pd.to_numeric(df_m["valor_pecas"], errors="coerce").fillna(0).sum()
        total_periodo_ida = pd.to_numeric(df_m["custo_transporte_ida"], errors="coerce").fillna(0)
        total_periodo_retorno = pd.to_numeric(df_m["custo_transporte_retorno"], errors="coerce").fillna(0)
        if "custo_transporte" in df_m.columns:
            total_periodo_legado = pd.to_numeric(df_m["custo_transporte"], errors="coerce").fillna(0)
            sem_ida_retorno = (total_periodo_ida == 0) & (total_periodo_retorno == 0)
            total_periodo_ida = total_periodo_ida.mask(sem_ida_retorno, total_periodo_legado)
        total_periodo_transporte = total_periodo_ida.sum() + total_periodo_retorno.sum()
        total_periodo_manut = total_periodo_mo + total_periodo_pecas + total_periodo_transporte

    resumo_manut_cols = st.columns(4)
    resumo_manut_cols[0].metric("Total Gasto no Período", brl(total_periodo_manut))
    resumo_manut_cols[1].metric("Mão de Obra", brl(total_periodo_mo))
    resumo_manut_cols[2].metric("Peças", brl(total_periodo_pecas))
    resumo_manut_cols[3].metric("Transporte", brl(total_periodo_transporte))

    if not df_m.empty:
        if "manut_excluir_mov_id" not in st.session_state:
            st.session_state.manut_excluir_mov_id = None

        st.markdown("##### Excluir Movimento")
        opcoes_excluir_manut = {}
        for _, mov in df_m.iterrows():
            data_mov = datetime.strptime(mov["data_entrada"], "%Y-%m-%d").strftime("%d/%m/%Y") if mov["data_entrada"] else "-"
            ct_ida_mov = float(mov["custo_transporte_ida"] or 0)
            ct_ret_mov = float(mov["custo_transporte_retorno"] or 0)
            total_mov = float(mov["valor_mo"] or 0) + float(mov["valor_pecas"] or 0) + ct_ida_mov + ct_ret_mov
            label_mov = (
                f"ID {int(mov['id'])} | {data_mov} | {mov['veiculo_placa']} | "
                f"{mov['fornecedor_nome']} | Total: {brl(total_mov)}"
            )
            opcoes_excluir_manut[label_mov] = int(mov["id"])

        sel_excluir_manut = st.selectbox(
            "Selecione o movimento para excluir",
            options=list(opcoes_excluir_manut.keys()),
            index=None,
            placeholder="Escolha um movimento",
            key="manut_sel_excluir_movimento",
        )
        ex_col1, ex_col2 = st.columns(2)
        if ex_col1.button("🗑️ Excluir Movimento Selecionado", type="primary", use_container_width=True, key="btn_manut_excluir_movimento"):
            if sel_excluir_manut:
                st.session_state.manut_excluir_mov_id = opcoes_excluir_manut[sel_excluir_manut]
                st.rerun()
            else:
                st.warning("Selecione um movimento para excluir.")

        if st.session_state.manut_excluir_mov_id is not None:
            st.warning(f"Confirma a exclusão do movimento ID {int(st.session_state.manut_excluir_mov_id)}?")
            conf_col1, conf_col2 = st.columns(2)
            if conf_col1.button("✅ Confirmar exclusão", type="primary", use_container_width=True, key="btn_manut_confirmar_excluir_movimento"):
                excluir_manutencao_completa(int(st.session_state.manut_excluir_mov_id))
                st.session_state.manut_excluir_mov_id = None
                st.success("Movimento excluído com sucesso.")
                st.rerun()
            if conf_col2.button("❌ Cancelar", use_container_width=True, key="btn_manut_cancelar_excluir_movimento"):
                st.session_state.manut_excluir_mov_id = None
                st.rerun()

        if st.button("🖨️ Imprimir Manutenção por Período", use_container_width=True, key="btn_print_manutencao_periodo"):
            total_mo = 0.0
            total_pe = 0.0
            total_ida = 0.0
            total_ret = 0.0
            total_servicos = 0.0
            placas_rel_manut = (
                ", ".join(placas_manut_selecionadas)
                if placas_manut_selecionadas else "Todas as placas"
            )
            pecas_por_manutencao = {}
            ids_manutencao_rel = [int(x) for x in df_m["id"].tolist()]
            if ids_manutencao_rel:
                placeholders = ",".join(["?"] * len(ids_manutencao_rel))
                with conn() as c:
                    pecas_rel = c.execute(
                        f"""SELECT mp.manutencao_id, f.codigo, f.nome, mp.data_compra, mp.num_nf, mp.descricao_peca, mp.valor_peca
                            FROM manutencoes_pecas mp
                            LEFT JOIN fornecedores f ON f.id = mp.fornecedor_id
                            WHERE mp.manutencao_id IN ({placeholders})
                            ORDER BY mp.manutencao_id, mp.id""",
                        ids_manutencao_rel,
                    ).fetchall()
                for peca in pecas_rel:
                    data_compra_txt = ""
                    if peca["data_compra"]:
                        data_compra_txt = datetime.strptime(peca["data_compra"], "%Y-%m-%d").strftime("%d/%m/%Y")
                    fornecedor_txt = f"{peca['codigo']} - {peca['nome']}" if peca["codigo"] and peca["nome"] else "-"
                    linha_peca = (
                        f"<p class='linha-detalhe'><strong>{fornecedor_txt}</strong>"
                        f" | Compra: {data_compra_txt or '-'}"
                        f" | NF: {peca['num_nf'] or '-'}"
                        f" | {str(peca['descricao_peca'] or '').replace(chr(10), '<br>')}"
                        f" | {brl(peca['valor_peca'] or 0)}</p>"
                    )
                    pecas_por_manutencao.setdefault(int(peca["manutencao_id"]), []).append(linha_peca)
            html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: sans-serif; margin: 30px; color: #333; }}
                    header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 12px; }}
                    th, td {{ border: 1px solid #999; padding: 8px; text-align: left; vertical-align: top; }}
                    th {{ background-color: #f2f2f2; }}
                    .btn-print {{ background: #007bff; color: white; padding: 12px; border: none; width: 100%; cursor: pointer; font-weight: bold; font-size: 14px; border-radius: 5px; margin-bottom: 20px; }}
                    .resumo {{ margin-top: 20px; font-size: 14px; }}
                    .linha-detalhe {{ margin: 0; padding: 0; }}
                    @media print {{ .btn-print {{ display: none; }} body {{ margin: 0; }} }}
                </style>
            </head>
            <body>
                <button class="btn-print" onclick="window.print()">🖨️ IMPRIMIR MANUTENÇÃO POR PERÍODO</button>
                <header>
                    <h1 style="margin:0;">ART TRANSPORTES</h1>
                    <p style="margin:5px 0;">RELATÓRIO DE MANUTENÇÃO</p>
                    <p>Período: <b>{filtro_ini.strftime('%d/%m/%Y')}</b> até <b>{filtro_fim.strftime('%d/%m/%Y')}</b></p>
                    <p style="margin:5px 0;">Placa(s): <b>{placas_rel_manut}</b></p>
                    <p style="margin:5px 0;">Registros: <b>{len(df_m)}</b></p>
                </header>
                <table>
                    <thead>
                        <tr>
                            <th>Data Entrada</th>
                            <th>Nº OS</th>
                            <th>Veículo</th>
                            <th>KM Entrada</th>
                            <th>Fornecedor</th>
                            <th>Defeito Relatado</th>
                            <th>Serviço Realizado</th>
                            <th>Fornecedores / Peças</th>
                            <th>Mão de Obra</th>
                            <th>Peças</th>
                            <th>Transp Ida</th>
                            <th>Transp Ret</th>
                            <th>Total Serviço</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            for _, r in df_m.iterrows():
                dt_ent_br = datetime.strptime(r['data_entrada'], '%Y-%m-%d').strftime('%d/%m/%Y') if r['data_entrada'] else ''
                num_os_txt = r['num_os'] or ''
                veiculo_txt = r['veiculo_placa'] or ''
                km_txt = int(r['km_servico']) if r['km_servico'] not in (None, '') else 0
                km_txt = f"{km_txt:,}".replace(',', '.') if km_txt else ''
                fornecedor_txt_manut = r['fornecedor_nome'] or ''
                defeito_txt = str(r['defeito'] or '').replace('\n', '<br>')
                servico_txt = str(r['servico'] or '').replace('\n', '<br>')
                pecas_txt = "".join(pecas_por_manutencao.get(int(r["id"]), [])) or "-"
                valor_mo = float(r['valor_mo'] or 0)
                valor_pe = float(r['valor_pecas'] or 0)
                ct_ida = float(r['custo_transporte_ida'] or 0)
                ct_ret = float(r['custo_transporte_retorno'] or 0)
                ct_leg = float(r['custo_transporte'] or 0) if 'custo_transporte' in r else 0
                if ct_ida == 0.0 and ct_ret == 0.0 and ct_leg > 0.0:
                    ct_ida = ct_leg
                total_serv = valor_mo + valor_pe + ct_ida + ct_ret
                total_mo += valor_mo
                total_pe += valor_pe
                total_ida += ct_ida
                total_ret += ct_ret
                total_servicos += total_serv
                html += f"""
                    <tr>
                        <td>{dt_ent_br}</td>
                        <td>{num_os_txt}</td>
                        <td>{veiculo_txt}</td>
                        <td>{km_txt}</td>
                        <td>{fornecedor_txt_manut}</td>
                        <td>{defeito_txt}</td>
                        <td>{servico_txt}</td>
                        <td>{pecas_txt}</td>
                        <td>{brl(valor_mo)}</td>
                        <td>{brl(valor_pe)}</td>
                        <td>{brl(ct_ida)}</td>
                        <td>{brl(ct_ret)}</td>
                        <td>{brl(total_serv)}</td>
                    </tr>
                """
            html += f"""
                    </tbody>
                </table>
                <div class="resumo">
                    <p><strong>Total de ordens:</strong> {len(df_m)}</p>
                    <p><strong>Gasto total Mão de Obra:</strong> {brl(total_mo)}</p>
                    <p><strong>Gasto total Peças:</strong> {brl(total_pe)}</p>
                    <p><strong>Total Transporte Ida:</strong> {brl(total_ida)}</p>
                    <p><strong>Total Transporte Retorno:</strong> {brl(total_ret)}</p>
                    <p><strong>Total serviço:</strong> {brl(total_servicos)}</p>
                    <p><strong>Gerado em:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                </div>
                <script>setTimeout(function(){{ window.print(); }}, 700);</script>
            </body>
            </html>
            """
            components.html(html, height=1000, scrolling=True)

        for idx, r in df_m.iterrows():
            dt_ent_br = datetime.strptime(r['data_entrada'], '%Y-%m-%d').strftime('%d/%m/%Y')
            status = "✅" if r['servico'] else "⏳"
            # Soma do total para o título/resumo
            ct_ida_raw = r['custo_transporte_ida'] if 'custo_transporte_ida' in r else 0
            ct_ret_raw = r['custo_transporte_retorno'] if 'custo_transporte_retorno' in r else 0
            ct_leg_raw = r['custo_transporte'] if 'custo_transporte' in r else 0
            ct_ida = 0.0 if pd.isna(ct_ida_raw) else float(ct_ida_raw)
            ct_ret = 0.0 if pd.isna(ct_ret_raw) else float(ct_ret_raw)
            ct_leg = 0.0 if pd.isna(ct_leg_raw) else float(ct_leg_raw)
            if ct_ida == 0.0 and ct_ret == 0.0 and ct_leg > 0.0:
                ct_ida = ct_leg
            valor_total_serv = (r['valor_mo'] or 0) + (r['valor_pecas'] or 0) + ct_ida + ct_ret
            
            titulo = f"{status} | {dt_ent_br} | {r['veiculo_placa']} | Fornecedor: {r['fornecedor_nome']} | Total: {brl(valor_total_serv)}"
            
            with st.expander(titulo):
                ed_key = f"edit_full_{r['id']}"
                if ed_key not in st.session_state: st.session_state[ed_key] = False

                if not st.session_state[ed_key]:
                    # --- MODO VISUALIZAÇÃO ---
                    col_v1, col_v2, col_v3 = st.columns(3)
                    col_v1.write(f"**Fornecedor:** {r['fornecedor_nome']}")
                    col_v1.write(f"**KM Entrada:** {r['km_servico']:,}".replace(",", "."))
                    
                    col_v2.write(f"**Nº NF:** {r['num_nf'] or '-'}")
                    if r['data_fim']:
                        dt_saida_br = datetime.strptime(r['data_fim'], '%Y-%m-%d').strftime('%d/%m/%Y')
                        col_v2.write(f"**Data Saída:** {dt_saida_br}")
                    
                    if r['data_vencimento_garantia']:
                        dt_gar_br = datetime.strptime(r['data_vencimento_garantia'], '%Y-%m-%d').strftime('%d/%m/%Y')
                        col_v3.write(f"**🛡️ Garantia até:** {dt_gar_br}")

                    st.divider()
                    ca, cb = st.columns(2)
                    ca.info(f"📋 **Defeito Relatado:**\n\n{r['defeito']}")
                    cb.success(f"🔧 **Serviço Realizado:**\n\n{r['servico'] or 'Pendente'}")
                    
                    if r['observacao_adicional']:
                        st.warning(f"📝 **Observação:** {r['observacao_adicional']}")

                    with conn() as c:
                        pecas_manut = c.execute(
                            """SELECT mp.id, f.codigo, f.nome, mp.data_compra, mp.num_nf, mp.descricao_peca, mp.valor_peca
                               FROM manutencoes_pecas mp
                               LEFT JOIN fornecedores f ON f.id = mp.fornecedor_id
                               WHERE mp.manutencao_id=?
                               ORDER BY mp.id ASC""",
                            (int(r["id"]),),
                        ).fetchall()
                        anexos_manut = c.execute(
                            """SELECT id, nome_arquivo, caminho_arquivo
                               FROM manutencoes_anexos
                               WHERE manutencao_id=?
                               ORDER BY id DESC""",
                            (int(r["id"]),),
                        ).fetchall()
                    if anexos_manut:
                        st.markdown("**📎 Pedidos do Fornecedor (Anexos):**")
                        for anexo in anexos_manut:
                            caminho_anexo = str(anexo["caminho_arquivo"] or "").strip()
                            if not caminho_anexo:
                                continue
                            path_anexo = Path(caminho_anexo)
                            nome_anexo = anexo["nome_arquivo"] or path_anexo.name
                            a_col1, a_col2 = st.columns([4, 1])
                            if path_anexo.exists():
                                with path_anexo.open("rb") as f_anexo:
                                    a_col1.download_button(
                                        f"Baixar: {nome_anexo}",
                                        data=f_anexo.read(),
                                        file_name=nome_anexo,
                                        mime="application/octet-stream",
                                        key=f"btn_manut_download_pedido_{r['id']}_{anexo['id']}",
                                    )
                            else:
                                a_col1.info(f"Anexo não encontrado no disco: {nome_anexo}")
                            if a_col2.button("🗑️ Excluir", key=f"btn_manut_excluir_anexo_{r['id']}_{anexo['id']}"):
                                with conn() as c:
                                    c.execute("DELETE FROM manutencoes_anexos WHERE id=?", (int(anexo["id"]),))
                                if path_anexo.exists():
                                    path_anexo.unlink()
                                st.success(f"Anexo excluído: {nome_anexo}")
                                st.rerun()
                    else:
                        pedido_path_legacy = str(r.get("pedido_fornecedor_arquivo") or "").strip()
                        if pedido_path_legacy:
                            path_pedido_legacy = Path(pedido_path_legacy)
                            if path_pedido_legacy.exists():
                                with path_pedido_legacy.open("rb") as f_pedido_legacy:
                                    st.download_button(
                                        "📎 Baixar Pedido do Fornecedor",
                                        data=f_pedido_legacy.read(),
                                        file_name=path_pedido_legacy.name,
                                        mime="application/octet-stream",
                                        key=f"btn_manut_download_pedido_legacy_{r['id']}",
                                    )
                            else:
                                st.info(f"Pedido do fornecedor registrado: {pedido_path_legacy}")

                    if pecas_manut:
                        st.markdown("**Peças compradas de fornecedores:**")
                        dados_pecas = []
                        for peca in pecas_manut:
                            data_compra_br = ""
                            if peca["data_compra"]:
                                data_compra_br = datetime.strptime(peca["data_compra"], "%Y-%m-%d").strftime("%d/%m/%Y")
                            dados_pecas.append(
                                {
                                    "Fornecedor": f"{peca['codigo']} - {peca['nome']}" if peca["codigo"] and peca["nome"] else "-",
                                    "Data Compra": data_compra_br,
                                    "N. NF": peca["num_nf"] or "",
                                    "Descrição da Peça": peca["descricao_peca"] or "",
                                    "Valor": brl(peca["valor_peca"] or 0),
                                }
                            )
                        st.dataframe(pd.DataFrame(dados_pecas), hide_index=True, use_container_width=True)

                        if f"manut_peca_excluir_id_{int(r['id'])}" not in st.session_state:
                            st.session_state[f"manut_peca_excluir_id_{int(r['id'])}"] = None

                        mapa_pecas_excluir = {}
                        for peca in pecas_manut:
                            fornecedor_peca = f"{peca['codigo']} - {peca['nome']}" if peca["codigo"] and peca["nome"] else "-"
                            label_peca = (
                                f"ID {int(peca['id'])} | {fornecedor_peca} | "
                                f"NF: {peca['num_nf'] or '-'} | {peca['descricao_peca'] or '-'} | "
                                f"{brl(peca['valor_peca'] or 0)}"
                            )
                            mapa_pecas_excluir[label_peca] = int(peca["id"])

                        peca_sel_excluir = st.selectbox(
                            "Excluir item de peça comprada",
                            options=list(mapa_pecas_excluir.keys()),
                            index=None,
                            placeholder="Selecione a peça",
                            key=f"sel_excluir_peca_manut_{r['id']}",
                        )
                        peca_col1, peca_col2 = st.columns(2)
                        if peca_col1.button("🗑️ Excluir Peça Selecionada", type="primary", use_container_width=True, key=f"btn_excluir_peca_manut_{r['id']}"):
                            if peca_sel_excluir:
                                st.session_state[f"manut_peca_excluir_id_{int(r['id'])}"] = mapa_pecas_excluir[peca_sel_excluir]
                                st.rerun()
                            else:
                                st.warning("Selecione uma peça para excluir.")

                        peca_excluir_id = st.session_state.get(f"manut_peca_excluir_id_{int(r['id'])}")
                        if peca_excluir_id is not None:
                            st.warning(f"Confirma a exclusão da peça ID {int(peca_excluir_id)}?")
                            conf_peca1, conf_peca2 = st.columns(2)
                            if conf_peca1.button("✅ Confirmar exclusão da peça", type="primary", use_container_width=True, key=f"btn_conf_excluir_peca_manut_{r['id']}"):
                                excluir_peca_manutencao(int(peca_excluir_id))
                                st.session_state[f"manut_peca_excluir_id_{int(r['id'])}"] = None
                                st.success("Peça excluída com sucesso.")
                                st.rerun()
                            if conf_peca2.button("❌ Cancelar", use_container_width=True, key=f"btn_cancel_excluir_peca_manut_{r['id']}"):
                                st.session_state[f"manut_peca_excluir_id_{int(r['id'])}"] = None
                                st.rerun()
                    
                    # Exibição Financeira Detalhada
                    st.markdown(f"""
                    **Detalhamento Financeiro:**
                    * Mão de Obra: {brl(r['valor_mo'] or 0)}
                    * Peças: {brl(r['valor_pecas'] or 0)}
                    * Custo Transp Ida: {brl(ct_ida)}
                    * Custo Transporte Retorno: {brl(ct_ret)}
                    * **TOTAL DO SERVIÇO: {brl(valor_total_serv)}**
                    """)
                    
                    c_b1, c_b2 = st.columns(2)
                    if c_b1.button("✏️ EDITAR TUDO", key=f"btn_ed_f_{r['id']}", use_container_width=True):
                        st.session_state[ed_key] = True
                        st.rerun()
                    if c_b2.button("🗑️ EXCLUIR", key=f"btn_ex_f_{r['id']}", type="primary", use_container_width=True):
                        st.session_state.manut_excluir_mov_id = int(r["id"])
                        st.rerun()
                else:
                    # --- MODO EDIÇÃO COMPLETA ---
                    with st.form(f"form_edit_total_{r['id']}"):
                        st.markdown("**1️⃣ Dados de Entrada**")
                        e_c1, e_c2, e_c3 = st.columns(3)
                        val_dt_e = datetime.strptime(r['data_entrada'], '%Y-%m-%d').date() if r['data_entrada'] else datetime.now().date()
                        ed_dt_e = e_c1.date_input("Data Entrada", value=val_dt_e, format="DD/MM/YYYY")
                        ed_os = e_c2.text_input("Nº O.S.", value=r['num_os'] or "")
                        ed_km = e_c3.number_input("KM Entrada", value=float(r['km_servico'] or 0))
                        ed_def = st.text_area("Defeito Relatado", value=r['defeito'])
                        
                        st.divider()
                        st.markdown("**2️⃣ Dados de Finalização**")
                        e_c4, e_c5, e_c6 = st.columns(3)
                        val_dt_f = datetime.strptime(r['data_fim'], '%Y-%m-%d').date() if r['data_fim'] else datetime.now().date()
                        ed_dt_f = e_c4.date_input("Data Saída", value=val_dt_f, format="DD/MM/YYYY")
                        ed_nf = e_c5.text_input("Nº N.F.", value=r['num_nf'] or "")
                        val_dt_g = datetime.strptime(r['data_vencimento_garantia'], '%Y-%m-%d').date() if r['data_vencimento_garantia'] else datetime.now().date()
                        ed_dt_g = e_c6.date_input("Vencimento Garantia", value=val_dt_g, format="DD/MM/YYYY")
                        
                        ed_serv = st.text_area("Serviço Realizado", value=r['servico'] or "")
                        ed_obs = st.text_area("Observações Adicionais", value=r['observacao_adicional'] or "")
                        ed_pedido_fornecedor_files = st.file_uploader(
                            "Adicionar Pedido(s) do Fornecedor (opcional)",
                            type=["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx", "xls", "xlsx"],
                            accept_multiple_files=True,
                            key=f"manut_edit_pedido_upload_{r['id']}",
                        )

                        fornecedores_pecas_edit, mapa_fornecedores_pecas_edit = carregar_fornecedores_para_pecas()
                        st.markdown("**Peças compradas de fornecedores**")
                        if not fornecedores_pecas_edit:
                            st.warning("Cadastre fornecedores na aba Fornecedores para lançar peças.")
                        df_pecas_edit = st.data_editor(
                            carregar_pecas_manutencao(int(r["id"])),
                            key=f"editor_pecas_editar_{r['id']}",
                            hide_index=True,
                            use_container_width=True,
                            num_rows="dynamic",
                            column_config={
                                "Fornecedor": st.column_config.SelectboxColumn("Nome do Fornecedor", options=fornecedores_pecas_edit),
                                "Data Compra": st.column_config.DateColumn("Data Compra", format="DD/MM/YYYY"),
                                "N. NF": st.column_config.TextColumn("N. NF"),
                                "Descricao da Peca": st.column_config.TextColumn("Descrição da Peça"),
                                "Valor da Peca": st.column_config.NumberColumn("Valor da Peça", min_value=0.0, step=0.01, format="R$ %.2f"),
                                "Excluir": st.column_config.CheckboxColumn("🗑️ Excluir"),
                            },
                        )
                        itens_pecas_edit, ed_pe, erro_pecas_edit = preparar_pecas_manutencao(df_pecas_edit, mapa_fornecedores_pecas_edit)
                        
                        valor_ct_ida_edit = 0.0 if pd.isna(ct_ida_raw) else float(ct_ida_raw)
                        valor_ct_ret_edit = 0.0 if pd.isna(ct_ret_raw) else float(ct_ret_raw)
                        if valor_ct_ida_edit == 0.0 and valor_ct_ret_edit == 0.0 and ct_leg > 0.0:
                            valor_ct_ida_edit = ct_leg

                        e_c7, e_c8, e_c9, e_c10 = st.columns(4)
                        ed_mo = e_c7.number_input("Valor Mão de Obra", value=float(r['valor_mo'] or 0))
                        e_c8.metric("Valor Peças", brl(ed_pe))
                        ed_ct_ida = e_c9.number_input("Custo Transp Ida", value=valor_ct_ida_edit)
                        ed_ct_ret = e_c10.number_input("Custo Transporte Retorno", value=valor_ct_ret_edit)
                        
                        # Total dinâmico na edição
                        st.warning(f"💰 **Novo Total Calculado: {brl(ed_mo + ed_pe + ed_ct_ida + ed_ct_ret)}**")

                        b_col1, b_col2 = st.columns(2)
                        if b_col1.form_submit_button("💾 Gravar", use_container_width=True, key=f"btn_manut_editar_gravar_{r['id']}"):
                            if erro_pecas_edit:
                                st.warning(erro_pecas_edit)
                                st.stop()
                            novos_anexos = salvar_anexos_pedido_fornecedor(ed_pedido_fornecedor_files)
                            pedido_fornecedor_atual = str(r.get("pedido_fornecedor_arquivo") or "").strip() or None
                            pedido_fornecedor_final = (novos_anexos[0]["caminho_arquivo"] if novos_anexos else pedido_fornecedor_atual)
                            with conn() as c:
                                c.execute("""UPDATE manutencoes SET 
                                             data_entrada=?, num_os=?, km_servico=?, defeito=?,
                                             data_fim=?, num_nf=?, servico=?, valor_mo=?, valor_pecas=?, custo_transporte_ida=?, custo_transporte_retorno=?,
                                             data_vencimento_garantia=?, observacao_adicional=?, pedido_fornecedor_arquivo=?
                                             WHERE id=?""", 
                                         (ed_dt_e.isoformat(), ed_os, ed_km, ed_def,
                                           ed_dt_f.isoformat(), ed_nf, ed_serv, ed_mo, ed_pe, ed_ct_ida, ed_ct_ret,
                                           ed_dt_g.isoformat(), ed_obs, pedido_fornecedor_final, r['id']))
                                salvar_pecas_manutencao(c, int(r["id"]), itens_pecas_edit)
                                for caminho in novos_anexos:
                                    c.execute(
                                        """INSERT INTO manutencoes_anexos (manutencao_id, nome_arquivo, caminho_arquivo, data_inclusao)
                                           VALUES (?, ?, ?, ?)""",
                                        (int(r["id"]), caminho["nome_arquivo"], caminho["caminho_arquivo"], datetime.now().isoformat()),
                                    )
                            st.session_state[ed_key] = False
                            st.rerun()
                        if b_col2.form_submit_button("❌ CANCELAR", use_container_width=True):
                            st.session_state[ed_key] = False
                            st.rerun()

with aba5:
    st.subheader("🏢 Gestão de Oficinas")
    if "oficina_editando" not in st.session_state:
        st.session_state.oficina_editando = False
    
    with st.expander("➕ Cadastro de Oficina", expanded=False):
        # --- FORMULÁRIO DE CADASTRO ---
        with st.form("f_of", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            n_o = c1.text_input("Nome da Oficina")
            cnpj_o = c2.text_input("CNPJ")
            ie_o = c3.text_input("Inscrição Estadual")
            e_o = c1.text_input("Endereço")
            num_o = c2.text_input("Numero")
            compl_o = c3.text_input("Complemento")
            b_o = c1.text_input("Bairro")
            ci_o = c2.text_input("Cidade")
            uf_o = c3.text_input("Estado")
            cep_o = c1.text_input("CEP")
            tel_o = c2.text_input("Telefone Contato")
            email_o = c3.text_input("E-mail")
            r_o = c1.text_input("Responsável / Contato")
            
            if st.form_submit_button("💾 Gravar", key="btn_oficina_cadastro_gravar"):
                if n_o:
                    with conn() as c:
                        c.execute("""INSERT INTO oficinas (
                                        nome, cnpj, endereco, numero, complemento, bairro, cidade, estado, cep,
                                        inscricao_estadual, telefone_contato, email, responsavel
                                     ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                 (n_o, cnpj_o, e_o, num_o, compl_o, b_o, ci_o, uf_o, cep_o, ie_o, tel_o, email_o, r_o))
                    alerta_gravado()
                    st.rerun()
                else:
                    st.error("O nome da oficina é obrigatório.")

    st.markdown("---")
    st.markdown("### 📋 Editar ou Excluir Oficinas")

    # --- TABELA DE EDIÇÃO E EXCLUSÃO ---
    with conn() as c:
        df_oficinas = pd.read_sql("SELECT * FROM oficinas ORDER BY nome ASC", c)

    if not df_oficinas.empty:
        st.button("✏️ Editar", key="btn_oficina_editar", use_container_width=True)
        df_oficinas["Excluir"] = False
        df_ed_of = st.data_editor(
            df_oficinas,
            key="editor_oficinas_art",
            column_config={
                "id": None,
                "Excluir": st.column_config.CheckboxColumn("🗑️"),
                "nome": st.column_config.TextColumn("Nome", width="medium"),
                "cnpj": "CNPJ",
                "inscricao_estadual": "Inscrição Estadual",
                "endereco": "Endereço",
                "numero": "Numero",
                "complemento": "Complemento",
                "bairro": "Bairro",
                "cidade": "Cidade",
                "estado": "Estado",
                "cep": "CEP",
                "telefone_contato": "Telefone Contato",
                "email": "E-mail",
                "responsavel": "Responsável"
            },
            hide_index=True,
            use_container_width=True
        )
        col_btn1, col_btn2 = st.columns(2)
        if col_btn1.button("💾 Gravar", key="btn_save_oficina"):
            with conn() as c:
                for _, r in df_ed_of.iterrows():
                    if not r["Excluir"]:
                        c.execute("""UPDATE oficinas SET 
                                     nome=?, cnpj=?, inscricao_estadual=?, endereco=?, numero=?, complemento=?, bairro=?, cidade=?, estado=?, cep=?, telefone_contato=?, email=?, responsavel=? 
                                     WHERE id=?""", 
                                  (r["nome"], r["cnpj"], r["inscricao_estadual"], r["endereco"], r["numero"], r["complemento"], r["bairro"], r["cidade"], r["estado"], r["cep"], r["telefone_contato"], r["email"], r["responsavel"], r["id"]))
            alerta_gravado()
            st.rerun()
        if col_btn2.button("🔴 Excluir Oficinas Selecionadas", type="primary", key="btn_del_oficina"):
            ids_para_excluir = df_ed_of[df_ed_of["Excluir"] == True]["id"].tolist()
            if ids_para_excluir:
                with conn() as c:
                    for id_of in ids_para_excluir:
                        c.execute("DELETE FROM oficinas WHERE id=?", (id_of,))
                st.warning("Oficinas removidas.")
                st.rerun()
            else:
                st.info("Marque a lixeira (🗑️) das oficinas que deseja remover.")
    else:
        st.info("Nenhuma oficina cadastrada no sistema.")


with aba6:
    st.subheader("🏙️ Cidades")
    if "cidade_editando" not in st.session_state:
        st.session_state.cidade_editando = False
    if "msg_cidades" in st.session_state:
        st.success(st.session_state.pop("msg_cidades"))

    with st.form("cid_f", clear_on_submit=True):
        nc = st.text_input("Nome da Cidade").strip().upper()
        if st.form_submit_button("💾 Gravar", key="btn_cidade_cadastro_gravar"):
            if not nc:
                st.warning("Informe o nome da cidade.")
            else:
                with conn() as c:
                    cur_cidade = c.execute("INSERT OR IGNORE INTO cidades (nome) VALUES (?)", (nc,))
                if cur_cidade.rowcount == 0:
                    st.warning("Essa cidade já está cadastrada.")
                else:
                    st.session_state.msg_cidades = "✅ Gravado com sucesso!"
                    st.rerun()

    with conn() as c:
        df_cidades = pd.read_sql("SELECT id, nome FROM cidades ORDER BY nome", c)

    if not df_cidades.empty:
        st.button("✏️ Editar", key="btn_cidade_editar", use_container_width=True)
        df_cidades["Editar"] = False
        with st.form("form_editar_cidades"):
            df_cidades_ed = st.data_editor(
                df_cidades,
                key="editor_cidades_art",
                hide_index=True,
                use_container_width=True,
                column_config={
                    "id": None,
                    "nome": st.column_config.TextColumn("Nome da Cidade"),
                    "Editar": st.column_config.CheckboxColumn("✏️ Editar", default=False),
                },
            )
            salvar_edicao_cidades = st.form_submit_button("💾 Gravar", use_container_width=True, key="btn_cidade_edicao_gravar")

        if salvar_edicao_cidades:
            selecionadas = df_cidades_ed[df_cidades_ed["Editar"] == True]
            if selecionadas.empty:
                st.warning("Marque pelo menos uma cidade na coluna Editar.")
            else:
                alteradas = 0
                erros = []
                with conn() as c:
                    nomes_atuais = {
                        int(r["id"]): str(r["nome"]).strip().upper()
                        for r in c.execute("SELECT id, nome FROM cidades").fetchall()
                    }
                    for _, row in selecionadas.iterrows():
                        cidade_id = int(row["id"])
                        nome_antigo = nomes_atuais.get(cidade_id, "")
                        nome_novo = str(row["nome"] or "").strip().upper()
                        if not nome_novo:
                            erros.append(f"ID {cidade_id}: nome vazio.")
                            continue
                        if nome_novo == nome_antigo:
                            continue
                        nome_duplicado = c.execute(
                            "SELECT 1 FROM cidades WHERE nome=? AND id<>?",
                            (nome_novo, cidade_id),
                        ).fetchone()
                        if nome_duplicado:
                            erros.append(f"{nome_novo}: já existe outra cidade com esse nome.")
                            continue
                        c.execute("UPDATE cidades SET nome=? WHERE id=?", (nome_novo, cidade_id))
                        c.execute("UPDATE rotas SET origem=? WHERE origem=?", (nome_novo, nome_antigo))
                        c.execute("UPDATE rotas SET destino=? WHERE destino=?", (nome_novo, nome_antigo))
                        c.execute("UPDATE viagens SET origem=? WHERE origem=?", (nome_novo, nome_antigo))
                        c.execute("UPDATE viagens SET destino=? WHERE destino=?", (nome_novo, nome_antigo))
                        alteradas += 1
                if alteradas:
                    st.session_state.msg_cidades = "✅ Gravado com sucesso!"
                    st.rerun()
                if erros:
                    st.warning(" | ".join(erros))
    else:
        st.info("Nenhuma cidade cadastrada.")

with aba7:
    st.subheader("🛣️ Gestão de Rotas (KM e Valores)")
    if "rota_editando" not in st.session_state:
        st.session_state.rota_editando = False
    
    # 1. FORMULÁRIO PARA NOVO CADASTRO
    with st.expander("➕ Cadastrar Nova Rota", expanded=False):
        with st.form("rota_f", clear_on_submit=True):
            c1, c2, c3, c4, c5 = st.columns(5)
            o = c1.selectbox("Origem", lista_cidades, key="orig_rota")
            d = c2.selectbox("Destino", lista_cidades, key="dest_rota")
            k = c3.number_input("Distância (KM)", min_value=0.0, step=0.1)
            v = c4.number_input("Valor por Tonelada (R$)", min_value=0.0, step=0.01)
            vk = c5.number_input("Valor por KM (R$)", min_value=0.0, step=0.01)
            ce1, ce2 = st.columns(2)
            nome_empresa_origem = ce1.text_input("Nome Empresa Origem", key="rota_nome_empresa_origem").strip()
            nome_empresa_destino = ce2.text_input("Nome Empresa Destino", key="rota_nome_empresa_destino").strip()
            
            if st.form_submit_button("💾 Gravar", key="btn_rota_cadastro_gravar"):
                if o == d:
                    st.error("Origem e Destino não podem ser iguais.")
                elif float(v) == 0.0 and float(vk) == 0.0:
                    st.warning("VALOR NULO")
                elif o and d:
                    with conn() as c:
                        c.execute(
                            """INSERT OR REPLACE INTO rotas
                               (origem, destino, nome_empresa_origem, nome_empresa_destino, km, valor_ton, valor_km)
                               VALUES (?,?,?,?,?,?,?)""",
                            (o, d, nome_empresa_origem, nome_empresa_destino, k, v, vk),
                        )
                    alerta_gravado()

    st.markdown("---")
    st.markdown("### 📋 Lista de Rotas Cadastradas")

    # 2. BUSCA DOS DADOS
    with conn() as c:
        df_rotas = pd.read_sql("SELECT * FROM rotas ORDER BY origem, destino", c)

    if not df_rotas.empty:
        if "valor_km" not in df_rotas.columns:
            df_rotas["valor_km"] = 0.0
        if "nome_empresa_origem" not in df_rotas.columns:
            df_rotas["nome_empresa_origem"] = ""
        if "nome_empresa_destino" not in df_rotas.columns:
            df_rotas["nome_empresa_destino"] = ""

        st.markdown("### 📈 Comparativo de Lucratividade das Rotas")
        df_cmp_rotas = df_rotas.copy()
        df_cmp_rotas["km"] = pd.to_numeric(df_cmp_rotas["km"], errors="coerce").fillna(0.0)
        df_cmp_rotas["valor_ton"] = pd.to_numeric(df_cmp_rotas["valor_ton"], errors="coerce").fillna(0.0)
        df_cmp_rotas["valor_km"] = pd.to_numeric(df_cmp_rotas["valor_km"], errors="coerce").fillna(0.0)
        df_cmp_rotas["rota"] = (
            df_cmp_rotas["origem"].fillna("").astype(str).str.strip()
            + " → "
            + df_cmp_rotas["destino"].fillna("").astype(str).str.strip()
        )
        df_cmp_rotas["indice_ton_por_km"] = (
            df_cmp_rotas["valor_ton"] / df_cmp_rotas["km"].where(df_cmp_rotas["km"] > 0)
        ).fillna(0.0)
        df_cmp_rotas["indice_lucratividade_ref"] = df_cmp_rotas["valor_km"]
        sem_valor_km = df_cmp_rotas["indice_lucratividade_ref"] <= 0
        df_cmp_rotas.loc[sem_valor_km, "indice_lucratividade_ref"] = df_cmp_rotas.loc[sem_valor_km, "indice_ton_por_km"]

        top_km = df_cmp_rotas.sort_values("valor_km", ascending=False).head(1)
        top_ton = df_cmp_rotas.sort_values("valor_ton", ascending=False).head(1)
        top_ref = df_cmp_rotas.sort_values("indice_lucratividade_ref", ascending=False).head(1)

        c_cmp1, c_cmp2, c_cmp3 = st.columns(3)
        if not top_km.empty:
            r_km = top_km.iloc[0]
            c_cmp1.metric("Mais Lucrativa por Valor/KM", brl(float(r_km["valor_km"])) + "/km", str(r_km["rota"]))
        if not top_ton.empty:
            r_ton = top_ton.iloc[0]
            c_cmp2.metric("Mais Lucrativa por Valor/Ton", brl(float(r_ton["valor_ton"])) + "/ton", str(r_ton["rota"]))
        if not top_ref.empty:
            r_ref = top_ref.iloc[0]
            c_cmp3.metric(
                "Melhor Índice de Referência",
                brl(float(r_ref["indice_lucratividade_ref"])) + "/km",
                str(r_ref["rota"]),
            )

        st.caption(
            "Comparativo usando apenas KM Rotas: Valor/KM, Valor/Ton e índice técnico Valor/Ton por KM (quando não houver Valor/KM)."
        )
        st.dataframe(
            df_cmp_rotas[
                [
                    "rota",
                    "km",
                    "valor_ton",
                    "valor_km",
                    "indice_ton_por_km",
                    "indice_lucratividade_ref",
                ]
            ].sort_values("indice_lucratividade_ref", ascending=False),
            hide_index=True,
            use_container_width=True,
            column_config={
                "rota": "Rota",
                "km": st.column_config.NumberColumn("KM", format="%.1f"),
                "valor_ton": st.column_config.NumberColumn("Valor/Ton", format="R$ %.2f"),
                "valor_km": st.column_config.NumberColumn("Valor/KM", format="R$ %.2f"),
                "indice_ton_por_km": st.column_config.NumberColumn("Índice Ton/KM", format="R$ %.4f"),
                "indice_lucratividade_ref": st.column_config.NumberColumn("Índice Lucratividade", format="R$ %.4f"),
            },
        )
        st.markdown("---")

        st.button("✏️ Editar", key="btn_rota_editar", use_container_width=True)
        df_rotas["Excluir"] = False
        df_ed_rotas = st.data_editor(
            df_rotas,
            key="editor_rotas_art_v2",
            column_config={
                "id": None, 
                "Excluir": st.column_config.CheckboxColumn("🗑️"),
                "origem": st.column_config.TextColumn("Origem", disabled=True),
                "destino": st.column_config.TextColumn("Destino", disabled=True),
                "nome_empresa_origem": st.column_config.TextColumn("Nome Empresa Origem"),
                "nome_empresa_destino": st.column_config.TextColumn("Nome Empresa Destino"),
                "km": st.column_config.NumberColumn("KM", format="%.1f"),
                "valor_ton": st.column_config.NumberColumn("Valor/Ton", format="R$ %.2f"),
                "valor_km": st.column_config.NumberColumn("Valor/KM", format="R$ %.2f")
            },
            hide_index=True,
            use_container_width=True
        )
        col_r1, col_r2 = st.columns(2)
        if col_r1.button("💾 Gravar", key="btn_save_rotas", use_container_width=True):
            rotas_valor_nulo = df_ed_rotas[
                (pd.to_numeric(df_ed_rotas["valor_ton"], errors="coerce").fillna(0.0) == 0.0) &
                (pd.to_numeric(df_ed_rotas["valor_km"], errors="coerce").fillna(0.0) == 0.0) &
                (df_ed_rotas["Excluir"] != True)
            ]
            if not rotas_valor_nulo.empty:
                st.warning("Existem rotas com valor zerado. Os nomes de empresa e demais alterações serão gravados mesmo assim.")
            with conn() as c:
                for _, r in df_ed_rotas.iterrows():
                    if not r["Excluir"]:
                        nome_emp_origem = "" if pd.isna(r.get("nome_empresa_origem")) else str(r.get("nome_empresa_origem")).strip()
                        nome_emp_destino = "" if pd.isna(r.get("nome_empresa_destino")) else str(r.get("nome_empresa_destino")).strip()
                        c.execute(
                            """UPDATE rotas
                               SET nome_empresa_origem=?, nome_empresa_destino=?, km=?, valor_ton=?, valor_km=?
                               WHERE id=?""",
                            (
                                nome_emp_origem,
                                nome_emp_destino,
                                r["km"],
                                r["valor_ton"],
                                r["valor_km"],
                                r["id"],
                            ),
                        )
            alerta_gravado()
        if col_r2.button("🔴 Excluir Selecionados", type="primary", key="btn_del_rotas", use_container_width=True):
            itens_para_excluir = df_ed_rotas[df_ed_rotas["Excluir"] == True]
            if not itens_para_excluir.empty:
                com_erro = []
                sucesso_count = 0
                with conn() as c:
                    for _, rota in itens_para_excluir.iterrows():
                        check = c.execute(
                            """SELECT COUNT(*) as total FROM viagens 
                               WHERE (origem = ? AND destino = ?) 
                               OR (origem = ? AND destino = ?)""",
                            (rota['origem'], rota['destino'], rota['destino'], rota['origem'])
                        ).fetchone()
                        if check['total'] > 0:
                            com_erro.append(f"{rota['origem']} x {rota['destino']}")
                        else:
                            c.execute("DELETE FROM rotas WHERE id=?", (rota['id'],))
                            sucesso_count += 1
                if com_erro:
                    st.error(f"⚠️ **Não foi possível excluir as seguintes rotas:** {', '.join(com_erro)}. \n\nMotivo: Existem fretes cadastrados usando estas rotas no histórico.")
                if sucesso_count > 0:
                    st.success(f"✅ {sucesso_count} rota(s) excluída(s com sucesso!")
                    st.rerun()
            else:
                st.info("Selecione a lixeira (🗑️) para excluir.")
    else:
        st.info("Nenhuma rota cadastrada.")
with aba8:
    st.subheader("📑 Relatório para Impressão")
    st.markdown(
        """
        <style>
        /* Padroniza tamanho dos números dos cards do relatório */
        div[data-testid="stMetricValue"] {
            font-size: 1.55rem !important;
            line-height: 1.1 !important;
            white-space: nowrap !important;
        }
        div[data-testid="stMetricLabel"] {
            font-size: 0.90rem !important;
            white-space: nowrap !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )
    fixo_db = valor_mensal_rateado_periodo("motora_fixo", filtro_ini, filtro_fim)
    
    if not df_db.empty:
        # Cálculos de apoio
        if "qtd_viagens" in df_db.columns:
            qtd_rel = pd.to_numeric(df_db["qtd_viagens"], errors="coerce").fillna(1.0)
            qtd_rel = qtd_rel.apply(lambda x: max(1, int(round(float(x)))))
        else:
            qtd_rel = pd.Series(1, index=df_db.index, dtype=int)
        pagto_estadia_rel = pd.to_numeric(df_db.get("pagto_estadia", 0.0), errors="coerce").fillna(0.0)
        adicional_frete_rel = pd.to_numeric(df_db.get("valor_adicional_frete", 0.0), errors="coerce").fillna(0.0)
        total_estadia_rel = float((pagto_estadia_rel * qtd_rel).sum())
        pagamento_estadia_10_rel = total_estadia_rel * 0.10
        total_adicional_frete_rel = float((adicional_frete_rel * qtd_rel).sum())
        total_frete_rel = (
            (pd.to_numeric(df_db["Total Frete"], errors="coerce").fillna(0.0) + pagto_estadia_rel + adicional_frete_rel) * qtd_rel
        )
        df_rel_param = df_db.copy()
        df_rel_param["total_frete_rel"] = total_frete_rel
        df_rel_param = aplicar_parametros_por_data(df_rel_param, col_data="data")
        total_f = float(total_frete_rel.sum())
        frete_fixo_periodo_rel = frete_fixo_rateado_periodo(filtro_ini, filtro_fim)
        total_f += frete_fixo_periodo_rel
        pct_parametros_rel = pd.to_numeric(df_rel_param["param_motora_pct"], errors="coerce").dropna().tolist()
        pct_frete_fixo_rel = serie_parametro_diaria("motora_pct", filtro_ini, filtro_fim).dropna().tolist()
        pct_comissao_rel = pct_parametro_relatorio(pct_parametros_rel + pct_frete_fixo_rel)
        pct_db_txt = format_pct_parametros(pct_parametros_rel + pct_frete_fixo_rel)
        base_comissionavel_total = float(total_f)
        v_comis = base_comissionavel_total * (pct_comissao_rel / 100.0)
        total_pg = v_comis + fixo_db + pagamento_estadia_10_rel
        qtde_fretes = int(qtd_rel.sum())

        # Visualização no Streamlit
        m0, m1, m2, m3, m4, m5 = st.columns(6)
        m0.metric("Qtd. Fretes", qtde_fretes)
        m1.metric("Total Fretes", brl(total_f))
        m2.metric(f"Comissão ({pct_db_txt})", brl(v_comis))
        m3.metric("Total a Pagar", brl(total_pg))
        m4.metric("Pagamento Estadia 10%", brl(pagamento_estadia_10_rel))
        m5.metric("Adicional Frete", brl(total_adicional_frete_rel))
        st.caption(f"Salário fixo do motorista: {brl(fixo_db)}")
        st.caption(
            "Base da comissão = Total Fretes. "
            f"Base comissionável no período: {brl(base_comissionavel_total)}"
        )
        
        if st.button("🔍 PREPARAR IMPRESSÃO", use_container_width=True, key="btn_print_rel_final"):
            # Início do HTML
            html = f"""
            <html>
            <head>
                <style>
                    body {{ font-family: sans-serif; margin: 30px; color: #333; }}
                    header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 12px; }}
                    th, td {{ border: 1px solid #999; padding: 8px; text-align: left; }}
                    th {{ background-color: #f2f2f2; }}
                    .container-resumo {{ margin-top: 30px; display: flex; justify-content: flex-end; }}
                    .tot {{ width: 350px; border: 1px solid #ccc; padding: 15px; background: #fafafa; }}
                    .ln {{ display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 14px; }}
                    .fin {{ font-weight: bold; font-size: 18px; border-top: 2px solid #2e7d32; color: #2e7d32; padding-top: 10px; margin-top: 10px; }}
                    .assinatura-box {{ margin-top: 80px; text-align: center; width: 400px; }}
                    .linha-assinatura {{ border-top: 1px solid #000; margin-bottom: 5px; }}
                    .btn-print {{ background: #007bff; color: white; padding: 15px; border: none; width: 100%; cursor: pointer; font-weight: bold; font-size: 16px; border-radius: 5px; }}
                    @media print {{ .btn-print {{ display: none; }} body {{ margin: 0; }} }}
                </style>
            </head>
            <body>
                <button class="btn-print" onclick="window.print()">🖨️ CLIQUE AQUI PARA IMPRIMIR ESTE RELATÓRIO</button>
                
                <header>
                    <h1 style="margin:0;">ART TRANSPORTES</h1>
                    <p style="margin:5px 0;">RELATÓRIO DE PRESTAÇÃO DE CONTAS</p>
                    <p>Período: <b>{filtro_ini.strftime('%d/%m/%Y')}</b> até <b>{filtro_fim.strftime('%d/%m/%Y')}</b></p>
                    <p style="margin:5px 0;">
                        Salário Fixo do Motorista: <b>{brl(fixo_db)}</b>
                        | Comissão: <b>{pct_db_txt}</b>
                        | Total a Pagar: <b>{brl(total_pg)}</b>
                    </p>
                </header>

                <table>
                    <thead>
                        <tr>
                            <th>Data</th>
                            <th>NF</th>
                            <th>Cliente</th>
                            <th>Rota (Origem x Destino)</th>
                            <th>Peso (Ton)</th>
                            <th>Valor Ton</th>
                            <th>Total Frete</th>
                        </tr>
                    </thead>
                    <tbody>
            """
            
            # Loop das linhas da tabela
            for i_rel, r in df_db.iterrows():
                qtd_linha = int(qtd_rel.loc[i_rel]) if i_rel in qtd_rel.index else 1
                pagto_estadia_linha = float(r.get("pagto_estadia") or 0.0)
                adicional_frete_linha = float(r.get("valor_adicional_frete") or 0.0)
                frete_base_linha = float(r.get("Total Frete") or 0.0)
                total_frete_linha = float((frete_base_linha + pagto_estadia_linha + adicional_frete_linha) * qtd_linha)
                html += f"""
                <tr>
                    <td>{r['data'].strftime('%d/%m/%Y')}</td>
                    <td>{r['nf'] or '-'}</td>
                    <td>{r['cliente']}</td>
                    <td>{r['origem']} x {r['destino']}</td>
                    <td>{r['toneladas']}</td>
                    <td>{brl(r['valor_ton'])}</td>
                    <td>{brl(total_frete_linha)}</td>
                </tr>
                """
            
            # Fechamento da tabela e inclusão ÚNICA do resumo e assinatura
            html += f"""
                    </tbody>
                </table>

                <div class="container-resumo">
                    <div class="tot">
                        <div class="ln"><span>Qtd. de Fretes:</span> <span>{qtde_fretes}</span></div>
                        <div class="ln"><span>Total Bruto:</span> <span>{brl(total_f)}</span></div>
                        <div class="ln"><span>Adicional Frete:</span> <span>{brl(total_adicional_frete_rel)}</span></div>
                        <div class="ln"><span>Base da Comissão:</span> <span>{brl(base_comissionavel_total)}</span></div>
                        <div class="ln"><span>Frete Fixo Rateado:</span> <span>{brl(frete_fixo_periodo_rel)}</span></div>
                        <div class="ln"><span>Comissão ({pct_db_txt}):</span> <span>{brl(v_comis)}</span></div>
                        <div class="ln"><span>Pagamento Estadia 10%:</span> <span>{brl(pagamento_estadia_10_rel)}</span></div>
                        <div class="ln"><span>Salário Fixo:</span> <span>{brl(fixo_db)}</span></div>
                        <div class="ln fin"><span>TOTAL A PAGAR:</span> <span>{brl(total_pg)}</span></div>
                    </div>
                </div>

                <div class="assinatura-box">
                    <div class="linha-assinatura"></div>
                    <p><b>Assinatura do Responsável / Motorista</b></p>
                    <p style="font-size: 10px; color: #666;">Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                </div>

                <script>
                    // Pequeno atraso para garantir que o layout carregue antes de abrir o print
                    setTimeout(function(){{ window.print(); }}, 700);
                </script>
            </body>
            </html>
            """
            components.html(html, height=1000, scrolling=True)
    else:
        st.warning("Não existem fretes lançados neste período para gerar o relatório.")


# ==================================================================================
# ABA 9 - ABASTECIMENTO (MÉDIA KM/L CORRIGIDA: DESCONSIDERA LITROS DO KM INICIAL)
# ==================================================================================
with aba9:
    st.subheader("⛽ Lançar Abastecimento")
    if "abastecimento_editando" not in st.session_state:
        st.session_state.abastecimento_editando = False
    with conn() as c:
        tipos_combustivel_db = [
            normalizar_tipo_combustivel(r["tipo_combustivel"])
            for r in c.execute(
                "SELECT DISTINCT tipo_combustivel FROM abastecimentos WHERE COALESCE(TRIM(tipo_combustivel), '') <> ''"
            ).fetchall()
        ]
    tipos_combustivel_sugeridos = sorted(set(["DIESEL", "ARLA"] + [t for t in tipos_combustivel_db if t]))
    opcoes_veiculos_abastecimento = lista_veiculos_full if lista_veiculos_full else apenas_placas
    index_veiculo_abastecimento = 0
    if placa_filtro_calculo and opcoes_veiculos_abastecimento:
        for idx_opt, opt_veic in enumerate(opcoes_veiculos_abastecimento):
            if str(opt_veic).split(" - ")[0].strip().upper() == str(placa_filtro_calculo).strip().upper():
                index_veiculo_abastecimento = idx_opt
                break
    if "abs_form_nonce" not in st.session_state:
        st.session_state["abs_form_nonce"] = 0

    params_ultimo_abs = []
    where_ultimo_abs = ""
    if placa_filtro_calculo:
        where_ultimo_abs = "WHERE UPPER(TRIM(veiculo_placa)) = ?"
        params_ultimo_abs.append(str(placa_filtro_calculo).strip().upper())
    with conn() as c:
        ultimo_abastecimento = c.execute(
            f"""SELECT *
                FROM abastecimentos
                {where_ultimo_abs}
                ORDER BY date(data) DESC, id DESC
                LIMIT 1""",
            params_ultimo_abs,
        ).fetchone()

    if ultimo_abastecimento:
        if st.button("📋 Replicar último cadastro", key="btn_replicar_ultimo_abastecimento", use_container_width=True):
            ultimo_abs = dict(ultimo_abastecimento)
            placa_ultimo_abs = str(ultimo_abs.get("veiculo_placa") or "").strip().upper()
            try:
                data_replicada_abs = pd.to_datetime(ultimo_abs.get("data"), errors="coerce").date()
            except Exception:
                data_replicada_abs = date.today()
            if pd.isna(data_replicada_abs):
                data_replicada_abs = date.today()
            st.session_state["abs_form_defaults"] = {
                "data": data_replicada_abs,
                "placa": placa_ultimo_abs,
                "local": str(ultimo_abs.get("local") or ""),
                "doc_nf": str(ultimo_abs.get("doc_nf") or ""),
                "km_inicial": float(ultimo_abs.get("km_inicial") or 0.0),
                "tipo_combustivel": normalizar_tipo_combustivel(ultimo_abs.get("tipo_combustivel")),
                "qtde_litros": float(ultimo_abs.get("qtde_litros") or 0.0),
                "valor_unit": float(ultimo_abs.get("valor_unit") or 0.0),
                "desconto": float(ultimo_abs.get("desconto") or 0.0),
            }
            st.session_state["abs_form_nonce"] += 1
            st.session_state["abs_expandir_cadastro"] = True
            st.session_state["abs_replicado_msg"] = "Último cadastro carregado no formulário. Altere o que precisar e clique em Gravar."
            st.rerun()
    else:
        st.caption("Nenhum abastecimento anterior para replicar.")

    if st.session_state.get("abs_sucesso_msg"):
        st.success(st.session_state.pop("abs_sucesso_msg"))
    if st.session_state.get("abs_replicado_msg"):
        st.info(st.session_state.pop("abs_replicado_msg"))

    abs_form_defaults = st.session_state.get("abs_form_defaults", {})
    abs_form_nonce = st.session_state.get("abs_form_nonce", 0)
    abs_data_default = abs_form_defaults.get("data", date.today())
    abs_placa_default = str(abs_form_defaults.get("placa") or "").strip().upper()
    abs_tem_defaults = bool(abs_form_defaults)
    index_veiculo_form = index_veiculo_abastecimento
    if abs_placa_default and opcoes_veiculos_abastecimento:
        for idx_opt, opt_veic in enumerate(opcoes_veiculos_abastecimento):
            if str(opt_veic).split(" - ")[0].strip().upper() == abs_placa_default:
                index_veiculo_form = idx_opt
                break

    expandir_cadastro_abs = st.session_state.pop("abs_expandir_cadastro", False)
    with st.expander("➕ Cadastro de Abastecimento", expanded=expandir_cadastro_abs):
        if st.session_state.get("abs_validacao_msg"):
            st.warning(st.session_state.pop("abs_validacao_msg"))

        with st.form("form_inclusao_abastecimento", clear_on_submit=False):
            col_a, col_b, col_c, col_d = st.columns(4)
            data_abs = col_a.date_input("Data", value=abs_data_default, format="DD/MM/YYYY", key=f"abs_data_incluir_{abs_form_nonce}")
            if opcoes_veiculos_abastecimento:
                veic_abs = col_b.selectbox(
                    "Veículo/Placa",
                    options=opcoes_veiculos_abastecimento,
                    index=index_veiculo_form,
                    key=f"abastecimento_veiculo_incluir_{abs_form_nonce}",
                )
                placa_abs = str(veic_abs).split(" - ")[0].strip()
            else:
                placa_abs = col_b.text_input("Placa", value=abs_placa_default, key=f"abs_placa_manual_incluir_{abs_form_nonce}").upper().strip()
            local_abs = col_c.text_input("Local do Abastecimento", value=str(abs_form_defaults.get("local") or ""), key=f"abs_local_incluir_{abs_form_nonce}")
            doc_nf_abs = col_d.text_input("Documento / NF", value=str(abs_form_defaults.get("doc_nf") or ""), key=f"abs_doc_nf_incluir_{abs_form_nonce}")

            col_e, col_f, col_g, col_h, col_i = st.columns(5)
            km_inicial_abs = col_e.number_input("KM Inicial", min_value=0.0, value=(float(abs_form_defaults.get("km_inicial") or 0.0) if abs_tem_defaults else None), step=1.0, key=f"abs_km_inicial_incluir_{abs_form_nonce}")
            tipo_default_abs = str(abs_form_defaults.get("tipo_combustivel") or "")
            tipo_cadastrado_index_abs = tipos_combustivel_sugeridos.index(tipo_default_abs) + 1 if tipo_default_abs in tipos_combustivel_sugeridos else 0
            tipo_cadastrado_abs = col_f.selectbox(
                "Tipo Cadastrado (opcional)",
                options=[""] + tipos_combustivel_sugeridos,
                index=tipo_cadastrado_index_abs,
                placeholder="Selecione um tipo já cadastrado",
                key=f"abs_tipo_cadastrado_incluir_{abs_form_nonce}",
            )
            tipo_comb_abs = col_f.text_input(
                "Tipo de Combustível",
                value=tipo_default_abs or tipo_cadastrado_abs,
                help="Você pode escolher um tipo já cadastrado acima ou digitar um novo aqui.",
                key=f"abs_tipo_comb_incluir_{abs_form_nonce}",
            ).strip()
            tipo_comb_normalizado_abs = normalizar_tipo_combustivel(tipo_cadastrado_abs or tipo_comb_abs)
            qtde_litros_abs = col_g.number_input("Qtde Litros", min_value=0.0, value=(float(abs_form_defaults.get("qtde_litros") or 0.0) if abs_tem_defaults else None), step=0.001, format="%.3f", key=f"abs_qtde_litros_incluir_{abs_form_nonce}")
            valor_unit_abs = col_h.number_input("Valor Unitário (R$)", min_value=0.0, value=(float(abs_form_defaults.get("valor_unit") or 0.0) if abs_tem_defaults else None), step=0.001, format="%.3f", key=f"abs_valor_unit_incluir_{abs_form_nonce}")
            desconto_abs = col_i.number_input("Desconto (R$)", min_value=0.0, value=(float(abs_form_defaults.get("desconto") or 0.0) if abs_tem_defaults else None), step=0.01, key=f"abs_desconto_incluir_{abs_form_nonce}")

            qtde_litros_calc_abs = float(qtde_litros_abs or 0.0)
            valor_unit_calc_abs = float(valor_unit_abs or 0.0)
            desconto_calc_abs = float(desconto_abs or 0.0)
            total_bruto_abs = qtde_litros_calc_abs * valor_unit_calc_abs
            total_gasto_abs = max(total_bruto_abs - desconto_calc_abs, 0.0)
            st.caption(f"Total calculado: {brl(total_bruto_abs)} - desconto {brl(desconto_abs)} = {brl(total_gasto_abs)}")

            if st.form_submit_button("💾 Gravar", use_container_width=True, type="primary", key="btn_abastecimento_incluir_gravar"):
                if not placa_abs.strip():
                    st.session_state["abs_validacao_msg"] = "Informe a placa do veículo para incluir o registro."
                    st.session_state["abs_focus_label"] = "Placa"
                    st.session_state["abs_expandir_cadastro"] = True
                    st.rerun()
                elif not local_abs.strip():
                    st.session_state["abs_validacao_msg"] = "Informe o local do abastecimento para incluir o registro."
                    st.session_state["abs_focus_label"] = "Local do Abastecimento"
                    st.session_state["abs_expandir_cadastro"] = True
                    st.rerun()
                elif not tipo_comb_normalizado_abs:
                    st.session_state["abs_validacao_msg"] = "Informe o tipo de combustível para incluir o registro."
                    st.session_state["abs_focus_label"] = "Tipo de Combustível"
                    st.session_state["abs_expandir_cadastro"] = True
                    st.rerun()
                elif qtde_litros_calc_abs <= 0 or valor_unit_calc_abs <= 0:
                    campo_foco_abs = "Qtde Litros" if qtde_litros_calc_abs <= 0 else "Valor Unitário"
                    st.session_state["abs_validacao_msg"] = "Informe quantidade de litros e valor unitário maiores que zero."
                    st.session_state["abs_focus_label"] = campo_foco_abs
                    st.session_state["abs_expandir_cadastro"] = True
                    st.rerun()
                else:
                    with conn() as c:
                        c.execute(
                            """INSERT INTO abastecimentos
                               (data, local, doc_nf, km_inicial, tipo_combustivel, qtde_litros, valor_unit, desconto, total_gasto, veiculo_placa)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                data_abs.isoformat(),
                                local_abs.strip(),
                                doc_nf_abs.strip(),
                                float(km_inicial_abs or 0.0),
                                tipo_comb_normalizado_abs,
                                qtde_litros_calc_abs,
                                valor_unit_calc_abs,
                                desconto_calc_abs,
                                total_gasto_abs,
                                placa_abs.strip().upper(),
                            ),
                        )
                    limpar_cache_app()
                    st.session_state["abs_form_defaults"] = {}
                    st.session_state["abs_form_nonce"] += 1
                    st.session_state["abs_sucesso_msg"] = "✅ Abastecimento gravado com sucesso!"
                    st.rerun()

        if st.session_state.get("abs_focus_label"):
            focar_campo_por_rotulo(st.session_state.pop("abs_focus_label"))

    st.markdown("---")
    
    with conn() as c:
        df_abs = pd.read_sql(
            """SELECT *
               FROM abastecimentos
               WHERE date(data) BETWEEN ? AND ?
               ORDER BY date(data) ASC, id ASC""",
            c,
            params=(filtro_ini.isoformat(), filtro_fim.isoformat()),
        )

    if not df_abs.empty:
        if placa_filtro_calculo and "veiculo_placa" in df_abs.columns:
            placa_ref_abs = str(placa_filtro_calculo).strip().upper()
            df_abs = df_abs[
                df_abs["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_abs
            ].copy()
            st.caption(f"Filtro de abastecimento por placa ativo: `{rotulo_placa_com_descricao(placa_ref_abs)}`")

    if not df_abs.empty:
        df_abs['data_dt'] = pd.to_datetime(df_abs['data'], errors='coerce').dt.date
        if "veiculo_placa" not in df_abs.columns:
            df_abs["veiculo_placa"] = ""
        df_abs["veiculo_placa"] = df_abs["veiculo_placa"].fillna("").astype(str).str.strip().str.upper()
        df_abs["km_inicial"] = pd.to_numeric(df_abs["km_inicial"], errors="coerce")
        df_abs["qtde_litros"] = pd.to_numeric(df_abs["qtde_litros"], errors="coerce").fillna(0.0)
        df_abs["valor_unit"] = pd.to_numeric(df_abs["valor_unit"], errors="coerce").fillna(0.0)
        if "desconto" not in df_abs.columns:
            df_abs["desconto"] = 0.0
        df_abs["desconto"] = pd.to_numeric(df_abs["desconto"], errors="coerce").fillna(0.0)
        df_abs["total_gasto"] = pd.to_numeric(df_abs["total_gasto"], errors="coerce").fillna(0.0)
        df_abs["tipo_combustivel"] = df_abs["tipo_combustivel"].apply(normalizar_tipo_combustivel)
        df_abs["local_filtro"] = df_abs["local"].fillna("").astype(str).str.strip()
        tipos_filtro_abs = sorted([t for t in df_abs["tipo_combustivel"].dropna().unique().tolist() if t])
        locais_filtro_abs = sorted([l for l in df_abs["local_filtro"].dropna().unique().tolist() if l])
        if "filtro_tipos_abastecimento" not in st.session_state:
            st.session_state["filtro_tipos_abastecimento"] = tipos_filtro_abs
        else:
            st.session_state["filtro_tipos_abastecimento"] = [
                t for t in st.session_state["filtro_tipos_abastecimento"] if t in tipos_filtro_abs
            ] or tipos_filtro_abs
        if "filtro_locais_abastecimento" not in st.session_state:
            st.session_state["filtro_locais_abastecimento"] = locais_filtro_abs
        else:
            st.session_state["filtro_locais_abastecimento"] = [
                l for l in st.session_state["filtro_locais_abastecimento"] if l in locais_filtro_abs
            ] or locais_filtro_abs
        f_abs_tipo, f_abs_local = st.columns(2)
        filtro_tipos_abs = f_abs_tipo.multiselect(
            "Filtrar por tipo de combustível",
            options=tipos_filtro_abs,
            default=tipos_filtro_abs,
            help="Por padrão, todos os tipos ficam selecionados.",
            key="filtro_tipos_abastecimento",
        )
        filtro_locais_abs = f_abs_local.multiselect(
            "Filtrar por local/posto",
            options=locais_filtro_abs,
            default=locais_filtro_abs,
            help="Por padrão, todos os locais ficam selecionados.",
            key="filtro_locais_abastecimento",
        )
        filtro_tipos_abs = filtro_tipos_abs or tipos_filtro_abs
        filtro_locais_abs = filtro_locais_abs or locais_filtro_abs
        if filtro_tipos_abs:
            df_abs = df_abs[df_abs["tipo_combustivel"].isin(filtro_tipos_abs)].copy()
        else:
            df_abs = df_abs.iloc[0:0].copy()
        if filtro_locais_abs:
            df_abs = df_abs[df_abs["local_filtro"].isin(filtro_locais_abs)].copy()
        else:
            df_abs = df_abs.iloc[0:0].copy()
        if df_abs.empty:
            st.info("Sem abastecimentos para o(s) filtro(s) selecionado(s).")

        if df_abs["km_inicial"].notna().sum() >= 2: # Precisamos de pelo menos 2 registros válidos para tentar calcular média
            st.markdown(f"### 📊 Resumo de Consumo Real ({filtro_ini.strftime('%d/%m/%Y')} - {filtro_fim.strftime('%d/%m/%Y')})")
            
            # 1. Distância do período = último KM do período - primeiro KM do período.
            # 2. Média = (2 últimos KMs diferentes) / litros do KM final de referência.
            df_km_valido = df_abs.dropna(subset=["km_inicial"]).copy()
            df_km_valido = df_km_valido.sort_values(by=["data_dt", "id"], ascending=[True, True])
            if len(df_km_valido) >= 2:
                # Distância real do período (primeiro KM até o último KM dentro do filtro).
                km_inicial_periodo = float(df_km_valido.iloc[0]["km_inicial"])
                km_final_periodo = float(df_km_valido.iloc[-1]["km_inicial"])
                distancia_total = km_final_periodo - km_inicial_periodo

                def calcular_media_por_tipo(df, termo_tipo):
                    df_tipo = df[df["tipo_combustivel"].str.contains(termo_tipo, na=False)].copy()
                    kms_validos = sorted(df_tipo["km_inicial"].dropna().unique().tolist(), reverse=True)
                    if len(kms_validos) < 2:
                        return 0.0
                    km_final = float(kms_validos[0])
                    km_anterior = float(kms_validos[1])
                    ultimo = df_tipo[df_tipo["km_inicial"] == km_final].iloc[-1]
                    litros_final = float(ultimo["qtde_litros"])
                    return (km_final - km_anterior) / litros_final if litros_final > 0 and km_final > km_anterior else 0.0

                media_diesel = calcular_media_por_tipo(df_abs, "DIESEL")
                media_arla = calcular_media_por_tipo(df_abs, "ARLA")
                tem_base_media = (media_diesel > 0) or (media_arla > 0)
                media_diesel_txt = f"{media_diesel:.2f} KM/L" if media_diesel > 0 else "-"
                media_arla_txt = f"{media_arla:.2f} KM/L" if media_arla > 0 else "-"

                # Totais reais do período filtrado.
                litros_diesel_periodo = df_abs[df_abs['tipo_combustivel'].str.contains("DIESEL", na=False)]['qtde_litros'].sum()
                litros_arla_periodo = df_abs[df_abs['tipo_combustivel'].str.contains("ARLA", na=False)]['qtde_litros'].sum()
                gasto_diesel_periodo = df_abs[df_abs['tipo_combustivel'].str.contains("DIESEL", na=False)]['total_gasto'].sum()
                gasto_arla_periodo = df_abs[df_abs['tipo_combustivel'].str.contains("ARLA", na=False)]['total_gasto'].sum()
                investimento_periodo = df_abs['total_gasto'].sum()

                # --- EXIBIÇÃO DAS MÉTRICAS ---
                c1, c2, c3 = st.columns(3)
                c1.metric("🏁 Distância Percorrida", f"{distancia_total:,.0f} KM".replace(",", "."))
                c2.metric("⛽ Diesel no Período", f"{litros_diesel_periodo:.2f} L")
                c3.metric("🧪 Arla no Período", f"{litros_arla_periodo:.2f} L")

                v1, v2, v3 = st.columns(3)
                v1.metric("📉 Média Diesel", media_diesel_txt)
                v2.metric("📉 Média Arla", media_arla_txt)
                v3.metric("💰 Investimento no Período", brl(investimento_periodo))
                g1, g2 = st.columns(2)
                g1.metric("💵 Gasto com Diesel", brl(gasto_diesel_periodo))
                g2.metric("💵 Gasto com Arla", brl(gasto_arla_periodo))

                st.info(
                    f"💡 Distância do período: ({km_final_periodo:,.0f} - {km_inicial_periodo:,.0f}). "
                    "Média de consumo usa os dois últimos abastecimentos do mesmo tipo e os litros do último abastecimento. "
                    "Os totais de litros e investimento consideram todo o período filtrado."
                    .replace(",", ".")
                )
                st.markdown("---")
            else:
                st.info("ℹ️ Para calcular a média, é necessário ter pelo menos 2 leituras de KM diferentes no período.")
            
        elif len(df_abs) == 1 or df_abs["km_inicial"].notna().sum() == 1:
            st.info("ℹ️ Para calcular a média de consumo, é necessário pelo menos dois registros no período (KM Inicial e KM Final).")
        
        # --- TABELA DE HISTÓRICO (MOSTRA TUDO, INCLUSIVE O INICIAL) ---
        if not df_abs.empty:
            df_abs = df_abs.sort_values(["data_dt", "id"], ascending=[True, True]).reset_index(drop=True)
            df_abs["Data_BR"] = pd.to_datetime(df_abs["data_dt"]).dt.strftime('%d/%m/%Y')
            df_abs["Excluir"] = False
            df_base_abs = df_abs[["id", "data", "veiculo_placa", "local", "doc_nf", "km_inicial", "tipo_combustivel", "qtde_litros", "valor_unit", "desconto"]].copy()
            df_base_abs = df_base_abs.set_index("id")
            df_editor_abs = df_abs[["id", "data_dt", "veiculo_placa", "local", "doc_nf", "km_inicial", "tipo_combustivel", "qtde_litros", "valor_unit", "desconto", "total_gasto", "Excluir"]].copy().reset_index(drop=True)

            if "abastecimento_id_editando" not in st.session_state:
                st.session_state.abastecimento_id_editando = None

            df_lista_abs = df_editor_abs.assign(
                Data_BR=pd.to_datetime(df_editor_abs["data_dt"], errors="coerce").dt.strftime('%d/%m/%Y'),
                Rotulo=lambda d: (
                    "ID "
                    + d["id"].astype(str)
                    + " | "
                    + d["Data_BR"].fillna("-")
                    + " | "
                    + d["veiculo_placa"].fillna("").astype(str)
                    + " | "
                    + d["local"].fillna("").astype(str)
                ),
            )

            st.dataframe(
                df_lista_abs[["id", "Data_BR", "veiculo_placa", "local", "doc_nf", "km_inicial", "tipo_combustivel", "qtde_litros", "valor_unit", "desconto", "total_gasto"]].rename(
                    columns={
                        "id": "ID",
                        "Data_BR": "Data",
                        "veiculo_placa": "Placa",
                        "local": "Local",
                        "doc_nf": "N.NF",
                        "km_inicial": "Km Inicial",
                        "tipo_combustivel": "Tipo Combustível",
                        "qtde_litros": "Qtde Litros",
                        "valor_unit": "Valor Unitário",
                        "desconto": "Desconto",
                        "total_gasto": "Total Gasto",
                    }
                ),
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Qtde Litros": st.column_config.NumberColumn("Qtde Litros", format="%.3f"),
                    "Valor Unitário": st.column_config.NumberColumn("Valor Unitário", format="R$ %.3f"),
                    "Desconto": st.column_config.NumberColumn("Desconto", format="R$ %.2f"),
                    "Total Gasto": st.column_config.NumberColumn("Total Gasto", format="R$ %.2f"),
                },
            )

            if st.button("🖨️ Impressão", key="btn_abs_impressao", use_container_width=True):
                st.session_state.mostrar_impressao_abastecimento = True

            if st.session_state.get("mostrar_impressao_abastecimento"):
                df_imp_abs = df_abs.copy().sort_values(["data_dt", "id"])
                linhas_imp_abs = []
                for _, r_imp in df_imp_abs.iterrows():
                    data_txt = pd.to_datetime(r_imp.get("data_dt"), errors="coerce")
                    data_txt = data_txt.strftime("%d/%m/%Y") if pd.notna(data_txt) else ""
                    linhas_imp_abs.append(
                        "<tr>"
                        f"<td>{escape(data_txt)}</td>"
                        f"<td>{escape(str(r_imp.get('veiculo_placa') or ''))}</td>"
                        f"<td>{escape(str(r_imp.get('local') or ''))}</td>"
                        f"<td>{escape(str(r_imp.get('doc_nf') or ''))}</td>"
                        f"<td>{escape(str(r_imp.get('tipo_combustivel') or ''))}</td>"
                        f"<td class='num'>{format_br(r_imp.get('qtde_litros') or 0, casas_decimais=3)}</td>"
                        f"<td class='num'>R$ {format_br(r_imp.get('valor_unit') or 0, casas_decimais=3)}</td>"
                        f"<td class='num'>{brl(r_imp.get('total_gasto') or 0)}</td>"
                        "</tr>"
                    )

                total_litros_imp = pd.to_numeric(df_imp_abs["qtde_litros"], errors="coerce").fillna(0.0).sum()
                total_gasto_imp = pd.to_numeric(df_imp_abs["total_gasto"], errors="coerce").fillna(0.0).sum()
                periodo_imp = f"{filtro_ini.strftime('%d/%m/%Y')} a {filtro_fim.strftime('%d/%m/%Y')}"
                placa_imp = rotulo_placa_com_descricao(placa_filtro_calculo) if placa_filtro_calculo else "Todas as placas"
                tipos_imp = ", ".join(filtro_tipos_abs) if filtro_tipos_abs else "Todos"
                locais_imp = ", ".join(filtro_locais_abs) if filtro_locais_abs else "Todos"

                html_impressao_abs = f"""
                <!DOCTYPE html>
                <html>
                <head>
                    <meta charset="UTF-8">
                    <style>
                        body {{ font-family: sans-serif; margin: 30px; color: #333; }}
                        header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 12px; }}
                        th, td {{ border: 1px solid #999; padding: 8px; text-align: left; vertical-align: top; }}
                        th {{ background-color: #f2f2f2; }}
                        .num {{ text-align: right; white-space: nowrap; }}
                        tfoot td {{ font-weight: bold; background: #fafafa; }}
                        .resumo {{ margin-top: 20px; text-align: right; font-size: 14px; }}
                        .btn-print {{ background: #007bff; color: white; padding: 15px; border: none; width: 100%; cursor: pointer; font-weight: bold; font-size: 16px; border-radius: 5px; }}
                        @media print {{ .btn-print {{ display: none; }} body {{ margin: 0; }} }}
                    </style>
                </head>
                <body>
                    <button class="btn-print" onclick="window.print()">🖨️ CLIQUE AQUI PARA IMPRIMIR ESTE RELATÓRIO</button>

                    <header>
                        <h1 style="margin:0;">ART TRANSPORTES</h1>
                        <p style="margin:5px 0;">RELATÓRIO DE ABASTECIMENTO</p>
                        <p>Período: <b>{escape(periodo_imp)}</b></p>
                        <p style="margin:5px 0;">
                            Placa: <b>{escape(placa_imp)}</b>
                            | Tipo Comb.: <b>{escape(tipos_imp)}</b>
                            | Local/Posto: <b>{escape(locais_imp)}</b>
                        </p>
                    </header>

                    <table>
                        <thead>
                            <tr>
                                <th>DATA</th>
                                <th>PLACA</th>
                                <th>LOCAL POSTO</th>
                                <th>N.NF</th>
                                <th>TIPO COMB</th>
                                <th>QTDE LITROS</th>
                                <th>VALOR UNITARIO</th>
                                <th>TOTAL</th>
                            </tr>
                        </thead>
                        <tbody>
                            {''.join(linhas_imp_abs)}
                        </tbody>
                        <tfoot>
                            <tr>
                                <td colspan="5">TOTAL</td>
                                <td class="num">{format_br(total_litros_imp, casas_decimais=3)}</td>
                                <td></td>
                                <td class="num">{brl(total_gasto_imp)}</td>
                            </tr>
                        </tfoot>
                    </table>
                    <div class="resumo">
                        <p><strong>Registros:</strong> {len(df_imp_abs)}</p>
                        <p><strong>Total Litros:</strong> {format_br(total_litros_imp, casas_decimais=3)}</p>
                        <p><strong>Total Geral:</strong> {brl(total_gasto_imp)}</p>
                        <p><strong>Gerado em:</strong> {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                    </div>
                    <script>setTimeout(function(){{ window.print(); }}, 700);</script>
                </body>
                </html>
                """
                components.html(html_impressao_abs, height=1000, scrolling=True)

            id_para_editar = st.selectbox(
                "Escolha um abastecimento para editar",
                options=df_lista_abs["id"].tolist(),
                format_func=lambda x: df_lista_abs.loc[df_lista_abs["id"] == x, "Rotulo"].iloc[0],
                key="abs_id_select_editar",
            )
            col_abs_ed1, col_abs_ed2 = st.columns(2)
            if col_abs_ed1.button("✏️ Editar Registro", key="btn_abs_abrir_form_edicao", use_container_width=True):
                st.session_state.abastecimento_id_editando = int(id_para_editar)
                st.rerun()
            if col_abs_ed2.button("❌ Cancelar Edição", key="btn_abs_cancelar_form_edicao", use_container_width=True):
                st.session_state.abastecimento_id_editando = None
                st.rerun()

            if st.session_state.abastecimento_id_editando is not None:
                registro_sel = df_abs[df_abs["id"] == st.session_state.abastecimento_id_editando]
                if registro_sel.empty:
                    st.session_state.abastecimento_id_editando = None
                    st.warning("Registro selecionado não foi encontrado.")
                else:
                    r = registro_sel.iloc[0]
                    st.markdown("### ✏️ Editar Abastecimento")
                    with st.form("form_edicao_abastecimento"):
                        c1, c2, c3, c4 = st.columns(4)
                        data_ed = c1.date_input("Data", value=r["data_dt"], format="DD/MM/YYYY")
                        placa_atual_abs = str(r.get("veiculo_placa") or "").strip().upper()
                        if opcoes_veiculos_abastecimento:
                            opcoes_veiculo_ed_abs = list(opcoes_veiculos_abastecimento)
                            rotulo_placa_manual_abs = f"{placa_atual_abs} - (placa manual)" if placa_atual_abs else None
                            if rotulo_placa_manual_abs and not any(
                                str(opt).split(" - ")[0].strip().upper() == placa_atual_abs
                                for opt in opcoes_veiculo_ed_abs
                            ):
                                opcoes_veiculo_ed_abs = [rotulo_placa_manual_abs] + opcoes_veiculo_ed_abs
                            idx_veiculo_ed_abs = 0
                            for idx_opt, opt_veic in enumerate(opcoes_veiculo_ed_abs):
                                if str(opt_veic).split(" - ")[0].strip().upper() == placa_atual_abs:
                                    idx_veiculo_ed_abs = idx_opt
                                    break
                            veic_abs_ed = c2.selectbox(
                                "Veículo/Placa",
                                options=opcoes_veiculo_ed_abs,
                                index=idx_veiculo_ed_abs,
                                key="abastecimento_veiculo_editar",
                            )
                            placa_ed_abs = str(veic_abs_ed).split(" - ")[0].strip().upper()
                        else:
                            placa_ed_abs = c2.text_input("Placa", value=placa_atual_abs).upper().strip()
                        local_ed = c3.text_input("Local do Abastecimento", value=str(r["local"] or ""))
                        doc_nf_ed = c4.text_input("Documento / NF", value=str(r["doc_nf"] or ""))

                        c5, c6, c7, c8, c9 = st.columns(5)
                        km_inicial_ed = c5.number_input("KM Inicial", min_value=0.0, value=float(r["km_inicial"] or 0.0), step=1.0)
                        tipo_atual_ed_abs = normalizar_tipo_combustivel(r["tipo_combustivel"])
                        tipo_cadastrado_key_ed = f"abs_tipo_cadastrado_ed_{int(st.session_state.abastecimento_id_editando)}"
                        tipo_manual_key_ed = f"abs_tipo_comb_ed_{int(st.session_state.abastecimento_id_editando)}"
                        tipo_cadastrado_index_ed = (
                            tipos_combustivel_sugeridos.index(tipo_atual_ed_abs) + 1
                            if tipo_atual_ed_abs in tipos_combustivel_sugeridos
                            else 0
                        )
                        tipo_cadastrado_ed = c6.selectbox(
                            "Tipo Cadastrado (opcional)",
                            options=[""] + tipos_combustivel_sugeridos,
                            index=tipo_cadastrado_index_ed,
                            placeholder="Selecione um tipo já cadastrado",
                            key=tipo_cadastrado_key_ed,
                        )
                        tipo_comb_ed = c6.text_input(
                            "Tipo de Combustível",
                            value=tipo_atual_ed_abs,
                            help="Você pode escolher um tipo já cadastrado acima ou digitar um novo aqui.",
                            key=tipo_manual_key_ed,
                        ).strip()
                        qtde_litros_ed = c7.number_input("Qtde Litros", min_value=0.0, value=float(r["qtde_litros"] or 0.0), step=0.001, format="%.3f")
                        valor_unit_ed = c8.number_input("Valor Unitário (R$)", min_value=0.0, value=float(r["valor_unit"] or 0.0), step=0.001, format="%.3f")
                        desconto_ed = c9.number_input("Desconto (R$)", min_value=0.0, value=float(r["desconto"] or 0.0), step=0.01)

                        total_bruto_ed = qtde_litros_ed * valor_unit_ed
                        total_gasto_ed = max(total_bruto_ed - desconto_ed, 0.0)
                        st.caption(f"Total calculado: {brl(total_bruto_ed)} - desconto {brl(desconto_ed)} = {brl(total_gasto_ed)}")

                        a1, a2 = st.columns(2)
                        btn_atualizar = a1.form_submit_button("💾 Atualizar", use_container_width=True, type="primary")
                        btn_excluir = a2.form_submit_button("🗑️ Excluir Registro", use_container_width=True)

                    if btn_atualizar:
                        tipo_cadastrado_submit_ed = st.session_state.get(tipo_cadastrado_key_ed, tipo_cadastrado_ed)
                        tipo_manual_submit_ed = st.session_state.get(tipo_manual_key_ed, tipo_comb_ed)
                        tipo_comb_normalizado_ed = normalizar_tipo_combustivel(
                            tipo_cadastrado_submit_ed or tipo_manual_submit_ed
                        )
                        if not placa_ed_abs.strip():
                            st.warning("Informe a placa do veículo para atualizar o registro.")
                        elif not local_ed.strip():
                            st.warning("Informe o local do abastecimento para atualizar o registro.")
                        elif not tipo_comb_normalizado_ed:
                            st.warning("Informe o tipo de combustível para atualizar o registro.")
                        elif qtde_litros_ed <= 0 or valor_unit_ed <= 0:
                            st.warning("Informe quantidade de litros e valor unitário maiores que zero.")
                        else:
                            with conn() as c:
                                c.execute(
                                    """UPDATE abastecimentos
                                       SET data=?, local=?, doc_nf=?, km_inicial=?, tipo_combustivel=?, qtde_litros=?, valor_unit=?, desconto=?, total_gasto=?, veiculo_placa=?
                                       WHERE id=?""",
                                    (
                                        data_ed.isoformat(),
                                        local_ed.strip(),
                                        doc_nf_ed.strip(),
                                        km_inicial_ed,
                                        tipo_comb_normalizado_ed,
                                        qtde_litros_ed,
                                        valor_unit_ed,
                                        desconto_ed,
                                        total_gasto_ed,
                                        placa_ed_abs.strip().upper(),
                                        int(st.session_state.abastecimento_id_editando),
                                    ),
                                )
                            alerta_gravado("✅ Abastecimento atualizado com sucesso!")
                            st.session_state.abastecimento_id_editando = None
                            st.rerun()

                    if btn_excluir:
                        with conn() as c:
                            c.execute("DELETE FROM abastecimentos WHERE id=?", (int(st.session_state.abastecimento_id_editando),))
                        alerta_gravado("✅ Abastecimento excluído com sucesso!")
                        st.session_state.abastecimento_id_editando = None
                        st.rerun()
    else:
        st.info("Sem abastecimentos no período filtrado.")

with aba10:
    st.subheader("⚙️ Configurações e Simulador de Custos")
    if "param_editando" not in st.session_state:
        st.session_state.param_editando = False

    # 1. FUNÇÃO DE CONTROLE (ON_CHANGE)
    # Garante que ao desligar o botão, a memória de teste seja limpa imediatamente
    def alternar_simulacao():
        if not st.session_state.toggle_sim:
            st.session_state.p_simulado = {} 
            st.session_state.simulacao_ativa = False
        else:
            st.session_state.simulacao_ativa = True
            # Ao ativar simulação, clona os parâmetros reais para memória.
            # Assim, toda edição ocorre em sessão sem tocar no banco.
            if not st.session_state.p_simulado:
                with conn() as c:
                    p_base = dict(c.execute("SELECT * FROM parametros WHERE id=1").fetchone())
                st.session_state.p_simulado = p_base

    # Botão de Ativação
    st.toggle(
        "🚀 ATIVAR MODO SIMULAÇÃO", 
        value=st.session_state.simulacao_ativa,
        key="toggle_sim",
        on_change=alternar_simulacao,
        help="Ative para testar novos valores sem alterar o banco de dados original."
    )

    # 2. BUSCA OS DADOS REAIS DIRETO DO BANCO (PORTO SEGURO)
    with conn() as c:
        p_real_banco = dict(c.execute("SELECT * FROM parametros WHERE id=1").fetchone())

    opcoes_param_placa = list(apenas_placas)
    if not opcoes_param_placa:
        st.warning("Cadastre um veículo antes de cadastrar parâmetros por placa.")
    if st.session_state.get("param_placa_cadastro") not in opcoes_param_placa:
        st.session_state.param_placa_cadastro = opcoes_param_placa[0] if opcoes_param_placa else None
    placa_param_cadastro = st.selectbox(
        "Placa do Veículo para Cadastro de Parâmetros",
        opcoes_param_placa,
        index=0 if opcoes_param_placa else None,
        key="param_placa_cadastro",
        format_func=rotulo_placa_com_descricao,
        placeholder="Selecione a placa do veículo",
        help="Cada placa possui seu próprio cadastro de parâmetros.",
    )
    with conn() as c:
        if placa_param_cadastro:
            row_param_placa = c.execute(
                """SELECT consumo, manut, pneu, depre, motora_fixo, motora_pct,
                          seguro, seguro_vida_motorista, financiamento, pagto_ipva,
                          cmp_custo_escritorio, vl_custo_rastreador, imposto_pct,
                          valor_frete_mensal_fixo, qtde_pneu, vl_gasto_pneu_km
                   FROM parametros_historico
                   WHERE UPPER(TRIM(veiculo_placa)) = UPPER(TRIM(?))
                   ORDER BY date(vigencia_data) DESC, id DESC
                   LIMIT 1""",
                (placa_param_cadastro,),
            ).fetchone()
            if row_param_placa:
                p_real_banco = {**p_real_banco, **dict(row_param_placa)}

    # 3. DEFINE A FONTE DE DADOS PARA EXIBIÇÃO
    if st.session_state.simulacao_ativa and st.session_state.p_simulado:
        dados_para_exibir = st.session_state.p_simulado
        st.warning("⚠️ **MODO SIMULAÇÃO ATIVO:** Os valores abaixo são temporários.")
    else:
        dados_para_exibir = p_real_banco
        if placa_param_cadastro:
            st.info(f"✅ **MODO REAL:** Os valores abaixo estão salvos para `{rotulo_placa_com_descricao(placa_param_cadastro)}`.")

    # 4. CAMPOS COM CHAVE DINÂMICA (tipo_modo)
    # Fora de st.form para atualizar cálculos em tempo real
    tipo_modo = "sim" if st.session_state.simulacao_ativa else "real"

    # --- SEÇÃO DE DATAS E META ---
    c_ctrl_p1, c_ctrl_p2 = st.columns([1, 1])
    if c_ctrl_p1.button("✏️ Editar", use_container_width=True, key="btn_param_editar", disabled=st.session_state.param_editando):
        st.session_state.param_editando = True
        st.rerun()
    c_ctrl_p2.button("💾 Gravar", use_container_width=True, type="primary", key="btn_param_gravar_top", disabled=True)

    st.markdown("##### 📅 Período Global de Filtro")
    c_data1, c_data2, c_placa_default, c_meta, c_frete = st.columns([1, 1, 1.4, 1.5, 1.5])
    
    # Converte datas do banco para o seletor do Streamlit
    d_ini_val = datetime.strptime(dados_para_exibir.get('data_filtro_ini', '2026-01-01'), '%Y-%m-%d').date()
    d_fim_val = datetime.strptime(dados_para_exibir.get('data_filtro_fim', '2026-12-31'), '%Y-%m-%d').date()
    
    dt_ini_param = c_data1.date_input("Data Início", value=d_ini_val, format="DD/MM/YYYY", key=f"dtini_{tipo_modo}", disabled=not st.session_state.param_editando)
    dt_fim_param = c_data2.date_input("Data Fim", value=d_fim_val, format="DD/MM/YYYY", key=f"dtfim_{tipo_modo}", disabled=not st.session_state.param_editando)
    placa_default_v = str(dados_para_exibir.get("filtro_placa_default") or "Todas as placas").strip()
    if placa_default_v not in opcoes_filtro_placa:
        placa_default_v = "Todas as placas"
    placa_default_v = c_placa_default.selectbox(
        "Filtro Placa Default",
        opcoes_filtro_placa,
        index=opcoes_filtro_placa.index(placa_default_v),
        key=f"placa_default_{tipo_modo}",
        format_func=rotulo_placa_com_descricao,
        disabled=not st.session_state.param_editando,
    )
    meta_v = c_meta.number_input("Meta de Faturamento Mensal (R$)", value=float(dados_para_exibir.get('meta_faturamento', 50000.0)), key=f"meta_{tipo_modo}", disabled=not st.session_state.param_editando)
    frete_mensal_fixo_v = c_frete.number_input(
        "Valor Frete Mensal Fixo (R$)",
        min_value=0.0,
        step=100.0,
        value=float(dados_para_exibir.get('valor_frete_mensal_fixo', 0.0)),
        key=f"frete_mensal_fixo_{tipo_modo}",
        disabled=not st.session_state.param_editando
    )

    st.markdown("---")
    st.markdown("##### 💰 Custos e Índices Operacionais")
    
    c1, c2, c3, c4, c5 = st.columns(5)
    con_v = c1.number_input("Consumo (km/l)", value=float(dados_para_exibir.get('consumo', 2.5)), step=0.1, key=f"con_{tipo_modo}", disabled=not st.session_state.param_editando)
    man_v = c2.number_input("Manutenção (R$/km)", value=float(dados_para_exibir.get('manut', 0.25)), step=0.01, key=f"man_{tipo_modo}", disabled=not st.session_state.param_editando)
    qtde_pneu_v = c3.number_input("Qtde Pneu", min_value=0.0, step=1.0, format="%.0f", value=float(dados_para_exibir.get('qtde_pneu', 1.0)), key=f"qtde_pneu_{tipo_modo}", disabled=not st.session_state.param_editando)
    vl_gasto_pneu_km_v = c4.number_input(
        "Vl Gasto por KM",
        min_value=0.0,
        step=0.0001,
        format="%.4f",
        value=float(dados_para_exibir.get('vl_gasto_pneu_km', dados_para_exibir.get('pneu', 0.12))),
        key=f"vl_pneu_km_{tipo_modo}",
        disabled=not st.session_state.param_editando
    )
    pne_v = qtde_pneu_v * vl_gasto_pneu_km_v
    c5.number_input("Pneu (R$/km)", value=float(pne_v), step=0.01, disabled=True)
    dep_v = st.number_input("Depreciação (R$/km)", value=float(dados_para_exibir.get('depre', 0.30)), step=0.01, key=f"dep_{tipo_modo}", disabled=not st.session_state.param_editando)
    
    c6, c7, c8, c9, c10, c11, c12, c13, c14 = st.columns(9)
    fix_v = c6.number_input("Salário Fixo (R$)", value=float(dados_para_exibir.get('motora_fixo', 2500.0)), step=50.0, key=f"fix_{tipo_modo}", disabled=not st.session_state.param_editando)
    pct_v = c7.number_input("Comissão (%)", value=float(dados_para_exibir.get('motora_pct', 10.0)), step=0.5, key=f"pct_{tipo_modo}", disabled=not st.session_state.param_editando)
    seg_v = c8.number_input("Seguro Mensal (R$)", value=float(dados_para_exibir.get('seguro', 2750.0)), step=10.0, key=f"seg_{tipo_modo}", disabled=not st.session_state.param_editando)
    seg_vida_motorista_v = c9.number_input("Seguro Vida Motorista Mensal (R$)", value=float(dados_para_exibir.get('seguro_vida_motorista', 0.0)), step=10.0, key=f"seg_vida_mot_{tipo_modo}", disabled=not st.session_state.param_editando)
    fin_v = c10.number_input("Financiamento (R$)", value=float(dados_para_exibir.get('financiamento', 0.0)), step=100.0, key=f"fin_{tipo_modo}", disabled=not st.session_state.param_editando)
    esc_v = c11.number_input("Escritório (R$)", value=float(dados_para_exibir.get('cmp_custo_escritorio', 0.0)), step=50.0, key=f"esc_{tipo_modo}", disabled=not st.session_state.param_editando)
    rastreador_v = c12.number_input("VL Custo Rastreador (R$)", value=float(dados_para_exibir.get('vl_custo_rastreador', 0.0)), step=50.0, key=f"rastreador_{tipo_modo}", disabled=not st.session_state.param_editando)
    ipva_v = c13.number_input("Pagto IPVA Anual (R$)", value=float(dados_para_exibir.get('pagto_ipva', 0.0)), step=50.0, key=f"ipva_{tipo_modo}", disabled=not st.session_state.param_editando)
    imp_v = c14.number_input("% de Impostos", min_value=0.0, step=0.1, value=float(dados_para_exibir.get('imposto_pct', 0.0)), key=f"imposto_{tipo_modo}", disabled=not st.session_state.param_editando)

    if not df_db.empty:
        qtd_imposto = pd.to_numeric(df_db.get("qtd_viagens", 1), errors="coerce").fillna(1.0)
        qtd_imposto = qtd_imposto.apply(lambda x: max(1, int(round(float(x)))))
        pagto_estadia_imposto = pd.to_numeric(df_db.get("pagto_estadia", 0.0), errors="coerce").fillna(0.0)
        adicional_frete_imposto = pd.to_numeric(df_db.get("valor_adicional_frete", 0.0), errors="coerce").fillna(0.0)
        total_frete_base_imposto = float(
            (
                (pd.to_numeric(df_db["Total Frete"], errors="coerce").fillna(0.0) + pagto_estadia_imposto + adicional_frete_imposto)
                * qtd_imposto
            ).sum()
        )
    else:
        total_frete_base_imposto = 0.0
    total_frete_base_imposto += frete_mensal_fixo_v * fator_rateio_mensal_por_periodo(filtro_ini, filtro_fim)
    total_imposto_calc = total_frete_base_imposto * (imp_v / 100.0)
    st.number_input("Total Imposto (R$)", value=float(total_imposto_calc), step=0.01, disabled=True)

    st.markdown("<br>", unsafe_allow_html=True)
    btn_label = "💾 Gravar"
    
    if st.button(btn_label, use_container_width=True, type="primary", key=f"btn_param_save_{tipo_modo}", disabled=(not st.session_state.param_editando or not placa_param_cadastro)):
        # Cria o dicionário de dados atualizados
        novos_dados = {
            'consumo': con_v, 'manut': man_v, 'pneu': pne_v, 'depre': dep_v,
            'qtde_pneu': qtde_pneu_v, 'vl_gasto_pneu_km': vl_gasto_pneu_km_v,
            'motora_fixo': fix_v, 'motora_pct': pct_v, 'seguro': seg_v, 'seguro_vida_motorista': seg_vida_motorista_v, 'financiamento': fin_v,
            'cmp_custo_escritorio': esc_v, 'vl_custo_rastreador': rastreador_v, 'pagto_ipva': ipva_v, 'imposto_pct': imp_v,
            'meta_faturamento': meta_v,
            'valor_frete_mensal_fixo': frete_mensal_fixo_v,
            'data_filtro_ini': dt_ini_param.strftime('%Y-%m-%d'),
            'data_filtro_fim': dt_fim_param.strftime('%Y-%m-%d'),
            'filtro_placa_default': placa_default_v,
        }

        if st.session_state.simulacao_ativa:
            # Grava apenas na memória da sessão
            st.session_state.p_simulado = {**st.session_state.p_simulado, **novos_dados}
            alerta_gravado()
        else:
            # Grava permanentemente no SQLite
            with conn() as c:
                c.execute(
                    """UPDATE parametros
                       SET data_filtro_ini=?, data_filtro_fim=?, filtro_placa_default=?
                       WHERE id=1""",
                    (dt_ini_param.strftime('%Y-%m-%d'), dt_fim_param.strftime('%Y-%m-%d'), placa_default_v),
                )
                data_vigencia_param = dt_ini_param.isoformat()
                c.execute(
                    """INSERT INTO parametros_historico (
                           veiculo_placa, vigencia_data, consumo, manut, pneu, depre, motora_fixo, motora_pct,
                           seguro, seguro_vida_motorista, financiamento, pagto_ipva, cmp_custo_escritorio, vl_custo_rastreador, imposto_pct, valor_frete_mensal_fixo,
                           qtde_pneu, vl_gasto_pneu_km
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT(veiculo_placa, vigencia_data) DO UPDATE SET
                           consumo=excluded.consumo,
                           manut=excluded.manut,
                           pneu=excluded.pneu,
                           depre=excluded.depre,
                           motora_fixo=excluded.motora_fixo,
                           motora_pct=excluded.motora_pct,
                           seguro=excluded.seguro,
                           seguro_vida_motorista=excluded.seguro_vida_motorista,
                           financiamento=excluded.financiamento,
                           pagto_ipva=excluded.pagto_ipva,
                           cmp_custo_escritorio=excluded.cmp_custo_escritorio,
                           vl_custo_rastreador=excluded.vl_custo_rastreador,
                           imposto_pct=excluded.imposto_pct,
                           valor_frete_mensal_fixo=excluded.valor_frete_mensal_fixo,
                           qtde_pneu=excluded.qtde_pneu,
                           vl_gasto_pneu_km=excluded.vl_gasto_pneu_km""",
                    (
                        placa_param_cadastro,
                        data_vigencia_param,
                        con_v, man_v, pne_v, dep_v, fix_v, pct_v,
                        seg_v, seg_vida_motorista_v, fin_v, ipva_v, esc_v, rastreador_v, imp_v, frete_mensal_fixo_v,
                        qtde_pneu_v, vl_gasto_pneu_km_v,
                    ),
                )
                limpar_cache_bootstrap()
            st.session_state.p_simulado = {} # Limpa para garantir sincronia
            alerta_gravado()
        
        st.session_state.param_editando = False
        st.session_state.filtro_placa_top_pendente = placa_default_v
        st.rerun()

    st.markdown("---")
    st.markdown("##### 🕒 Histórico de Vigência dos Parâmetros")
    st.caption("As alterações feitas aqui impactam apenas os cálculos a partir da data de vigência selecionada.")

    with conn() as c:
        df_hist_param = pd.read_sql(
            """SELECT id, COALESCE(NULLIF(TRIM(veiculo_placa), ''), 'GERAL') AS veiculo_placa,
                      vigencia_data, consumo, manut, pneu, depre, motora_fixo, motora_pct,
                      seguro, seguro_vida_motorista, financiamento, pagto_ipva, cmp_custo_escritorio, vl_custo_rastreador, imposto_pct, valor_frete_mensal_fixo,
                      qtde_pneu, vl_gasto_pneu_km
               FROM parametros_historico
               ORDER BY veiculo_placa ASC, date(vigencia_data) DESC, id DESC""",
            c,
        )

    if df_hist_param.empty:
        st.info("Ainda não há histórico de parâmetros.")
    else:
        if "hist_param_excluir_tudo" not in st.session_state:
            st.session_state.hist_param_excluir_tudo = False

        ac1, ac2 = st.columns([1, 2])
        if ac1.button("🗑️ Excluir Histórico", use_container_width=True, key="btn_hist_excluir_tudo"):
            st.session_state.hist_param_excluir_tudo = True

        if st.session_state.hist_param_excluir_tudo:
            ac2.warning("Confirma a exclusão de todo o histórico de vigência?")
            c1, c2 = st.columns(2)
            if c1.button("✅ Confirmar exclusão total", use_container_width=True, type="primary", key="btn_hist_excluir_tudo_conf"):
                with conn() as c:
                    c.execute("DELETE FROM parametros_historico")
                st.session_state.hist_param_excluir_tudo = False
                st.success("Histórico de vigência excluído com sucesso.")
                st.rerun()
            if c2.button("❌ Cancelar", use_container_width=True, key="btn_hist_excluir_tudo_cancel"):
                st.session_state.hist_param_excluir_tudo = False
                st.rerun()

        df_hist_exibir = df_hist_param.copy()
        df_hist_exibir["vigencia_data"] = pd.to_datetime(df_hist_exibir["vigencia_data"], errors="coerce").dt.date
        st.dataframe(
            df_hist_exibir,
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", format="%d"),
                "veiculo_placa": st.column_config.TextColumn("Placa"),
                "vigencia_data": st.column_config.DateColumn("Vigência", format="DD/MM/YYYY"),
                "consumo": st.column_config.NumberColumn("Consumo", format="%.2f"),
                "manut": st.column_config.NumberColumn("Manut.", format="R$ %.4f"),
                "pneu": st.column_config.NumberColumn("Pneu", format="R$ %.4f"),
                "depre": st.column_config.NumberColumn("Depre.", format="R$ %.4f"),
                "motora_fixo": st.column_config.NumberColumn("Motorista Fixo", format="R$ %.2f"),
                "motora_pct": st.column_config.NumberColumn("Comissão (%)", format="%.2f"),
                "seguro": st.column_config.NumberColumn("Seguro", format="R$ %.2f"),
                "seguro_vida_motorista": st.column_config.NumberColumn("Seguro Vida Motorista Mensal", format="R$ %.2f"),
                "financiamento": st.column_config.NumberColumn("Financiamento", format="R$ %.2f"),
                "pagto_ipva": st.column_config.NumberColumn("IPVA Anual", format="R$ %.2f"),
                "cmp_custo_escritorio": st.column_config.NumberColumn("Escritório", format="R$ %.2f"),
                "vl_custo_rastreador": st.column_config.NumberColumn("VL Custo Rastreador", format="R$ %.2f"),
                "imposto_pct": st.column_config.NumberColumn("Imposto (%)", format="%.2f"),
                "valor_frete_mensal_fixo": st.column_config.NumberColumn("Frete Fixo Mensal", format="R$ %.2f"),
                "qtde_pneu": st.column_config.NumberColumn("Qtde Pneu", format="%.0f"),
                "vl_gasto_pneu_km": st.column_config.NumberColumn("Valor Pneu Km", format="R$ %.4f"),
            },
        )

        st.markdown("##### ✏️ Editar Registro de Vigência")
        if "hist_param_excluir_id" not in st.session_state:
            st.session_state.hist_param_excluir_id = None
        mapa_hist = {
            f"ID {int(r['id'])} | Placa {str(r['veiculo_placa'] or 'GERAL')} | Vigência {str(r['vigencia_data'])}": int(r["id"])
            for _, r in df_hist_param.iterrows()
        }
        hist_sel_label = st.selectbox(
            "Selecione um registro do histórico",
            options=list(mapa_hist.keys()),
            index=None,
            placeholder="Escolha um registro para editar",
            key="hist_param_sel",
        )

        if hist_sel_label:
            id_hist_sel = mapa_hist[hist_sel_label]
            row_hist = df_hist_param[df_hist_param["id"] == id_hist_sel].iloc[0]
            vig_atual = pd.to_datetime(row_hist["vigencia_data"], errors="coerce").date()
            placa_hist_atual = str(row_hist.get("veiculo_placa") or "GERAL").strip().upper()
            opcoes_hist_placa = list(apenas_placas)
            if placa_hist_atual != "GERAL" and placa_hist_atual not in opcoes_hist_placa:
                opcoes_hist_placa.append(placa_hist_atual)
            if not opcoes_hist_placa:
                st.warning("Cadastre um veículo para editar registros de parâmetros por placa.")

            h1, h2, h3 = st.columns(3)
            nova_placa_hist = h1.selectbox(
                "Placa",
                opcoes_hist_placa,
                index=opcoes_hist_placa.index(placa_hist_atual) if placa_hist_atual in opcoes_hist_placa else (0 if opcoes_hist_placa else None),
                key=f"hist_placa_{id_hist_sel}",
                format_func=rotulo_placa_com_descricao,
                placeholder="Selecione a placa",
            )
            nova_vigencia = h2.date_input(
                "Data Vigência",
                value=vig_atual,
                min_value=date(1900, 1, 1),
                max_value=date(2100, 12, 31),
                format="DD/MM/YYYY",
                key=f"hist_vig_{id_hist_sel}",
            )
            novo_consumo = h3.number_input("Consumo (km/l)", value=float(row_hist["consumo"]), step=0.1, key=f"hist_con_{id_hist_sel}")
            nova_manut = st.number_input("Manutenção (R$/km)", value=float(row_hist["manut"]), step=0.01, key=f"hist_man_{id_hist_sel}")

            h4, h5, h6 = st.columns(3)
            novo_pneu = h4.number_input("Pneu (R$/km)", value=float(row_hist["pneu"]), step=0.01, key=f"hist_pneu_{id_hist_sel}")
            nova_depre = h5.number_input("Depreciação (R$/km)", value=float(row_hist["depre"]), step=0.01, key=f"hist_dep_{id_hist_sel}")
            novo_mot_fixo = h6.number_input("Motorista Fixo (R$)", value=float(row_hist["motora_fixo"]), step=50.0, key=f"hist_fix_{id_hist_sel}")

            h7, h8, h9, h10 = st.columns(4)
            novo_mot_pct = h7.number_input("Comissão (%)", value=float(row_hist["motora_pct"]), step=0.1, key=f"hist_pct_{id_hist_sel}")
            novo_seguro = h8.number_input("Seguro (R$)", value=float(row_hist["seguro"]), step=10.0, key=f"hist_seg_{id_hist_sel}")
            novo_seguro_vida_motorista = h9.number_input("Seguro Vida Motorista Mensal (R$)", value=float(row_hist["seguro_vida_motorista"]), step=10.0, key=f"hist_seg_vida_mot_{id_hist_sel}")
            novo_fin = h10.number_input("Financiamento (R$)", value=float(row_hist["financiamento"]), step=50.0, key=f"hist_fin_{id_hist_sel}")

            h11, h12, h13, h14, h15 = st.columns(5)
            novo_ipva = h11.number_input("IPVA Anual (R$)", value=float(row_hist["pagto_ipva"]), step=50.0, key=f"hist_ipva_{id_hist_sel}")
            novo_escr = h12.number_input("Escritório (R$)", value=float(row_hist["cmp_custo_escritorio"]), step=50.0, key=f"hist_esc_{id_hist_sel}")
            novo_rastreador = h13.number_input("VL Custo Rastreador (R$)", value=float(row_hist["vl_custo_rastreador"] or 0.0), step=50.0, key=f"hist_rastreador_{id_hist_sel}")
            novo_imp = h14.number_input("Imposto (%)", value=float(row_hist["imposto_pct"]), step=0.1, key=f"hist_imp_{id_hist_sel}")
            novo_frete_fixo = h15.number_input("Frete Fixo Mensal (R$)", value=float(row_hist["valor_frete_mensal_fixo"]), step=100.0, key=f"hist_ff_{id_hist_sel}")
            h16, h17 = st.columns(2)
            nova_qtde_pneu = h16.number_input("Qtde Pneu", value=float(row_hist["qtde_pneu"] or 0.0), min_value=0.0, step=1.0, format="%.0f", key=f"hist_qpneu_{id_hist_sel}")
            novo_vl_pneu_km = h17.number_input("Valor Pneu Km (R$)", value=float(row_hist["vl_gasto_pneu_km"] or 0.0), min_value=0.0, step=0.0001, format="%.4f", key=f"hist_vlpkm_{id_hist_sel}")

            st.markdown("##### 📄 Duplicar Vigência")
            d1, d2 = st.columns([2, 1])
            data_duplicar = d1.date_input(
                "Nova data para cópia do registro",
                value=(vig_atual + timedelta(days=1)),
                min_value=date(1900, 1, 1),
                max_value=date(2100, 12, 31),
                format="DD/MM/YYYY",
                key=f"hist_dup_data_{id_hist_sel}",
            )
            if d2.button("📄 Duplicar", use_container_width=True, key=f"btn_hist_dup_{id_hist_sel}", disabled=not nova_placa_hist):
                data_dup_txt = data_duplicar.isoformat()
                with conn() as c:
                    existe_dup = c.execute(
                        "SELECT id FROM parametros_historico WHERE veiculo_placa=? AND vigencia_data=?",
                        (nova_placa_hist, data_dup_txt),
                    ).fetchone()
                    if existe_dup:
                        st.warning("Já existe um registro nessa placa e data. Escolha outra data para duplicar.")
                    else:
                        c.execute(
                            """INSERT INTO parametros_historico (
                                   veiculo_placa, vigencia_data, consumo, manut, pneu, depre, motora_fixo, motora_pct,
                                   seguro, seguro_vida_motorista, financiamento, pagto_ipva, cmp_custo_escritorio, vl_custo_rastreador, imposto_pct, valor_frete_mensal_fixo,
                                   qtde_pneu, vl_gasto_pneu_km
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                nova_placa_hist,
                                data_dup_txt,
                                float(row_hist["consumo"] or 0.0),
                                float(row_hist["manut"] or 0.0),
                                float(row_hist["pneu"] or 0.0),
                                float(row_hist["depre"] or 0.0),
                                float(row_hist["motora_fixo"] or 0.0),
                                float(row_hist["motora_pct"] or 0.0),
                                float(row_hist["seguro"] or 0.0),
                                float(row_hist["seguro_vida_motorista"] or 0.0),
                                float(row_hist["financiamento"] or 0.0),
                                float(row_hist["pagto_ipva"] or 0.0),
                                float(row_hist["cmp_custo_escritorio"] or 0.0),
                                float(row_hist["vl_custo_rastreador"] or 0.0),
                                float(row_hist["imposto_pct"] or 0.0),
                                float(row_hist["valor_frete_mensal_fixo"] or 0.0),
                                float(row_hist["qtde_pneu"] or 0.0),
                                float(row_hist["vl_gasto_pneu_km"] or 0.0),
                            ),
                        )
                alerta_gravado()
                st.rerun()

            a_salvar, a_excluir = st.columns(2)
            if a_salvar.button("💾 Salvar Alteração de Vigência", use_container_width=True, key=f"btn_hist_save_{id_hist_sel}", disabled=not nova_placa_hist):
                nova_vig_txt = nova_vigencia.isoformat()
                with conn() as c:
                    existe_data = c.execute(
                        "SELECT id FROM parametros_historico WHERE veiculo_placa=? AND vigencia_data=? AND id<>?",
                        (nova_placa_hist, nova_vig_txt, int(id_hist_sel)),
                    ).fetchone()
                    if existe_data:
                        st.warning("Já existe outro registro nessa placa e data de vigência.")
                    else:
                        c.execute(
                            """UPDATE parametros_historico
                               SET veiculo_placa=?, vigencia_data=?, consumo=?, manut=?, pneu=?, depre=?, motora_fixo=?, motora_pct=?,
                                   seguro=?, seguro_vida_motorista=?, financiamento=?, pagto_ipva=?, cmp_custo_escritorio=?, vl_custo_rastreador=?, imposto_pct=?, valor_frete_mensal_fixo=?,
                                   qtde_pneu=?, vl_gasto_pneu_km=?
                               WHERE id=?""",
                            (
                                nova_placa_hist, nova_vig_txt, novo_consumo, nova_manut, novo_pneu, nova_depre, novo_mot_fixo, novo_mot_pct,
                                novo_seguro, novo_seguro_vida_motorista, novo_fin, novo_ipva, novo_escr, novo_rastreador, novo_imp, novo_frete_fixo,
                                nova_qtde_pneu, novo_vl_pneu_km, int(id_hist_sel),
                            ),
                        )
                alerta_gravado()
                st.rerun()

            if a_excluir.button("🗑️ Excluir Registro", use_container_width=True, key=f"btn_hist_del_{id_hist_sel}"):
                st.session_state.hist_param_excluir_id = int(id_hist_sel)

            if st.session_state.hist_param_excluir_id == int(id_hist_sel):
                st.warning(f"Confirma a exclusão do registro de vigência ID {int(id_hist_sel)}?")
                d_conf, d_cancel = st.columns(2)
                if d_conf.button("✅ Confirmar exclusão", use_container_width=True, type="primary", key=f"btn_hist_del_conf_{id_hist_sel}"):
                    with conn() as c:
                        cur_del = c.execute("DELETE FROM parametros_historico WHERE id=?", (int(id_hist_sel),))
                    st.session_state.hist_param_excluir_id = None
                    if int(cur_del.rowcount or 0) > 0:
                        alerta_gravado()
                    else:
                        st.warning("Registro não encontrado para exclusão.")
                    st.rerun()
                if d_cancel.button("❌ Cancelar exclusão", use_container_width=True, key=f"btn_hist_del_cancel_{id_hist_sel}"):
                    st.session_state.hist_param_excluir_id = None
                    st.rerun()

with aba11:
    if "meta_editando" not in st.session_state:
        st.session_state.meta_editando = False
    if "meta_aba11_valor" not in st.session_state:
        st.session_state.meta_aba11_valor = float(p.get("meta_faturamento", 50000.0))

    meta_atual = float(p.get("meta_faturamento", 50000.0))
    if not st.session_state.meta_editando:
        st.session_state.meta_aba11_valor = meta_atual

    c_meta, c_editar, c_gravar = st.columns([2, 1, 1])
    with c_meta:
        meta = st.number_input(
            "Meta Mensal",
            min_value=0.0,
            step=100.0,
            key="meta_aba11_valor",
            disabled=not st.session_state.meta_editando,
        )
    with c_editar:
        if st.button("✏️ Editar Meta", use_container_width=True, disabled=st.session_state.meta_editando):
            st.session_state.meta_editando = True
            st.rerun()
    with c_gravar:
        if st.button("💾 Gravar", use_container_width=True, type="primary", disabled=not st.session_state.meta_editando, key="btn_meta_gravar"):
            nova_meta = float(st.session_state.meta_aba11_valor)
            if st.session_state.simulacao_ativa:
                if not st.session_state.p_simulado:
                    with conn() as c:
                        p_base = dict(c.execute("SELECT * FROM parametros WHERE id=1").fetchone())
                    st.session_state.p_simulado = p_base
                st.session_state.p_simulado["meta_faturamento"] = nova_meta
                alerta_gravado()
            else:
                with conn() as c:
                    c.execute("UPDATE parametros SET meta_faturamento=? WHERE id=1", (nova_meta,))
                alerta_gravado()
            st.session_state.meta_editando = False
            st.rerun()

    if not df_db.empty:
        if "qtd_viagens" in df_db.columns:
            qtd_meta = pd.to_numeric(df_db["qtd_viagens"], errors="coerce").fillna(1.0)
            qtd_meta = qtd_meta.apply(lambda x: max(1, int(round(float(x)))))
        else:
            qtd_meta = pd.Series(1, index=df_db.index, dtype=int)
        pagto_estadia_meta = pd.to_numeric(df_db.get("pagto_estadia", 0.0), errors="coerce").fillna(0.0)
        adicional_frete_meta = pd.to_numeric(df_db.get("valor_adicional_frete", 0.0), errors="coerce").fillna(0.0)
        fat_at = float(
            (
                (pd.to_numeric(df_db["Total Frete"], errors="coerce").fillna(0.0) + pagto_estadia_meta + adicional_frete_meta)
                * qtd_meta
            ).sum()
        )
    else:
        fat_at = 0.0
    fat_at += frete_fixo_rateado_periodo(filtro_ini, filtro_fim)
    fig = go.Figure(
        go.Indicator(
            mode="gauge+number+delta",
            value=fat_at,
            delta={"reference": float(meta)},
            gauge={"bar": {"color": "#B8E0D2"}},
        )
    )
    st.plotly_chart(fig, use_container_width=True)

with aba12:
    st.subheader("🚚 Veículos")
    if "veiculo_editando" not in st.session_state:
        st.session_state.veiculo_editando = False
    with st.expander("➕ Cadastro de Veículo", expanded=False):
        with st.form("f_v"):
            c1, c2 = st.columns(2)
            d_v, m_v, a_v = c1.text_input("Desc"), c1.text_input("Modelo"), c1.text_input("Ano")
            co_v, pl_v, re_v = c2.text_input("Cor"), c2.text_input("Placa").upper(), c2.text_input("Renavan")
            qtd_eixo_v = c2.number_input("Quantidade Eixo", min_value=0, step=1, value=0)
            ob_v = st.text_area("Observação")
            if st.form_submit_button("💾 Gravar", key="btn_veiculo_gravar"):
                with conn() as c:
                    c.execute(
                        "INSERT OR REPLACE INTO veiculos (descricao, modelo, ano, cor, placa, renavan, observacao, quantidade_eixo) VALUES (?,?,?,?,?,?,?,?)",
                        (d_v, m_v, a_v, co_v, pl_v, re_v, ob_v, int(qtd_eixo_v)),
                    )
                alerta_gravado()
                st.rerun()
    with conn() as c:
        df_veic = pd.read_sql("SELECT * FROM veiculos ORDER BY descricao ASC", c)
    st.dataframe(df_veic, use_container_width=True, hide_index=True)
    c_ve1, c_ve2 = st.columns(2)
    if c_ve1.button("✏️ Editar", key="btn_veiculo_editar", use_container_width=True, disabled=st.session_state.veiculo_editando):
        st.session_state.veiculo_editando = True
        st.rerun()
    if c_ve2.button("❌ Cancelar Edição", key="btn_veiculo_cancelar_editar", use_container_width=True, disabled=not st.session_state.veiculo_editando):
        st.session_state.veiculo_editando = False
        st.rerun()
    if st.session_state.veiculo_editando and not df_veic.empty:
        df_veic_ed = st.data_editor(
            df_veic,
            key="editor_veiculos_cad",
            hide_index=True,
            use_container_width=True,
            column_config={"id": None},
        )
        if st.button("💾 Gravar", key="btn_veiculo_gravar_edicao", type="primary", use_container_width=True):
            with conn() as c:
                for _, r in df_veic_ed.iterrows():
                    c.execute(
                        """UPDATE veiculos
                           SET descricao=?, modelo=?, ano=?, cor=?, placa=?, renavan=?, observacao=?, quantidade_eixo=?
                           WHERE id=?""",
                        (
                            r["descricao"],
                            r["modelo"],
                            r["ano"],
                            r["cor"],
                            r["placa"],
                            r["renavan"],
                            r["observacao"],
                            int(pd.to_numeric(r["quantidade_eixo"], errors="coerce")) if pd.notna(pd.to_numeric(r["quantidade_eixo"], errors="coerce")) else 0,
                            int(r["id"]),
                        ),
                    )
            alerta_gravado()
            st.session_state.veiculo_editando = False
            st.rerun()

# =========================
# ABA 13 - CONTROLE DE TROCAS (LÓGICA ALTERADA)
# =========================
with aba13:
    st.subheader("🛢️ Controle de Trocas e Revisões")
    
    with conn() as c:
        rows_servico = c.execute("SELECT nome FROM tipos_servico_troca ORDER BY nome").fetchall()
    lista_servicos_troca = [r["nome"] for r in rows_servico] if rows_servico else [
        "Troca de Óleo Motor", "Troca de Óleo Câmbio", "Troca de Óleo Diferencial", 
        "Filtro de Ar", "Filtro de Combustível", "Revisão Geral", 
        "Troca Pneu Dianteiro", "Troca Pneu Tração", "Troca Pneu Truck", "Outros"
    ]
    
    col_cad, col_hist = st.columns([1, 2])
    
    with col_cad:
        st.markdown("### 📝 Registrar Troca/Revisão")
        with st.expander("➕ Cadastrar novo Tipo de Serviço", expanded=False):
            novo_tipo_cad, btn_tipo_cad = st.columns([3, 1])
            novo_tipo_servico = novo_tipo_cad.text_input("Cadastrar novo Tipo de Serviço", key="novo_tipo_servico")
            if btn_tipo_cad.button("➕ Adicionar", key="btn_add_tipo_servico"):
                if str(novo_tipo_servico or "").strip():
                    with conn() as c:
                        c.execute("INSERT OR IGNORE INTO tipos_servico_troca (nome) VALUES (?)", (novo_tipo_servico.strip(),))
                    st.success("Tipo de serviço cadastrado com sucesso.")
                    st.rerun()
                else:
                    st.warning("Digite o nome do tipo de serviço antes de adicionar.")

        with st.expander("➕ Alterar Tipo de Serviço", expanded=False):
            with st.form("f_alterar_tipo_servico", clear_on_submit=True):
                tipo_alterar, novo_nome = st.columns([3, 2])
                tipo_servico_para_alterar = tipo_alterar.selectbox("Tipo de Serviço a Alterar", ["Selecione"] + lista_servicos_troca, key="tipo_servico_para_alterar")
                novo_nome_servico = novo_nome.text_input("Novo nome do Tipo de Serviço", key="novo_nome_tipo_servico")
                if st.form_submit_button("🔄 Alterar Tipo", key="btn_alterar_tipo_servico"):
                    alterou_tipo_servico = False
                    if tipo_servico_para_alterar == "Selecione":
                        st.warning("Escolha um tipo de serviço para alterar.")
                    elif not str(novo_nome_servico or "").strip():
                        st.warning("Informe o novo nome do tipo de serviço.")
                    else:
                        novo_nome_clean = novo_nome_servico.strip()
                        with conn() as c:
                            existente = c.execute("SELECT 1 FROM tipos_servico_troca WHERE nome=?", (novo_nome_clean,)).fetchone()
                            if existente:
                                st.warning("Já existe um tipo de serviço com esse nome.")
                            else:
                                updated_tipos = c.execute("UPDATE tipos_servico_troca SET nome=? WHERE nome=?", (novo_nome_clean, tipo_servico_para_alterar)).rowcount
                                updated_controle = c.execute("UPDATE controle_trocas SET tipo_servico=? WHERE tipo_servico=?", (novo_nome_clean, tipo_servico_para_alterar)).rowcount
                                if updated_tipos > 0:
                                    st.success(f"Tipo de serviço alterado com sucesso. ({updated_tipos} tipo(s), {updated_controle} movimento(s) atualizados)")
                                    alterou_tipo_servico = True
                                else:
                                    st.warning("Nenhum tipo de serviço foi alterado. Verifique a seleção.")
                        if alterou_tipo_servico:
                            st.rerun()

        st.markdown("---")
        with st.form("f_troca_v11", clear_on_submit=True):
            opcoes_veiculos_troca = lista_veiculos_full if lista_veiculos_full else apenas_placas
            veic_t = st.selectbox("Veículo (Placa - Descrição)", opcoes_veiculos_troca, key="veic_troca_new")
            if " - " in str(veic_t):
                placa_t, descricao_t = str(veic_t).split(" - ", 1)
            else:
                placa_t, descricao_t = str(veic_t), ""
            serv_t = st.selectbox("Tipo de Serviço", lista_servicos_troca)
            
            c_t1, c_t2 = st.columns(2)
            dt_t = c_t1.date_input("Data do Serviço", format="DD/MM/YYYY")
            km_t = c_t2.number_input("KM Atual", step=1.0)
            
            c_t3, c_t4 = st.columns(2)
            km_prox = c_t3.number_input("Próxima KM", step=1.0)
            dt_venc_t = c_t4.date_input("Vencimento (Data)", value=dt_t + timedelta(days=180), format="DD/MM/YYYY")
            dias_alerta_t = c_t4.number_input("Ativar popup faltando (dias)", min_value=0, max_value=365, value=30, step=1)
            
            det_t = st.text_area("Detalhes/Peças Utilizadas (Ex: Marca do pneu, DOT, etc)")
            
            if st.form_submit_button("💾 Gravar", key="btn_troca_cadastro_gravar"):
                with conn() as c:
                    c.execute("""INSERT INTO controle_trocas (tipo_servico, data_servico, veiculo_placa, descricao_veiculo, km_atual, km_proxima, detalhes, data_vencimento, dias_alerta) 
                                 VALUES (?,?,?,?,?,?,?,?,?)""", 
                              (serv_t, dt_t.isoformat(), placa_t, descricao_t, km_t, km_prox, det_t, dt_venc_t.isoformat(), int(dias_alerta_t)))
                alerta_gravado()
                st.rerun()

    with col_hist:
        st.markdown("### 📋 Histórico de Trocas")
        with conn() as c:
            df_t = pd.read_sql("SELECT * FROM controle_trocas ORDER BY data_servico DESC", c)
        
        if not df_t.empty:
            df_t["veiculo_label_filtro"] = df_t.apply(
                lambda r: (
                    f"{str(r.get('veiculo_placa') or '').strip()} - {str(r.get('descricao_veiculo') or '').strip()}"
                    if str(r.get("descricao_veiculo") or "").strip()
                    else str(r.get("veiculo_placa") or "").strip()
                ),
                axis=1,
            )
            opcoes_filtro_troca = ["Todos"] + sorted(
                [v for v in df_t["veiculo_label_filtro"].dropna().unique().tolist() if str(v).strip()]
            )
            filtro_veiculo_troca = st.selectbox(
                "Filtrar por Veículo",
                opcoes_filtro_troca,
                key="filtro_veiculo_troca",
            )
            if filtro_veiculo_troca != "Todos":
                df_t = df_t[df_t["veiculo_label_filtro"] == filtro_veiculo_troca]

        if not df_t.empty:
            hoje_troca = date.today()
            for idx, r in df_t.iterrows():
                ed_key_t = f"edit_troca_{r['id']}"
                if ed_key_t not in st.session_state: st.session_state[ed_key_t] = False

                dt_s_br = datetime.strptime(r['data_servico'], '%Y-%m-%d').strftime('%d/%m/%Y') if r['data_servico'] else "-"
                # Ícone visual para pneus
                prefixo = "🛞" if "Pneu" in r['tipo_servico'] else "🔧"
                veiculo_desc_hist = str(r.get("descricao_veiculo") or "").strip()
                veiculo_info_hist = f"{r['veiculo_placa']} - {veiculo_desc_hist}" if veiculo_desc_hist else str(r["veiculo_placa"])
                exp_title = f"{prefixo} {dt_s_br} - {veiculo_info_hist} - {r['tipo_servico']}"
                
                with st.expander(exp_title):
                    if not st.session_state[ed_key_t]:
                        # --- VISUALIZAÇÃO ---
                        c1, c2, c3 = st.columns(3)
                        placa_txt = str(r.get("veiculo_placa") or "").strip()
                        desc_txt = str(r.get("descricao_veiculo") or "").strip()
                        c1.write(f"**Veículo/Placa:**\n{(placa_txt + ' - ' + desc_txt) if desc_txt else placa_txt}")
                        c2.write(f"**KM Realizada:**\n{r['km_atual']:,}".replace(",", "."))
                        dt_v_br = datetime.strptime(r['data_vencimento'], '%Y-%m-%d').strftime('%d/%m/%Y') if r['data_vencimento'] else "-"
                        c3.write(f"**Venc. Data:**\n{dt_v_br}")

                        c4, c5 = st.columns(2)
                        c4.write(f"**Próxima KM:**\n{r['km_proxima']:,}".replace(",", "."))
                        dias_alerta_troca = int(r.get("dias_alerta") or 30)
                        c5.write(f"**Popup faltando:**\n{dias_alerta_troca} dia(s)")

                        dt_v_calc = datetime.strptime(r['data_vencimento'], '%Y-%m-%d').date() if r['data_vencimento'] else None
                        if dt_v_calc is not None:
                            dias_para_vencer_troca = (dt_v_calc - hoje_troca).days
                            if dias_para_vencer_troca > 0:
                                st.caption(f"Faltam {dias_para_vencer_troca} dia(s) para vencer.")
                            elif dias_para_vencer_troca == 0:
                                st.warning("Vence hoje.")
                            elif abs(dias_para_vencer_troca) > dias_alerta_troca:
                                st.error(f"Já venceu o prazo de {dias_alerta_troca} dias (há {abs(dias_para_vencer_troca)} dia(s)).")
                            else:
                                st.warning(f"Vencido há {abs(dias_para_vencer_troca)} dia(s).")
                        
                        st.info(f"**Detalhes:** {r['detalhes'] or 'Sem observações'}")
                        
                        b1, b2 = st.columns(2)
                        if b1.button("✏️ EDITAR", key=f"btn_ed_t_{r['id']}", use_container_width=True):
                            st.session_state[ed_key_t] = True
                            st.rerun()
                        if b2.button("🗑️ EXCLUIR", key=f"btn_del_t_{r['id']}", type="primary", use_container_width=True):
                            with conn() as c: c.execute("DELETE FROM controle_trocas WHERE id=?", (r['id'],))
                            st.rerun()
                    else:
                        # --- EDIÇÃO ---
                        with st.form(f"form_ed_t_{r['id']}"):
                            opcoes_veiculos_troca = lista_veiculos_full if lista_veiculos_full else apenas_placas
                            placa_atual = str(r.get("veiculo_placa") or "").strip()
                            desc_atual = str(r.get("descricao_veiculo") or "").strip()
                            veiculo_label_atual = f"{placa_atual} - {desc_atual}" if desc_atual else placa_atual
                            if veiculo_label_atual and veiculo_label_atual not in opcoes_veiculos_troca:
                                opcoes_veiculos_troca = list(opcoes_veiculos_troca) + [veiculo_label_atual]
                            idx_veic_edit = opcoes_veiculos_troca.index(veiculo_label_atual) if veiculo_label_atual in opcoes_veiculos_troca else 0
                            veic_t_edit = st.selectbox("Veículo (Placa - Descrição)", opcoes_veiculos_troca, index=idx_veic_edit, key=f"troca_veic_edit_{r['id']}")
                            if " - " in str(veic_t_edit):
                                new_placa, new_desc = str(veic_t_edit).split(" - ", 1)
                            else:
                                new_placa, new_desc = str(veic_t_edit), ""

                            opcoes_servicos_edit = list(lista_servicos_troca)
                            tipo_servico_atual = str(r["tipo_servico"] or "").strip()
                            if tipo_servico_atual and tipo_servico_atual not in opcoes_servicos_edit:
                                opcoes_servicos_edit.append(tipo_servico_atual)
                            idx_serv = opcoes_servicos_edit.index(tipo_servico_atual) if tipo_servico_atual in opcoes_servicos_edit else 0

                            new_serv = st.selectbox("Tipo de Serviço", opcoes_servicos_edit, index=idx_serv, key=f"troca_serv_edit_{r['id']}")
                            
                            ce1, ce2 = st.columns(2)
                            d_s_v = datetime.strptime(r['data_servico'], '%Y-%m-%d').date() if r['data_servico'] else datetime.now().date()
                            new_dt_s = ce1.date_input("Data do Serviço", value=d_s_v, format="DD/MM/YYYY")
                            new_km_a = ce2.number_input("KM Atual", value=float(r['km_atual'] or 0))
                            
                            ce3, ce4, ce5 = st.columns(3)
                            new_km_p = ce3.number_input("Próxima KM", value=float(r['km_proxima'] or 0))
                            d_v_v = datetime.strptime(r['data_vencimento'], '%Y-%m-%d').date() if r['data_vencimento'] else datetime.now().date()
                            new_dt_v = ce4.date_input("Vencimento (Data)", value=d_v_v, format="DD/MM/YYYY")
                            new_dias_alerta = ce5.number_input(
                                "Ativar popup faltando (dias)",
                                min_value=0,
                                max_value=365,
                                value=int(r.get("dias_alerta") or 30),
                                step=1,
                            )
                            
                            new_det = st.text_area("Detalhes", value=r['detalhes'] or "")
                            
                            be1, be2 = st.columns(2)
                            if be1.form_submit_button("💾 Gravar", use_container_width=True, key=f"btn_troca_edicao_gravar_{r['id']}"):
                                with conn() as c:
                                    c.execute("""UPDATE controle_trocas SET 
                                                 tipo_servico=?, data_servico=?, veiculo_placa=?, descricao_veiculo=?, km_atual=?, km_proxima=?, detalhes=?, data_vencimento=?, dias_alerta=? 
                                                 WHERE id=?""", 
                                              (new_serv, new_dt_s.isoformat(), new_placa, new_desc, new_km_a, new_km_p, new_det, new_dt_v.isoformat(), int(new_dias_alerta), r['id']))
                                st.session_state[ed_key_t] = False
                                alerta_gravado()
                                st.rerun()
                            if be2.form_submit_button("❌ CANCELAR", use_container_width=True):
                                st.session_state[ed_key_t] = False
                                st.rerun()
        else:
            st.info("Nenhum registro de troca encontrado para o filtro selecionado.")

# =========================
# ABA 14 - FRETE LIQUIDO NO PERIODO
# =========================
with aba14:
    st.subheader("💵 Frete Líquido no Período")
    opcoes_placa_frete_liq = ["Todas as placas"] + apenas_placas
    placa_padrao_frete_liq = placa_filtro_calculo if placa_filtro_calculo in apenas_placas else "Todas as placas"
    filtro_placa_frete_liq = st.selectbox(
        "Filtrar por Placa",
        opcoes_placa_frete_liq,
        index=opcoes_placa_frete_liq.index(placa_padrao_frete_liq),
        key="filtro_placa_frete_liq",
        format_func=rotulo_placa_com_descricao,
    )
    placa_filtro_frete_liq = filtro_placa_frete_liq if filtro_placa_frete_liq != "Todas as placas" else None
    placa_rateio_frete_liq = placa_filtro_frete_liq or "GERAL"
    st.caption(
        f"Período: {filtro_ini.strftime('%d/%m/%Y')} até {filtro_fim.strftime('%d/%m/%Y')}"
        + (f" | Placa: {rotulo_placa_com_descricao(placa_filtro_frete_liq)}" if placa_filtro_frete_liq else " | Placa: Todas as placas")
    )

    with conn() as c:
        df_viagens_liq = pd.read_sql(
            """SELECT data, origem, destino, veiculo_placa, km, toneladas, valor_ton, valor_km, tipo_cobranca, qtd_viagens, pedagio, gasto_extra, pagto_estadia, valor_adicional_frete, diesel, consumo
               FROM viagens
               WHERE date(data) BETWEEN ? AND ?""",
            c,
            params=(filtro_ini.isoformat(), filtro_fim.isoformat()),
        )
        df_abs_periodo = pd.read_sql(
            """SELECT tipo_combustivel, qtde_litros, total_gasto, veiculo_placa
               FROM abastecimentos
               WHERE date(data) BETWEEN ? AND ?""",
            c,
            params=(filtro_ini.isoformat(), filtro_fim.isoformat()),
        )
        df_abs_ref = pd.read_sql(
            """SELECT id, data, km_inicial, tipo_combustivel, qtde_litros, valor_unit, veiculo_placa
               FROM abastecimentos
               WHERE data <= ?
               ORDER BY data ASC, id ASC""",
            c,
            params=(filtro_fim.isoformat(),),
        )

    if placa_filtro_frete_liq and not df_viagens_liq.empty and "veiculo_placa" in df_viagens_liq.columns:
        placa_ref_liq = str(placa_filtro_frete_liq).strip().upper()
        df_viagens_liq = df_viagens_liq[
            df_viagens_liq["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_liq
        ].copy()

    if placa_filtro_frete_liq and not df_abs_periodo.empty and "veiculo_placa" in df_abs_periodo.columns:
        placa_ref_abs_liq = str(placa_filtro_frete_liq).strip().upper()
        df_abs_periodo = df_abs_periodo[
            df_abs_periodo["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_abs_liq
        ].copy()
    if placa_filtro_frete_liq and not df_abs_ref.empty and "veiculo_placa" in df_abs_ref.columns:
        placa_ref_abs_liq = str(placa_filtro_frete_liq).strip().upper()
        df_abs_ref = df_abs_ref[
            df_abs_ref["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_abs_liq
        ].copy()

    if not df_viagens_liq.empty:
        df_viagens_liq["km"] = pd.to_numeric(df_viagens_liq["km"], errors="coerce").fillna(0.0)
        df_viagens_liq["toneladas"] = pd.to_numeric(df_viagens_liq["toneladas"], errors="coerce").fillna(0.0)
        df_viagens_liq["valor_ton"] = pd.to_numeric(df_viagens_liq["valor_ton"], errors="coerce").fillna(0.0)
        if "valor_km" not in df_viagens_liq.columns:
            df_viagens_liq["valor_km"] = 0.0
        if "tipo_cobranca" not in df_viagens_liq.columns:
            df_viagens_liq["tipo_cobranca"] = "TONELADA"
        df_viagens_liq["valor_km"] = pd.to_numeric(df_viagens_liq["valor_km"], errors="coerce").fillna(0.0)
        df_viagens_liq["tipo_cobranca"] = df_viagens_liq["tipo_cobranca"].astype(str).str.upper().str.strip()
        if "qtd_viagens" not in df_viagens_liq.columns:
            df_viagens_liq["qtd_viagens"] = 1
        if "gasto_extra" not in df_viagens_liq.columns:
            df_viagens_liq["gasto_extra"] = 0.0
        if "pedagio" not in df_viagens_liq.columns:
            df_viagens_liq["pedagio"] = 0.0
        if "pagto_estadia" not in df_viagens_liq.columns:
            df_viagens_liq["pagto_estadia"] = 0.0
        if "valor_adicional_frete" not in df_viagens_liq.columns:
            df_viagens_liq["valor_adicional_frete"] = 0.0
        if "diesel" not in df_viagens_liq.columns:
            df_viagens_liq["diesel"] = 0.0
        if "consumo" not in df_viagens_liq.columns:
            df_viagens_liq["consumo"] = 0.0
        df_viagens_liq["qtd_viagens"] = pd.to_numeric(df_viagens_liq["qtd_viagens"], errors="coerce").fillna(1.0)
        # Garante quantidade inteira e mínima de 1 para refletir o número real de viagens.
        df_viagens_liq["qtd_viagens"] = df_viagens_liq["qtd_viagens"].apply(lambda x: max(1, int(round(float(x)))))
        df_viagens_liq["pedagio"] = pd.to_numeric(df_viagens_liq["pedagio"], errors="coerce").fillna(0.0)
        df_viagens_liq["gasto_extra"] = pd.to_numeric(df_viagens_liq["gasto_extra"], errors="coerce").fillna(0.0)
        df_viagens_liq["pagto_estadia"] = pd.to_numeric(df_viagens_liq["pagto_estadia"], errors="coerce").fillna(0.0)
        df_viagens_liq["valor_adicional_frete"] = pd.to_numeric(df_viagens_liq["valor_adicional_frete"], errors="coerce").fillna(0.0)
        df_viagens_liq["diesel"] = pd.to_numeric(df_viagens_liq["diesel"], errors="coerce").fillna(0.0)
        df_viagens_liq["consumo"] = pd.to_numeric(df_viagens_liq["consumo"], errors="coerce").fillna(0.0)
        origem_norm_liq = df_viagens_liq["origem"].fillna("").astype(str).str.strip().str.upper()
        destino_norm_liq = df_viagens_liq["destino"].fillna("").astype(str).str.strip().str.upper()
        mask_od_diferente_liq = origem_norm_liq != destino_norm_liq
        qtd_ignoradas_liq = int((~mask_od_diferente_liq).sum())
        df_viagens_liq = df_viagens_liq.loc[mask_od_diferente_liq].copy()
        if qtd_ignoradas_liq > 0:
            st.info(
                f"{qtd_ignoradas_liq} viagem(ns) com origem = destino foram desconsideradas nesta aba para manter o mesmo cálculo da Análise."
            )
        df_viagens_liq["frete_unitario"] = df_viagens_liq.apply(
            lambda r: (r["km"] * r["valor_km"]) if r["tipo_cobranca"] == "KM" else (r["toneladas"] * r["valor_ton"]),
            axis=1,
        )
        df_viagens_liq["total_frete"] = df_viagens_liq["frete_unitario"] * df_viagens_liq["qtd_viagens"]
        df_viagens_liq["km_total"] = df_viagens_liq["km"] * df_viagens_liq["qtd_viagens"]
        df_viagens_liq["pedagio_total"] = df_viagens_liq["pedagio"] * df_viagens_liq["qtd_viagens"]
        df_viagens_liq["gasto_extra_total"] = df_viagens_liq["gasto_extra"] * df_viagens_liq["qtd_viagens"]
        df_viagens_liq["pagto_estadia_total"] = df_viagens_liq["pagto_estadia"] * df_viagens_liq["qtd_viagens"]
        df_viagens_liq["valor_adicional_frete_total"] = df_viagens_liq["valor_adicional_frete"] * df_viagens_liq["qtd_viagens"]
        consumo_valido_liq = df_viagens_liq["consumo"] > 0
        df_viagens_liq["litros_diesel_total"] = 0.0
        df_viagens_liq.loc[consumo_valido_liq, "litros_diesel_total"] = (
            df_viagens_liq.loc[consumo_valido_liq, "km_total"] / df_viagens_liq.loc[consumo_valido_liq, "consumo"]
        )
        df_viagens_liq["valor_diesel_total"] = df_viagens_liq["litros_diesel_total"] * df_viagens_liq["diesel"]
    else:
        df_viagens_liq = pd.DataFrame(columns=["km", "qtd_viagens", "total_frete", "pedagio", "gasto_extra", "pagto_estadia", "valor_adicional_frete", "km_total", "pedagio_total", "gasto_extra_total", "pagto_estadia_total", "valor_adicional_frete_total", "litros_diesel_total", "valor_diesel_total"])

    if not df_abs_periodo.empty:
        df_abs_periodo["tipo_combustivel"] = df_abs_periodo["tipo_combustivel"].apply(normalizar_tipo_combustivel)
        df_abs_periodo["qtde_litros"] = pd.to_numeric(df_abs_periodo["qtde_litros"], errors="coerce").fillna(0.0)
        df_abs_periodo["total_gasto"] = pd.to_numeric(df_abs_periodo["total_gasto"], errors="coerce").fillna(0.0)
        litros_arla_periodo = float(
            df_abs_periodo[df_abs_periodo["tipo_combustivel"].str.contains("ARLA", na=False)]["qtde_litros"].sum()
        )
        valor_arla_periodo = float(
            df_abs_periodo[df_abs_periodo["tipo_combustivel"].str.contains("ARLA", na=False)]["total_gasto"].sum()
        )
    else:
        litros_arla_periodo = 0.0
        valor_arla_periodo = 0.0

    if not df_abs_ref.empty:
        df_abs_ref["km_inicial"] = pd.to_numeric(df_abs_ref["km_inicial"], errors="coerce")
        df_abs_ref["qtde_litros"] = pd.to_numeric(df_abs_ref["qtde_litros"], errors="coerce").fillna(0.0)
        df_abs_ref["valor_unit"] = pd.to_numeric(df_abs_ref["valor_unit"], errors="coerce").fillna(0.0)
        df_abs_ref["tipo_combustivel"] = df_abs_ref["tipo_combustivel"].apply(normalizar_tipo_combustivel)
    else:
        df_abs_ref = pd.DataFrame(columns=["km_inicial", "tipo_combustivel", "qtde_litros", "valor_unit"])

    qtde_viagem = int(df_viagens_liq["qtd_viagens"].sum()) if not df_viagens_liq.empty else 0
    tem_viagem_frete_liq = qtde_viagem > 0
    valor_total_viagem = float(df_viagens_liq["total_frete"].sum()) if not df_viagens_liq.empty else 0.0
    valor_total_pedagio = float(df_viagens_liq["pedagio_total"].sum()) if not df_viagens_liq.empty else 0.0
    valor_total_gasto_extra = float(df_viagens_liq["gasto_extra_total"].sum()) if not df_viagens_liq.empty else 0.0
    valor_total_pagto_estadia = float(df_viagens_liq["pagto_estadia_total"].sum()) if not df_viagens_liq.empty else 0.0
    valor_total_adicional_frete = float(df_viagens_liq["valor_adicional_frete_total"].sum()) if not df_viagens_liq.empty else 0.0
    valor_total_viagem += (valor_total_pagto_estadia + valor_total_adicional_frete)
    if tem_viagem_frete_liq:
        valor_total_viagem += frete_fixo_rateado_periodo(filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
    valor_pago_por_viagem = (valor_total_viagem / qtde_viagem) if qtde_viagem > 0 else 0.0
    km_total = float(df_viagens_liq["km_total"].sum()) if not df_viagens_liq.empty else 0.0
    km_por_viagem = (km_total / qtde_viagem) if qtde_viagem > 0 else 0.0
    qtd_datas_periodo = df_viagens_liq["data"].nunique() if not df_viagens_liq.empty else 0

    # Referência de consumo por KM baseada no mesmo raciocínio da aba Abastecimento:
    # diferença entre os 2 últimos KMs diferentes / litros do KM final.
    kms_ref = sorted(df_abs_ref["km_inicial"].dropna().unique().tolist()) if not df_abs_ref.empty else []
    km_ref_anterior = kms_ref[-2] if len(kms_ref) >= 2 else None
    km_ref_final = kms_ref[-1] if len(kms_ref) >= 2 else None
    distancia_ref = (km_ref_final - km_ref_anterior) if km_ref_anterior is not None and km_ref_final is not None else 0.0
    consumo_diesel_ref = 0.0
    litros_diesel_ref = 0.0
    if not df_abs_ref.empty and distancia_ref > 0 and km_ref_final is not None:
        df_abs_ref_km_final = df_abs_ref[df_abs_ref["km_inicial"] == km_ref_final].copy()
        litros_diesel_ref = float(
            df_abs_ref_km_final[
                df_abs_ref_km_final["tipo_combustivel"].str.contains("DIESEL", na=False)
            ]["qtde_litros"].sum()
        )
        consumo_diesel_ref = (distancia_ref / litros_diesel_ref) if litros_diesel_ref > 0 else 0.0

    def calcula_por_ultimo_abastecimento(df_ref, termo_tipo):
        if df_ref.empty or distancia_ref <= 0:
            return 0.0, 0.0, 0.0, 0.0
        df_tipo = df_ref[df_ref["tipo_combustivel"].str.contains(termo_tipo, na=False)].copy()
        if df_tipo.empty:
            return 0.0, 0.0, 0.0, 0.0
        ult = df_tipo.iloc[-1]
        litros_ultimo = float(ult["qtde_litros"] or 0.0)
        valor_unit_ultimo = float(ult["valor_unit"] or 0.0)
        if litros_ultimo <= 0:
            return 0.0, 0.0, 0.0, valor_unit_ultimo
        consumo_km_l = distancia_ref / litros_ultimo
        litros_estimados = (km_total / consumo_km_l) if consumo_km_l > 0 else 0.0
        valor_estimado = litros_estimados * valor_unit_ultimo
        return consumo_km_l, litros_estimados, valor_estimado, valor_unit_ultimo

    _, _, _, preco_diesel_ref = calcula_por_ultimo_abastecimento(df_abs_ref, "DIESEL")
    consumo_arla_ref, _, _, preco_arla_ref = calcula_por_ultimo_abastecimento(df_abs_ref, "ARLA")
    litros_arla_total = litros_arla_periodo
    valor_arla_total = valor_arla_periodo
    litros_diesel_total = float(df_viagens_liq["litros_diesel_total"].sum()) if not df_viagens_liq.empty else 0.0
    valor_diesel_total = float(df_viagens_liq["valor_diesel_total"].sum()) if not df_viagens_liq.empty else 0.0

    if not df_viagens_liq.empty:
        df_viagens_liq = aplicar_parametros_por_data(df_viagens_liq, col_data="data")
        df_viagens_liq["receita_comissionavel_viagem"] = (
            pd.to_numeric(df_viagens_liq["total_frete"], errors="coerce").fillna(0.0)
            + pd.to_numeric(df_viagens_liq["pagto_estadia_total"], errors="coerce").fillna(0.0)
        )
        df_viagens_liq["receita_viagem"] = (
            df_viagens_liq["receita_comissionavel_viagem"]
            + pd.to_numeric(df_viagens_liq["valor_adicional_frete_total"], errors="coerce").fillna(0.0)
        )
        valor_motorista_viagens = float(
            (df_viagens_liq["receita_comissionavel_viagem"] * (pd.to_numeric(df_viagens_liq["param_motora_pct"], errors="coerce").fillna(0.0) / 100.0)).sum()
        )
        valor_imposto_viagens = float(
            (df_viagens_liq["receita_viagem"] * (pd.to_numeric(df_viagens_liq["param_imposto_pct"], errors="coerce").fillna(0.0) / 100.0)).sum()
        )
    else:
        valor_motorista_viagens = 0.0
        valor_imposto_viagens = 0.0
    if tem_viagem_frete_liq:
        valor_motorista_frete_fixo = float(
            (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq) / 30.0
             * (serie_parametro_diaria("motora_pct", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq) / 100.0)).sum()
        )
        valor_imposto_frete_fixo = float(
            (serie_parametro_diaria("valor_frete_mensal_fixo", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq) / 30.0
             * (serie_parametro_diaria("imposto_pct", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq) / 100.0)).sum()
        )
    else:
        valor_motorista_frete_fixo = 0.0
        valor_imposto_frete_fixo = 0.0
    valor_motorista_10 = valor_motorista_viagens + valor_motorista_frete_fixo
    if tem_viagem_frete_liq:
        valor_motorista_fixo_rateado = valor_mensal_rateado_periodo("motora_fixo", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
        valor_seguro_rateado = valor_mensal_rateado_periodo("seguro", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
        valor_seguro_vida_motorista_rateado = valor_mensal_rateado_periodo("seguro_vida_motorista", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
        valor_financiamento_rateado = valor_mensal_rateado_periodo("financiamento", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
        valor_ipva_rateado = valor_anual_rateado_periodo("pagto_ipva", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
        valor_escritorio_rateado = valor_mensal_rateado_periodo("cmp_custo_escritorio", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
        valor_rastreador_rateado = valor_mensal_rateado_periodo("vl_custo_rastreador", filtro_ini, filtro_fim, veiculo_placa=placa_rateio_frete_liq)
    else:
        valor_motorista_fixo_rateado = 0.0
        valor_seguro_rateado = 0.0
        valor_seguro_vida_motorista_rateado = 0.0
        valor_financiamento_rateado = 0.0
        valor_ipva_rateado = 0.0
        valor_escritorio_rateado = 0.0
        valor_rastreador_rateado = 0.0
    valor_motorista_total = valor_motorista_10 + valor_motorista_fixo_rateado
    valor_imposto = valor_imposto_viagens + valor_imposto_frete_fixo
    if not df_viagens_liq.empty:
        valor_manutencao_desgaste = float(
            (df_viagens_liq["km_total"] * pd.to_numeric(df_viagens_liq["param_manut"], errors="coerce").fillna(0.0)).sum()
        )
        valor_pneu_desgaste = float(
            (df_viagens_liq["km_total"] * pd.to_numeric(df_viagens_liq["param_pneu"], errors="coerce").fillna(0.0)).sum()
        )
        valor_depreciacao_desgaste = float(
            (df_viagens_liq["km_total"] * pd.to_numeric(df_viagens_liq["param_depre"], errors="coerce").fillna(0.0)).sum()
        )
    else:
        valor_manutencao_desgaste = 0.0
        valor_pneu_desgaste = 0.0
        valor_depreciacao_desgaste = 0.0
    total_desgastes = valor_manutencao_desgaste + valor_pneu_desgaste + valor_depreciacao_desgaste
    valor_liquido = (
        valor_total_viagem
        - valor_diesel_total
        - valor_arla_total
        - valor_total_pedagio
        - valor_total_gasto_extra
        - valor_motorista_total
        - valor_escritorio_rateado
        - valor_rastreador_rateado
        - valor_seguro_rateado
        - valor_seguro_vida_motorista_rateado
        - valor_financiamento_rateado
        - valor_ipva_rateado
        - valor_imposto
        - total_desgastes
    )
    valor_liquido_pct = (valor_liquido / valor_total_viagem * 100.0) if valor_total_viagem > 0 else 0.0
    valor_bruto_km = (valor_total_viagem / km_total) if km_total > 0 else 0.0
    valor_liquido_km = (valor_liquido / km_total) if km_total > 0 else 0.0

    st.caption("Resumo")
    l1c1, l1c2, l1c3, l1c4, l1c5, l1c6 = st.columns(6)
    l1c1.metric("Qtd Viagens", f"{qtde_viagem}")
    l1c2.metric("Receita Total", brl(valor_total_viagem))
    l1c3.metric("Valor Líquido", brl(valor_liquido))
    l1c4.metric("Margem Líquida", f"{valor_liquido_pct:.2f}%")
    l1c5.metric("KM Total", f"{km_total:,.2f} KM".replace(",", "."))
    l1c6.metric(
        "Bruto/KM",
        (f"R$ {valor_bruto_km:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".") + "/KM") if km_total > 0 else "-",
    )

    l2c1, l2c2, l2c3, l2c4, l2c5, l2c6 = st.columns(6)
    l2c1.metric(
        "Valor Líquido por KM no Período",
        (f"R$ {valor_liquido_km:,.4f}".replace(",", "X").replace(".", ",").replace("X", ".") + "/KM") if km_total > 0 else "-",
    )
    if qtd_datas_periodo <= 1:
        l2c2.metric("Valor/Viagem", brl(valor_pago_por_viagem))
        l2c3.metric("KM/Viagem", f"{km_por_viagem:,.2f} KM".replace(",", "."))
    else:
        l2c2.metric("Valor/Viagem", "-")
        l2c3.metric("KM/Viagem", "-")
        st.caption("Valor/Viagem e KM/Viagem aparecem apenas com filtro em data única.")
    l2c4.metric("Litros Diesel", f"{litros_diesel_total:.2f} L")
    l2c5.metric("Gasto Diesel", brl(valor_diesel_total))
    l2c6.metric("Média Diesel", f"{consumo_diesel_ref:.2f} KM/L" if consumo_diesel_ref > 0 else "-")

    st.caption("Custos")
    l3c1, l3c2, l3c3, l3c4, l3c5, l3c6 = st.columns(6)
    l3c1.metric("Litros Arla", f"{litros_arla_total:.2f} L")
    l3c2.metric("Gasto Arla", brl(valor_arla_total))
    l3c3.metric("Pedágio", brl(valor_total_pedagio))
    l3c4.metric("Gasto Extra", brl(valor_total_gasto_extra))
    l3c5.metric("Pagto Estadia", brl(valor_total_pagto_estadia))
    l3c6.metric("Imposto", brl(valor_imposto))

    l4c1, l4c2, l4c3, l4c4, l4c5, l4c6, l4c7, l4c8 = st.columns(8)
    l4c1.metric("Motorista Rateado", brl(valor_motorista_fixo_rateado))
    l4c2.metric("Comissão Motorista", brl(valor_motorista_10))
    l4c3.metric("Escritório Rateado", brl(valor_escritorio_rateado))
    l4c4.metric("Rastreador Rateado", brl(valor_rastreador_rateado))
    l4c5.metric("Seguro Rateado", brl(valor_seguro_rateado))
    l4c6.metric("Seguro Vida Motorista Rateado", brl(valor_seguro_vida_motorista_rateado))
    l4c7.metric("Financiamento Rateado", brl(valor_financiamento_rateado))
    l4c8.metric("IPVA Rateado", brl(valor_ipva_rateado))

    l5c1, l5c2, l5c3, l5c4 = st.columns(4)
    l5c1.metric("Manutenção Parâmetros", brl(valor_manutencao_desgaste))
    l5c2.metric("Pneu Parâmetros", brl(valor_pneu_desgaste))
    l5c3.metric("Depreciação Parâmetros", brl(valor_depreciacao_desgaste))
    l5c4.metric("Tl manutenão/pneu/depreciação", brl(total_desgastes))

    st.markdown("##### Componentes do Cálculo do Valor Líquido")
    df_componentes_liquido = pd.DataFrame(
        {
            "Componente": [
                "Receita Total (Frete + Estadia + Frete Fixo)",
                "Diesel",
                "Arla",
                "Pedágio",
                "Gasto Extra",
                "Motorista (Comissão + Fixo Rateado)",
                "Escritório (Rateado)",
                "Rastreador (Rateado)",
                "Seguro (Rateado)",
                "Seguro Vida Motorista Mensal (Rateado)",
                "Financiamento (Rateado)",
                "IPVA (Rateado)",
                "Imposto",
                "Custo Manut/Pneu/Depreciação",
                "Valor Líquido",
            ],
            "Tipo": [
                "Receita",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Desconto",
                "Resultado",
            ],
            "Valor": [
                valor_total_viagem,
                valor_diesel_total,
                valor_arla_total,
                valor_total_pedagio,
                valor_total_gasto_extra,
                valor_motorista_total,
                valor_escritorio_rateado,
                valor_rastreador_rateado,
                valor_seguro_rateado,
                valor_seguro_vida_motorista_rateado,
                valor_financiamento_rateado,
                valor_ipva_rateado,
                valor_imposto,
                total_desgastes,
                valor_liquido,
            ],
        }
    )
    st.dataframe(
        df_componentes_liquido,
        use_container_width=True,
        hide_index=True,
        column_config={
            "Componente": st.column_config.TextColumn("Componente"),
            "Tipo": st.column_config.TextColumn("Tipo"),
            "Valor": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
        },
    )
    if distancia_ref > 0:
        st.info(
            (
                f"Referência do último abastecimento: distância base {distancia_ref:,.0f} KM "
                f"({km_ref_final:,.0f} - {km_ref_anterior:,.0f}). "
                f"Diesel ref: {consumo_diesel_ref:.2f} KM/L (R$ {preco_diesel_ref:.2f}/L). "
                f"Arla ref: {consumo_arla_ref:.2f} KM/L (R$ {preco_arla_ref:.2f}/L)."
            ).replace(",", ".")
        )
    else:
        st.warning("Sem base suficiente de abastecimento para calcular consumo por KM (necessário 2 KMs de abastecimento diferentes).")

# =========================
# ABA 15 - FORNECEDORES
# =========================
with aba15:
    st.subheader("🏭 Cadastro de Fornecedores")
    if "fornecedor_editando" not in st.session_state:
        st.session_state.fornecedor_editando = False
    with st.expander("➕ Cadastro de Fornecedor", expanded=False):
        with conn() as c:
            codigos_numericos = [
                int(r[0]) for r in c.execute(
                    "SELECT codigo FROM fornecedores WHERE codigo GLOB '[0-9]*'"
                ).fetchall()
                if str(r[0]).isdigit()
            ]
            proximo_codigo = str(max(codigos_numericos) + 1) if codigos_numericos else "1"

        with st.form("form_fornecedor", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            cod_forn = c1.text_input("Código Fornecedor", value=proximo_codigo).strip().upper()
            nome_forn = c2.text_input("Nome do Fornecedor").strip()
            cnpj_forn = c3.text_input("CNPJ").strip()

            c4, c5, c6 = st.columns(3)
            ie_forn = c4.text_input("Insc. Est.").strip()
            end_forn = c5.text_input("Endereço").strip()
            numero_forn = c6.text_input("Numero").strip()

            c7, c8, c9 = st.columns(3)
            complemento_forn = c7.text_input("Complemento").strip()
            cidade_forn = c8.text_input("Cidade").strip()
            estado_forn = c9.text_input("Estado").strip()

            c10, c11, c12, c13 = st.columns(4)
            bairro_forn = c10.text_input("Bairro").strip()
            cep_forn = c11.text_input("CEP").strip()
            tel_forn = c12.text_input("Telefone").strip()
            pix_forn = c13.text_input("PIX").strip()

            c14, c15 = st.columns(2)
            email_forn = c14.text_input("E-mail").strip()
            responsavel_forn = c15.text_input("Responsável / Contato").strip()

            if st.form_submit_button("💾 Gravar", type="primary", key="btn_fornecedor_gravar"):
                if not cod_forn or not nome_forn:
                    st.warning("Informe Código Fornecedor e Nome do Fornecedor.")
                else:
                    try:
                        with conn() as c:
                            c.execute(
                                """INSERT INTO fornecedores
                                   (codigo, nome, cnpj, insc_est, endereco, numero, complemento, cidade,
                                    estado, bairro, cep, telefone, email, responsavel, pix)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    cod_forn, nome_forn, cnpj_forn, ie_forn, end_forn, numero_forn,
                                    complemento_forn, cidade_forn, estado_forn, bairro_forn, cep_forn,
                                    tel_forn, email_forn, responsavel_forn, pix_forn,
                                ),
                            )
                        limpar_cache_bootstrap()
                        alerta_gravado()
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.error("Código Fornecedor já cadastrado.")

    if st.button("📥 Copiar Oficinas para Fornecedores", key="btn_importar_oficinas_fornecedores", use_container_width=True):
        inseridos, atualizados = importar_oficinas_para_fornecedores()
        limpar_cache_bootstrap()
        st.success(f"Oficinas copiadas: {inseridos} novo(s) fornecedor(es) e {atualizados} atualizado(s).")
        st.rerun()

    with conn() as c:
        df_forn = pd.read_sql(
            """SELECT codigo, nome, cnpj, insc_est, endereco, numero, complemento, cidade,
                      estado, bairro, cep, telefone, email, responsavel, pix
               FROM fornecedores
               ORDER BY nome ASC""",
            c,
        )
    st.dataframe(df_forn, use_container_width=True, hide_index=True)
    c_fed1, c_fed2 = st.columns(2)
    if c_fed1.button("✏️ Editar", key="btn_fornecedor_editar", use_container_width=True, disabled=st.session_state.fornecedor_editando):
        st.session_state.fornecedor_editando = True
        st.rerun()
    if c_fed2.button("❌ Cancelar Edição", key="btn_fornecedor_cancelar_editar", use_container_width=True, disabled=not st.session_state.fornecedor_editando):
        st.session_state.fornecedor_editando = False
        st.rerun()
    if st.session_state.fornecedor_editando and not df_forn.empty:
        with conn() as c:
            df_forn_ed = pd.read_sql(
                """SELECT id, codigo, nome, cnpj, insc_est, endereco, numero, complemento, cidade,
                          estado, bairro, cep, telefone, email, responsavel, pix, origem_oficina_id
                   FROM fornecedores
                   ORDER BY nome ASC""",
                c,
            )
        df_forn_ed["Excluir"] = False
        df_forn_ed2 = st.data_editor(
            df_forn_ed,
            key="editor_fornecedores_cad",
            hide_index=True,
            use_container_width=True,
            column_config={
                "id": None,
                "origem_oficina_id": None,
                "Excluir": st.column_config.CheckboxColumn("🗑️ Excluir"),
            },
        )
        b_forn_salvar, b_forn_excluir = st.columns(2)
        if b_forn_salvar.button("💾 Gravar", key="btn_fornecedor_gravar_edicao", type="primary", use_container_width=True):
            df_validar_forn = df_forn_ed2.copy()
            df_validar_forn["codigo"] = df_validar_forn["codigo"].fillna("").astype(str).str.strip().str.upper()
            df_validar_forn = df_validar_forn[df_validar_forn["Excluir"] != True]
            codigos_vazios = df_validar_forn[df_validar_forn["codigo"] == ""]
            codigos_repetidos = df_validar_forn[df_validar_forn.duplicated("codigo", keep=False) & (df_validar_forn["codigo"] != "")]

            if not codigos_vazios.empty:
                st.warning("Todos os fornecedores precisam ter Código Fornecedor.")
            elif not codigos_repetidos.empty:
                repetidos = ", ".join(sorted(codigos_repetidos["codigo"].unique().tolist()))
                st.warning(f"Não foi possível gravar. Código Fornecedor repetido: {repetidos}.")
            else:
                try:
                    with conn() as c:
                        for _, r in df_validar_forn.iterrows():
                            c.execute(
                                """UPDATE fornecedores
                                   SET codigo=?, nome=?, cnpj=?, insc_est=?, endereco=?, numero=?, complemento=?,
                                       cidade=?, estado=?, bairro=?, cep=?, telefone=?, email=?, responsavel=?, pix=?
                                   WHERE id=?""",
                                (
                                    r["codigo"], r["nome"], r["cnpj"], r["insc_est"], r["endereco"], r["numero"],
                                    r["complemento"], r["cidade"], r["estado"], r["bairro"], r["cep"], r["telefone"],
                                    r["email"], r["responsavel"], r["pix"], int(r["id"]),
                                ),
                            )
                    limpar_cache_bootstrap()
                    alerta_gravado()
                    st.session_state.fornecedor_editando = False
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Não foi possível gravar: já existe fornecedor cadastrado com esse Código Fornecedor.")

        if b_forn_excluir.button("🗑️ Deletar Selecionados", key="btn_fornecedor_deletar_selecionados", type="primary", use_container_width=True):
            ids_excluir_forn = df_forn_ed2.loc[df_forn_ed2["Excluir"] == True, "id"].tolist()
            if not ids_excluir_forn:
                st.warning("Marque pelo menos um fornecedor na coluna Excluir.")
            else:
                bloqueados = []
                excluidos = 0
                with conn() as c:
                    for _, r in df_forn_ed2[df_forn_ed2["Excluir"] == True].iterrows():
                        fornecedor_id = int(r["id"])
                        usos = movimentos_fornecedor(c, fornecedor_id)
                        if usos:
                            bloqueados.append(f"{r['codigo']} - {r['nome']} ({'; '.join(usos)})")
                            continue
                        c.execute("DELETE FROM fornecedores WHERE id=?", (fornecedor_id,))
                        excluidos += 1

                limpar_cache_bootstrap()
                if excluidos:
                    st.success(f"{excluidos} fornecedor(es) deletado(s) com sucesso.")
                if bloqueados:
                    st.warning("Não foi possível deletar fornecedor(es) com movimento: " + " | ".join(bloqueados))
                st.rerun()

# =========================
# ABA 16 - OBRIGAÇÃO
# =========================
with aba16:
    st.subheader("📌 Cadastro de Obrigação")
    if "obrigacao_editando" not in st.session_state:
        st.session_state.obrigacao_editando = False
    with st.form("form_obrigacao", clear_on_submit=True):
        desc_ob = st.text_input("Descrição Obrigação").strip()
        if st.form_submit_button("💾 Gravar", type="primary", key="btn_obrigacao_gravar"):
            if not desc_ob:
                st.warning("Informe a descrição da obrigação.")
            else:
                try:
                    with conn() as c:
                        c.execute("INSERT INTO obrigacoes (descricao_obrigacao) VALUES (?)", (desc_ob,))
                    alerta_gravado()
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("Essa obrigação já está cadastrada.")

    with conn() as c:
        df_ob = pd.read_sql("SELECT id, descricao_obrigacao FROM obrigacoes ORDER BY descricao_obrigacao ASC", c)
    st.dataframe(df_ob, use_container_width=True, hide_index=True)
    c_ob1, c_ob2 = st.columns(2)
    if c_ob1.button("✏️ Editar", key="btn_obrigacao_editar", use_container_width=True, disabled=st.session_state.obrigacao_editando):
        st.session_state.obrigacao_editando = True
        st.rerun()
    if c_ob2.button("❌ Cancelar Edição", key="btn_obrigacao_cancelar_editar", use_container_width=True, disabled=not st.session_state.obrigacao_editando):
        st.session_state.obrigacao_editando = False
        st.rerun()
    if st.session_state.obrigacao_editando and not df_ob.empty:
        df_ob_ed = st.data_editor(df_ob, key="editor_obrigacoes_cad", hide_index=True, use_container_width=True, column_config={"id": None})
        if st.button("💾 Gravar", key="btn_obrigacao_gravar_edicao", type="primary", use_container_width=True):
            with conn() as c:
                for _, r in df_ob_ed.iterrows():
                    c.execute("UPDATE obrigacoes SET descricao_obrigacao=? WHERE id=?", (r["descricao_obrigacao"], int(r["id"])))
            alerta_gravado()
            st.session_state.obrigacao_editando = False
            st.rerun()

# =========================
# ABA 19 - COMPARATIVO CONSUMO DIESEL
# =========================
with aba19:
    st.subheader("⛽ Comparativo Consumo de Diesel")
    if "cmp_editando" not in st.session_state:
        st.session_state.cmp_editando = False

    st.caption(
        f"Período aplicado (mesmo filtro do Histórico): {filtro_ini.strftime('%d/%m/%Y')} até {filtro_fim.strftime('%d/%m/%Y')}"
    )

    if not df_db.empty:
        df_km_ref = df_db.copy()
        if "qtd_viagens" not in df_km_ref.columns:
            df_km_ref["qtd_viagens"] = 1
        df_km_ref["km"] = pd.to_numeric(df_km_ref["km"], errors="coerce").fillna(0.0)
        df_km_ref["qtd_viagens"] = pd.to_numeric(df_km_ref["qtd_viagens"], errors="coerce").fillna(1.0)
        df_km_ref["qtd_viagens"] = df_km_ref["qtd_viagens"].apply(lambda x: max(1, int(round(float(x)))))
        total_km_mensal = float((df_km_ref["km"] * df_km_ref["qtd_viagens"]).sum())
    else:
        total_km_mensal = 0.0

    valor_litro_padrao = float(p.get("cmp_valor_litro_diesel", p.get("diesel", v_diesel_sug or 0.0)))
    consumo_001_padrao = float(p.get("cmp_consumo_001", p.get("consumo", v_cons_sug or 2.5)))
    consumo_002_padrao = float(p.get("cmp_consumo_002", consumo_001_padrao))
    consumo_003_padrao = float(p.get("cmp_consumo_003", consumo_001_padrao))
    custo_escritorio_padrao = float(p.get("cmp_custo_escritorio", 0.0))

    if not st.session_state.cmp_editando:
        st.session_state["cmp_valor_litro_diesel"] = valor_litro_padrao
        st.session_state["cmp_consumo_001"] = consumo_001_padrao
        st.session_state["cmp_consumo_002"] = consumo_002_padrao
        st.session_state["cmp_consumo_003"] = consumo_003_padrao
        st.session_state["cmp_custo_escritorio"] = custo_escritorio_padrao

    with st.expander("➕ Configuração do Comparativo", expanded=False):
        c1, c2, c3, c4 = st.columns([2, 1, 1, 1])
        c1.metric("Total KM Mensal (Histórico)", format_br(total_km_mensal, casas_decimais=0))
        with c2:
            if st.button("✏️ Editar", use_container_width=True, disabled=st.session_state.cmp_editando):
                st.session_state.cmp_editando = True
                st.rerun()
        with c3:
            if st.button("💾 Gravar", use_container_width=True, type="primary", disabled=not st.session_state.cmp_editando, key="btn_cmp_gravar"):
                novo_valor_litro = float(st.session_state.get("cmp_valor_litro_diesel", valor_litro_padrao))
                novo_consumo_001 = float(st.session_state.get("cmp_consumo_001", consumo_001_padrao))
                novo_consumo_002 = float(st.session_state.get("cmp_consumo_002", consumo_002_padrao))
                novo_consumo_003 = float(st.session_state.get("cmp_consumo_003", consumo_003_padrao))
                novo_custo_escritorio = float(st.session_state.get("cmp_custo_escritorio", custo_escritorio_padrao))

                if st.session_state.simulacao_ativa:
                    if not st.session_state.p_simulado:
                        with conn() as c:
                            p_base = dict(c.execute("SELECT * FROM parametros WHERE id=1").fetchone())
                        st.session_state.p_simulado = p_base
                    st.session_state.p_simulado["cmp_valor_litro_diesel"] = novo_valor_litro
                    st.session_state.p_simulado["cmp_consumo_001"] = novo_consumo_001
                    st.session_state.p_simulado["cmp_consumo_002"] = novo_consumo_002
                    st.session_state.p_simulado["cmp_consumo_003"] = novo_consumo_003
                    st.session_state.p_simulado["cmp_custo_escritorio"] = novo_custo_escritorio
                    alerta_gravado()
                else:
                    with conn() as c:
                        c.execute(
                            """UPDATE parametros
                               SET cmp_valor_litro_diesel=?, cmp_consumo_001=?, cmp_consumo_002=?, cmp_consumo_003=?, cmp_custo_escritorio=?
                               WHERE id=1""",
                            (novo_valor_litro, novo_consumo_001, novo_consumo_002, novo_consumo_003, novo_custo_escritorio),
                        )
                        p_hist_base = c.execute(
                            """SELECT consumo, manut, pneu, depre, motora_fixo, motora_pct, seguro, seguro_vida_motorista, financiamento,
                                      pagto_ipva, vl_custo_rastreador, imposto_pct, valor_frete_mensal_fixo, qtde_pneu, vl_gasto_pneu_km
                               FROM parametros WHERE id=1"""
                        ).fetchone()
                        data_vigencia_cmp = str(p.get("data_filtro_ini", date.today().isoformat()) or date.today().isoformat())
                        c.execute(
                            """INSERT INTO parametros_historico (
                                   veiculo_placa, vigencia_data, consumo, manut, pneu, depre, motora_fixo, motora_pct,
                                   seguro, seguro_vida_motorista, financiamento, pagto_ipva, cmp_custo_escritorio, vl_custo_rastreador, imposto_pct, valor_frete_mensal_fixo,
                                   qtde_pneu, vl_gasto_pneu_km
                               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                               ON CONFLICT(veiculo_placa, vigencia_data) DO UPDATE SET
                                   cmp_custo_escritorio=excluded.cmp_custo_escritorio""",
                            (
                                "GERAL",
                                data_vigencia_cmp,
                                float(p_hist_base["consumo"] or 0.0),
                                float(p_hist_base["manut"] or 0.0),
                                float(p_hist_base["pneu"] or 0.0),
                                float(p_hist_base["depre"] or 0.0),
                                float(p_hist_base["motora_fixo"] or 0.0),
                                float(p_hist_base["motora_pct"] or 0.0),
                                float(p_hist_base["seguro"] or 0.0),
                                float(p_hist_base["seguro_vida_motorista"] or 0.0),
                                float(p_hist_base["financiamento"] or 0.0),
                                float(p_hist_base["pagto_ipva"] or 0.0),
                                float(novo_custo_escritorio or 0.0),
                                float(p_hist_base["vl_custo_rastreador"] or 0.0),
                                float(p_hist_base["imposto_pct"] or 0.0),
                                float(p_hist_base["valor_frete_mensal_fixo"] or 0.0),
                                float(p_hist_base["qtde_pneu"] or 0.0),
                                float(p_hist_base["vl_gasto_pneu_km"] or 0.0),
                            ),
                        )
                    alerta_gravado()

                st.session_state.cmp_editando = False
                st.rerun()
        with c4:
            st.write("")

        valor_litro_diesel = st.number_input(
            "Valor Litro Diesel (R$)",
            min_value=0.0,
            step=0.01,
            key="cmp_valor_litro_diesel",
            disabled=not st.session_state.cmp_editando,
        )
        custo_escritorio = st.number_input(
            "Custo Escritório (R$)",
            min_value=0.0,
            step=10.0,
            key="cmp_custo_escritorio",
            disabled=not st.session_state.cmp_editando,
        )

        st.markdown("#### Consumo por KM/L")
        p1, p2, p3 = st.columns(3)
        consumo_001 = p1.number_input(
            "Consumo por KM 001",
            min_value=0.0,
            step=0.01,
            key="cmp_consumo_001",
            disabled=not st.session_state.cmp_editando,
        )
        consumo_002 = p2.number_input(
            "Consumo por KM 002",
            min_value=0.0,
            step=0.01,
            key="cmp_consumo_002",
            disabled=not st.session_state.cmp_editando,
        )
        consumo_003 = p3.number_input(
            "Consumo por KM 003",
            min_value=0.0,
            step=0.01,
            key="cmp_consumo_003",
            disabled=not st.session_state.cmp_editando,
        )

    comparativos = [
        ("Consumo 001", float(consumo_001)),
        ("Consumo 002", float(consumo_002)),
        ("Consumo 003", float(consumo_003)),
    ]

    linhas_cmp = []
    for nome, consumo_km_l in comparativos:
        litros = (total_km_mensal / consumo_km_l) if consumo_km_l > 0 else None
        custo = (litros * float(valor_litro_diesel)) if litros is not None else None
        linhas_cmp.append(
            {
                "Cenário": nome,
                "Consumo (KM/L)": consumo_km_l,
                "Litros Estimados": litros,
                "Custo Diesel": custo,
                "Custo Escritório": float(custo_escritorio),
                "Custo Total": (custo + float(custo_escritorio)) if custo is not None else None,
            }
        )

    df_cmp = pd.DataFrame(linhas_cmp)
    if not df_cmp.empty:
        df_cmp["Custo Diesel"] = pd.to_numeric(df_cmp["Custo Diesel"], errors="coerce")
        df_cmp["Custo Escritório"] = pd.to_numeric(df_cmp["Custo Escritório"], errors="coerce").fillna(0.0)
        df_cmp["Custo Total"] = pd.to_numeric(df_cmp["Custo Total"], errors="coerce")
        df_cmp["Litros Estimados"] = pd.to_numeric(df_cmp["Litros Estimados"], errors="coerce")
        df_cmp_valid = df_cmp[df_cmp["Custo Total"].notna()].copy()

        if df_cmp_valid.empty:
            st.warning("Informe consumos maiores que zero para calcular o comparativo.")
        else:
            maior_custo = float(df_cmp_valid["Custo Total"].max())
            menor_custo = float(df_cmp_valid["Custo Total"].min())
            df_cmp["Economia vs Pior Cenário"] = maior_custo - df_cmp["Custo Total"]

            custo_ref_001 = float(df_cmp_valid.loc[df_cmp_valid["Cenário"] == "Consumo 001", "Custo Total"].iloc[0]) if not df_cmp_valid.loc[df_cmp_valid["Cenário"] == "Consumo 001"].empty else None
            custo_ref_002 = float(df_cmp_valid.loc[df_cmp_valid["Cenário"] == "Consumo 002", "Custo Total"].iloc[0]) if not df_cmp_valid.loc[df_cmp_valid["Cenário"] == "Consumo 002"].empty else None
            custo_ref_003 = float(df_cmp_valid.loc[df_cmp_valid["Cenário"] == "Consumo 003", "Custo Total"].iloc[0]) if not df_cmp_valid.loc[df_cmp_valid["Cenário"] == "Consumo 003"].empty else None
            df_cmp["Economia vs Consumo 001"] = (custo_ref_001 - df_cmp["Custo Total"]) if custo_ref_001 is not None else None

            st.dataframe(
                df_cmp.style.format(
                    {
                        "Consumo (KM/L)": "{:.2f}",
                        "Litros Estimados": "{:.2f}",
                        "Custo Diesel": "R$ {:,.2f}",
                        "Custo Escritório": "R$ {:,.2f}",
                        "Custo Total": "R$ {:,.2f}",
                        "Economia vs Pior Cenário": "R$ {:,.2f}",
                        "Economia vs Consumo 001": "R$ {:,.2f}",
                    }
                ),
                use_container_width=True,
                hide_index=True,
            )

            melhor_idx = df_cmp_valid["Custo Total"].idxmin()
            melhor = df_cmp_valid.loc[melhor_idx]
            e1, e2 = st.columns(2)
            e1.metric("Melhor Consumo", f"{melhor['Cenário']} ({melhor['Consumo (KM/L)']:.2f} KM/L)")
            e2.metric("Custo Mensal Melhor Cenário", brl(menor_custo))

            r1, r2 = st.columns(2)
            economia_002_vs_001 = (custo_ref_001 - custo_ref_002) if (custo_ref_001 is not None and custo_ref_002 is not None) else None
            economia_003_vs_001 = (custo_ref_001 - custo_ref_003) if (custo_ref_001 is not None and custo_ref_003 is not None) else None
            r1.metric("Economia Mensal Consumo 002 (vs 001)", brl(economia_002_vs_001) if economia_002_vs_001 is not None else "-")
            r2.metric("Economia Mensal Consumo 003 (vs 001)", brl(economia_003_vs_001) if economia_003_vs_001 is not None else "-")

            st.caption("A economia é estimada com base no KM total do período filtrado, no valor do litro e no custo de escritório informado.")

# =========================
# ABA 21 - PRAÇA PEDÁGIO
# =========================
with aba21:
    st.subheader("🛣️ Praça Pedágio")
    with conn() as c:
        df_rotas_pp = pd.read_sql(
            """SELECT origem, destino
               FROM rotas
               ORDER BY origem ASC, destino ASC""",
            c,
        )
    opcoes_rotas_pp = []
    if not df_rotas_pp.empty:
        opcoes_rotas_pp = (
            df_rotas_pp["origem"].fillna("").astype(str).str.strip()
            + " → "
            + df_rotas_pp["destino"].fillna("").astype(str).str.strip()
        ).tolist()
        opcoes_rotas_pp = [r for r in opcoes_rotas_pp if r != " → "]
        opcoes_rotas_pp = sorted(list(dict.fromkeys(opcoes_rotas_pp)))
    else:
        st.info("Cadastre rotas na aba '🛣️ KM Rotas' para selecionar aqui.")

    with st.expander("➕ Cadastro de Praça Pedágio", expanded=False):
        with st.form("f_praca_pedagio", clear_on_submit=True):
            pp1, pp2 = st.columns(2)
            rota_pp = pp1.selectbox(
                "Rota",
                options=opcoes_rotas_pp,
                index=None,
                placeholder="Selecione a rota do pré-cadastro",
                key="praca_pedagio_rota",
            )
            praca_pp = pp2.text_input("Praça de pedágio")
            pp3, pp4 = st.columns(2)
            rodovia_pp = pp3.text_input("Rodovia")
            concessionaria_pp = pp4.text_input("Concessionária")
            sentido_viagem_pp = st.text_input("Sentido Viagem")
            pp5, pp6, pp7 = st.columns(3)
            quantidade_eixos_pp = pp5.number_input("Quantidade Eixos", min_value=1.0, step=1.0, value=1.0, format="%.0f")
            valor_eixo_pp = pp6.number_input("Valor por Eixo", min_value=0.0, step=0.000001, format="%.6f")
            valor_todos_eixos_pp = float(quantidade_eixos_pp) * float(valor_eixo_pp)
            pp7.number_input("Valor Todos os Eixos", min_value=0.0, step=0.01, format="%.2f", value=float(valor_todos_eixos_pp), disabled=True)

            if st.form_submit_button("💾 Gravar", key="btn_praca_pedagio_gravar"):
                if not str(rota_pp or "").strip():
                    st.warning("Selecione a rota no pré-cadastro.")
                elif not str(praca_pp or "").strip():
                    st.warning("Informe a praça de pedágio.")
                else:
                    with conn() as c:
                        c.execute(
                            """INSERT INTO praca_pedagio (rota, praca_pedagio, rodovia, concessionaria, sentido_viagem, quantidade_eixos, valor_por_eixo, valor_todos_eixos)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                str(rota_pp).strip(),
                                str(praca_pp).strip(),
                                str(rodovia_pp or "").strip(),
                                str(concessionaria_pp or "").strip(),
                                str(sentido_viagem_pp or "").strip(),
                                float(quantidade_eixos_pp or 1.0),
                                float(valor_eixo_pp or 0.0),
                                float(valor_todos_eixos_pp or 0.0),
                            ),
                        )
                    alerta_gravado()
                    st.rerun()

    with conn() as c:
        df_praca_pedagio = pd.read_sql(
            """SELECT id, rota, praca_pedagio, rodovia, concessionaria, sentido_viagem, quantidade_eixos, valor_por_eixo, valor_todos_eixos
               FROM praca_pedagio
               ORDER BY rota ASC, praca_pedagio ASC, id DESC""",
            c,
        )
    opcoes_filtro_rota_pp = sorted(df_praca_pedagio["rota"].dropna().astype(str).str.strip().unique().tolist()) if not df_praca_pedagio.empty else []
    opcoes_filtro_sentido_pp = sorted(df_praca_pedagio["sentido_viagem"].dropna().astype(str).str.strip().unique().tolist()) if not df_praca_pedagio.empty else []
    rota_filtro_pendente_pp = st.session_state.pop("praca_pedagio_filtro_rota_pendente", None)
    if (
        rota_filtro_pendente_pp
        and opcoes_filtro_rota_pp
        and str(rota_filtro_pendente_pp).strip() in opcoes_filtro_rota_pp
    ):
        st.session_state["praca_pedagio_filtro_rota"] = str(rota_filtro_pendente_pp).strip()
    ff1, ff2 = st.columns(2)
    rota_filtro_pp = ff1.selectbox(
        "Filtrar por Rota",
        options=opcoes_filtro_rota_pp,
        index=None,
        placeholder="Selecione uma rota para filtrar",
        key="praca_pedagio_filtro_rota",
    )
    if not isinstance(st.session_state.get("praca_pedagio_filtro_sentido"), list):
        st.session_state["praca_pedagio_filtro_sentido"] = []
    sentido_filtro_pp = ff2.multiselect(
        "Filtrar por Sentido Viagem",
        options=opcoes_filtro_sentido_pp,
        placeholder="Selecione um ou mais sentidos",
        key="praca_pedagio_filtro_sentido",
    )
    df_praca_pedagio_exibir = df_praca_pedagio.copy()
    if rota_filtro_pp:
        df_praca_pedagio_exibir = df_praca_pedagio_exibir[
            df_praca_pedagio_exibir["rota"].astype(str).str.strip() == str(rota_filtro_pp).strip()
        ]
    if sentido_filtro_pp:
        sentidos_selecionados_pp = {str(s).strip() for s in sentido_filtro_pp}
        df_praca_pedagio_exibir = df_praca_pedagio_exibir[
            df_praca_pedagio_exibir["sentido_viagem"].astype(str).str.strip().isin(sentidos_selecionados_pp)
        ]

    valores_pp = pd.to_numeric(df_praca_pedagio_exibir.get("valor_todos_eixos", 0.0), errors="coerce").fillna(0.0)
    total_pedagio_rota = float(valores_pp.sum())
    label_total_pedagio = "Valor Total Pedágio por Rota" if rota_filtro_pp else "Valor Total Pedágio (Todas as Rotas)"

    df_sentido_metricas = df_praca_pedagio_exibir.copy()
    df_sentido_metricas["sentido_norm"] = (
        df_sentido_metricas["sentido_viagem"]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    df_sentido_metricas["valor_todos_eixos"] = pd.to_numeric(
        df_sentido_metricas["valor_todos_eixos"], errors="coerce"
    ).fillna(0.0)

    total_ida = float(
        df_sentido_metricas.loc[
            df_sentido_metricas["sentido_norm"] == "IDA", "valor_todos_eixos"
        ].sum()
    )
    total_volta = float(
        df_sentido_metricas.loc[
            df_sentido_metricas["sentido_norm"] == "VOLTA", "valor_todos_eixos"
        ].sum()
    )
    qtd_pedagios_ida = int((df_sentido_metricas["sentido_norm"] == "IDA").sum())
    qtd_pedagios_volta = int((df_sentido_metricas["sentido_norm"] == "VOLTA").sum())
    qtd_pedagios_checar = int((df_sentido_metricas["sentido_norm"] == "CHECAR").sum())
    total_checar = float(
        df_sentido_metricas.loc[
            df_sentido_metricas["sentido_norm"] == "CHECAR", "valor_todos_eixos"
        ].sum()
    )
    qtd_pedagios_total = int(len(df_sentido_metricas))

    exibir_checar_metricas = qtd_pedagios_checar > 0
    if exibir_checar_metricas:
        mpp1, mpp2, mpp3, mpp4, mpp5, mpp6, mpp7, mpp8 = st.columns(8)
    else:
        mpp1, mpp2, mpp3, mpp5, mpp6, mpp7 = st.columns(6)

    mpp1.metric(label_total_pedagio, brl(total_pedagio_rota))
    mpp2.metric("Total Ida", brl(total_ida))
    mpp3.metric("Total Volta", brl(total_volta))
    mpp5.metric("Qtd Pedágios", f"{qtd_pedagios_total}")
    mpp6.metric("Qtd Ida", f"{qtd_pedagios_ida}")
    mpp7.metric("Qtd Volta", f"{qtd_pedagios_volta}")
    if exibir_checar_metricas:
        mpp4.metric("Total Checar", brl(total_checar))
        mpp8.metric("Qtd Checar", f"{qtd_pedagios_checar}")

    st.dataframe(
        df_praca_pedagio_exibir,
        use_container_width=True,
        hide_index=True,
        column_config={
            "id": st.column_config.NumberColumn("ID", format="%d"),
            "rota": st.column_config.TextColumn("Rota"),
            "praca_pedagio": st.column_config.TextColumn("Praça de pedágio"),
            "rodovia": st.column_config.TextColumn("Rodovia"),
            "concessionaria": st.column_config.TextColumn("Concessionária"),
            "sentido_viagem": st.column_config.TextColumn("Sentido Viagem"),
            "quantidade_eixos": st.column_config.NumberColumn("Qtd. Eixos", format="%.0f"),
            "valor_por_eixo": st.column_config.NumberColumn("Valor por Eixo", format="R$ %.2f"),
            "valor_todos_eixos": st.column_config.NumberColumn("Valor Todos os Eixos", format="R$ %.2f"),
        },
    )

    if not df_praca_pedagio_exibir.empty:
        # Create report dataframe
        df_report = df_praca_pedagio_exibir[['rota', 'praca_pedagio', 'rodovia', 'concessionaria', 'sentido_viagem', 'quantidade_eixos', 'valor_por_eixo', 'valor_todos_eixos']].copy()
        df_report.columns = ['Rota', 'Praça Pedagio', 'Rodovia', 'Concessionaria', 'Sentido Viagem', 'Qtde Eixos', 'Valor Por Eixo', 'Valor Todos os Eixos']
        
        # Calculate totals
        total_ida = float(df_sentido_metricas.loc[df_sentido_metricas["sentido_norm"] == "IDA", "valor_todos_eixos"].sum())
        total_volta = float(df_sentido_metricas.loc[df_sentido_metricas["sentido_norm"] == "VOLTA", "valor_todos_eixos"].sum())
        qtd_total = int(len(df_sentido_metricas))
        
        # Add summary rows
        summary_data = {
            'Rota': '',
            'Praça Pedagio': '',
            'Rodovia': '',
            'Concessionaria': '',
            'Sentido Viagem': '',
            'Qtde Eixos': '',
            'Valor Por Eixo': '',
            'Valor Todos os Eixos': ''
        }
        df_summary_ida = pd.DataFrame([summary_data])
        df_summary_ida.loc[0, 'Rota'] = 'TOTAL PEDAGIO IDA'
        df_summary_ida.loc[0, 'Valor Todos os Eixos'] = total_ida
        
        df_summary_volta = pd.DataFrame([summary_data])
        df_summary_volta.loc[0, 'Rota'] = 'TOTAL PEDAGIO RETORNO'
        df_summary_volta.loc[0, 'Valor Todos os Eixos'] = total_volta
        
        df_summary_qtd = pd.DataFrame([summary_data])
        df_summary_qtd.loc[0, 'Rota'] = 'QTDE PEDAGIO NO PERIODO'
        df_summary_qtd.loc[0, 'Qtde Eixos'] = qtd_total
        
        df_report = pd.concat([df_report, df_summary_ida, df_summary_volta, df_summary_qtd], ignore_index=True)
        
        csv_data = df_report.to_csv(index=False, sep=';', decimal=',')
        
        st.download_button(
            label="📊 Relatórios Pedágio",
            data=csv_data,
            file_name="relatorio_pedagio.csv",
            mime="text/csv",
            key="btn_relatorio_pedagio"
        )

        # Visualização do Relatório na Tela
        with st.expander("📋 Visualizar Relatório Pedágio", expanded=False):
            if st.button("🔍 PREPARAR IMPRESSÃO", use_container_width=True, key="btn_print_pedagio"):
                # Geração do HTML para impressão
                html_impressao = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: sans-serif; margin: 30px; color: #333; }}
                        header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }}
                        table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 12px; }}
                        th, td {{ border: 1px solid #999; padding: 8px; text-align: left; }}
                        th {{ background-color: #f2f2f2; }}
                        .container-resumo {{ margin-top: 30px; display: flex; justify-content: flex-end; }}
                        .tot {{ width: 350px; border: 1px solid #ccc; padding: 15px; background: #fafafa; }}
                        .ln {{ display: flex; justify-content: space-between; margin-bottom: 8px; font-size: 14px; }}
                        .fin {{ font-weight: bold; font-size: 18px; border-top: 2px solid #2e7d32; color: #2e7d32; padding-top: 10px; margin-top: 10px; }}
                        .assinatura-box {{ margin-top: 80px; text-align: center; width: 400px; }}
                        .linha-assinatura {{ border-top: 1px solid #000; margin-bottom: 5px; }}
                        .btn-print {{ background: #007bff; color: white; padding: 15px; border: none; width: 100%; cursor: pointer; font-weight: bold; font-size: 16px; border-radius: 5px; }}
                        @media print {{ .btn-print {{ display: none; }} body {{ margin: 0; }} }}
                    </style>
                </head>
                <body>
                    <button class="btn-print" onclick="window.print()">🖨️ CLIQUE AQUI PARA IMPRIMIR ESTE RELATÓRIO</button>
                    
                    <header>
                        <h1 style="margin:0;">ART TRANSPORTES</h1>
                        <p style="margin:5px 0;">RELATÓRIO DE PEDÁGIOS</p>
                        <p>Período: <b>{filtro_ini.strftime('%d/%m/%Y')}</b> até <b>{filtro_fim.strftime('%d/%m/%Y')}</b></p>
                        <p style="margin:5px 0;">
                            Total Pedágio Ida: <b>{brl(total_ida)}</b>
                            | Total Pedágio Retorno: <b>{brl(total_volta)}</b>
                            | Quantidade: <b>{qtd_total}</b>
                        </p>
                    </header>

                    <table>
                        <thead>
                            <tr>
                                <th>Rota</th>
                                <th>Praça Pedágio</th>
                                <th>Rodovia</th>
                                <th>Concessionária</th>
                                <th>Sentido Viagem</th>
                                <th>Qtde Eixos</th>
                                <th>Valor por Eixo</th>
                                <th>Valor Todos os Eixos</th>
                            </tr>
                        </thead>
                        <tbody>
                """
                
                for _, r in df_praca_pedagio_exibir.iterrows():
                    html_impressao += f"""
                    <tr>
                        <td>{r['rota'] or '-'}</td>
                        <td>{r['praca_pedagio'] or '-'}</td>
                        <td>{r['rodovia'] or '-'}</td>
                        <td>{r['concessionaria'] or '-'}</td>
                        <td>{r['sentido_viagem'] or '-'}</td>
                        <td>{r['quantidade_eixos'] or 0}</td>
                        <td>{brl(r['valor_por_eixo'] or 0)}</td>
                        <td>{brl(r['valor_todos_eixos'] or 0)}</td>
                    </tr>
                    """
                
                html_impressao += f"""
                        </tbody>
                    </table>

                    <div class="container-resumo">
                        <div class="tot">
                            <div class="ln"><span>Quantidade de Pedágios:</span> <span>{qtd_total}</span></div>
                            <div class="ln"><span>Total Pedágio Ida:</span> <span>{brl(total_ida)}</span></div>
                            <div class="ln"><span>Total Pedágio Retorno:</span> <span>{brl(total_volta)}</span></div>
                            <div class="ln fin"><span>TOTAL GERAL:</span> <span>{brl(total_ida + total_volta)}</span></div>
                        </div>
                    </div>

                    <div class="assinatura-box">
                        <div class="linha-assinatura"></div>
                        <p><b>Assinatura do Responsável</b></p>
                        <p style="font-size: 10px; color: #666;">Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
                    </div>

                    <script>
                        // Pequeno atraso para garantir que o layout carregue antes de abrir o print
                        setTimeout(function(){{ window.print(); }}, 700);
                    </script>
                </body>
                </html>
                """
                components.html(html_impressao, height=1000, scrolling=True)
            else:
                # Métricas do relatório
                rm1, rm2, rm3, rm4 = st.columns(4)
                rm1.metric("Quantidade de Pedágios", qtd_total)
                rm2.metric("Total Pedágio Ida", brl(total_ida))
                rm3.metric("Total Pedágio Retorno", brl(total_volta))
                rm4.metric("Total Geral", brl(total_ida + total_volta))

    if "praca_pedagio_editando_id" not in st.session_state:
        st.session_state.praca_pedagio_editando_id = None
    if "praca_pedagio_excluir_id" not in st.session_state:
        st.session_state.praca_pedagio_excluir_id = None

    if not df_praca_pedagio_exibir.empty:
        st.markdown("##### Replicar Registros por Rota")
        mapa_registros_replica_pp = {
            (
                f"ID: {int(r['id'])} | Rota: {r['rota']} | Praça: {r['praca_pedagio']} | "
                f"Sentido: {r['sentido_viagem'] if pd.notna(r['sentido_viagem']) and str(r['sentido_viagem']).strip() else '-'}"
            ): int(r["id"])
            for _, r in df_praca_pedagio_exibir.sort_values(by="id", ascending=False).iterrows()
        }
        rp1, rp2 = st.columns(2)
        registros_replica_sel = rp1.multiselect(
            "Marque os registros para replicar",
            options=list(mapa_registros_replica_pp.keys()),
            key="praca_pedagio_replica_registros",
        )
        rota_destino_replica_pp = rp2.selectbox(
            "Rota de destino da réplica",
            options=opcoes_rotas_pp,
            index=None,
            placeholder="Selecione a rota de destino",
            key="praca_pedagio_replica_rota_destino",
        )
        if st.button("🔁 Replicar Marcados", key="btn_praca_pedagio_replicar_marcados", use_container_width=True):
            if not registros_replica_sel:
                st.warning("Marque pelo menos um registro para replicar.")
            elif not str(rota_destino_replica_pp or "").strip():
                st.warning("Selecione a rota de destino para replicar.")
            else:
                ids_replica = [mapa_registros_replica_pp[x] for x in registros_replica_sel]
                df_replica_base = df_praca_pedagio[df_praca_pedagio["id"].isin(ids_replica)].copy()
                novos_ids_pp = []
                with conn() as c:
                    for _, rr in df_replica_base.iterrows():
                        c.execute(
                            """INSERT INTO praca_pedagio (rota, praca_pedagio, rodovia, concessionaria, sentido_viagem, quantidade_eixos, valor_por_eixo, valor_todos_eixos)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                str(rota_destino_replica_pp).strip(),
                                str(rr["praca_pedagio"] or "").strip(),
                                str(rr["rodovia"] or "").strip(),
                                str(rr["concessionaria"] or "").strip(),
                                str(rr["sentido_viagem"] or "").strip(),
                                float(pd.to_numeric(rr["quantidade_eixos"], errors="coerce") or 1.0),
                                float(pd.to_numeric(rr["valor_por_eixo"], errors="coerce") or 0.0),
                                float(pd.to_numeric(rr["valor_todos_eixos"], errors="coerce") or 0.0),
                            ),
                        )
                        novos_ids_pp.append(int(c.execute("SELECT last_insert_rowid()").fetchone()[0]))
                if novos_ids_pp:
                    st.session_state.praca_pedagio_editando_id = int(novos_ids_pp[0])
                    st.session_state.praca_pedagio_excluir_id = None
                    st.session_state.praca_pedagio_filtro_rota_pendente = str(rota_destino_replica_pp).strip()
                    st.success(f"{len(novos_ids_pp)} registro(s) replicado(s). Abrindo edição para ajuste.")
                    st.rerun()

        mapa_praca_pedagio = {
            (
                f"ID: {int(r['id'])} | Rota: {r['rota']} | Praça: {r['praca_pedagio']} | "
                f"Rodovia: {r['rodovia'] if pd.notna(r['rodovia']) and str(r['rodovia']).strip() else '-'}"
            ): int(r["id"])
            for _, r in df_praca_pedagio_exibir.sort_values(by="id", ascending=False).iterrows()
        }
        praca_pedagio_sel_label = st.selectbox(
            "Selecione para editar ou deletar",
            options=list(mapa_praca_pedagio.keys()),
            index=None,
            placeholder="Escolha um registro",
            key="praca_pedagio_sel_alteracao",
        )

        if praca_pedagio_sel_label:
            id_sel = mapa_praca_pedagio[praca_pedagio_sel_label]
            ac1, ac2 = st.columns(2)
            if ac1.button("✏️ Editar", key="btn_praca_pedagio_editar", use_container_width=True):
                st.session_state.praca_pedagio_editando_id = id_sel
                st.session_state.praca_pedagio_excluir_id = None
            if ac2.button("🗑️ Deletar", key="btn_praca_pedagio_deletar", type="primary", use_container_width=True):
                st.session_state.praca_pedagio_excluir_id = id_sel

        if st.session_state.praca_pedagio_excluir_id is not None:
            st.warning(f"Confirma a exclusão do registro ID {int(st.session_state.praca_pedagio_excluir_id)}?")
            ex1, ex2 = st.columns(2)
            if ex1.button("✅ Confirmar exclusão", key="btn_praca_pedagio_confirmar_exclusao", type="primary", use_container_width=True):
                with conn() as c:
                    c.execute("DELETE FROM praca_pedagio WHERE id=?", (int(st.session_state.praca_pedagio_excluir_id),))
                st.session_state.praca_pedagio_excluir_id = None
                st.session_state.praca_pedagio_editando_id = None
                alerta_gravado("✅ Registro excluído com sucesso!")
                st.rerun()
            if ex2.button("❌ Cancelar exclusão", key="btn_praca_pedagio_cancelar_exclusao", use_container_width=True):
                st.session_state.praca_pedagio_excluir_id = None
                st.rerun()

        if st.session_state.praca_pedagio_editando_id is not None:
            id_edit = int(st.session_state.praca_pedagio_editando_id)
            row_edit = df_praca_pedagio[df_praca_pedagio["id"] == id_edit]
            if row_edit.empty:
                st.session_state.praca_pedagio_editando_id = None
            else:
                reg = row_edit.iloc[0]
                opcoes_rotas_edit = list(opcoes_rotas_pp)
                rota_atual = str(reg["rota"] or "").strip()
                if rota_atual and rota_atual not in opcoes_rotas_edit:
                    opcoes_rotas_edit.append(rota_atual)
                idx_rota_edit = opcoes_rotas_edit.index(rota_atual) if rota_atual in opcoes_rotas_edit else None
                quantidade_eixos_atual = pd.to_numeric(reg["quantidade_eixos"], errors="coerce")
                quantidade_eixos_atual = float(quantidade_eixos_atual) if pd.notna(quantidade_eixos_atual) and float(quantidade_eixos_atual) > 0 else 1.0
                valor_pp_atual = pd.to_numeric(reg["valor_por_eixo"], errors="coerce")
                valor_pp_atual = float(valor_pp_atual) if pd.notna(valor_pp_atual) else 0.0

                with st.form("form_praca_pedagio_editar"):
                    ep1, ep2 = st.columns(2)
                    rota_pp_edit = ep1.selectbox(
                        "Rota (Editar)",
                        options=opcoes_rotas_edit,
                        index=idx_rota_edit,
                        placeholder="Selecione a rota",
                        key=f"praca_pedagio_rota_edit_{id_edit}",
                    )
                    praca_pp_edit = ep2.text_input(
                        "Praça de pedágio (Editar)",
                        value=str(reg["praca_pedagio"] or ""),
                        key=f"praca_pedagio_nome_edit_{id_edit}",
                    )
                    ep3, ep4 = st.columns(2)
                    rodovia_pp_edit = ep3.text_input(
                        "Rodovia (Editar)",
                        value=str(reg["rodovia"] or ""),
                        key=f"praca_pedagio_rodovia_edit_{id_edit}",
                    )
                    concessionaria_pp_edit = ep4.text_input(
                        "Concessionária (Editar)",
                        value=str(reg["concessionaria"] or ""),
                        key=f"praca_pedagio_concessionaria_edit_{id_edit}",
                    )
                    sentido_viagem_pp_edit = st.text_input(
                        "Sentido Viagem (Editar)",
                        value=str(reg["sentido_viagem"] or ""),
                        key=f"praca_pedagio_sentido_edit_{id_edit}",
                    )
                    ep5, ep6, ep7 = st.columns(3)
                    quantidade_eixos_edit = ep5.number_input(
                        "Quantidade Eixos (Editar)",
                        min_value=1.0,
                        step=1.0,
                        format="%.0f",
                        value=quantidade_eixos_atual,
                        key=f"praca_pedagio_qtd_eixos_edit_{id_edit}",
                    )
                    valor_pp_edit = ep6.number_input(
                        "Valor por Eixo (Editar)",
                        min_value=0.0,
                        step=0.000001,
                        format="%.6f",
                        value=valor_pp_atual,
                        key=f"praca_pedagio_valor_edit_{id_edit}",
                    )
                    valor_todos_eixos_edit = float(quantidade_eixos_edit) * float(valor_pp_edit)
                    ep7.number_input(
                        "Valor Todos os Eixos (Editar)",
                        min_value=0.0,
                        step=0.01,
                        format="%.2f",
                        value=float(valor_todos_eixos_edit),
                        disabled=True,
                        key=f"praca_pedagio_valor_total_edit_{id_edit}",
                    )
                    eb1, eb2 = st.columns(2)
                    gravar_edit_pp = eb1.form_submit_button("💾 Gravar", use_container_width=True, key=f"btn_praca_pedagio_gravar_edit_{id_edit}")
                    cancelar_edit_pp = eb2.form_submit_button("❌ Cancelar", use_container_width=True, key=f"btn_praca_pedagio_cancelar_edit_{id_edit}")

                    if cancelar_edit_pp:
                        st.session_state.praca_pedagio_editando_id = None
                        st.rerun()

                    if gravar_edit_pp:
                        if not str(rota_pp_edit or "").strip():
                            st.warning("Selecione a rota no pré-cadastro.")
                        elif not str(praca_pp_edit or "").strip():
                            st.warning("Informe a praça de pedágio.")
                        else:
                            with conn() as c:
                                c.execute(
                                    """UPDATE praca_pedagio
                                       SET rota=?, praca_pedagio=?, rodovia=?, concessionaria=?, sentido_viagem=?, quantidade_eixos=?, valor_por_eixo=?, valor_todos_eixos=?
                                       WHERE id=?""",
                                    (
                                        str(rota_pp_edit).strip(),
                                        str(praca_pp_edit).strip(),
                                        str(rodovia_pp_edit or "").strip(),
                                        str(concessionaria_pp_edit or "").strip(),
                                        str(sentido_viagem_pp_edit or "").strip(),
                                        float(quantidade_eixos_edit or 1.0),
                                        float(valor_pp_edit or 0.0),
                                        float(valor_todos_eixos_edit or 0.0),
                                        id_edit,
                                    ),
                                )
                            st.session_state.praca_pedagio_editando_id = None
                            alerta_gravado()
                            st.rerun()

# =========================
# ABA 17 - CONTAS A PAGAR
# =========================

CATEGORIAS_CP = [
    "Combustível", "Manutenção", "Pneus", "Pedágio", "Seguro",
    "IPVA / Licenciamento", "Financiamento / Parcela", "Salários",
    "Impostos / Taxas", "Aluguel", "Água / Energia", "Telefone / Internet",
    "Material de Escritório", "Outros",
]
FORMAS_PAGAMENTO_CP = ["PIX", "TED / DOC", "Dinheiro", "Cheque", "Boleto", "Débito Automático", "Cartão", "Outros"]

CATEGORIAS_CR = ["Frete", "Serviço", "Bonificação", "Aluguel", "Outros"]
FORMAS_RECEBIMENTO_CR = ["PIX", "TED / DOC", "Dinheiro", "Cheque", "Depósito", "Cartão", "Outros"]

def _add_status_cp(df):
    hoje = date.today()
    df = df.copy()
    df["status"] = "PENDENTE"
    if df.empty:
        return df
    try:
        mask_pago = df["data_pagamento"].apply(lambda d: d is not None and str(d) not in ("None", "NaT", ""))
        df.loc[mask_pago, "status"] = "PAGO"
        mask_venc = ~mask_pago & df["data_vencimento"].apply(
            lambda d: d is not None and str(d) not in ("None", "NaT", "") and d < hoje
        )
        df.loc[mask_venc, "status"] = "VENCIDO"
    except Exception:
        pass
    return df

def _add_status_cr(df):
    hoje = date.today()
    df = df.copy()
    df["status"] = "PENDENTE"
    if df.empty:
        return df
    try:
        mask_rec = df["data_recebimento"].apply(lambda d: d is not None and str(d) not in ("None", "NaT", ""))
        df.loc[mask_rec, "status"] = "RECEBIDO"
        mask_venc = ~mask_rec & df["data_vencimento"].apply(
            lambda d: d is not None and str(d) not in ("None", "NaT", "") and d < hoje
        )
        df.loc[mask_venc, "status"] = "VENCIDO"
    except Exception:
        pass
    return df

def _garantir_colunas(df, defaults):
    df = df.copy()
    for coluna, valor_padrao in defaults.items():
        if coluna not in df.columns:
            df[coluna] = valor_padrao
    return df

def _fmt_data_relatorio_financeiro(valor):
    if valor is None or str(valor) in ("None", "NaT", ""):
        return "-"
    try:
        valor_dt = pd.to_datetime(valor, errors="coerce")
        if pd.isna(valor_dt):
            return "-"
        return valor_dt.strftime("%d/%m/%Y")
    except Exception:
        return "-"

def _html_relatorio_financeiro(
    titulo,
    periodo_ini,
    periodo_fim,
    status_filtro,
    categoria_filtro,
    placa_filtro,
    df_rel,
    colunas,
    resumo_linhas,
    total_label,
    total_valor,
):
    placa_txt = rotulo_placa_com_descricao(placa_filtro) if placa_filtro else "Todas"
    linhas_tabela = []
    for _, r in df_rel.iterrows():
        tds = []
        for campo, _rotulo, tipo in colunas:
            valor = r.get(campo)
            if tipo == "data":
                valor_txt = _fmt_data_relatorio_financeiro(valor)
            elif tipo == "moeda":
                valor_txt = brl(float(valor or 0))
            else:
                valor_txt = str(valor or "-")
            tds.append(f"<td>{escape(valor_txt)}</td>")
        linhas_tabela.append("<tr>" + "".join(tds) + "</tr>")

    if not linhas_tabela:
        linhas_tabela.append(
            f"<tr><td colspan=\"{len(colunas)}\" style=\"text-align:center;\">Nenhum lançamento encontrado para os filtros selecionados.</td></tr>"
        )

    linhas_resumo = "\n".join(
        f"<div class=\"ln\"><span>{escape(label)}:</span> <span>{escape(valor)}</span></div>"
        for label, valor in resumo_linhas
    )
    cabecalho_tabela = "\n".join(f"<th>{escape(rotulo)}</th>" for _campo, rotulo, _tipo in colunas)

    return f"""
    <html>
    <head>
        <style>
            body {{ font-family: sans-serif; margin: 30px; color: #333; }}
            header {{ text-align: center; border-bottom: 2px solid #333; padding-bottom: 10px; }}
            table {{ width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 11px; }}
            th, td {{ border: 1px solid #999; padding: 7px; text-align: left; }}
            th {{ background-color: #f2f2f2; }}
            .container-resumo {{ margin-top: 30px; display: flex; justify-content: flex-end; }}
            .tot {{ width: 360px; border: 1px solid #ccc; padding: 15px; background: #fafafa; }}
            .ln {{ display: flex; justify-content: space-between; gap: 20px; margin-bottom: 8px; font-size: 14px; }}
            .fin {{ font-weight: bold; font-size: 18px; border-top: 2px solid #2e7d32; color: #2e7d32; padding-top: 10px; margin-top: 10px; }}
            .assinatura-box {{ margin-top: 80px; text-align: center; width: 400px; }}
            .linha-assinatura {{ border-top: 1px solid #000; margin-bottom: 5px; }}
            .btn-print {{ background: #007bff; color: white; padding: 15px; border: none; width: 100%; cursor: pointer; font-weight: bold; font-size: 16px; border-radius: 5px; }}
            @media print {{ .btn-print {{ display: none; }} body {{ margin: 0; }} }}
        </style>
    </head>
    <body>
        <button class="btn-print" onclick="window.print()">🖨️ CLIQUE AQUI PARA IMPRIMIR ESTE RELATÓRIO</button>

        <header>
            <h1 style="margin:0;">ART TRANSPORTES</h1>
            <p style="margin:5px 0;">{escape(titulo.upper())}</p>
            <p>Período de vencimento: <b>{periodo_ini.strftime('%d/%m/%Y')}</b> até <b>{periodo_fim.strftime('%d/%m/%Y')}</b></p>
            <p style="margin:5px 0;">
                Status: <b>{escape(status_filtro)}</b>
                | Categoria: <b>{escape(categoria_filtro)}</b>
                | Placa: <b>{escape(placa_txt)}</b>
            </p>
        </header>

        <table>
            <thead>
                <tr>
                    {cabecalho_tabela}
                </tr>
            </thead>
            <tbody>
                {''.join(linhas_tabela)}
            </tbody>
        </table>

        <div class="container-resumo">
            <div class="tot">
                {linhas_resumo}
                <div class="ln fin"><span>{escape(total_label)}:</span> <span>{escape(brl(total_valor))}</span></div>
            </div>
        </div>

        <div class="assinatura-box">
            <div class="linha-assinatura"></div>
            <p><b>Assinatura do Responsável</b></p>
            <p style="font-size: 10px; color: #666;">Gerado em: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
        </div>

        <script>
            setTimeout(function(){{ window.print(); }}, 700);
        </script>
    </body>
    </html>
    """

with aba17:
    if st.session_state.get("cp_flash_msg"):
        st.success(st.session_state.pop("cp_flash_msg"))

    with conn() as c:
        df_cp = pd.read_sql(
            "SELECT * FROM contas_pagar ORDER BY data_vencimento ASC, id DESC", c
        )

    df_cp = _garantir_colunas(df_cp, {
        "data_emissao": None,
        "data_vencimento": None,
        "data_pagamento": None,
        "valor": 0.0,
        "categoria": None,
        "veiculo_placa": None,
    })
    df_cp["data_vencimento"] = pd.to_datetime(df_cp["data_vencimento"], errors="coerce").dt.date
    df_cp["data_emissao"] = pd.to_datetime(df_cp["data_emissao"], errors="coerce").dt.date
    df_cp["data_pagamento"] = pd.to_datetime(df_cp["data_pagamento"], errors="coerce").dt.date
    df_cp["valor"] = pd.to_numeric(df_cp["valor"], errors="coerce").fillna(0.0)
    df_cp = _add_status_cp(df_cp)
    if placa_filtro_calculo and "veiculo_placa" in df_cp.columns:
        placa_ref_cp = str(placa_filtro_calculo).strip().upper()
        df_cp = df_cp[
            df_cp["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_cp
        ]

    if not df_cp.empty:
        hoje_cp = date.today()
        cp_resumo_ini = st.session_state.get("cp_fil_ini", data_ini_carregar)
        cp_resumo_fim = st.session_state.get("cp_fil_fim", data_fim_carregar)
        df_cp_resumo = df_cp[
            df_cp["data_vencimento"].apply(
                lambda d: cp_resumo_ini <= d <= cp_resumo_fim if pd.notna(d) else True
            )
        ]
        df_pend_cp = df_cp_resumo[df_cp_resumo["status"].isin(["PENDENTE", "VENCIDO"])]
        df_venc_cp = df_cp_resumo[df_cp_resumo["status"] == "VENCIDO"]
        df_pago_cp = df_cp_resumo[df_cp_resumo["status"] == "PAGO"]
        df_7d_cp = df_cp_resumo[
            (df_cp_resumo["status"] == "PENDENTE")
            & df_cp_resumo["data_vencimento"].notna()
            & df_cp_resumo["data_vencimento"].apply(lambda d: 0 <= (d - hoje_cp).days <= 7 if pd.notna(d) else False)
        ]

        st.markdown("### 📊 Resumo — Contas a Pagar")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("💰 Total a Pagar", brl(df_pend_cp["valor"].sum()), help="Pendentes + Vencidas")
        k2.metric("🔴 Vencidas", brl(df_venc_cp["valor"].sum()), delta=f"{len(df_venc_cp)} lançamento(s)", delta_color="inverse")
        k3.metric("✅ Total Pago", brl(df_pago_cp["valor"].sum()))
        k4.metric("⏰ Vence em 7 dias", brl(df_7d_cp["valor"].sum()), delta=f"{len(df_7d_cp)} lançamento(s)", delta_color="off")

        if not df_venc_cp.empty:
            st.error(f"⚠️ Você tem **{len(df_venc_cp)} conta(s) vencida(s)** totalizando **{brl(df_venc_cp['valor'].sum())}**. Regularize o quanto antes!")

    st.markdown("---")
    with st.expander("➕ Nova Conta a Pagar", expanded=df_cp.empty if not df_cp.empty else True):
        with st.form("form_nova_cp", clear_on_submit=True):
            st.markdown("**Dados da Conta**")
            fa1, fa2 = st.columns(2)
            cp_descricao = fa1.text_input("Descrição *", placeholder="Ex: Pagamento seguro caminhão").strip()
            cp_fornecedor = fa2.text_input("Fornecedor / Beneficiário", placeholder="Ex: Seguradora XYZ").strip()

            fb1, fb2, fb3 = st.columns(3)
            cp_categoria = fb1.selectbox("Categoria", CATEGORIAS_CP)
            cp_n_doc = fb2.text_input("N. Documento / Boleto / NF", placeholder="Ex: 00123").strip()
            cp_forma = fb3.selectbox("Forma de Pagamento", FORMAS_PAGAMENTO_CP)

            cp_veiculo = st.selectbox(
                "Placa do Veículo",
                lista_veiculos_full,
                index=None,
                placeholder="Selecione a placa do veículo",
                key="cp_veiculo_form",
            )
            cp_veiculo_placa = placa_de_opcao_veiculo(cp_veiculo)

            fc1, fc2, fc3 = st.columns(3)
            cp_dt_emissao = fc1.date_input("Data Emissão", value=date.today(), format="DD/MM/YYYY", key="cp_dt_emissao_form")
            cp_dt_venc = fc2.date_input("Data Vencimento *", value=date.today() + timedelta(days=30), format="DD/MM/YYYY", key="cp_dt_venc_form")
            cp_valor = fc3.number_input("Valor (R$) *", min_value=0.01, step=0.01, format="%.2f", key="cp_valor_form")

            cp_obs = st.text_area("Observação", height=60, key="cp_obs_form")

            cp_ja_pago = st.checkbox("Já foi pago?", key="cp_ja_pago_form")
            cp_dt_pagamento_form = None
            if cp_ja_pago:
                cp_dt_pagamento_form = st.date_input("Data do Pagamento", value=date.today(), format="DD/MM/YYYY", key="cp_dt_pagamento_check")

            sub_cp = st.form_submit_button("💾 Salvar Conta a Pagar", type="primary", use_container_width=True)
            if sub_cp:
                if not cp_descricao:
                    st.warning("⚠️ Preencha a Descrição.")
                elif cp_valor <= 0:
                    st.warning("⚠️ Informe um valor maior que zero.")
                else:
                    with conn() as c:
                        c.execute(
                            """INSERT INTO contas_pagar
                               (descricao, fornecedor, categoria, n_documento, data_emissao,
                                data_vencimento, valor, data_pagamento, forma_pagamento, observacao, data_cadastro, veiculo_placa)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                cp_descricao,
                                cp_fornecedor or None,
                                cp_categoria,
                                cp_n_doc or None,
                                cp_dt_emissao.isoformat(),
                                cp_dt_venc.isoformat(),
                                float(cp_valor),
                                cp_dt_pagamento_form.isoformat() if cp_dt_pagamento_form else None,
                                cp_forma,
                                cp_obs.strip() or None,
                                datetime.now().isoformat(),
                                cp_veiculo_placa,
                            ),
                        )
                    limpar_cache_app()
                    st.session_state["cp_flash_msg"] = "✅ Conta a pagar salva com sucesso!"
                    st.rerun()

    if not df_cp.empty:
        st.markdown("---")
        st.markdown("### 📋 Lançamentos")
        if placa_filtro_calculo:
            st.caption(f"Filtro por placa ativo: `{rotulo_placa_com_descricao(placa_filtro_calculo)}`")

        fil1, fil2, fil3, fil4 = st.columns([2, 2, 2, 2])
        cp_filtro_status = fil1.radio(
            "Status", ["Todos", "🟡 Pendentes", "🔴 Vencidas", "🟢 Pagas"],
            horizontal=False, key="cp_fil_status"
        )
        cp_fil_ini = fil2.date_input("Vencimento de:", value=data_ini_carregar, format="DD/MM/YYYY", key="cp_fil_ini")
        cp_fil_fim = fil3.date_input("Vencimento até:", value=data_fim_carregar, format="DD/MM/YYYY", key="cp_fil_fim")
        cp_cats_disp = ["Todas"] + sorted(df_cp["categoria"].dropna().unique().tolist())
        cp_filtro_cat = fil4.selectbox("Categoria", cp_cats_disp, key="cp_fil_cat")

        df_cp_f = df_cp.copy()
        if "Pendentes" in cp_filtro_status:
            df_cp_f = df_cp_f[df_cp_f["status"] == "PENDENTE"]
        elif "Vencidas" in cp_filtro_status:
            df_cp_f = df_cp_f[df_cp_f["status"] == "VENCIDO"]
        elif "Pagas" in cp_filtro_status:
            df_cp_f = df_cp_f[df_cp_f["status"] == "PAGO"]
        if cp_filtro_cat != "Todas":
            df_cp_f = df_cp_f[df_cp_f["categoria"] == cp_filtro_cat]
        mask_cp_venc = df_cp_f["data_vencimento"].apply(
            lambda d: cp_fil_ini <= d <= cp_fil_fim if pd.notna(d) else True
        ).reindex(df_cp_f.index, fill_value=False).astype(bool)
        df_cp_f = df_cp_f.loc[mask_cp_venc]
        df_cp_f = df_cp_f.sort_values("data_emissao", na_position="last")

        _CP_STATUS_ICON = {"PENDENTE": "🟡 Pendente", "VENCIDO": "🔴 Vencida", "PAGO": "🟢 Paga"}
        _cp_n_filtrado = len(df_cp_f)
        _cp_total_filtrado = pd.to_numeric(df_cp_f["valor"], errors="coerce").sum() if "valor" in df_cp_f.columns else 0.0
        _cp_show_data = df_cp_f.copy(deep=True)
        _st_cp = _cp_show_data["status"] if "status" in _cp_show_data.columns else pd.Series(["PENDENTE"] * len(_cp_show_data), index=_cp_show_data.index)
        _cp_show_data["Status"] = _st_cp.map(_CP_STATUS_ICON).fillna("🟡 Pendente")
        _cp_show_data = _cp_show_data.rename(columns={
            "id": "ID", "descricao": "Descrição", "fornecedor": "Fornecedor",
            "categoria": "Categoria", "veiculo_placa": "Placa", "n_documento": "Documento",
            "data_emissao": "Emissão", "data_vencimento": "Vencimento", "valor": "Valor (R$)",
            "data_pagamento": "Data Pgto", "forma_pagamento": "Forma Pgto",
        })
        colunas_cp_grid = ["ID", "Emissão", "Status", "Descrição", "Fornecedor", "Categoria", "Placa", "Documento", "Vencimento", "Valor (R$)", "Data Pgto", "Forma Pgto"]
        st.dataframe(
            _cp_show_data[[c for c in colunas_cp_grid if c in _cp_show_data.columns]],
            use_container_width=True, hide_index=True,
            column_config={
                "ID": st.column_config.NumberColumn("ID", format="%d", width="small"),
                "Emissão": st.column_config.DateColumn("Emissão", format="DD/MM/YYYY"),
                "Status": st.column_config.TextColumn("Status"),
                "Placa": st.column_config.TextColumn("Placa"),
                "Vencimento": st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"),
                "Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
                "Data Pgto": st.column_config.DateColumn("Data Pgto", format="DD/MM/YYYY"),
            },
        )
        st.caption(f"Exibindo **{_cp_n_filtrado}** de **{len(df_cp)}** lançamentos | Total filtrado: **{brl(_cp_total_filtrado)}**")

        if st.button("🖨️ Imprimir", use_container_width=True, key="btn_print_contas_pagar"):
            df_cp_print = df_cp_f.copy()
            total_cp_pendente = pd.to_numeric(df_cp_print.loc[df_cp_print["status"] == "PENDENTE", "valor"], errors="coerce").fillna(0).sum()
            total_cp_vencido = pd.to_numeric(df_cp_print.loc[df_cp_print["status"] == "VENCIDO", "valor"], errors="coerce").fillna(0).sum()
            total_cp_pago = pd.to_numeric(df_cp_print.loc[df_cp_print["status"] == "PAGO", "valor"], errors="coerce").fillna(0).sum()
            df_cp_print["status_print"] = df_cp_print["status"].map(_CP_STATUS_ICON).fillna("🟡 Pendente")
            html_cp_print = _html_relatorio_financeiro(
                "Relatório de Contas a Pagar",
                cp_fil_ini,
                cp_fil_fim,
                cp_filtro_status,
                cp_filtro_cat,
                placa_filtro_calculo,
                df_cp_print,
                [
                    ("id", "ID", "texto"),
                    ("data_emissao", "Emissão", "data"),
                    ("status_print", "Status", "texto"),
                    ("descricao", "Descrição", "texto"),
                    ("fornecedor", "Fornecedor", "texto"),
                    ("categoria", "Categoria", "texto"),
                    ("veiculo_placa", "Placa", "texto"),
                    ("n_documento", "Documento", "texto"),
                    ("data_vencimento", "Vencimento", "data"),
                    ("valor", "Valor", "moeda"),
                    ("data_pagamento", "Data Pgto", "data"),
                    ("forma_pagamento", "Forma Pgto", "texto"),
                ],
                [
                    ("Quantidade de Lançamentos", str(len(df_cp_print))),
                    ("Total Pendente", brl(total_cp_pendente)),
                    ("Total Vencido", brl(total_cp_vencido)),
                    ("Total Pago", brl(total_cp_pago)),
                ],
                "TOTAL FILTRADO",
                _cp_total_filtrado,
            )
            components.html(html_cp_print, height=1000, scrolling=True)

        st.markdown("#### ⚡ Gerenciar Lançamento")
        if "cp_editando_id" not in st.session_state:
            st.session_state.cp_editando_id = None
        if "cp_duplicando_id" not in st.session_state:
            st.session_state.cp_duplicando_id = None

        opcoes_cp_sel = {
            f"ID {int(r['id'])} | {_CP_STATUS_ICON.get(r['status'], r['status'])} | {str(r.get('descricao') or '')} | Venc. {r['data_vencimento'].strftime('%d/%m/%Y') if pd.notna(r['data_vencimento']) else '-'} | {brl(float(r.get('valor') or 0))}": int(r["id"])
            for _, r in df_cp_f.iterrows()
        }
        cp_sel_label = st.selectbox(
            "Selecione um lançamento:",
            options=[None] + list(opcoes_cp_sel.keys()),
            format_func=lambda x: "— Selecione para gerenciar —" if x is None else x,
            key="cp_sel_acao",
        )

        if cp_sel_label:
            cp_id_sel = opcoes_cp_sel[cp_sel_label]
            with conn() as c:
                cp_row_raw = c.execute("SELECT * FROM contas_pagar WHERE id=?", (cp_id_sel,)).fetchone()
            cp_row = dict(cp_row_raw)
            _cp_tmp = pd.DataFrame([cp_row])
            _cp_tmp["data_vencimento"] = pd.to_datetime(_cp_tmp["data_vencimento"], errors="coerce").dt.date
            _cp_tmp["data_pagamento"] = pd.to_datetime(_cp_tmp["data_pagamento"], errors="coerce").dt.date
            cp_status_atual = _add_status_cp(_cp_tmp)["status"].iloc[0]

            bt1, bt2, bt3, bt4 = st.columns(4)
            if cp_status_atual != "PAGO":
                if bt1.button("✅ Dar Baixa (Marcar como Pago)", key=f"cp_baixa_{cp_id_sel}", use_container_width=True, type="primary"):
                    st.session_state[f"cp_show_baixa_{cp_id_sel}"] = True
                    st.session_state.cp_editando_id = None
                    st.session_state.cp_duplicando_id = None
            else:
                if bt1.button("↩️ Estornar Pagamento", key=f"cp_estornar_{cp_id_sel}", use_container_width=True):
                    with conn() as c:
                        c.execute("UPDATE contas_pagar SET data_pagamento=NULL WHERE id=?", (cp_id_sel,))
                    limpar_cache_app()
                    st.session_state["cp_flash_msg"] = "↩️ Pagamento estornado."
                    st.rerun()

            if bt2.button("✏️ Editar", key=f"cp_editar_{cp_id_sel}", use_container_width=True):
                st.session_state.cp_editando_id = cp_id_sel
                st.session_state.cp_duplicando_id = None
                st.session_state.pop(f"cp_show_baixa_{cp_id_sel}", None)

            if bt3.button("📄 Duplicar", key=f"cp_duplicar_{cp_id_sel}", use_container_width=True):
                st.session_state.cp_duplicando_id = cp_id_sel
                st.session_state.cp_editando_id = None
                st.session_state.pop(f"cp_show_baixa_{cp_id_sel}", None)

            if bt4.button("🗑️ Excluir", key=f"cp_excluir_{cp_id_sel}", use_container_width=True):
                st.session_state[f"cp_confirmar_del_{cp_id_sel}"] = True

            if st.session_state.get(f"cp_show_baixa_{cp_id_sel}"):
                st.markdown("**💳 Confirmar Pagamento**")
                b1, b2 = st.columns(2)
                cp_dt_baixa = b1.date_input("Data do Pagamento", value=date.today(), format="DD/MM/YYYY", key=f"cp_dt_baixa_{cp_id_sel}")
                cp_forma_baixa = b2.selectbox("Forma de Pagamento", FORMAS_PAGAMENTO_CP, key=f"cp_forma_baixa_{cp_id_sel}")
                bc1, bc2 = st.columns(2)
                if bc1.button("✅ Confirmar Pagamento", key=f"cp_conf_baixa_{cp_id_sel}", type="primary", use_container_width=True):
                    with conn() as c:
                        c.execute(
                            "UPDATE contas_pagar SET data_pagamento=?, forma_pagamento=? WHERE id=?",
                            (cp_dt_baixa.isoformat(), cp_forma_baixa, cp_id_sel),
                        )
                    st.session_state.pop(f"cp_show_baixa_{cp_id_sel}", None)
                    limpar_cache_app()
                    st.session_state["cp_flash_msg"] = f"✅ Pagamento registrado em {cp_dt_baixa.strftime('%d/%m/%Y')}."
                    st.rerun()
                if bc2.button("❌ Cancelar", key=f"cp_canc_baixa_{cp_id_sel}", use_container_width=True):
                    st.session_state.pop(f"cp_show_baixa_{cp_id_sel}", None)
                    st.rerun()

            if st.session_state.get(f"cp_confirmar_del_{cp_id_sel}"):
                st.warning(f"⚠️ Confirma a exclusão de **{cp_row.get('descricao')}**?")
                cd1, cd2 = st.columns(2)
                if cd1.button("✅ Sim, Excluir", key=f"cp_del_sim_{cp_id_sel}", use_container_width=True):
                    with conn() as c:
                        c.execute("DELETE FROM contas_pagar WHERE id=?", (cp_id_sel,))
                    st.session_state.pop(f"cp_confirmar_del_{cp_id_sel}", None)
                    st.session_state.cp_editando_id = None
                    st.session_state.cp_duplicando_id = None
                    limpar_cache_app()
                    st.session_state["cp_flash_msg"] = "🗑️ Lançamento excluído."
                    st.rerun()
                if cd2.button("❌ Cancelar", key=f"cp_del_nao_{cp_id_sel}", use_container_width=True):
                    st.session_state.pop(f"cp_confirmar_del_{cp_id_sel}", None)
                    st.rerun()

            if st.session_state.get("cp_duplicando_id") == cp_id_sel:
                st.markdown(f"**📄 Duplicando — base ID {cp_id_sel}**")
                with st.form(f"form_dup_cp_{cp_id_sel}"):
                    d1, d2 = st.columns(2)
                    cp_d_desc = d1.text_input("Descrição", value=cp_row.get("descricao") or "").strip()
                    cp_d_forn = d2.text_input("Fornecedor", value=cp_row.get("fornecedor") or "").strip()

                    d3, d4, d5 = st.columns(3)
                    _dup_cat_idx = CATEGORIAS_CP.index(cp_row.get("categoria")) if cp_row.get("categoria") in CATEGORIAS_CP else 0
                    cp_d_cat = d3.selectbox("Categoria", CATEGORIAS_CP, index=_dup_cat_idx)
                    cp_d_ndoc = d4.text_input("Documento", value=cp_row.get("n_documento") or "").strip()
                    _dup_forma_idx = FORMAS_PAGAMENTO_CP.index(cp_row.get("forma_pagamento")) if cp_row.get("forma_pagamento") in FORMAS_PAGAMENTO_CP else 0
                    cp_d_forma = d5.selectbox("Forma Pagamento", FORMAS_PAGAMENTO_CP, index=_dup_forma_idx)

                    cp_dup_placa_atual = str(cp_row.get("veiculo_placa") or "").strip().upper()
                    opcoes_cp_veic_dup = list(lista_veiculos_full)
                    if cp_dup_placa_atual and not any(placa_de_opcao_veiculo(opt) == cp_dup_placa_atual for opt in opcoes_cp_veic_dup):
                        opcoes_cp_veic_dup = [f"{cp_dup_placa_atual} - (placa manual)"] + opcoes_cp_veic_dup
                    cp_dup_veic_idx = None
                    for i, opt in enumerate(opcoes_cp_veic_dup):
                        if placa_de_opcao_veiculo(opt) == cp_dup_placa_atual:
                            cp_dup_veic_idx = i
                            break
                    cp_d_veiculo = st.selectbox(
                        "Placa do Veículo",
                        opcoes_cp_veic_dup,
                        index=cp_dup_veic_idx,
                        placeholder="Selecione a placa do veículo",
                        key=f"cp_veiculo_dup_{cp_id_sel}",
                    )
                    cp_d_veiculo_placa = placa_de_opcao_veiculo(cp_d_veiculo)

                    d6, d7, d8 = st.columns(3)
                    _dup_dt_em = pd.to_datetime(cp_row.get("data_emissao"), errors="coerce")
                    cp_d_dt_em = d6.date_input("Data Emissão", value=_dup_dt_em.date() if pd.notna(_dup_dt_em) else date.today(), format="DD/MM/YYYY")
                    _dup_dt_vn = pd.to_datetime(cp_row.get("data_vencimento"), errors="coerce")
                    cp_d_dt_vn = d7.date_input("Data Vencimento", value=_dup_dt_vn.date() if pd.notna(_dup_dt_vn) else date.today(), format="DD/MM/YYYY")
                    cp_d_val = d8.number_input("Valor (R$)", min_value=0.01, step=0.01, value=float(cp_row.get("valor") or 0.0), format="%.2f")

                    cp_d_obs = st.text_area("Observação", value=cp_row.get("observacao") or "", height=60)

                    df1, df2 = st.columns(2)
                    gravar_d_cp = df1.form_submit_button("💾 Gravar Conta Duplicada", use_container_width=True, type="primary")
                    cancelar_d_cp = df2.form_submit_button("❌ Cancelar", use_container_width=True)

                    if cancelar_d_cp:
                        st.session_state.cp_duplicando_id = None
                        st.rerun()
                    if gravar_d_cp:
                        if not cp_d_desc:
                            st.warning("Preencha a Descrição.")
                        else:
                            with conn() as c:
                                c.execute(
                                    """INSERT INTO contas_pagar
                                       (descricao, fornecedor, categoria, n_documento, data_emissao,
                                        data_vencimento, valor, data_pagamento, forma_pagamento, observacao, data_cadastro, veiculo_placa)
                                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (
                                        cp_d_desc,
                                        cp_d_forn or None,
                                        cp_d_cat,
                                        cp_d_ndoc or None,
                                        cp_d_dt_em.isoformat(),
                                        cp_d_dt_vn.isoformat(),
                                        float(cp_d_val),
                                        None,
                                        cp_d_forma,
                                        cp_d_obs.strip() or None,
                                        datetime.now().isoformat(),
                                        cp_d_veiculo_placa,
                                    ),
                                )
                            st.session_state.cp_duplicando_id = None
                            limpar_cache_app()
                            st.session_state["cp_flash_msg"] = "✅ Conta a pagar duplicada com sucesso!"
                            st.rerun()

            if st.session_state.get("cp_editando_id") == cp_id_sel:
                st.markdown(f"**✏️ Editando — ID {cp_id_sel}**")
                with st.form(f"form_edit_cp_{cp_id_sel}"):
                    e1, e2 = st.columns(2)
                    cp_e_desc = e1.text_input("Descrição", value=cp_row.get("descricao") or "").strip()
                    cp_e_forn = e2.text_input("Fornecedor", value=cp_row.get("fornecedor") or "").strip()

                    e3, e4, e5 = st.columns(3)
                    _cat_idx = CATEGORIAS_CP.index(cp_row.get("categoria")) if cp_row.get("categoria") in CATEGORIAS_CP else 0
                    cp_e_cat = e3.selectbox("Categoria", CATEGORIAS_CP, index=_cat_idx)
                    cp_e_ndoc = e4.text_input("Documento", value=cp_row.get("n_documento") or "").strip()
                    _forma_idx = FORMAS_PAGAMENTO_CP.index(cp_row.get("forma_pagamento")) if cp_row.get("forma_pagamento") in FORMAS_PAGAMENTO_CP else 0
                    cp_e_forma = e5.selectbox("Forma Pagamento", FORMAS_PAGAMENTO_CP, index=_forma_idx)

                    cp_placa_atual = str(cp_row.get("veiculo_placa") or "").strip().upper()
                    opcoes_cp_veic_ed = list(lista_veiculos_full)
                    if cp_placa_atual and not any(placa_de_opcao_veiculo(opt) == cp_placa_atual for opt in opcoes_cp_veic_ed):
                        opcoes_cp_veic_ed = [f"{cp_placa_atual} - (placa manual)"] + opcoes_cp_veic_ed
                    cp_veic_idx = None
                    for i, opt in enumerate(opcoes_cp_veic_ed):
                        if placa_de_opcao_veiculo(opt) == cp_placa_atual:
                            cp_veic_idx = i
                            break
                    cp_e_veiculo = st.selectbox(
                        "Placa do Veículo",
                        opcoes_cp_veic_ed,
                        index=cp_veic_idx,
                        placeholder="Selecione a placa do veículo",
                        key=f"cp_veiculo_edit_{cp_id_sel}",
                    )
                    cp_e_veiculo_placa = placa_de_opcao_veiculo(cp_e_veiculo)

                    e6, e7, e8 = st.columns(3)
                    _dt_em = pd.to_datetime(cp_row.get("data_emissao"), errors="coerce")
                    cp_e_dt_em = e6.date_input("Data Emissão", value=_dt_em.date() if pd.notna(_dt_em) else date.today(), format="DD/MM/YYYY")
                    _dt_vn = pd.to_datetime(cp_row.get("data_vencimento"), errors="coerce")
                    cp_e_dt_vn = e7.date_input("Data Vencimento", value=_dt_vn.date() if pd.notna(_dt_vn) else date.today(), format="DD/MM/YYYY")
                    cp_e_val = e8.number_input("Valor (R$)", min_value=0.01, step=0.01, value=float(cp_row.get("valor") or 0.0), format="%.2f")

                    cp_e_obs = st.text_area("Observação", value=cp_row.get("observacao") or "", height=60)

                    ef1, ef2 = st.columns(2)
                    gravar_e_cp = ef1.form_submit_button("💾 Gravar Alterações", use_container_width=True, type="primary")
                    cancelar_e_cp = ef2.form_submit_button("❌ Cancelar", use_container_width=True)

                    if cancelar_e_cp:
                        st.session_state.cp_editando_id = None
                        st.rerun()
                    if gravar_e_cp:
                        if not cp_e_desc:
                            st.warning("Preencha a Descrição.")
                        else:
                            with conn() as c:
                                c.execute(
                                    """UPDATE contas_pagar SET descricao=?, fornecedor=?, categoria=?,
                                       n_documento=?, data_emissao=?, data_vencimento=?, valor=?,
                                       forma_pagamento=?, observacao=?, veiculo_placa=? WHERE id=?""",
                                    (cp_e_desc, cp_e_forn or None, cp_e_cat, cp_e_ndoc or None,
                                     cp_e_dt_em.isoformat(), cp_e_dt_vn.isoformat(), float(cp_e_val),
                                     cp_e_forma, cp_e_obs.strip() or None, cp_e_veiculo_placa, cp_id_sel),
                                )
                            st.session_state.cp_editando_id = None
                            limpar_cache_app()
                            st.session_state["cp_flash_msg"] = "✅ Lançamento atualizado."
                            st.rerun()

# =========================
# ABA CONTAS A RECEBER
# =========================
with aba_cr:
    if st.session_state.get("cr_flash_msg"):
        st.success(st.session_state.pop("cr_flash_msg"))

    with conn() as c:
        df_cr = pd.read_sql(
            "SELECT * FROM contas_receber ORDER BY data_vencimento ASC, id DESC", c
        )

    df_cr = _garantir_colunas(df_cr, {
        "data_emissao": None,
        "data_vencimento": None,
        "data_recebimento": None,
        "valor": 0.0,
        "categoria": None,
        "veiculo_placa": None,
    })
    df_cr["data_vencimento"] = pd.to_datetime(df_cr["data_vencimento"], errors="coerce").dt.date
    df_cr["data_emissao"] = pd.to_datetime(df_cr["data_emissao"], errors="coerce").dt.date
    df_cr["data_recebimento"] = pd.to_datetime(df_cr["data_recebimento"], errors="coerce").dt.date
    df_cr["valor"] = pd.to_numeric(df_cr["valor"], errors="coerce").fillna(0.0)
    df_cr = _add_status_cr(df_cr)
    if placa_filtro_calculo and "veiculo_placa" in df_cr.columns:
        placa_ref_cr = str(placa_filtro_calculo).strip().upper()
        df_cr = df_cr[
            df_cr["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_cr
        ]

    if not df_cr.empty:
        hoje_cr = date.today()
        cr_resumo_ini = st.session_state.get("cr_fil_ini", data_ini_carregar)
        cr_resumo_fim = st.session_state.get("cr_fil_fim", data_fim_carregar)
        df_cr_resumo = df_cr[
            df_cr["data_vencimento"].apply(
                lambda d: cr_resumo_ini <= d <= cr_resumo_fim if pd.notna(d) else True
            )
        ]
        df_pend_cr = df_cr_resumo[df_cr_resumo["status"].isin(["PENDENTE", "VENCIDO"])]
        df_venc_cr = df_cr_resumo[df_cr_resumo["status"] == "VENCIDO"]
        df_rec_cr = df_cr_resumo[df_cr_resumo["status"] == "RECEBIDO"]
        df_7d_cr = df_cr_resumo[
            (df_cr_resumo["status"] == "PENDENTE")
            & df_cr_resumo["data_vencimento"].notna()
            & df_cr_resumo["data_vencimento"].apply(lambda d: 0 <= (d - hoje_cr).days <= 7 if pd.notna(d) else False)
        ]

        st.markdown("### 📊 Resumo — Contas a Receber")
        k1, k2, k3, k4 = st.columns(4)
        k1.metric("💰 Total a Receber", brl(df_pend_cr["valor"].sum()), help="Pendentes + Vencidas")
        k2.metric("🔴 Vencidas (não recebidas)", brl(df_venc_cr["valor"].sum()), delta=f"{len(df_venc_cr)} lançamento(s)", delta_color="inverse")
        k3.metric("✅ Total Recebido", brl(df_rec_cr["valor"].sum()))
        k4.metric("⏰ Vence em 7 dias", brl(df_7d_cr["valor"].sum()), delta=f"{len(df_7d_cr)} lançamento(s)", delta_color="off")

        if not df_venc_cr.empty:
            st.error(f"⚠️ Você tem **{len(df_venc_cr)} conta(s) vencida(s)** não recebida(s), totalizando **{brl(df_venc_cr['valor'].sum())}**. Entre em contato com o cliente!")

    st.markdown("---")
    with st.expander("➕ Nova Conta a Receber", expanded=df_cr.empty if not df_cr.empty else True):
        with st.form("form_nova_cr", clear_on_submit=True):
            st.markdown("**Dados da Conta**")
            ga1, ga2 = st.columns(2)
            cr_descricao = ga1.text_input("Descrição *", placeholder="Ex: Frete entrega cliente ABC").strip()
            cr_cliente = ga2.text_input("Cliente *", placeholder="Ex: Empresa ABC Ltda").strip()

            gb1, gb2, gb3 = st.columns(3)
            cr_categoria = gb1.selectbox("Categoria", CATEGORIAS_CR)
            cr_n_doc = gb2.text_input("N. NF / Documento", placeholder="Ex: NF-00456").strip()
            cr_forma = gb3.selectbox("Forma de Recebimento", FORMAS_RECEBIMENTO_CR)

            cr_veiculo = st.selectbox(
                "Placa do Veículo",
                lista_veiculos_full,
                index=None,
                placeholder="Selecione a placa do veículo",
                key="cr_veiculo_form",
            )
            cr_veiculo_placa = placa_de_opcao_veiculo(cr_veiculo)

            gc1, gc2, gc3 = st.columns(3)
            cr_dt_emissao = gc1.date_input("Data Emissão", value=date.today(), format="DD/MM/YYYY", key="cr_dt_emissao_form")
            cr_dt_venc = gc2.date_input("Data Vencimento *", value=date.today() + timedelta(days=30), format="DD/MM/YYYY", key="cr_dt_venc_form")
            cr_valor = gc3.number_input("Valor (R$) *", min_value=0.01, step=0.01, format="%.2f", key="cr_valor_form")

            cr_obs = st.text_area("Observação", height=60, key="cr_obs_form")

            cr_ja_recebido = st.checkbox("Já foi recebido?", key="cr_ja_rec_form")
            cr_dt_recebimento_form = None
            if cr_ja_recebido:
                cr_dt_recebimento_form = st.date_input("Data do Recebimento", value=date.today(), format="DD/MM/YYYY", key="cr_dt_rec_check")

            sub_cr = st.form_submit_button("💾 Salvar Conta a Receber", type="primary", use_container_width=True)
            if sub_cr:
                if not cr_descricao:
                    st.warning("⚠️ Preencha a Descrição.")
                elif not cr_cliente:
                    st.warning("⚠️ Preencha o nome do Cliente.")
                elif cr_valor <= 0:
                    st.warning("⚠️ Informe um valor maior que zero.")
                else:
                    with conn() as c:
                        c.execute(
                            """INSERT INTO contas_receber
                               (descricao, cliente, categoria, n_documento, data_emissao,
                                data_vencimento, valor, data_recebimento, forma_recebimento, observacao, data_cadastro, veiculo_placa)
                               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                            (
                                cr_descricao,
                                cr_cliente,
                                cr_categoria,
                                cr_n_doc or None,
                                cr_dt_emissao.isoformat(),
                                cr_dt_venc.isoformat(),
                                float(cr_valor),
                                cr_dt_recebimento_form.isoformat() if cr_dt_recebimento_form else None,
                                cr_forma,
                                cr_obs.strip() or None,
                                datetime.now().isoformat(),
                                cr_veiculo_placa,
                            ),
                        )
                    limpar_cache_app()
                    st.session_state["cr_flash_msg"] = "✅ Conta a receber salva com sucesso!"
                    st.rerun()

    if not df_cr.empty:
        st.markdown("---")
        st.markdown("### 📋 Lançamentos")
        if placa_filtro_calculo:
            st.caption(f"Filtro por placa ativo: `{rotulo_placa_com_descricao(placa_filtro_calculo)}`")

        gf1, gf2, gf3, gf4 = st.columns([2, 2, 2, 2])
        cr_filtro_status = gf1.radio(
            "Status", ["Todos", "🟡 Pendentes", "🔴 Vencidas", "🟢 Recebidas"],
            horizontal=False, key="cr_fil_status"
        )
        cr_fil_ini = gf2.date_input("Vencimento de:", value=data_ini_carregar, format="DD/MM/YYYY", key="cr_fil_ini")
        cr_fil_fim = gf3.date_input("Vencimento até:", value=data_fim_carregar, format="DD/MM/YYYY", key="cr_fil_fim")
        cr_cats_disp = ["Todas"] + sorted(df_cr["categoria"].dropna().unique().tolist())
        cr_filtro_cat = gf4.selectbox("Categoria", cr_cats_disp, key="cr_fil_cat")

        df_cr_f = df_cr.copy()
        if "Pendentes" in cr_filtro_status:
            df_cr_f = df_cr_f[df_cr_f["status"] == "PENDENTE"]
        elif "Vencidas" in cr_filtro_status:
            df_cr_f = df_cr_f[df_cr_f["status"] == "VENCIDO"]
        elif "Recebidas" in cr_filtro_status:
            df_cr_f = df_cr_f[df_cr_f["status"] == "RECEBIDO"]
        if cr_filtro_cat != "Todas":
            df_cr_f = df_cr_f[df_cr_f["categoria"] == cr_filtro_cat]
        mask_cr_venc = df_cr_f["data_vencimento"].apply(
            lambda d: cr_fil_ini <= d <= cr_fil_fim if pd.notna(d) else True
        ).reindex(df_cr_f.index, fill_value=False).astype(bool)
        df_cr_f = df_cr_f.loc[mask_cr_venc]
        df_cr_f = df_cr_f.sort_values("data_emissao", na_position="last")

        _CR_STATUS_ICON = {"PENDENTE": "🟡 Pendente", "VENCIDO": "🔴 Vencida", "RECEBIDO": "🟢 Recebida"}
        _cr_n_filtrado = len(df_cr_f)
        _cr_total_filtrado = pd.to_numeric(df_cr_f["valor"], errors="coerce").sum() if "valor" in df_cr_f.columns else 0.0
        _cr_show_data = df_cr_f.copy(deep=True)
        _st_cr = _cr_show_data["status"] if "status" in _cr_show_data.columns else pd.Series(["PENDENTE"] * len(_cr_show_data), index=_cr_show_data.index)
        _cr_show_data["Status"] = _st_cr.map(_CR_STATUS_ICON).fillna("🟡 Pendente")
        _cr_show_data = _cr_show_data.rename(columns={
            "id": "ID", "descricao": "Descrição", "cliente": "Cliente",
            "categoria": "Categoria", "veiculo_placa": "Placa", "n_documento": "Documento",
            "data_emissao": "Emissão", "data_vencimento": "Vencimento", "valor": "Valor (R$)",
            "data_recebimento": "Data Rec.", "forma_recebimento": "Forma Rec.",
        })
        colunas_cr_grid = ["ID", "Emissão", "Status", "Descrição", "Cliente", "Categoria", "Placa", "Documento", "Vencimento", "Valor (R$)", "Data Rec.", "Forma Rec."]
        st.dataframe(
            _cr_show_data[[c for c in colunas_cr_grid if c in _cr_show_data.columns]],
            use_container_width=True, hide_index=True,
            column_config={
                "ID": st.column_config.NumberColumn("ID", format="%d", width="small"),
                "Emissão": st.column_config.DateColumn("Emissão", format="DD/MM/YYYY"),
                "Status": st.column_config.TextColumn("Status"),
                "Placa": st.column_config.TextColumn("Placa"),
                "Vencimento": st.column_config.DateColumn("Vencimento", format="DD/MM/YYYY"),
                "Valor (R$)": st.column_config.NumberColumn("Valor (R$)", format="R$ %.2f"),
                "Data Rec.": st.column_config.DateColumn("Data Rec.", format="DD/MM/YYYY"),
            },
        )
        st.caption(f"Exibindo **{_cr_n_filtrado}** de **{len(df_cr)}** lançamentos | Total filtrado: **{brl(_cr_total_filtrado)}**")

        if st.button("🖨️ Imprimir", use_container_width=True, key="btn_print_contas_receber"):
            df_cr_print = df_cr_f.copy()
            total_cr_pendente = pd.to_numeric(df_cr_print.loc[df_cr_print["status"] == "PENDENTE", "valor"], errors="coerce").fillna(0).sum()
            total_cr_vencido = pd.to_numeric(df_cr_print.loc[df_cr_print["status"] == "VENCIDO", "valor"], errors="coerce").fillna(0).sum()
            total_cr_recebido = pd.to_numeric(df_cr_print.loc[df_cr_print["status"] == "RECEBIDO", "valor"], errors="coerce").fillna(0).sum()
            df_cr_print["status_print"] = df_cr_print["status"].map(_CR_STATUS_ICON).fillna("🟡 Pendente")
            html_cr_print = _html_relatorio_financeiro(
                "Relatório de Contas a Receber",
                cr_fil_ini,
                cr_fil_fim,
                cr_filtro_status,
                cr_filtro_cat,
                placa_filtro_calculo,
                df_cr_print,
                [
                    ("id", "ID", "texto"),
                    ("data_emissao", "Emissão", "data"),
                    ("status_print", "Status", "texto"),
                    ("descricao", "Descrição", "texto"),
                    ("cliente", "Cliente", "texto"),
                    ("categoria", "Categoria", "texto"),
                    ("veiculo_placa", "Placa", "texto"),
                    ("n_documento", "Documento", "texto"),
                    ("data_vencimento", "Vencimento", "data"),
                    ("valor", "Valor", "moeda"),
                    ("data_recebimento", "Data Rec.", "data"),
                    ("forma_recebimento", "Forma Rec.", "texto"),
                ],
                [
                    ("Quantidade de Lançamentos", str(len(df_cr_print))),
                    ("Total Pendente", brl(total_cr_pendente)),
                    ("Total Vencido", brl(total_cr_vencido)),
                    ("Total Recebido", brl(total_cr_recebido)),
                ],
                "TOTAL FILTRADO",
                _cr_total_filtrado,
            )
            components.html(html_cr_print, height=1000, scrolling=True)

        st.markdown("#### ⚡ Gerenciar Lançamento")
        if "cr_editando_id" not in st.session_state:
            st.session_state.cr_editando_id = None
        if "cr_duplicando_id" not in st.session_state:
            st.session_state.cr_duplicando_id = None

        opcoes_cr_sel = {
            f"ID {int(r['id'])} | {_CR_STATUS_ICON.get(r['status'], r['status'])} | {str(r.get('cliente') or '')} | {str(r.get('descricao') or '')} | Venc. {r['data_vencimento'].strftime('%d/%m/%Y') if pd.notna(r['data_vencimento']) else '-'} | {brl(float(r.get('valor') or 0))}": int(r["id"])
            for _, r in df_cr_f.iterrows()
        }
        cr_sel_label = st.selectbox(
            "Selecione um lançamento:",
            options=[None] + list(opcoes_cr_sel.keys()),
            format_func=lambda x: "— Selecione para gerenciar —" if x is None else x,
            key="cr_sel_acao",
        )

        if cr_sel_label:
            cr_id_sel = opcoes_cr_sel[cr_sel_label]
            with conn() as c:
                cr_row_raw = c.execute("SELECT * FROM contas_receber WHERE id=?", (cr_id_sel,)).fetchone()
            cr_row = dict(cr_row_raw)
            _cr_tmp = pd.DataFrame([cr_row])
            _cr_tmp["data_vencimento"] = pd.to_datetime(_cr_tmp["data_vencimento"], errors="coerce").dt.date
            _cr_tmp["data_recebimento"] = pd.to_datetime(_cr_tmp["data_recebimento"], errors="coerce").dt.date
            cr_status_atual = _add_status_cr(_cr_tmp)["status"].iloc[0]

            rb1, rb2, rb3, rb4 = st.columns(4)
            if cr_status_atual != "RECEBIDO":
                if rb1.button("✅ Confirmar Recebimento", key=f"cr_baixa_{cr_id_sel}", use_container_width=True, type="primary"):
                    st.session_state[f"cr_show_baixa_{cr_id_sel}"] = True
                    st.session_state.cr_editando_id = None
                    st.session_state.cr_duplicando_id = None
            else:
                if rb1.button("↩️ Estornar Recebimento", key=f"cr_estornar_{cr_id_sel}", use_container_width=True):
                    with conn() as c:
                        c.execute("UPDATE contas_receber SET data_recebimento=NULL WHERE id=?", (cr_id_sel,))
                    limpar_cache_app()
                    st.session_state["cr_flash_msg"] = "↩️ Recebimento estornado."
                    st.rerun()

            if rb2.button("✏️ Editar", key=f"cr_editar_{cr_id_sel}", use_container_width=True):
                st.session_state.cr_editando_id = cr_id_sel
                st.session_state.cr_duplicando_id = None
                st.session_state.pop(f"cr_show_baixa_{cr_id_sel}", None)

            if rb3.button("📄 Duplicar", key=f"cr_duplicar_{cr_id_sel}", use_container_width=True):
                st.session_state.cr_duplicando_id = cr_id_sel
                st.session_state.cr_editando_id = None
                st.session_state.pop(f"cr_show_baixa_{cr_id_sel}", None)

            if rb4.button("🗑️ Excluir", key=f"cr_excluir_{cr_id_sel}", use_container_width=True):
                st.session_state[f"cr_confirmar_del_{cr_id_sel}"] = True

            if st.session_state.get(f"cr_show_baixa_{cr_id_sel}"):
                st.markdown("**💳 Confirmar Recebimento**")
                r1, r2 = st.columns(2)
                cr_dt_baixa = r1.date_input("Data do Recebimento", value=date.today(), format="DD/MM/YYYY", key=f"cr_dt_baixa_{cr_id_sel}")
                cr_forma_baixa = r2.selectbox("Forma de Recebimento", FORMAS_RECEBIMENTO_CR, key=f"cr_forma_baixa_{cr_id_sel}")
                rc1, rc2 = st.columns(2)
                if rc1.button("✅ Confirmar Recebimento", key=f"cr_conf_baixa_{cr_id_sel}", type="primary", use_container_width=True):
                    with conn() as c:
                        c.execute(
                            "UPDATE contas_receber SET data_recebimento=?, forma_recebimento=? WHERE id=?",
                            (cr_dt_baixa.isoformat(), cr_forma_baixa, cr_id_sel),
                        )
                    st.session_state.pop(f"cr_show_baixa_{cr_id_sel}", None)
                    limpar_cache_app()
                    st.session_state["cr_flash_msg"] = f"✅ Recebimento registrado em {cr_dt_baixa.strftime('%d/%m/%Y')}."
                    st.rerun()
                if rc2.button("❌ Cancelar", key=f"cr_canc_baixa_{cr_id_sel}", use_container_width=True):
                    st.session_state.pop(f"cr_show_baixa_{cr_id_sel}", None)
                    st.rerun()

            if st.session_state.get(f"cr_confirmar_del_{cr_id_sel}"):
                st.warning(f"⚠️ Confirma a exclusão de **{cr_row.get('descricao')}**?")
                rd1, rd2 = st.columns(2)
                if rd1.button("✅ Sim, Excluir", key=f"cr_del_sim_{cr_id_sel}", use_container_width=True):
                    with conn() as c:
                        c.execute("DELETE FROM contas_receber WHERE id=?", (cr_id_sel,))
                    st.session_state.pop(f"cr_confirmar_del_{cr_id_sel}", None)
                    st.session_state.cr_editando_id = None
                    st.session_state.cr_duplicando_id = None
                    limpar_cache_app()
                    st.session_state["cr_flash_msg"] = "🗑️ Lançamento excluído."
                    st.rerun()
                if rd2.button("❌ Cancelar", key=f"cr_del_nao_{cr_id_sel}", use_container_width=True):
                    st.session_state.pop(f"cr_confirmar_del_{cr_id_sel}", None)
                    st.rerun()

            if st.session_state.get("cr_duplicando_id") == cr_id_sel:
                st.markdown(f"**📄 Duplicando — base ID {cr_id_sel}**")
                with st.form(f"form_dup_cr_{cr_id_sel}"):
                    dr1, dr2 = st.columns(2)
                    cr_d_desc = dr1.text_input("Descrição", value=cr_row.get("descricao") or "").strip()
                    cr_d_cli = dr2.text_input("Cliente", value=cr_row.get("cliente") or "").strip()

                    dr3, dr4, dr5 = st.columns(3)
                    _cr_dup_cat_idx = CATEGORIAS_CR.index(cr_row.get("categoria")) if cr_row.get("categoria") in CATEGORIAS_CR else 0
                    cr_d_cat = dr3.selectbox("Categoria", CATEGORIAS_CR, index=_cr_dup_cat_idx)
                    cr_d_ndoc = dr4.text_input("Documento", value=cr_row.get("n_documento") or "").strip()
                    _cr_dup_forma_idx = FORMAS_RECEBIMENTO_CR.index(cr_row.get("forma_recebimento")) if cr_row.get("forma_recebimento") in FORMAS_RECEBIMENTO_CR else 0
                    cr_d_forma = dr5.selectbox("Forma Recebimento", FORMAS_RECEBIMENTO_CR, index=_cr_dup_forma_idx)

                    cr_dup_placa_atual = str(cr_row.get("veiculo_placa") or "").strip().upper()
                    opcoes_cr_veic_dup = list(lista_veiculos_full)
                    if cr_dup_placa_atual and not any(placa_de_opcao_veiculo(opt) == cr_dup_placa_atual for opt in opcoes_cr_veic_dup):
                        opcoes_cr_veic_dup = [f"{cr_dup_placa_atual} - (placa manual)"] + opcoes_cr_veic_dup
                    cr_dup_veic_idx = None
                    for i, opt in enumerate(opcoes_cr_veic_dup):
                        if placa_de_opcao_veiculo(opt) == cr_dup_placa_atual:
                            cr_dup_veic_idx = i
                            break
                    cr_d_veiculo = st.selectbox(
                        "Placa do Veículo",
                        opcoes_cr_veic_dup,
                        index=cr_dup_veic_idx,
                        placeholder="Selecione a placa do veículo",
                        key=f"cr_veiculo_dup_{cr_id_sel}",
                    )
                    cr_d_veiculo_placa = placa_de_opcao_veiculo(cr_d_veiculo)

                    dr6, dr7, dr8 = st.columns(3)
                    _cr_dup_dt_em = pd.to_datetime(cr_row.get("data_emissao"), errors="coerce")
                    cr_d_dt_em = dr6.date_input("Data Emissão", value=_cr_dup_dt_em.date() if pd.notna(_cr_dup_dt_em) else date.today(), format="DD/MM/YYYY")
                    _cr_dup_dt_vn = pd.to_datetime(cr_row.get("data_vencimento"), errors="coerce")
                    cr_d_dt_vn = dr7.date_input("Data Vencimento", value=_cr_dup_dt_vn.date() if pd.notna(_cr_dup_dt_vn) else date.today(), format="DD/MM/YYYY")
                    cr_d_val = dr8.number_input("Valor (R$)", min_value=0.01, step=0.01, value=float(cr_row.get("valor") or 0.0), format="%.2f")

                    cr_d_obs = st.text_area("Observação", value=cr_row.get("observacao") or "", height=60)

                    drf1, drf2 = st.columns(2)
                    gravar_d_cr = drf1.form_submit_button("💾 Gravar Conta Duplicada", use_container_width=True, type="primary")
                    cancelar_d_cr = drf2.form_submit_button("❌ Cancelar", use_container_width=True)

                    if cancelar_d_cr:
                        st.session_state.cr_duplicando_id = None
                        st.rerun()
                    if gravar_d_cr:
                        if not cr_d_desc or not cr_d_cli:
                            st.warning("Preencha Descrição e Cliente.")
                        else:
                            with conn() as c:
                                c.execute(
                                    """INSERT INTO contas_receber
                                       (descricao, cliente, categoria, n_documento, data_emissao,
                                        data_vencimento, valor, data_recebimento, forma_recebimento, observacao, data_cadastro, veiculo_placa)
                                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                                    (
                                        cr_d_desc,
                                        cr_d_cli,
                                        cr_d_cat,
                                        cr_d_ndoc or None,
                                        cr_d_dt_em.isoformat(),
                                        cr_d_dt_vn.isoformat(),
                                        float(cr_d_val),
                                        None,
                                        cr_d_forma,
                                        cr_d_obs.strip() or None,
                                        datetime.now().isoformat(),
                                        cr_d_veiculo_placa,
                                    ),
                                )
                            st.session_state.cr_duplicando_id = None
                            limpar_cache_app()
                            st.session_state["cr_flash_msg"] = "✅ Conta a receber duplicada com sucesso!"
                            st.rerun()

            if st.session_state.get("cr_editando_id") == cr_id_sel:
                st.markdown(f"**✏️ Editando — ID {cr_id_sel}**")
                with st.form(f"form_edit_cr_{cr_id_sel}"):
                    re1, re2 = st.columns(2)
                    cr_e_desc = re1.text_input("Descrição", value=cr_row.get("descricao") or "").strip()
                    cr_e_cli = re2.text_input("Cliente", value=cr_row.get("cliente") or "").strip()

                    re3, re4, re5 = st.columns(3)
                    _cr_cat_idx = CATEGORIAS_CR.index(cr_row.get("categoria")) if cr_row.get("categoria") in CATEGORIAS_CR else 0
                    cr_e_cat = re3.selectbox("Categoria", CATEGORIAS_CR, index=_cr_cat_idx)
                    cr_e_ndoc = re4.text_input("Documento", value=cr_row.get("n_documento") or "").strip()
                    _cr_forma_idx = FORMAS_RECEBIMENTO_CR.index(cr_row.get("forma_recebimento")) if cr_row.get("forma_recebimento") in FORMAS_RECEBIMENTO_CR else 0
                    cr_e_forma = re5.selectbox("Forma Recebimento", FORMAS_RECEBIMENTO_CR, index=_cr_forma_idx)

                    cr_placa_atual = str(cr_row.get("veiculo_placa") or "").strip().upper()
                    opcoes_cr_veic_ed = list(lista_veiculos_full)
                    if cr_placa_atual and not any(placa_de_opcao_veiculo(opt) == cr_placa_atual for opt in opcoes_cr_veic_ed):
                        opcoes_cr_veic_ed = [f"{cr_placa_atual} - (placa manual)"] + opcoes_cr_veic_ed
                    cr_veic_idx = None
                    for i, opt in enumerate(opcoes_cr_veic_ed):
                        if placa_de_opcao_veiculo(opt) == cr_placa_atual:
                            cr_veic_idx = i
                            break
                    cr_e_veiculo = st.selectbox(
                        "Placa do Veículo",
                        opcoes_cr_veic_ed,
                        index=cr_veic_idx,
                        placeholder="Selecione a placa do veículo",
                        key=f"cr_veiculo_edit_{cr_id_sel}",
                    )
                    cr_e_veiculo_placa = placa_de_opcao_veiculo(cr_e_veiculo)

                    re6, re7, re8 = st.columns(3)
                    _cr_dt_em = pd.to_datetime(cr_row.get("data_emissao"), errors="coerce")
                    cr_e_dt_em = re6.date_input("Data Emissão", value=_cr_dt_em.date() if pd.notna(_cr_dt_em) else date.today(), format="DD/MM/YYYY")
                    _cr_dt_vn = pd.to_datetime(cr_row.get("data_vencimento"), errors="coerce")
                    cr_e_dt_vn = re7.date_input("Data Vencimento", value=_cr_dt_vn.date() if pd.notna(_cr_dt_vn) else date.today(), format="DD/MM/YYYY")
                    cr_e_val = re8.number_input("Valor (R$)", min_value=0.01, step=0.01, value=float(cr_row.get("valor") or 0.0), format="%.2f")

                    cr_e_obs = st.text_area("Observação", value=cr_row.get("observacao") or "", height=60)

                    rg1, rg2 = st.columns(2)
                    gravar_e_cr = rg1.form_submit_button("💾 Gravar Alterações", use_container_width=True, type="primary")
                    cancelar_e_cr = rg2.form_submit_button("❌ Cancelar", use_container_width=True)

                    if cancelar_e_cr:
                        st.session_state.cr_editando_id = None
                        st.rerun()
                    if gravar_e_cr:
                        if not cr_e_desc or not cr_e_cli:
                            st.warning("Preencha Descrição e Cliente.")
                        else:
                            with conn() as c:
                                c.execute(
                                    """UPDATE contas_receber SET descricao=?, cliente=?, categoria=?,
                                       n_documento=?, data_emissao=?, data_vencimento=?, valor=?,
                                       forma_recebimento=?, observacao=?, veiculo_placa=? WHERE id=?""",
                                    (cr_e_desc, cr_e_cli, cr_e_cat, cr_e_ndoc or None,
                                     cr_e_dt_em.isoformat(), cr_e_dt_vn.isoformat(), float(cr_e_val),
                                     cr_e_forma, cr_e_obs.strip() or None, cr_e_veiculo_placa, cr_id_sel),
                                )
                            st.session_state.cr_editando_id = None
                            limpar_cache_app()
                            st.session_state["cr_flash_msg"] = "✅ Lançamento atualizado."
                            st.rerun()

with aba_fluxo:
    st.subheader("📈 Fluxo de Caixa")
    st.caption("Recebimentos menos pagamentos dentro do período informado.")

    hoje_fluxo = date.today()
    inicio_mes_fluxo = date(hoje_fluxo.year, hoje_fluxo.month, 1)
    fim_mes_fluxo = date(hoje_fluxo.year, 12, 31)
    try:
        fim_mes_fluxo = (date(hoje_fluxo.year + (1 if hoje_fluxo.month == 12 else 0), 1 if hoje_fluxo.month == 12 else hoje_fluxo.month + 1, 1) - timedelta(days=1))
    except Exception:
        fim_mes_fluxo = hoje_fluxo

    fcx1, fcx2, fcx3 = st.columns([1, 1, 1.2])
    fluxo_ini = fcx1.date_input("Período de:", value=inicio_mes_fluxo, format="DD/MM/YYYY", key="fluxo_ini")
    fluxo_fim = fcx2.date_input("Período até:", value=fim_mes_fluxo, format="DD/MM/YYYY", key="fluxo_fim")
    fluxo_base = fcx3.radio(
        "Considerar data",
        ["Vencimento", "Pagamento/Recebimento"],
        horizontal=True,
        key="fluxo_base_data",
    )

    if fluxo_ini > fluxo_fim:
        st.warning("A data inicial não pode ser maior que a data final.")
    else:
        with conn() as c:
            df_fluxo_cp = pd.read_sql("SELECT * FROM contas_pagar", c)
            df_fluxo_cr = pd.read_sql("SELECT * FROM contas_receber", c)

        df_fluxo_cp = _garantir_colunas(df_fluxo_cp, {
            "data_emissao": None,
            "data_vencimento": None,
            "data_pagamento": None,
            "valor": 0.0,
            "descricao": "",
            "fornecedor": "",
            "categoria": "",
            "veiculo_placa": "",
        })
        df_fluxo_cr = _garantir_colunas(df_fluxo_cr, {
            "data_emissao": None,
            "data_vencimento": None,
            "data_recebimento": None,
            "valor": 0.0,
            "descricao": "",
            "cliente": "",
            "categoria": "",
            "veiculo_placa": "",
        })

        for _col_fluxo in ["data_emissao", "data_vencimento", "data_pagamento"]:
            df_fluxo_cp[_col_fluxo] = pd.to_datetime(df_fluxo_cp[_col_fluxo], errors="coerce").dt.date
        for _col_fluxo in ["data_emissao", "data_vencimento", "data_recebimento"]:
            df_fluxo_cr[_col_fluxo] = pd.to_datetime(df_fluxo_cr[_col_fluxo], errors="coerce").dt.date

        df_fluxo_cp["valor"] = pd.to_numeric(df_fluxo_cp["valor"], errors="coerce").fillna(0.0)
        df_fluxo_cr["valor"] = pd.to_numeric(df_fluxo_cr["valor"], errors="coerce").fillna(0.0)
        df_fluxo_cp = _add_status_cp(df_fluxo_cp)
        df_fluxo_cr = _add_status_cr(df_fluxo_cr)

        if placa_filtro_calculo:
            placa_ref_fluxo = str(placa_filtro_calculo).strip().upper()
            df_fluxo_cp = df_fluxo_cp[
                df_fluxo_cp["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_fluxo
            ]
            df_fluxo_cr = df_fluxo_cr[
                df_fluxo_cr["veiculo_placa"].fillna("").astype(str).str.strip().str.upper() == placa_ref_fluxo
            ]
            st.caption(f"Filtro por placa ativo: `{rotulo_placa_com_descricao(placa_filtro_calculo)}`")

        col_data_cp = "data_vencimento" if fluxo_base == "Vencimento" else "data_pagamento"
        col_data_cr = "data_vencimento" if fluxo_base == "Vencimento" else "data_recebimento"

        mask_cp_fluxo = df_fluxo_cp[col_data_cp].apply(
            lambda d: fluxo_ini <= d <= fluxo_fim if pd.notna(d) else False
        ).reindex(df_fluxo_cp.index, fill_value=False).astype(bool)
        mask_cr_fluxo = df_fluxo_cr[col_data_cr].apply(
            lambda d: fluxo_ini <= d <= fluxo_fim if pd.notna(d) else False
        ).reindex(df_fluxo_cr.index, fill_value=False).astype(bool)

        df_cp_periodo = df_fluxo_cp.loc[mask_cp_fluxo].copy()
        df_cr_periodo = df_fluxo_cr.loc[mask_cr_fluxo].copy()

        total_receber_fluxo = float(df_cr_periodo["valor"].sum()) if not df_cr_periodo.empty else 0.0
        total_pagar_fluxo = float(df_cp_periodo["valor"].sum()) if not df_cp_periodo.empty else 0.0
        saldo_fluxo = total_receber_fluxo - total_pagar_fluxo

        m1, m2, m3 = st.columns(3)
        m1.metric("💰 Contas a Receber", brl(total_receber_fluxo), delta=f"{len(df_cr_periodo)} lançamento(s)", delta_color="off")
        m2.metric("🧾 Contas a Pagar", brl(total_pagar_fluxo), delta=f"{len(df_cp_periodo)} lançamento(s)", delta_color="inverse")
        m3.metric("📌 Saldo do Período", brl(saldo_fluxo), delta="Receber - Pagar", delta_color="normal" if saldo_fluxo >= 0 else "inverse")

        if saldo_fluxo < 0:
            st.error(f"Saldo negativo no período: **{brl(saldo_fluxo)}**.")
        else:
            st.success(f"Saldo positivo no período: **{brl(saldo_fluxo)}**.")

        st.markdown("### 📋 Detalhamento do Período")

        det_cr = pd.DataFrame()
        if not df_cr_periodo.empty:
            det_cr = df_cr_periodo.copy()
            det_cr["Tipo"] = "Receber"
            det_cr["Pessoa"] = det_cr["cliente"].fillna("")
            det_cr["Data"] = det_cr[col_data_cr]
            det_cr["Entrada (R$)"] = det_cr["valor"]
            det_cr["Saída (R$)"] = 0.0

        det_cp = pd.DataFrame()
        if not df_cp_periodo.empty:
            det_cp = df_cp_periodo.copy()
            det_cp["Tipo"] = "Pagar"
            det_cp["Pessoa"] = det_cp["fornecedor"].fillna("")
            det_cp["Data"] = det_cp[col_data_cp]
            det_cp["Entrada (R$)"] = 0.0
            det_cp["Saída (R$)"] = det_cp["valor"]

        df_detalhe_fluxo = pd.concat([det_cr, det_cp], ignore_index=True)
        if df_detalhe_fluxo.empty:
            st.info("Nenhum lançamento encontrado para o período informado.")
        else:
            df_detalhe_fluxo["Saldo Linha (R$)"] = df_detalhe_fluxo["Entrada (R$)"] - df_detalhe_fluxo["Saída (R$)"]
            df_detalhe_fluxo = df_detalhe_fluxo.sort_values(["Data", "Tipo"], na_position="last")
            df_detalhe_fluxo = df_detalhe_fluxo.rename(columns={
                "descricao": "Descrição",
                "categoria": "Categoria",
                "veiculo_placa": "Placa",
                "status": "Status",
            })
            colunas_fluxo = ["Data", "Tipo", "Status", "Descrição", "Pessoa", "Categoria", "Placa", "Entrada (R$)", "Saída (R$)", "Saldo Linha (R$)"]
            st.dataframe(
                df_detalhe_fluxo[[c for c in colunas_fluxo if c in df_detalhe_fluxo.columns]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
                    "Entrada (R$)": st.column_config.NumberColumn("Entrada (R$)", format="R$ %.2f"),
                    "Saída (R$)": st.column_config.NumberColumn("Saída (R$)", format="R$ %.2f"),
                    "Saldo Linha (R$)": st.column_config.NumberColumn("Saldo Linha (R$)", format="R$ %.2f"),
                },
            )

with aba18:
    st.subheader("🔔 ME LEMBRA")
    st.caption("Cadastro de lembretes com prazo de alerta definido por lançamento.")
    st.info("Fluxo sugerido: 1) Cadastre Frota, 2) Cadastre Descrição, 3) Cadastre o Lembrete, 4) Acompanhe e edite na lista abaixo.")

    with st.expander("➕ 1) Cadastro de Frota", expanded=False):
        with st.form("form_ml_frota", clear_on_submit=True):
            nova_frota_ml = st.text_input("Nova Frota", key="ml_nova_frota").strip()
            if st.form_submit_button("💾 Gravar", key="btn_ml_frota_cadastro_gravar"):
                if not nova_frota_ml:
                    st.warning("Informe a frota para salvar.")
                else:
                    try:
                        with conn() as c:
                            c.execute(
                                "INSERT INTO me_lembra_frotas (frota) VALUES (?)",
                                (nova_frota_ml,),
                            )
                        alerta_gravado()
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.warning("Essa frota já está cadastrada.")

        with conn() as c:
            frotas_ml_db = c.execute(
                "SELECT id, frota FROM me_lembra_frotas ORDER BY frota ASC"
            ).fetchall()
        lista_frotas_ml = [r["frota"] for r in frotas_ml_db]
        mapa_frotas_ml = {f"{r['id']} - {r['frota']}": int(r["id"]) for r in frotas_ml_db}

        c_fa1, c_fa2 = st.columns([2, 1])
        if mapa_frotas_ml:
            frota_sel_alt = c_fa1.selectbox(
                "Frota cadastrada",
                options=list(mapa_frotas_ml.keys()),
                index=None,
                placeholder="Selecione uma frota para alterar",
                key="ml_frota_sel_alt",
            )
        else:
            c_fa1.info("Nenhuma frota cadastrada ainda.")
            frota_sel_alt = None
        if "ml_frota_edit_id" not in st.session_state:
            st.session_state.ml_frota_edit_id = None

        if c_fa2.button("✏️ ALTERAR FROTA", key="ml_btn_alterar_frota", use_container_width=True):
            if frota_sel_alt:
                st.session_state.ml_frota_edit_id = mapa_frotas_ml[frota_sel_alt]
            else:
                st.warning("Selecione uma frota para alterar.")

        if st.session_state.ml_frota_edit_id is not None:
            row_frota_edit = next((r for r in frotas_ml_db if int(r["id"]) == int(st.session_state.ml_frota_edit_id)), None)
            valor_atual_frota = row_frota_edit["frota"] if row_frota_edit else ""

            with st.form("form_ml_gravar_frota"):
                nova_frota_edit = st.text_input("Frota (Editar)", value=valor_atual_frota).strip()
                bf1, bf2 = st.columns(2)
                gravar_frota = bf1.form_submit_button("💾 Gravar", use_container_width=True, key="btn_ml_frota_edicao_gravar")
                cancelar_frota = bf2.form_submit_button("❌ CANCELAR", use_container_width=True)

                if cancelar_frota:
                    st.session_state.ml_frota_edit_id = None
                    st.rerun()

                if gravar_frota:
                    if not nova_frota_edit:
                        st.warning("Informe a frota para gravar.")
                    else:
                        try:
                            with conn() as c:
                                old_frota = c.execute(
                                    "SELECT frota FROM me_lembra_frotas WHERE id=?",
                                    (int(st.session_state.ml_frota_edit_id),),
                                ).fetchone()
                                old_frota_txt = old_frota["frota"] if old_frota else ""

                                c.execute(
                                    "UPDATE me_lembra_frotas SET frota=? WHERE id=?",
                                    (nova_frota_edit, int(st.session_state.ml_frota_edit_id)),
                                )
                                if old_frota_txt:
                                    c.execute(
                                        "UPDATE me_lembra_descricoes SET frota=? WHERE frota=?",
                                        (nova_frota_edit, old_frota_txt),
                                    )
                                    c.execute(
                                        "UPDATE me_lembra SET frota=? WHERE frota=?",
                                        (nova_frota_edit, old_frota_txt),
                                    )
                            st.session_state.ml_frota_edit_id = None
                            alerta_gravado()
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.warning("Já existe uma frota com esse nome.")

    st.divider()
    with st.expander("➕ 2) Cadastro de Descrição", expanded=False):
        if not lista_frotas_ml:
            st.warning("Cadastre pelo menos uma frota antes de cadastrar a descrição.")
        else:
            with st.form("form_ml_descricao", clear_on_submit=True):
                d1, d2 = st.columns(2)
                nova_descricao_ml = d1.text_input("Nova Descrição", key="ml_nova_descricao").strip()
                nova_frota_desc_ml = d2.selectbox(
                    "Frota",
                    options=lista_frotas_ml,
                    index=None,
                    placeholder="Selecione uma frota cadastrada",
                    key="ml_nova_frota_desc_sel",
                )
                if st.form_submit_button("💾 Gravar", key="btn_ml_descricao_cadastro_gravar"):
                    if not nova_descricao_ml:
                        st.warning("Informe a descrição para salvar.")
                    elif not nova_frota_desc_ml:
                        st.warning("Informe a frota para salvar.")
                    else:
                        try:
                            with conn() as c:
                                c.execute(
                                    "INSERT INTO me_lembra_descricoes (descricao, frota) VALUES (?, ?)",
                                    (nova_descricao_ml, nova_frota_desc_ml),
                                )
                            alerta_gravado()
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.warning("Essa descrição já está cadastrada.")

        with conn() as c:
            descricoes_ml_db = c.execute(
                "SELECT id, descricao, frota FROM me_lembra_descricoes ORDER BY descricao ASC"
            ).fetchall()
        lista_descricoes_ml = [r["descricao"] for r in descricoes_ml_db]
        mapa_frota_por_descricao_ml = {r["descricao"]: ((r["frota"] or "").strip()) for r in descricoes_ml_db}
        mapa_descricoes_ml = {
            f"{r['id']} - {r['descricao']} | Frota: {r['frota'] or '-'}": int(r["id"])
            for r in descricoes_ml_db
        }

        c_alt1, c_alt2 = st.columns([2, 1])
        descricao_sel_alt = c_alt1.selectbox(
            "Descrição cadastrada",
            options=list(mapa_descricoes_ml.keys()),
            index=None,
            placeholder="Selecione uma descrição para alterar",
            key="ml_desc_sel_alt",
        )
        if "ml_desc_edit_id" not in st.session_state:
            st.session_state.ml_desc_edit_id = None

        if c_alt2.button("✏️ ALTERAR", key="ml_btn_alterar_desc", use_container_width=True):
            if descricao_sel_alt:
                st.session_state.ml_desc_edit_id = mapa_descricoes_ml[descricao_sel_alt]
            else:
                st.warning("Selecione uma descrição para alterar.")

        if st.session_state.ml_desc_edit_id is not None:
            row_desc_edit = next((r for r in descricoes_ml_db if int(r["id"]) == int(st.session_state.ml_desc_edit_id)), None)
            valor_atual_desc = row_desc_edit["descricao"] if row_desc_edit else ""
            valor_atual_frota_desc = (row_desc_edit["frota"] if row_desc_edit else "") or ""

            with st.form("form_ml_gravar_descricao"):
                de1, de2 = st.columns(2)
                nova_desc_edit = de1.text_input("Descrição (Editar)", value=valor_atual_desc).strip()
                opcoes_frota_desc = list(lista_frotas_ml)
                if valor_atual_frota_desc and valor_atual_frota_desc not in opcoes_frota_desc:
                    opcoes_frota_desc.append(valor_atual_frota_desc)
                idx_frota_desc = opcoes_frota_desc.index(valor_atual_frota_desc) if valor_atual_frota_desc in opcoes_frota_desc else None
                nova_frota_desc_edit = de2.selectbox(
                    "Frota (Editar)",
                    options=opcoes_frota_desc,
                    index=idx_frota_desc,
                    placeholder="Selecione uma frota cadastrada",
                )
                b_desc1, b_desc2 = st.columns(2)
                gravar_desc = b_desc1.form_submit_button("💾 Gravar", use_container_width=True, key="btn_ml_descricao_edicao_gravar")
                cancelar_desc = b_desc2.form_submit_button("❌ CANCELAR", use_container_width=True)

                if cancelar_desc:
                    st.session_state.ml_desc_edit_id = None
                    st.rerun()

                if gravar_desc:
                    if not nova_desc_edit:
                        st.warning("Informe a descrição para gravar.")
                    elif not nova_frota_desc_edit:
                        st.warning("Informe a frota para gravar.")
                    else:
                        try:
                            with conn() as c:
                                old_desc = c.execute(
                                    "SELECT descricao, frota FROM me_lembra_descricoes WHERE id=?",
                                    (int(st.session_state.ml_desc_edit_id),),
                                ).fetchone()
                                old_desc_txt = old_desc["descricao"] if old_desc else ""
                                old_frota_txt = (old_desc["frota"] if old_desc else "") or ""

                                c.execute(
                                    "UPDATE me_lembra_descricoes SET descricao=?, frota=? WHERE id=?",
                                    (nova_desc_edit, nova_frota_desc_edit, int(st.session_state.ml_desc_edit_id)),
                                )
                                if old_desc_txt:
                                    c.execute(
                                        "UPDATE me_lembra SET descricao=?, frota=? WHERE descricao=? AND COALESCE(frota,'')=COALESCE(?, '')",
                                        (nova_desc_edit, nova_frota_desc_edit, old_desc_txt, old_frota_txt),
                                    )
                            st.session_state.ml_desc_edit_id = None
                            alerta_gravado()
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.warning("Já existe uma descrição com esse nome.")

    st.divider()
    with st.expander("➕ 3) Cadastro do Lembrete", expanded=False):
        if not lista_frotas_ml:
            st.warning("Cadastre pelo menos uma frota antes de lançar lembretes.")
        elif not lista_descricoes_ml:
            st.warning("Cadastre pelo menos uma descrição antes de lançar lembretes.")
        else:
            with st.form("form_me_lembra", clear_on_submit=True):
                c1, c2, c3, c4, c5, c6 = st.columns(6)
                data_ativacao_ml = c1.date_input("Data Ativação", value=date.today(), format="DD/MM/YYYY", key="ml_data_ativacao")
                data_vencimento_ml = c2.date_input("Data Vencimento", format="DD/MM/YYYY", key="ml_data_vencimento")
                dias_alerta_lancamento_ml = c5.number_input(
                    "Dias para alerta",
                    min_value=1,
                    max_value=365,
                    value=30,
                    step=1,
                    key="ml_dias_alerta_lancamento",
                )
                descricao_veiculo_ml = c3.selectbox(
                    "Descrição Veículo",
                    options=lista_veiculos_full,
                    index=None,
                    placeholder="Selecione no cadastro de veículos",
                    key="ml_descricao_veiculo",
                )
                descricao_ml = st.selectbox(
                    "Descrição",
                    options=lista_descricoes_ml,
                    index=None,
                    placeholder="Selecione uma descrição cadastrada",
                    key="ml_descricao_sel",
                )
                idx_frota_sel = None
                if descricao_ml:
                    frota_sugerida = mapa_frota_por_descricao_ml.get(descricao_ml, "")
                    if frota_sugerida in lista_frotas_ml:
                        idx_frota_sel = lista_frotas_ml.index(frota_sugerida)
                frota_ml = c4.selectbox(
                    "Frota",
                    options=lista_frotas_ml,
                    index=idx_frota_sel,
                    placeholder="Selecione uma frota cadastrada",
                    key="ml_frota_sel",
                )
                nao_ativar_popup_ml = c6.checkbox(
                    "Não ativar popup",
                    value=False,
                    key="ml_nao_ativar_popup",
                )

                if st.form_submit_button("💾 Gravar", type="primary", key="btn_ml_lembrete_cadastro_gravar"):
                    if not descricao_ml:
                        st.warning("Cadastre e selecione uma descrição para continuar.")
                    elif not descricao_veiculo_ml:
                        st.warning("Selecione a Descrição Veículo para continuar.")
                    elif not frota_ml:
                        st.warning("Selecione a frota para continuar.")
                    elif data_vencimento_ml < data_ativacao_ml:
                        st.warning("A Data de Vencimento não pode ser menor que a Data de Ativação.")
                    else:
                        with conn() as c:
                            c.execute(
                                """INSERT INTO me_lembra
                                   (data_ativacao, data_vencimento, data_alerta, descricao, descricao_veiculo, frota, dias_alerta, popup_ativo)
                                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                                (
                                    data_ativacao_ml.isoformat(),
                                    data_vencimento_ml.isoformat(),
                                    None,
                                    descricao_ml,
                                    descricao_veiculo_ml,
                                    frota_ml,
                                    int(dias_alerta_lancamento_ml),
                                    0 if nao_ativar_popup_ml else 1,
                                ),
                            )
                        alerta_gravado()
                        st.rerun()

    def preparar_df_me_lembra(df_base: pd.DataFrame) -> pd.DataFrame:
        df_preparado = df_base.copy()
        hoje_ref = pd.Timestamp(date.today())

        venc_ts = pd.to_datetime(df_preparado["data_vencimento"], errors="coerce").dt.normalize()
        ativ_ts = pd.to_datetime(df_preparado["data_ativacao"], errors="coerce").dt.normalize()
        df_preparado["data_ativacao"] = ativ_ts.dt.date
        df_preparado["data_vencimento"] = venc_ts.dt.date
        df_preparado["dias_para_vencer"] = (venc_ts - hoje_ref).dt.days
        df_preparado["dias_alerta"] = pd.to_numeric(df_preparado.get("dias_alerta", 30), errors="coerce").fillna(30).astype(int)
        df_preparado["popup_ativo"] = pd.to_numeric(df_preparado.get("popup_ativo", 1), errors="coerce").fillna(1).astype(int)
        df_preparado["popup"] = df_preparado["popup_ativo"].apply(lambda v: "Ativo" if int(v) == 1 else "Desativado")
        df_preparado["alarme_ativo"] = (
            df_preparado["dias_para_vencer"].notna()
            & (df_preparado["dias_para_vencer"] >= 0)
            & (df_preparado["dias_para_vencer"] <= df_preparado["dias_alerta"])
        )

        def _status_por_regra(dias, dias_alerta):
            if pd.isna(dias):
                return "Sem vencimento"
            dias_int = int(dias)
            if dias_int < 0:
                return "Vencido"
            alerta_int = max(int(dias_alerta or 30), 1)
            if dias_int == alerta_int:
                return f"Faltam {alerta_int} dias"
            if 0 <= dias_int < alerta_int:
                return "Alarme ativo"
            return "No prazo"

        df_preparado["status"] = df_preparado.apply(
            lambda row: _status_por_regra(row["dias_para_vencer"], row["dias_alerta"]),
            axis=1,
        )
        return df_preparado

    with conn() as c:
        df_ml = pd.read_sql(
            """SELECT id, data_ativacao, data_vencimento, data_alerta, descricao, descricao_veiculo, frota, COALESCE(dias_alerta, 30) AS dias_alerta, COALESCE(popup_ativo, 1) AS popup_ativo
               FROM me_lembra
               ORDER BY date(data_vencimento) ASC, id DESC""",
            c,
        )

    if not df_ml.empty:
        df_ml = preparar_df_me_lembra(df_ml)

        avisos_alerta = df_ml[df_ml["alarme_ativo"] == True]
        proximos_alerta = df_ml[df_ml["alarme_ativo"] == True]
        vencidos = df_ml[df_ml["dias_para_vencer"] < 0]

        if not avisos_alerta.empty:
            descricoes = ", ".join(
                avisos_alerta.apply(lambda row: f"{row['descricao']} ({int(row['dias_alerta'])} dias)", axis=1).tolist()
            )
            st.warning(f"Alerta de vencimento: {descricoes}")
        if not proximos_alerta.empty:
            st.info(f"Lembretes dentro do prazo de alerta: {len(proximos_alerta)}")
        if not vencidos.empty:
            st.error(f"Lembretes vencidos: {len(vencidos)}")

        st.divider()
        st.markdown("### 4) Acompanhamento dos Lembretes")
        m1, m2, m3 = st.columns(3)
        m1.metric("Total", len(df_ml))
        m2.metric("Vencidos", len(vencidos))
        m3.metric("Dentro do prazo de alerta", len(proximos_alerta))

        f1, f2 = st.columns([2, 1])
        busca_ml = f1.text_input("Buscar (Descrição / Veículo / Frota)", key="ml_busca_texto").strip().lower()
        opcoes_status_ml = sorted(df_ml["status"].dropna().astype(str).unique().tolist())
        filtro_status_ml = f2.multiselect("Filtrar status", options=opcoes_status_ml, key="ml_filtro_status")

        df_ml_exibir = df_ml.copy()
        if busca_ml:
            df_ml_exibir = df_ml_exibir[
                df_ml_exibir["descricao"].astype(str).str.lower().str.contains(busca_ml, na=False)
                | df_ml_exibir["descricao_veiculo"].astype(str).str.lower().str.contains(busca_ml, na=False)
                | df_ml_exibir["frota"].astype(str).str.lower().str.contains(busca_ml, na=False)
            ]
        if filtro_status_ml:
            df_ml_exibir = df_ml_exibir[df_ml_exibir["status"].isin(filtro_status_ml)]

        st.dataframe(
            df_ml_exibir[["id", "data_ativacao", "data_vencimento", "dias_alerta", "popup", "frota", "descricao_veiculo", "descricao", "dias_para_vencer", "status"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", format="%d"),
                "data_ativacao": st.column_config.DateColumn("Data Ativação", format="DD/MM/YYYY"),
                "data_vencimento": st.column_config.DateColumn("Data Vencimento", format="DD/MM/YYYY"),
                "dias_alerta": st.column_config.NumberColumn("Dias Alerta", format="%d"),
                "popup": st.column_config.TextColumn("Popup"),
                "frota": st.column_config.TextColumn("Frota"),
                "descricao_veiculo": st.column_config.TextColumn("Descrição Veículo"),
                "descricao": st.column_config.TextColumn("Descrição"),
                "dias_para_vencer": st.column_config.NumberColumn("Dias para vencer", format="%d"),
                "status": st.column_config.TextColumn("Status"),
            },
        )

        st.markdown("##### 4.1) Alterar Lembrete")
        mapa_ml_edicao = {
            (
                f"ID: {int(r['id'])} | Descrição: {r['descricao']} | "
                f"Veículo: {r['descricao_veiculo'] if pd.notna(r['descricao_veiculo']) and r['descricao_veiculo'] else '-'} | "
                f"Frota: {r['frota'] if pd.notna(r['frota']) and r['frota'] else '-'} | "
                f"Venc.: {r['data_vencimento'].strftime('%d/%m/%Y') if pd.notna(r['data_vencimento']) else '-'}"
            ): int(r["id"])
            for _, r in df_ml.sort_values(by="id", ascending=False).iterrows()
        }
        lembrete_sel_label = st.selectbox(
            "Selecione o lembrete para alteração/exclusão (com ID)",
            options=list(mapa_ml_edicao.keys()),
            index=None,
            placeholder="Escolha um registro",
            key="ml_sel_alteracao",
        )

        if "ml_em_edicao_id" not in st.session_state:
            st.session_state.ml_em_edicao_id = None
        if "ml_excluir_id" not in st.session_state:
            st.session_state.ml_excluir_id = None

        if lembrete_sel_label:
            id_sel = mapa_ml_edicao[lembrete_sel_label]
            a1, a2 = st.columns(2)
            if a1.button("✏️ EDITAR", key="btn_ml_alterar", use_container_width=True):
                st.session_state.ml_em_edicao_id = id_sel
                st.session_state.ml_excluir_id = None
            if a2.button("🗑️ EXCLUIR", key="btn_ml_excluir", type="primary", use_container_width=True):
                st.session_state.ml_excluir_id = id_sel

        if st.session_state.ml_excluir_id is not None:
            st.warning(f"Confirma a exclusão do lembrete ID {int(st.session_state.ml_excluir_id)}?")
            e1, e2 = st.columns(2)
            if e1.button("✅ Confirmar exclusão", key="btn_ml_confirmar_exclusao", type="primary", use_container_width=True):
                with conn() as c:
                    c.execute("DELETE FROM me_lembra WHERE id=?", (int(st.session_state.ml_excluir_id),))
                st.session_state.ml_excluir_id = None
                st.session_state.ml_em_edicao_id = None
                st.success("Lembrete excluído com sucesso.")
                st.rerun()
            if e2.button("❌ Cancelar exclusão", key="btn_ml_cancelar_exclusao", use_container_width=True):
                st.session_state.ml_excluir_id = None
                st.rerun()

        if st.session_state.ml_em_edicao_id is not None:
            id_edit = st.session_state.ml_em_edicao_id
            row_edit = df_ml[df_ml["id"] == id_edit]

            if row_edit.empty:
                st.session_state.ml_em_edicao_id = None
            else:
                reg = row_edit.iloc[0]
                valor_data_ativ = reg["data_ativacao"] if pd.notna(reg["data_ativacao"]) else date.today()
                valor_data_venc = reg["data_vencimento"] if pd.notna(reg["data_vencimento"]) else date.today()
                valor_frota = reg["frota"] if pd.notna(reg["frota"]) else ""
                valor_dias_alerta = int(reg["dias_alerta"]) if pd.notna(reg["dias_alerta"]) else 30
                valor_popup_ativo = int(reg["popup_ativo"]) if pd.notna(reg["popup_ativo"]) else 1

                opcoes_desc = list(lista_descricoes_ml)
                if reg["descricao"] and reg["descricao"] not in opcoes_desc:
                    opcoes_desc.append(reg["descricao"])
                idx_desc = opcoes_desc.index(reg["descricao"]) if reg["descricao"] in opcoes_desc else 0

                opcoes_veic = list(lista_veiculos_full)
                valor_veic = reg["descricao_veiculo"] if pd.notna(reg["descricao_veiculo"]) else None
                if valor_veic and valor_veic not in opcoes_veic:
                    opcoes_veic.append(valor_veic)
                idx_veic = opcoes_veic.index(valor_veic) if valor_veic in opcoes_veic else None

                with st.form("form_ml_gravar"):
                    e1, e2, e3, e4, e5, e6 = st.columns(6)
                    nova_data_ativ = e1.date_input(
                        "Data Ativação (Editar)",
                        value=valor_data_ativ,
                        format="DD/MM/YYYY",
                        key=f"ml_edit_data_ativ_{int(id_edit)}",
                    )
                    nova_data_venc = e2.date_input(
                        "Data Vencimento (Editar)",
                        value=valor_data_venc,
                        format="DD/MM/YYYY",
                        key=f"ml_edit_data_venc_{int(id_edit)}",
                    )
                    novo_veiculo = e3.selectbox(
                        "Descrição Veículo (Editar)",
                        options=opcoes_veic,
                        index=idx_veic,
                        key=f"ml_edit_veiculo_{int(id_edit)}",
                    )
                    opcoes_frota_edit = list(lista_frotas_ml)
                    if valor_frota and valor_frota not in opcoes_frota_edit:
                        opcoes_frota_edit.append(valor_frota)
                    idx_frota_edit = opcoes_frota_edit.index(valor_frota) if valor_frota in opcoes_frota_edit else None
                    nova_frota = e4.selectbox(
                        "Frota (Editar)",
                        options=opcoes_frota_edit,
                        index=idx_frota_edit,
                        placeholder="Selecione uma frota",
                        key=f"ml_edit_frota_{int(id_edit)}",
                    )
                    novo_dias_alerta = e5.number_input(
                        "Dias Alerta (Editar)",
                        min_value=1,
                        max_value=365,
                        value=valor_dias_alerta,
                        step=1,
                        key=f"ml_edit_dias_alerta_{int(id_edit)}",
                    )
                    novo_nao_ativar_popup = e6.checkbox(
                        "Não ativar popup (Editar)",
                        value=(valor_popup_ativo == 0),
                        key=f"ml_edit_nao_ativar_popup_{int(id_edit)}",
                    )
                    nova_descricao = st.selectbox(
                        "Descrição (Editar)",
                        options=opcoes_desc,
                        index=idx_desc,
                        key=f"ml_edit_desc_{int(id_edit)}",
                    )

                    b1, b2 = st.columns(2)
                    gravar_ml = b1.form_submit_button("💾 Gravar", use_container_width=True, key=f"btn_ml_lembrete_edicao_gravar_{int(id_edit)}")
                    cancelar_ml = b2.form_submit_button("❌ CANCELAR", use_container_width=True)

                    if cancelar_ml:
                        st.session_state.ml_em_edicao_id = None
                        st.rerun()

                    if gravar_ml:
                        if nova_data_venc < nova_data_ativ:
                            st.warning("A Data de Vencimento não pode ser menor que a Data de Ativação.")
                        elif not nova_frota:
                            st.warning("Informe a Frota para gravar.")
                        else:
                            with conn() as c:
                                c.execute(
                                    """UPDATE me_lembra
                                       SET data_ativacao=?, data_vencimento=?, data_alerta=?, descricao=?, descricao_veiculo=?, frota=?, dias_alerta=?, popup_ativo=?
                                       WHERE id=?""",
                                    (
                                        nova_data_ativ.isoformat(),
                                        nova_data_venc.isoformat(),
                                        None,
                                        nova_descricao,
                                        novo_veiculo,
                                        nova_frota,
                                        int(novo_dias_alerta),
                                        0 if novo_nao_ativar_popup else 1,
                                        int(id_edit),
                                    ),
                                )
                            st.session_state.ml_em_edicao_id = None
                            alerta_gravado()
                            st.rerun()
    else:
        st.info("Nenhum lembrete cadastrado.")

# =========================
# ABA 20 - ANOTAÇÕES
# =========================
with aba20:
    st.subheader("📝 Anotações")
    st.caption("Use esta aba para registrar anotações importantes do dia a dia.")
    st.info("Dica: registre primeiro a data e depois uma descrição curta e objetiva para facilitar consultas futuras.")

    with st.form("form_anotacoes", clear_on_submit=True):
        c1, c2 = st.columns([1, 3])
        data_anotacao = c1.date_input("Data", value=date.today(), format="DD/MM/YYYY", key="anot_data")
        descricao_anotacao = c2.text_area("Descrição", placeholder="Digite a anotação importante do dia...", key="anot_descricao")
        documento_anotacao = st.file_uploader(
            "Anexar Documento(s)",
            type=["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx", "xls", "xlsx", "txt", "csv"],
            accept_multiple_files=True,
            key="anot_documento",
        )

        if st.form_submit_button("💾 Gravar", type="primary", key="btn_anotacoes_gravar"):
            if not str(descricao_anotacao or "").strip():
                st.warning("Informe a descrição para salvar a anotação.")
            else:
                documentos_salvos = salvar_documentos_anotacao(documento_anotacao)
                documento_principal = documentos_salvos[0] if documentos_salvos else None
                with conn() as c:
                    cursor = c.execute(
                        "INSERT INTO anotacoes (data, descricao, documento_nome, documento_arquivo) VALUES (?, ?, ?, ?)",
                        (
                            data_anotacao.isoformat(),
                            descricao_anotacao.strip(),
                            documento_principal["nome_arquivo"] if documento_principal else None,
                            documento_principal["caminho_arquivo"] if documento_principal else None,
                        ),
                    )
                    anotacao_id = int(cursor.lastrowid)
                    for documento_salvo in documentos_salvos:
                        c.execute(
                            """INSERT INTO anotacoes_anexos (anotacao_id, nome_arquivo, caminho_arquivo, data_inclusao)
                               VALUES (?, ?, ?, ?)""",
                            (
                                anotacao_id,
                                documento_salvo["nome_arquivo"],
                                documento_salvo["caminho_arquivo"],
                                datetime.now().isoformat(),
                            ),
                        )
                alerta_gravado()
                st.rerun()

    with conn() as c:
        df_anotacoes = pd.read_sql(
            """SELECT id, data, descricao, documento_nome, documento_arquivo
               FROM anotacoes
               ORDER BY date(data) DESC, id DESC""",
            c,
        )

    if df_anotacoes.empty:
        st.info("Nenhuma anotação cadastrada ainda.")
    else:
        df_anotacoes["data"] = pd.to_datetime(df_anotacoes["data"], errors="coerce").dt.date
        df_anotacoes_exibir = df_anotacoes.copy()
        ids_anotacoes = [int(v) for v in df_anotacoes_exibir["id"].tolist()]
        caminhos_anexos = {}
        if ids_anotacoes:
            placeholders = ",".join(["?"] * len(ids_anotacoes))
            with conn() as c:
                rows_anexos = c.execute(
                    f"""SELECT anotacao_id, caminho_arquivo
                        FROM anotacoes_anexos
                        WHERE anotacao_id IN ({placeholders})""",
                    ids_anotacoes,
                ).fetchall()
            for anexo in rows_anexos:
                caminhos_anexos.setdefault(int(anexo["anotacao_id"]), set()).add(str(anexo["caminho_arquivo"] or "").strip())

        def rotulo_documento_anotacao(row):
            caminhos = set(caminhos_anexos.get(int(row["id"]), set()))
            caminho_legacy = str(row.get("documento_arquivo") or "").strip()
            if caminho_legacy:
                caminhos.add(caminho_legacy)
            total = len([caminho for caminho in caminhos if caminho])
            return f"{total} anexo(s)" if total else "Não"

        df_anotacoes_exibir["documento"] = df_anotacoes_exibir.apply(rotulo_documento_anotacao, axis=1)
        st.dataframe(
            df_anotacoes_exibir[["id", "data", "descricao", "documento"]],
            use_container_width=True,
            hide_index=True,
            column_config={
                "id": st.column_config.NumberColumn("ID", format="%d"),
                "data": st.column_config.DateColumn("Data", format="DD/MM/YYYY"),
                "descricao": st.column_config.TextColumn("Descrição"),
                "documento": st.column_config.TextColumn("Documento"),
            },
        )

        st.markdown("##### Alterar ou Excluir Anotação")
        mapa_anotacoes = {
            (
                f"ID: {int(r['id'])} | "
                f"Data: {r['data'].strftime('%d/%m/%Y') if pd.notna(r['data']) else '-'} | "
                f"Descrição: {str(r['descricao'])[:80]}"
            ): int(r["id"])
            for _, r in df_anotacoes.sort_values(by="id", ascending=False).iterrows()
        }

        anotacao_sel_label = st.selectbox(
            "Selecione a anotação",
            options=list(mapa_anotacoes.keys()),
            index=None,
            placeholder="Escolha uma anotação para editar ou excluir",
            key="anot_sel_alteracao",
        )

        if "anot_editando_id" not in st.session_state:
            st.session_state.anot_editando_id = None
        if "anot_excluir_id" not in st.session_state:
            st.session_state.anot_excluir_id = None

        if anotacao_sel_label:
            id_sel = mapa_anotacoes[anotacao_sel_label]
            a1, a2 = st.columns(2)
            if a1.button("✏️ EDITAR", key="btn_anot_editar", use_container_width=True):
                st.session_state.anot_editando_id = id_sel
                st.session_state.anot_excluir_id = None
            if a2.button("🗑️ EXCLUIR", key="btn_anot_excluir", type="primary", use_container_width=True):
                st.session_state.anot_excluir_id = id_sel

        if st.session_state.anot_excluir_id is not None:
            st.warning(f"Confirma a exclusão da anotação ID {int(st.session_state.anot_excluir_id)}?")
            e1, e2 = st.columns(2)
            if e1.button("✅ Confirmar exclusão", key="btn_anot_confirmar_exclusao", type="primary", use_container_width=True):
                caminhos_excluir = []
                with conn() as c:
                    row_doc = c.execute(
                        "SELECT documento_arquivo FROM anotacoes WHERE id=?",
                        (int(st.session_state.anot_excluir_id),),
                    ).fetchone()
                    caminho_documento_legacy = str(row_doc["documento_arquivo"] or "").strip() if row_doc else None
                    if caminho_documento_legacy:
                        caminhos_excluir.append(caminho_documento_legacy)
                    rows_anexos_excluir = c.execute(
                        "SELECT caminho_arquivo FROM anotacoes_anexos WHERE anotacao_id=?",
                        (int(st.session_state.anot_excluir_id),),
                    ).fetchall()
                    caminhos_excluir.extend(
                        str(r["caminho_arquivo"] or "").strip()
                        for r in rows_anexos_excluir
                        if str(r["caminho_arquivo"] or "").strip()
                    )
                    c.execute("DELETE FROM anotacoes_anexos WHERE anotacao_id=?", (int(st.session_state.anot_excluir_id),))
                    c.execute("DELETE FROM anotacoes WHERE id=?", (int(st.session_state.anot_excluir_id),))
                for caminho_documento_excluir in set(caminhos_excluir):
                    try:
                        Path(caminho_documento_excluir).unlink(missing_ok=True)
                    except Exception:
                        pass
                st.session_state.anot_excluir_id = None
                st.session_state.anot_editando_id = None
                st.success("Anotação excluída com sucesso.")
                st.rerun()
            if e2.button("❌ Cancelar exclusão", key="btn_anot_cancelar_exclusao", use_container_width=True):
                st.session_state.anot_excluir_id = None
                st.rerun()

        if st.session_state.anot_editando_id is not None:
            id_edit = int(st.session_state.anot_editando_id)
            row_edit = df_anotacoes[df_anotacoes["id"] == id_edit]

            if row_edit.empty:
                st.session_state.anot_editando_id = None
            else:
                reg = row_edit.iloc[0]
                data_edit_atual = reg["data"] if pd.notna(reg["data"]) else date.today()
                desc_edit_atual = str(reg["descricao"] or "")
                documento_nome_atual = str(reg.get("documento_nome") or "").strip()
                documento_arquivo_atual = str(reg.get("documento_arquivo") or "").strip()
                with conn() as c:
                    anexos_anotacao = c.execute(
                        """SELECT id, nome_arquivo, caminho_arquivo
                           FROM anotacoes_anexos
                           WHERE anotacao_id=?
                           ORDER BY id ASC""",
                        (id_edit,),
                    ).fetchall()
                anexos_exibir = [
                    {
                        "id": int(anexo["id"]),
                        "nome_arquivo": str(anexo["nome_arquivo"] or "").strip(),
                        "caminho_arquivo": str(anexo["caminho_arquivo"] or "").strip(),
                    }
                    for anexo in anexos_anotacao
                ]
                if documento_arquivo_atual and documento_arquivo_atual not in {a["caminho_arquivo"] for a in anexos_exibir}:
                    anexos_exibir.insert(
                        0,
                        {
                            "id": 0,
                            "nome_arquivo": documento_nome_atual,
                            "caminho_arquivo": documento_arquivo_atual,
                        },
                    )

                if anexos_exibir:
                    st.markdown("##### Anexos da anotação")
                    for anexo in anexos_exibir:
                        caminho_anexo = anexo["caminho_arquivo"]
                        if not caminho_anexo:
                            continue
                        path_documento_atual = Path(caminho_anexo)
                        nome_download = anexo["nome_arquivo"] or path_documento_atual.name
                        if path_documento_atual.exists():
                            with path_documento_atual.open("rb") as f_doc_atual:
                                st.download_button(
                                    f"📎 Baixar: {nome_download}",
                                    data=f_doc_atual.read(),
                                    file_name=nome_download,
                                    mime="application/octet-stream",
                                    key=f"btn_anot_download_doc_{id_edit}_{anexo['id']}_{path_documento_atual.name}",
                                )
                        else:
                            st.info(f"Documento registrado, mas não encontrado no disco: {nome_download}")

                with st.form("form_anotacoes_editar"):
                    ed1, ed2 = st.columns([1, 3])
                    nova_data_anotacao = ed1.date_input(
                        "Data (Editar)",
                        value=data_edit_atual,
                        format="DD/MM/YYYY",
                        key=f"anot_edit_data_{id_edit}",
                    )
                    nova_descricao_anotacao = ed2.text_area(
                        "Descrição (Editar)",
                        value=desc_edit_atual,
                        key=f"anot_edit_desc_{id_edit}",
                    )
                    novo_documento_anotacao = st.file_uploader(
                        "Adicionar Documento(s)",
                        type=["pdf", "png", "jpg", "jpeg", "webp", "doc", "docx", "xls", "xlsx", "txt", "csv"],
                        accept_multiple_files=True,
                        key=f"anot_edit_documento_{id_edit}",
                    )
                    b1, b2 = st.columns(2)
                    gravar_edit = b1.form_submit_button("💾 Gravar", use_container_width=True, key=f"btn_anot_gravar_edit_{id_edit}")
                    cancelar_edit = b2.form_submit_button("❌ CANCELAR", use_container_width=True)

                    if cancelar_edit:
                        st.session_state.anot_editando_id = None
                        st.rerun()

                    if gravar_edit:
                        if not str(nova_descricao_anotacao or "").strip():
                            st.warning("Informe a descrição para gravar.")
                        else:
                            novos_documentos_salvos = salvar_documentos_anotacao(novo_documento_anotacao)
                            documento_principal = novos_documentos_salvos[0] if novos_documentos_salvos else None
                            documento_nome_final = documento_nome_atual or (documento_principal["nome_arquivo"] if documento_principal else None)
                            documento_arquivo_final = documento_arquivo_atual or (documento_principal["caminho_arquivo"] if documento_principal else None)
                            with conn() as c:
                                c.execute(
                                    "UPDATE anotacoes SET data=?, descricao=?, documento_nome=?, documento_arquivo=? WHERE id=?",
                                    (
                                        nova_data_anotacao.isoformat(),
                                        nova_descricao_anotacao.strip(),
                                        documento_nome_final or None,
                                        documento_arquivo_final or None,
                                        id_edit,
                                    ),
                                )
                                for documento_salvo in novos_documentos_salvos:
                                    c.execute(
                                        """INSERT INTO anotacoes_anexos (anotacao_id, nome_arquivo, caminho_arquivo, data_inclusao)
                                           VALUES (?, ?, ?, ?)""",
                                        (
                                            id_edit,
                                            documento_salvo["nome_arquivo"],
                                            documento_salvo["caminho_arquivo"],
                                            datetime.now().isoformat(),
                                        ),
                                    )
                            st.session_state.anot_editando_id = None
                            alerta_gravado()
                            st.rerun()

# =========================
# ABA CÁLCULO RÁPIDO
# =========================
with aba_calc:
    st.markdown(
        """
        <style>
        .cmp-header {
            border-radius: 12px; padding: 12px 18px; text-align: center;
            margin-bottom: 10px; font-weight: 800; font-size: 1rem; letter-spacing: 0.2px;
        }
        .cmp-km-header   { background: linear-gradient(120deg,#1b6ca8,#00a6a6); color:#fff; }
        .cmp-ton-header  { background: linear-gradient(120deg,#0b3c5d,#1b6ca8); color:#fff; }
        .cmp-row {
            display: flex; justify-content: space-between; align-items: center;
            padding: 7px 14px; border-bottom: 1px solid #eef4fb; font-size: 0.88rem;
        }
        .cmp-row:last-child { border-bottom: none; }
        .cmp-row-label { color: #627d98; font-weight: 600; }
        .cmp-row-value { color: #102a43; font-weight: 700; }
        .cmp-card {
            background: #ffffff; border: 1px solid #cfdce9; border-radius: 14px;
            overflow: hidden; box-shadow: 0 3px 12px rgba(11,60,93,0.08);
        }
        .cmp-liquido-km {
            background: linear-gradient(120deg,#1b6ca8,#00a6a6);
            padding: 16px; text-align: center;
        }
        .cmp-liquido-ton {
            background: linear-gradient(120deg,#0b3c5d,#1b6ca8);
            padding: 16px; text-align: center;
        }
        .cmp-liquido-label { font-size:0.70rem; font-weight:700; text-transform:uppercase;
            letter-spacing:0.8px; color:rgba(255,255,255,0.78); margin-bottom:5px; }
        .cmp-liquido-value { font-size:1.9rem; font-weight:900; color:#fff; line-height:1.15; }
        .cmp-liquido-value.neg { color:#fca5a5; }
        .cmp-liquido-sub { font-size:0.75rem; color:rgba(255,255,255,0.72); margin-top:4px; }
        .cmp-shared-title {
            font-size:0.70rem; font-weight:700; text-transform:uppercase;
            letter-spacing:0.8px; color:#627d98; padding-bottom:4px;
            border-bottom: 2px solid #cfdce9; margin-bottom:8px;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("#### 🧮 Cálculo Rápido — Comparativo KM × Tonelada")
    st.caption("Preencha os dados comuns e os específicos de cada modalidade. Os resultados atualizam em tempo real.")

    # ── DADOS COMUNS ────────────────────────────────────────────────────────
    st.markdown('<div class="cmp-shared-title">📦 Dados Comuns da Viagem</div>', unsafe_allow_html=True)

    def _cr_num(txt, default=0.0):
        txt = str(txt or "").strip().replace(" ", "").replace(",", ".")
        try:
            v = float(txt)
        except ValueError:
            return default
        return v if v >= 0 else default

    sh1, sh2, sh3, sh4, sh5, sh6 = st.columns(6)
    cr_km = _cr_num(
        sh1.text_input("Distância (KM)", value="0", key="cr_km_shared"),
    )
    cr_diesel_vl = _cr_num(
        sh2.text_input("Valor Litro Diesel (R$)", value=f"{float(v_diesel_sug or 0.0):.2f}", key="cr_diesel_vl2"),
    )
    cr_cons_diesel = _cr_num(
        sh3.text_input("Consumo Diesel (km/L)", value=f"{float(v_cons_sug or 2.5):.2f}", key="cr_cons_diesel2"),
    )
    cr_arla_vl = _cr_num(
        sh4.text_input("Valor Litro Arla (R$)", value=f"{float(v_arla_sug or 0.0):.2f}", key="cr_arla_vl2"),
    )
    cr_cons_arla = _cr_num(
        sh5.text_input("Consumo Arla (km/L)", value=f"{float(v_cons_arla_sug or 0.0):.2f}", key="cr_cons_arla2"),
    )
    cr_pedagio = _cr_num(
        sh6.text_input("Total Pedágio (R$)", value="0.00", key="cr_pedagio2"),
    )

    # ── CÁLCULOS COMPARTILHADOS ─────────────────────────────────────────────
    lit_diesel = (cr_km / cr_cons_diesel) if cr_cons_diesel > 0 else 0.0
    custo_diesel = lit_diesel * cr_diesel_vl
    lit_arla = (cr_km / cr_cons_arla) if cr_cons_arla > 0 else 0.0
    custo_arla = lit_arla * cr_arla_vl
    custos_comuns = custo_diesel + custo_arla + cr_pedagio

    st.markdown("---")

    # ── INPUTS ESPECÍFICOS ───────────────────────────────────────────────────
    col_km_in, col_sep, col_ton_in = st.columns([5, 1, 5])

    with col_km_in:
        st.markdown('<div class="cmp-shared-title">📏 Cobrança por KM</div>', unsafe_allow_html=True)
        ci1, ci2 = st.columns(2)
        cr_val_km = _cr_num(
            ci1.text_input("Valor por KM (R$/km)", value="0.0000", key="cr_val_km2"),
        )

    with col_sep:
        st.markdown("<div style='text-align:center;padding-top:32px;font-size:1.4rem;color:#cfdce9;font-weight:900;'>VS</div>", unsafe_allow_html=True)

    with col_ton_in:
        st.markdown('<div class="cmp-shared-title">🏋️ Cobrança por Tonelada</div>', unsafe_allow_html=True)
        ci3, ci4 = st.columns(2)
        cr_val_ton = _cr_num(
            ci3.text_input("Valor por Tonelada (R$/ton)", value="0.00", key="cr_val_ton2"),
        )
        cr_qtde_ton = _cr_num(
            ci4.text_input("Qtde Toneladas (ton)", value="0.000", key="cr_qtde_ton2"),
        )

    # ── CÁLCULOS POR MODALIDADE ──────────────────────────────────────────────
    frete_bruto_km  = cr_val_km  * cr_km
    frete_bruto_ton = cr_val_ton * cr_qtde_ton

    liquido_km  = frete_bruto_km  - custos_comuns
    liquido_ton = frete_bruto_ton - custos_comuns

    margem_km  = (liquido_km  / frete_bruto_km  * 100) if frete_bruto_km  > 0 else 0.0
    margem_ton = (liquido_ton / frete_bruto_ton * 100) if frete_bruto_ton > 0 else 0.0

    # ── CARDS COMPARATIVOS ───────────────────────────────────────────────────
    st.markdown("<br>", unsafe_allow_html=True)
    card_km, sp2, card_ton = st.columns([5, 1, 5])

    def _cmp_row(label, value, sub=""):
        sub_html = f'<span style="color:#aac0d4;font-size:0.76rem;"> ({sub})</span>' if sub else ""
        return (
            f'<div class="cmp-row">'
            f'  <span class="cmp-row-label">{label}</span>'
            f'  <span class="cmp-row-value">{value}{sub_html}</span>'
            f'</div>'
        )

    def _build_card(frete_bruto, liquido, margem, header_class, liquido_class_name, modalidade_txt):
        neg_class = " neg" if liquido < 0 else ""
        margem_txt = f"{margem:.1f}% de margem" if frete_bruto > 0 else "—"
        liquido_por_km = liquido / cr_km if cr_km > 0 else 0.0
        liq_km_txt = f"{brl(liquido_por_km)}/km" if cr_km > 0 else "—"
        rows = (
            _cmp_row("Frete Bruto",    brl(frete_bruto))
            + _cmp_row("Custo Diesel", brl(custo_diesel), f"{lit_diesel:.1f} L")
            + _cmp_row("Custo Arla",   brl(custo_arla),   f"{lit_arla:.1f} L")
            + _cmp_row("Pedágio",      brl(cr_pedagio))
            + _cmp_row("Total Custos", brl(custos_comuns))
        )
        return f"""
        <div class="cmp-card">
            <div class="cmp-header {header_class}">{modalidade_txt}</div>
            {rows}
            <div class="{liquido_class_name}">
                <div class="cmp-liquido-label">💰 Frete Líquido</div>
                <div class="cmp-liquido-value{neg_class}">{brl(liquido)}</div>
                <div class="cmp-liquido-sub">{margem_txt} &nbsp;·&nbsp; <strong>{liq_km_txt}</strong> líquido/km</div>
            </div>
        </div>
        """

    with card_km:
        st.markdown(
            _build_card(frete_bruto_km, liquido_km, margem_km,
                        "cmp-km-header", "cmp-liquido-km",
                        f"📏 Por KM &nbsp;·&nbsp; {format_br(cr_km, casas_decimais=0)} km × {brl(cr_val_km)}/km"),
            unsafe_allow_html=True,
        )

    with sp2:
        st.markdown(
            "<div style='text-align:center;padding-top:80px;font-size:1.2rem;"
            "color:#cfdce9;font-weight:900;'>VS</div>",
            unsafe_allow_html=True,
        )

    with card_ton:
        st.markdown(
            _build_card(frete_bruto_ton, liquido_ton, margem_ton,
                        "cmp-ton-header", "cmp-liquido-ton",
                        f"🏋️ Por Tonelada &nbsp;·&nbsp; {format_br(cr_qtde_ton, casas_decimais=3)} ton × {brl(cr_val_ton)}/ton"),
            unsafe_allow_html=True,
        )

