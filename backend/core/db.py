# backend/core/db.py
import logging
import firebase_admin
from firebase_admin import credentials, firestore
import os

# --- Importação Relativa Corrigida ---
# Importa os modelos Pydantic do arquivo 'models.py' que está NA MESMA PASTA (core)
from .models import ClientDetail, Service 
# --- Fim da Correção ---

try:
    if not firebase_admin._apps:
        # Tenta encontrar a credencial
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        
        # Verificação do caminho (baseado no Root Directory do Render ser 'backend')
        if not os.path.exists(cred_path):
            logging.warning(f"Credencial não encontrada em '{cred_path}', tentando 'backend/credentials.json'")
            cred_path_backend = "backend/credentials.json"
            if os.path.exists(cred_path_backend):
                 cred_path = cred_path_backend
            else:
                 logging.warning(f"Credencial não encontrada em '{cred_path_backend}'. Usando 'credentials.json' como padrão.")
                 cred_path = "credentials.json" # Tenta o caminho simples

        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        logging.info(f"Firebase Admin SDK inicializado (a partir do db.py) com: {cred_path}")
    
    db = firestore.client() # Define a variável db global
except Exception as e:
    logging.error(f"Falha CRÍTICA ao inicializar Firebase no db.py: {e}")
    db = None # Define db como None se a inicialização falhar
# --- Fim da Inicialização ---


# --- Funções DB ---
def get_hairdresser_data_from_db(salao_id: str):
    """Busca dados completos do salão (horários, ID calendário, serviços, cores, etc.)."""
    if db is None:
        logging.error("Firestore DB não está inicializado. get_hairdresser_data_from_db falhou.")
        return None
    try:
        doc_ref = db.collection('cabeleireiros').document(salao_id)
        hairdresser_doc = doc_ref.get()
        if not hairdresser_doc.exists: 
            logging.warning(f"Salão não encontrado no Firestore: {salao_id}")
            return None
        
        hairdresser_data = hairdresser_doc.to_dict()
        
        services_ref = doc_ref.collection('servicos')
        services_stream = services_ref.stream()
        services_dict_with_ids = {doc.id: doc.to_dict() for doc in services_stream} # Guarda ID e dados
        
        # Retorna o dicionário de dados brutos
        return {
            "nome_salao": hairdresser_data.get('nome_salao'), "tagline": hairdresser_data.get('tagline'),
            "calendar_id": hairdresser_data.get('calendar_id'), "servicos_data": services_dict_with_ids,
            "dias_trabalho": hairdresser_data.get('dias_trabalho', []), "horario_inicio": hairdresser_data.get('horario_inicio', '09:00'),
            "horario_fim": hairdresser_data.get('horario_fim', '18:00'), "url_logo": hairdresser_data.get('url_logo'),
            "cor_primaria": hairdresser_data.get('cor_primaria', "#6366F1"), "cor_secundaria": hairdresser_data.get('cor_secundaria', "#EC4899"),
            "cor_gradiente_inicio": hairdresser_data.get('cor_gradiente_inicio', "#A78BFA"), "cor_gradiente_fim": hairdresser_data.get('cor_gradiente_fim', "#F472B6")
        }
    except Exception as e: 
        logging.error(f"Erro ao buscar dados Firestore para {salao_id}: {e}")
        return None

def get_all_clients_from_db():
    """Busca todos os documentos da coleção 'cabeleireiros' (para Admin)."""
    if db is None:
        logging.error("Firestore DB não está inicializado. get_all_clients_from_db falhou.")
        return None
    try:
        clients_ref = db.collection('cabeleireiros').stream()
        clients_list = []
        for doc in clients_ref:
            client_data = doc.to_dict()
            # Usa o modelo Pydantic 'ClientDetail' importado
            clients_list.append(ClientDetail(id=doc.id, servicos=[], **client_data))
        return clients_list
    except Exception as e: 
        logging.error(f"Erro ao buscar todos os clientes: {e}")
        return None

