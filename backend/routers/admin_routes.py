# backend/routers/admin_routes.py
import logging
from fastapi import APIRouter, Depends, HTTPException, status, Query
from firebase_admin import firestore
from typing import List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel, Field # Adicionado Field para validação

# Importações dos nossos módulos refatorados
from core.models import ClientDetail, NewClientData, Service
from core.auth import get_current_user # O nosso "guarda" de segurança
from core.db import get_all_clients_from_db, get_hairdresser_data_from_db, db # Importa a instância 'db'

# --- Configuração do Roteador Admin ---
router = APIRouter(
    prefix="/admin", # Todas as rotas aqui começarão com /admin
    tags=["Admin"], # Agrupa na documentação do /docs
    dependencies=[Depends(get_current_user)] # Proteção GLOBAL para /admin
)

# --- Modelo de Evento (O que o FullCalendar espera) ---
class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    backgroundColor: Optional[str] = None
    borderColor: Optional[str] = None
    extendedProps: Optional[dict] = None
    
class ManualAppointmentData(BaseModel):
    salao_id: str
    start_time: str # ISO string
    duration_minutes: int
    customer_name: str = Field(..., min_length=2)
    customer_phone: Optional[str] = None
    service_name: str = Field(..., min_length=3)
    # Não precisamos de service_id, pois é um agendamento manual

@router.get("/user/salao-id", response_model=dict[str, str])
async def get_salao_id_for_user(current_user: dict[str, any] = Depends(get_current_user)):
    """Busca o numero_whatsapp (ID do salão) associado ao usuário logado (pelo UID)."""
    user_uid = current_user.get("uid")
    logging.info(f"Admin (UID: {user_uid}) solicitou ID do salão.")
    
    try:
        clients_ref = db.collection('cabeleireiros')
        # Busca o salão que tenha o campo 'ownerUID' igual ao UID do usuário logado
        query = clients_ref.where('ownerUID', '==', user_uid).limit(1) 
        client_doc_list = list(query.stream()) # Executa a query
        
        if not client_doc_list:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, 
                                detail="Nenhum salão encontrado para esta conta de usuário.")
                                
        salao_id = client_doc_list[0].id # O ID do documento é o numero_whatsapp
        return {"salao_id": salao_id}
        
    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro ao buscar salão por UID ({user_uid}): {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao buscar o ID do salão.")

# --- Endpoints CRUD de Clientes (Sem alterações) ---

@router.get("/clientes", response_model=List[ClientDetail])
async def list_clients(current_user: dict = Depends(get_current_user)):
    # ... (código existente) ...
    logging.info(f"Admin {current_user.get('email')} solicitou lista de clientes.")
    clients = get_all_clients_from_db()
    if clients is None: raise HTTPException(status_code=500, detail="Erro ao buscar clientes.")
    return clients

@router.get("/clientes/{client_id}", response_model=ClientDetail)
async def get_client_details(client_id: str, current_user: dict = Depends(get_current_user)):
    # ... (código existente) ...
    admin_email = current_user.get("email"); logging.info(f"Admin {admin_email} detalhes cliente: {client_id}")
    try:
        client_ref = db.collection('cabeleireiros').document(client_id); client_doc = client_ref.get()
        if not client_doc.exists: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado.")
        client_data = client_doc.to_dict()
        services_ref = client_ref.collection('servicos').stream()
        services_list = [Service(id=doc.id, **doc.to_dict()) for doc in services_ref]
        client_details = ClientDetail(id=client_doc.id, servicos=services_list, **client_data) 
        return client_details
    except Exception as e: logging.exception(f"Erro buscar detalhes cliente {client_id}:"); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")
# --- Endpoint de Criação (CORRIGIDO PARA SALVAR O UID) ---
@router.post("/clientes", response_model=ClientDetail, status_code=status.HTTP_201_CREATED)
async def create_client(client_data: NewClientData, current_user: dict = Depends(get_current_user)):
    """Cria um novo cliente (cabeleireiro), salvando o UID do dono."""
    admin_email = current_user.get("email")
    user_uid = current_user.get("uid")
    logging.info(f"Admin {admin_email} (UID: {user_uid}) criando: {client_data.nome_salao}")
    client_id = client_data.numero_whatsapp
    
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if client_ref.get().exists: 
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cliente {client_id} já existe.")
        
        data_to_save = client_data.dict() 
        
        # --- ADIÇÃO CRÍTICA ---
        data_to_save['ownerUID'] = user_uid # <<< Vincula o salão ao usuário
        # --- FIM DA ADIÇÃO ---
        
        client_ref.set(data_to_save)
        logging.info(f"Cliente '{data_to_save['nome_salao']}' (Dono: {user_uid}) criado ID: {client_id}")
        
        return ClientDetail(id=client_id, servicos=[], **data_to_save)
        
    except HTTPException as httpe: raise httpe
    except Exception as e:
        logging.exception(f"Erro ao criar cliente:");
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@router.put("/clientes/{client_id}", response_model=ClientDetail)
async def update_client(client_id: str, client_update_data: ClientDetail, current_user: dict = Depends(get_current_user)):
    # ... (código existente da transação) ...
    admin_email = current_user.get("email"); logging.info(f"Admin {admin_email} atualizando: {client_id}")
    if client_id != client_update_data.id: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID URL não corresponde aos dados.")
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if not client_ref.get(retry=None, timeout=None).exists: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado.")
        
        client_info = client_update_data.dict(exclude={'servicos', 'id'}, exclude_unset=True)
        updated_services = client_update_data.servicos

        @firestore.transactional
        def update_in_transaction(transaction, client_ref, client_info_to_save, services_to_save):
            services_ref = client_ref.collection('servicos')
            old_services_refs = [doc.reference for doc in services_ref.stream(transaction=transaction)]
            transaction.update(client_ref, client_info_to_save) # Usa UPDATE
            for old_ref in old_services_refs: transaction.delete(old_ref)
            for service_data in services_to_save:
                 new_service_ref = services_ref.document()
                 service_dict = service_data.dict(exclude={'id'}, exclude_unset=True, exclude_none=True)
                 transaction.set(new_service_ref, service_dict)

        transaction = db.transaction()
        update_in_transaction(transaction, client_ref, client_info, updated_services)
        logging.info(f"Cliente '{client_update_data.nome_salao}' atualizado.")
        
        updated_details = await get_client_details(client_id, current_user)
        return updated_details
        
    except HTTPException as httpe: raise httpe
    except Exception as e: logging.exception(f"Erro CRÍTICO ao atualizar cliente {client_id}:"); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@router.post("/calendario/agendar", status_code=status.HTTP_201_CREATED)
async def create_manual_appointment(
    manual_data: ManualAppointmentData,
    current_user: dict[str, any] = Depends(get_current_user)
):
    """
    Endpoint protegido para o dono do salão adicionar um agendamento manualmente.
    Salva diretamente no Firestore na coleção 'agendamentos'.
    """
    user_email = current_user.get("email")
    logging.info(f"Admin {user_email} criando agendamento manual para {manual_data.salao_id}")

    # 1. Validação de Conflito (Ainda vamos implementar a verificação no Firestore)
    # Por enquanto, assumimos que o dono do salão sabe o que está a fazer.

    try:
        # Converte a string ISO 'start_time' para um objeto datetime
        start_time_dt = datetime.fromisoformat(manual_data.start_time)
        end_time_dt = start_time_dt + timedelta(minutes=manual_data.duration_minutes)
        
        # 2. Preparar os dados para o Firestore
        agendamento_data = {
            "salaoId": manual_data.salao_id,
            "serviceName": manual_data.service_name,
            "durationMinutes": manual_data.duration_minutes,
            "startTime": start_time_dt,
            "endTime": end_time_dt,
            "customerName": manual_data.customer_name,
            "customerPhone": manual_data.customer_phone or "N/A",
            "status": "manual", # Indica que foi inserido manualmente pelo salão
            "createdBy": user_email, # Quem inseriu
            "createdAt": firestore.SERVER_TIMESTAMP 
        }
        
        # 3. Salvar na sub-coleção 'agendamentos'
        agendamento_ref = db.collection('cabeleireiros').document(manual_data.salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        
        logging.info(f"Agendamento manual criado com ID: {agendamento_ref.id} pelo admin {user_email}")
        
        return {"message": "Agendamento manual criado com sucesso!", "id": agendamento_ref.id}

    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento manual:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao criar agendamento manual.")

# --- NOVO ENDPOINT PARA O CALENDÁRIO DO PAINEL ---
@router.get("/calendario/{salao_id}/eventos", response_model=List[CalendarEvent])
async def get_calendar_events(
    salao_id: str, 
    start: str, # FullCalendar envia ?start=...&end=...
    end: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Busca os agendamentos de um salão específico no Firestore
    e os formata para o FullCalendar.
    """
    admin_email = current_user.get("email")
    logging.info(f"Admin {admin_email} buscando eventos para {salao_id} de {start} a {end}")
    
    # (Validação futura: O admin logado pode ver este salao_id?)
    
    try:
        # Converte as strings ISO do FullCalendar para objetos datetime
        start_dt_utc = datetime.fromisoformat(start)
        end_dt_utc = datetime.fromisoformat(end)

        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        
        # Query: busca agendamentos que comecem DENTRO da janela de visão do calendário
        query = agendamentos_ref.where("startTime", ">=", start_dt_utc).where("startTime", "<=", end_dt_utc)
        docs = query.stream()

        eventos = []
        for doc in docs:
            data = doc.to_dict()
            
            # (Opcional: Adicionar cores baseadas no serviço, etc.)
            
            evento_formatado = CalendarEvent(
                id=doc.id,
                # Título do evento: "Nome do Serviço - Nome do Cliente"
                title=f"{data.get('serviceName', 'Serviço')} - {data.get('customerName', 'Cliente')}",
                start=data['startTime'], # Já está como datetime
                end=data['endTime'],     # Já está como datetime
                extendedProps={ # Dados extras para o clique
                    "customerName": data.get('customerName'),
                    "customerPhone": data.get('customerPhone'),
                    "serviceName": data.get('serviceName'),
                }
            )
            eventos.append(evento_formatado)
        
        logging.info(f"Retornando {len(eventos)} eventos para o FullCalendar.")
        return eventos

    except Exception as e:
        logging.exception(f"Erro ao buscar eventos do calendário para {salao_id}:")
        raise HTTPException(status_code=500, detail="Erro interno ao buscar eventos.")