# backend/routers/admin_routes.py
import logging
import os
import re
from fastapi import APIRouter, Depends, HTTPException, status, Query
from fastapi.responses import RedirectResponse # MANTIDO para o /callback
from firebase_admin import firestore
from typing import List, Optional, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field 
# --- NOVOS IMPORTS PARA GOOGLE OAUTH ---
from google_auth_oauthlib.flow import Flow
# --- FIM DOS NOVOS IMPORTS ---

# Importações dos nossos módulos refatorados
from core.models import ClientDetail, NewClientData, Service
from core.auth import get_current_user 
from core.db import get_all_clients_from_db, get_hairdresser_data_from_db, db
import calendar_service # <<< ADICIONADO >>>

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

# ... (Constantes GOOGLE_CLIENT_ID, SCOPES, REDIRECT_URI, etc. - Sem alteração) ...
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
SCOPES = ['https://www.googleapis.com/auth/calendar']
RENDER_API_URL = "https://api-agendador.onrender.com" 
REDIRECT_URI = f"{RENDER_API_URL}/api/v1/admin/google/auth/callback"


# --- Modelos Pydantic (Sem alteração nos seus, apenas adição) ---
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
    
# <<< ADICIONADO: Modelo para o corpo (body) do Reagendamento >>>
class ReagendamentoBody(BaseModel):
    new_start_time: str # Espera uma string ISO (ex: "2025-10-27T14:00:00-03:00")
    
    
# --- ENDPOINTS OAUTH (Sem alterações) ---

@router.get("/google/auth/start", response_model=dict[str, str])
async def google_auth_start(current_user: dict[str, Any] = Depends(get_current_user)):
    # ... (Seu código aqui - Sem alteração) ...
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logging.error("Credenciais OAuth do Google não configuradas no ambiente.")
        raise HTTPException(status_code=500, detail="Integração com Google não configurada.")
    
    client_config = {
        "web": {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token"
        }
    }
    
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
    
    return {"authorization_url": authorization_url}

@router.get("/user/salao-id", response_model=dict[str, str])
async def get_salao_id_for_user(current_user: dict[str, Any] = Depends(get_current_user)):
    # ... (Seu código aqui - Sem alteração) ...
    user_uid = current_user.get("uid")
    logging.info(f"Admin (UID: {user_uid}) solicitou ID do salão.")
    
    try:
        clients_ref = db.collection('cabeleireiros')
        query = clients_ref.where('ownerUID', '==', user_uid).limit(1) 
        client_doc_list = list(query.stream()) 
        
        if not client_doc_list:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, 
                                detail="Nenhum salão encontrado para esta conta de usuário.")
                    
        salao_id = client_doc_list[0].id 
        return {"salao_id": salao_id}
        
    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro ao buscar salão por UID ({user_uid}): {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao buscar o ID do salão.")

# --- Endpoints CRUD de Clientes (Sem alterações) ---

@router.get("/clientes", response_model=List[ClientDetail])
async def list_clients(current_user: dict = Depends(get_current_user)):
    # ... (Seu código aqui - Sem alteração) ...
    logging.info(f"Admin {current_user.get('email')} solicitou lista de clientes.")
    clients = get_all_clients_from_db()
    if clients is None: raise HTTPException(status_code=500, detail="Erro ao buscar clientes.")
    return clients

@router.get("/clientes/{client_id}", response_model=ClientDetail)
async def get_client_details(client_id: str, current_user: dict = Depends(get_current_user)):
    # ... (Seu código aqui - Sem alteração) ...
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

@router.post("/clientes", response_model=ClientDetail, status_code=status.HTTP_201_CREATED)
async def create_client(client_data: NewClientData, current_user: dict = Depends(get_current_user)):
    # ... (Seu código aqui - Sem alteração) ...
    admin_email = current_user.get("email")
    user_uid = current_user.get("uid")
    logging.info(f"Admin {admin_email} (UID: {user_uid}) criando: {client_data.nome_salao}")
    client_id = client_data.numero_whatsapp
    
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if client_ref.get().exists: 
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cliente {client_id} já existe.")
        
        data_to_save = client_data.dict() 
        
        data_to_save['ownerUID'] = user_uid 
        
        client_ref.set(data_to_save)
        logging.info(f"Cliente '{data_to_save['nome_salao']}' (Dono: {user_uid}) criado ID: {client_id}")
        
        return ClientDetail(id=client_id, servicos=[], **data_to_save)
        
    except HTTPException as httpe: raise httpe
    except Exception as e:
        logging.exception(f"Erro ao criar cliente:");
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@router.put("/clientes/{client_id}", response_model=ClientDetail)
async def update_client(client_id: str, client_update_data: ClientDetail, current_user: dict = Depends(get_current_user)):
    # ... (Seu código aqui - Sem alteração) ...
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


# --- <<< MODIFICADO: Agendamento Manual agora Sincroniza com Google >>> ---
@router.post("/calendario/agendar", status_code=status.HTTP_201_CREATED)
async def create_manual_appointment(
    manual_data: ManualAppointmentData,
    current_user: dict[str, Any] = Depends(get_current_user)
):
    """
    Endpoint protegido para o dono do salão adicionar um agendamento manualmente.
    Salva no Firestore E SINCRONIZA com Google Calendar (se ativo).
    """
    user_email = current_user.get("email")
    salao_id = manual_data.salao_id # <<< Pega o salao_id
    logging.info(f"Admin {user_email} criando agendamento manual para {salao_id}")
    
    try:
        start_time_dt = datetime.fromisoformat(manual_data.start_time)
        end_time_dt = start_time_dt + timedelta(minutes=manual_data.duration_minutes)
        
        agendamento_data = {
            "salaoId": salao_id,
            "serviceName": manual_data.service_name,
            "durationMinutes": manual_data.duration_minutes,
            "startTime": start_time_dt,
            "endTime": end_time_dt,
            "customerName": manual_data.customer_name,
            "customerPhone": manual_data.customer_phone or "N/A",
            "status": "manual", 
            "createdBy": user_email, 
            "createdAt": firestore.SERVER_TIMESTAMP 
        }
        
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento manual criado no Firestore com ID: {agendamento_ref.id}")

        # --- <<< ADICIONADO: Lógica de Sincronização Google >>> ---
        salon_data = get_hairdresser_data_from_db(salao_id)
        if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
            logging.info("Sincronização Google Ativa para agendamento manual.")
            
            google_event_data = {
                "summary": f"{manual_data.service_name} - {manual_data.customer_name}",
                "description": f"Agendamento via Horalis (Manual).\nCliente: {manual_data.customer_name}\nTelefone: {manual_data.customer_phone}\nServiço: {manual_data.service_name}",
                "start_time_iso": start_time_dt.isoformat(),
                "end_time_iso": end_time_dt.isoformat(),
            }
            
            try:
                google_event_id = calendar_service.create_google_event_with_oauth(
                    refresh_token=salon_data.get("google_refresh_token"),
                    event_data=google_event_data
                )
                if google_event_id:
                    agendamento_ref.update({"googleEventId": google_event_id})
                    logging.info(f"Agendamento manual salvo no Google Calendar. ID: {google_event_id}")
                else:
                    logging.warning("Falha ao salvar agendamento manual no Google Calendar (função retornou None).")
            except Exception as e:
                logging.error(f"Erro ao salvar agendamento manual no Google Calendar: {e}")
        else:
            logging.info("Sincronização Google desativada. Pulando etapa para agendamento manual.")
        # --- <<< FIM DA ADIÇÃO >>> ---
        
        return {"message": "Agendamento manual criado com sucesso!", "id": agendamento_ref.id}

    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento manual:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao criar agendamento manual.")
# --- <<< FIM DA MODIFICAÇÃO >>> ---


# --- Endpoint de Leitura do Calendário (Sem alterações) ---
@router.get("/calendario/{salao_id}/eventos", response_model=List[CalendarEvent])
async def get_calendar_events(
    salao_id: str, 
    start: str, # FullCalendar envia ?start=...&end=...
    end: str,
    current_user: dict = Depends(get_current_user)
):
    # ... (Seu código aqui - Sem alteração) ...
    admin_email = current_user.get("email")
    logging.info(f"Admin {admin_email} buscando eventos para {salao_id} de {start} a {end}")
    
    try:
        start_dt_utc = datetime.fromisoformat(start)
        end_dt_utc = datetime.fromisoformat(end)

        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        
        query = agendamentos_ref.where("startTime", ">=", start_dt_utc).where("startTime", "<=", end_dt_utc)
        docs = query.stream()

        eventos = []
        for doc in docs:
            data = doc.to_dict()
            
            evento_formatado = CalendarEvent(
                id=doc.id,
                title=f"{data.get('serviceName', 'Serviço')} - {data.get('customerName', 'Cliente')}",
                start=data['startTime'], 
                end=data['endTime'],
                extendedProps={ 
                    "customerName": data.get('customerName'),
                    "customerPhone": data.get('customerPhone'),
                    "serviceName": data.get('serviceName'),
                    # <<< ADICIONADO: Envia o googleEventId para o modal no frontend >>>
                    "googleEventId": data.get("googleEventId") 
                }
            )
            eventos.append(evento_formatado)
        
        logging.info(f"Retornando {len(eventos)} eventos para o FullCalendar.")
        return eventos

    except Exception as e:
        logging.exception(f"Erro ao buscar eventos do calendário para {salao_id}:")
        raise HTTPException(status_code=500, detail="Erro interno ao buscar eventos.")


# --- <<< ADICIONADO: NOVOS ENDPOINTS DE CANCELAR E REAGENDAR >>> ---

@router.delete("/calendario/{salao_id}/agendamentos/{agendamento_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    salao_id: str, 
    agendamento_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Cancela um agendamento (Deleta do Firestore e do Google Calendar)
    """
    logging.info(f"Admin {current_user.get('email')} cancelando agendamento: {agendamento_id}")
    
    try:
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
        agendamento_doc = agendamento_ref.get()

        if not agendamento_doc.exists:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
        agendamento_data = agendamento_doc.to_dict()
        google_event_id = agendamento_data.get("googleEventId")

        # 1. Sincronização: Deletar do Google Calendar (se existir)
        if google_event_id:
            salon_data = get_hairdresser_data_from_db(salao_id)
            refresh_token = salon_data.get("google_refresh_token")
            if refresh_token:
                logging.info(f"Tentando deletar evento do Google Calendar: {google_event_id}")
                # Chamada assíncrona (não bloqueia)
                calendar_service.delete_google_event(refresh_token, google_event_id)
            else:
                logging.warning(f"Não foi possível deletar {google_event_id} do Google. Refresh token não encontrado.")
        
        # 2. Deletar do Firestore
        agendamento_ref.delete()
        logging.info(f"Agendamento {agendamento_id} deletado do Firestore.")
        
        return # Retorna 204 No Content

    except Exception as e:
        logging.exception(f"Erro ao cancelar agendamento {agendamento_id}:")
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")


@router.patch("/calendario/{salao_id}/agendamentos/{agendamento_id}")
async def reschedule_appointment(
    salao_id: str, 
    agendamento_id: str,
    body: ReagendamentoBody, # <<< Usa o Pydantic Model
    current_user: dict = Depends(get_current_user)
):
    """
    Reagenda um agendamento (Atualiza no Firestore e no Google Calendar)
    """
    logging.info(f"Admin {current_user.get('email')} reagendando {agendamento_id} para {body.new_start_time}")
    
    try:
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
        agendamento_doc = agendamento_ref.get()

        if not agendamento_doc.exists:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
        agendamento_data = agendamento_doc.to_dict()
        google_event_id = agendamento_data.get("googleEventId")
        duration = agendamento_data.get("durationMinutes")

        if not duration:
            raise HTTPException(status_code=500, detail="Agendamento não possui duração definida.")

        # Calcular novos horários
        new_start_dt = datetime.fromisoformat(body.new_start_time)
        new_end_dt = new_start_dt + timedelta(minutes=duration)
        
        # 1. Sincronização: Atualizar no Google Calendar
        if google_event_id:
            salon_data = get_hairdresser_data_from_db(salao_id)
            refresh_token = salon_data.get("google_refresh_token")
            if refresh_token:
                logging.info(f"Tentando atualizar evento do Google Calendar: {google_event_id}")
                calendar_service.update_google_event(
                    refresh_token, 
                    google_event_id, 
                    new_start_dt.isoformat(), 
                    new_end_dt.isoformat()
                )
            else:
                logging.warning(f"Não foi possível atualizar {google_event_id} no Google. Refresh token não encontrado.")

        # 2. Atualizar no Firestore
        agendamento_ref.update({
            "startTime": new_start_dt,
            "endTime": new_end_dt
        })
        logging.info(f"Agendamento {agendamento_id} atualizado no Firestore.")

        return {"message": "Agendamento reagendado com sucesso."}

    except Exception as e:
        logging.exception(f"Erro ao reagendar agendamento {agendamento_id}:")
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")

# --- <<< FIM DAS ADIÇÕES >>> ---


# --- ROTEADOR PÚBLICO PARA O CALLBACK (Sem alterações) ---
@callback_router.get("/google/auth/callback")
async def google_auth_callback_handler(
    state: str, # O UID do Firebase que enviámos
    code: str, # O código de autorização do Google
    scope: str  # Os escopos que o Google aprovou
):
    # ... (Seu código aqui - Sem alteração) ...
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