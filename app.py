import streamlit as st
import os
import re
import shutil
import zipfile
from pathlib import Path
import fitz  # PyMuPDF
import tempfile

# Configuração da página
st.set_page_config(
    page_title="Pesquisador de Credores - Falência Unick",
    page_icon="🔍",
    layout="wide"
)

# ============================================================================
# CLASSE DO PESQUISADOR
# ============================================================================

class PesquisadorCredores:
    def __init__(self, pasta_base, pasta_resultado, nome_pesquisa):
        self.pasta_base = Path(pasta_base)
        self.pasta_resultado = self.pasta_base / pasta_resultado
        self.nome_pesquisa = nome_pesquisa.upper().strip()
        self.pastas_encontradas = []
        
    def limpar_texto(self, texto):
        if not texto:
            return ""
        texto = texto.upper()
        texto = re.sub(r'[^A-Z0-9\s]', '', texto)
        texto = re.sub(r'\s+', ' ', texto)
        return texto.strip()
    
    def normalizar_para_busca(self, texto):
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
    
    def eh_pasta_credor(self, nome_pasta):
        if '_' in nome_pasta:
            partes = nome_pasta.split('_')
            if len(partes) >= 2 and re.search(r'\d{11}', partes[-1]):
                return True
            if len(partes) >= 2 and partes[-1].isdigit():
                return True
        return False
    
    def nome_contem_pesquisa(self, nome_pasta):
        nome_limpo = self.limpar_texto(nome_pasta)
        pesquisa_limpa = self.limpar_texto(self.nome_pesquisa)
        
        if pesquisa_limpa in nome_limpo:
            return True
        
        nome_sem_espaco = self.normalizar_para_busca(nome_pasta)
        pesquisa_sem_espaco = self.normalizar_para_busca(self.nome_pesquisa)
        
        if pesquisa_sem_espaco in nome_sem_espaco:
            return True
        
        partes_pesquisa = pesquisa_sem_espaco.split()
        if len(partes_pesquisa) >= 2:
            partes_encontradas = 0
            for parte in partes_pesquisa:
                if parte in nome_sem_espaco:
                    partes_encontradas += 1
            if partes_encontradas == len(partes_pesquisa):
                return True
        
        return False
    
    def pesquisar_em_pdf(self, pdf_path):
        try:
            if pdf_path.stat().st_size == 0:
                return False
            
            doc = fitz.open(pdf_path)
            
            if doc.is_encrypted:
                doc.close()
                return False
            
            termo_busca_normal = self.normalizar_para_busca(self.nome_pesquisa)
            partes_busca = termo_busca_normal.split()
            
            for page_num in range(len(doc)):
                page = doc[page_num]
                texto = page.get_text()
                
                texto_limpo = self.limpar_texto(texto)
                if self.limpar_texto(self.nome_pesquisa) in texto_limpo:
                    doc.close()
                    return True
                
                texto_sem_espaco = self.normalizar_para_busca(texto)
                if termo_busca_normal in texto_sem_espaco:
                    doc.close()
                    return True
                
                if len(partes_busca) >= 2:
                    partes_encontradas = 0
                    for parte in partes_busca:
                        if parte in texto_sem_espaco:
                            partes_encontradas += 1
                    
                    if partes_encontradas == len(partes_busca):
                        doc.close()
                        return True
            
            doc.close()
            return False
            
        except Exception as e:
            return False
    
    def buscar_em_pastas_nao_credor(self, pasta_atual):
        try:
            for item in pasta_atual.iterdir():
                if item.is_file() and item.suffix.lower() == '.pdf':
                    if self.pesquisar_em_pdf(item):
                        return True
                elif item.is_dir():
                    if self.eh_pasta_credor(item.name):
                        if self.nome_contem_pesquisa(item.name):
                            return item
                        else:
                            continue
                    else:
                        resultado = self.buscar_em_pastas_nao_credor(item)
                        if resultado:
                            return resultado
            return False
        except Exception as e:
            return False
    
    def copiar_preservando_estrutura(self, pasta_encontrada):
        caminho_relativo = pasta_encontrada.relative_to(self.pasta_base)
        destino = self.pasta_resultado / caminho_relativo
        
        if destino.exists():
            contador = 1
            while destino.exists():
                destino = self.pasta_resultado / f"{caminho_relativo}_{contador}"
                contador += 1
        
        destino.parent.mkdir(parents=True, exist_ok=True)
        
        try:
            shutil.copytree(pasta_encontrada, destino)
            return destino, caminho_relativo
        except Exception as e:
            return None, None
    
    def processar_pastas(self):
        pastas_raiz = [p for p in self.pasta_base.iterdir() if p.is_dir()]
        pastas_raiz = [p for p in pastas_raiz if p.name != self.pasta_resultado.name]
        
        for idx, pasta_raiz in enumerate(pastas_raiz):
            if self.eh_pasta_credor(pasta_raiz.name):
                if self.nome_contem_pesquisa(pasta_raiz.name):
                    destino, caminho = self.copiar_preservando_estrutura(pasta_raiz)
                    if destino:
                        self.pastas_encontradas.append({
                            'pasta': pasta_raiz.name,
                            'caminho_relativo': str(caminho),
                            'motivo': 'Nome da pasta (credor)'
                        })
                continue
            
            resultado = self.buscar_em_pastas_nao_credor(pasta_raiz)
            
            if resultado:
                if isinstance(resultado, Path):
                    destino, caminho = self.copiar_preservando_estrutura(resultado)
                    if destino:
                        self.pastas_encontradas.append({
                            'pasta': resultado.name,
                            'caminho_relativo': str(caminho),
                            'motivo': f'Encontrado dentro de {pasta_raiz.name}'
                        })
                elif resultado:
                    destino, caminho = self.copiar_preservando_estrutura(pasta_raiz)
                    if destino:
                        self.pastas_encontradas.append({
                            'pasta': pasta_raiz.name,
                            'caminho_relativo': str(caminho),
                            'motivo': f'Conteúdo de PDF'
                        })
        
        return self.pastas_encontradas

# ============================================================================
# INTERFACE STREAMLIT
# ============================================================================

# Título
st.title("🔍 Pesquisador de Credores")
st.markdown("### Falência Unick - Habilitação de Crédito")

# Sidebar com instruções
with st.sidebar:
    st.markdown("## 📋 Instruções")
    st.markdown("""
    1. Compacte a pasta **TESTEEMAIL** em um arquivo `.zip`
    2. Faça o upload do arquivo ZIP
    3. Digite o nome do credor
    4. Clique em **Pesquisar**
    5. Aguarde o processamento
    6. Faça o download dos resultados
    """)
    
    st.markdown("---")
    
    st.markdown("### 📁 Como criar o ZIP:")
    st.code("""
Windows:
1. Clique direito na pasta
2. Enviar para > Pasta compactada

Mac:
1. Clique direito na pasta
2. Compactar "TESTEEMAIL"
    """)
    
    st.markdown("---")
    st.markdown("### 💡 Dicas:")
    st.info("""
    - A pesquisa pode levar alguns minutos
    - Quanto mais arquivos, mais demorado
    - Você receberá um ZIP com os resultados
    """)

# Colunas para organização
col1, col2 = st.columns([2, 1])

with col1:
    # Upload do arquivo
    uploaded_file = st.file_uploader(
        "📁 **Faça o upload da pasta TESTEEMAIL (arquivo ZIP)**",
        type=['zip'],
        help="Compacte sua pasta TESTEEMAIL em um arquivo .zip e faça o upload"
    )
    
    # Campo para nome
    nome_busca = st.text_input(
        "🔍 **Nome do credor**",
        placeholder="Ex: CLEUSA RODRIGUES",
        help="Digite o nome completo ou parcial do credor"
    )

with col2:
    st.markdown("### 📊 Exemplo:")
    st.markdown("""
    - CLEUSA RODRIGUES
    - CLAUDIA ONEIDE GOLLMANN
    - ADRIANO CHAVES
    """)

# Botão de pesquisa
botao_pesquisar = st.button("🔍 PESQUISAR", type="primary", use_container_width=True)

# Área de resultados
if botao_pesquisar:
    if not nome_busca:
        st.error("❌ **Erro:** Digite o nome do credor!")
    elif not uploaded_file:
        st.error("❌ **Erro:** Faça o upload da pasta TESTEEMAIL!")
    else:
        # Barra de progresso
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        try:
            status_text.info("📦 Preparando arquivos...")
            progress_bar.progress(10)
            
            # Salvar o ZIP enviado
            with tempfile.NamedTemporaryFile(delete=False, suffix='.zip') as tmp_file:
                tmp_file.write(uploaded_file.getvalue())
                zip_path = tmp_file.name
            
            status_text.info("📂 Extraindo arquivos...")
            progress_bar.progress(20)
            
            # Extrair o ZIP
            extract_path = tempfile.mkdtemp()
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(extract_path)
            
            # Encontrar a pasta TESTEEMAIL extraída
            pasta_base = None
            for item in Path(extract_path).iterdir():
                if item.is_dir() and item.name == "TESTEEMAIL":
                    pasta_base = item
                    break
            
            if not pasta_base:
                st.error("❌ **Erro:** Pasta TESTEEMAIL não encontrada no ZIP!")
                st.info("Verifique se o ZIP contém uma pasta chamada exatamente 'TESTEEMAIL'")
            else:
                status_text.info(f"🔍 Pesquisando por: {nome_busca.upper()}...")
                progress_bar.progress(30)
                
                # Executar pesquisa
                pesquisador = PesquisadorCredores(pasta_base, "RESULTADO_ENCONTRADO", nome_busca)
                
                # Executar (simular progresso)
                resultados = pesquisador.processar_pastas()
                
                progress_bar.progress(90)
                status_text.info("✅ Finalizando...")
                
                if resultados:
                    st.success(f"✅ **ENCONTRADO!** ({len(resultados)} pasta(s))")
                    
                    # Mostrar resultados em expansores
                    st.markdown("### 📋 Pastas localizadas:")
                    for item in resultados:
                        with st.expander(f"📁 {item['pasta']}"):
                            st.markdown(f"**Caminho:** `{item['caminho_relativo']}`")
                            st.markdown(f"**Motivo:** {item['motivo']}")
                    
                    # Criar ZIP com resultados
                    pasta_resultado = pasta_base / "RESULTADO_ENCONTRADO"
                    if pasta_resultado.exists():
                        status_text.info("📦 Compactando resultados...")
                        
                        zip_resultado = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
                        with zipfile.ZipFile(zip_resultado.name, 'w', zipfile.ZIP_DEFLATED) as zipf:
                            for root, dirs, files in os.walk(pasta_resultado):
                                for file in files:
                                    file_path = os.path.join(root, file)
                                    arcname = os.path.relpath(file_path, pasta_resultado.parent)
                                    zipf.write(file_path, arcname)
                        
                        progress_bar.progress(100)
                        
                        # Botão de download
                        with open(zip_resultado.name, 'rb') as f:
                            st.download_button(
                                label="📥 **BAIXAR RESULTADOS (ZIP)**",
                                data=f,
                                file_name="resultados_encontrados.zip",
                                mime="application/zip",
                                use_container_width=True
                            )
                        
                        status_text.success("✅ Pesquisa concluída com sucesso!")
                else:
                    progress_bar.progress(100)
                    st.warning(f"❌ **Nenhuma pasta encontrada** com o nome '{nome_busca}'")
                    st.info("💡 Dica: Verifique se o nome está correto ou tente com variações")
        
        except Exception as e:
            st.error(f"❌ **Erro durante a pesquisa:** {str(e)}")
            st.info("Tente novamente ou verifique se o arquivo ZIP está correto")
        
        finally:
            # Limpar arquivos temporários
            try:
                if 'zip_path' in locals():
                    os.unlink(zip_path)
                if 'extract_path' in locals():
                    shutil.rmtree(extract_path)
            except:
                pass
