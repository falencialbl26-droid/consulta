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

# Configuração da página
st.set_page_config(
    page_title="Pesquisador de Credores - Falência Unick",
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

# Cache para PDFs já baixados (evita baixar o mesmo PDF várias vezes)
PDF_CACHE = {}

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
    """Verifica se o nome da pasta contém o termo"""
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
# FUNÇÕES DE BUSCA (INCLUINDO PDFs)
# ============================================================================

def baixar_pdf_temporario(drive_service, file_id, file_name, temp_dir):
    """Baixa um PDF temporariamente para leitura"""
    try:
        # Verifica cache
        if file_id in PDF_CACHE:
            return PDF_CACHE[file_id]
        
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        temp_path = Path(temp_dir) / f"temp_{file_id}.pdf"
        with open(temp_path, 'wb') as f:
            f.write(fh.getvalue())
        
        # Salva no cache
        PDF_CACHE[file_id] = temp_path
        return temp_path
    except:
        return None

def pesquisar_em_pdf(drive_service, file_id, file_name, termo_busca, eh_documento, temp_dir):
    """Pesquisa em um único PDF"""
    pdf_path = baixar_pdf_temporario(drive_service, file_id, file_name, temp_dir)
    if not pdf_path:
        return False
    
    try:
        doc = fitz.open(pdf_path)
        termo_norm = normalizar_texto(termo_busca)
        termo_sem_sep = termo_norm.replace(' ', '').replace('_', '')
        termo_numeros = extrair_numeros(termo_busca) if eh_documento else None
        
        for page_num in range(min(5, len(doc))):  # Limita a 5 páginas
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

def encontrar_pasta_credor_pai(drive_service, folder_id, folder_name):
    """Sobe na árvore até encontrar a pasta do credor"""
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

def buscar_recursivamente(drive_service, folder_id, termo_busca, eh_documento, temp_dir, resultados, nivel=0):
    """
    Busca recursiva - entra em pastas sem padrão de credor para ler PDFs
    """
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
                    # Caso 1: Pasta com padrão de credor (tem CPF)
                    if eh_pasta_credor(file['name']):
                        # Verifica se o nome corresponde
                        if pasta_contem_termo(file['name'], termo_busca, eh_documento):
                            print(f"{indent}✅ Pasta credor encontrada: {file['name']}")
                            credor_id, credor_name = encontrar_pasta_credor_pai(drive_service, file['id'], file['name'])
                            if credor_id not in resultados:
                                resultados[credor_id] = credor_name
                        # Pasta credor que não corresponde -> IGNORA (não entra)
                        continue
                    
                    # Caso 2: Pasta SEM padrão de credor (precisa entrar e ler PDFs)
                    else:
                        # Primeiro verifica se o nome já corresponde
                        if pasta_contem_termo(file['name'], termo_busca, eh_documento):
                            print(f"{indent}✅ Pasta encontrada pelo nome: {file['name']}")
                            credor_id, credor_name = encontrar_pasta_credor_pai(drive_service, file['id'], file['name'])
                            if credor_id not in resultados:
                                resultados[credor_id] = credor_name
                        else:
                            # Entra na pasta e busca dentro (pode ter PDFs)
                            print(f"{indent}🔍 Entrando em: {file['name']}")
                            buscar_recursivamente(drive_service, file['id'], termo_busca, eh_documento, temp_dir, resultados, nivel + 1)
                
                elif file['name'].lower().endswith('.pdf'):
                    # Verifica se o PDF contém o termo
                    if pesquisar_em_pdf(drive_service, file['id'], file['name'], termo_busca, eh_documento, temp_dir):
                        print(f"{indent}📄 Encontrado no PDF: {file['name']}")
                        credor_id, credor_name = encontrar_pasta_credor_pai(drive_service, folder_id, file['name'])
                        if credor_id not in resultados:
                            resultados[credor_id] = credor_name
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
    except Exception as e:
        pass
    
    return resultados

def baixar_pasta_completa(drive_service, folder_id, folder_name, destino):
    """Baixa uma pasta completa do Drive"""
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
                    baixar_pasta_completa(drive_service, file['id'], file['name'], destino_path)
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
        return True
    except Exception as e:
        return False

def processar_pesquisa(drive_service, termo_busca, temp_dir, progress_callback=None):
    """Processa a pesquisa para um termo"""
    eh_documento = len(extrair_numeros(termo_busca)) in [11, 14]
    
    # Listar pastas base
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
        st.error(f"Erro ao listar pastas: {e}")
        return []
    
    resultados = {}
    
    for idx, pasta in enumerate(pastas_base):
        if progress_callback:
            progress_callback(idx + 1, len(pastas_base), pasta['name'])
        
        # Busca recursiva (entra em pastas sem padrão)
        buscar_recursivamente(drive_service, pasta['id'], termo_busca, eh_documento, temp_dir, resultados)
    
    # Baixar as pastas encontradas
    pastas_baixadas = []
    for folder_id, folder_name in resultados.items():
        if baixar_pasta_completa(drive_service, folder_id, folder_name, temp_dir):
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
st.markdown("### Falência Unick - Habilitação de Crédito")

with st.sidebar:
    st.markdown("## 📋 Regras de busca")
    st.markdown("""
    **Pastas com CPF (padrão credor):**
    - ✅ Nome corresponde → copia direto (rápido)
    - ❌ Nome não corresponde → IGNORA (não entra)
    
    **Pastas sem CPF:**
    - 🔍 Entra e lê PDFs para encontrar a busca
    """)
    
    st.markdown("---")
    st.markdown("### 💡 Exemplos:")
    st.code("""
01710455055, 08429728899
William Miranda, Albert
    """)

termos_input = st.text_area(
    "🔍 **Digite os termos separados por vírgula**",
    placeholder="Ex: 01710455055, William Miranda, Albert",
    height=100
)

if st.button("🔍 PESQUISAR", type="primary", use_container_width=True):
    if not termos_input.strip():
        st.error("❌ Digite pelo menos um termo!")
    else:
        termos = [t.strip() for t in termos_input.split(',') if t.strip()]
        
        st.info(f"📋 {len(termos)} termo(s) para pesquisar")
        
        with st.expander("📋 Ver termos"):
            for i, t in enumerate(termos, 1):
                st.write(f"{i}. {t}")
        
        drive_service = autenticar_drive()
        
        if not drive_service:
            st.error("❌ Erro ao conectar ao Google Drive")
        else:
            temp_dir = tempfile.mkdtemp()
            progress_bar = st.progress(0)
            status_text = st.empty()
            resultados_container = st.empty()
            
            todos_resultados = []
            
            for idx, termo in enumerate(termos):
                status_text.info(f"🔍 Pesquisando: {termo} ({idx+1}/{len(termos)})")
                progress_bar.progress(idx / len(termos))
                
                def atualizar_progresso(atual, total, pasta_atual):
                    percentual = (idx + (atual/total)) / len(termos)
                    progress_bar.progress(percentual)
                    status_text.info(f"📁 {pasta_atual} - {termo}")
                
                resultados = processar_pesquisa(drive_service, termo, temp_dir, atualizar_progresso)
                todos_resultados.extend(resultados)
            
            progress_bar.progress(1.0)
            status_text.success("✅ Pesquisa concluída!")
            
            if todos_resultados:
                resultados_container.success(f"✅ **ENCONTRADOS!** {len(todos_resultados)} resultado(s)")
                
                for item in todos_resultados:
                    with st.expander(f"📁 {item['pasta']} - Termo: {item['termo']}"):
                        st.write(f"**Caminho:** {item['caminho']}")
                
                zip_path = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
                with zipfile.ZipFile(zip_path.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                    for root, dirs, files in os.walk(temp_dir):
                        for file in files:
                            file_path = os.path.join(root, file)
                            arcname = os.path.relpath(file_path, temp_dir)
                            zipf.write(file_path, arcname)
                
                with open(zip_path.name, 'rb') as f:
                    st.download_button(
                        label="📥 BAIXAR RESULTADOS (ZIP)",
                        data=f,
                        file_name="resultados_encontrados.zip",
                        mime="application/zip",
                        use_container_width=True
                    )
                
                os.unlink(zip_path.name)
            else:
                resultados_container.warning("❌ Nenhum resultado encontrado")
            
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)