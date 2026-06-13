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

# Pastas base (containers)
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
        return False

def baixar_arquivo(drive_service, file_id, file_name, destino):
    """Baixa um único arquivo"""
    try:
        request = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            status, done = downloader.next_chunk()
        
        file_path = Path(destino) / file_name
        with open(file_path, 'wb') as f:
            f.write(fh.getvalue())
        return True
    except:
        return False

# ============================================================================
# CLASSE DO PESQUISADOR (versão Drive)
# ============================================================================

class PesquisadorCredoresDrive:
    def __init__(self, drive_service, folder_id, termo_busca, temp_dir):
        self.drive_service = drive_service
        self.folder_id = folder_id
        self.termo_busca = termo_busca.upper().strip()
        self.temp_dir = Path(temp_dir)
        self.pastas_encontradas = []
        self.eh_documento = self._detectar_documento(termo_busca)
        
    def _detectar_documento(self, texto):
        numeros = re.sub(r'[^0-9]', '', texto)
        return len(numeros) == 11 or len(numeros) == 14
    
    def normalizar_texto(self, texto):
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
    
    def extrair_numeros(self, texto):
        return re.sub(r'[^0-9]', '', texto)
    
    def pasta_contem_documento(self, nome_pasta):
        if not self.eh_documento:
            return False
        numeros_pasta = self.extrair_numeros(nome_pasta)
        numeros_busca = self.extrair_numeros(self.termo_busca)
        return numeros_busca in numeros_pasta
    
    def pasta_contem_texto(self, nome_pasta):
        nome_norm = self.normalizar_texto(nome_pasta)
        busca_norm = self.normalizar_texto(self.termo_busca)
        
        nome_sem_sep = nome_norm.replace(' ', '').replace('_', '')
        busca_sem_sep = busca_norm.replace(' ', '').replace('_', '')
        
        if busca_sem_sep in nome_sem_sep:
            return True
        if busca_norm in nome_norm:
            return True
        return False
    
    def eh_pasta_credor(self, nome_pasta):
        numeros = self.extrair_numeros(nome_pasta)
        return len(numeros) >= 11
    
    def eh_pasta_base(self, nome_pasta):
        return nome_pasta.upper() in [p.upper() for p in PASTAS_BASE]
    
    def encontrar_pasta_credor_pai(self, folder_id, folder_name, parent_id=None):
        """Sobe na árvore até encontrar a pasta do credor"""
        current_id = folder_id
        current_name = folder_name
        
        # Lista para rastrear o caminho
        path = []
        
        while True:
            # Se encontrou uma pasta com CPF, é credor
            if self.eh_pasta_credor(current_name):
                return current_id, current_name
            
            # Se não é pasta base, pode ser credor sem CPF
            if not self.eh_pasta_base(current_name):
                if self.pasta_contem_texto(current_name) or self.pasta_contem_documento(current_name):
                    return current_id, current_name
            
            # Sobe para o pai (precisamos do parent_id)
            # Como não temos o parent_id facilmente, vamos buscar
            try:
                response = self.drive_service.files().get(fileId=current_id, fields='parents').execute()
                parents = response.get('parents', [])
                if not parents:
                    break
                parent_id = parents[0]
                
                # Busca informações do pai
                parent_response = self.drive_service.files().get(fileId=parent_id, fields='id, name').execute()
                current_id = parent_response['id']
                current_name = parent_response['name']
            except:
                break
        
        return folder_id, folder_name
    
    def pesquisar_em_pdf(self, file_id, file_name):
        try:
            # Baixar apenas para leitura
            temp_pdf = self.temp_dir / f"temp_{file_id}.pdf"
            if not baixar_arquivo(self.drive_service, file_id, file_name, self.temp_dir):
                return False
            
            doc = fitz.open(str(temp_pdf))
            termo_norm = self.normalizar_texto(self.termo_busca)
            termo_sem_sep = termo_norm.replace(' ', '').replace('_', '')
            termo_numeros = self.extrair_numeros(self.termo_busca) if self.eh_documento else None
            
            for page_num in range(len(doc)):
                texto = doc[page_num].get_text()
                texto_norm = self.normalizar_texto(texto)
                texto_sem_sep = texto_norm.replace(' ', '').replace('_', '')
                
                if termo_sem_sep in texto_sem_sep:
                    doc.close()
                    os.unlink(temp_pdf)
                    return True
                
                if termo_numeros:
                    texto_numeros = self.extrair_numeros(texto)
                    if termo_numeros in texto_numeros:
                        doc.close()
                        os.unlink(temp_pdf)
                        return True
            
            doc.close()
            os.unlink(temp_pdf)
            return False
        except:
            return False
    
    def buscar_recursivamente(self, folder_id, folder_name, resultados):
        """Busca recursiva no Drive"""
        try:
            page_token = None
            while True:
                response = self.drive_service.files().list(
                    q=f"'{folder_id}' in parents and trashed=false",
                    spaces='drive',
                    fields='nextPageToken, files(id, name, mimeType)',
                    pageToken=page_token
                ).execute()
                
                for file in response.get('files', []):
                    if file['mimeType'] == 'application/vnd.google-apps.folder':
                        # Verifica se o nome corresponde
                        if self.pasta_contem_texto(file['name']) or self.pasta_contem_documento(file['name']):
                            print(f"✅ Pasta encontrada: {file['name']}")
                            pasta_credor_id, pasta_credor_name = self.encontrar_pasta_credor_pai(file['id'], file['name'])
                            if pasta_credor_id not in resultados:
                                resultados[pasta_credor_id] = pasta_credor_name
                            continue
                        
                        # Busca dentro
                        self.buscar_recursivamente(file['id'], file['name'], resultados)
                    
                    elif file['name'].lower().endswith('.pdf'):
                        if self.pesquisar_em_pdf(file['id'], file['name']):
                            print(f"📄 Encontrado no PDF: {file['name']}")
                            pasta_credor_id, pasta_credor_name = self.encontrar_pasta_credor_pai(folder_id, folder_name)
                            if pasta_credor_id not in resultados:
                                resultados[pasta_credor_id] = pasta_credor_name
                
                page_token = response.get('nextPageToken', None)
                if page_token is None:
                    break
        except Exception as e:
            pass
        
        return resultados
    
    def processar(self, progress_callback=None):
        # Listar pastas raiz
        pastas_raiz = listar_pastas_raiz(self.drive_service, self.folder_id)
        
        resultados = {}
        
        for idx, pasta in enumerate(pastas_raiz):
            if progress_callback:
                progress_callback(idx + 1, len(pastas_raiz), pasta['name'])
            
            self.buscar_recursivamente(pasta['id'], pasta['name'], resultados)
        
        # Baixar as pastas encontradas
        pastas_baixadas = []
        for folder_id, folder_name in resultados.items():
            # Criar diretório temporário para esta pasta
            pasta_temp = self.temp_dir / folder_name
            if baixar_pasta_completa(self.drive_service, folder_id, folder_name, self.temp_dir):
                pastas_baixadas.append({
                    'termo': self.termo_busca,
                    'pasta': folder_name,
                    'caminho': str(pasta_temp)
                })
        
        return pastas_baixadas

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
William Miranda, Albert
    """)
    
    st.markdown("---")
    st.markdown("### 📁 Pasta conectada:")
    st.code("TESTEEMAIL_ARQUIVOS (Google Drive)")

# Entrada de termos
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
        
        # Autenticar
        drive_service = autenticar_drive()
        
        if not drive_service:
            st.error("❌ Erro ao conectar ao Google Drive. Verifique as Secrets.")
        else:
            # Criar diretório temporário
            import tempfile
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
                
                pesquisador = PesquisadorCredoresDrive(drive_service, FOLDER_ID, termo, temp_dir)
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
                
                os.unlink(zip_path.name)
            else:
                resultados_container.warning("❌ Nenhum resultado encontrado")
            
            # Limpar
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)