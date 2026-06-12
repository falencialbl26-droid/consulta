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
FOLDER_ID = "1bSfx68JysAIY-iRH42MSNoUMXxF6U1MH"  # ID da sua pasta pública

# ============================================================================
# AUTENTICAÇÃO
# ============================================================================

def autenticar_drive():
    """Autentica usando Service Account do Streamlit Secrets"""
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
# FUNÇÕES DO GOOGLE DRIVE
# ============================================================================

def listar_pastas_raiz(drive_service, folder_id):
    """Lista as pastas dentro da pasta principal"""
    pastas = []
    try:
        page_token = None
        while True:
            response = drive_service.files().list(
                q=f"'{folder_id}' in parents and mimeType='application/vnd.google-apps.folder' and trashed=false",
                spaces='drive',
                fields='nextPageToken, files(id, name)',
                pageToken=page_token
            ).execute()
            
            for file in response.get('files', []):
                pastas.append({
                    'id': file['id'],
                    'name': file['name']
                })
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
    except Exception as e:
        st.error(f"Erro ao listar pastas: {e}")
    
    return pastas

def baixar_pasta_completa(drive_service, folder_id, folder_name, destino):
    """Baixa recursivamente todos os arquivos de uma pasta"""
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
        st.error(f"Erro ao baixar pasta {folder_name}: {e}")
        return False

# ============================================================================
# CLASSE DO PESQUISADOR
# ============================================================================

class PesquisadorCredores:
    def __init__(self, pasta_base, termo_busca):
        self.pasta_base = Path(pasta_base)
        self.termo_busca = termo_busca.upper().strip()
        self.eh_documento = self._detectar_documento(termo_busca)
        
    def _detectar_documento(self, texto):
        numeros = re.sub(r'[^0-9]', '', texto)
        return len(numeros) == 11 or len(numeros) == 14
    
    def normalizar_texto(self, texto):
        if not texto:
            return ""
        texto = texto.upper()
        # Remove acentos
        texto = re.sub(r'[ÁÀÂÃÄ]', 'A', texto)
        texto = re.sub(r'[ÉÈÊË]', 'E', texto)
        texto = re.sub(r'[ÍÌÎÏ]', 'I', texto)
        texto = re.sub(r'[ÓÒÔÕÖ]', 'O', texto)
        texto = re.sub(r'[ÚÙÛÜ]', 'U', texto)
        texto = re.sub(r'Ç', 'C', texto)
        # Remove caracteres especiais
        texto = re.sub(r'[_\-\.,;:]', ' ', texto)
        texto = re.sub(r'[^A-Z0-9\s]', '', texto)
        texto = re.sub(r'\s+', ' ', texto)
        return texto.strip()
    
    def extrair_numeros(self, texto):
        return re.sub(r'[^0-9]', '', texto)
    
    def pasta_contem_termo(self, nome_pasta):
        nome_norm = self.normalizar_texto(nome_pasta)
        termo_norm = self.normalizar_texto(self.termo_busca)
        
        # Busca por documento (CPF/CNPJ)
        if self.eh_documento:
            numeros_pasta = self.extrair_numeros(nome_pasta)
            numeros_termo = self.extrair_numeros(self.termo_busca)
            if numeros_termo in numeros_pasta:
                return True, "Documento corresponde"
        
        # Busca por nome (palavra exata)
        palavras_nome = nome_norm.split()
        palavras_termo = termo_norm.split()
        
        for palavra_termo in palavras_termo:
            if len(palavra_termo) >= 2:
                for palavra_nome in palavras_nome:
                    if palavra_termo == palavra_nome:
                        return True, "Nome corresponde"
        return False, ""
    
    def pesquisar_em_pdf(self, pdf_path):
        try:
            if pdf_path.stat().st_size == 0:
                return False
            
            doc = fitz.open(pdf_path)
            termo_norm = self.normalizar_texto(self.termo_busca)
            numeros_termo = self.extrair_numeros(self.termo_busca) if self.eh_documento else None
            
            for page_num in range(len(doc)):
                texto = doc[page_num].get_text()
                texto_norm = self.normalizar_texto(texto)
                
                # Busca por nome
                if termo_norm in texto_norm:
                    doc.close()
                    return True
                
                # Busca por documento
                if numeros_termo:
                    texto_numeros = self.extrair_numeros(texto)
                    if numeros_termo in texto_numeros:
                        doc.close()
                        return True
            
            doc.close()
            return False
        except Exception as e:
            return False
    
    def buscar_recursivamente(self, pasta_atual):
        try:
            for item in pasta_atual.iterdir():
                if item.is_dir():
                    corresponde, _ = self.pasta_contem_termo(item.name)
                    if corresponde:
                        return item
                    
                    resultado = self.buscar_recursivamente(item)
                    if resultado:
                        return resultado
                elif item.is_file() and item.suffix.lower() == '.pdf':
                    if self.pesquisar_em_pdf(item):
                        return pasta_atual
        except Exception as e:
            pass
        return None
    
    def processar(self, progress_callback=None):
        pastas_raiz = [p for p in self.pasta_base.iterdir() if p.is_dir()]
        
        resultados = []
        for idx, pasta_raiz in enumerate(pastas_raiz):
            if progress_callback:
                progress_callback(idx + 1, len(pastas_raiz), pasta_raiz.name)
            
            pasta_encontrada = self.buscar_recursivamente(pasta_raiz)
            if pasta_encontrada:
                resultados.append({
                    'termo': self.termo_busca,
                    'pasta': pasta_encontrada.name,
                    'caminho': str(pasta_encontrada.relative_to(self.pasta_base))
                })
        
        return resultados

# ============================================================================
# INTERFACE STREAMLIT
# ============================================================================

st.title("🔍 Pesquisador de Credores")
st.markdown("### Falência Unick - Habilitação de Crédito")

with st.sidebar:
    st.markdown("## 📋 Instruções")
    st.markdown("""
    1. **Digite os termos** separados por vírgula
    2. **Clique em Pesquisar**
    3. **Aguarde** o processamento
    4. **Baixe** os resultados
    """)
    
    st.markdown("---")
    st.markdown("### 💡 Exemplos:")
    st.code("""
01710455055, 08429728899
Adao Andrade, Albert
    """)
    
    st.markdown("---")
    st.markdown("### 📁 Pasta conectada:")
    st.code("TESTEEMAIL_ARQUIVOS (Google Drive)")

# Entrada de termos
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
        
        # Autenticar
        drive_service = autenticar_drive()
        
        if not drive_service:
            st.error("❌ Erro ao conectar ao Google Drive. Verifique as Secrets.")
        else:
            # Criar pasta temporária
            temp_dir = tempfile.mkdtemp()
            
            with st.spinner("📥 Baixando dados do Google Drive (pode levar alguns minutos)..."):
                # Listar pastas raiz
                pastas_raiz = listar_pastas_raiz(drive_service, FOLDER_ID)
                
                if not pastas_raiz:
                    st.error("❌ Nenhuma pasta encontrada no Drive")
                else:
                    # Baixar cada pasta raiz
                    for pasta in pastas_raiz:
                        baixar_pasta_completa(drive_service, pasta['id'], pasta['name'], temp_dir)
                    
                    st.success("✅ Dados baixados com sucesso!")
            
            # Processar cada termo
            progress_bar = st.progress(0)
            status_text = st.empty()
            resultados_container = st.empty()
            
            todos_resultados = []
            
            for idx, termo in enumerate(termos):
                status_text.info(f"🔍 Pesquisando: {termo} ({idx+1}/{len(termos)})")
                
                def atualizar_progresso(atual, total, pasta_atual):
                    percentual = (idx + (atual/total)) / len(termos)
                    progress_bar.progress(percentual)
                    status_text.info(f"📁 {pasta_atual} - {termo}")
                
                pesquisador = PesquisadorCredores(temp_dir, termo)
                resultados = pesquisador.processar(atualizar_progresso)
                todos_resultados.extend(resultados)
            
            progress_bar.progress(1.0)
            status_text.success("✅ Pesquisa concluída!")
            
            if todos_resultados:
                resultados_container.success(f"✅ **ENCONTRADOS!** {len(todos_resultados)} resultado(s)")
                
                # Mostrar resultados
                for item in todos_resultados:
                    with st.expander(f"📁 {item['pasta']} - Termo: {item['termo']}"):
                        st.write(f"**Caminho:** {item['caminho']}")
                
                # Criar ZIP com resultados
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
                
                # Limpar arquivo temporário
                os.unlink(zip_path.name)
            else:
                resultados_container.warning("❌ Nenhum resultado encontrado")
            
            # Limpar pasta temporária
            shutil.rmtree(temp_dir, ignore_errors=True)