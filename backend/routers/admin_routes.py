# backend/routers/admin_routes.py
from dotenv import load_dotenv
load_dotenv() 
import logging
import os
import re
import pytz # <<< ADICIONADO (para o fuso do reagendamento)
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request,BackgroundTasks
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
class EmailPromocionalBody(BaseModel):
    cliente_id: str
    salao_id: str
    subject: str = Field(..., min_length=5)
    message: str = Field(..., min_length=10)
class ClienteListItem(BaseModel):
    id: str
    nome: str
    email: str
    whatsapp: str
    data_cadastro: Optional[datetime] = None
    ultima_visita: Optional[datetime] = None
    
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
    
class PayerIdentification(BaseModel):
 type: str
 number: str
 
class NotaManualBody(BaseModel):
    salao_id: str
    cliente_id: str
    nota_texto: str = Field(..., min_length=1)

class TimelineItem(BaseModel):
    id: str
    tipo: str  # Ex: 'Agendamento', 'NotaManual', 'Promocional'
    data_evento: datetime # Data/Hora para ordenação
    dados: dict[str, Any] # O conteúdo completo do registro/agendamento
    
class PayerData(BaseModel):
 email: EmailStr
 # AQUI ESTÁ A CORREÇÃO:
 # Trocamos 'Optional[dict]' por 'Optional[PayerIdentification]'.
 # O Pydantic agora vai converter o dict em um objeto.
 identification: Optional[PayerIdentification] = None 
# --- <<< FIM DA ALTERAÇÃO 1 >>> ---

class UserPaidSignupPayload(BaseModel):
 # Dados do Usuário
 email: EmailStr
 password: str = Field(..., min_length=6)
 nome_salao: str = Field(..., min_length=2)
 numero_whatsapp: str
 
 # Dados do Pagamento (do Brick)
 token: Optional[str] = None
 issuer_id: Optional[str] = None
 payment_method_id: str
 transaction_amount: float
 installments: Optional[int] = None
 payer: PayerData

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

# --- <<< NOVO: ENDPOINT PÚBLICO DE CADASTRO PAGO DIRETO >>> ---
@auth_router.post("/criar-conta-paga", status_code=status.HTTP_201_CREATED)
async def criar_conta_paga_com_pagamento(payload: UserPaidSignupPayload):
    """
    Endpoint PÚBLICO para criar conta e processar pagamento transparente.
    Lida com fluxos de Cartão (transparente) e PIX (retorna QR Code).
    """
    
    if not mp_payment_client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Serviço de pagamento indisponível.")

    salao_id = payload.numero_whatsapp
    uid = None # Inicializa para o bloco try/except final

    # --- Passo 1: Validação de Conflito (e-mail e WhatsApp) ---
    try:
        admin_auth.get_user_by_email(payload.email)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Este e-mail já está cadastrado. Tente fazer login."
        )
    except admin_auth.UserNotFoundError:
        pass 
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao verificar e-mail: {e}")

    try:
        salao_doc = db.collection('cabeleireiros').document(salao_id).get()
        if salao_doc.exists:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este número de WhatsApp já está cadastrado."
            )
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao verificar dados: {e}")

    # --- Passo 2: Criar Usuário no Firebase Auth ---
    try:
        logging.info(f"Criando usuário no Auth para {payload.email}...")
        new_user = admin_auth.create_user(
            email=payload.email,
            password=payload.password,
            display_name=payload.nome_salao
        )
        uid = new_user.uid
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar usuário: {e}")

    # --- Passo 3: Criar Salão no Firestore (como "pending") ---
    salao_doc_ref = db.collection("cabeleireiros").document(salao_id)
    try:
        logging.info(f"Criando documento do salão (pending) com ID: {salao_id} para UID: {uid}...")
        now = datetime.now(pytz.utc)
        salao_data = {
            "nome_salao": payload.nome_salao,
            "numero_whatsapp": payload.numero_whatsapp,
            "email": payload.email,
            "ownerUID": uid,
            "createdAt": now,
            "subscriptionStatus": "pending", 
            "paidUntil": None,
            "subscriptionLastUpdated": now,
            "trialEndsAt": None,
            "mercadopago_customer_id": None,
            "google_sync_enabled": False,
        }
        salao_doc_ref.set(salao_data)
        
    except Exception as e:
        logging.error(f"Erro ao criar salão no Firestore: {e}. Fazendo rollback do Auth...")
        admin_auth.delete_user(uid)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao salvar dados do salão: {e}")

    # --- Passo 4: Processar o Pagamento (Lógica Dividida) ---
    try:
        logging.info(f"Processando pagamento para {salao_id} via {payload.payment_method_id}...")
        notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"
        
        # Dados de identificação do pagador (corretamente como objeto PayerIdentification)
        payer_identification_data = {
            "type": payload.payer.identification.type,
            "number": payload.payer.identification.number
        } if payload.payer.identification else None

        # --- CASO 1: PAGAMENTO COM PIX ---
        if payload.payment_method_id == 'pix':
            logging.info(f"Criando pagamento PIX para {salao_id}...")
            
            payment_data = {
                "transaction_amount": payload.transaction_amount,
                "description": "Assinatura Horalis Pro (PIX)",
                "payment_method_id": "pix",
                "payer": {
                    "email": payload.payer.email,
                    "identification": payer_identification_data
                },
                "external_reference": salao_id, 
                "notification_url": notification_url, 
            }
            
            payment_response = mp_payment_client.create(payment_data)
            
            if payment_response["status"] not in [200, 201]:
                raise Exception(f"Erro MercadoPago (PIX): {payment_response.get('response').get('message', 'Erro desconhecido ao processar PIX')}")

            payment_result = payment_response["response"]
            qr_code_data = payment_result.get("point_of_interaction", {}).get("transaction_data", {})
            
            qr_code_b64 = qr_code_data.get("qr_code_base64")
            qr_code_str = qr_code_data.get("qr_code")

            if not qr_code_b64 or not qr_code_str:
                logging.error("Resposta do PIX não continha dados do QR Code.")
                raise Exception("Falha ao gerar QR Code do PIX.")

            # Salva o ID do pagamento pendente para o webhook
            salao_doc_ref.update({
                "mercadopagoLastPaymentId": payment_result.get("id")
            })
            
            # Retorna os dados para o frontend exibir o QR Code
            return {
                "status": "pending_pix",
                "message": "PIX gerado. Aguardando pagamento.",
                "payment_data": {
                    "qr_code": qr_code_str,
                    "qr_code_base64": qr_code_b64,
                    "payment_id": payment_result.get("id")
                }
            }
        
        # --- CASO 2: PAGAMENTO COM CARTÃO (ou outros métodos) ---
        else: # Assumimos que é um método com token (Cartão)
            logging.info(f"Criando pagamento com Cartão ({payload.payment_method_id}) para {salao_id}...")
            
            payment_data = {
                "transaction_amount": payload.transaction_amount,
                "token": payload.token,
                "description": "Assinatura Horalis Pro (Cartão)",
                "installments": payload.installments,
                "payment_method_id": payload.payment_method_id,
                "issuer_id": payload.issuer_id,
                "payer": {
                    "email": payload.payer.email,
                    "identification": payer_identification_data
                },
                "external_reference": salao_id, 
                "notification_url": notification_url, 
            }

            payment_response = mp_payment_client.create(payment_data)

            if payment_response["status"] not in [200, 201]:
                error_msg = payment_response.get('response', {}).get('message', 'Erro desconhecido ao processar o cartão.')
                raise Exception(f"Erro MercadoPago (Cartão): {error_msg}")

            payment_status = payment_response["response"].get("status")
            
            # --- Passo 5: Tratar Resposta do Pagamento Cartão ---
            if payment_status == "approved":
                logging.info(f"Pagamento APROVADO instantaneamente para {salao_id}.")
                new_paid_until = datetime.now(pytz.utc) + timedelta(days=30)
                salao_doc_ref.update({
                    "subscriptionStatus": "active",
                    "paidUntil": new_paid_until,
                    "subscriptionLastUpdated": firestore.SERVER_TIMESTAMP,
                    "mercadopagoLastPaymentId": payment_response["response"].get("id")
                })
                return {"status": "approved", "message": "Pagamento aprovado e conta criada!"}
            
            elif payment_status in ["in_process", "pending"]:
                logging.info(f"Pagamento PENDENTE para {salao_id}.")
                return {"status": "pending", "message": "Pagamento em processamento. Sua conta será ativada em breve."}
            
            else:
                logging.warning(f"Pagamento REJEITADO para {salao_id}.")
                error_detail = payment_response["response"].get("status_detail", "Pagamento rejeitado pelo MercadoPago.")
                # Desfaz tudo (Rollback)
                admin_auth.delete_user(uid)
                salao_doc_ref.delete()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    except Exception as e:
        # Rollback Completo se algo falhar
        error_message = str(e)
        logging.error(f"Erro ao processar pagamento: {error_message}. Fazendo rollback total...")
        
        # Tenta o rollback do Firebase Auth
        if uid:
            try:
                admin_auth.delete_user(uid)
            except Exception as auth_err:
                logging.error(f"Falha no rollback do Auth: {auth_err}")
                
        # Tenta o rollback do Firestore
        try:
            db.collection("cabeleireiros").document(salao_id).delete()
        except Exception as db_err:
            logging.error(f"Falha no rollback do Firestore: {db_err}")
            
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao processar pagamento: {error_message}")
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
    """Cria um agendamento manual, salva no Firestore, sincroniza Google, E AGORA ENVIA E-MAIL."""
    
    user_email = current_user.get("email") # E-mail do Admin
    salao_id = manual_data.salao_id 
    customer_email_provided = manual_data.customer_email 
    logging.info(f"Admin {user_email} criando agendamento manual para {salao_id}")
    
    try:
        # 1. Validação e Coleta de Dados
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "Seu Salão")
        salon_email_destino = salon_data.get('calendar_id') # E-mail do salão para notificação

        # Cálculos de tempo
        start_time_dt = datetime.fromisoformat(manual_data.start_time)
        end_time_dt = start_time_dt + timedelta(minutes=manual_data.duration_minutes)

        if not salon_email_destino:
             logging.warning("E-mail de destino do salão não encontrado. Pulando notificação.")
        
        # 2. Dados do Agendamento (para o Firestore)
        agendamento_data = {
            "salaoId": salao_id,
            "salonName": salon_name,
            "serviceName": manual_data.service_name,
            "durationMinutes": manual_data.duration_minutes,
            "startTime": start_time_dt,
            "endTime": end_time_dt,
            "customerName": manual_data.customer_name,
            "customerPhone": manual_data.customer_phone or None,
            "customerEmail": customer_email_provided,
            "status": "confirmado",
            "createdBy": user_email,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "reminderSent": False,
            "serviceId": manual_data.service_id,
            "servicePrice": manual_data.service_price,
            "clienteId": manual_data.cliente_id or None # Novo campo CRM
        }

        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento manual criado no Firestore com ID: {agendamento_ref.id}")

        # 3. Disparo do E-mail (CORREÇÃO APLICADA AQUI)
        if customer_email_provided and salon_email_destino:
            try:
                # E-mail para o SALÃO
                email_service.send_confirmation_email_to_salon(
                    salon_email=salon_email_destino, salon_name=salon_name, 
                    customer_name=manual_data.customer_name, client_phone=manual_data.customer_phone, 
                    service_name=manual_data.service_name, start_time_iso=manual_data.start_time
                )
                # E-mail para o CLIENTE
                email_service.send_confirmation_email_to_customer(
                    customer_email=customer_email_provided, customer_name=manual_data.customer_name,
                    service_name=manual_data.service_name, start_time_iso=manual_data.start_time,
                    salon_name=salon_name
                )
                logging.info(f"E-mails de confirmação disparados com sucesso para o agendamento manual.")
            except Exception as e:
                logging.error(f"Erro CRÍTICO ao disparar e-mail no agendamento manual: {e}")
        else:
             logging.warning("E-mails de confirmação pulados. Cliente/Salão e-mail ausente.")


        # 4. Sincronização Google (Lógica idêntica)
        google_event_id = None
        if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
            logging.info("Sincronização Google Ativa para agendamento manual.")

            google_event_data = {
                "summary": f"{manual_data.service_name} - {manual_data.customer_name}",
                "description": (
                    f"Agendamento via Horalis (Manual).\n"
                    f"Cliente: {manual_data.customer_name}\n"
                    f"Telefone: {manual_data.customer_phone or 'N/A'}\n"
                    f"Serviço: {manual_data.service_name}"
                ),
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
                logging.exception(f"Erro inesperado ao sync Google (manual): {e}")
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
    
@auth_router.get("/check-payment-status/{payment_id}", response_model=dict[str, str])
async def check_payment_status(payment_id: str):
    """
    Endpoint PÚBLICO usado pelo frontend (polling) para verificar o status
    da assinatura no Firestore, usando o ID do pagamento gerado (PIX/Boleto).
    """
    logging.info(f"Polling recebido para verificar Payment ID: {payment_id}")
    
    try:
        # 1. Buscar o salão que tem este mercadopagoLastPaymentId
        query = db.collection('cabeleireiros').where(
            filter=firestore.FieldFilter('mercadopagoLastPaymentId', '==', payment_id)
        ).limit(1)
        client_doc_list = list(query.stream())
        
        if not client_doc_list:
            # Se não encontrou, talvez o pagamento ainda não tenha sido registrado pelo webhook (ou o ID está errado)
            return {"status": "pending", "message": "Aguardando registro inicial ou pagamento."}
        
        salao_doc = client_doc_list[0]
        current_status = salao_doc.get('subscriptionStatus')

        if current_status == 'active':
            # O webhook já passou e ativou a conta!
            return {"status": "approved", "message": "Pagamento confirmado. Login liberado."}
        elif current_status in ['pending', 'trialing']:
            # Ainda pendente (PIX ainda não foi pago)
            return {"status": "pending", "message": "Aguardando confirmação do PIX."}
        else:
            # Rejeitado, cancelado, etc.
            return {"status": current_status, "message": "Pagamento não aprovado. Tente novamente."}

    except Exception as e:
        logging.exception(f"Erro no Polling de Pagamento para {payment_id}: {e}")
        # Retorna 'pending' por segurança, para não interromper o polling
        return {"status": "pending", "message": "Erro de comunicação. Tente o login em instantes."}
# --- <<< FIM DO ENDPOINT DE POLLING >>> ---

@router.get("/clientes/{salao_id}/lista-crm", response_model=List[ClienteListItem])
async def list_crm_clients(
    salao_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Lista todos os clientes CRM (perfis implícitos) associados a um salão específico.
    Acesso restrito a usuários logados e com assinatura ativa.
    """
    user_email = current_user.get("email")
    logging.info(f"Admin {user_email} solicitou lista CRM para salão: {salao_id}")

    try:
        # 1. Referência à subcoleção 'clientes'
        clientes_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes')
        
        # 2. Busca todos os documentos
        # Nota: Você pode querer adicionar 'orderBy' e 'limit' aqui no futuro
        # para performance, mas por enquanto, vamos buscar todos.
        docs = clientes_ref.stream()
        
        clientes_list = []
        for doc in docs:
            data = doc.to_dict()
            
            # 3. Formata os dados para o Pydantic
            # Converte os Timestamps (do Firestore) para datetime
            data_cadastro = data.get('data_cadastro')
            ultima_visita = data.get('ultima_visita')

            clientes_list.append(ClienteListItem(
                id=doc.id,
                nome=data.get('nome', 'N/A'),
                email=data.get('email', 'N/A'),
                whatsapp=data.get('whatsapp', 'N/A'),
                # Converte o Firestore Timestamp (se existir) para string ISO (o frontend React espera string)
                data_cadastro=data_cadastro.isoformat() if data_cadastro else None,
                ultima_visita=ultima_visita.isoformat() if ultima_visita else None,
            ))
        
        logging.info(f"Retornando {len(clientes_list)} perfis CRM para o salão {salao_id}.")
        return clientes_list

    except Exception as e:
        logging.exception(f"Erro ao buscar perfis CRM para o salão {salao_id}: {e}")
        # Retorna erro 404 se o salão não existir (embora o get_current_user já proteja um pouco)
        if "No document to update" in str(e):
             raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado.")
        
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao buscar clientes.")
    
class HistoricoAgendamentoItem(BaseModel):
    id: str
    serviceName: str
    startTime: datetime
    durationMinutes: int
    servicePrice: Optional[float] = None
    status: str
    # Adicione mais campos do agendamento se precisar
    
class ClienteDetailsResponse(BaseModel):
    # Detalhes do Cliente (Perfil CRM)
    cliente: dict[str, Any] # Dicionário com todos os dados do cliente (nome, email, etc.)
    # Agora a lista de histórico é o novo TimelineItem
    historico_agendamentos: List[TimelineItem] # <<< MUDANÇA CRÍTICA: AGORA USA TimelineItem


@router.get("/clientes/{salao_id}/detalhes-crm/{cliente_id}", response_model=ClienteDetailsResponse)
async def get_cliente_details_and_history(
    salao_id: str,
    cliente_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Busca os detalhes do perfil do cliente E A TIMELINE COMPLETA 
    (Agendamentos + Notas Manuais + Registros de E-mail).
    """
    user_email = current_user.get("email")
    logging.info(f"Admin {user_email} buscando detalhes e timeline do cliente: {cliente_id}")

    try:
        timeline_items = []
        
        # 1. Busca os dados do Perfil CRM
        cliente_doc_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes').document(cliente_id)
        cliente_doc = cliente_doc_ref.get()

        if not cliente_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perfil do cliente não encontrado.")
        
        cliente_data = cliente_doc.to_dict()

        # 2. Busca o Histórico de Agendamentos
        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        history_query = agendamentos_ref.where('clienteId', '==', cliente_id)
        
        agendamento_docs = history_query.stream()
        for doc in agendamento_docs:
            data = doc.to_dict()
            if data.get('startTime'):
                timeline_items.append(TimelineItem(
                    id=doc.id,
                    tipo="Agendamento",
                    data_evento=data.get('startTime'), # Firestore Timestamp
                    dados=data 
                ))

        # 3. Busca o Histórico de Registros (Notas, E-mails)
        registros_ref = cliente_doc_ref.collection('registros')
        registro_docs = registros_ref.stream()
        
        for doc in registro_docs:
            data = doc.to_dict()
            if data.get('data_envio'):
                timeline_items.append(TimelineItem(
                    id=doc.id,
                    tipo=data.get("tipo", "Registro"), # 'NotaManual', 'Promocional'
                    data_evento=data.get('data_envio'), # Firestore Timestamp
                    dados=data
                ))

        # 4. Ordena a timeline combinada pela data (mais recente primeiro)
        # O Pydantic serializa os Timestamps/datetime corretamente no retorno.
        timeline_items.sort(key=lambda item: item.data_evento, reverse=True)
        
        logging.info(f"Timeline de {len(timeline_items)} itens encontrada para o cliente {cliente_id}.")

        # 5. Retorna a resposta completa
        return ClienteDetailsResponse(
            cliente=cliente_data,
            historico_agendamentos=timeline_items
        )

    except HTTPException as httpe: 
        raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao buscar detalhes do cliente {cliente_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")


@router.post("/clientes/adicionar-nota", status_code=status.HTTP_201_CREATED, response_model=TimelineItem)
async def adicionar_nota_manual(
    body: NotaManualBody,
    current_user: dict = Depends(get_current_user)
):
    """
    Adiciona uma nota manual (registro) ao perfil de um cliente.
    """
    user_email = current_user.get("email")
    logging.info(f"Admin {user_email} adicionando nota ao cliente {body.cliente_id} no salão {body.salao_id}.")
    
    try:
        # Define o local de salvamento
        nota_ref = db.collection('cabeleireiros').document(body.salao_id).collection('clientes').document(body.cliente_id).collection('registros').document()
        
        nota_data = {
            "tipo": "NotaManual",
            "data_envio": firestore.SERVER_TIMESTAMP, # Usamos 'data_envio' para ordenação
            "texto": body.nota_texto,
            "enviado_por": user_email
        }
        
        # Salva no Firestore
        nota_ref.set(nota_data)
        
        # Busca os dados salvos (para obter o timestamp REAL)
        # Necessário dar get() novamente após o set() para ter o SERVER_TIMESTAMP resolvido
        nota_salva = nota_ref.get().to_dict() 
        
        # Pydantic serializa a data de forma segura, o frontend fará o parseISO
        return TimelineItem(
            id=nota_ref.id,
            tipo=nota_salva.get("tipo"),
            data_evento=nota_salva.get("data_envio"), # Firestore Timestamp
            dados=nota_salva
        )
        
    except Exception as e:
        logging.exception(f"Erro ao adicionar nota manual: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao salvar nota.")
    
    
@router.post("/clientes/enviar-promocional", status_code=status.HTTP_200_OK)
async def send_promotional_email_endpoint(
    body: EmailPromocionalBody,
    current_user: dict = Depends(get_current_user)
):
    """
    Busca o perfil do cliente no Firestore e envia um e-mail promocional personalizado.
    Também registra o envio no perfil do cliente (CRM).
    """
    user_uid = current_user.get("uid")
    logging.info(f"Admin {current_user.get('email')} solicitou envio promocional para o cliente {body.cliente_id}.")

    try:
        # --- 1. Busca os Dados CRM e do Salão ---
        
        # Busca o perfil do cliente
        cliente_doc_ref = db.collection('cabeleireiros').document(body.salao_id).collection('clientes').document(body.cliente_id)
        cliente_doc = cliente_doc_ref.get()

        if not cliente_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perfil do cliente não encontrado.")

        cliente_data = cliente_doc.to_dict()
        
        # Pega os dados essenciais do cliente
        customer_email = cliente_data.get('email')
        customer_name = cliente_data.get('nome')

        if not customer_email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="E-mail do cliente ausente no perfil.")
            
        # Pega o nome do salão (remetente)
        salon_data = get_hairdresser_data_from_db(body.salao_id)
        salon_name = salon_data.get("nome_salao", "Seu Salão")

        
        # --- 2. Envio do E-mail ---
        email_sent = email_service.send_promotional_email_to_customer(
            customer_email=customer_email,
            customer_name=customer_name,
            salon_name=salon_name,
            custom_subject=body.subject,
            custom_message_html=body.message # O frontend enviará HTML (pode ser texto simples também)
        )
        
        if not email_sent:
            raise Exception("O serviço de e-mail falhou ao enviar a mensagem.")

        # --- 3. Registro no Histórico do Cliente (CRM) ---
        # Adiciona uma nota/registro no documento do cliente
        try:
            registro_ref = cliente_doc_ref.collection('registros').document()
            registro_ref.set({
                "tipo": "Promocional",
                "data_envio": firestore.SERVER_TIMESTAMP,
                "assunto": body.subject,
                "enviado_por": current_user.get('email'),
                "message_preview": body.message[:100] + "..." # Salva um preview
            })
        except Exception as e:
            logging.error(f"Falha ao registrar envio promocional no CRM: {e}")
            # Continua, pois o e-mail já foi enviado
            
        logging.info(f"E-mail promocional enviado e registrado para {customer_email}.")
        
        return {"message": "E-mail promocional enviado com sucesso!"}

    except HTTPException as httpe: 
        raise httpe
    except Exception as e:
        # Se falhou, tentamos dar ao usuário uma mensagem útil
        detail_msg = str(e)
        if "E-mail do cliente ausente" in detail_msg:
             detail_msg = "O perfil do cliente não possui um e-mail cadastrado."
             
        logging.exception(f"Erro ao enviar e-mail promocional para cliente {body.cliente_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail_msg)
    
class DashboardDataResponse(BaseModel):
    agendamentos_foco_valor: int
    novos_clientes_valor: int
    receita_estimada: str # R$ formatada
    chart_data: List[dict] # Dados para o gráfico
    
def _process_chart_data(snapshot, end_date: datetime, dias: int) -> List[dict[str, Any]]:
    """
    Processa o snapshot de agendamentos e retorna a lista formatada para o Recharts.
    """
    
    # 1. Preparar a estrutura de dias
    agendamentos_por_data = {}
    for i in range(dias):
        # A data é o dia do período
        date = (end_date - timedelta(days=i)).date() 
        # Usa ISO formatado como chave para fácil comparação
        date_key = date.isoformat()
        
        # O nome do dia (ex: 'Seg', 'Ter')
        day_name = date.strftime('%a') 
        
        agendamentos_por_data[date_key] = {
            "name": day_name.capitalize().replace('.', ''), # Capitaliza e remove ponto (ex: 'Seg')
            "Agendamentos": 0,
            "fullDate": date_key
        }

    # 2. Contar agendamentos no Snapshot
    for doc in snapshot:
        data = doc.to_dict()
        # O campo 'startTime' é um objeto Timestamp do Firestore, que já foi convertido
        # para datetime por padrão. Precisamos apenas da parte da data.
        
        start_dt: datetime = data['startTime']
        
        # Converte para a data ISO para encontrar a chave
        agendamento_date_key = start_dt.date().isoformat()
        
        if agendamento_date_key in agendamentos_por_data:
            agendamentos_por_data[agendamento_date_key]["Agendamentos"] += 1

    # 3. Formatar para a saída (Array de objetos)
    # Ordena as chaves por data antes de retornar o valor (garante ordem correta no gráfico)
    sorted_keys = sorted(agendamentos_por_data.keys())
    
    return [agendamentos_por_data[key] for key in sorted_keys]

# --- FIM DA FUNÇÃO AUXILIAR ---


@router.get("/dashboard-data/{salao_id}", response_model=DashboardDataResponse)
async def get_dashboard_data_consolidated(
    salao_id: str,
    agendamentos_foco_periodo: str = Query("hoje"), # 'hoje', 'prox7dias', 'novos24h'
    novos_clientes_periodo: str = Query("30dias"), # 'hoje', '7dias', '30dias'
    agendamentos_grafico_dias: int = Query(7), # 7, 15, 30
    receita_periodo: str = Query("hoje"), 
    current_user: dict = Depends(get_current_user)
):
    """
    Busca de forma segura todos os dados de KPIs e Gráfico em uma única chamada.
    Executa TODAS as queries do dashboard no backend.
    """
    logging.info(f"Admin {current_user.get('email')} buscando dados consolidados para {salao_id}.")
    
    try:
        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        clientes_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes')

        now_utc = datetime.now(pytz.utc) 
        
        # --- Lógica de Datas ---
        hoje_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 1. Novos Clientes
        if novos_clientes_periodo == 'hoje':
             clientes_start = hoje_utc
             clientes_end = now_utc
        elif novos_clientes_periodo == '7dias':
             clientes_start = hoje_utc - timedelta(days=6) # 7 dias, incluindo hoje
             clientes_end = now_utc
        else: # 30 dias
             clientes_start = hoje_utc - timedelta(days=29) # 30 dias, incluindo hoje
             clientes_end = now_utc
        
        # 2. Agendamentos em Foco
        if agendamentos_foco_periodo == 'hoje':
            foco_start = hoje_utc
            foco_end = hoje_utc + timedelta(days=1)
        elif agendamentos_foco_periodo == 'prox7dias':
            foco_start = now_utc # A partir da hora atual
            foco_end = now_utc + timedelta(days=7)
        else: # novos24h
            foco_start = now_utc - timedelta(hours=24)
            foco_end = now_utc
            
        # 3. Receita
        if receita_periodo == 'mes':
             receita_start = now_utc.replace(day=1).replace(hour=0, minute=0, second=0, microsecond=0)
             receita_end = (now_utc.replace(day=1) + timedelta(days=32)).replace(day=1) - timedelta(microseconds=1)
        else: # (Assumindo que sua lógica de receita já cobre os outros filtros 'hoje'/'semana' corretamente)
             receita_start = hoje_utc
             receita_end = now_utc + timedelta(days=7) # Exemplo de default

        # 4. Gráfico
        chart_start = hoje_utc - timedelta(days=agendamentos_grafico_dias - 1)
        chart_end = now_utc
        
        
        # --- Queries Firestone (AGORA SEGURAS) ---
        novos_clientes_query = clientes_ref.where('data_cadastro', '>=', clientes_start).where('data_cadastro', '<=', clientes_end)
        foco_query = agendamentos_ref.where('startTime', '>=', foco_start).where('startTime', '<', foco_end).where('status', '!=', 'cancelado')
        receita_query = agendamentos_ref.where('startTime', '>=', receita_start).where('status', '!=', 'cancelado')
        chart_query = agendamentos_ref.where('startTime', '>=', chart_start).where('startTime', '<=', chart_end).where('status', '!=', 'cancelado')

        
        # --- Execução das Consultas (Síncronas ou Assíncronas) ---
        # OBS: Se seu projeto usa o Admin SDK em ambiente assíncrono (FastAPI),
        # você precisa usar threadpool/executor. Para simplicidade, assumi que 
        # 'get_dashboard_data_consolidated' é chamada de forma assíncrona.

        novos_clientes_snapshot = novos_clientes_query.get()
        foco_snapshot = foco_query.get()
        receita_snapshot = receita_query.get()
        chart_snapshot = chart_query.get()
        
        
        # --- Processamento dos Resultados ---
        
        # 1. Novos Clientes
        count_novos_clientes = len(novos_clientes_snapshot)
        
        # 2. Agendamentos em Foco
        count_agendamentos_foco = len(foco_snapshot)
        
        # 3. Receita
        total_receita = sum(doc.to_dict().get('servicePrice', 0) for doc in receita_snapshot)
        receita_formatada = f"{total_receita:.2f}".replace('.', ',')
        
        # 4. Gráfico (CHAMADA À FUNÇÃO DE PROCESSAMENTO)
        processed_chart_data = _process_chart_data(
            snapshot=chart_snapshot, 
            end_date=now_utc, 
            dias=agendamentos_grafico_dias
        )
        
        
        # --- Retorno Consolidado ---
        return DashboardDataResponse(
            agendamentos_foco_valor=count_agendamentos_foco,
            novos_clientes_valor=count_novos_clientes,
            receita_estimada=receita_formatada,
            chart_data=processed_chart_data # <<< RETORNA O ARRAY CORRETO
        )

    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro no endpoint consolidado do dashboard: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao carregar dados do dashboard.")
    
class MarketingMassaBody(BaseModel):
    salao_id: str
    subject: str = Field(..., min_length=5)
    message: str = Field(..., min_length=10)
    # Adicione filtros aqui no futuro (ex: segmentacao: str = "todos")
# --- <<< FIM DO NOVO MODELO >>> ---

def _process_mass_email_send(salao_id: str, subject: str, message: str, admin_email: str):
    """
    Executa o envio real em uma thread separada para evitar o timeout da requisição principal.
    """
    logging.info(f"THREAD DE BACKGROUND: Iniciando envio em massa para salão {salao_id}.")

    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "Seu Salão")
    except Exception:
        logging.error(f"Falha na thread: Salão {salao_id} não encontrado ou dados incompletos.")
        return

    clientes_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes')
    
    clientes_enviados = 0
    clientes_falha_email = 0
    EMAIL_DELAY_SECONDS = 0.1 # 100ms de pausa por email para evitar bloqueio

    import time # Importação necessária para o time.sleep
    
    # OBS: Usamos clientes_ref.stream() para buscar todos.
    for doc in clientes_ref.stream():
        cliente_data = doc.to_dict()
        customer_email = cliente_data.get('email')
        customer_name = cliente_data.get('nome', 'Cliente')
        cliente_doc_ref = doc.reference

        if customer_email and customer_email.strip().lower() != 'n/a':
            try:
                email_sent = email_service.send_promotional_email_to_customer(
                    customer_email=customer_email,
                    customer_name=customer_name,
                    salon_name=salon_name,
                    custom_subject=subject,
                    custom_message_html=message
                )

                if email_sent:
                    clientes_enviados += 1
                    # Registro do envio (CRM)
                    registro_ref = cliente_doc_ref.collection('registros').document()
                    registro_ref.set({
                        "tipo": "MarketingMassa",
                        "data_envio": firestore.SERVER_TIMESTAMP,
                        "assunto": subject,
                        "enviado_por": admin_email,
                        "message_preview": message[:100] + "..."
                    })
                
            except Exception as e:
                clientes_falha_email += 1
                logging.error(f"Falha no envio de e-mail para {customer_email}: {e}")
        
        time.sleep(EMAIL_DELAY_SECONDS) # Pausa para evitar rate limiting

    logging.info(f"THREAD FINALIZADA. Disparo de marketing em massa para {salao_id}. Enviados: {clientes_enviados}, Falhas: {clientes_falha_email}")


# --- <<< ENDPOINT DE REQUISIÇÃO (CHAMADOR) >>> ---
@router.post("/marketing/enviar-massa", status_code=status.HTTP_202_ACCEPTED)
async def send_mass_marketing_email(
    body: MarketingMassaBody,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    """
    Recebe a requisição do frontend, delega a tarefa de envio para o background
    e retorna imediatamente 202 Accepted (sucesso), garantindo um corpo JSON válido.
    """
    
    user_email_admin = current_user.get("email")
    logging.info(f"Admin {user_email_admin} REQUISITOU disparo de marketing em massa.")

    # 1. Busca os dados essenciais do salão para a thread
    try:
        salon_data = get_hairdresser_data_from_db(body.salao_id)
        if not salon_data:
             raise HTTPException(status_code=404, detail="Salão não encontrado.")
        # O nome do salão é necessário para a mensagem de sucesso
        salon_name = salon_data.get("nome_salao", "Seu Salão")
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Falha na busca inicial do salão: {e}")
        raise HTTPException(status_code=500, detail="Erro ao verificar dados iniciais do salão.")


    # 2. Delega o trabalho pesado para a função de background
    try:
        background_tasks.add_task(
            _process_mass_email_send, 
            body.salao_id, 
            body.subject, 
            body.message, 
            user_email_admin
        )
    except Exception as e:
        logging.error(f"Falha CRÍTICA ao iniciar Background Task: {e}")
        raise HTTPException(status_code=500, detail="O servidor não conseguiu iniciar o processo de envio.")
    
    # 3. Retorna SUCESSO (202 ACCEPTED) com um corpo JSON explícito
    return {
        "status": "Processamento Aceito",
        "message": f"Disparo de e-mail iniciado em segundo plano para {salon_name}. Retorne ao painel."
    }
