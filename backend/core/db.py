# backend/core/db.py
import logging
import firebase_admin
from firebase_admin import credentials, firestore
from typing import List, Dict, Any, Optional # Para type hinting

# Importa os modelos de dados que vamos retornar
from .models import ClientDetail, Service

# Inicializa a conexão com o Firestore
# Assumimos que o Firebase já foi inicializado no main.py
# Uma abordagem alternativa seria inicializá-lo aqui se este módulo fosse independente.
# Por agora, apenas obtemos o cliente do DB.
try:
    db = firestore.client()
except ValueError:
    # Se o app default do Firebase não foi inicializado (ex: em testes)
    # Podemos tentar inicializar aqui, mas é melhor garantir que o main.py o faça.
    logging.warning("Firebase app não inicializado. Tentando inicialização padrão.")
    # Esta linha pode falhar se 'credentials.json' não estiver no caminho esperado
    # A inicialização deve ocorrer no ponto de entrada principal (main.py)
    # cred = credentials.Certificate("credentials.json")
    # firebase_admin.initialize_app(cred)
    # db = firestore.client()
    # A melhor prática é deixar o main.py lidar com a inicialização.
    # Esta função irá falhar se 'db' não for inicializado antes.
    pass # Permite que o código seja importado, mas falhará em tempo de execução se não for inicializado

def get_hairdresser_data_from_db(salao_id: str) -> Optional[Dict[str, Any]]:
    """Busca dados completos do salão (horários, ID calendário, serviços, cores, etc.)."""
    try:
        db = firestore.client() # Obtém a instância inicializada
        doc_ref = db.collection('cabeleireiros').document(salao_id)
        hairdresser_doc = doc_ref.get()

        if not hairdresser_doc.exists:
            logging.warning(f"Documento não encontrado no Firestore para ID: {salao_id}")
            return None # Retorna None se o salão não for encontrado

        hairdresser_data = hairdresser_doc.to_dict()

        # Busca os serviços na sub-coleção 'servicos'
        services_ref = doc_ref.collection('servicos')
        services_stream = services_ref.stream()
        
        # Guarda ID e dados
        services_dict_with_ids = {doc.id: doc.to_dict() for doc in services_stream} 

        # Monta o dicionário final com todos os dados
        # Usamos .get com valores padrão para evitar erros se um campo faltar
        return {
            "nome_salao": hairdresser_data.get('nome_salao'),
            "tagline": hairdresser_data.get('tagline'),
            "calendar_id": hairdresser_data.get('calendar_id'),
            "servicos_data": services_dict_with_ids, # Retorna dict[id, data]
            "dias_trabalho": hairdresser_data.get('dias_trabalho', []),
            "horario_inicio": hairdresser_data.get('horario_inicio', '09:00'),
            "horario_fim": hairdresser_data.get('horario_fim', '18:00'),
            "url_logo": hairdresser_data.get('url_logo'),
            "cor_primaria": hairdresser_data.get('cor_primaria', "#6366F1"),
            "cor_secundaria": hairdresser_data.get('cor_secundaria', "#EC4899"),
            "cor_gradiente_inicio": hairdresser_data.get('cor_gradiente_inicio', "#A78BFA"),
            "cor_gradiente_fim": hairdresser_data.get('cor_gradiente_fim', "#F472B6")
        }
    except Exception as e:
        logging.error(f"Erro ao buscar dados no Firestore para {salao_id}: {e}")
        return None # Retorna None em caso de erro na busca

def get_all_clients_from_db() -> Optional[List[ClientDetail]]:
    """Busca todos os documentos da coleção 'cabeleireiros' (para Admin)."""
    try:
        db = firestore.client() # Obtém a instância inicializada
        clients_ref = db.collection('cabeleireiros').stream()
        clients_list = []
        for doc in clients_ref:
            client_data = doc.to_dict()
            # Usamos ClientDetail diretamente para ter todos os campos na lista do admin
            # Como servicos não estão no doc principal, passamos uma lista vazia
            clients_list.append(ClientDetail(id=doc.id, servicos=[], **client_data))
        return clients_list
    except Exception as e:
        logging.error(f"Erro ao buscar todos os clientes no Firestore: {e}")
        return None
