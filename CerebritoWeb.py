#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CerebritoWeb - Sistema de Análisis de Llamadas
Versión 33.3 - Mejoras de coordenadas TELCEL/MOVISTAR
"""

import streamlit as st
import sqlite3
import os
import json
import bcrypt
import time
import random
import string
import hashlib
import io
import pandas as pd
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List
from collections import Counter as CounterLib
import matplotlib.pyplot as plt
from sqlalchemy import create_engine, text
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from io import BytesIO

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False

# ============================================================================
# CONFIGURACIÓN GLOBAL
# ============================================================================
APP_DIR = Path(__file__).parent
DB_USERS = APP_DIR / 'users_v17.db'
SESS_DIR = APP_DIR / '.sessions'
SESS_DIR.mkdir(exist_ok=True)

LOCAL_DB_PATH = os.environ.get('LOCAL_INFONAVIT_DB', 'infonavit.db')
TABLE_NAME = os.environ.get('INFONAVIT_TABLE', 'personas')
PHONE_COL = os.environ.get('INFONAVIT_PHONE_COL', 'TELEFONOCELULAR')
REMOTE_DATABASE_URL = os.environ.get('REMOTE_DATABASE_URL')
CACHE_DB = os.environ.get('SEARCH_CACHE_DB', 'search_index.db')

# ============================================================================
# MÓDULO: CONVERSIÓN DE COORDENADAS MEJORADA
# ============================================================================
def limpiar_texto_coordenada(texto):
    """
    Limpia texto de coordenada eliminando caracteres invisibles y espacios extras.
    Solo conserva: dígitos 0-9, punto '.', guion '-', y letras N,S,E,W,n,s,e,w
    """
    if pd.isna(texto):
        return ""
    
    # Convertir a string y strip
    texto = str(texto).strip()
    
    # Eliminar comillas dobles al inicio y final
    texto = texto.strip('"').strip("'")
    
    # Caracteres permitidos: dígitos, punto, guion, letras NSEW, y símbolos de grados/minutos
    permitidos = set('0123456789.-NSEWnsew°\'\"')
    
    # Filtrar solo caracteres permitidos
    texto_limpio = ''.join(c if c in permitidos else ' ' for c in texto)
    
    # Normalizar espacios múltiples
    texto_limpio = ' '.join(texto_limpio.split())
    
    return texto_limpio

def convertir_coord_telcel(coord_str):
    """
    Convierte coordenadas TELCEL formato: 25°45'49.6N" o 100°13'4.35W"
    Limpia caracteres invisibles y convierte a decimal.
    Retorna valor decimal o None si falla.
    """
    try:
        # Limpiar la cadena primero
        coord_limpia = limpiar_texto_coordenada(coord_str)
        
        if not coord_limpia:
            return None
        
        # Patrón flexible: grados°minutos'segundos.decimalesHEMISFERIO
        # Ejemplo: 25°45'49.6N o 100°13'4.35W
        patron = r"(\d+)[°\s]+(\d+)['\s]+([\d.]+)\s*([NSEWnsew])"
        match = re.search(patron, coord_limpia)
        
        if not match:
            return None
        
        grados = float(match.group(1))
        minutos = float(match.group(2))
        segundos = float(match.group(3))
        hemisferio = match.group(4).upper()
        
        # Convertir a decimal
        decimal = grados + (minutos / 60.0) + (segundos / 3600.0)
        
        # Aplicar signo según hemisferio
        if hemisferio in ['S', 'W']:
            decimal = -decimal
        
        return decimal
    
    except Exception:
        return None

def convertir_coord_movistar(coord_str):
    """
    Convierte coordenadas MOVISTAR formato: 25.655944000 o -100.385305000
    Limpia caracteres invisibles y convierte a float.
    """
    try:
        coord_limpia = limpiar_texto_coordenada(coord_str)
        
        if not coord_limpia:
            return None
        
        # Extraer solo dígitos, punto y guion
        coord_numerica = ''.join(c for c in coord_limpia if c in '0123456789.-')
        
        if not coord_numerica or coord_numerica == '-':
            return None
        
        return float(coord_numerica)
    
    except Exception:
        return None

def procesar_coordenadas_fila_mejorado(row, col_lat, col_lon, formato='MOVISTAR'):
    """
    Procesa una fila y retorna (lat, lon) según el formato.
    Versión mejorada con limpieza robusta de caracteres.
    """
    try:
        if formato == 'MOVISTAR':
            lat = convertir_coord_movistar(row.get(col_lat))
            lon = convertir_coord_movistar(row.get(col_lon))
            
            if lat is not None and lon is not None:
                # Validar rangos México
                if 14 <= lat <= 33 and -118 <= lon <= -86:
                    return (lat, lon)
        
        elif formato == 'TELCEL':
            lat = convertir_coord_telcel(row.get(col_lat))
            lon = convertir_coord_telcel(row.get(col_lon))
            
            if lat is not None and lon is not None:
                # Validar rangos México
                if 14 <= lat <= 33 and -118 <= lon <= -86:
                    return (lat, lon)
    
    except Exception:
        pass
    
    return (None, None)

# ============================================================================
# MÓDULO: OPENCELLID API (MEJORADO)
# ============================================================================
def parsear_codbts_mejorado(codbts_str):
    """
    Parsea formato CodBTS: 334-3-30312-1
    Limpia caracteres extra y extrae MCC, MNC, LAC, CellID
    """
    try:
        codbts_limpio = limpiar_texto_coordenada(str(codbts_str))
        
        # Eliminar guiones finales extra
        codbts_limpio = codbts_limpio.rstrip('-').strip()
        
        partes = codbts_limpio.split('-')
        
        if len(partes) >= 3:
            mcc = partes[0]  # 334 (México)
            mnc = partes[1]  # 3 (Movistar)
            cellid = partes[2]  # ID completo de la celda
            
            # LAC: primeros 5 dígitos del CellID
            lac = cellid[:5] if len(cellid) >= 5 else cellid
            
            return {
                'mcc': mcc,
                'mnc': mnc,
                'lac': lac,
                'cellid': cellid,
                'sector': partes[3] if len(partes) > 3 else None
            }
    except Exception:
        pass
    
    return None

def consultar_opencellid(mcc, mnc, lac, cell_id, token):
    """
    Consulta OpenCelliD para obtener coordenadas de una antena
    """
    if not REQUESTS_AVAILABLE:
        return {'status': 'error', 'detalle': 'requests no disponible'}
    
    try:
        url = "https://opencellid.org/cell/get"
        params = {
            'key': token,
            'mcc': mcc,
            'mnc': mnc,
            'lac': lac,
            'cellid': cell_id,
            'format': 'json'
        }
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'lat' in data and 'lon' in data:
                return {
                    'lat': float(data['lat']),
                    'lon': float(data['lon']),
                    'range': data.get('range', 0),
                    'samples': data.get('samples', 0),
                    'status': 'found'
                }
        
        return {'status': 'not_found'}
    
    except Exception as e:
        return {'status': 'error', 'detalle': str(e)}

def completar_coordenadas_opencellid(df, col_codbts, col_lat, col_lon, token, progress_callback=None):
    """
    Completa coordenadas faltantes usando OpenCelliD con barra de progreso
    """
    df_result = df.copy()
    stats = {
        'total_filas': len(df),
        'sin_coords': 0,
        'consultadas': 0,
        'encontradas': 0,
        'no_encontradas': 0,
        'errores': 0
    }
    
    # Convertir coordenadas a numérico
    df_result['lat_num'] = df_result[col_lat].apply(convertir_coord_movistar)
    df_result['lon_num'] = df_result[col_lon].apply(convertir_coord_movistar)
    
    mask_sin_coords = df_result['lat_num'].isna() | df_result['lon_num'].isna()
    stats['sin_coords'] = mask_sin_coords.sum()
    
    if col_codbts not in df_result.columns:
        return df_result, stats
    
    # Procesar filas sin coordenadas
    indices_sin_coords = df_result[mask_sin_coords].index.tolist()
    total_procesar = len(indices_sin_coords)
    
    for i, idx in enumerate(indices_sin_coords):
        codbts = df_result.at[idx, col_codbts]
        parsed = parsear_codbts_mejorado(codbts)
        
        if parsed:
            stats['consultadas'] += 1
            resultado = consultar_opencellid(
                parsed['mcc'],
                parsed['mnc'],
                parsed['lac'],
                parsed['cellid'],
                token
            )
            
            if resultado.get('status') == 'found':
                df_result.at[idx, col_lat] = resultado['lat']
                df_result.at[idx, col_lon] = resultado['lon']
                df_result.at[idx, 'lat_num'] = resultado['lat']
                df_result.at[idx, 'lon_num'] = resultado['lon']
                stats['encontradas'] += 1
            elif resultado.get('status') == 'not_found':
                stats['no_encontradas'] += 1
            else:
                stats['errores'] += 1
            
            # Callback de progreso con decimales
            if progress_callback and total_procesar > 0:
                progreso = ((i + 1) / total_procesar) * 100
                progress_callback(progreso, i + 1, total_procesar)
            
            time.sleep(0.1)
    
    return df_result, stats

# ============================================================================
# MÓDULO: CACHÉ Y BÚSQUEDA (SIN CAMBIOS)
# ============================================================================
def init_cache():
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    cur.execute('''
    CREATE TABLE IF NOT EXISTS searches (
        id INTEGER PRIMARY KEY,
        telefono TEXT UNIQUE,
        status TEXT,
        source TEXT,
        datos TEXT,
        last_seen TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )''')
    conn.commit()
    conn.close()

def get_cached(telefono):
    conn = sqlite3.connect(CACHE_DB)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    try:
        cur.execute('SELECT * FROM searches WHERE telefono=?', (telefono,))
        r = cur.fetchone()
    finally:
        conn.close()
    if r:
        return {
            'telefono': r['telefono'],
            'status': r['status'],
            'source': r['source'],
            'datos': json.loads(r['datos']) if r['datos'] else None
        }
    return None

def set_cached(telefono, status, source=None, datos=None):
    conn = sqlite3.connect(CACHE_DB)
    cur = conn.cursor()
    dstr = json.dumps(datos, ensure_ascii=False) if datos else None
    cur.execute(
        'INSERT OR REPLACE INTO searches (telefono,status,source,datos,last_seen) VALUES (?,?,?,?,CURRENT_TIMESTAMP)',
        (telefono, status, source, dstr)
    )
    conn.commit()
    conn.close()

def _digits(s):
    return ''.join(ch for ch in str(s) if ch.isdigit())

def buscar_numero_hibrido(telefono: str, timeout_seconds: int = 20) -> Dict:
    cached = get_cached(telefono)
    if cached:
        cached['cached'] = True
        return cached
    
    nd = _digits(telefono or '')
    if not nd:
        res = {'status': 'invalid', 'detalle': 'sin dígitos', 'telefono_querido': telefono}
        set_cached(telefono, res['status'])
        return res
    
    candidates = [nd] + ([nd[-10:]] if len(nd) > 10 else [])
    start = time.time()
    
    # Búsqueda en SQLite local
    try:
        if os.path.exists(LOCAL_DB_PATH):
            conn = sqlite3.connect(LOCAL_DB_PATH, timeout=5)
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            
            for cand in candidates:
                cur.execute(f"SELECT * FROM {TABLE_NAME} WHERE {PHONE_COL} = ? LIMIT 1", (cand,))
                row = cur.fetchone()
                if row:
                    datos = dict(row)
                    res = {
                        'status': 'encontrado',
                        'telefono_usado': cand,
                        'query_type': 'exact_sqlite',
                        'datos': datos,
                        'telefono_querido': telefono
                    }
                    set_cached(telefono, res['status'], 'local_sqlite', datos)
                    conn.close()
                    return res
                
                if time.time() - start > timeout_seconds:
                    set_cached(telefono, 'error_timeout')
                    conn.close()
                    return {'status': 'error_timeout', 'detalle': 'timeout sqlite', 'telefono_querido': telefono}
            
            conn.close()
    except Exception:
        pass
    
    # Búsqueda en PostgreSQL remoto
    if REMOTE_DATABASE_URL:
        try:
            engine = create_engine(REMOTE_DATABASE_URL, connect_args={'connect_timeout': timeout_seconds})
            with engine.connect() as conn:
                sfx = candidates[-1]
                q = text(f"SELECT * FROM {TABLE_NAME} WHERE {PHONE_COL} = :n LIMIT 1")
                r = conn.execute(q, {'n': sfx}).fetchone()
                if r:
                    datos = dict(r._mapping)
                    res = {
                        'status': 'encontrado',
                        'telefono_usado': sfx,
                        'query_type': 'exact_pg',
                        'datos': datos,
                        'telefono_querido': telefono
                    }
                    set_cached(telefono, res['status'], 'remote_pg', datos)
                    return res
        except Exception:
            pass
    
    set_cached(telefono, 'no_encontrado')
    return {'status': 'no_encontrado', 'telefono_querido': telefono}

# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================
def limpiar_numero(x):
    s = ''.join(ch for ch in str(x) if ch.isdigit()) if pd.notna(x) else ''
    if len(s) == 10:
        return s
    if len(s) > 10:
        return s[-10:]
    return None

def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()

def cargar_excel_multihoja(archivo_bytes, header_row=0, nrows=None):
    try:
        excel_file = pd.ExcelFile(io.BytesIO(archivo_bytes))
        dfs = []
        
        for sheet_name in excel_file.sheet_names:
            df_sheet = pd.read_excel(
                io.BytesIO(archivo_bytes),
                sheet_name=sheet_name,
                header=header_row,
                nrows=nrows
            )
            dfs.append(df_sheet)
        
        if dfs:
            df_unido = pd.concat(dfs, ignore_index=True)
            return df_unido
        
        return None
    except Exception as e:
        st.error(f"Error al cargar múltiples hojas: {str(e)}")
        return None

def generar_pdf_compact(items, meta):
    ded = {}
    for it in items:
        t = it['telefono']
        ded.setdefault(t, {
            'telefono': t,
            'count': 0,
            'status': it.get('status'),
            'datos': it.get('datos')
        })
        ded[t]['count'] += it.get('count', 0)
        if ded[t]['status'] != 'encontrado' and it.get('status') == 'encontrado':
            ded[t]['status'] = 'encontrado'
            ded[t]['datos'] = it.get('datos')
    
    buf = BytesIO()
    styles = getSampleStyleSheet()
    normal = styles['Normal']
    title = styles['Title']
    
    elems = [
        Paragraph('REPORTE FINAL - CerebritoWeb', title),
        Spacer(1, 6),
        Paragraph('Fecha: ' + datetime.utcnow().isoformat(), normal)
    ]
    
    rows = [['Teléfono', 'Veces', 'Status']] + [
        [v['telefono'], str(v['count']), v['status']] for v in ded.values()
    ]
    t = Table(rows, colWidths=[120, 80, 220])
    t.setStyle(TableStyle([('GRID', (0, 0), (-1, -1), 0.25, colors.grey)]))
    elems.append(t)
    elems.append(PageBreak())
    
    for v in ded.values():
        elems.append(Paragraph('Tel: ' + v['telefono'] + '  Veces: ' + str(v['count']), normal))
        if v['status'] == 'encontrado' and isinstance(v.get('datos'), dict):
            for k, val in v['datos'].items():
                elems.append(Paragraph(f"{k}: {val}", normal))
        else:
            elems.append(Paragraph('No encontrado en Infonavit2025', normal))
        elems.append(Spacer(1, 6))
    
    SimpleDocTemplate(buf, pagesize=letter).build(elems)
    buf.seek(0)
    return buf

# ============================================================================
# APLICACIÓN PRINCIPAL MEJORADA
# ============================================================================
def render_cerebrito_app(user=None):
    st.title('CerebritoWeb v33.3 - Analizador de Llamadas')
    
    archivo = st.file_uploader('Sube archivo (.csv/.xlsx)', type=['csv', 'xlsx'])
    if not archivo:
        st.info('Sube un archivo para comenzar.')
        return
    
    # Selector de formato de coordenadas
    formato_coords = st.radio(
        '🗺️ Formato de coordenadas del archivo:',
        ['MOVISTAR', 'TELCEL'],
        help='Selecciona el formato según tu compañía telefónica'
    )
    
    # OpenCellID solo para MOVISTAR
    usar_opencellid = False
    opencellid_token = "pk.ced254ae7c6a137affe17ca7366abc6f"
    
    if formato_coords == 'MOVISTAR':
        st.markdown("---")
        st.markdown("**🌐 OpenCelliD - Completar coordenadas faltantes**")
        st.info("Si tu archivo tiene columna CodBTS pero le faltan coordenadas, esta función las buscará automáticamente.")
        usar_opencellid = st.checkbox("Usar OpenCelliD para completar coordenadas", value=False)
    
    inicio = st.number_input('Fila de inicio (1 = primera fila de datos)', min_value=1, value=1)
    modo = st.radio('Modo de carga', ['Preview', 'Cargar X filas', 'Cargar todo'], index=0)
    filas_a = None
    if modo == 'Cargar X filas':
        filas_a = st.number_input('Máx filas a cargar', min_value=10, value=1000)
    elif modo == 'Cargar todo':
        filas_a = None
    
    mostrar_filas = st.number_input('Mostrar filas (preview)', min_value=1, max_value=1000, value=10)
    
    try:
        contenido = archivo.getvalue()
    except:
        contenido = archivo.read()
    
    sha = sha256_bytes(contenido)
    st.write('SHA256 del archivo:', sha)
    
    # Cargar archivo con barra de progreso
    progress_bar = st.progress(0.0)
    status_text = st.empty()
    
    try:
        status_text.text("Cargando archivo...")
        progress_bar.progress(0.10)
        
        header_row = inicio - 1 if inicio > 1 else 0
        if archivo.name.lower().endswith('.csv'):
            df = pd.read_csv(io.BytesIO(contenido), header=header_row, nrows=filas_a, low_memory=False)
        else:
            df = cargar_excel_multihoja(contenido, header_row=header_row, nrows=filas_a)
            if df is None:
                df = pd.read_excel(io.BytesIO(contenido), header=header_row, nrows=filas_a)
        
        progress_bar.progress(0.30)
        status_text.text("Archivo cargado correctamente")
    
    except Exception as e:
        st.error('Error leyendo el archivo: ' + str(e))
        return
    
    # Renombrar columnas a A, B, C, D...
    df_display = df.copy()
    letras = [chr(65 + i) if i < 26 else f"A{chr(65 + i - 26)}" for i in range(len(df.columns))]
    df_display.columns = letras[:len(df.columns)]
    
    progress_bar.progress(0.50)
    status_text.text("Preparando vista previa...")
    
    st.subheader('Vista previa')
    df_preview = df_display.head(mostrar_filas).copy()
    df_preview.index = range(1, len(df_preview) + 1)
    st.dataframe(df_preview, use_container_width=True)
    
    progress_bar.progress(1.0)
    status_text.text("✅ Listo para configurar")
    time.sleep(0.5)
    progress_bar.empty()
    status_text.empty()
    
    # Selección de columnas
    cols = list(df.columns)
    letras_opciones = letras[:len(cols)]
    
    def detectar(cols):
        return [c for c in cols if any(k in str(c).lower() for k in ('tel', 'cel', 'phone', 'movil'))]
    
    det = detectar(cols)
    ent_default = det[0] if det else (cols[0] if cols else None)
    sal_default = det[1] if len(det) > 1 else (cols[1] if len(cols) > 1 else ent_default)
    
    st.markdown('**Selecciona columnas:**')
    col_ent_letra = st.selectbox('Entrantes', letras_opciones, index=letras_opciones.index(letras[cols.index(ent_default)]) if ent_default in cols else 0)
    col_sal_letra = st.selectbox('Salientes', letras_opciones, index=letras_opciones.index(letras[cols.index(sal_default)]) if sal_default in cols else (1 if len(cols) > 1 else 0))
    col_fecha_letra = st.selectbox('Columna - Fecha (opcional)', [None] + letras_opciones, index=0)
    col_lat_letra = st.selectbox('Columna - Latitud', [None] + letras_opciones, index=0)
    col_lon_letra = st.selectbox('Columna - Longitud', [None] + letras_opciones, index=0)
    
    # Selector de Azimuth para TELCEL
    col_azimuth_letra = None
    if formato_coords == 'TELCEL':
        st.markdown("**🧭 Azimuth (TELCEL) - Dirección de antena**")
        st.info("Columna con grados 0-360 que indica hacia dónde apunta la antena (Norte=0°, Este=90°, Sur=180°, Oeste=270°)")
        col_azimuth_letra = st.selectbox('Columna - Azimuth (opcional)', [None] + letras_opciones, index=0)
    
    # Convertir letras a nombres reales
    col_ent = cols[letras_opciones.index(col_ent_letra)]
    col_sal = cols[letras_opciones.index(col_sal_letra)]
    col_fecha = cols[letras_opciones.index(col_fecha_letra)] if col_fecha_letra else None
    col_lat = cols[letras_opciones.index(col_lat_letra)] if col_lat_letra else None
    col_lon = cols[letras_opciones.index(col_lon_letra)] if col_lon_letra else None
    col_azimuth = cols[letras_opciones.index(col_azimuth_letra)] if col_azimuth_letra else None
    
    # Selector CodBTS para OpenCelliD
    col_codbts = None
    if usar_opencellid:
        col_codbts_letra = st.selectbox('Columna - CodBTS (Código de Antena)', [None] + letras_opciones, index=0)
        col_codbts = cols[letras_opciones.index(col_codbts_letra)] if col_codbts_letra else None
        
        if not col_codbts:
            st.warning("⚠️ Debes seleccionar la columna CodBTS para usar OpenCelliD")
    
    if 'search_cache' not in st.session_state:
        st.session_state['search_cache'] = {}
    
    if st.button('🚀 Analizar y Buscar (Top10)'):
        t0 = time.time()
        
        # Barra de progreso principal
        main_progress = st.progress(0.0)
        main_status = st.empty()
        
        main_status.text("01.00% - Iniciando análisis...")
        main_progress.progress(0.01)
        
        # OpenCellID si está activado
        if usar_opencellid and col_codbts and col_lat and col_lon:
            main_status.text("05.00% - 🌐 Consultando OpenCelliD...")
            main_progress.progress(0.05)
            
            opencellid_progress = st.progress(0.0)
            opencellid_status = st.empty()
            
            def opencellid_callback(progreso, actual, total):
                opencellid_status.text(f"{progreso:.2f}% - Procesando {actual}/{total} antenas")
                opencellid_progress.progress(progreso / 100.0)
            
            df, stats_opencellid = completar_coordenadas_opencellid(
                df, 
                col_codbts, 
                col_lat, 
                col_lon, 
                opencellid_token,
                progress_callback=opencellid_callback
            )
            
            opencellid_progress.empty()
            opencellid_status.empty()
            
            st.success(f"""
            **Resultados OpenCelliD:**
            - Total de filas: {stats_opencellid['total_filas']}
            - Sin coordenadas: {stats_opencellid['sin_coords']}
            - Consultadas: {stats_opencellid['consultadas']}
            - ✅ Encontradas: {stats_opencellid['encontradas']}
            - ❌ No encontradas: {stats_opencellid['no_encontradas']}
            - ⚠️ Errores: {stats_opencellid['errores']}
            """)
        
        main_status.text("15.00% - Limpiando números telefónicos...")
        main_progress.progress(0.15)
        
        entr = [limpiar_numero(x) for x in df[col_ent].astype(str).tolist()] if col_ent in df.columns else []
        sal = [limpiar_numero(x) for x in df[col_sal].astype(str).tolist()] if col_sal in df.columns else []
        entr = [x for x in entr if x]
        sal = [x for x in sal if x]
        
        main_status.text("25.00% - Calculando top 10...")
        main_progress.progress(0.25)
        
        top_ent = CounterLib(entr).most_common(10)
        top_sal = CounterLib(sal).most_common(10)
        
        main_status.text("35.00% - Generando gráficas...")
        main_progress.progress(0.35)
        
        if top_ent:
            st.subheader('Top 10 - Entrantes')
            df_ent = pd.DataFrame(top_ent, columns=['Número', 'Veces']).sort_values('Veces', ascending=True)
            fig, ax = plt.subplots(figsize=(7, 4))
            ys = list(df_ent['Número'].astype(str))
            vals = list(df_ent['Veces'])
            ax.barh(ys, vals)
            ax.set_title('Top 10 Entrantes')
            st.pyplot(fig)
        
        if top_sal:
            st.subheader('Top 10 - Salientes')
            df_sal = pd.DataFrame(top_sal, columns=['Número', 'Veces']).sort_values('Veces', ascending=True)
            fig2, ax2 = plt.subplots(figsize=(7, 4))
            ys2 = list(df_sal['Número'].astype(str))
            vals2 = list(df_sal['Veces'])
            ax2.barh(ys2, vals2)
            ax2.set_title('Top 10 Salientes')
            st.pyplot(fig2)
        
        main_status.text("45.00% - Analizando días pico...")
        main_progress.progress(0.45)
        
        def dia_pico(columna, label):
            if not columna or columna not in df.columns:
                return None
            tmp = df.copy()
            tmp['fecha_parsed'] = pd.to_datetime(tmp[columna], errors='coerce')
            tmp = tmp.dropna(subset=['fecha_parsed'])
            if tmp.empty:
                return None
            dias = tmp['fecha_parsed'].dt.day_name()
            res = dias.value_counts()
            dia = res.index[0]
            count = int(res.iloc[0])
            return f"En {label} el día {dia} hubo {count} llamadas (máximo)"
        
        pico_ent = dia_pico(col_fecha, 'Entrantes')
        pico_sal = dia_pico(col_fecha, 'Salientes')
        if pico_ent:
            st.info(pico_ent)
        if pico_sal:
            st.info(pico_sal)
        
        main_status.text("50.00% - Preparando búsqueda en Infonavit...")
        main_progress.progress(0.50)
        
        order = []
        for n, _ in top_ent:
            if n and n not in order:
                order.append(n)
        for n, _ in top_sal:
            if n and n not in order:
                order.append(n)
        
        st.subheader('Búsqueda en Infonavit (1x1)')
        placeholders = {n: st.empty() for n in order}
        barra = st.progress(0.0)
        busqueda_status = st.empty()
        resultados = []
        cache = st.session_state.get('search_cache', {})
        total = len(order) or 1
        
        for idx, n in enumerate(order, start=1):
            try:
                if n in cache:
                    res = cache[n]
                    res['_cached'] = True
                else:
                    res = buscar_numero_hibrido(n, timeout_seconds=20)
                    cache[n] = res
            except Exception as e:
                res = {'status': 'error', 'detalle': str(e)}
            
            ph = placeholders[n]
            if res.get('status') == 'encontrado':
                ph.success(f"{n} -> encontrado ({res.get('query_type')})")
            elif res.get('status') == 'no_encontrado':
                ph.info(f"{n} -> No encontrado en Infonavit2025")
            elif res.get('status') in ('error_timeout', 'error_remote'):
                ph.warning(f"{n} -> Timeout/Error: {res.get('detalle')}")
            else:
                ph.info(f"{n} -> {res.get('detalle')}")
            
            # Progreso con decimales
            percent = (idx / total)
            percent_display = 50.0 + (percent * 30.0)  # 50% a 80%
            barra.progress(percent)
            
            elapsed = time.time() - t0
            avg = elapsed / idx if idx > 0 else 0
            rem = avg * (total - idx)
            
            def ms(secs):
                m = int(secs // 60)
                s = int(secs % 60)
                return f"{m:02d}:{s:02d}"
            
            busqueda_status.text(f'{percent_display:.2f}% - Procesados: {idx}/{total} - Transcurrido: {ms(elapsed)} - Restante: {ms(rem)}')
            resultados.append({
                'telefono': n,
                'count': dict(top_ent + top_sal).get(n, 0),
                'status': res.get('status'),
                'datos': res.get('datos') if res.get('status') == 'encontrado' else None
            })
        
        st.session_state['search_cache'] = cache
        
        main_status.text("80.00% - Procesando coordenadas...")
        main_progress.progress(0.80)
        
        # ==================== MAPAS INTERACTIVOS MEJORADOS ====================
        if col_lat and col_lon:
            st.divider()
            st.header('🗺️ MAPAS INTERACTIVOS - TOP 10 NÚMEROS')
            
            map_progress = st.progress(0.0)
            map_status = st.empty()
            
            # Obtener Top 10 únicos
            top_10_numeros = []
            for num, count in (top_ent + top_sal):
                if num and len(num) == 10 and num not in top_10_numeros:
                    top_10_numeros.append(num)
                if len(top_10_numeros) >= 10:
                    break
            
            map_status.text("85.00% - Extrayendo coordenadas...")
            map_progress.progress(0.85)
            
            # Procesar coordenadas para cada número
            coords_por_numero = {}
            total_nums = len(top_10_numeros)
            
            for num_idx, numero in enumerate(top_10_numeros):
                coords_list = []
                
                # Buscar en ambas columnas
                for idx, row in df.iterrows():
                    num_ent = limpiar_numero(row.get(col_ent))
                    num_sal = limpiar_numero(row.get(col_sal))
                    
                    if num_ent == numero or num_sal == numero:
                        lat, lon = procesar_coordenadas_fila_mejorado(row, col_lat, col_lon, formato_coords)
                        
                        # Agregar información de Azimuth si está disponible (TELCEL)
                        azimuth_valor = None
                        if col_azimuth and formato_coords == 'TELCEL':
                            try:
                                azimuth_str = str(row.get(col_azimuth, '')).strip()
                                azimuth_valor = float(azimuth_str)
                                if not (0 <= azimuth_valor <= 360):
                                    azimuth_valor = None
                            except:
                                azimuth_valor = None
                        
                        if lat and lon:
                            coords_list.append({
                                'lat': lat,
                                'lon': lon,
                                'azimuth': azimuth_valor
                            })
                
                if coords_list:
                    # Obtener la coordenada más repetida
                    coords_tuplas = [(c['lat'], c['lon']) for c in coords_list]
                    coords_counter = CounterLib(coords_tuplas)
                    coord_mas_repetida, repeticiones = coords_counter.most_common(1)[0]
                    
                    # Calcular azimuth promedio si existe
                    azimuths = [c['azimuth'] for c in coords_list if c['azimuth'] is not None]
                    azimuth_promedio = sum(azimuths) / len(azimuths) if azimuths else None
                    
                    coords_por_numero[numero] = {
                        'lat': coord_mas_repetida[0],
                        'lon': coord_mas_repetida[1],
                        'repeticiones': repeticiones,
                        'azimuth': azimuth_promedio
                    }
                
            # Actualizar progreso con decimales
                prog = 85.0 + ((num_idx + 1) / total_nums) * 10.0
                map_status.text(f"{prog:.2f}% - Procesando coordenadas {num_idx + 1}/{total_nums}")
                map_progress.progress(prog / 100.0)
            
            map_progress.empty()
            map_status.empty()
            
            st.success(f'✅ Se encontraron {len(coords_por_numero)} números con coordenadas válidas')
            
            main_status.text("95.00% - Generando mapas interactivos...")
            main_progress.progress(0.95)
            
            # Función para obtener dirección cardinal desde azimuth
            def azimuth_a_direccion(azimuth):
                if azimuth is None:
                    return "N/D"
                
                direcciones = [
                    (0, 22.5, "Norte"),
                    (22.5, 67.5, "Noreste"),
                    (67.5, 112.5, "Este"),
                    (112.5, 157.5, "Sureste"),
                    (157.5, 202.5, "Sur"),
                    (202.5, 247.5, "Suroeste"),
                    (247.5, 292.5, "Oeste"),
                    (292.5, 337.5, "Noroeste"),
                    (337.5, 360, "Norte")
                ]
                
                for min_ang, max_ang, direccion in direcciones:
                    if min_ang <= azimuth < max_ang:
                        return f"{direccion} ({azimuth:.1f}°)"
                
                return f"{azimuth:.1f}°"
            
            # Mostrar mapas individuales
            for idx, numero in enumerate(top_10_numeros, 1):
                if numero in coords_por_numero:
                    coord_info = coords_por_numero[numero]
                    lat = coord_info['lat']
                    lon = coord_info['lon']
                    repeticiones = coord_info['repeticiones']
                    azimuth = coord_info.get('azimuth')
                    
                    with st.expander(f"📍 {idx}. Número: {numero} (Aparece {repeticiones} veces)", expanded=(idx == 1)):
                        col1, col2 = st.columns([3, 1])
                        
                        with col1:
                            maps_url = f"https://www.google.com/maps?q={lat},{lon}&output=embed"
                            st.components.v1.iframe(maps_url, height=400, scrolling=False)
                        
                        with col2:
                            st.metric("📞 Número", numero)
                            st.metric("📍 Latitud", f"{lat:.6f}")
                            st.metric("🗺️ Longitud", f"{lon:.6f}")
                            st.metric("🔢 Repeticiones", repeticiones)
                            
                            if azimuth is not None:
                                st.metric("🧭 Dirección Antena", azimuth_a_direccion(azimuth))
                            
                            street_view_url = f"https://www.google.com/maps/@?api=1&map_action=pano&viewpoint={lat},{lon}"
                            maps_search_url = f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"
                            
                            st.markdown(
                                f'<a href="{street_view_url}" target="_blank" style="display: inline-block; padding: 10px 20px; background-color: #4285F4; color: white; text-decoration: none; border-radius: 5px; text-align: center; font-weight: bold; margin-top: 10px;">📸 Street View</a>',
                                unsafe_allow_html=True
                            )
                            st.markdown(
                                f'<a href="{maps_search_url}" target="_blank" style="display: inline-block; padding: 10px 20px; background-color: #34A853; color: white; text-decoration: none; border-radius: 5px; text-align: center; font-weight: bold; margin-top: 10px;">🗺️ Google Maps</a>',
                                unsafe_allow_html=True
                            )
                else:
                    st.info(f"ℹ️ {numero}: Sin coordenadas válidas")
        
        main_status.text("100.00% - ✅ Análisis completado")
        main_progress.progress(1.0)
        time.sleep(0.5)
        main_progress.empty()
        main_status.empty()
        
        # ==================== FIN MAPAS ====================
        
        st.session_state['last_analysis'] = {
            'top_ent': top_ent,
            'top_sal': top_sal,
            'resultados': resultados,
            'filename': getattr(archivo, 'name', 'upload'),
            'sha256': sha
        }
        st.success('✅ Análisis completado correctamente.')
    
    # Botón para generar PDF
    if st.session_state.get('last_analysis') and st.button('📄 Generar PDF final'):
        if user and user.get('credits', 0) > 0:
            la = st.session_state['last_analysis']
            
            with st.spinner('Generando PDF...'):
                pdf_progress = st.progress(0.0)
                pdf_status = st.empty()
                
                pdf_status.text("10.00% - Preparando documento...")
                pdf_progress.progress(0.10)
                
                buf = generar_pdf_compact(la['resultados'], {'filename': la['filename'], 'sha256': la['sha256']})
                
                pdf_status.text("80.00% - Compilando PDF...")
                pdf_progress.progress(0.80)
                
                # DESCONTAR CRÉDITO
                conn = sqlite3.connect(DB_USERS)
                cur = conn.cursor()
                cur.execute('UPDATE users SET credits=credits-1 WHERE id=?', (user['id'],))
                conn.commit()
                conn.close()
                
                pdf_status.text("100.00% - ✅ PDF generado")
                pdf_progress.progress(1.0)
                time.sleep(0.3)
                pdf_progress.empty()
                pdf_status.empty()
            
            st.success('✅ PDF generado. Crédito descontado.')
            st.download_button('⬇️ Descargar PDF', data=buf.getvalue(), file_name='CerebritoWeb_Reporte.pdf', mime='application/pdf')
        else:
            st.error('❌ Sin créditos suficientes. Contacta al administrador.')

# ============================================================================
# MÓDULO: GESTIÓN DE USUARIOS (SIN CAMBIOS)
# ============================================================================
def init_db():
    conn = sqlite3.connect(DB_USERS)
    cur = conn.cursor()
    cur.execute('''CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY,
        username TEXT UNIQUE,
        password_hash TEXT,
        email TEXT,
        role TEXT,
        verified INTEGER DEFAULT 0,
        verify_code TEXT,
        credits INTEGER DEFAULT 0,
        created_at TEXT
    )''')
    conn.commit()
    cur.execute('SELECT COUNT(1) FROM users')
    if cur.fetchone()[0] == 0:
        pw = bcrypt.hashpw('1234'.encode(), bcrypt.gensalt()).decode()
        cur.execute(
            'INSERT INTO users (username,password_hash,email,role,verified,credits,created_at) VALUES (?,?,?,?,?,?,?)',
            ('admin', pw, 'admin@example.com', 'superadmin', 1, 999999, datetime.utcnow().isoformat())
        )
        conn.commit()
    conn.close()

def get_user(username):
    conn = sqlite3.connect(DB_USERS)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE username=?', (username,))
    r = cur.fetchone()
    conn.close()
    return dict(r) if r else None

def get_user_by_id(uid):
    conn = sqlite3.connect(DB_USERS)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM users WHERE id=?', (uid,))
    r = cur.fetchone()
    conn.close()
    return dict(r) if r else None

def create_verify_code():
    return ''.join(random.choices(string.digits, k=6))

def save_session_file(uid):
    p = SESS_DIR / f'session_{uid}.json'
    p.write_text(json.dumps({'user_id': uid, 'created': int(time.time())}), encoding='utf-8')

def load_session_any():
    for p in SESS_DIR.glob('session_*.json'):
        try:
            d = json.loads(p.read_text(encoding='utf-8'))
            uid = d.get('user_id')
            if uid:
                return uid
        except Exception:
            pass
    return None

def confirmar_usuario_panel(admin_mode=False):
    if admin_mode:
        st.subheader('Confirmar Usuario')
    
    cu = st.text_input('Usuario a confirmar', key='cf_user_admin' if admin_mode else 'cf_user')
    ccode = st.text_input('Código (6 dígitos)', key='cf_code_admin' if admin_mode else 'cf_code')
    
    if st.button('Confirmar', key='btn_confirm_admin' if admin_mode else 'btn_confirm'):
        u = get_user(cu)
        if not u:
            st.error('Usuario no encontrado.')
        elif u.get('verified', 0):
            st.info('Usuario ya verificado.')
        elif str(u.get('verify_code')) == str(ccode):
            conn = sqlite3.connect(DB_USERS)
            cur = conn.cursor()
            cur.execute('UPDATE users SET verified=1, verify_code=NULL WHERE username=?', (cu,))
            conn.commit()
            conn.close()
            st.success('Cuenta verificada correctamente.')
        else:
            st.error('Código incorrecto.')

def cambiar_password(uid):
    st.subheader('Cambiar contraseña')
    
    with st.form(key=f'form_cambio_pw_{uid}'):
        old = st.text_input('Contraseña actual', type='password')
        new = st.text_input('Nueva contraseña', type='password')
        new_confirm = st.text_input('Confirmar nueva contraseña', type='password')
        submitted = st.form_submit_button('Cambiar contraseña')
        
        if submitted:
            if not old or not new or not new_confirm:
                st.error('Completa todos los campos.')
            elif new != new_confirm:
                st.error('Las contraseñas nuevas no coinciden.')
            else:
                u = get_user_by_id(uid)
                if not bcrypt.checkpw(old.encode(), u['password_hash'].encode()):
                    st.error('Contraseña actual incorrecta.')
                else:
                    ph = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
                    conn = sqlite3.connect(DB_USERS)
                    cur = conn.cursor()
                    cur.execute('UPDATE users SET password_hash=? WHERE id=?', (ph, uid))
                    conn.commit()
                    conn.close()
                    st.success('Contraseña cambiada correctamente.')

def admin_panel(u):
    st.header('Panel de Administración - CerebritoWeb v33.3')
    
    conn = sqlite3.connect(DB_USERS)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute('SELECT * FROM users ORDER BY id')
    rows = cur.fetchall()
    
    st.subheader('Usuarios registrados')
    for r in rows:
        cols = st.columns([2, 1, 1, 1, 1, 1])
        cols[0].markdown(f"**{r['username']}** ({r['role']}) - {r['email']}")
        cols[1].markdown(f"Créditos: {r['credits']}")
        
        if cols[2].button(f"+1_{r['id']}", key=f"add1_{r['id']}"):
            cur.execute('UPDATE users SET credits=credits+1 WHERE id=?', (r['id'],))
            conn.commit()
            st.success('Crédito añadido.')
            st.rerun()
        
        if cols[3].button(f"-1_{r['id']}", key=f"sub1_{r['id']}"):
            cur.execute('UPDATE users SET credits=CASE WHEN credits>=1 THEN credits-1 ELSE 0 END WHERE id=?', (r['id'],))
            conn.commit()
            st.success('Crédito descontado.')
            st.rerun()
        
        if r['verified'] == 0 and cols[4].button(f"✓_{r['id']}", key=f"verify_{r['id']}"):
            cur.execute('UPDATE users SET verified=1, verify_code=NULL WHERE id=?', (r['id'],))
            conn.commit()
            st.success('Usuario verificado.')
            st.rerun()
        
        if st.session_state.get('role') == 'superadmin' and r['username'] != 'admin':
            if cols[5].button(f"❌_{r['id']}", key=f"del_{r['id']}"):
                cur.execute('DELETE FROM users WHERE id=?', (r['id'],))
                conn.commit()
                st.success('Usuario eliminado.')
                st.rerun()
    
    conn.close()
    
    st.markdown('---')
    confirmar_usuario_panel(admin_mode=True)
    
    st.markdown('---')
    st.subheader('Crear usuario')
    
    with st.form('crear_usr'):
        nombre = st.text_input('Usuario')
        email = st.text_input('Email')
        pw = st.text_input('Contraseña', type='password')
        rol = st.selectbox('Rol', ['user', 'admin'] if st.session_state.get('role') == 'superadmin' else ['user'])
        credits = st.number_input('Créditos iniciales', min_value=0, value=0)
        if st.form_submit_button('Crear'):
            if get_user(nombre):
                st.error('Usuario ya existe.')
            else:
                code = create_verify_code()
                ph = bcrypt.hashpw(pw.encode(), bcrypt.gensalt()).decode()
                conn = sqlite3.connect(DB_USERS)
                cur = conn.cursor()
                cur.execute(
                    'INSERT INTO users (username,password_hash,email,role,verified,verify_code,credits,created_at) VALUES (?,?,?,?,?,?,?,?)',
                    (nombre, ph, email, rol, 0, code, credits, datetime.utcnow().isoformat())
                )
                conn.commit()
                conn.close()
                st.success(f'Usuario creado. Código de verificación: {code}')

# ============================================================================
# APLICACIÓN PRINCIPAL
# ============================================================================
def main():
    st.set_page_config(page_title='CerebritoWeb v33.3', layout='wide')
    
    init_cache()
    init_db()
    
    if 'user_id' not in st.session_state:
        uid = load_session_any()
        if uid:
            u = get_user_by_id(uid)
            if u:
                st.session_state['user_id'] = u['id']
                st.session_state['username'] = u['username']
                st.session_state['role'] = u['role']
    
    st.session_state.setdefault('user_id', None)
    st.session_state.setdefault('username', None)
    st.session_state.setdefault('role', None)
    
    if st.session_state['user_id'] is None:
        st.title('CerebritoWeb v33.3 - Iniciar sesión')
        c1, c2 = st.columns(2)
        
        with c1:
            st.subheader('Iniciar sesión')
            lu = st.text_input('Usuario', key='login_u')
            lp = st.text_input('Contraseña', type='password', key='login_p')
            if st.button('Entrar'):
                user = get_user(lu)
                if not user:
                    st.error('Usuario no encontrado.')
                elif not bcrypt.checkpw(lp.encode(), user['password_hash'].encode()):
                    st.error('Contraseña inválida.')
                elif not user.get('verified', 0):
                    st.warning('Cuenta no verificada. Use el código de 6 dígitos.')
                else:
                    st.session_state['user_id'] = user['id']
                    st.session_state['username'] = user['username']
                    st.session_state['role'] = user['role']
                    save_session_file(user['id'])
                    st.success('Bienvenido ' + user['username'])
                    st.rerun()
            
            st.markdown('---')
            confirmar_usuario_panel(admin_mode=False)
        
        with c2:
            st.subheader('Registrar nueva cuenta')
            ru = st.text_input('Usuario nuevo', key='reg_u')
            re = st.text_input('Email', key='reg_e')
            rp = st.text_input('Contraseña', type='password', key='reg_p')
            if st.button('Registrar'):
                if not ru or not re or not rp:
                    st.error('Completa los campos.')
                elif get_user(ru):
                    st.error('Usuario existe.')
                else:
                    code = create_verify_code()
                    ph = bcrypt.hashpw(rp.encode(), bcrypt.gensalt()).decode()
                    conn = sqlite3.connect(DB_USERS)
                    cur = conn.cursor()
                    cur.execute(
                        'INSERT INTO users (username,password_hash,email,role,verified,verify_code,credits,created_at) VALUES (?,?,?,?,?,?,?,?)',
                        (ru, ph, re, 'user', 0, code, 0, datetime.utcnow().isoformat())
                    )
                    conn.commit()
                    conn.close()
                    st.success(f'Usuario creado. Código de verificación: {code}')
    
    else:
        u = get_user_by_id(st.session_state['user_id'])
        st.sidebar.write(f"Conectado: {u['username']} ({u['role']})")
        st.sidebar.write(f"Créditos: {u['credits']}")
        
        if st.sidebar.button('Cerrar sesión'):
            uid = st.session_state.get('user_id')
            for p in SESS_DIR.glob(f"session_{uid}*.json"):
                try:
                    p.unlink()
                except:
                    pass
            st.session_state['user_id'] = None
            st.session_state['username'] = None
            st.session_state['role'] = None
            st.rerun()
        
        if st.sidebar.button('Cambiar contraseña'):
            st.session_state['show_cambio_pw'] = True
        
        if st.session_state.get('show_cambio_pw', False):
            cambiar_password(st.session_state['user_id'])
            if st.button('Cerrar cambio de contraseña'):
                st.session_state['show_cambio_pw'] = False
                st.rerun()
        else:
            nav = st.sidebar.radio(
                'Secciones',
                ['Aplicación', 'Panel Admin'] if u['role'] in ('admin', 'superadmin') else ['Aplicación']
            )
            
            if nav == 'Aplicación':
                render_cerebrito_app(user=u)
            else:
                admin_panel(u)

if __name__ == '__main__':
    main()