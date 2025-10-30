# backend/routers/admin_routes.py
from dotenv import load_dotenv
load_dotenv() 
import logging
import os
import re
import pytz # <<< ADICIONADO (para o fuso do reagendamento)
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request
from fastapi.responses import RedirectResponse 
from firebase_admin import firestore
from typing import List, Optional, Any
from datetime import datetime, timedelta
from pydantic import BaseModel, Field,EmailStr
from google_auth_oauthlib.flow import Flow
import mercadopago # Importa a biblioteca
from firebase_admin import auth as admin_auth
# Importações dos nossos módulos refatorados
from core.models import ClientDetail, NewClientData, Service, ManualAppointmentData
from core.auth import get_current_user 
from core.db import get_all_clients_from_db, get_hairdresser_data_from_db, db
import calendar_service 
import email_service # <<< AGORA VAMOS USAR AS NOVAS FUNÇÕES
API_BASE_URL = "https://api-agendador.onrender.com/api/v1"
sdk = mercadopago.SDK("TEST_ACCESS_TOKEN")

# --- Configuração do Roteador Admin ---
router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(get_current_user)] # Proteção GLOBAL para este router
)
callback_router = APIRouter(
    prefix="/admin", 
    tags=["Admin - OAuth Callback"],
)
webhook_router = APIRouter(
    prefix="/webhooks",
    tags=["Webhooks"]
    # Sem 'dependencies'
)
auth_router = APIRouter(
    prefix="/auth",
    tags=["Autenticação"],
    # Sem 'dependencies', pois é público
)

# ... (Constantes GOOGLE_CLIENT_ID, SCOPES, REDIRECT_URI, etc. - Sem alteração) ...
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
SCOPES = ['https://www.googleapis.com/auth/calendar']
RENDER_API_URL = "https://api-agendador.onrender.com/api/v1" 
REDIRECT_URI = f"{RENDER_API_URL}/api/v1/admin/google/auth/callback"


# --- Modelos Pydantic (Sem alteração) ---
class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    backgroundColor: Optional[str] = None
    borderColor: Optional[str] = None
    extendedProps: Optional[dict] = None

class ReagendamentoBody(BaseModel):
    new_start_time: str 

class UserSignupPayload(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    nome_salao: str = Field(..., min_length=2)
    numero_whatsapp: str # O React já envia formatado com +55

try:
    MP_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
    if not MP_ACCESS_TOKEN:
        logging.warning("MERCADO_PAGO_ACCESS_TOKEN não está configurado.")
        sdk = None
        mp_preference_client = None # Cliente de Pagamento Único
        mp_payment_client = None    # Cliente para consultar Pagamentos
    else:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        # Inicializa os clientes que vamos usar (baseado no seu sdk.py)
        mp_preference_client = sdk.preference()
        mp_payment_client = sdk.payment()
        logging.info("SDK do Mercado Pago (Preference e Payment) inicializados.")
except Exception as e:
    logging.error(f"Erro ao inicializar SDK Mercado Pago: {e}")
    sdk = None
    mp_preference_client = None
    mp_payment_client = None
# --- <<< FIM DA ALTERAÇÃO >>> ---

# --- <<< NOVO: ENDPOINT PÚBLICO DE CADASTRO COM PAGAMENTO >>> ---
@auth_router.post("/iniciar-cadastro-com-pagamento", status_code=status.HTTP_201_CREATED)
async def iniciar_cadastro_com_pagamento(payload: UserSignupPayload):
    """
    Endpoint PÚBLICO para iniciar o fluxo de cadastro pago.
    1. Valida se o e-mail e WhatsApp já existem.
    2. Cria o usuário no Firebase Auth.
    3. Cria o documento do salão no Firestore com status "pending".
    4. Gera o link de checkout do MercadoPago (lógica copiada do endpoint de admin).
    5. Retorna o link para o frontend.
    """
    
    if not mp_preference_client:
        raise HTTPException(status_code=503, detail="Serviço de pagamento indisponível.")

    # O ID do Salão/Documento será o número de WhatsApp formatado
    salao_id = payload.numero_whatsapp
    
    # --- Passo 1: Validação ---
    try:
        # 1a. Verifica se o e-mail já está em uso no Auth
        admin_auth.get_user_by_email(payload.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este e-mail já está cadastrado. Tente fazer login."
        )
    except admin_auth.UserNotFoundError:
        pass # E-mail está livre
    except Exception as e:
        logging.error(f"Erro ao verificar e-mail no Auth: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao verificar e-mail: {e}")

    # 1b. Verifica se o WhatsApp (ID do Salão) já está em uso no Firestore
    try:
        salao_doc = db.collection('cabeleireiros').document(salao_id).get()
        if salao_doc.exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este número de WhatsApp já está cadastrado."
            )
    except Exception as e:
        logging.error(f"Erro ao verificar doc do salão: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao verificar dados: {e}")

    
    # --- Passo 2: Criar Usuário no Firebase Auth ---
    uid = None
    try:
        logging.info(f"Criando usuário no Auth para {payload.email}...")
        new_user = admin_auth.create_user(
            email=payload.email,
            password=payload.password,
            display_name=payload.nome_salao
        )
        uid = new_user.uid
        logging.info(f"Usuário criado no Auth com UID: {uid}")
    except Exception as e:
        logging.error(f"Erro ao criar usuário no Auth: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar usuário: {e}")

    
    # --- Passo 3: Criar Salão no Firestore (como "pending") ---
    try:
        logging.info(f"Criando documento do salão (pending) com ID: {salao_id} para UID: {uid}...")
        now = datetime.now(pytz.utc) # Pega o tempo em UTC
        salao_data = {
            "nome_salao": payload.nome_salao,
            "numero_whatsapp": payload.numero_whatsapp,
            "email": payload.email,
            "ownerUID": uid, # Vincula ao usuário do Auth
            "createdAt": now,
            
            # --- Status de Assinatura Inicial (PENDENTE) ---
            "subscriptionStatus": "pending", 
            "paidUntil": None,
            "subscriptionLastUpdated": now,
            "trialEndsAt": None, # Sem trial
            "mercadopago_customer_id": None,
            "google_sync_enabled": False, # Inicia desabilitado
        }
        db.collection("cabeleireiros").document(salao_id).set(salao_data)
        
    except Exception as e:
        logging.error(f"Erro ao criar salão no Firestore: {e}. Fazendo rollback do Auth...")
        admin_auth.delete_user(uid) # Rollback 1
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao salvar dados do salão: {e}")


    # --- Passo 4: Gerar Link de Pagamento (Lógica copiada do seu endpoint existente) ---
    try:
        logging.info(f"Gerando link de pagamento para {salao_id}...")
        
        # URL para onde o cliente volta APÓS pagar (página pública de sucesso)
        back_url_success = "https://horalis.app/login?cadastro=sucesso"
        notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"

        preference_data = {
            "items": [
                {
                    "id": f"horalis_pro_mensal_{salao_id}",
                    "title": "Acesso Horalis Pro (30 dias)",
                    "description": "Acesso completo à plataforma Horalis por 30 dias.",
                    "quantity": 1,
                    "currency_id": "BRL",
                    "unit_price": 19.90 # Seu novo preço
                }
            ],
            "payer": { "email": payload.email, },
            "back_urls": {
                "success": back_url_success,
                "failure": "https://horalis.app/login?cadastro=falha",
                "pending": "https://horalis.app/login?cadastro=pendente"
            },
            "auto_return": "approved",
            "notification_url": notification_url,
            "external_reference": salao_id, # Chave do Webhook
        }
        
        preference_result = mp_preference_client.create(preference_data)

        if preference_result["status"] not in [200, 201]:
             raise Exception(f"Erro MercadoPago: {preference_result.get('response')}")
            
        checkout_url = preference_result["response"].get("init_point")
        if not checkout_url:
             raise Exception("MP retornou 200/201 mas 'init_point' está faltando.")
             
        logging.info(f"Link de checkout gerado para {salao_id}. Redirecionando usuário...")
        return {"checkout_url": checkout_url}

    except Exception as e:
        # Rollback Completo
        logging.error(f"Erro ao gerar link de pagamento: {e}. Fazendo rollback total...")
        try:
            admin_auth.delete_user(uid)
        except Exception as auth_err:
            logging.error(f"Falha no rollback do Auth: {auth_err}")
            
        try:
            db.collection("cabeleireiros").document(salao_id).delete()
        except Exception as db_err:
            logging.error(f"Falha no rollback do Firestore: {db_err}")
            
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao gerar link de pagamento: {e}")
    
    # --- <<< ADICIONADO: ENDPOINT PARA CRIAR ASSINATURA >>> ---
# --- <<< ENDPOINT PARA CRIAR ASSINATURA (CORREÇÃO RADICAL) >>> ---
# Esta rota está protegida (usa o 'router' principal)
@router.post("/pagamentos/criar-assinatura", status_code=status.HTTP_201_CREATED)
async def create_subscription_checkout(
    current_user: dict = Depends(get_current_user)
):
    """
    Cria um link de checkout (Preferência) para um PAGAMENTO ÚNICO
    que garante 30 dias de acesso.
    """
    if not mp_preference_client:
        raise HTTPException(status_code=503, detail="Serviço de pagamento indisponível.")

    user_uid = current_user.get("uid")
    user_email = current_user.get("email")
    
    try:
        query = db.collection('cabeleireiros').where(filter=firestore.FieldFilter('ownerUID', '==', user_uid)).limit(1)
        client_doc_list = list(query.stream())
        if not client_doc_list:
            raise HTTPException(status_code=404, detail="Nenhum salão encontrado.")
        salao_id = client_doc_list[0].id
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao associar pagamento.")

    back_url_success = f"https://horalis.app/painel/{salao_id}/assinatura?status=success"
    notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"

    # --- Dados da PREFERÊNCIA de Pagamento Único ---
    preference_data = {
        "items": [
            {
                "id": f"horalis_pro_mensal_{salao_id}", # ID interno do item
                "title": "Acesso Horalis Pro (30 dias)",
                "description": "Acesso completo à plataforma Horalis por 30 dias.",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": 19.99 # <<< SEU PREÇO
            }
        ],
        "payer": {
            "email": user_email,
        },
        "back_urls": {
            "success": back_url_success,
            "failure": f"https://horalis.app/painel/{salao_id}/assinatura?status=failure",
            "pending": f"https://horalis.app/painel/{salao_id}/assinatura?status=pending"
        },
        "auto_return": "approved", # Retorna automaticamente se aprovado
        "notification_url": notification_url,
        "external_reference": salao_id, # Vincula ao ID do salão
    }
    # --- FIM DOS DADOS ---

    try:
        logging.info(f"Enviando dados de Preferência (Pagamento Único) para MP para {user_email}...")
        
        # Chama a criação da preferência
        preference_result = mp_preference_client.create(preference_data)
        
        logging.info(f"Resposta do MP: {preference_result}")

        if preference_result["status"] not in [200, 201]:
            logging.error(f"Erro ao criar link de pagamento MP: {preference_result.get('response')}")
            raise HTTPException(status_code=500, detail="Erro ao gerar link de pagamento.")
            
        checkout_url = preference_result["response"].get("init_point")
        
        if not checkout_url:
             logging.error(f"MP retornou 200/201 mas 'init_point' está faltando.")
             raise HTTPException(status_code=500, detail="Erro ao obter URL de checkout.")
             
        logging.info(f"Link de checkout (Pagamento Único) gerado para {user_email}.")
        return {"checkout_url": checkout_url}

    except Exception as e:
        logging.exception(f"Erro crítico ao criar pagamento MP para {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pagamento.")
# --- <<< FIM DO ENDPOINT DE PAGAMENTO >>> ---


# --- <<< ALTERADO: ENDPOINT DE WEBHOOK (agora ouve 'payment') >>> ---
@webhook_router.post("/mercado-pago")
async def webhook_mercado_pago(request: Request):
    body = await request.json()
    logging.info(f"Webhook Mercado Pago recebido: Tipo: {body.get('type')}, Ação: {body.get('action')}")
    
    if not mp_payment_client or not body:
        logging.warning("Webhook ignorado: SDK não pronto ou corpo vazio.")
        return {"status": "ignorado"}

    # Ação 'payment.updated' ou 'payment.created'
    if body.get("type") == "payment":
        payment_id = body.get("data", {}).get("id")
        if not payment_id:
            logging.warning("Webhook de pagamento recebido sem ID.")
            return {"status": "id não encontrado"}
            
        try:
            # Busca os dados do PAGAMENTO no Mercado Pago
            payment_data = mp_payment_client.get(payment_id)
            if payment_data["status"] != 200:
                logging.error(f"Erro ao buscar dados do webhook MP (Payment): {payment_data}")
                return {"status": "erro ao buscar dados"}
            
            data = payment_data["response"]
            salao_id = data.get("external_reference") # Nosso ID do salão
            status = data.get("status") # Ex: 'approved', 'pending', 'rejected'
            
            if not salao_id:
                logging.warning(f"Webhook MP (Payment) recebido sem external_reference: {payment_id}")
                return {"status": "referência externa faltando"}

            salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
            
            # ATUALIZA O FIRESTORE APENAS SE O PAGAMENTO FOI APROVADO
            if status == 'approved':
                # Calcula a nova data de vencimento (30 dias a partir de agora)
                new_paid_until = datetime.now(pytz.utc) + timedelta(days=30)
                
                logging.info(f"Pagamento APROVADO. Atualizando assinatura para 'active' para o salão: {salao_id} até {new_paid_until.isoformat()}")
                salao_doc_ref.update({
                    "subscriptionStatus": "active",
                    "paidUntil": new_paid_until, # <<< SALVA A DATA DE VENCIMENTO
                    "mercadopagoLastPaymentId": payment_id,
                    "subscriptionLastUpdated": firestore.SERVER_TIMESTAMP
                })
            elif status in ['rejected', 'cancelled', 'refunded']:
                logging.info(f"Pagamento falhou ou foi revertido. Status: '{status}' para o salão: {salao_id}")
                # Aqui você pode decidir reverter o status se necessário
                salao_doc_ref.update({
                    "subscriptionStatus": status, # Salva o status da falha
                    "subscriptionLastUpdated": firestore.SERVER_TIMESTAMP
                })
            else:
                 logging.info(f"Webhook de pagamento recebido com status: '{status}'. Aguardando aprovação.")

            return {"status": "recebido"}
            
        except Exception as e:
            logging.exception(f"Erro ao processar webhook do MP (Payment): {e}")
            return {"status": "erro interno"}

    return {"status": "tipo de evento ignorado"}
# --- <<< FIM DO WEBHOOK >>> ---

# --- ENDPOINTS OAUTH (Sem alterações) ---
@router.get("/google/auth/start", response_model=dict[str, str])
async def google_auth_start(current_user: dict[str, Any] = Depends(get_current_user)):
    # ... (código sem alteração) ...
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
        state=current_user.get("uid") 
    )
    logging.info(f"Enviando URL de autorização do Google para o usuário {current_user.get('email')}...")
    return {"authorization_url": authorization_url}

@router.get("/user/salao-id", response_model=dict[str, str])
async def get_salao_id_for_user(current_user: dict[str, Any] = Depends(get_current_user)):
    # ... (código sem alteração) ...
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


@router.patch("/clientes/{salao_id}/google-sync", status_code=status.HTTP_200_OK)
async def disconnect_google_sync(
    salao_id: str,
    current_user: dict = Depends(get_current_user) # Protege a rota
):
    """
    Desativa a sincronização com o Google Calendar para um salão específico.
    Define 'google_sync_enabled' como False e remove o 'google_refresh_token'.
    """
    user_uid = current_user.get("uid") # Pega o UID do usuário logado
    logging.info(f"Admin (UID: {user_uid}) solicitou desconexão do Google Sync para salão: {salao_id}")

    try:
        salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
        salao_doc = salao_doc_ref.get(['ownerUID']) # Busca apenas o ownerUID para verificação

        if not salao_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado.")

        # --- Verificação de Propriedade (Opcional, mas MUITO recomendado) ---
        # Garante que o usuário logado só possa desconectar o *seu* salão
        salon_owner_uid = salao_doc.get('ownerUID')
        if salon_owner_uid != user_uid:
             logging.warning(f"Tentativa não autorizada de desconectar sync. User: {user_uid}, Salão: {salao_id}")
             raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ação não autorizada.")
        # --- Fim da Verificação ---

        # Atualiza o documento no Firestore
        salao_doc_ref.update({
            "google_sync_enabled": False,
            "google_refresh_token": firestore.DELETE_FIELD # Remove o campo do token
        })

        logging.info(f"Sincronização Google desativada com sucesso para o salão: {salao_id}")
        return {"message": "Sincronização com Google Calendar desativada com sucesso."}

    except HTTPException as httpe:
        raise httpe # Repassa erros HTTP (404, 403)
    except Exception as e:
        logging.exception(f"Erro ao desativar Google Sync para salão {salao_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao desconectar sincronização.")
    
# --- Endpoints CRUD de Clientes (Sem alterações) ---
@router.get("/clientes", response_model=List[ClientDetail])
async def list_clients(current_user: dict = Depends(get_current_user)):
    # ... (código sem alteração) ...
    logging.info(f"Admin {current_user.get('email')} solicitou lista de clientes.")
    clients = get_all_clients_from_db()
    if clients is None: raise HTTPException(status_code=500, detail="Erro ao buscar clientes.")
    return clients

@router.get("/clientes/{client_id}", response_model=ClientDetail)
async def get_client_details(client_id: str, current_user: dict = Depends(get_current_user)):
    # ... (código sem alteração) ...
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
    """Cria um novo cliente (cabeleireiro), salvando o UID do dono
       E iniciando o período de Teste Grátis (Trial)."""
    admin_email = current_user.get("email")
    user_uid = current_user.get("uid")
    logging.info(f"Admin {admin_email} (UID: {user_uid}) criando: {client_data.nome_salao}")
    client_id = client_data.numero_whatsapp
    
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if client_ref.get().exists: 
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cliente {client_id} (WhatsApp) já existe.")
        
        data_to_save = client_data.dict() 
        
        # --- Vincula o salão ao usuário de autenticação ---
        data_to_save['ownerUID'] = user_uid
        
        # --- <<< ADICIONADO: Lógica de Teste Grátis (Trial) >>> ---
        # Define o status inicial da assinatura
        data_to_save['subscriptionStatus'] = 'trialing' 
        # Define a data de criação (para referência)
        data_to_save['createdAt'] = firestore.SERVER_TIMESTAMP 
        # Define quando o teste termina (7 dias a partir de agora)
        # (O servidor da Render roda em UTC, o que é ótimo para consistência)
        trial_end_date = datetime.now() + timedelta(days=7)
        data_to_save['trialEndsAt'] = trial_end_date # O SDK do Firebase converte para Timestamp
        # --- <<< FIM DA ADIÇÃO >>> ---
        
        client_ref.set(data_to_save)
        logging.info(f"Cliente '{data_to_save['nome_salao']}' (Dono: {user_uid}) criado com ID: {client_id} em modo 'trialing'.")
        
        # Retorna o ClientDetail completo (o modelo não precisa ter os campos de assinatura)
        return ClientDetail(id=client_id, servicos=[], **data_to_save)
        
    except HTTPException as httpe: raise httpe
    except Exception as e:
        logging.exception(f"Erro ao criar cliente:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao criar cliente.")

@router.put("/clientes/{client_id}", response_model=ClientDetail)
async def update_client(client_id: str, client_update_data: ClientDetail, current_user: dict = Depends(get_current_user)):
    # ... (código sem alteração) ...
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
            transaction.update(client_ref, client_info_to_save)
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


# --- Agendamento Manual (Sem alteração) ---
@router.post("/calendario/agendar", status_code=status.HTTP_201_CREATED)
async def create_manual_appointment(
    manual_data: ManualAppointmentData,
    current_user: dict[str, Any] = Depends(get_current_user)
):
    # ... (código sem alteração, exceto pela adição do logging sobre o e-mail) ...
    user_email = current_user.get("email")
    salao_id = manual_data.salao_id 
    customer_email_provided = manual_data.customer_email # <<< Pega o e-mail do cliente (pode ser None)
    logging.info(f"Admin {user_email} criando agendamento manual para {salao_id}")
    
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "Seu Salão")

        start_time_dt = datetime.fromisoformat(manual_data.start_time)
        end_time_dt = start_time_dt + timedelta(minutes=manual_data.duration_minutes)

        agendamento_data = {
            "salaoId": salao_id,
            "salonName": salon_name,
            "serviceName": manual_data.service_name,
            "durationMinutes": manual_data.duration_minutes,
            "startTime": start_time_dt,
            "endTime": end_time_dt,
            "customerName": manual_data.customer_name,
            "customerPhone": manual_data.customer_phone or None, # Salva None se vazio
            "customerEmail": customer_email_provided, # <<< ADICIONADO: Salva o e-mail (ou None)
            "status": "confirmado",
            "createdBy": user_email,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "reminderSent": False,
            "serviceId": manual_data.service_id,       
            "servicePrice": manual_data.service_price,
        }

        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento manual criado no Firestore com ID: {agendamento_ref.id}")

        # Sincronização Google (sem alteração)
        google_event_id = None # Inicializa
        if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
            logging.info("Sincronização Google Ativa para agendamento manual.") # Log adicionado

            # <<< CORREÇÃO: Montar google_event_data corretamente >>>
            google_event_data = {
                "summary": f"{manual_data.service_name} - {manual_data.customer_name}",
                "description": (
                    f"Agendamento via Horalis (Manual).\n"
                    f"Cliente: {manual_data.customer_name}\n"
                    f"Telefone: {manual_data.customer_phone or 'N/A'}\n"
                    # Não incluímos e-mail na descrição por privacidade, a menos que você queira
                    f"Serviço: {manual_data.service_name}"
                ),
                "start_time_iso": start_time_dt.isoformat(), # Usa o datetime já calculado
                "end_time_iso": end_time_dt.isoformat(),     # Usa o datetime já calculado
            }
            # <<< FIM DA CORREÇÃO >>>

            try:
                google_event_id = calendar_service.create_google_event_with_oauth(
                    refresh_token=salon_data.get("google_refresh_token"),
                    event_data=google_event_data
                )
                if google_event_id:
                    agendamento_ref.update({"googleEventId": google_event_id})
                    logging.info(f"Agendamento manual salvo no Google Calendar. ID: {google_event_id}")
                else:
                    # Se create_google_event_with_oauth retornar None (falha interna lá)
                    logging.warning("Falha ao salvar agendamento manual no Google Calendar (função retornou None).")
            except Exception as e:
                # Pega qualquer outra exceção durante a chamada ou update
                logging.exception(f"Erro inesperado ao sync Google (manual): {e}") # <<< Alterado para exception para mais detalhes
        else:
            logging.info("Sincronização Google desativada. Pulando etapa para agendamento manual.")


        return {"message": "Agendamento manual criado com sucesso!", "id": agendamento_ref.id}

    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento manual:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao criar agendamento manual.")


# --- Endpoint de Leitura do Calendário (Sem alterações) ---
@router.get("/calendario/{salao_id}/eventos", response_model=List[CalendarEvent])
async def get_calendar_events(
    salao_id: str, 
    start: str, 
    end: str,
    current_user: dict = Depends(get_current_user)
):
    # ... (código sem alteração) ...
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
            startTime = data.get('startTime')
            endTime = data.get('endTime')
            if not startTime or not endTime: continue
                
            evento_formatado = CalendarEvent(
                id=doc.id,
                title=f"{data.get('serviceName', 'Serviço')} - {data.get('customerName', 'Cliente')}",
                start=startTime, 
                end=endTime,
                extendedProps={ 
                    "customerName": data.get('customerName'),
                    "customerPhone": data.get('customerPhone'),
                    "customerEmail": data.get('customerEmail'), # <<< ADICIONADO (para debug)
                    "serviceName": data.get('serviceName'),
                    "durationMinutes": data.get('durationMinutes'),
                    "googleEventId": data.get("googleEventId") 
                }
            )
            eventos.append(evento_formatado)
        logging.info(f"Retornando {len(eventos)} eventos para o FullCalendar.")
        return eventos
    except Exception as e:
        logging.exception(f"Erro ao buscar eventos do calendário para {salao_id}:")
        raise HTTPException(status_code=500, detail="Erro interno ao buscar eventos.")


# --- <<< MODIFICADO: Endpoint de Cancelar Agendamento >>> ---
@router.delete("/calendario/{salao_id}/agendamentos/{agendamento_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    salao_id: str, 
    agendamento_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Cancela um agendamento (Deleta do Firestore, Google Calendar E NOTIFICA O CLIENTE)
    """
    logging.info(f"Admin {current_user.get('email')} cancelando agendamento: {agendamento_id}")
    
    try:
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
        agendamento_doc = agendamento_ref.get()

        if not agendamento_doc.exists:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
        # <<< ADICIONADO: Coleta de dados para o e-mail >>>
        agendamento_data = agendamento_doc.to_dict()
        google_event_id = agendamento_data.get("googleEventId")
        customer_email = agendamento_data.get("customerEmail")
        customer_name = agendamento_data.get("customerName")
        service_name = agendamento_data.get("serviceName")
        start_time_dt = agendamento_data.get("startTime") # Pega como datetime
        
        # Pega o nome do salão (necessário para o e-mail)
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "seu salão")

        # 1. Sincronização: Deletar do Google Calendar (Sem alteração)
        if google_event_id:
            refresh_token = salon_data.get("google_refresh_token")
            if refresh_token:
                logging.info(f"Tentando deletar evento do Google Calendar: {google_event_id}")
                calendar_service.delete_google_event(refresh_token, google_event_id)
            else:
                logging.warning(f"Não foi possível deletar {google_event_id} do Google. Refresh token não encontrado.")
        
        # 2. Deletar do Firestore (Sem alteração)
        agendamento_ref.delete()
        logging.info(f"Agendamento {agendamento_id} deletado do Firestore.")
        
        # --- <<< ADICIONADO: Notificação por E-mail (Cliente) >>> ---
        if customer_email and customer_name and service_name and start_time_dt and salon_name:
            try:
                logging.info(f"Enviando e-mail de cancelamento para {customer_email}...")
                email_service.send_cancellation_email_to_customer(
                    customer_email=customer_email,
                    customer_name=customer_name,
                    service_name=service_name,
                    start_time_iso=start_time_dt.isoformat(), # Converte datetime para ISO string
                    salon_name=salon_name
                )
            except Exception as e:
                # Não quebra a operação se o e-mail falhar, apenas loga
                logging.error(f"Falha ao enviar e-mail de CANCELAMENTO (Cliente) para {customer_email}: {e}")
        else:
            logging.warning(f"Pulando e-mail de cancelamento (dados incompletos) para agendamento {agendamento_id}")
        # --- <<< FIM DA ADIÇÃO >>> ---
        
        return # Retorna 204 No Content

    except Exception as e:
        logging.exception(f"Erro ao cancelar agendamento {agendamento_id}:")
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")


# --- <<< MODIFICADO: Endpoint de Reagendar Agendamento >>> ---
@router.patch("/calendario/{salao_id}/agendamentos/{agendamento_id}")
async def reschedule_appointment(
    salao_id: str, 
    agendamento_id: str,
    body: ReagendamentoBody,
    current_user: dict = Depends(get_current_user)
):
    """
    Reagenda um agendamento (Verifica conflitos, Atualiza Firestore, 
    Atualiza Google Calendar E NOTIFICA O CLIENTE)
    """
    logging.info(f"Admin {current_user.get('email')} tentando reagendar {agendamento_id} para {body.new_start_time}")
    
    try:
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
        agendamento_doc = agendamento_ref.get()

        if not agendamento_doc.exists:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
        # <<< ADICIONADO: Coleta de dados para o e-mail >>>
        agendamento_data = agendamento_doc.to_dict()
        google_event_id = agendamento_data.get("googleEventId")
        duration = agendamento_data.get("durationMinutes")
        customer_email = agendamento_data.get("customerEmail")
        customer_name = agendamento_data.get("customerName")
        service_name = agendamento_data.get("serviceName")
        old_start_time_dt = agendamento_data.get("startTime") # Horário antigo
        
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "seu salão")

        if not duration or not salon_data or not old_start_time_dt:
             raise HTTPException(status_code=500, detail="Dados do agendamento ou salão estão incompletos.")

        # Calcular novos horários (Sem alteração)
        new_start_dt = datetime.fromisoformat(body.new_start_time)
        local_tz = pytz.timezone(calendar_service.LOCAL_TIMEZONE)
        if new_start_dt.tzinfo is None:
             new_start_dt = local_tz.localize(new_start_dt)
        else:
             new_start_dt = new_start_dt.astimezone(local_tz)
        new_end_dt = new_start_dt + timedelta(minutes=duration)
        
        # Verificação de Conflito (Sem alteração)
        is_free = calendar_service.is_slot_available(
            salao_id=salao_id, salon_data=salon_data,
            new_start_dt=new_start_dt, duration_minutes=duration,
            ignore_firestore_id=agendamento_id, ignore_google_event_id=google_event_id
        )
        if not is_free:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Horário indisponível. Conflito com outro agendamento ou evento pessoal."
            )
        
        # 1. Sincronização: Atualizar no Google Calendar (Sem alteração)
        if google_event_id:
            refresh_token = salon_data.get("google_refresh_token")
            if refresh_token:
                logging.info(f"Atualizando evento do Google Calendar: {google_event_id}")
                calendar_service.update_google_event(
                    refresh_token, google_event_id, 
                    new_start_dt.isoformat(), new_end_dt.isoformat()
                )
            else:
                logging.warning(f"Não foi possível atualizar {google_event_id} no Google. Refresh token não encontrado.")

        # 2. Atualizar no Firestore (Sem alteração)
        agendamento_ref.update({
            "startTime": new_start_dt,
            "endTime": new_end_dt
        })
        logging.info(f"Agendamento {agendamento_id} atualizado no Firestore.")

        # --- <<< ADICIONADO: Notificação por E-mail (Cliente) >>> ---
        if customer_email and customer_name and service_name and salon_name:
            try:
                logging.info(f"Enviando e-mail de reagendamento para {customer_email}...")
                email_service.send_reschedule_email_to_customer(
                    customer_email=customer_email,
                    customer_name=customer_name,
                    service_name=service_name,
                    salon_name=salon_name,
                    old_start_time_iso=old_start_time_dt.isoformat(), # Envia o horário antigo
                    new_start_time_iso=new_start_dt.isoformat()     # Envia o horário novo
                )
            except Exception as e:
                logging.error(f"Falha ao enviar e-mail de REAGENDAMENTO (Cliente) para {customer_email}: {e}")
        else:
            logging.warning(f"Pulando e-mail de reagendamento (dados incompletos) para agendamento {agendamento_id}")
        # --- <<< FIM DA ADIÇÃO >>> ---

        return {"message": "Agendamento reagendado com sucesso."}

    except HTTPException as httpe:
        raise httpe 
    except Exception as e:
        logging.exception(f"Erro ao reagendar agendamento {agendamento_id}:")
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")


# --- ROTEADOR PÚBLICO PARA O CALLBACK (Sem alterações) ---
@callback_router.get("/google/auth/callback")
async def google_auth_callback_handler(
    state: str, 
    code: str, 
    scope: str
):
    # ... (código sem alteração) ...
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
        frontend_redirect_url = f"https://horalis.app/painel/{salao_doc_ref.id}/configuracoes?sync=success"
        return RedirectResponse(frontend_redirect_url)
    except Exception as e:
        logging.exception(f"Erro CRÍTICO durante o callback do Google OAuth: {e}")
        frontend_error_url = f"https://horalis.app/painel/{state}/configuracoes?sync=error"
        return RedirectResponse(frontend_error_url)