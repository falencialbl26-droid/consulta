import streamlit as st
import os
import re
import tempfile
from pathlib import Path
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import io
import fitz  # PyMuPDF

# ============================================================================
# CONFIGURAÇÕES
# ============================================================================
FOLDER_ID = "1bSfx68JysAIY-iRH42MSNoUMXxF6U1MH"  # ID da sua pasta pública

# ============================================================================
# FUNÇÕES DO GOOGLE DRIVE
# ============================================================================

def autenticar_drive():
    """Autentica usando Service Account (para deploy) ou retorna None para teste local"""
    try:
        # Tenta usar as credenciais do Streamlit Secrets
        if "gcp_service_account" in st.secrets:
            credentials = service_account.Credentials.from_service_account_info(
                st.secrets["gcp_service_account"],
                scopes=['https://www.googleapis.com/auth/drive.readonly']
            )
            return build('drive', 'v3', credentials=credentials)
        else:
            # Modo de demonstração - sem acesso real ao Drive
            st.warning("🔧 Modo de demonstração. Configure as secrets para acesso real.")
            return None
    except Exception as e:
        st.error(f"Erro de autenticação: {e}")
        return None

def listar_pastas_raiz(drive_service, folder_id):
    """Lista as pastas dentro da pasta principal"""
    pastas = []
    
    if not drive_service:
        # Dados de exemplo para demonstração
        return [
            {"id": "ex1", "name": "RESULTADO_ANALISADOS"},
            {"id": "ex2", "name": "RESULTADO_SENTENCAS"},
            {"id": "ex3", "name": "RESULTADO_CAIXA_DE_ENTRADA"}
        ]
    
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

def buscar_pdfs_pasta(drive_service, folder_id):
    """Busca recursivamente por todos os PDFs em uma pasta"""
    arquivos_pdf = []
    
    if not drive_service:
        return []
    
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
                    # É uma subpasta: buscar recursivamente
                    sub_pdfs = buscar_pdfs_pasta(drive_service, file['id'])
                    arquivos_pdf.extend(sub_pdfs)
                elif file['name'].lower().endswith('.pdf'):
                    # É um PDF
                    arquivos_pdf.append({
                        'id': file['id'],
                        'name': file['name']
                    })
            
            page_token = response.get('nextPageToken', None)
            if page_token is None:
                break
    except Exception as e:
        st.error(f"Erro ao buscar PDFs: {e}")
    
    return arquivos_pdf

def baixar_pdf_temporario(drive_service, file_id, file_name):
    """Baixa um PDF do Drive para arquivo temporário"""
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
        
        return Path(temp_file.name)
    except Exception as e:
        return None

def normalizar_texto(texto):
    """Remove acentos, espaços e caracteres especiais para comparação"""
    if not texto:
        return ""
    texto = texto.upper()
    texto = re.sub(r'[ÁÀÂÃÄ]', 'A', texto)
    texto = re.sub(r'[ÉÈÊË]', 'E', texto)
    texto = re.sub(r'[ÍÌÎÏ]', 'I', texto)
    texto = re.sub(r'[ÓÒÔÕÖ]', 'O', texto)
    texto = re.sub(r'[ÚÙÛÜ]', 'U', texto)
    texto = re.sub(r'Ç', 'C', texto)
    texto = re.sub(r'[^A-Z0-9]', '', texto)
    return texto

def pesquisar_em_pdf(pdf_path, termo_busca):
    """Pesquisa o termo dentro de um PDF"""
    try:
        doc = fitz.open(pdf_path)
        termo_normalizado = normalizar_texto(termo_busca)
        
        for page_num in range(len(doc)):
            texto = doc[page_num].get_text()
            texto_normalizado = normalizar_texto(texto)
            
            if termo_normalizado in texto_normalizado:
                doc.close()
                return True
        
        doc.close()
        return False
    except Exception as e:
        return False
    finally:
        try:
            os.unlink(pdf_path)
        except:
            pass

def pasta_contem_nome(pasta_nome, termo_busca):
    """Verifica se o nome da pasta contém o termo buscado"""
    pasta_normalizado = normalizar_texto(pasta_nome)
    termo_normalizado = normalizar_texto(termo_busca)
    return termo_normalizado in pasta_normalizado

def processar_pesquisa(drive_service, folder_id, termo_busca, progress_placeholder, status_placeholder):
    """Função principal de pesquisa"""
    resultados = []
    
    # Listar pastas raiz
    status_placeholder.info("📂 Listando pastas...")
    pastas_raiz = listar_pastas_raiz(drive_service, folder_id)
    
    if not pastas_raiz:
        return [], "Nenhuma pasta encontrada"
    
    total_pastas = len(pastas_raiz)
    
    for idx, pasta in enumerate(pastas_raiz):
        # Atualizar progresso
        progresso_atual = (idx + 1) / total_pastas
        progress_placeholder.progress(progresso_atual)
        status_placeholder.info(f"🔍 Analisando: {pasta['name']} ({idx+1}/{total_pastas})")
        
        # Verificar se o nome da pasta já contém a busca
        if pasta_contem_nome(pasta['name'], termo_busca):
            resultados.append({
                'pasta': pasta['name'],
                'pasta_id': pasta['id'],
                'motivo': '✅ Nome da pasta corresponde'
            })
            continue
        
        # Se não, buscar PDFs e verificar conteúdo
        if drive_service:
            status_placeholder.info(f"📄 Buscando PDFs em: {pasta['name']}...")
            pdfs = buscar_pdfs_pasta(drive_service, pasta['id'])
            
            encontrado = False
            for pdf in pdfs:
                status_placeholder.info(f"📖 Lendo: {pdf['name']}...")
                pdf_path = baixar_pdf_temporario(drive_service, pdf['id'], pdf['name'])
                if pdf_path and pesquisar_em_pdf(pdf_path, termo_busca):
                    resultados.append({
                        'pasta': pasta['name'],
                        'pasta_id': pasta['id'],
                        'motivo': f'✅ Encontrado no PDF: {pdf["name"]}'
                    })
                    encontrado = True
                    break
            
            if not encontrado:
                resultados.append({
                    'pasta': pasta['name'],
                    'pasta_id': pasta['id'],
                    'motivo': '❌ Nome não encontrado'
                })
        else:
            # Modo demonstração
            resultados.append({
                'pasta': pasta['name'],
                'pasta_id': pasta['id'],
                'motivo': '🔧 Modo demonstração (configure secrets para acesso real)'
            })
    
    return resultados, None

# ============================================================================
# INTERFACE STREAMLIT
# ============================================================================

st.set_page_config(
    page_title="Pesquisador de Credores - Falência Unick",
    page_icon="🔍",
    layout="wide"
)

# CSS personalizado
st.markdown("""
<style>
    .main-header {
        background-color: #2c3e50;
        padding: 1rem;
        border-radius: 10px;
        margin-bottom: 2rem;
    }
    .result-card {
        background-color: #f0f2f6;
        padding: 1rem;
        border-radius: 8px;
        margin-bottom: 0.5rem;
    }
    .success {
        color: #27ae60;
        font-weight: bold;
    }
    .info {
        color: #2980b9;
    }
</style>
""", unsafe_allow_html=True)

# Título
st.markdown('<div class="main-header">', unsafe_allow_html=True)
st.title("🔍 Pesquisador de Credores")
st.markdown("### Falência Unick - Habilitação de Crédito")
st.markdown(f'<p class="info">📁 Pasta conectada: <code>TESTEEMAIL_ARQUIVOS</code></p>', unsafe_allow_html=True)
st.markdown('</div>', unsafe_allow_html=True)

# Sidebar
with st.sidebar:
    st.markdown("## 📋 Instruções")
    st.markdown("""
    1. **Digite o nome** do credor que deseja buscar
    2. **Clique em Pesquisar**
    3. **Aguarde** o processamento (pode levar alguns minutos)
    4. **Veja os resultados** na tela
    
    ---
    
    ### 💡 Dicas:
    - A busca ignora maiúsculas/minúsculas
    - Remove acentos automaticamente
    - Busca em nomes de pastas e dentro de PDFs
    - Quanto mais PDFs, mais demorado
    
    ---
    
    ### 📁 Acesso:
    - [Abrir pasta no Google Drive](https://drive.google.com/drive/folders/1bSfx68JysAIY-iRH42MSNoUMXxF6U1MH)
    """)
    
    st.markdown("---")
    st.caption("Sistema de busca para credores da Falência Unick")

# Campo de busca
col1, col2 = st.columns([3, 1])

with col1:
    nome_busca = st.text_input(
        "🔍 **Nome do credor**",
        placeholder="Ex: CLEUSA RODRIGUES, CLAUDIA ONEIDE GOLLMANN, ADRIANO CHAVES",
        help="Digite o nome completo ou parcial do credor"
    )

with col2:
    st.markdown("### ")
    pesquisar = st.button("🔍 PESQUISAR", type="primary", use_container_width=True)

# Área de resultados
if pesquisar:
    if not nome_busca:
        st.error("❌ **Erro:** Digite o nome do credor!")
    else:
        # Placeholders para progresso
        progress_bar = st.progress(0)
        status_text = st.empty()
        resultados_container = st.container()
        
        try:
            # Autenticar
            drive_service = autenticar_drive()
            
            # Executar pesquisa
            resultados, erro = processar_pesquisa(
                drive_service, 
                FOLDER_ID, 
                nome_busca, 
                progress_bar, 
                status_text
            )
            
            progress_bar.progress(1.0)
            
            if erro:
                status_text.error(f"❌ {erro}")
            else:
                # Separar resultados encontrados e não encontrados
                encontrados = [r for r in resultados if "✅" in r['motivo']]
                nao_encontrados = [r for r in resultados if "❌" in r['motivo']]
                
                with resultados_container:
                    if encontrados:
                        st.success(f"✅ **ENCONTRADO!** ({len(encontrados)} pasta(s))")
                        
                        st.markdown("### 📋 Pastas encontradas:")
                        for item in encontrados:
                            with st.expander(f"📁 {item['pasta']}"):
                                st.markdown(f"**Status:** {item['motivo']}")
                                st.markdown(f"**ID da pasta:** `{item['pasta_id']}`")
                    else:
                        st.warning(f"❌ **NENHUMA pasta encontrada** com o nome '{nome_busca}'")
                        st.info("💡 **Dica:** Verifique se o nome está correto ou tente com variações")
                    
                    if nao_encontrados and len(nao_encontrados) > 0:
                        with st.expander(f"📁 Pastas verificadas ({len(nao_encontrados)} sem correspondência)"):
                            for item in nao_encontrados:
                                st.markdown(f"- **{item['pasta']}**: {item['motivo']}")
                
                status_text.success(f"✅ Pesquisa concluída! Processadas {len(resultados)} pastas.")
                
        except Exception as e:
            status_text.error(f"❌ **Erro durante a pesquisa:** {str(e)}")
            st.info("Tente novamente ou verifique se a pasta está acessível")

# Informações adicionais
st.markdown("---")
st.markdown("### ℹ️ Sobre o sistema")
st.markdown("""
- **Busca inteligente:** Encontra o nome mesmo com acentos, maiúsculas/minúsculas diferentes
- **Modo de demonstração:** Se as credenciais não estiverem configuradas, o sistema mostra dados de exemplo
- **Privacidade:** Apenas leitura dos arquivos, nada é alterado ou excluído
""")
