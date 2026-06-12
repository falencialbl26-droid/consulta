import streamlit as st
import os
import re
import tempfile
import zipfile
from pathlib import Path
import fitz  # PyMuPDF
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io

# Configuração
st.set_page_config(page_title="Pesquisador de Credores", layout="wide")

FOLDER_ID = "1bSfx68JysAIY-iRH42MSNoUMXxF6U1MH"

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
        st.error(f"❌ Erro: {e}")
        return None

# ============================================================================
# FUNÇÕES DE PESQUISA (SEM BAIXAR)
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
    texto = re.sub(r'[_\-\.,;:]', ' ', texto)
    texto = re.sub(r'[^A-Z0-9\s]', '', texto)
    texto = re.sub(r'\s+', ' ', texto)
    return texto.strip()

def extrair_numeros(texto):
    return re.sub(r'[^0-9]', '', texto)

def pasta_contem_termo(nome_pasta, termo_busca, eh_documento):
    """Verifica se o nome da pasta contém o termo"""
    if eh_documento:
        numeros_pasta = extrair_numeros(nome_pasta)
        numeros_termo = extrair_numeros(termo_busca)
        if numeros_termo in numeros_pasta:
            return True
    
    nome_norm = normalizar_texto(nome_pasta)
    termo_norm = normalizar_texto(termo_busca)
    
    palavras_nome = nome_norm.split()
    palavras_termo = termo_norm.split()
    
    for palavra_termo in palavras_termo:
        if len(palavra_termo) >= 2:
            for palavra_nome in palavras_nome:
                if palavra_termo == palavra_nome:
                    return True
    return False

def baixar_pdf_temporario(drive_service, file_id, file_name):
    """Baixa apenas um PDF específico para leitura"""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix='.pdf')
        temp_file.write(fh.getvalue())
        temp_file.close()
        return temp_file.name
    except:
        return None

def pesquisar_em_pdf(drive_service, file_id, file_name, termo_busca, eh_documento):
    """Pesquisa dentro de um PDF sem baixar tudo primeiro"""
    pdf_path = baixar_pdf_temporario(drive_service, file_id, file_name)
    if not pdf_path:
        return False
    
    try:
        doc = fitz.open(pdf_path)
        termo_norm = normalizar_texto(termo_busca)
        numeros_termo = extrair_numeros(termo_busca) if eh_documento else None
        
        for page_num in range(len(doc)):
            texto = doc[page_num].get_text()
            texto_norm = normalizar_texto(texto)
            
            if termo_norm in texto_norm:
                doc.close()
                os.unlink(pdf_path)
                return True
            
            if numeros_termo:
                texto_numeros = extrair_numeros(texto)
                if numeros_termo in texto_numeros:
                    doc.close()
                    os.unlink(pdf_path)
                    return True
        
        doc.close()
        os.unlink(pdf_path)
        return False
    except:
        os.unlink(pdf_path)
        return False

def buscar_recursivamente(drive_service, folder_id, termo_busca, eh_documento, nivel=0):
    """Busca recursiva no Drive - retorna ID e nome da pasta encontrada"""
    indent = "  " * nivel
    try:
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
                    # Verifica se o nome da pasta corresponde
                    if pasta_contem_termo(file['name'], termo_busca, eh_documento):
                        print(f"{indent}✅ Pasta encontrada: {file['name']}")
                        return file['id'], file['name']
                    
                    # Busca dentro da subpasta
                    resultado = buscar_recursivamente(drive_service, file['id'], termo_busca, eh_documento, nivel+1)
                    if resultado:
                        return resultado
                
                elif file['name'].lower().endswith('.pdf'):
                    # Verifica se o PDF contém o termo
                    if pesquisar_em_pdf(drive_service, file['id'], file['name'], termo_busca, eh_documento):
                        print(f"{indent}📄 Encontrado no PDF: {file['name']}")
                        return folder_id, None
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
    except Exception as e:
        pass
    
    return None

def baixar_pasta_encontrada(drive_service, folder_id, folder_name, destino):
    """Baixa APENAS a pasta que foi encontrada"""
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
                    baixar_pasta_encontrada(drive_service, file['id'], file['name'], destino_path)
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
        st.error(f"Erro ao baixar: {e}")
        return False

# ============================================================================
# INTERFACE
# ============================================================================

st.title("🔍 Pesquisador de Credores")
st.markdown("### Falência Unick - Habilitação de Crédito")

with st.sidebar:
    st.markdown("## 📋 Instruções")
    st.markdown("""
    1. **Digite os termos** separados por vírgula
    2. **Clique em Pesquisar**
    3. O sistema busca **diretamente no Google Drive**
    4. **Baixa APENAS** os resultados encontrados
    """)
    
    st.markdown("---")
    st.markdown("### 💡 Exemplos:")
    st.code("""
01710455055, 08429728899
Adao Andrade, Albert
    """)

termos_input = st.text_area(
    "🔍 **Digite os termos separados por vírgula**",
    placeholder="Ex: 01710455055, Adao Andrade, Albert",
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
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # Listar pastas raiz
            status_text.info("📂 Listando pastas no Drive...")
            
            # Buscar pastas raiz
            pastas_raiz = []
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
                        pastas_raiz.append({'id': file['id'], 'name': file['name']})
                    
                    page_token = response.get('nextPageToken', None)
                    if page_token is None:
                        break
            except Exception as e:
                st.error(f"Erro ao listar pastas: {e}")
            
            resultados_encontrados = []
            temp_dir = tempfile.mkdtemp()
            
            for idx, termo in enumerate(termos):
                eh_doc = len(extrair_numeros(termo)) in [11, 14]
                status_text.info(f"🔍 Buscando: {termo} ({idx+1}/{len(termos)})")
                progress_bar.progress((idx) / len(termos))
                
                termo_encontrado = False
                
                for pasta in pastas_raiz:
                    if termo_encontrado:
                        break
                    
                    status_text.info(f"📁 {pasta['name']} - Buscando: {termo}")
                    
                    resultado = buscar_recursivamente(drive_service, pasta['id'], termo, eh_doc)
                    
                    if resultado:
                        folder_id_encontrada, folder_name = resultado
                        
                        status_text.info(f"✅ Encontrado! Baixando: {folder_name or pasta['name']}")
                        
                        # Baixar apenas a pasta encontrada
                        nome_pasta = folder_name if folder_name else pasta['name']
                        baixar_pasta_encontrada(drive_service, folder_id_encontrada, nome_pasta, temp_dir)
                        
                        resultados_encontrados.append({
                            'termo': termo,
                            'pasta': nome_pasta,
                            'caminho': str(Path(temp_dir) / nome_pasta)
                        })
                        termo_encontrado = True
            
            progress_bar.progress(1.0)
            status_text.success("✅ Pesquisa concluída!")
            
            if resultados_encontrados:
                st.success(f"✅ **ENCONTRADOS!** {len(resultados_encontrados)} resultado(s)")
                
                for item in resultados_encontrados:
                    with st.expander(f"📁 {item['pasta']} - Termo: {item['termo']}"):
                        st.write(f"**Caminho:** {item['caminho']}")
                
                # Criar ZIP com os resultados
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
                st.warning("❌ Nenhum resultado encontrado")
            
            # Limpar
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)