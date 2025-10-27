# backend/routers/admin_routes.py
# backend/routers/admin_routes.py
import logging
import os
import re
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse # MANTIDO para o /callback
from firebase_admin import firestore
from typing import List, Optional, dict, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field 
# --- NOVOS IMPORTS PARA GOOGLE OAUTH ---
from google_auth_oauthlib.flow import Flow
# --- FIM DOS NOVOS IMPORTS ---

# Importações dos nossos módulos refatorados
from core.models import ClientDetail, NewClientData, Service
from core.auth import get_current_user 
from core.db import get_all_clients_from_db, get_hairdresser_data_from_db, db
# --- Configuração do Roteador Admin ---
router = APIRouter(
    prefix="/admin", # Todas as rotas aqui começarão com /admin
    tags=["Admin"], # Agrupa na documentação do /docs
    dependencies=[Depends(get_current_user)] # Proteção GLOBAL para /admin
)

callback_router = APIRouter(
    prefix="/admin", 
    tags=["Admin - OAuth Callback"],
    # SEM 'dependencies'
)

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
SCOPES = ['https://www.googleapis.com/auth/calendar']
RENDER_API_URL = "https://api-agendador.onrender.com" 
REDIRECT_URI = f"{RENDER_API_URL}/api/v1/admin/google/auth/callback"

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
    
    
# --- NOVOS ENDPOINTS OAUTH ---

# --- CORREÇÃO AQUI: MUDAR DE Flow.from_client_secrets_file PARA Flow.from_client_config ---
@router.get("/google/auth/start", response_model=dict[str, str]) # Define o modelo de resposta
async def google_auth_start(current_user: dict[str, Any] = Depends(get_current_user)):
    """
    PASSO 1: Inicia o fluxo OAuth2.
    Retorna a URL de autorização do Google para o frontend.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logging.error("Credenciais OAuth do Google não configuradas no ambiente.")
        raise HTTPException(status_code=500, detail="Integração com Google não configurada.")
    
    # Configuração do cliente (o que 'from_client_secrets_file' faria)
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }
    
    # USA 'from_client_config' EM VEZ DE 'from_client_secrets_file(None,...)'
    flow = Flow.from_client_config(
        client_config=client_config, 
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent', 
        state=current_user.get("uid") # Passa o UID do Firebase para o próximo passo
    )
    
    logging.info(f"Enviando URL de autorização do Google para o usuário {current_user.get('email')}...")
    
    # Retorna o JSON (como o frontend espera)
    return {"authorization_url": authorization_url}
# --- FIM DA CORREÇÃO ---

@router.get("/user/salao-id", response_model=dict[str, str])
async def get_salao_id_for_user(current_user: dict[str, Any] = Depends(get_current_user)):
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
    current_user: dict[str, Any] = Depends(get_current_user)
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
    
    # --- ROTEADOR PÚBLICO PARA O CALLBACK ---
# Precisamos de um novo router que NÃO tenha a dependência de autenticação
# para o Google poder chamar o /callback


@callback_router.get("/google/auth/callback")
async def google_auth_callback_handler(
    state: str, # O UID do Firebase que enviámos
    code: str, # O código de autorização do Google
    scope: str  # Os escopos que o Google aprovou
):
    """
    PASSO 2: O Google redireciona o usuário para cá após o consentimento.
    Troca o 'code' por um 'refresh_token' e salva-o no Firestore.
    """
    logging.info(f"Recebido callback do Google para o state (UID): {state}")
    
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logging.error("Credenciais OAuth do Google não configuradas no ambiente.")
        raise HTTPException(status_code=500, detail="Integração com Google não configurada.")
    
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID, "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }
    
    flow = Flow.from_client_config(
        client_config=client_config,
        scopes=SCOPES,
        redirect_uri=REDIRECT_URI
    )
    
    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
        refresh_token = credentials.refresh_token
        
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Falha ao obter o token de atualização do Google. Tente remover o acesso Horalis da sua conta Google e tente novamente.")

        user_uid = state
        clients_ref = db.collection('cabeleireiros')
        query = clients_ref.where('ownerUID', '==', user_uid).limit(1) 
        client_doc_list = list(query.stream())
        
        if not client_doc_list:
            raise HTTPException(status_code=404, detail="Usuário autenticado, mas nenhum salão Horalis encontrado.")
            
        salao_doc_ref = client_doc_list[0].reference
        
        salao_doc_ref.update({
            "google_refresh_token": refresh_token,
            "google_sync_enabled": True
        })
        
        logging.info(f"Refresh Token do Google salvo com sucesso para o salão: {salao_doc_ref.id}")

        frontend_redirect_url = f"https://horalis.rebdigitalsolucoes.com.br/painel/{salao_doc_ref.id}/configuracoes?sync=success"
        return RedirectResponse(frontend_redirect_url)

    except Exception as e:
        logging.exception(f"Erro CRÍTICO durante o callback do Google OAuth: {e}")
        frontend_error_url = f"https://horalis.rebdigitalsolucoes.com.br/painel/{state}/configuracoes?sync=error"
        return RedirectResponse(frontend_error_url)
# --- FIM DOS ENDPOINTS OAUTH ---