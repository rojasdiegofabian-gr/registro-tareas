#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Registro Diario de Tareas — Streamlit App
Ejecutar con:  streamlit run registro_tareas.py
"""

import streamlit as st
import psycopg2
from psycopg2.extras import RealDictCursor
import hashlib
import os
import io
from datetime import datetime, date, timedelta
from contextlib import contextmanager

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from fpdf import FPDF

# ─────────────────────────────────────────────────
# Base de datos (PostgreSQL via Supabase)
# ─────────────────────────────────────────────────
DATABASE_URL = os.environ.get("DATABASE_URL", "")

DB_HOST = "aws-1-us-west-2.pooler.supabase.com"
DB_PORT = "5432"
DB_NAME = "postgres"
DB_USER = "postgres.hfuafmdpvfginosodqxc"
DB_PASS = "Registo2026"


@contextmanager
def get_db():
    if DATABASE_URL:
        conn = psycopg2.connect(DATABASE_URL)
    else:
        conn = psycopg2.connect(
            host=DB_HOST, port=DB_PORT, dbname=DB_NAME,
            user=DB_USER, password=DB_PASS,
            sslmode="require"
        )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


from decimal import Decimal


def db_execute(conn, query, params=None):
    """Helper: ejecuta query y retorna cursor con wrapper para convertir Decimal."""
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(query, params or ())
    return CursorWrapper(cur)


class CursorWrapper:
    """Wrapper de cursor que convierte Decimal a float automáticamente."""
    def __init__(self, cursor):
        self._cursor = cursor

    @staticmethod
    def _clean(val):
        if isinstance(val, Decimal):
            return float(val)
        return val

    @staticmethod
    def _clean_row(row):
        if row is None:
            return None
        if isinstance(row, dict):
            return {k: CursorWrapper._clean(v) for k, v in row.items()}
        return row

    def fetchone(self):
        return self._clean_row(self._cursor.fetchone())

    def fetchall(self):
        return [self._clean_row(r) for r in self._cursor.fetchall()]

    @property
    def lastrowid(self):
        return self._cursor.lastrowid

    @property
    def description(self):
        return self._cursor.description


def init_db():
    with get_db() as db:
        cur = db.cursor(cursor_factory=RealDictCursor)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS usuarios (
                id SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                rol TEXT NOT NULL DEFAULT 'usuario',
                activo INTEGER NOT NULL DEFAULT 1,
                creado_en TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS tareas (
                id SERIAL PRIMARY KEY,
                nombre TEXT UNIQUE NOT NULL,
                activa INTEGER NOT NULL DEFAULT 1
            );
            CREATE TABLE IF NOT EXISTS registros (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                fecha TEXT NOT NULL,
                turno TEXT NOT NULL DEFAULT 'Mañana',
                tarea_id INTEGER NOT NULL REFERENCES tareas(id),
                cantidad INTEGER NOT NULL,
                observacion TEXT DEFAULT '',
                creado_en TIMESTAMP DEFAULT NOW(),
                actualizado_en TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS metas (
                id SERIAL PRIMARY KEY,
                tarea_id INTEGER NOT NULL REFERENCES tareas(id),
                cantidad_objetivo INTEGER NOT NULL,
                periodo TEXT NOT NULL DEFAULT 'diario'
            );
            CREATE TABLE IF NOT EXISTS configuracion (
                clave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS audit_log (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER,
                accion TEXT NOT NULL,
                detalle TEXT DEFAULT '',
                fecha_hora TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS asignaciones (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                descripcion TEXT NOT NULL,
                fecha_limite TEXT,
                prioridad TEXT NOT NULL DEFAULT 'normal',
                estado TEXT NOT NULL DEFAULT 'pendiente',
                creado_en TIMESTAMP DEFAULT NOW(),
                completado_en TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS evaluaciones (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                fecha TEXT NOT NULL,
                categoria TEXT NOT NULL,
                puntaje INTEGER NOT NULL DEFAULT 3,
                observacion TEXT DEFAULT '',
                registrado_por INTEGER NOT NULL REFERENCES usuarios(id),
                creado_en TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS flujos (
                id SERIAL PRIMARY KEY,
                nombre TEXT NOT NULL,
                descripcion TEXT DEFAULT '',
                estado TEXT NOT NULL DEFAULT 'en_curso',
                creado_por INTEGER NOT NULL REFERENCES usuarios(id),
                creado_en TIMESTAMP DEFAULT NOW(),
                completado_en TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS flujo_pasos (
                id SERIAL PRIMARY KEY,
                flujo_id INTEGER NOT NULL REFERENCES flujos(id),
                numero_paso INTEGER NOT NULL,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                descripcion TEXT NOT NULL,
                estado TEXT NOT NULL DEFAULT 'bloqueado',
                creado_en TIMESTAMP DEFAULT NOW(),
                completado_en TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS avisos (
                id SERIAL PRIMARY KEY,
                titulo TEXT NOT NULL,
                mensaje TEXT NOT NULL,
                prioridad TEXT NOT NULL DEFAULT 'normal',
                activo INTEGER NOT NULL DEFAULT 1,
                creado_por INTEGER NOT NULL REFERENCES usuarios(id),
                creado_en TIMESTAMP DEFAULT NOW()
            );
            CREATE TABLE IF NOT EXISTS mensajes_dia (
                id SERIAL PRIMARY KEY,
                usuario_id INTEGER NOT NULL REFERENCES usuarios(id),
                fecha TEXT NOT NULL,
                mensaje TEXT NOT NULL,
                creado_en TIMESTAMP DEFAULT NOW()
            );
        """)
        db.commit()
        # Config por defecto
        cur.execute("SELECT valor FROM configuracion WHERE clave='dias_editables'")
        if not cur.fetchone():
            cur.execute("INSERT INTO configuracion (clave, valor) VALUES ('dias_editables', '0')")
        cur.execute("SELECT valor FROM configuracion WHERE clave='dias_dashboard'")
        if not cur.fetchone():
            cur.execute("INSERT INTO configuracion (clave, valor) VALUES ('dias_dashboard', '14')")
        # Admin por defecto
        cur.execute("SELECT id FROM usuarios WHERE rol='admin'")
        if not cur.fetchone():
            h = hashlib.sha256("admin123".encode()).hexdigest()
            cur.execute("INSERT INTO usuarios (nombre, password_hash, rol) VALUES (%s, %s, 'admin')", ("admin", h))
        db.commit()


def hash_pw(pw):
    return hashlib.sha256(pw.encode()).hexdigest()


def log_audit(db, usuario_id, accion, detalle=""):
    db_execute(db, "INSERT INTO audit_log (usuario_id, accion, detalle) VALUES (%s, %s, %s)",
               (usuario_id, accion, detalle))


def pdf_safe(text):
    """Reemplaza caracteres no soportados por Helvetica en fpdf2."""
    r = {"—": "-", "–": "-", "\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"',
         "\u2026": "...", "\u2022": "-", "\u2265": ">=", "\u2264": "<="}
    for old, new in r.items():
        text = text.replace(old, new)
    # Eliminar emojis y caracteres fuera de latin-1
    return text.encode("latin-1", errors="replace").decode("latin-1")


def fmt_ts(val):
    """Formatea timestamp de PostgreSQL a string legible."""
    if val is None:
        return ""
    if isinstance(val, str):
        return val[:16]
    try:
        return val.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(val)[:16]



def fix_df_types(df):
    """Convierte columnas numéricas que PostgreSQL puede devolver como string."""
    for col in ["cantidad", "puntaje", "cantidad_objetivo", "total", "id"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0).astype(int)
    return df


# ─────────────────────────────────────────────────
# Inicialización
# ─────────────────────────────────────────────────
init_db()

st.set_page_config(page_title="Registro de Tareas", page_icon="📋", layout="wide")

# CSS personalizado
st.markdown("""
<style>
    /* ── Layout general ── */
    .block-container { max-width: 1100px; padding-top: 1.5rem; }
    
    /* ── Sidebar moderna ── */
    [data-testid="stSidebar"] {
        background: linear-gradient(180deg, #0f172a 0%, #1e293b 100%);
    }
    [data-testid="stSidebar"] * {
        color: #e2e8f0 !important;
    }
    [data-testid="stSidebar"] .stButton > button {
        background: rgba(255,255,255,0.08);
        border: 1px solid rgba(255,255,255,0.15);
        color: #e2e8f0 !important;
        border-radius: 8px;
        transition: all 0.2s;
    }
    [data-testid="stSidebar"] .stButton > button:hover {
        background: rgba(255,255,255,0.15);
        border-color: rgba(255,255,255,0.3);
    }
    
    /* ── Métricas con estilo card ── */
    div[data-testid="stMetric"] {
        background: linear-gradient(135deg, #f8fafc 0%, #e2e8f0 100%);
        border-radius: 14px;
        padding: 18px 22px;
        border: none;
        border-left: 5px solid #3b82f6;
        box-shadow: 0 1px 3px rgba(0,0,0,0.06);
    }
    div[data-testid="stMetric"] label {
        color: #64748b !important;
        font-size: 0.8rem !important;
        font-weight: 600 !important;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    div[data-testid="stMetric"] [data-testid="stMetricValue"] {
        color: #0f172a !important;
        font-weight: 800 !important;
    }
    
    /* ── Tabs modernas ── */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px;
        background: #f1f5f9;
        border-radius: 12px;
        padding: 4px;
        border-bottom: none !important;
    }
    .stTabs [data-baseweb="tab"] {
        padding: 10px 18px;
        font-weight: 600;
        border-radius: 8px;
        font-size: 0.9rem;
        color: #64748b;
    }
    .stTabs [aria-selected="true"] {
        background: white !important;
        color: #1e40af !important;
        box-shadow: 0 1px 3px rgba(0,0,0,0.08);
    }
    .stTabs [data-baseweb="tab-border"] { display: none; }
    .stTabs [data-baseweb="tab-highlight"] { display: none; }
    
    /* ── Botones ── */
    .stButton > button[kind="primary"],
    .stFormSubmitButton > button[kind="primary"],
    button[kind="primary"] {
        background: linear-gradient(135deg, #2563eb 0%, #1d4ed8 100%) !important;
        border: none !important;
        border-radius: 10px !important;
        font-weight: 700 !important;
        letter-spacing: 0.3px;
        padding: 0.55rem 1.5rem !important;
        transition: all 0.2s !important;
        box-shadow: 0 2px 6px rgba(37,99,235,0.25) !important;
    }
    .stButton > button[kind="primary"]:hover,
    .stFormSubmitButton > button[kind="primary"]:hover,
    button[kind="primary"]:hover {
        box-shadow: 0 4px 12px rgba(37,99,235,0.35) !important;
        transform: translateY(-1px);
    }
    
    .stButton > button[kind="secondary"],
    button[kind="secondary"] {
        border-radius: 10px !important;
        font-weight: 600 !important;
        border: 1px solid #e2e8f0 !important;
    }
    
    /* ── Inputs ── */
    .stTextInput > div > div > input,
    .stNumberInput > div > div > input,
    .stDateInput > div > div > input,
    .stSelectbox > div > div {
        border-radius: 10px !important;
        border-color: #e2e8f0 !important;
    }
    .stTextInput > div > div > input:focus,
    .stNumberInput > div > div > input:focus {
        border-color: #3b82f6 !important;
        box-shadow: 0 0 0 3px rgba(59,130,246,0.15) !important;
    }
    
    /* ── Expanders ── */
    .streamlit-expanderHeader {
        border-radius: 10px !important;
        font-weight: 600;
    }
    
    /* ── DataFrames ── */
    .stDataFrame { border-radius: 12px; overflow: hidden; }
    
    /* ── Dividers ── */
    hr { border-color: #e2e8f0 !important; }
    
    /* ── Alertas ── */
    .stAlert { border-radius: 12px !important; }
    
    /* ── Progress bars ── */
    .stProgress > div > div { border-radius: 10px; }
    
    /* ── Headers ── */
    h1, h2, h3 {
        color: #0f172a !important;
        letter-spacing: -0.3px;
    }
    h4 {
        color: #334155 !important;
        font-size: 1.1rem !important;
        margin-top: 0.5rem !important;
    }
    
    /* ── Download button ── */
    .stDownloadButton > button {
        border-radius: 10px !important;
        background: linear-gradient(135deg, #059669, #047857) !important;
        color: white !important;
        border: none !important;
        font-weight: 700 !important;
        box-shadow: 0 2px 6px rgba(5,150,105,0.25) !important;
    }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────
# Estado de sesión
# ─────────────────────────────────────────────────
if "user" not in st.session_state:
    st.session_state.user = None


# ─────────────────────────────────────────────────
# Login
# ─────────────────────────────────────────────────
def pantalla_login():
    st.markdown("""
    <div style="text-align:center; padding: 3rem 0 1rem;">
        <div style="display:inline-block; background:linear-gradient(135deg,#2563eb,#1d4ed8); 
                    width:70px; height:70px; border-radius:18px; line-height:70px; font-size:32px;
                    box-shadow: 0 8px 24px rgba(37,99,235,0.3); margin-bottom:1rem;">📋</div>
        <h1 style="font-size:2rem; margin:0; letter-spacing:-0.5px;">Registro de Tareas</h1>
        <p style="color:#64748b; font-size:1rem; margin-top:0.3rem;">Ingresá tus credenciales para continuar</p>
    </div>
    """, unsafe_allow_html=True)

    col1, col2, col3 = st.columns([1, 1.2, 1])
    with col2:
        with st.form("login_form"):
            usuario = st.text_input("👤 Usuario")
            password = st.text_input("🔒 Contraseña", type="password")
            st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
            submitted = st.form_submit_button("Ingresar", use_container_width=True, type="primary")

        if submitted:
            if not usuario or not password:
                st.error("Completá ambos campos.")
                return
            with get_db() as db:
                row = db_execute(db, 
                    "SELECT id, nombre, password_hash, rol, activo FROM usuarios WHERE nombre = %s",
                    (usuario,)
                ).fetchone()
            if not row:
                st.error("Usuario o contraseña incorrectos.")
            elif not row["activo"]:
                st.error("Tu cuenta está desactivada. Contactá al administrador.")
            elif row["password_hash"] != hash_pw(password):
                st.error("Usuario o contraseña incorrectos.")
            else:
                st.session_state.user = {"id": row["id"], "nombre": row["nombre"], "rol": row["rol"]}
                with get_db() as db:
                    log_audit(db, row["id"], "login", f"Ingresó al sistema")
                st.rerun()


# ─────────────────────────────────────────────────
# Helpers comunes
# ─────────────────────────────────────────────────
def obtener_tareas_activas():
    with get_db() as db:
        rows = db_execute(db, "SELECT id, nombre FROM tareas WHERE activa=1 ORDER BY nombre").fetchall()
    return {r["nombre"]: r["id"] for r in rows}


def obtener_config(clave):
    with get_db() as db:
        row = db_execute(db, "SELECT valor FROM configuracion WHERE clave=%s", (clave,)).fetchone()
    return row["valor"] if row else None


TURNOS = ["Mañana", "Tarde", "Noche"]


# ─────────────────────────────────────────────────
# Panel del USUARIO
# ─────────────────────────────────────────────────
def panel_usuario():
    user = st.session_state.user

    st.markdown(f"""
    <div style="margin-bottom:1rem;">
        <h2 style="margin:0; font-size:1.6rem;">Hola, {user['nombre']} 👋</h2>
        <p style="color:#64748b; margin:0.2rem 0 0;">Registrá y consultá tus tareas del día</p>
    </div>
    """, unsafe_allow_html=True)

    # ── Avisos activos ──
    with get_db() as db:
        avisos = db_execute(db, """
            SELECT titulo, mensaje, prioridad, creado_en FROM avisos
            WHERE activo = 1 ORDER BY creado_en DESC LIMIT 5
        """).fetchall()
    if avisos:
        for aviso in avisos:
            if aviso["prioridad"] == "urgente":
                st.error(f"🚨 **{aviso['titulo']}** — {aviso['mensaje']}")
            elif aviso["prioridad"] == "importante":
                st.warning(f"⚠️ **{aviso['titulo']}** — {aviso['mensaje']}")
            else:
                st.info(f"📢 **{aviso['titulo']}** — {aviso['mensaje']}")

    # ── Tareas asignadas pendientes ──
    with get_db() as db:
        cant_pendientes = db_execute(db, 
            "SELECT COUNT(*) as c FROM asignaciones WHERE usuario_id=%s AND estado='pendiente'",
            (user["id"],)).fetchone()["c"]
        cant_flujos = db_execute(db, """
            SELECT COUNT(*) as c FROM flujo_pasos fp
            JOIN flujos f ON fp.flujo_id = f.id
            WHERE fp.usuario_id=%s AND fp.estado='pendiente' AND f.estado='en_curso'
        """, (user["id"],)).fetchone()["c"]
    if cant_pendientes > 0 or cant_flujos > 0:
        total_pend = cant_pendientes + cant_flujos
        st.warning(f"📌 Tenés **{total_pend}** tarea{'s' if total_pend != 1 else ''} pendiente{'s' if total_pend != 1 else ''}. Revisá la pestaña **Tareas asignadas**.")

    tab_cargar, tab_mis_registros, tab_estadisticas, tab_asignadas = st.tabs([
        "📝 Cargar tarea", "📄 Mis registros", "📊 Mis estadísticas", "📌 Tareas asignadas"
    ])

    # ── Cargar tarea ──
    with tab_cargar:
        tareas = obtener_tareas_activas()
        if not tareas:
            st.warning("No hay tareas configuradas todavía. Pedile al administrador que las cargue.")
            return

        dias_edit = int(obtener_config("dias_editables") or 0)
        fecha_min = date.today() - timedelta(days=dias_edit)

        with st.form("form_carga", clear_on_submit=True):
            c1, c2, c3 = st.columns([1, 1, 1])
            with c1:
                fecha = st.date_input("Fecha", value=date.today(), min_value=fecha_min, max_value=date.today())
            with c2:
                turno = st.selectbox("Turno", TURNOS)
            with c3:
                tarea_sel = st.selectbox("Tarea", list(tareas.keys()))

            c4, c5 = st.columns([1, 2])
            with c4:
                cantidad = st.number_input("Cantidad", min_value=1, step=1, value=1)
            with c5:
                observacion = st.text_input("Observación (opcional)")

            enviado = st.form_submit_button("Agregar registro", type="primary", use_container_width=True)

        if enviado:
            tarea_id = tareas[tarea_sel]
            with get_db() as db:
                db_execute(db, 
                    "INSERT INTO registros (usuario_id, fecha, turno, tarea_id, cantidad, observacion) VALUES (%s,%s,%s,%s,%s,%s)",
                    (user["id"], fecha.isoformat(), turno, tarea_id, cantidad, observacion)
                )
                log_audit(db, user["id"], "carga",
                          f"Tarea: {tarea_sel}, Cantidad: {cantidad}, Fecha: {fecha.isoformat()}")
            st.success("✓ Registro guardado")
            st.rerun()

        # ── Mensaje del día ──
        st.divider()
        st.markdown("**💬 Mensaje del día** (opcional — se lo ve el encargado)")
        st.caption("Dejá un comentario sobre cómo fue tu día: problemas, novedades, lo que necesites comunicar.")

        with get_db() as db:
            msg_hoy = db_execute(db, "SELECT id, mensaje FROM mensajes_dia WHERE usuario_id=%s AND fecha=%s",
                                 (user["id"], date.today().isoformat())).fetchone()

        msg_actual = msg_hoy["mensaje"] if msg_hoy else ""
        nuevo_msg = st.text_area("", value=msg_actual, key="msg_dia", height=80,
                                  placeholder="Ej: Hoy hubo corte de luz 2 horas, se demoró todo...")
        if st.button("Guardar mensaje", key="btn_msg_dia"):
            with get_db() as db:
                if msg_hoy:
                    db_execute(db, "UPDATE mensajes_dia SET mensaje=%s WHERE id=%s", (nuevo_msg.strip(), msg_hoy["id"]))
                else:
                    db_execute(db, "INSERT INTO mensajes_dia (usuario_id, fecha, mensaje) VALUES (%s,%s,%s)",
                               (user["id"], date.today().isoformat(), nuevo_msg.strip()))
                db.commit()
            st.success("✓ Mensaje guardado")

    # ── Mis registros (con edición) ──
    with tab_mis_registros:
        dias_edit = int(obtener_config("dias_editables") or 0)
        fecha_min = date.today() - timedelta(days=dias_edit)

        fecha_ver = st.date_input("Ver registros de:", value=date.today(), key="ver_fecha")

        with get_db() as db:
            rows = db_execute(db, """
                SELECT r.id, r.fecha, r.turno, t.nombre as tarea, r.cantidad, r.observacion, r.creado_en
                FROM registros r JOIN tareas t ON r.tarea_id = t.id
                WHERE r.usuario_id = %s AND r.fecha = %s
                ORDER BY r.creado_en DESC
            """, (user["id"], fecha_ver.isoformat())).fetchall()

        if not rows:
            st.info("No hay registros para esta fecha.")
        else:
            total = sum(r["cantidad"] for r in rows)
            st.metric("Total del día", total)

            for r in rows:
                with st.expander(f"**{r['tarea']}** — Cantidad: {r['cantidad']}  |  Turno: {r['turno']}  |  {fmt_ts(r['creado_en'])}"):
                    if r["observacion"]:
                        st.caption(f"Observación: {r['observacion']}")

                    puede_editar = fecha_ver >= fecha_min
                    if puede_editar:
                        tareas = obtener_tareas_activas()
                        with st.form(f"edit_{r['id']}"):
                            ce1, ce2 = st.columns(2)
                            with ce1:
                                nueva_cant = st.number_input("Cantidad", value=r["cantidad"], min_value=1, key=f"c_{r['id']}")
                            with ce2:
                                nueva_obs = st.text_input("Observación", value=r["observacion"] or "", key=f"o_{r['id']}")
                            col_btn1, col_btn2 = st.columns(2)
                            with col_btn1:
                                guardar = st.form_submit_button("Guardar cambios")
                            with col_btn2:
                                eliminar = st.form_submit_button("🗑 Eliminar", type="secondary")

                        if guardar:
                            with get_db() as db:
                                db_execute(db, 
                                    "UPDATE registros SET cantidad=%s, observacion=%s, actualizado_en=NOW() WHERE id=%s",
                                    (nueva_cant, nueva_obs, r["id"])
                                )
                                log_audit(db, user["id"], "edicion",
                                          f"Registro #{r['id']}: cantidad {r['cantidad']}→{nueva_cant}")
                            st.success("Actualizado")
                            st.rerun()
                        if eliminar:
                            with get_db() as db:
                                db_execute(db, "DELETE FROM registros WHERE id=%s", (r["id"],))
                                log_audit(db, user["id"], "eliminacion",
                                          f"Registro #{r['id']}: {r['tarea']} x{r['cantidad']}")
                            st.success("Eliminado")
                            st.rerun()
                    else:
                        st.caption("⏳ Este registro ya no se puede editar (fuera del plazo permitido).")

    # ── Mis estadísticas ──
    with tab_estadisticas:
        c1, c2 = st.columns(2)
        with c1:
            est_desde = st.date_input("Desde", value=date.today() - timedelta(days=30), key="est_d")
        with c2:
            est_hasta = st.date_input("Hasta", value=date.today(), key="est_h")

        with get_db() as db:
            df = pd.read_sql_query("""
                SELECT r.fecha, t.nombre as tarea, r.cantidad, r.turno
                FROM registros r JOIN tareas t ON r.tarea_id = t.id
                WHERE r.usuario_id = %s AND r.fecha BETWEEN %s AND %s
                ORDER BY r.fecha
            """, db, params=(user["id"], est_desde.isoformat(), est_hasta.isoformat()))
            if not df.empty: df = fix_df_types(df)

        if df.empty:
            st.info("Sin datos en este período.")
        else:
            total = df["cantidad"].sum()
            dias_con_carga = df["fecha"].nunique()
            promedio = round(total / dias_con_carga, 1) if dias_con_carga else 0

            m1, m2, m3 = st.columns(3)
            m1.metric("Total producido", f"{total:,}")
            m2.metric("Días con carga", dias_con_carga)
            m3.metric("Promedio diario", promedio)

            # Por tarea
            st.markdown("#### Por tarea")
            df_tarea = df.groupby("tarea")["cantidad"].sum().reset_index().sort_values("cantidad", ascending=True)
            fig_t = px.bar(df_tarea, y="tarea", x="cantidad", orientation="h",
                           color_discrete_sequence=["#1D5FA8"])
            fig_t.update_layout(yaxis_title="", xaxis_title="Cantidad", height=max(250, len(df_tarea) * 40))
            st.plotly_chart(fig_t, use_container_width=True)

            # Por día
            st.markdown("#### Evolución diaria")
            df_dia = df.groupby("fecha")["cantidad"].sum().reset_index()
            fig_d = px.line(df_dia, x="fecha", y="cantidad", markers=True,
                            color_discrete_sequence=["#1D5FA8"])
            fig_d.update_layout(xaxis_title="Fecha", yaxis_title="Cantidad", height=300)
            st.plotly_chart(fig_d, use_container_width=True)

            # Metas
            with get_db() as db:
                metas = db_execute(db, """
                    SELECT t.nombre as tarea, m.cantidad_objetivo, m.periodo
                    FROM metas m JOIN tareas t ON m.tarea_id = t.id
                """).fetchall()
            if metas:
                st.markdown("#### Cumplimiento de metas")
                for meta in metas:
                    if meta["periodo"] == "diario":
                        df_meta = df[df["tarea"] == meta["tarea"]].groupby("fecha")["cantidad"].sum()
                        if not df_meta.empty:
                            cumplidos = (df_meta >= meta["cantidad_objetivo"]).sum()
                            total_dias = len(df_meta)
                            pct = round(cumplidos / total_dias * 100) if total_dias else 0
                            st.progress(min(pct, 100), text=f"**{meta['tarea']}**: meta diaria {meta['cantidad_objetivo']} — cumplido {pct}% de los días ({cumplidos}/{total_dias})")

    # ── Tareas asignadas ──
    with tab_asignadas:
        with get_db() as db:
            pendientes = db_execute(db, """
                SELECT id, descripcion, fecha_limite, prioridad, estado, creado_en
                FROM asignaciones WHERE usuario_id=%s AND estado='pendiente'
                ORDER BY
                    CASE prioridad WHEN 'urgente' THEN 1 WHEN 'alta' THEN 2 WHEN 'normal' THEN 3 WHEN 'baja' THEN 4 END,
                    fecha_limite
            """, (user["id"],)).fetchall()

            completadas = db_execute(db, """
                SELECT id, descripcion, fecha_limite, prioridad, completado_en
                FROM asignaciones WHERE usuario_id=%s AND estado='completada'
                ORDER BY completado_en DESC LIMIT 20
            """, (user["id"],)).fetchall()

        if not pendientes:
            st.success("🎉 No tenés tareas pendientes.")
        else:
            st.markdown(f"#### Tenés {len(pendientes)} tarea{'s' if len(pendientes) != 1 else ''} pendiente{'s' if len(pendientes) != 1 else ''}")
            for t in pendientes:
                prioridad_emoji = {"urgente": "🔴", "alta": "🟠", "normal": "🔵", "baja": "⚪"}.get(t["prioridad"], "🔵")
                fecha_txt = f" · Fecha límite: **{t['fecha_limite']}**" if t["fecha_limite"] else ""
                with st.expander(f"{prioridad_emoji} {t['descripcion']}{fecha_txt}"):
                    st.caption(f"Asignada el {fmt_ts(t['creado_en'])}  ·  Prioridad: {t['prioridad']}")
                    if st.button("✅ Marcar como completada", key=f"comp_{t['id']}"):
                        with get_db() as db:
                            db_execute(db, "UPDATE asignaciones SET estado='completada', completado_en=NOW() WHERE id=%s",
                                       (t["id"],))
                            log_audit(db, user["id"], "tarea_completada", t["descripcion"])
                        st.success("¡Tarea completada!")
                        st.rerun()

        if completadas:
            st.divider()
            st.markdown("#### Completadas recientemente")
            for t in completadas:
                st.caption(f"✅ ~~{t['descripcion']}~~ — completada el {fmt_ts(t['completado_en'])}")

        # ── Flujos de trabajo del usuario ──
        st.divider()
        st.markdown("#### 🔄 Flujos de trabajo")

        with get_db() as db:
            mis_pasos = db_execute(db, """
                SELECT fp.id, fp.numero_paso, fp.descripcion, fp.estado, fp.flujo_id,
                       f.nombre as flujo_nombre, f.descripcion as flujo_desc
                FROM flujo_pasos fp
                JOIN flujos f ON fp.flujo_id = f.id
                WHERE fp.usuario_id = %s AND fp.estado = 'pendiente' AND f.estado = 'en_curso'
                ORDER BY f.creado_en DESC
            """, (user["id"],)).fetchall()

        if not mis_pasos:
            st.success("No tenés pasos de flujo pendientes.")
        else:
            for paso in mis_pasos:
                with st.expander(f"⏳ **{paso['flujo_nombre']}** — Paso {paso['numero_paso']}: {paso['descripcion']}"):
                    if paso["flujo_desc"]:
                        st.caption(f"Flujo: {paso['flujo_desc']}")

                    # Mostrar pasos anteriores completados
                    with get_db() as db:
                        pasos_ant = db_execute(db, """
                            SELECT fp.numero_paso, fp.descripcion, u.nombre as colaborador, fp.completado_en
                            FROM flujo_pasos fp JOIN usuarios u ON fp.usuario_id = u.id
                            WHERE fp.flujo_id = %s AND fp.numero_paso < %s AND fp.estado = 'completada'
                            ORDER BY fp.numero_paso
                        """, (paso["flujo_id"], paso["numero_paso"])).fetchall()

                    if pasos_ant:
                        st.markdown("**Pasos anteriores completados:**")
                        for pa in pasos_ant:
                            st.caption(f"✅ Paso {pa['numero_paso']}: {pa['descripcion']} ({pa['colaborador']} — {fmt_ts(pa['completado_en'])})")

                    if st.button("✅ Marcar mi paso como completado", key=f"fcomp_{paso['id']}"):
                        with get_db() as db:
                            db_execute(db, "UPDATE flujo_pasos SET estado='completada', completado_en=NOW() WHERE id=%s",
                                       (paso["id"],))
                            # Desbloquear siguiente paso
                            siguiente = db_execute(db, """
                                SELECT id FROM flujo_pasos
                                WHERE flujo_id = %s AND numero_paso = %s
                            """, (paso["flujo_id"], paso["numero_paso"] + 1)).fetchone()
                            if siguiente:
                                db_execute(db, "UPDATE flujo_pasos SET estado='pendiente' WHERE id=%s", (siguiente["id"],))
                            else:
                                # Era el último paso, completar el flujo
                                db_execute(db, "UPDATE flujos SET estado='completado', completado_en=NOW() WHERE id=%s",
                                           (paso["flujo_id"],))
                            db.commit()
                            log_audit(db, user["id"], "flujo_paso_completado",
                                      f"{paso['flujo_nombre']} — Paso {paso['numero_paso']}")
                        st.success("¡Paso completado!")
                        st.rerun()


# ─────────────────────────────────────────────────
# Panel del ADMIN
# ─────────────────────────────────────────────────
def panel_admin():
    user = st.session_state.user
    st.markdown("""
    <div style="margin-bottom:1rem;">
        <h2 style="margin:0; font-size:1.6rem;">Panel de administración 🛡️</h2>
        <p style="color:#64748b; margin:0.2rem 0 0;">Gestión del equipo, rendimiento y configuración</p>
    </div>
    """, unsafe_allow_html=True)

    tab_dash, tab_tend, tab_rend, tab_comp, tab_eval, tab_avisos, tab_asignar, tab_flujos, tab_colabs, tab_tareas, tab_metas, tab_config, tab_export, tab_audit = st.tabs([
        "📊 Dashboard", "📈 Tendencias", "👤 Rendimiento", "🔀 Comparar", "🔒 Evaluaciones",
        "📢 Avisos", "📌 Asignar tareas", "🔄 Flujos", "👥 Colaboradores", "📋 Tareas",
        "🎯 Metas", "⚙ Configuración", "📥 Exportar", "📜 Auditoría"
    ])

    # ── Dashboard ──
    with tab_dash:
        st.markdown("#### Resumen del día")
        hoy = date.today().isoformat()

        with get_db() as db:
            # Quiénes cargaron hoy
            cargaron = db_execute(db, """
                SELECT DISTINCT u.nombre FROM registros r
                JOIN usuarios u ON r.usuario_id = u.id
                WHERE r.fecha = %s AND u.rol = 'usuario' AND u.activo = 1
            """, (hoy,)).fetchall()
            nombres_cargaron = {r["nombre"] for r in cargaron}

            todos = db_execute(db, "SELECT nombre FROM usuarios WHERE rol='usuario' AND activo=1").fetchall()
            nombres_todos = {r["nombre"] for r in todos}

            no_cargaron = nombres_todos - nombres_cargaron

            total_hoy = db_execute(db, 
                "SELECT COALESCE(SUM(cantidad),0) as t FROM registros WHERE fecha=%s", (hoy,)
            ).fetchone()["t"]

            registros_hoy = db_execute(db, 
                "SELECT COUNT(*) as c FROM registros WHERE fecha=%s", (hoy,)
            ).fetchone()["c"]

        m1, m2, m3 = st.columns(3)
        m1.metric("Registros hoy", registros_hoy)
        m2.metric("Cantidad total hoy", f"{total_hoy:,}")
        m3.metric("Personas activas", len(nombres_todos))

        # Alertas
        if no_cargaron:
            st.warning(f"**⚠ Sin carga hoy:** {', '.join(sorted(no_cargaron))}")
        else:
            if nombres_todos:
                st.success("✓ Todos los colaboradores cargaron tareas hoy.")

        # Bajo promedio
        with get_db() as db:
            semana_pasada = (date.today() - timedelta(days=7)).isoformat()
            promedios = db_execute(db, """
                SELECT u.nombre,
                       ROUND(AVG(sub.total_dia), 1) as promedio,
                       COALESCE((SELECT SUM(r2.cantidad) FROM registros r2
                                 WHERE r2.usuario_id = u.id AND r2.fecha = %s), 0) as hoy
                FROM usuarios u
                JOIN (
                    SELECT usuario_id, fecha, SUM(cantidad) as total_dia
                    FROM registros
                    WHERE fecha BETWEEN %s AND %s
                    GROUP BY usuario_id, fecha
                ) sub ON sub.usuario_id = u.id
                WHERE u.rol = 'usuario' AND u.activo = 1
                GROUP BY u.id
            """, (hoy, semana_pasada, hoy)).fetchall()

        bajo_promedio = [p for p in promedios if p["hoy"] < (p["promedio"] * 0.7) and p["hoy"] > 0]
        if bajo_promedio:
            nombres_bp = ", ".join(f"{p['nombre']} ({p['hoy']} vs prom. {p['promedio']})" for p in bajo_promedio)
            st.warning(f"**📉 Por debajo del promedio hoy:** {nombres_bp}")

        # Gráfico últimos N días
        dias_dash = int(obtener_config("dias_dashboard") or 14)
        st.markdown(f"#### Producción del equipo — últimos {dias_dash} días")
        with get_db() as db:
            hace14 = (date.today() - timedelta(days=dias_dash)).isoformat()
            df_equipo = pd.read_sql_query("""
                SELECT r.fecha, u.nombre, SUM(r.cantidad) as cantidad
                FROM registros r JOIN usuarios u ON r.usuario_id = u.id
                WHERE r.fecha >= %s AND u.activo = 1
                GROUP BY r.fecha, u.nombre ORDER BY r.fecha
            """, db, params=(hace14,))
            if not df_equipo.empty: df_equipo = fix_df_types(df_equipo)

        if not df_equipo.empty:
            COLORES_DASH = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
                            "#db2777", "#0891b2", "#65a30d", "#ea580c", "#6366f1"]
            nombres_unicos = df_equipo["nombre"].unique().tolist()
            color_map_dash = {n: COLORES_DASH[i % len(COLORES_DASH)] for i, n in enumerate(nombres_unicos)}
            fig = px.bar(df_equipo, x="fecha", y="cantidad", color="nombre",
                         color_discrete_map=color_map_dash)
            fig.update_layout(xaxis_title="", yaxis_title="Cantidad", height=350,
                              legend_title="Colaborador", barmode="stack")
            st.plotly_chart(fig, use_container_width=True)

        # ── Mensajes del día de los colaboradores ──
        st.markdown("#### 💬 Mensajes del equipo hoy")
        with get_db() as db:
            mensajes_hoy = db_execute(db, """
                SELECT u.nombre, m.mensaje FROM mensajes_dia m
                JOIN usuarios u ON m.usuario_id = u.id
                WHERE m.fecha = %s ORDER BY m.creado_en DESC
            """, (hoy,)).fetchall()
        if mensajes_hoy:
            for m in mensajes_hoy:
                st.info(f"**{m['nombre']}:** {m['mensaje']}")
        else:
            st.caption("Nadie dejó un mensaje hoy.")

    # ── Tendencias automáticas ──
    with tab_tend:
        st.markdown("#### 📈 Detección automática de tendencias")
        st.caption("Análisis de las últimas 2 semanas comparadas con las 2 semanas anteriores.")

        with get_db() as db:
            colabs_tend = db_execute(db, "SELECT id, nombre FROM usuarios WHERE rol='usuario' AND activo=1 ORDER BY nombre").fetchall()

        if not colabs_tend:
            st.info("No hay colaboradores cargados.")
        else:
            hoy_d = date.today()
            sem_actual_desde = (hoy_d - timedelta(days=13)).isoformat()
            sem_actual_hasta = hoy_d.isoformat()
            sem_anterior_desde = (hoy_d - timedelta(days=27)).isoformat()
            sem_anterior_hasta = (hoy_d - timedelta(days=14)).isoformat()

            alertas_tend = []

            for c in colabs_tend:
                with get_db() as db:
                    actual = db_execute(db, """
                        SELECT COALESCE(SUM(cantidad), 0) as total,
                               COUNT(DISTINCT fecha) as dias
                        FROM registros WHERE usuario_id=%s AND fecha BETWEEN %s AND %s
                    """, (c["id"], sem_actual_desde, sem_actual_hasta)).fetchone()

                    anterior = db_execute(db, """
                        SELECT COALESCE(SUM(cantidad), 0) as total,
                               COUNT(DISTINCT fecha) as dias
                        FROM registros WHERE usuario_id=%s AND fecha BETWEEN %s AND %s
                    """, (c["id"], sem_anterior_desde, sem_anterior_hasta)).fetchone()

                    # Días sin cargar en la última semana
                    dias_sin_carga = db_execute(db, """
                        SELECT COUNT(DISTINCT fecha) as dias FROM registros
                        WHERE usuario_id=%s AND fecha BETWEEN %s AND %s
                    """, (c["id"], (hoy_d - timedelta(days=6)).isoformat(), sem_actual_hasta)).fetchone()["dias"]

                prom_actual = round(actual["total"] / actual["dias"], 1) if actual["dias"] else 0
                prom_anterior = round(anterior["total"] / anterior["dias"], 1) if anterior["dias"] else 0

                if prom_anterior > 0 and prom_actual > 0:
                    cambio = round((prom_actual - prom_anterior) / prom_anterior * 100, 1)
                elif prom_anterior == 0 and prom_actual > 0:
                    cambio = 100.0
                elif prom_anterior > 0 and prom_actual == 0:
                    cambio = -100.0
                else:
                    cambio = 0

                alertas_tend.append({
                    "nombre": c["nombre"],
                    "prom_actual": prom_actual,
                    "prom_anterior": prom_anterior,
                    "cambio": cambio,
                    "total_actual": actual["total"],
                    "dias_carga": actual["dias"],
                    "dias_sin_carga_semana": 7 - dias_sin_carga
                })

            # Ordenar por cambio para mostrar primero los que más bajaron
            alertas_tend.sort(key=lambda x: x["cambio"])

            # Resumen visual
            mejoraron = [a for a in alertas_tend if a["cambio"] > 10]
            estables = [a for a in alertas_tend if -10 <= a["cambio"] <= 10]
            bajaron = [a for a in alertas_tend if a["cambio"] < -10]
            sin_datos = [a for a in alertas_tend if a["prom_actual"] == 0 and a["prom_anterior"] == 0]

            m1, m2, m3 = st.columns(3)
            m1.metric("📉 Bajaron", len(bajaron))
            m2.metric("➡️ Estables", len(estables))
            m3.metric("📈 Mejoraron", len(mejoraron))

            # Alertas críticas
            if bajaron:
                st.markdown("##### ⚠️ Requieren atención (bajaron más del 10%)")
                for a in bajaron:
                    st.error(f"**{a['nombre']}**: bajó un **{abs(a['cambio'])}%** "
                             f"(promedio diario: {a['prom_anterior']} → {a['prom_actual']})")

            # Días sin cargar
            sin_cargar = [a for a in alertas_tend if a["dias_sin_carga_semana"] >= 2]
            if sin_cargar:
                st.markdown("##### ⏳ Días sin carga en la última semana")
                for a in sin_cargar:
                    st.warning(f"**{a['nombre']}**: {a['dias_sin_carga_semana']} día{'s' if a['dias_sin_carga_semana'] != 1 else ''} sin cargar")

            # Mejoraron
            if mejoraron:
                st.markdown("##### 🌟 Mejoraron (más del 10%)")
                for a in mejoraron:
                    st.success(f"**{a['nombre']}**: subió un **+{a['cambio']}%** "
                               f"(promedio diario: {a['prom_anterior']} → {a['prom_actual']})")

            # Tabla completa
            st.divider()
            st.markdown("##### Tabla completa")
            df_tend = pd.DataFrame(alertas_tend)
            if not df_tend.empty:
                df_tend = df_tend.rename(columns={
                    "nombre": "Colaborador", "prom_actual": "Prom. actual",
                    "prom_anterior": "Prom. anterior", "cambio": "Variación %",
                    "total_actual": "Total últ. 2 sem.", "dias_carga": "Días con carga"
                })
                df_tend = df_tend[["Colaborador", "Prom. anterior", "Prom. actual", "Variación %", "Total últ. 2 sem.", "Días con carga"]]
                st.dataframe(df_tend, use_container_width=True, hide_index=True)

            # Gráfico de evolución de todo el equipo
            st.markdown("##### Evolución del equipo — últimas 4 semanas")
            with get_db() as db:
                df_evo_tend = pd.read_sql_query("""
                    SELECT r.fecha, u.nombre, SUM(r.cantidad) as cantidad
                    FROM registros r JOIN usuarios u ON r.usuario_id = u.id
                    WHERE r.fecha >= %s AND u.activo = 1
                    GROUP BY r.fecha, u.nombre ORDER BY r.fecha
                """, db, params=(sem_anterior_desde,))
                if not df_evo_tend.empty: df_evo_tend = fix_df_types(df_evo_tend)

            if not df_evo_tend.empty:
                COLORES_T = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
                             "#db2777", "#0891b2", "#65a30d", "#ea580c", "#6366f1"]
                noms = df_evo_tend["nombre"].unique().tolist()
                cmap_t = {n: COLORES_T[i % len(COLORES_T)] for i, n in enumerate(noms)}
                fig_tend = px.line(df_evo_tend, x="fecha", y="cantidad", color="nombre",
                                   markers=True, color_discrete_map=cmap_t)
                fig_tend.update_layout(height=400, xaxis_title="", yaxis_title="Cantidad diaria",
                                       legend_title="Colaborador")
                fig_tend.update_traces(line=dict(width=3))
                st.plotly_chart(fig_tend, use_container_width=True)

    # ── Avisos / Cartelera ──
    with tab_avisos:
        st.markdown("#### 📢 Cartelera de avisos")
        st.caption("Los avisos activos se muestran a todos los colaboradores cuando entran.")

        st.markdown("##### Publicar aviso")
        with st.form("form_aviso", clear_on_submit=True):
            av1, av2 = st.columns([3, 1])
            with av1:
                aviso_titulo = st.text_input("Título", key="av_titulo", placeholder="Ej: Reunión de equipo")
            with av2:
                aviso_prio = st.selectbox("Prioridad", ["normal", "importante", "urgente"], key="av_prio")
            aviso_msg = st.text_area("Mensaje", key="av_msg", height=80,
                                      placeholder="Ej: Mañana a las 10hs reunión en sala 2. Puntualidad.")
            aviso_enviar = st.form_submit_button("Publicar aviso", type="primary")

        if aviso_enviar:
            if not aviso_titulo.strip() or not aviso_msg.strip():
                st.error("Completá título y mensaje.")
            else:
                with get_db() as db:
                    db_execute(db, "INSERT INTO avisos (titulo, mensaje, prioridad, creado_por) VALUES (%s,%s,%s,%s)",
                               (aviso_titulo.strip(), aviso_msg.strip(), aviso_prio, user["id"]))
                    db.commit()
                    log_audit(db, user["id"], "aviso", aviso_titulo.strip())
                st.success("✓ Aviso publicado.")
                st.rerun()

        st.divider()
        st.markdown("##### Avisos activos")
        with get_db() as db:
            avisos_admin = db_execute(db, """
                SELECT a.id, a.titulo, a.mensaje, a.prioridad, a.creado_en
                FROM avisos a WHERE a.activo = 1 ORDER BY a.creado_en DESC
            """).fetchall()

        if not avisos_admin:
            st.info("No hay avisos activos.")
        else:
            for a in avisos_admin:
                prio_icon = {"urgente": "🚨", "importante": "⚠️", "normal": "📢"}.get(a["prioridad"], "📢")
                with st.expander(f"{prio_icon} **{a['titulo']}** — {fmt_ts(a['creado_en'])}"):
                    st.write(a["mensaje"])
                    if st.button("🗑 Desactivar aviso", key=f"avdel_{a['id']}"):
                        with get_db() as db:
                            db_execute(db, "UPDATE avisos SET activo=0 WHERE id=%s", (a["id"],))
                            db.commit()
                        st.rerun()

        st.divider()
        st.markdown("##### 💬 Mensajes de los colaboradores")
        msg_fecha = st.date_input("Ver mensajes del", value=date.today(), key="msg_fecha_admin")
        with get_db() as db:
            msgs = db_execute(db, """
                SELECT u.nombre, m.mensaje, m.creado_en
                FROM mensajes_dia m JOIN usuarios u ON m.usuario_id = u.id
                WHERE m.fecha = %s ORDER BY m.creado_en DESC
            """, (msg_fecha.isoformat(),)).fetchall()
        if msgs:
            for m in msgs:
                st.info(f"**{m['nombre']}** ({fmt_ts(m['creado_en'])}): {m['mensaje']}")
        else:
            st.caption("No hay mensajes para esta fecha.")

    # ── Rendimiento individual ──
    with tab_rend:
        with get_db() as db:
            colabs = db_execute(db, "SELECT id, nombre FROM usuarios WHERE rol='usuario' AND activo=1 ORDER BY nombre").fetchall()

        if not colabs:
            st.info("No hay colaboradores cargados.")
        else:
            nombres = {c["nombre"]: c["id"] for c in colabs}
            sel = st.selectbox("Colaborador", list(nombres.keys()), key="rend_sel")
            uid = nombres[sel]

            cr1, cr2 = st.columns(2)
            with cr1:
                rd = st.date_input("Desde", value=date.today() - timedelta(days=30), key="rend_d")
            with cr2:
                rh = st.date_input("Hasta", value=date.today(), key="rend_h")

            with get_db() as db:
                df_r = pd.read_sql_query("""
                    SELECT r.fecha, t.nombre as tarea, r.cantidad, r.turno, r.observacion
                    FROM registros r JOIN tareas t ON r.tarea_id = t.id
                    WHERE r.usuario_id = %s AND r.fecha BETWEEN %s AND %s
                    ORDER BY r.fecha DESC
                """, db, params=(uid, rd.isoformat(), rh.isoformat()))
                if not df_r.empty: df_r = fix_df_types(df_r)

            if df_r.empty:
                st.info("Sin registros en este período.")
            else:
                total = df_r["cantidad"].sum()
                dias = df_r["fecha"].nunique()
                prom = round(total / dias, 1) if dias else 0

                m1, m2, m3 = st.columns(3)
                m1.metric("Total", f"{total:,}")
                m2.metric("Días con carga", dias)
                m3.metric("Promedio diario", prom)

                # Comparar con período anterior
                rango = (rh - rd).days
                rd_ant = rd - timedelta(days=rango + 1)
                rh_ant = rd - timedelta(days=1)

                with get_db() as db:
                    ant = db_execute(db, """
                        SELECT COALESCE(SUM(cantidad),0) as total
                        FROM registros WHERE usuario_id=%s AND fecha BETWEEN %s AND %s
                    """, (uid, rd_ant.isoformat(), rh_ant.isoformat())).fetchone()["total"]

                if ant > 0:
                    delta = round((total - ant) / ant * 100, 1)
                    signo = "+" if delta > 0 else ""
                    st.info(f"📈 Comparado con el período anterior ({rd_ant.strftime('%d/%m')} al {rh_ant.strftime('%d/%m')}): "
                            f"**{signo}{delta}%** ({ant:,} → {total:,})")

                # Gráfico diario
                df_dia = df_r.groupby("fecha")["cantidad"].sum().reset_index()
                fig = px.bar(df_dia, x="fecha", y="cantidad", color_discrete_sequence=["#1D5FA8"])
                fig.update_layout(xaxis_title="", yaxis_title="Cantidad", height=300)
                st.plotly_chart(fig, use_container_width=True)

                # Por tarea
                df_t = df_r.groupby("tarea")["cantidad"].sum().reset_index().sort_values("cantidad", ascending=True)
                fig2 = px.bar(df_t, y="tarea", x="cantidad", orientation="h", color_discrete_sequence=["#2E86AB"])
                fig2.update_layout(yaxis_title="", xaxis_title="Cantidad", height=max(250, len(df_t) * 40))
                st.plotly_chart(fig2, use_container_width=True)

                # Detalle
                with st.expander("Ver detalle de registros"):
                    st.dataframe(df_r, use_container_width=True, hide_index=True)

                # ── Editar registros (admin) ──
                st.divider()
                st.markdown("#### ✏️ Editar registros del colaborador")
                st.caption("Corregí registros que no coincidan con los reportes del sistema.")

                fecha_edit = st.date_input("Fecha a editar", value=date.today(), key="admin_edit_fecha")

                with get_db() as db:
                    regs_edit = db_execute(db, """
                        SELECT r.id, r.fecha, r.turno, t.nombre as tarea, t.id as tarea_id,
                               r.cantidad, r.observacion, r.creado_en
                        FROM registros r JOIN tareas t ON r.tarea_id = t.id
                        WHERE r.usuario_id = %s AND r.fecha = %s
                        ORDER BY r.creado_en DESC
                    """, (uid, fecha_edit.isoformat())).fetchall()

                if not regs_edit:
                    st.info("No hay registros de este colaborador para esa fecha.")
                else:
                    for reg in regs_edit:
                        with st.expander(f"**{reg['tarea']}** — Cantidad: {reg['cantidad']}  |  Turno: {reg['turno']}  |  {fmt_ts(reg['creado_en'])}"):
                            if reg["observacion"]:
                                st.caption(f"Observación del colaborador: {reg['observacion']}")

                            with st.form(f"admin_edit_{reg['id']}"):
                                ae1, ae2 = st.columns(2)
                                with ae1:
                                    nueva_cant = st.number_input("Cantidad corregida", value=reg["cantidad"], min_value=0, key=f"ae_c_{reg['id']}")
                                with ae2:
                                    nueva_obs = st.text_input("Observación del admin", value="", key=f"ae_o_{reg['id']}",
                                                               placeholder="Ej: Corregido, el sistema reporta 45")
                                col_guardar, col_eliminar = st.columns(2)
                                with col_guardar:
                                    guardar = st.form_submit_button("💾 Guardar corrección")
                                with col_eliminar:
                                    eliminar = st.form_submit_button("🗑 Eliminar registro")

                            if guardar:
                                obs_final = reg["observacion"] or ""
                                if nueva_obs.strip():
                                    obs_final = f"{obs_final} | [ADMIN: {nueva_obs.strip()}]" if obs_final else f"[ADMIN: {nueva_obs.strip()}]"
                                with get_db() as db:
                                    db_execute(db, """
                                        UPDATE registros SET cantidad=%s, observacion=%s, actualizado_en=NOW()
                                        WHERE id=%s
                                    """, (nueva_cant, obs_final, reg["id"]))
                                    log_audit(db, user["id"], "admin_edicion",
                                              f"Registro #{reg['id']} de {sel}: cantidad {reg['cantidad']}->{nueva_cant}")
                                st.success(f"✓ Registro corregido: {reg['cantidad']} → {nueva_cant}")
                                st.rerun()

                            if eliminar:
                                with get_db() as db:
                                    db_execute(db, "DELETE FROM registros WHERE id=%s", (reg["id"],))
                                    log_audit(db, user["id"], "admin_eliminacion",
                                              f"Registro #{reg['id']} de {sel}: {reg['tarea']} x{reg['cantidad']}")
                                st.success("Registro eliminado.")
                                st.rerun()

    # ── Comparar colaboradores ──
    with tab_comp:
        with get_db() as db:
            colabs = db_execute(db, "SELECT id, nombre FROM usuarios WHERE rol='usuario' AND activo=1 ORDER BY nombre").fetchall()

        if len(colabs) < 2:
            st.info("Necesitás al menos 2 colaboradores para comparar.")
        else:
            nombres = {c["nombre"]: c["id"] for c in colabs}
            seleccion = st.multiselect("Colaboradores a comparar", list(nombres.keys()),
                                       default=list(nombres.keys())[:2], key="comp_sel")

            cc1, cc2 = st.columns(2)
            with cc1:
                cd = st.date_input("Desde", value=date.today() - timedelta(days=30), key="comp_d")
            with cc2:
                ch = st.date_input("Hasta", value=date.today(), key="comp_h")

            if len(seleccion) >= 2:
                ids = [nombres[n] for n in seleccion]
                placeholders = ",".join(["%s"] * len(ids))

                with get_db() as db:
                    query = f"""
                        SELECT u.nombre as colaborador, r.fecha, t.nombre as tarea, SUM(r.cantidad) as cantidad
                        FROM registros r
                        JOIN usuarios u ON r.usuario_id = u.id
                        JOIN tareas t ON r.tarea_id = t.id
                        WHERE r.usuario_id IN ({placeholders}) AND r.fecha BETWEEN %s AND %s
                        GROUP BY u.nombre, r.fecha, t.nombre
                        ORDER BY r.fecha
                    """
                    df_c = pd.read_sql_query(query, db, params=(*ids, cd.isoformat(), ch.isoformat()))
                    if not df_c.empty: df_c = fix_df_types(df_c)

                if df_c.empty:
                    st.info("Sin datos para comparar en este período.")
                else:
                    # Paleta de colores bien diferenciados
                    COLORES = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
                               "#db2777", "#0891b2", "#65a30d", "#ea580c", "#6366f1"]
                    color_map = {nombre: COLORES[i % len(COLORES)] for i, nombre in enumerate(seleccion)}

                    # Filtro por tarea
                    tareas_disponibles = sorted(df_c["tarea"].unique().tolist())
                    filtro_tarea = st.selectbox("Filtrar por tarea", ["Todas las tareas"] + tareas_disponibles, key="comp_tarea")

                    if filtro_tarea != "Todas las tareas":
                        df_c = df_c[df_c["tarea"] == filtro_tarea]

                    # ── Totales generales ──
                    df_totales = df_c.groupby("colaborador")["cantidad"].sum().reset_index()
                    for nombre in seleccion:
                        if nombre not in df_totales["colaborador"].values:
                            df_totales = pd.concat([df_totales, pd.DataFrame([{"colaborador": nombre, "cantidad": 0}])], ignore_index=True)
                    df_totales = df_totales.sort_values("cantidad", ascending=False)

                    titulo_totales = f"Totales — {filtro_tarea}" if filtro_tarea != "Todas las tareas" else "Totales generales"
                    st.markdown(f"#### {titulo_totales}")
                    fig_tot = px.bar(df_totales, x="colaborador", y="cantidad",
                                     color="colaborador", color_discrete_map=color_map)
                    fig_tot.update_layout(showlegend=True, height=350, xaxis_title="", yaxis_title="Cantidad total")
                    fig_tot.update_traces(texttemplate='%{y}', textposition='outside')
                    st.plotly_chart(fig_tot, use_container_width=True)

                    # ── Evolución diaria ──
                    titulo_evo = f"Evolución diaria — {filtro_tarea}" if filtro_tarea != "Todas las tareas" else "Evolución diaria — todas las tareas"
                    st.markdown(f"#### {titulo_evo}")
                    df_dia_c = df_c.groupby(["fecha", "colaborador"])["cantidad"].sum().reset_index()
                    fig_evo = px.line(df_dia_c, x="fecha", y="cantidad", color="colaborador",
                                      markers=True, color_discrete_map=color_map)
                    fig_evo.update_layout(height=350, xaxis_title="", yaxis_title="Cantidad")
                    fig_evo.update_traces(line=dict(width=3))
                    st.plotly_chart(fig_evo, use_container_width=True)

                    # ── Gráfico por cada tarea (solo si está en "Todas") ──
                    if filtro_tarea == "Todas las tareas":
                        st.markdown("#### Comparación por tarea")
                        for tarea in tareas_disponibles:
                            df_t = df_c[df_c["tarea"] == tarea].groupby("colaborador")["cantidad"].sum().reset_index()
                            for nombre in seleccion:
                                if nombre not in df_t["colaborador"].values:
                                    df_t = pd.concat([df_t, pd.DataFrame([{"colaborador": nombre, "cantidad": 0}])], ignore_index=True)
                            df_t = df_t.sort_values("cantidad", ascending=False)

                            st.markdown(f"**{tarea}**")
                            fig_t = px.bar(df_t, x="colaborador", y="cantidad",
                                           color="colaborador", color_discrete_map=color_map)
                            fig_t.update_layout(showlegend=False, height=280, xaxis_title="", yaxis_title="Cantidad",
                                                margin=dict(t=10, b=10))
                            fig_t.update_traces(texttemplate='%{y}', textposition='outside')
                            st.plotly_chart(fig_t, use_container_width=True)

    # ── Evaluaciones privadas ──
    with tab_eval:
        st.markdown("#### 🔒 Evaluaciones de desempeño (solo visible para administradores)")

        CATEGORIAS = ["Actitud", "Puntualidad", "Predisposición", "Trato con compañeros",
                       "Respeto", "Proactividad", "Presentismo", "Otro"]
        PUNTAJES = {"1 — Muy malo": 1, "2 — Malo": 2, "3 — Regular": 3, "4 — Bueno": 4, "5 — Excelente": 5}

        with get_db() as db:
            colabs_eval = db_execute(db, "SELECT id, nombre FROM usuarios WHERE rol='usuario' AND activo=1 ORDER BY nombre").fetchall()

        if not colabs_eval:
            st.info("No hay colaboradores cargados.")
        else:
            nombres_eval = {c["nombre"]: c["id"] for c in colabs_eval}

            st.markdown("##### Registrar evaluación")
            with st.form("form_eval", clear_on_submit=True):
                fe1, fe2 = st.columns(2)
                with fe1:
                    eval_colab = st.selectbox("Colaborador", list(nombres_eval.keys()), key="eval_colab")
                with fe2:
                    eval_fecha = st.date_input("Fecha", value=date.today(), key="eval_fecha")

                fe3, fe4 = st.columns(2)
                with fe3:
                    eval_cat = st.selectbox("Categoría", CATEGORIAS, key="eval_cat")
                with fe4:
                    eval_punt = st.selectbox("Puntaje", list(PUNTAJES.keys()), index=2, key="eval_punt")

                eval_obs = st.text_area("Observación (detalle de la situación)", key="eval_obs", height=80,
                                         placeholder="Ej: Llegó 30 min tarde sin aviso, contestó de mala manera cuando se le consultó...")
                eval_enviar = st.form_submit_button("Registrar evaluación", type="primary")

            if eval_enviar:
                if not eval_obs.strip():
                    st.error("Escribí una observación describiendo la situación.")
                else:
                    uid_eval = nombres_eval[eval_colab]
                    with get_db() as db:
                        db_execute(db, """INSERT INTO evaluaciones (usuario_id, fecha, categoria, puntaje, observacion, registrado_por)
                                      VALUES (%s, %s, %s, %s, %s, %s)""",
                                   (uid_eval, eval_fecha.isoformat(), eval_cat, PUNTAJES[eval_punt],
                                    eval_obs.strip(), user["id"]))
                        log_audit(db, user["id"], "evaluacion_privada",
                                  f"{eval_colab}: {eval_cat} ({PUNTAJES[eval_punt]})")
                    st.success(f"✓ Evaluación registrada para {eval_colab}")
                    st.rerun()

            st.divider()

            # ── Historial de evaluaciones ──
            st.markdown("##### Historial de evaluaciones")
            he1, he2, he3 = st.columns(3)
            with he1:
                hist_colab = st.selectbox("Filtrar por colaborador", ["Todos"] + list(nombres_eval.keys()), key="hist_eval_colab")
            with he2:
                hist_desde = st.date_input("Desde", value=date.today() - timedelta(days=30), key="hist_eval_d")
            with he3:
                hist_hasta = st.date_input("Hasta", value=date.today(), key="hist_eval_h")

            with get_db() as db:
                if hist_colab == "Todos":
                    evals = db_execute(db, """
                        SELECT e.id, u.nombre as colaborador, e.fecha, e.categoria, e.puntaje,
                               e.observacion, e.creado_en
                        FROM evaluaciones e JOIN usuarios u ON e.usuario_id = u.id
                        WHERE e.fecha BETWEEN %s AND %s
                        ORDER BY e.fecha DESC, e.creado_en DESC
                    """, (hist_desde.isoformat(), hist_hasta.isoformat())).fetchall()
                else:
                    uid_hist = nombres_eval[hist_colab]
                    evals = db_execute(db, """
                        SELECT e.id, u.nombre as colaborador, e.fecha, e.categoria, e.puntaje,
                               e.observacion, e.creado_en
                        FROM evaluaciones e JOIN usuarios u ON e.usuario_id = u.id
                        WHERE e.usuario_id = %s AND e.fecha BETWEEN %s AND %s
                        ORDER BY e.fecha DESC, e.creado_en DESC
                    """, (uid_hist, hist_desde.isoformat(), hist_hasta.isoformat())).fetchall()

            if not evals:
                st.info("No hay evaluaciones en este período.")
            else:
                # Resumen de promedios
                df_eval = pd.DataFrame([dict(e) for e in evals])
                st.markdown("##### Promedio por categoría")

                if hist_colab == "Todos":
                    pivot = df_eval.groupby(["colaborador", "categoria"])["puntaje"].mean().reset_index()
                    pivot["puntaje"] = pivot["puntaje"].round(1)
                    COLORES_EVAL = ["#2563eb", "#dc2626", "#059669", "#d97706", "#7c3aed",
                                    "#db2777", "#0891b2", "#65a30d", "#ea580c", "#6366f1"]
                    nombres_u = pivot["colaborador"].unique().tolist()
                    cmap_eval = {n: COLORES_EVAL[i % len(COLORES_EVAL)] for i, n in enumerate(nombres_u)}
                    fig_eval = px.bar(pivot, x="categoria", y="puntaje", color="colaborador",
                                      barmode="group", color_discrete_map=cmap_eval)
                    fig_eval.update_layout(height=350, xaxis_title="", yaxis_title="Puntaje promedio",
                                           yaxis=dict(range=[0, 5.5]))
                    fig_eval.update_traces(texttemplate='%{y}', textposition='outside')
                    st.plotly_chart(fig_eval, use_container_width=True)
                else:
                    prom_cat = df_eval.groupby("categoria")["puntaje"].mean().reset_index()
                    prom_cat["puntaje"] = prom_cat["puntaje"].round(1)
                    prom_general = round(df_eval["puntaje"].mean(), 1)

                    st.metric("Promedio general", f"{prom_general} / 5")

                    fig_eval = px.bar(prom_cat, x="categoria", y="puntaje",
                                      color_discrete_sequence=["#2563eb"])
                    fig_eval.update_layout(height=300, xaxis_title="", yaxis_title="Puntaje promedio",
                                           yaxis=dict(range=[0, 5.5]))
                    fig_eval.update_traces(texttemplate='%{y}', textposition='outside')
                    st.plotly_chart(fig_eval, use_container_width=True)

                # Detalle
                st.markdown("##### Detalle de evaluaciones")
                for e in evals:
                    puntaje_emoji = {1: "🔴", 2: "🟠", 3: "🟡", 4: "🔵", 5: "🟢"}.get(e["puntaje"], "⚪")
                    with st.expander(f"{puntaje_emoji} {e['fecha']} — **{e['colaborador']}** — {e['categoria']} ({e['puntaje']}/5)"):
                        st.write(e["observacion"])
                        st.caption(f"Registrado el {fmt_ts(e['creado_en'])}")
                        if st.button("🗑 Eliminar", key=f"edel_{e['id']}"):
                            with get_db() as db:
                                db_execute(db, "DELETE FROM evaluaciones WHERE id=%s", (e["id"],))
                            st.rerun()

    # ── Asignar tareas ──
    with tab_asignar:
        st.markdown("#### Asignar tarea a un colaborador")
        with get_db() as db:
            colabs_asig = db_execute(db, "SELECT id, nombre FROM usuarios WHERE rol='usuario' AND activo=1 ORDER BY nombre").fetchall()

        if not colabs_asig:
            st.info("No hay colaboradores cargados.")
        else:
            nombres_asig = {c["nombre"]: c["id"] for c in colabs_asig}
            with st.form("form_asignar", clear_on_submit=True):
                fa1, fa2 = st.columns(2)
                with fa1:
                    asig_colab = st.selectbox("Colaborador", list(nombres_asig.keys()), key="asig_colab")
                with fa2:
                    asig_prioridad = st.selectbox("Prioridad", ["urgente", "alta", "normal", "baja"], index=2, key="asig_prio")
                asig_desc = st.text_area("Descripción de la tarea", key="asig_desc", height=80)
                asig_fecha = st.date_input("Fecha límite (opcional)", value=None, key="asig_fecha")
                asig_enviar = st.form_submit_button("Asignar tarea", type="primary")

            if asig_enviar:
                if not asig_desc.strip():
                    st.error("Escribí una descripción para la tarea.")
                else:
                    uid = nombres_asig[asig_colab]
                    fecha_lim = asig_fecha.isoformat() if asig_fecha else None
                    with get_db() as db:
                        db_execute(db, "INSERT INTO asignaciones (usuario_id, descripcion, fecha_limite, prioridad) VALUES (%s,%s,%s,%s)",
                                   (uid, asig_desc.strip(), fecha_lim, asig_prioridad))
                        log_audit(db, user["id"], "asignacion", f"A {asig_colab}: {asig_desc.strip()[:50]}")
                    st.success(f"✓ Tarea asignada a {asig_colab}")
                    st.rerun()

            st.divider()
            st.markdown("#### Tareas asignadas activas")

            filtro_asig = st.selectbox("Filtrar por colaborador", ["Todos"] + list(nombres_asig.keys()), key="filtro_asig")

            with get_db() as db:
                if filtro_asig == "Todos":
                    asignaciones = db_execute(db, """
                        SELECT a.id, u.nombre as colaborador, a.descripcion, a.fecha_limite,
                               a.prioridad, a.estado, a.creado_en, a.completado_en
                        FROM asignaciones a JOIN usuarios u ON a.usuario_id = u.id
                        ORDER BY
                            CASE a.estado WHEN 'pendiente' THEN 1 WHEN 'completada' THEN 2 END,
                            CASE a.prioridad WHEN 'urgente' THEN 1 WHEN 'alta' THEN 2 WHEN 'normal' THEN 3 WHEN 'baja' THEN 4 END,
                            a.creado_en DESC
                    """).fetchall()
                else:
                    uid_f = nombres_asig[filtro_asig]
                    asignaciones = db_execute(db, """
                        SELECT a.id, u.nombre as colaborador, a.descripcion, a.fecha_limite,
                               a.prioridad, a.estado, a.creado_en, a.completado_en
                        FROM asignaciones a JOIN usuarios u ON a.usuario_id = u.id
                        WHERE a.usuario_id = %s
                        ORDER BY
                            CASE a.estado WHEN 'pendiente' THEN 1 WHEN 'completada' THEN 2 END,
                            a.creado_en DESC
                    """, (uid_f,)).fetchall()

            if not asignaciones:
                st.info("No hay tareas asignadas.")
            else:
                for a in asignaciones:
                    prio_emoji = {"urgente": "🔴", "alta": "🟠", "normal": "🔵", "baja": "⚪"}.get(a["prioridad"], "🔵")
                    estado_emoji = "✅" if a["estado"] == "completada" else "⏳"
                    fecha_txt = f" · Límite: {a['fecha_limite']}" if a["fecha_limite"] else ""
                    titulo = f"{estado_emoji} {prio_emoji} **{a['colaborador']}** — {a['descripcion'][:60]}{fecha_txt}"

                    with st.expander(titulo):
                        st.write(a["descripcion"])
                        st.caption(f"Prioridad: {a['prioridad']}  ·  Asignada: {fmt_ts(a['creado_en'])}")
                        if a["estado"] == "completada":
                            st.caption(f"✅ Completada el {fmt_ts(a['completado_en'])}")
                        col_a1, col_a2 = st.columns(2)
                        if a["estado"] == "pendiente":
                            if col_a1.button("✅ Marcar completada", key=f"acomp_{a['id']}"):
                                with get_db() as db:
                                    db_execute(db, "UPDATE asignaciones SET estado='completada', completado_en=NOW() WHERE id=%s",
                                               (a["id"],))
                                st.rerun()
                        if col_a2.button("🗑 Eliminar", key=f"adel_{a['id']}"):
                            with get_db() as db:
                                db_execute(db, "DELETE FROM asignaciones WHERE id=%s", (a["id"],))
                            st.rerun()

    # ── Flujos de trabajo ──
    with tab_flujos:
        st.markdown("#### 🔄 Tareas por pasos (flujos de trabajo)")
        st.caption("Creá tareas complejas que pasan de un colaborador a otro por etapas.")

        with get_db() as db:
            colabs_flujo = db_execute(db, "SELECT id, nombre FROM usuarios WHERE rol='usuario' AND activo=1 ORDER BY nombre").fetchall()

        if not colabs_flujo:
            st.info("No hay colaboradores cargados.")
        else:
            nombres_flujo = {c["nombre"]: c["id"] for c in colabs_flujo}

            # ── Crear nuevo flujo ──
            st.markdown("##### Crear nuevo flujo")

            if "pasos_temp" not in st.session_state:
                st.session_state.pasos_temp = []

            flujo_nombre = st.text_input("Nombre del flujo", key="flujo_nombre", placeholder="Ej: Armado y despacho de pedido")
            flujo_desc = st.text_input("Descripción general (opcional)", key="flujo_desc")

            st.markdown("**Pasos del flujo:**")

            # Mostrar pasos agregados
            if st.session_state.pasos_temp:
                for i, paso in enumerate(st.session_state.pasos_temp):
                    st.info(f"**Paso {i+1}:** {paso['descripcion']} → **{paso['colaborador']}**")
            else:
                st.caption("Todavía no agregaste pasos. Usá el formulario de abajo.")

            # Agregar paso
            with st.form("form_paso", clear_on_submit=True):
                fp1, fp2 = st.columns(2)
                with fp1:
                    paso_colab = st.selectbox("Colaborador para este paso", list(nombres_flujo.keys()), key="paso_colab")
                with fp2:
                    paso_desc = st.text_input("Descripción del paso", key="paso_desc",
                                              placeholder="Ej: Preparar los materiales")
                agregar_paso = st.form_submit_button("➕ Agregar paso")

            if agregar_paso and paso_desc.strip():
                st.session_state.pasos_temp.append({
                    "colaborador": paso_colab,
                    "usuario_id": nombres_flujo[paso_colab],
                    "descripcion": paso_desc.strip()
                })
                st.rerun()

            col_crear, col_limpiar = st.columns(2)
            with col_crear:
                if st.button("✅ Crear flujo", type="primary", disabled=len(st.session_state.pasos_temp) < 2 or not flujo_nombre):
                    with get_db() as db:
                        cur = db_execute(db, "INSERT INTO flujos (nombre, descripcion, creado_por) VALUES (%s, %s, %s) RETURNING id",
                                         (flujo_nombre.strip(), flujo_desc.strip(), user["id"]))
                        flujo_id = cur.fetchone()['id']
                        for i, paso in enumerate(st.session_state.pasos_temp):
                            estado = "pendiente" if i == 0 else "bloqueado"
                            db_execute(db, "INSERT INTO flujo_pasos (flujo_id, numero_paso, usuario_id, descripcion, estado) VALUES (%s,%s,%s,%s,%s)",
                                       (flujo_id, i + 1, paso["usuario_id"], paso["descripcion"], estado))
                        db.commit()
                        log_audit(db, user["id"], "flujo_creado",
                                  f"{flujo_nombre}: {len(st.session_state.pasos_temp)} pasos")
                    st.session_state.pasos_temp = []
                    st.success("✓ Flujo creado. El paso 1 ya está visible para el colaborador asignado.")
                    st.rerun()
            with col_limpiar:
                if st.button("🗑 Limpiar pasos"):
                    st.session_state.pasos_temp = []
                    st.rerun()

            st.divider()

            # ── Flujos activos ──
            st.markdown("##### Flujos activos")
            with get_db() as db:
                flujos = db_execute(db, """
                    SELECT f.id, f.nombre, f.descripcion, f.estado, f.creado_en
                    FROM flujos f ORDER BY
                        CASE f.estado WHEN 'en_curso' THEN 1 WHEN 'completado' THEN 2 END,
                        f.creado_en DESC
                """).fetchall()

            if not flujos:
                st.info("No hay flujos creados.")
            else:
                for f in flujos:
                    estado_flujo = "🟢 En curso" if f["estado"] == "en_curso" else "✅ Completado"
                    with st.expander(f"{estado_flujo} — **{f['nombre']}** — {f['creado_en'][:10]}"):
                        if f["descripcion"]:
                            st.caption(f["descripcion"])

                        with get_db() as db:
                            pasos = db_execute(db, """
                                SELECT fp.id, fp.numero_paso, fp.descripcion, fp.estado,
                                       u.nombre as colaborador, fp.completado_en
                                FROM flujo_pasos fp JOIN usuarios u ON fp.usuario_id = u.id
                                WHERE fp.flujo_id = %s
                                ORDER BY fp.numero_paso
                            """, (f["id"],)).fetchall()

                        for p in pasos:
                            if p["estado"] == "completada":
                                st.success(f"**Paso {p['numero_paso']}:** {p['descripcion']} → {p['colaborador']} ✅ ({fmt_ts(p['completado_en'])})")
                            elif p["estado"] == "pendiente":
                                st.warning(f"**Paso {p['numero_paso']}:** {p['descripcion']} → {p['colaborador']} ⏳ En espera")
                            else:
                                st.caption(f"**Paso {p['numero_paso']}:** {p['descripcion']} → {p['colaborador']} 🔒 Bloqueado")

                        if f["estado"] == "en_curso":
                            if st.button("🗑 Cancelar flujo", key=f"fdel_{f['id']}"):
                                with get_db() as db:
                                    db_execute(db, "DELETE FROM flujo_pasos WHERE flujo_id=%s", (f["id"],))
                                    db_execute(db, "DELETE FROM flujos WHERE id=%s", (f["id"],))
                                    db.commit()
                                st.rerun()

    # ── Gestión de colaboradores ──
    with tab_colabs:
        st.markdown("#### Agregar colaborador")
        with st.form("form_colab", clear_on_submit=True):
            fc1, fc2 = st.columns(2)
            with fc1:
                nuevo_nombre = st.text_input("Nombre de usuario")
            with fc2:
                nueva_pw = st.text_input("Contraseña", type="password")
            agregar = st.form_submit_button("Agregar", type="primary")

        if agregar:
            if not nuevo_nombre or not nueva_pw:
                st.error("Completá nombre y contraseña.")
            else:
                try:
                    with get_db() as db:
                        db_execute(db, "INSERT INTO usuarios (nombre, password_hash, rol) VALUES (%s, %s, 'usuario')",
                                   (nuevo_nombre.strip(), hash_pw(nueva_pw)))
                        log_audit(db, user["id"], "alta_usuario", f"Nuevo usuario: {nuevo_nombre}")
                    st.success(f"✓ '{nuevo_nombre}' creado.")
                    st.rerun()
                except Exception as e:
                    if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        st.error("Ese nombre de usuario ya existe.")
                    else:
                        st.error(f"Error: {e}")

        st.markdown("#### Colaboradores actuales")
        with get_db() as db:
            colabs = db_execute(db, "SELECT id, nombre, activo, creado_en FROM usuarios WHERE rol='usuario' ORDER BY nombre").fetchall()

        for c in colabs:
            col1, col2, col3, col4 = st.columns([3, 1, 1, 1])
            col1.write(f"**{c['nombre']}**")
            col2.write("✅ Activo" if c["activo"] else "❌ Inactivo")

            if c["activo"]:
                if col3.button("Desactivar", key=f"des_{c['id']}"):
                    with get_db() as db:
                        db_execute(db, "UPDATE usuarios SET activo=0 WHERE id=%s", (c["id"],))
                        log_audit(db, user["id"], "desactivar_usuario", c["nombre"])
                    st.rerun()
            else:
                if col3.button("Activar", key=f"act_{c['id']}"):
                    with get_db() as db:
                        db_execute(db, "UPDATE usuarios SET activo=1 WHERE id=%s", (c["id"],))
                        log_audit(db, user["id"], "activar_usuario", c["nombre"])
                    st.rerun()

            if col4.button("🔑 Reset", key=f"rst_{c['id']}"):
                with get_db() as db:
                    db_execute(db, "UPDATE usuarios SET password_hash=%s WHERE id=%s",
                               (hash_pw("123456"), c["id"]))
                    log_audit(db, user["id"], "reset_password", c["nombre"])
                st.info(f"Contraseña de '{c['nombre']}' reseteada a: **123456**")

    # ── Gestión de tareas ──
    with tab_tareas:
        st.markdown("#### Agregar tarea")
        with st.form("form_tarea", clear_on_submit=True):
            nombre_tarea = st.text_input("Nombre de la tarea")
            agregar_t = st.form_submit_button("Agregar", type="primary")

        if agregar_t:
            if not nombre_tarea:
                st.error("Ingresá un nombre.")
            else:
                try:
                    with get_db() as db:
                        db_execute(db, "INSERT INTO tareas (nombre) VALUES (%s)", (nombre_tarea.strip(),))
                        log_audit(db, user["id"], "alta_tarea", nombre_tarea)
                    st.success(f"✓ Tarea '{nombre_tarea}' creada.")
                    st.rerun()
                except Exception as e:
                    if "unique" in str(e).lower() or "duplicate" in str(e).lower():
                        st.error("Esa tarea ya existe.")
                    else:
                        st.error(f"Error: {e}")

        st.markdown("#### Tareas actuales")
        with get_db() as db:
            tareas_list = db_execute(db, "SELECT id, nombre, activa FROM tareas ORDER BY nombre").fetchall()

        for t in tareas_list:
            ct1, ct2, ct3 = st.columns([4, 1, 1])
            ct1.write(f"**{t['nombre']}**")
            ct2.write("✅" if t["activa"] else "❌")
            if t["activa"]:
                if ct3.button("Desactivar", key=f"dt_{t['id']}"):
                    with get_db() as db:
                        db_execute(db, "UPDATE tareas SET activa=0 WHERE id=%s", (t["id"],))
                        log_audit(db, user["id"], "desactivar_tarea", t["nombre"])
                    st.rerun()
            else:
                if ct3.button("Activar", key=f"at_{t['id']}"):
                    with get_db() as db:
                        db_execute(db, "UPDATE tareas SET activa=1 WHERE id=%s", (t["id"],))
                        log_audit(db, user["id"], "activar_tarea", t["nombre"])
                    st.rerun()

    # ── Metas ──
    with tab_metas:
        st.markdown("#### Definir meta por tarea")
        tareas = obtener_tareas_activas()

        if not tareas:
            st.info("Primero cargá tareas en la pestaña Tareas.")
        else:
            with st.form("form_meta", clear_on_submit=True):
                fm1, fm2, fm3 = st.columns(3)
                with fm1:
                    meta_tarea = st.selectbox("Tarea", list(tareas.keys()), key="meta_t")
                with fm2:
                    meta_cant = st.number_input("Objetivo de cantidad", min_value=1, step=1, value=10)
                with fm3:
                    meta_periodo = st.selectbox("Período", ["diario", "semanal", "mensual"])
                guardar_meta = st.form_submit_button("Guardar meta", type="primary")

            if guardar_meta:
                tarea_id = tareas[meta_tarea]
                with get_db() as db:
                    # Reemplazar si ya existe
                    db_execute(db, "DELETE FROM metas WHERE tarea_id=%s AND periodo=%s", (tarea_id, meta_periodo))
                    db_execute(db, "INSERT INTO metas (tarea_id, cantidad_objetivo, periodo) VALUES (%s,%s,%s)",
                               (tarea_id, meta_cant, meta_periodo))
                    log_audit(db, user["id"], "meta",
                              f"{meta_tarea}: {meta_cant} por {meta_periodo}")
                st.success("✓ Meta guardada.")
                st.rerun()

        st.markdown("#### Metas actuales")
        with get_db() as db:
            metas = db_execute(db, """
                SELECT m.id, t.nombre as tarea, m.cantidad_objetivo, m.periodo
                FROM metas m JOIN tareas t ON m.tarea_id = t.id
                ORDER BY t.nombre
            """).fetchall()

        if metas:
            for m in metas:
                cm1, cm2, cm3, cm4 = st.columns([3, 1, 1, 1])
                cm1.write(f"**{m['tarea']}**")
                cm2.write(f"{m['cantidad_objetivo']}")
                cm3.write(m["periodo"])
                if cm4.button("🗑", key=f"dm_{m['id']}"):
                    with get_db() as db:
                        db_execute(db, "DELETE FROM metas WHERE id=%s", (m["id"],))
                    st.rerun()
        else:
            st.info("No hay metas definidas.")

    # ── Configuración ──
    with tab_config:
        st.markdown("#### Parámetros generales")

        dias_actual = int(obtener_config("dias_editables") or 0)
        nuevo_dias = st.number_input(
            "Días anteriores que un usuario puede editar (0 = solo hoy)",
            min_value=0, max_value=30, value=dias_actual, step=1
        )
        dias_dash_actual = int(obtener_config("dias_dashboard") or 14)
        nuevo_dias_dash = st.number_input(
            "Días a mostrar en el gráfico del dashboard",
            min_value=1, max_value=90, value=dias_dash_actual, step=1
        )

        if st.button("Guardar configuración", type="primary"):
            with get_db() as db:
                db_execute(db, "UPDATE configuracion SET valor=%s WHERE clave='dias_editables'",
                           (str(nuevo_dias),))
                db_execute(db, "INSERT INTO configuracion (clave, valor) VALUES ('dias_dashboard', %s) ON CONFLICT (clave) DO UPDATE SET valor = EXCLUDED.valor",
                           (str(nuevo_dias_dash),))
                log_audit(db, user["id"], "config", f"dias_editables: {nuevo_dias}, dias_dashboard: {nuevo_dias_dash}")
            st.success("✓ Configuración guardada.")

        st.divider()
        st.markdown("#### Cambiar contraseña de admin")
        with st.form("form_pw_admin"):
            pw_actual = st.text_input("Contraseña actual", type="password")
            pw_nueva = st.text_input("Contraseña nueva", type="password")
            pw_conf = st.text_input("Confirmar contraseña nueva", type="password")
            cambiar = st.form_submit_button("Cambiar contraseña")

        if cambiar:
            if pw_nueva != pw_conf:
                st.error("Las contraseñas nuevas no coinciden.")
            elif len(pw_nueva) < 4:
                st.error("La contraseña debe tener al menos 4 caracteres.")
            else:
                with get_db() as db:
                    row = db_execute(db, "SELECT password_hash FROM usuarios WHERE id=%s", (user["id"],)).fetchone()
                    if row["password_hash"] != hash_pw(pw_actual):
                        st.error("La contraseña actual es incorrecta.")
                    else:
                        db_execute(db, "UPDATE usuarios SET password_hash=%s WHERE id=%s",
                                   (hash_pw(pw_nueva), user["id"]))
                        log_audit(db, user["id"], "cambio_password", "Admin cambió su contraseña")
                        st.success("✓ Contraseña actualizada.")

    # ── Exportar ──
    with tab_export:
        st.markdown("#### Exportar informes")

        ce1, ce2 = st.columns(2)
        with ce1:
            exp_desde = st.date_input("Desde", value=date.today() - timedelta(days=30), key="exp_d")
        with ce2:
            exp_hasta = st.date_input("Hasta", value=date.today(), key="exp_h")

        # Filtro por colaborador
        with get_db() as db:
            colabs_exp = db_execute(db, "SELECT id, nombre FROM usuarios WHERE rol='usuario' AND activo=1 ORDER BY nombre").fetchall()
        nombres_exp = ["Todos"] + [c["nombre"] for c in colabs_exp]
        filtro_colab = st.selectbox("Colaborador", nombres_exp, key="exp_colab")

        st.divider()

        # ── Excel ──
        st.markdown("##### 📊 Exportar a Excel")
        if st.button("Generar Excel", type="primary"):
            with get_db() as db:
                if filtro_colab == "Todos":
                    df_exp = pd.read_sql_query("""
                        SELECT u.nombre as Colaborador, r.fecha as Fecha, r.turno as Turno,
                               t.nombre as Tarea, r.cantidad as Cantidad,
                               r.observacion as Observacion, r.creado_en as Registrado
                        FROM registros r
                        JOIN usuarios u ON r.usuario_id = u.id
                        JOIN tareas t ON r.tarea_id = t.id
                        WHERE r.fecha BETWEEN %s AND %s
                        ORDER BY r.fecha DESC, u.nombre
                    """, db, params=(exp_desde.isoformat(), exp_hasta.isoformat()))
                    if not df_exp.empty: df_exp = fix_df_types(df_exp)
                else:
                    df_exp = pd.read_sql_query("""
                        SELECT u.nombre as Colaborador, r.fecha as Fecha, r.turno as Turno,
                               t.nombre as Tarea, r.cantidad as Cantidad,
                               r.observacion as Observacion, r.creado_en as Registrado
                        FROM registros r
                        JOIN usuarios u ON r.usuario_id = u.id
                        JOIN tareas t ON r.tarea_id = t.id
                        WHERE r.fecha BETWEEN %s AND %s AND u.nombre = %s
                        ORDER BY r.fecha DESC
                    """, db, params=(exp_desde.isoformat(), exp_hasta.isoformat(), filtro_colab))
                    if not df_exp.empty: df_exp = fix_df_types(df_exp)

            if df_exp.empty:
                st.info("No hay registros en este período.")
            else:
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
                    df_exp.to_excel(writer, index=False, sheet_name="Registros")
                    df_res = df_exp.groupby(["Colaborador", "Tarea"])["Cantidad"].sum().reset_index()
                    df_res.to_excel(writer, index=False, sheet_name="Resumen")

                st.download_button(
                    "📥 Descargar Excel",
                    data=buffer.getvalue(),
                    file_name=f"registros_{exp_desde}_{exp_hasta}.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
                st.success(f"Listo: {len(df_exp)} registros exportados.")

        st.divider()

        # ── PDF configurable ──
        st.markdown("##### 📄 Exportar informe PDF")
        st.caption("Elegí qué secciones querés incluir en el informe:")

        inc_resumen = st.checkbox("Resumen general (totales y promedios)", value=True, key="pdf_resumen")
        inc_por_colab = st.checkbox("Desglose por colaborador", value=True, key="pdf_colab")
        inc_por_tarea = st.checkbox("Desglose por tarea", value=True, key="pdf_tarea")
        inc_metas = st.checkbox("Cumplimiento de metas", value=True, key="pdf_metas")
        inc_detalle = st.checkbox("Detalle de registros (línea por línea)", value=False, key="pdf_detalle")
        inc_ranking = st.checkbox("Ranking de colaboradores", value=True, key="pdf_ranking")
        inc_evaluaciones = st.checkbox("🔒 Evaluaciones de desempeño (confidencial)", value=False, key="pdf_eval")
        titulo_pdf = st.text_input("Título del informe (opcional)", value="Informe de Producción", key="pdf_titulo")

        if st.button("Generar PDF", type="primary", key="btn_pdf"):
            # Obtener datos
            with get_db() as db:
                if filtro_colab == "Todos":
                    df_pdf = pd.read_sql_query("""
                        SELECT u.nombre as Colaborador, r.fecha as Fecha, r.turno as Turno,
                               t.nombre as Tarea, r.cantidad as Cantidad,
                               r.observacion as Observacion
                        FROM registros r
                        JOIN usuarios u ON r.usuario_id = u.id
                        JOIN tareas t ON r.tarea_id = t.id
                        WHERE r.fecha BETWEEN %s AND %s
                        ORDER BY r.fecha, u.nombre
                    """, db, params=(exp_desde.isoformat(), exp_hasta.isoformat()))
                    if not df_pdf.empty: df_pdf = fix_df_types(df_pdf)
                else:
                    df_pdf = pd.read_sql_query("""
                        SELECT u.nombre as Colaborador, r.fecha as Fecha, r.turno as Turno,
                               t.nombre as Tarea, r.cantidad as Cantidad,
                               r.observacion as Observacion
                        FROM registros r
                        JOIN usuarios u ON r.usuario_id = u.id
                        JOIN tareas t ON r.tarea_id = t.id
                        WHERE r.fecha BETWEEN %s AND %s AND u.nombre = %s
                        ORDER BY r.fecha
                    """, db, params=(exp_desde.isoformat(), exp_hasta.isoformat(), filtro_colab))
                    if not df_pdf.empty: df_pdf = fix_df_types(df_pdf)

                metas_db = db_execute(db, """
                    SELECT t.nombre as tarea, m.cantidad_objetivo, m.periodo
                    FROM metas m JOIN tareas t ON m.tarea_id = t.id
                """).fetchall()

            if df_pdf.empty:
                st.info("No hay registros en este período.")
            else:
                # Generar PDF
                class SafePDF(FPDF):
                    def cell(self, w=0, h=0, text="", *args, **kwargs):
                        return super().cell(w, h, pdf_safe(str(text)), *args, **kwargs)
                pdf = SafePDF()
                pdf.set_auto_page_break(auto=True, margin=20)
                pdf.add_page()

                # ── Portada / Encabezado ──
                pdf.set_fill_color(15, 23, 42)
                pdf.rect(0, 0, 210, 45, "F")
                pdf.set_text_color(255, 255, 255)
                pdf.set_font("Helvetica", "B", 22)
                pdf.set_y(12)
                pdf.cell(0, 10, titulo_pdf, ln=True, align="C")
                pdf.set_font("Helvetica", "", 11)
                periodo_txt = f"Periodo: {exp_desde.strftime('%d/%m/%Y')} al {exp_hasta.strftime('%d/%m/%Y')}"
                if filtro_colab != "Todos":
                    periodo_txt += f"  |  Colaborador: {filtro_colab}"
                pdf.cell(0, 8, periodo_txt, ln=True, align="C")
                pdf.set_text_color(0, 0, 0)
                pdf.ln(15)

                # Helper para encabezados de sección
                def seccion(titulo):
                    pdf.set_fill_color(37, 99, 235)
                    pdf.set_text_color(255, 255, 255)
                    pdf.set_font("Helvetica", "B", 12)
                    pdf.cell(0, 9, f"  {titulo}", ln=True, fill=True)
                    pdf.set_text_color(0, 0, 0)
                    pdf.ln(4)

                # Helper para tablas
                def tabla(encabezados, filas, anchos):
                    pdf.set_font("Helvetica", "B", 9)
                    pdf.set_fill_color(241, 245, 249)
                    pdf.set_draw_color(203, 213, 225)
                    for i, h in enumerate(encabezados):
                        pdf.cell(anchos[i], 8, h, border=1, fill=True, align="C")
                    pdf.ln()
                    pdf.set_font("Helvetica", "", 9)
                    alt = False
                    for fila in filas:
                        if pdf.get_y() > 265:
                            pdf.add_page()
                        if alt:
                            pdf.set_fill_color(248, 250, 252)
                        else:
                            pdf.set_fill_color(255, 255, 255)
                        for i, val in enumerate(fila):
                            alin = "R" if isinstance(val, (int, float)) else "L"
                            texto = f"{val:,.0f}" if isinstance(val, float) else str(val)
                            pdf.cell(anchos[i], 7, texto, border=1, fill=True, align=alin)
                        pdf.ln()
                        alt = not alt
                    pdf.ln(4)

                # ── Resumen general ──
                if inc_resumen:
                    seccion("Resumen General")
                    total = df_pdf["Cantidad"].sum()
                    dias = df_pdf["Fecha"].nunique()
                    personas = df_pdf["Colaborador"].nunique()
                    promedio = round(total / dias, 1) if dias else 0
                    registros_total = len(df_pdf)

                    pdf.set_font("Helvetica", "", 10)
                    datos_resumen = [
                        ("Total producido:", f"{total:,}"),
                        ("Registros:", f"{registros_total:,}"),
                        ("Dias con actividad:", str(dias)),
                        ("Colaboradores:", str(personas)),
                        ("Promedio diario:", str(promedio)),
                    ]
                    for etiq, val in datos_resumen:
                        pdf.set_font("Helvetica", "", 10)
                        pdf.cell(55, 7, etiq)
                        pdf.set_font("Helvetica", "B", 10)
                        pdf.cell(0, 7, val, ln=True)
                    pdf.ln(4)

                # ── Ranking ──
                if inc_ranking and filtro_colab == "Todos":
                    seccion("Ranking de Colaboradores")
                    df_rank = df_pdf.groupby("Colaborador")["Cantidad"].sum().reset_index()
                    df_rank = df_rank.sort_values("Cantidad", ascending=False).reset_index(drop=True)
                    df_rank.insert(0, "Pos.", range(1, len(df_rank) + 1))
                    filas = [list(r) for _, r in df_rank.iterrows()]
                    tabla(["Pos.", "Colaborador", "Cantidad Total"], filas, [15, 100, 50])

                # ── Por colaborador ──
                if inc_por_colab:
                    seccion("Desglose por Colaborador")
                    for nombre in sorted(df_pdf["Colaborador"].unique()):
                        df_c = df_pdf[df_pdf["Colaborador"] == nombre]
                        total_c = df_c["Cantidad"].sum()
                        dias_c = df_c["Fecha"].nunique()
                        prom_c = round(total_c / dias_c, 1) if dias_c else 0

                        pdf.set_font("Helvetica", "B", 10)
                        pdf.set_fill_color(226, 232, 240)
                        pdf.cell(0, 8, f"  {nombre}  -  Total: {total_c:,}  |  Dias: {dias_c}  |  Promedio: {prom_c}", ln=True, fill=True)
                        pdf.ln(2)

                        df_ct = df_c.groupby("Tarea")["Cantidad"].sum().reset_index()
                        df_ct = df_ct.sort_values("Cantidad", ascending=False)
                        filas = [[r["Tarea"], r["Cantidad"]] for _, r in df_ct.iterrows()]
                        tabla(["Tarea", "Cantidad"], filas, [120, 45])

                # ── Por tarea ──
                if inc_por_tarea:
                    seccion("Desglose por Tarea")
                    df_t = df_pdf.groupby("Tarea")["Cantidad"].sum().reset_index()
                    df_t = df_t.sort_values("Cantidad", ascending=False)
                    filas = [[r["Tarea"], r["Cantidad"]] for _, r in df_t.iterrows()]
                    tabla(["Tarea", "Cantidad Total"], filas, [120, 50])

                # ── Metas ──
                if inc_metas and metas_db:
                    seccion("Cumplimiento de Metas")
                    for meta in metas_db:
                        tarea_n = meta["tarea"]
                        objetivo = meta["cantidad_objetivo"]
                        periodo = meta["periodo"]
                        df_mt = df_pdf[df_pdf["Tarea"] == tarea_n]

                        if periodo == "diario" and not df_mt.empty:
                            df_dias = df_mt.groupby("Fecha")["Cantidad"].sum()
                            cumplidos = int((df_dias >= objetivo).sum())
                            total_dias = len(df_dias)
                            pct = round(cumplidos / total_dias * 100) if total_dias else 0
                            pdf.set_font("Helvetica", "B", 10)
                            pdf.cell(80, 7, tarea_n)
                            pdf.set_font("Helvetica", "", 10)
                            pdf.cell(0, 7, f"Meta {periodo}: {objetivo}  |  Cumplido: {pct}% ({cumplidos}/{total_dias} dias)", ln=True)
                        elif not df_mt.empty:
                            total_t = df_mt["Cantidad"].sum()
                            if periodo == "semanal":
                                n_periodos = max(1, (exp_hasta - exp_desde).days // 7)
                            else:
                                n_periodos = max(1, (exp_hasta - exp_desde).days // 30)
                            prom_periodo = round(total_t / n_periodos, 1)
                            pct = round(prom_periodo / objetivo * 100) if objetivo else 0
                            pdf.set_font("Helvetica", "B", 10)
                            pdf.cell(80, 7, tarea_n)
                            pdf.set_font("Helvetica", "", 10)
                            pdf.cell(0, 7, f"Meta {periodo}: {objetivo}  |  Promedio: {prom_periodo}  |  Cumplimiento: {pct}%", ln=True)
                    pdf.ln(4)

                # ── Detalle ──
                if inc_detalle:
                    seccion("Detalle de Registros")
                    filas = []
                    for _, r in df_pdf.iterrows():
                        filas.append([r["Fecha"], r["Colaborador"], r["Turno"], r["Tarea"],
                                      r["Cantidad"], str(r["Observacion"] or "")])
                    tabla(
                        ["Fecha", "Colaborador", "Turno", "Tarea", "Cant.", "Observacion"],
                        filas,
                        [22, 35, 18, 45, 16, 54]
                    )

                if inc_evaluaciones:
                    with get_db() as db:
                        if filtro_colab == "Todos":
                            evals_pdf = db_execute(db, """
                                SELECT u.nombre as colaborador, e.fecha, e.categoria, e.puntaje, e.observacion
                                FROM evaluaciones e JOIN usuarios u ON e.usuario_id = u.id
                                WHERE e.fecha BETWEEN %s AND %s
                                ORDER BY u.nombre, e.fecha DESC
                            """, (exp_desde.isoformat(), exp_hasta.isoformat())).fetchall()
                        else:
                            evals_pdf = db_execute(db, """
                                SELECT u.nombre as colaborador, e.fecha, e.categoria, e.puntaje, e.observacion
                                FROM evaluaciones e JOIN usuarios u ON e.usuario_id = u.id
                                WHERE u.nombre = %s AND e.fecha BETWEEN %s AND %s
                                ORDER BY e.fecha DESC
                            """, (filtro_colab, exp_desde.isoformat(), exp_hasta.isoformat())).fetchall()

                    if evals_pdf:
                        pdf.add_page()
                        seccion("Evaluaciones de Desempeño (CONFIDENCIAL)")
                        pdf.set_font("Helvetica", "I", 9)
                        pdf.set_text_color(200, 0, 0)
                        pdf.cell(0, 7, "DOCUMENTO CONFIDENCIAL - Solo para uso de la administración", ln=True)
                        pdf.set_text_color(0, 0, 0)
                        pdf.ln(4)

                        # Promedios por persona
                        df_ev = pd.DataFrame([dict(e) for e in evals_pdf])
                        prom_persona = df_ev.groupby("colaborador")["puntaje"].mean().reset_index()
                        prom_persona["puntaje"] = prom_persona["puntaje"].round(1)
                        prom_persona = prom_persona.sort_values("puntaje", ascending=True)
                        filas_prom = [[r["colaborador"], r["puntaje"]] for _, r in prom_persona.iterrows()]
                        tabla(["Colaborador", "Puntaje promedio"], filas_prom, [120, 50])

                        # Detalle
                        filas_ev = [[e["fecha"], e["colaborador"], e["categoria"],
                                     str(e["puntaje"]), str(e["observacion"] or "")[:60]]
                                    for e in evals_pdf]
                        tabla(["Fecha", "Colaborador", "Categoría", "Punt.", "Observación"],
                              filas_ev, [22, 35, 35, 14, 84])

                # ── Pie de página ──
                pdf.set_y(-15)
                pdf.set_font("Helvetica", "I", 8)
                pdf.set_text_color(150, 150, 150)
                pdf.cell(0, 10, f"Generado el {datetime.now().strftime('%d/%m/%Y %H:%M')}  |  Registro de Tareas", align="C")

                # Descargar
                pdf_bytes = pdf.output()
                st.download_button(
                    "📥 Descargar PDF",
                    data=bytes(pdf_bytes),
                    file_name=f"informe_{exp_desde}_{exp_hasta}.pdf",
                    mime="application/pdf"
                )
                st.success("✓ Informe PDF generado.")

    # ── Auditoría ──
    with tab_audit:
        st.markdown("#### Log de actividad")
        with get_db() as db:
            logs = db_execute(db, """
                SELECT a.fecha_hora, COALESCE(u.nombre, 'sistema') as usuario, a.accion, a.detalle
                FROM audit_log a LEFT JOIN usuarios u ON a.usuario_id = u.id
                ORDER BY a.fecha_hora DESC LIMIT 200
            """).fetchall()

        if logs:
            df_log = pd.DataFrame([dict(l) for l in logs])
            df_log.columns = ["Fecha/Hora", "Usuario", "Acción", "Detalle"]
            st.dataframe(df_log, use_container_width=True, hide_index=True)
        else:
            st.info("Sin actividad registrada.")


# ─────────────────────────────────────────────────
# Router principal
# ─────────────────────────────────────────────────
def main():
    if st.session_state.user is None:
        pantalla_login()
    else:
        # Barra lateral
        with st.sidebar:
            rol_label = "🛡️ Administrador" if st.session_state.user["rol"] == "admin" else "👤 Usuario"
            st.markdown(f"""
            <div style="text-align:center; padding: 1.5rem 0 0.5rem;">
                <div style="display:inline-block; background:linear-gradient(135deg,#3b82f6,#1d4ed8);
                            width:56px; height:56px; border-radius:14px; line-height:56px;
                            font-size:24px; color:white; font-weight:800; margin-bottom:0.6rem;
                            box-shadow: 0 4px 12px rgba(59,130,246,0.3);">
                    {st.session_state.user['nombre'][0].upper()}
                </div>
                <div style="font-size:1.15rem; font-weight:700; color:#f1f5f9;">{st.session_state.user['nombre']}</div>
                <div style="font-size:0.8rem; color:#94a3b8; margin-top:2px;">{rol_label}</div>
            </div>
            """, unsafe_allow_html=True)
            st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
            st.divider()
            if st.button("🚪 Cerrar sesión", use_container_width=True):
                with get_db() as db:
                    log_audit(db, st.session_state.user["id"], "logout", "Cerró sesión")
                st.session_state.user = None
                st.rerun()

        if st.session_state.user["rol"] == "admin":
            panel_admin()
        else:
            panel_usuario()


if __name__ == "__main__":
    main()
