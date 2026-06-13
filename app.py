import streamlit as st
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
import fitz  # PyMuPDF
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
from datetime import datetime

# Configuração da página
st.set_page_config(
    page_title="Pesquisador de Credores",
    page_icon="🔍",
    layout="wide"
)

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================
FOLDER_ID = "1bSfx68JysAIY-iRH42MSNoUMXxF6U1MH"

PASTAS_BASE = [
    'RESULTADO_CAIXA_DE_ENTRADA',
    'RESULTADO_16-05',
    'RESULTADO_AGUARDA_DOC',
    'RESULTADO_ANALISADOS',
    'RESULTADO_COMPLEMENTACOES',
    'RESULTADO_IMPUGNACOES',
    'RESULTADO_SENTENCAS'
]

# ============================================================================
# AUTENTICAÇÃO
# ============================================================================

def autenticar_drive():
    try:
        credentials = service_account.Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=['https://www.googleapis.com/auth/drive.readonly']
        )
        return build('drive', 'v3', credentials=credentials)
    except Exception as e:
        st.error(f"❌ Erro de autenticação: {e}")
        return None

# ============================================================================
# FUNÇÕES DE NORMALIZAÇÃO
# ============================================================================

def normalizar_texto(texto):
    if not texto:
        return ""
    texto = texto.upper()
    texto = re.sub(r'[ÁÀÂÃÄ]', 'A', texto)
    texto = re.sub(r'[ÉÈÊË]', 'E', texto)
    texto = re.sub(r'[ÍÌÎÏ]', 'I', texto)
    texto = re.sub(r'[ÓÒÔÕÖ]', 'O', texto)
    texto = re.sub(r'[ÚÙÛÜ]', 'U', texto)
    texto = re.sub(r'Ç', 'C', texto)
    texto = re.sub(r'[-\.,;:]', ' ', texto)
    texto = re.sub(r'[^A-Z0-9\s_]', '', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()

def extrair_numeros(texto):
    return re.sub(r'[^0-9]', '', texto)

def pasta_contem_termo(nome_pasta, termo_busca, eh_documento):
    nome_norm = normalizar_texto(nome_pasta)
    busca_norm = normalizar_texto(termo_busca)
    
    if eh_documento:
        numeros_pasta = extrair_numeros(nome_pasta)
        numeros_busca = extrair_numeros(termo_busca)
        if numeros_busca in numeros_pasta:
            return True
    
    nome_sem_sep = nome_norm.replace(' ', '').replace('_', '')
    busca_sem_sep = busca_norm.replace(' ', '').replace('_', '')
    
    return busca_sem_sep in nome_sem_sep

def eh_pasta_credor(nome_pasta):
    numeros = extrair_numeros(nome_pasta)
    return len(numeros) >= 11

def eh_pasta_base(nome_pasta):
    return nome_pasta.upper() in [p.upper() for p in PASTAS_BASE]

# ============================================================================
# FUNÇÕES DE BUSCA
# ============================================================================

def baixar_pdf_temporario(drive_service, file_id, file_name, temp_dir, log_func=None):
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        temp_path = Path(temp_dir) / f"temp_{file_id}.pdf"
        with open(temp_path, 'wb') as f:
            f.write(fh.getvalue())
        return temp_path
    except Exception as e:
        if log_func:
            log_func(f"      ⚠️ Erro ao baixar {file_name}: {e}")
        return None

def pesquisar_em_pdf(drive_service, file_id, file_name, termo_busca, eh_documento, temp_dir, log_func=None):
    pdf_path = baixar_pdf_temporario(drive_service, file_id, file_name, temp_dir, log_func)
    if not pdf_path:
        return False
    
    try:
        doc = fitz.open(pdf_path)
        termo_norm = normalizar_texto(termo_busca)
        termo_sem_sep = termo_norm.replace(' ', '').replace('_', '')
        termo_numeros = extrair_numeros(termo_busca) if eh_documento else None
        
        for page_num in range(min(5, len(doc))):
            texto = doc[page_num].get_text()
            texto_norm = normalizar_texto(texto)
            texto_sem_sep = texto_norm.replace(' ', '').replace('_', '')
            
            if termo_sem_sep in texto_sem_sep:
                doc.close()
                return True
            
            if termo_numeros:
                texto_numeros = extrair_numeros(texto)
                if termo_numeros in texto_numeros:
                    doc.close()
                    return True
        
        doc.close()
        return False
    except:
        return False
    finally:
        try:
            os.unlink(pdf_path)
        except:
            pass

def encontrar_pasta_credor_pai(drive_service, folder_id, folder_name):
    current_id = folder_id
    current_name = folder_name
    
    for _ in range(10):
        if eh_pasta_credor(current_name):
            return current_id, current_name
        
        if not eh_pasta_base(current_name):
            return current_id, current_name
        
        try:
            response = drive_service.files().get(fileId=current_id, fields='parents').execute()
            parents = response.get('parents', [])
            if not parents:
                break
            parent_id = parents[0]
            
            parent_response = drive_service.files().get(fileId=parent_id, fields='id, name').execute()
            current_id = parent_response['id']
            current_name = parent_response['name']
        except:
            break
    
    return folder_id, folder_name

def buscar_recursivamente(drive_service, folder_id, termo_busca, eh_documento, temp_dir, resultados, log_func=None, nivel=0):
    indent = "  " * nivel
    try:
        page_token = None
        while True:
            response = drive_service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name, mimeType)',
                pageToken=page_token,
                pageSize=100
            ).execute()
            
            for file in response.get('files', []):
                if file['mimeType'] == 'application/vnd.google-apps.folder':
                    if eh_pasta_credor(file['name']):
                        if pasta_contem_termo(file['name'], termo_busca, eh_documento):
                            if log_func:
                                log_func(f"{indent}✅ Pasta credor encontrada: {file['name']}")
                            credor_id, credor_name = encontrar_pasta_credor_pai(drive_service, file['id'], file['name'])
                            if credor_id not in resultados:
                                resultados[credor_id] = credor_name
                        continue
                    else:
                        if pasta_contem_termo(file['name'], termo_busca, eh_documento):
                            if log_func:
                                log_func(f"{indent}✅ Pasta encontrada pelo nome: {file['name']}")
                            credor_id, credor_name = encontrar_pasta_credor_pai(drive_service, file['id'], file['name'])
                            if credor_id not in resultados:
                                resultados[credor_id] = credor_name
                        else:
                            if log_func:
                                log_func(f"{indent}🔍 Entrando em: {file['name']}")
                            buscar_recursivamente(drive_service, file['id'], termo_busca, eh_documento, temp_dir, resultados, log_func, nivel + 1)
                
                elif file['name'].lower().endswith('.pdf'):
                    if pesquisar_em_pdf(drive_service, file['id'], file['name'], termo_busca, eh_documento, temp_dir, log_func):
                        if log_func:
                            log_func(f"{indent}📄 Encontrado no PDF: {file['name']}")
                        credor_id, credor_name = encontrar_pasta_credor_pai(drive_service, folder_id, file['name'])
                        if credor_id not in resultados:
                            resultados[credor_id] = credor_name
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
    except Exception as e:
        if log_func:
            log_func(f"{indent}⚠️ Erro: {e}")
    
    return resultados

def baixar_pasta_completa(drive_service, folder_id, folder_name, destino, log_func=None):
    try:
        destino_path = Path(destino) / folder_name
        destino_path.mkdir(parents=True, exist_ok=True)
        
        page_token = None
        while True:
            response = drive_service.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name, mimeType)',
                pageToken=page_token
            ).execute()
            
            for file in response.get('files', []):
                if file['mimeType'] == 'application/vnd.google-apps.folder':
                    baixar_pasta_completa(drive_service, file['id'], file['name'], destino_path, log_func)
                else:
                    request = drive_service.files().get_media(fileId=file['id'])
                    fh = io.BytesIO()
                    downloader = MediaIoBaseDownload(fh, request)
                    done = False
                    while not done:
                        status, done = downloader.next_chunk()
                    
                    file_path = destino_path / file['name']
                    with open(file_path, 'wb') as f:
                        f.write(fh.getvalue())
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
        if log_func:
            log_func(f"   ✅ Pasta baixada: {folder_name}")
        return True
    except Exception as e:
        if log_func:
            log_func(f"   ❌ Erro ao baixar {folder_name}: {e}")
        return False

def processar_pesquisa(drive_service, termo_busca, temp_dir, log_func, progress_callback=None):
    eh_documento = len(extrair_numeros(termo_busca)) in [11, 14]
    
    if log_func:
        log_func(f"\n{'='*60}")
        log_func(f"🔍 Buscando: {termo_busca}")
        if eh_documento:
            log_func(f"📄 Detectado como: DOCUMENTO (CPF/CNPJ)")
        else:
            log_func(f"👤 Detectado como: NOME")
        log_func(f"{'='*60}")
    
    pastas_base = []
    try:
        page_token = None
        while True:
            response = drive_service.files().list(
                q=f"'{FOLDER_ID}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageToken=page_token
            ).execute()
            
            for file in response.get('files', []):
                pastas_base.append(file)
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
    except Exception as e:
        if log_func:
            log_func(f"❌ Erro ao listar pastas: {e}")
        return []
    
    if log_func:
        log_func(f"\n📁 Pastas base encontradas: {len(pastas_base)}")
        for pb in pastas_base:
            log_func(f"   • {pb['name']}")
    
    resultados = {}
    
    for idx, pasta in enumerate(pastas_base):
        if progress_callback:
            progress_callback(idx + 1, len(pastas_base), pasta['name'])
        
        if log_func:
            log_func(f"\n📂 Analisando: {pasta['name']}")
        
        buscar_recursivamente(drive_service, pasta['id'], termo_busca, eh_documento, temp_dir, resultados, log_func)
    
    if log_func:
        log_func(f"\n📊 Resultados encontrados: {len(resultados)}")
    
    pastas_baixadas = []
    for folder_id, folder_name in resultados.items():
        if log_func:
            log_func(f"\n📥 Baixando: {folder_name}")
        if baixar_pasta_completa(drive_service, folder_id, folder_name, temp_dir, log_func):
            pastas_baixadas.append({
                'termo': termo_busca,
                'pasta': folder_name,
                'caminho': str(Path(temp_dir) / folder_name)
            })
    
    return pastas_baixadas

# ============================================================================
# INTERFACE STREAMLIT
# ============================================================================

st.title("🔍 Pesquisador de Credores")
st.markdown("### Habilitação de Crédito")

with st.sidebar:
    st.markdown("## 📋 Regras de busca")
    st.markdown("""
    **Pastas com CPF (padrão credor):**
    - ✅ Nome corresponde → copia direto
    - ❌ Nome não corresponde → IGNORA
    
    **Pastas sem CPF:**
    - 🔍 Entra e lê PDFs
    """)
    
    st.markdown("---")
    st.markdown("### 💡 Exemplos:")
    st.code("""
012345678901, 09876543210
Marcos Moretti, Albert
    """)
    
    st.markdown("---")
    st.markdown(f"### 🕐 Última execução:")
    if 'ultima_execucao' in st.session_state:
        st.caption(st.session_state.ultima_execucao)
    else:
        st.caption("Nenhuma ainda")

# Entrada de termos
termos_input = st.text_area(
    "🔍 **Digite os termos separados por vírgula**",
    placeholder="Ex: 012345678901, Marcos Moretti, Albert",
    height=100
)

# Container para a área de progresso
progress_container = st.container()

if st.button("🔍 PESQUISAR", type="primary", use_container_width=True):
    if not termos_input.strip():
        st.error("❌ Digite pelo menos um termo!")
    else:
        termos = [t.strip() for t in termos_input.split(',') if t.strip()]
        
        with progress_container:
            st.markdown("---")
            st.markdown("## 🚀 Processando...")
            
            # Barra de progresso geral
            progress_bar_geral = st.progress(0)
            
            # Log em tempo real
            log_text = st.empty()
            
            # Status atual
            status_text = st.empty()
            
            # Resultados parciais
            resultados_text = st.empty()
        
        # Lista para acumular logs
        logs = []
        
        def adicionar_log(msg):
            logs.append(msg)
            # Mantém apenas as últimas 50 linhas para não sobrecarregar
            if len(logs) > 50:
                logs.pop(0)
            log_text.code("\n".join(logs), language=None)
        
        def atualizar_progresso(atual, total, pasta_atual):
            percentual = atual / total
            progress_bar_geral.progress(percentual)
            status_text.info(f"📁 Processando: {pasta_atual} ({atual}/{total})")
        
        def atualizar_resultados_parciais(encontrados):
            if encontrados:
                resultados_text.success(f"✅ Pastas encontradas até agora: {len(encontrados)}")
            else:
                resultados_text.info("🔍 Aguardando resultados...")
        
        # Registrar início
        adicionar_log(f"\n{'#'*70}")
        adicionar_log(f"🚀 INICIANDO PESQUISA - {datetime.now().strftime('%H:%M:%S')}")
        adicionar_log(f"{'#'*70}")
        adicionar_log(f"📋 Termos: {', '.join(termos)}")
        
        drive_service = autenticar_drive()
        
        if not drive_service:
            adicionar_log("❌ Erro ao conectar ao Google Drive")
            st.error("❌ Erro ao conectar ao Google Drive")
        else:
            adicionar_log("✅ Conectado ao Google Drive")
            
            # Criar diretório temporário
            temp_dir = tempfile.mkdtemp()
            adicionar_log(f"📁 Diretório temporário: {temp_dir}")
            
            todos_resultados = []
            
            for idx, termo in enumerate(termos):
                adicionar_log(f"\n{'='*60}")
                adicionar_log(f"🔍 Processando termo {idx+1}/{len(termos)}: {termo}")
                adicionar_log(f"{'='*60}")
                
                # Atualiza barra de progresso para este termo
                def termo_progresso(atual, total, pasta_atual):
                    percentual_geral = (idx + (atual/total)) / len(termos)
                    progress_bar_geral.progress(percentual_geral)
                    status_text.info(f"🔍 {termo} - {pasta_atual} ({atual}/{total})")
                    atualizar_resultados_parciais(todos_resultados)
                
                resultados = processar_pesquisa(drive_service, termo, temp_dir, adicionar_log, termo_progresso)
                todos_resultados.extend(resultados)
                
                adicionar_log(f"\n📊 Termo '{termo}': {len(resultados)} resultado(s)")
            
            progress_bar_geral.progress(1.0)
            
            # Resumo final
            adicionar_log(f"\n{'#'*70}")
            adicionar_log(f"✅ PESQUISA CONCLUÍDA - {datetime.now().strftime('%H:%M:%S')}")
            adicionar_log(f"{'#'*70}")
            adicionar_log(f"📊 TOTAL DE RESULTADOS: {len(todos_resultados)}")
            
            for item in todos_resultados:
                adicionar_log(f"   📁 {item['pasta']} (termo: {item['termo']})")
            
            status_text.success("✅ Pesquisa concluída!")
            
            if todos_resultados:
                resultados_text.success(f"✅ **ENCONTRADOS!** {len(todos_resultados)} resultado(s)")
                
                # Mostrar resultados expansíveis
                st.markdown("---")
                st.markdown("## 📋 Resultados encontrados")
                
                for item in todos_resultados:
                    with st.expander(f"📁 {item['pasta']} - Termo: {item['termo']}"):
                        st.write(f"**Caminho:** `{item['caminho']}`")
                
                # Botão de download
                st.markdown("---")
                st.markdown("## 📥 Download dos resultados")
                
                with st.spinner("Compactando arquivos..."):
                    zip_path = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
                    with zipfile.ZipFile(zip_path.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                        for root, dirs, files in os.walk(temp_dir):
                            for file in files:
                                file_path = os.path.join(root, file)
                                arcname = os.path.relpath(file_path, temp_dir)
                                zipf.write(file_path, arcname)
                    
                    # Salvar informações adicionais
                    info_path = Path(temp_dir) / "log_da_pesquisa.txt"
                    with open(info_path, 'w', encoding='utf-8') as f:
                        f.write("\n".join(logs))
                    zipf = zipfile.ZipFile(zip_path.name, 'a', zipfile.ZIP_DEFLATED)
                    zipf.write(info_path, "log_da_pesquisa.txt")
                    zipf.close()
                
                with open(zip_path.name, 'rb') as f:
                    st.download_button(
                        label="📥 BAIXAR RESULTADOS (ZIP)",
                        data=f,
                        file_name=f"resultados_pesquisa_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip",
                        mime="application/zip",
                        use_container_width=True
                    )
                
                # Limpar arquivo temporário
                os.unlink(zip_path.name)
                
                # Registrar no histórico
                st.session_state.ultima_execucao = datetime.now().strftime('%d/%m/%Y %H:%M:%S')
                with st.sidebar:
                    st.caption(f"✅ {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
            else:
                resultados_text.warning("❌ Nenhum resultado encontrado")
            
            # Limpar diretório temporário (opcional, mantém para debug)
            # import shutil
            # shutil.rmtree(temp_dir, ignore_errors=True)