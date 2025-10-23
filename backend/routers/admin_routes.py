# backend/routers/admin_routes.py
import logging
from fastapi import APIRouter, HTTPException, Depends, status
from typing import List
from firebase_admin import firestore

# Importações relativas da nossa nova estrutura
from core.auth import get_current_user # O nosso "guarda" de segurança
from core.db import get_all_clients_from_db, get_hairdresser_data_from_db
from core.models import ClientDetail, Service, NewClientData
import calendar_service


# Obtém a instância do DB (assumindo que já foi inicializada no main.py)
db = firestore.client()

# Cria um novo "roteador" para os endpoints de administração
# Todos os endpoints aqui serão prefixados com /admin (definiremos isso no main.py)
router = APIRouter(
    prefix="/admin", # Adiciona /admin a todas as rotas deste ficheiro
    tags=["Admin"], # Agrupa na documentação /docs
    dependencies=[Depends(get_current_user)] # <<< PROTEÇÃO GLOBAL!
)

# --- ENDPOINTS PROTEGIDOS DO ADMIN ---

@router.get("/clientes", response_model=List[ClientDetail])
async def list_clients(current_user: dict = Depends(get_current_user)):
    """
    Endpoint protegido para listar todos os clientes (cabeleireiros).
    """
    logging.info(f"Admin {current_user.get('email')} solicitou lista de clientes.")
    clients = get_all_clients_from_db()
    if clients is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao buscar clientes.")
    return clients

@router.get("/clientes/{client_id}", response_model=ClientDetail)
async def get_client_details(client_id: str, current_user: dict = Depends(get_current_user)):
    """
    Endpoint protegido para buscar os detalhes completos de um cliente, incluindo serviços.
    """
    admin_email = current_user.get("email")
    logging.info(f"Admin {admin_email} solicitando detalhes do cliente: {client_id}")
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        client_doc = client_ref.get()
        if not client_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado.")
        
        client_data = client_doc.to_dict()
        
        # Busca os serviços na sub-coleção
        services_ref = client_ref.collection('servicos').stream()
        services_list = [Service(id=doc.id, **doc.to_dict()) for doc in services_ref]
        
        # Usa **client_data para passar todos os campos, Pydantic valida e aplica defaults
        client_details = ClientDetail(id=client_doc.id, servicos=services_list, **client_data)
        return client_details
    except Exception as e:
        logging.exception(f"Erro buscar detalhes cliente {client_id}:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@router.post("/clientes", response_model=ClientDetail, status_code=status.HTTP_201_CREATED)
async def create_client(client_data: NewClientData, current_user: dict = Depends(get_current_user)):
    """
    Endpoint protegido para criar um novo cliente (cabeleireiro).
    """
    admin_email = current_user.get("email")
    logging.info(f"Admin {admin_email} criando: {client_data.nome_salao}")
    
    client_id = client_data.numero_whatsapp # Pydantic já validou o formato
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if client_ref.get().exists:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cliente {client_id} já existe.")
        
        data_to_save = client_data.dict(exclude_unset=True) # Não salva campos não enviados
        client_ref.set(data_to_save)
        logging.info(f"Cliente '{data_to_save['nome_salao']}' criado ID: {client_id}")
        
        # Retorna ClientDetail completo (serviços vazio)
        return ClientDetail(id=client_id, servicos=[], **data_to_save)
    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro ao criar cliente:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@router.put("/clientes/{client_id}", response_model=ClientDetail)
async def update_client(client_id: str, client_update_data: ClientDetail, current_user: dict = Depends(get_current_user)):
    """
    Endpoint protegido para atualizar TODOS os dados de um cliente, incluindo serviços.
    """
    admin_email = current_user.get("email")
    logging.info(f"Admin {admin_email} atualizando: {client_id}")
    if client_id != client_update_data.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID URL não corresponde aos dados.")
    
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if not client_ref.get(retry=None, timeout=None).exists: # Verificação simples
             raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado.")
        
        # Prepara dados principais e serviços
        client_info = client_update_data.dict(exclude={'servicos', 'id'}, exclude_unset=True)
        updated_services = client_update_data.servicos

        @firestore.transactional
        def update_in_transaction(transaction, client_ref, client_info_to_save, services_to_save):
            # 1. Leituras primeiro
            services_ref = client_ref.collection('servicos')
            old_services_refs = [doc.reference for doc in services_ref.stream(transaction=transaction)]
            
            # 2. Escritas depois
            transaction.update(client_ref, client_info_to_save) # Usa UPDATE para mesclar
            for old_ref in old_services_refs:
                transaction.delete(old_ref)
            for service_data in services_to_save:
                 new_service_ref = services_ref.document()
                 service_dict = service_data.dict(exclude={'id'}, exclude_unset=True, exclude_none=True)
                 transaction.set(new_service_ref, service_dict)

        transaction = db.transaction()
        update_in_transaction(transaction, client_ref, client_info, updated_services)
        logging.info(f"Cliente '{client_update_data.nome_salao}' atualizado.")
        
        # Busca novamente para retornar o estado atualizado com os IDs dos serviços
        updated_details = await get_client_details(client_id, current_user)
        return updated_details
        
    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao atualizar cliente {client_id}:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")
