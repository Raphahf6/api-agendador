# backend/routers/admin_routes.py
from dotenv import load_dotenv
load_dotenv() 
import logging
import os
import re
import pytz 
from fastapi import APIRouter, Depends, HTTPException, status, Query, Request, BackgroundTasks
from fastapi.responses import RedirectResponse 
from firebase_admin import firestore
from google.cloud.firestore import FieldFilter
from typing import List, Optional, Any, Dict
from datetime import datetime, timedelta
from pydantic import BaseModel, Field,EmailStr
from google_auth_oauthlib.flow import Flow
import mercadopago 
import httpx # Importado para o OAuth do MP
from firebase_admin import auth as admin_auth
from mercadopago.config import RequestOptions

# Importações dos modelos
from core.models import (
    ClientDetail, NewClientData, Service, ManualAppointmentData, ClienteListItem, 
    EmailPromocionalBody, NotaManualBody, TimelineItem, CalendarEvent, 
    ReagendamentoBody, UserPaidSignupPayload, DashboardDataResponse, 
    PayerIdentification, PayerData, HistoricoAgendamentoItem, ClienteDetailsResponse,
    MarketingMassaBody,PagamentoSettingsBody,OwnerRegisterRequest
)
from core.auth import get_current_user 
from core.db import get_all_clients_from_db, get_hairdresser_data_from_db, db
from services import email_service, calendar_service

API_BASE_URL = "https://api-agendador.onrender.com/api/v1"
sdk = mercadopago.SDK("TEST_ACCESS_TOKEN")

# --- Constantes ---
MARKETING_COTA_INICIAL = 100 
SETUP_PRICE = float(os.environ.get("HORALIS_SETUP_PRICE"))

# --- Configuração dos Roteadores ---
router = APIRouter(
    prefix="/admin",
    tags=["Admin"],
    dependencies=[Depends(get_current_user)] 
)
callback_router = APIRouter(
    prefix="/admin", 
    tags=["Admin - OAuth Callback"],
)
webhook_router = APIRouter(
    prefix="/webhooks",
    tags=["Webhooks"]
)
auth_router = APIRouter(
    prefix="/auth",
    tags=["Autenticação"],
)

# --- Constantes do Google OAuth ---
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
SCOPES = ['https://www.googleapis.com/auth/calendar']
RENDER_API_URL = "https://api-agendador.onrender.com/api/v1" 
GOOGLE_REDIRECT_URI = f"{RENDER_API_URL}/admin/google/auth/callback"

# --- Constantes do Mercado Pago OAuth ---
MP_APP_ID = os.environ.get("MP_APP_ID")
MP_SECRET_KEY = os.environ.get("MP_SECRET_KEY")
MP_REDIRECT_URI = f"{RENDER_API_URL}/admin/mercadopago/callback"
MP_AUTH_URL = "https://auth.mercadopago.com.br/authorization"
MP_TOKEN_URL = "https://api.mercadopago.com/oauth/token"

# --- Configuração SDK Mercado Pago ---
try:
    MP_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
    if not MP_ACCESS_TOKEN:
        logging.warning("MERCADO_PAGO_ACCESS_TOKEN não está configurado.")
        sdk = None
        mp_preference_client = None 
        mp_payment_client = None
    else:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        mp_preference_client = sdk.preference()
        mp_payment_client = sdk.payment()
        logging.info("SDK do Mercado Pago (Preference e Payment) inicializados.")
except Exception as e:
    logging.error(f"Erro ao inicializar SDK Mercado Pago: {e}")
    sdk = None
    mp_preference_client = None
    mp_payment_client = None
    
    
PIX_EXPIRATION_LIMIT = timedelta(minutes=30) 

def is_pending_payment_expired(payment_id: str, mp_payment_client) -> bool:
    """
    Verifica se um PIX pendente expirou baseado no status do MP e no tempo de criação.
    """
    if not payment_id:
        return True # Se não há ID de pagamento, está "expirado" para fins de re-registro.

    try:
        # 1. Tenta obter o status do pagamento no MP
        payment_response = mp_payment_client.get(payment_id)
        
        if payment_response.get("status") in [200, 201]:
            payment = payment_response.get("response")
            
            # Se o status já for final (approved, rejected, cancelled), ele não é mais 'pending'.
            if payment.get("status") not in ["pending", "in_process"]:
                return True
            
            # 2. Se o PIX ainda estiver 'pending', verificamos a data de criação
            date_created_str = payment.get("date_created")
            if date_created_str:
                try:
                    # Tenta converter a data
                    date_created = datetime.fromisoformat(date_created_str).astimezone(pytz.utc)
                    now = datetime.now(pytz.utc)
                    
                    # Compara
                    if now - date_created > PIX_EXPIRATION_LIMIT:
                        return True
                        
                except ValueError:
                    # Se a formatação da data falhar, considera o PIX "estranho" e expirado
                    logging.error(f"Data de criação do PIX {payment_id} inválida: {date_created_str}")
                    return True # Considera expirado para não travar o usuário
            else:
                # Se date_created_str for None, considera-se expirado
                logging.warning(f"PIX {payment_id} pendente sem data de criação. Forçando expiração.")
                return True

            return False # Pagamento ainda pendente e DENTRO do prazo de validade.
                
    except Exception as e:
        # Se a API do MP falhar, assumimos que o pagamento está inacessível/expirado
        logging.error(f"Erro ao verificar status MP para {payment_id}: {e}")
        return True
        
    return False # Pagamento ainda pendente e DENTRO do prazo de validade.

# --- ENDPOINT PÚBLICO DE CADASTRO PAGO DIRETO ---
DIAS_DA_SEMANA_KEYS = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

# ESTRUTURA INICIAL COMPLETA (Para injeção no Firestore)
INITIAL_SCHEDULE_DATA = {
    day: {
        'isOpen': day not in ['saturday', 'sunday'],
        'openTime': '09:00',
        'closeTime': '18:00',
        'hasLunch': True,
        'lunchStart': '12:00',
        'lunchEnd': '13:00',
    }
    for day in DIAS_DA_SEMANA_KEYS
}
# ----------------------------------------------------

@auth_router.post("/criar-conta-paga", status_code=status.HTTP_201_CREATED)
async def criar_conta_paga_com_pagamento(payload: UserPaidSignupPayload,
                                         background_tasks: BackgroundTasks):
    
    # OBS: Usamos a variável SETUP_PRICE (do ENV) no bloco de pagamento
    
    salao_id = payload.client_whatsapp_id
    uid = None
    
    if not mp_payment_client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Serviço de pagamento indisponível.")

    # ----------------------------------------------------
    # VERIFICAÇÃO DE E-MAIL (E VALIDAÇÃO DE CONFLITO DE ID)
    # ----------------------------------------------------
    try:
        admin_auth.get_user_by_email(payload.email)
        pass  # Email existe
    except admin_auth.UserNotFoundError:
        pass # Email é novo
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao verificar e-mail: {e}")

    # ----------------------------------------------------
    # LÓGICA DE VERIFICAÇÃO E REEXIBIÇÃO DE PIX PENDENTE (Mantida)
    # ----------------------------------------------------
    try:
        salao_doc = db.collection('cabeleireiros').document(salao_id).get()
        
        if salao_doc.exists:
            status_atual = salao_doc.get("subscriptionStatus")
            
            if status_atual in ["active", "trialing"]:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este número de WhatsApp já está ativo ou em teste.")
            
            if status_atual == "pending":
                last_payment_id = salao_doc.get("mercadopagoLastPaymentId")
                
                if is_pending_payment_expired(last_payment_id, mp_payment_client):
                    logging.info(f"PIX expirado detectado para {salao_id}. Executando Rollback Forçado.")
                    try:
                        user = admin_auth.get_user_by_email(payload.email)
                        admin_auth.delete_user(user.uid)
                    except: pass 
                    salao_doc.reference.delete()
                    pass
                else:
                    payment_response = mp_payment_client.get(last_payment_id)
                    payment_result = payment_response.get("response", {})
                    qr_code_data = payment_result.get("point_of_interaction", {}).get("transaction_data", {})
                    if qr_code_data.get("qr_code_base64") and qr_code_data.get("qr_code"):
                        return { "status": "pending_pix_existing", "message": "Pagamento PIX pendente encontrado. Reexibindo QR Code.",
                                 "payment_data": { "qr_code": qr_code_data.get("qr_code"), "qr_code_base64": qr_code_data.get("qr_code_base64"),
                                                   "payment_id": last_payment_id } }
                    else:
                        logging.warning(f"Falha ao obter dados PIX existentes para {last_payment_id}. Forçando Rollback.")
                        salao_doc.reference.delete()
                        try:
                            user = admin_auth.get_user_by_email(payload.email)
                            admin_auth.delete_user(user.uid)
                        except: pass
                        pass
            
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao verificar dados: {e}")

    # ----------------------------------------------------
    # CRIAÇÃO DE USUÁRIO (SÓ CHEGA AQUI SE FOR NOVO OU HOUVE ROLLBACK)
    # ----------------------------------------------------
    try:
        new_user = admin_auth.create_user(
            email=payload.email,
            password=payload.password,
            display_name=payload.nome_salao
        )
        uid = new_user.uid
    except Exception as e:
        if "EMAIL_EXISTS" in str(e):
             raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este e-mail já está cadastrado em outra conta. Tente fazer login ou use outro e-mail.")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar usuário: {e}")

    salao_doc_ref = db.collection("cabeleireiros").document(salao_id)
    
    # ----------------------------------------------------
    # CRIAÇÃO DO DOCUMENTO FIRESTORE (INJETANDO DADOS PADRÃO DE HORÁRIO)
    # ----------------------------------------------------
    try:
        now = datetime.now(pytz.utc)
        
        # Leitura segura do preço (do ambiente)
        SETUP_PRICE = float(os.environ.get("HORALIS_SETUP_PRICE", 0.99))
        
        salao_data = {
            "nome_salao": payload.nome_salao,
            "numero_whatsapp": payload.numero_whatsapp,
            "email": payload.email,
            "ownerUID": uid,
            "createdAt": now,
            
            # >>> INCLUSÃO CRÍTICA DA AGENDA DETALHADA PADRÃO <<<
            "horario_trabalho_detalhado": INITIAL_SCHEDULE_DATA,
            
            # Manter os campos antigos com valores padrão para compatibilidade de leitura do DB:
            "dias_trabalho": ['monday', 'tuesday', 'wednesday', 'thursday', 'friday'],
            "horario_inicio": '09:00',
            "horario_fim": '18:00',
            
            # Restante dos campos de subscrição
            "subscriptionStatus": "pending",
            "paidUntil": None,
            "subscriptionLastUpdated": now,
            "trialEndsAt": None,
            "mercadopago_customer_id": None,
            "google_sync_enabled": False,
            "marketing_cota_total": MARKETING_COTA_INICIAL,
            "marketing_cota_usada": 0,
            "marketing_cota_reset_em": None,
        }
        salao_doc_ref.set(salao_data)
    except Exception as e:
        logging.error(f"Erro ao criar salão no Firestore: {e}. Fazendo rollback do Auth...")
        if uid: admin_auth.delete_user(uid)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao salvar dados do salão: {e}")

    # ----------------------------------------------------
    # PROCESSAMENTO MP
    # ----------------------------------------------------
    try:
        notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"
        
        ro_obj = RequestOptions(custom_headers={"X-Meli-Session-Id": payload.device_id})

        payer_identification_data = {
            "type": payload.payer.identification.type,
            "number": payload.payer.identification.number
        } if payload.payer.identification else None

        # Lógica de additional_info (mantida)
        nome_completo = payload.nome_salao.strip().split()
        primeiro_nome = nome_completo[0]
        ultimo_nome = nome_completo[-1] if len(nome_completo) > 1 else primeiro_nome

        additional_info = {
            "payer": {
                "first_name": primeiro_nome, "last_name": ultimo_nome,
                "phone": { "area_code": payload.numero_whatsapp[3:5], "number": payload.numero_whatsapp[5:] },
            },
            "items": [{
                "id": "HoralisAssinatura", "title": "Horalis Pro (30 dias)",
                "description": "Assinatura Horalis", "quantity": 1, 
                "unit_price": SETUP_PRICE, "category_id": "saas" 
            }]
        }
        statement_descriptor = "HORALISPRO"
        
        # LÓGICA PIX
        if payload.payment_method_id == 'pix':
            payment_data = {
                "transaction_amount": SETUP_PRICE, "description": "Assinatura Horalis Pro (PIX)",
                "payment_method_id": "pix",
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": salao_id, "notification_url": notification_url, 
                "additional_info": additional_info, "statement_descriptor": statement_descriptor
            }
            payment_response = mp_payment_client.create(payment_data, request_options=ro_obj)
            
            if payment_response["status"] not in [200, 201]:
                raise Exception(f"Erro MercadoPago (PIX): {payment_response.get('response').get('message', 'Erro desconhecido')}")

            payment_result = payment_response["response"]
            qr_code_data = payment_result.get("point_of_interaction", {}).get("transaction_data", {})
            salao_doc_ref.update({"mercadopagoLastPaymentId": payment_result.get("id")})
            
            return { "status": "pending_pix", "message": "PIX gerado. Aguardando pagamento.",
                     "payment_data": { "qr_code": qr_code_data.get("qr_code"), "qr_code_base64": qr_code_data.get("qr_code_base64"),
                                       "payment_id": payment_result.get("id") } }
        
        # LÓGICA CARTÃO
        else:
            payment_data = {
                "transaction_amount": SETUP_PRICE, "token": payload.token, "description": "Assinatura Horalis Pro (Cartão)",
                "installments": payload.installments, "payment_method_id": payload.payment_method_id, "issuer_id": payload.issuer_id,
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": salao_id, "notification_url": notification_url,
                "additional_info": additional_info, "statement_descriptor": statement_descriptor
            }
            payment_response = mp_payment_client.create(payment_data, request_options=ro_obj)

            if payment_response["status"] not in [200, 201]:
                error_msg = payment_response.get('response', {}).get('message', 'Erro desconhecido')
                raise Exception(f"Erro MercadoPago (Cartão): {error_msg}")

            payment_status = payment_response["response"].get("status")
            
            if payment_status == "approved":
                new_paid_until = datetime.now(pytz.utc) + timedelta(days=30)
                salao_doc_ref.update({
                    "subscriptionStatus": "active", "paidUntil": new_paid_until,
                    "subscriptionLastUpdated": firestore.SERVER_TIMESTAMP,
                    "mercadopagoLastPaymentId": payment_response["response"].get("id"),
                    "marketing_cota_total": MARKETING_COTA_INICIAL, "marketing_cota_usada": 0,
                    "marketing_cota_reset_em": new_paid_until
                })
                
                background_tasks.add_task(
                    email_service.send_welcome_email_to_salon,
                    salon_email=payload.email, salon_name=payload.nome_salao, 
                    salao_id=salao_id, login_email=payload.email 
                )

                return {"status": "approved", "message": "Pagamento aprovado e conta criada!"}
            
            elif payment_status in ["in_process", "pending", "pending_review_manual"]:
                logging.info(f"Assinatura (Cartão) PENDENTE ou EM REVISÃO ({payment_status}). Salão {salao_id} aguardando webhook.")
                salao_doc_ref.update({"mercadopagoLastPaymentId": payment_response["response"].get("id")})
                return {"status": "pending_review", "message": "Seu pagamento está em análise. Você será notificado por e-mail."}
            
            else:
                error_detail = payment_response["response"].get("status_detail", "Pagamento rejeitado.")
                if uid: admin_auth.delete_user(uid)
                salao_doc_ref.delete()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    except Exception as e:
        error_message = str(e)
        logging.error(f"Erro ao processar pagamento: {error_message}. Fazendo rollback total...")
        if uid:
            try: admin_auth.delete_user(uid)
            except Exception as auth_err: logging.error(f"Falha no rollback do Auth: {auth_err}")
        try: db.collection("cabeleireiros").document(salao_id).delete()
        except Exception as db_err: logging.error(f"Falha no rollback do Firestore: {db_err}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=error_message)
    
    
@auth_router.post("/register-owner", status_code=status.HTTP_201_CREATED)
def register_owner(data: OwnerRegisterRequest):
    try:
        # 1. Configurar datas
        tz = pytz.timezone('America/Sao_Paulo')
        now = datetime.now(tz)
        trial_end = now + timedelta(days=7) # 🌟 7 Dias de Teste Grátis

        # 2. Dados Iniciais do Salão
        # Aqui definimos os padrões para que o painel não quebre
        new_salon_data = {
            "ownerUID": data.uid,
            "nome_salao": data.nome_salao,
            "numero_whatsapp": data.whatsapp, # Mapeando para o nome usado no BD
            "email_contato": data.email,
            "cpf_proprietario": data.cpf,
            
            # --- Configurações de Assinatura ---
            "subscriptionStatus": "trialing", # Status de Teste
            "trialEndsAt": trial_end.isoformat(),
            "createdAt": now.isoformat(),
            
            # --- Configurações Visuais Padrão ---
            "cor_primaria": "#0E7490",
            "cor_secundaria": "#FFFFFF",
            "tagline": "Agende seu horário conosco!",
            
            # --- Configurações de Negócio Padrão ---
            "marketing_cota_total": 100,
            "marketing_cota_usada": 0,
            "sinal_valor": 0.0,
            "mp_public_key": None,
            
            # --- Horário Padrão (Seg-Sex 09-18) ---
            "horario_trabalho_detalhado": {
                "monday": {"isOpen": True, "openTime": "09:00", "closeTime": "18:00", "hasLunch": True, "lunchStart": "12:00", "lunchEnd": "13:00"},
                "tuesday": {"isOpen": True, "openTime": "09:00", "closeTime": "18:00", "hasLunch": True, "lunchStart": "12:00", "lunchEnd": "13:00"},
                "wednesday": {"isOpen": True, "openTime": "09:00", "closeTime": "18:00", "hasLunch": True, "lunchStart": "12:00", "lunchEnd": "13:00"},
                "thursday": {"isOpen": True, "openTime": "09:00", "closeTime": "18:00", "hasLunch": True, "lunchStart": "12:00", "lunchEnd": "13:00"},
                "friday": {"isOpen": True, "openTime": "09:00", "closeTime": "18:00", "hasLunch": True, "lunchStart": "12:00", "lunchEnd": "13:00"},
                "saturday": {"isOpen": True, "openTime": "09:00", "closeTime": "14:00", "hasLunch": False, "lunchStart": None, "lunchEnd": None},
                "sunday": {"isOpen": False, "openTime": "09:00", "closeTime": "18:00", "hasLunch": False, "lunchStart": None, "lunchEnd": None},
            }
        }

        # 3. Salvar no Firestore
        # Usamos o UID do usuário como ID do documento para facilitar a busca (1 para 1)
        # Ou você pode gerar um ID aleatório, mas usar o UID é prático.
        
        # Opção A: Usar UID como ID do Documento (Recomendado se 1 usuário = 1 salão)
        db.collection('cabeleireiros').document(data.uid).set(new_salon_data)
        
        # Opção B: Se o ID do salão for diferente do UID, você precisa gerar um e vincular.
        # Mas pelo seu código anterior, parece que salaoId é passado na URL, então vamos garantir que o login redirecione corretamente.

        return {
            "message": "Conta criada com sucesso!",
            "salao_id": data.uid,
            "trial_ends_at": trial_end.isoformat()
        }

    except Exception as e:
        print(f"Erro ao registrar dono: {e}")
        raise HTTPException(status_code=500, detail=f"Erro interno ao criar conta: {str(e)}")

# --- ENDPOINT PARA CRIAR ASSINATURA (LOGADO) ---
@router.post("/pagamentos/criar-assinatura", status_code=status.HTTP_201_CREATED)
async def create_subscription_checkout(
    current_user: dict = Depends(get_current_user)
):
    if not mp_preference_client:
        raise HTTPException(status_code=503, detail="Serviço de pagamento indisponível.")
    user_uid = current_user.get("uid")
    user_email = current_user.get("email")
    try:
        query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', user_uid)).limit(1)
        client_doc_list = list(query.stream())
        if not client_doc_list:
            raise HTTPException(status_code=404, detail="Nenhum salão encontrado.")
        salao_id = client_doc_list[0].id
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao associar pagamento.")

    back_url_success = f"https://horalis.app/painel/{salao_id}/assinatura?status=success"
    notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"

    preference_data = {
        "items": [
            {
                "id": f"horalis_pro_mensal_{salao_id}",
                "title": "Acesso Horalis Pro (30 dias)",
                "description": "Acesso completo à plataforma Horalis por 30 dias.",
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": SETUP_PRICE
            }
        ],
        "payer": { "email": user_email },
        "back_urls": {
            "success": back_url_success,
            "failure": f"https://horalis.app/painel/{salao_id}/assinatura?status=failure",
            "pending": f"https://horalis.app/painel/{salao_id}/assinatura?status=pending"
        },
        "auto_return": "approved",
        "notification_url": notification_url,
        "external_reference": salao_id,
    }
    try:
        preference_result = mp_preference_client.create(preference_data)
        if preference_result["status"] not in [200, 201]:
            raise HTTPException(status_code=500, detail="Erro ao gerar link de pagamento.")
        checkout_url = preference_result["response"].get("init_point")
        if not checkout_url:
             raise HTTPException(status_code=500, detail="Erro ao obter URL de checkout.")
        return {"checkout_url": checkout_url}
    except Exception as e:
        logging.exception(f"Erro crítico ao criar pagamento MP para {user_email}: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar pagamento.")

# --- ENDPOINT DE WEBHOOK (MODIFICADO COM COTAS) ---
@webhook_router.post("/mercado-pago")
async def webhook_mercado_pago(request: Request):
    body = await request.json()
    logging.info(f"Webhook Mercado Pago recebido: Tipo: {body.get('type')}, Ação: {body.get('action')}")
    
    if not mp_payment_client or not body:
        return {"status": "ignorado"}

    if body.get("type") == "payment":
        payment_id = body.get("data", {}).get("id")
        if not payment_id:
            return {"status": "id não encontrado"}
            
        try:
            payment_data = mp_payment_client.get(payment_id)
            if payment_data["status"] != 200:
                return {"status": "erro ao buscar dados"}
            
            data = payment_data["response"]
            ref_id = data.get("external_reference") 
            status_pagamento = data.get("status")
            
            if not ref_id:
                return {"status": "referência externa faltando"}
            
            # --- ROTEAMENTO DO WEBHOOK ---
            
            # CASO 1: É um SINAL DE AGENDAMENTO
            if ref_id.startswith("agendamento__"):
                logging.info(f"Webhook recebido para um Sinal de Agendamento: {ref_id}")
                try:
                    parts = ref_id.split("__")
                    salao_id = parts[1]
                    agendamento_id = parts[2]
                except Exception:
                    logging.error(f"Webhook falhou. Formato de external_reference inválido: {ref_id}")
                    return {"status": "referência inválida"}

                agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
                
                if status_pagamento == 'approved':
                    logging.info(f"Sinal APROVADO para agendamento {agendamento_id}. Confirmando...")
                    
                    agendamento_ref.update({
                        "status": "confirmado",
                        "mercadopagoPaymentId": payment_id
                    })
                    
                    try:
                        agendamento_data = agendamento_ref.get().to_dict()
                        salon_data = get_hairdresser_data_from_db(salao_id)
                        
                        email_service.send_confirmation_email_to_salon(
                            salon_email=salon_data.get('calendar_id'), 
                            salon_name=salon_data.get('nome_salao'), 
                            customer_name=agendamento_data.get('customerName'), 
                            client_phone=agendamento_data.get('customerPhone'), 
                            service_name=agendamento_data.get('serviceName'), 
                            start_time_iso=agendamento_data.get('startTime').isoformat()
                        )
                        email_service.send_confirmation_email_to_customer(
                            customer_email=agendamento_data.get('customerEmail'), 
                            customer_name=agendamento_data.get('customerName'),
                            service_name=agendamento_data.get('serviceName'), 
                            start_time_iso=agendamento_data.get('startTime').isoformat(),
                            salon_name=salon_data.get('nome_salao'),
                            salao_id=salao_id
                        )
                    except Exception as e:
                        logging.error(f"Webhook (Agendamento) Aprovado, mas falhou ao enviar e-mails: {e}")
                
                else:
                    logging.info(f"Sinal falhou/expirou para agendamento {agendamento_id}. Status: {status_pagamento}")
                    agendamento_ref.update({"status": status_pagamento}) 

            # CASO 2: É um PAGAMENTO DE ASSINATURA
            else:
                logging.info(f"Webhook recebido para uma Assinatura de Salão: {ref_id}")
                salao_id = ref_id
                salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
                
                if status_pagamento == 'approved':
                    new_paid_until = datetime.now(pytz.utc) + timedelta(days=30)
                    logging.info(f"Assinatura APROVADA. Atualizando para 'active' o salão: {salao_id}...")
                    
                    salao_doc_ref.update({
                        "subscriptionStatus": "active",
                        "paidUntil": new_paid_until,
                        "mercadopagoLastPaymentId": payment_id,
                        "subscriptionLastUpdated": firestore.SERVER_TIMESTAMP,
                        "marketing_cota_total": MARKETING_COTA_INICIAL,
                        "marketing_cota_usada": 0, 
                        "marketing_cota_reset_em": new_paid_until
                    })
                elif status_pagamento in ['rejected', 'cancelled', 'refunded']:
                    logging.info(f"Assinatura falhou. Status: '{status_pagamento}' para o salão: {salao_id}")
                    salao_doc_ref.update({
                        "subscriptionStatus": status_pagamento,
                        "subscriptionLastUpdated": firestore.SERVER_TIMESTAMP
                    })
                else:
                    logging.info(f"Webhook de assinatura recebido com status: '{status_pagamento}'. Aguardando.")

            return {"status": "recebido"}
            
        except Exception as e:
            logging.exception(f"Erro ao processar webhook do MP (Payment): {e}")
            return {"status": "erro interno"}

    return {"status": "tipo de evento ignorado"}

# --- ENDPOINTS OAUTH GOOGLE ---
@router.get("/google/auth/start", response_model=dict[str, str])
async def google_auth_start(current_user: dict[str, Any] = Depends(get_current_user)):
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
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
        redirect_uri=GOOGLE_REDIRECT_URI
    )
    authorization_url, state = flow.authorization_url(
        access_type='offline',
        prompt='consent', 
        state=current_user.get("uid") 
    )
    return {"authorization_url": authorization_url}

@callback_router.get("/google/auth/callback")
async def google_auth_callback_handler(
    state: str, 
    code: str, 
    scope: str
):
    logging.info(f"Recebido callback do Google para o state (UID): {state}")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
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
        redirect_uri=GOOGLE_REDIRECT_URI
    )
    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
        refresh_token = credentials.refresh_token
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Falha ao obter o token de atualização do Google...")
        user_uid = state
        clients_ref = db.collection('cabeleireiros')
        query = clients_ref.where(filter=FieldFilter('ownerUID', '==', user_uid)).limit(1) 
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

# --- <<< NOVOS ENDPOINTS: AUTORIZAÇÃO MERCADOPAGO >>> ---
@router.get("/mercadopago/auth/start", response_model=dict[str, str])
async def mercadopago_auth_start(current_user: dict[str, Any] = Depends(get_current_user)):
    """
    Gera a URL de autorização do MercadoPago para o salão logado.
    """
    if not MP_APP_ID:
        raise HTTPException(status_code=500, detail="Integração com MercadoPago (APP_ID) não configurada.")

    # Busca o salao_id do usuário logado
    user_uid = current_user.get("uid")
    try:
        clients_ref = db.collection('cabeleireiros')
        query = clients_ref.where(filter=FieldFilter('ownerUID', '==', user_uid)).limit(1) 
        client_doc_list = list(query.stream()) 
        if not client_doc_list:
            raise HTTPException(status_code=404, detail="Nenhum salão encontrado para este usuário.")
        salao_id = client_doc_list[0].id 
    except Exception as e:
        raise HTTPException(status_code=500, detail="Erro ao buscar ID do salão.")

    auth_url = (
        f"{MP_AUTH_URL}?"
        f"response_type=code&"
        f"client_id={MP_APP_ID}&"
        f"redirect_uri={MP_REDIRECT_URI}&"
        f"state={salao_id}" # Passamos o salao_id no 'state'
    )
    
    logging.info(f"Gerando URL de autorização MP para o salão {salao_id}...")
    return {"authorization_url": auth_url}

@callback_router.get("/mercadopago/callback")
async def mercadopago_auth_callback_handler(
    state: str,  # O salao_id que passamos
    code: str    # O código de autorização temporário
):
    
    """
    Recebe o callback do MercadoPago, troca o 'code' pelo 'access_token'
    e salva as credenciais no documento do salão.
    """
    logging.info(f"Recebido callback do MercadoPago para o state (salao_id): {state}")
    
    if not MP_APP_ID or not MP_SECRET_KEY:
        raise HTTPException(status_code=500, detail="Credenciais da aplicação (Marketplace) não configuradas.")

    salao_id = state
    if salao_id:
        salao_id = salao_id.strip()
    frontend_error_url = f"https://horalis.app/painel/{salao_id}/configuracoes?mp_sync=error"
    
    # 1. Troca o código pelo Access Token
    try:
        token_payload = {
            "client_id": MP_APP_ID,
            "client_secret": MP_SECRET_KEY,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": MP_REDIRECT_URI,
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(MP_TOKEN_URL, data=token_payload)
            response.raise_for_status() 
            
            token_data = response.json()
            
            access_token = token_data.get("access_token")
            refresh_token = token_data.get("refresh_token")
            public_key = token_data.get("public_key")
            mp_user_id = token_data.get("user_id") 

            if not all([access_token, refresh_token, public_key, mp_user_id]):
                logging.error(f"Resposta de token incompleta do MP: {token_data}")
                raise Exception("Resposta de token incompleta do MercadoPago.")

        # 2. Salva as credenciais no Firestore
        salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
        salao_doc = salao_doc_ref.get()

        if not salao_doc.exists:
            logging.error(f"Callback do MP recebido para salão_id ({salao_id}) que não existe.")
            return RedirectResponse(frontend_error_url)
            
        salao_doc_ref.update({
            "mp_access_token": access_token,    
            "mp_refresh_token": refresh_token,  
            "mp_public_key": public_key,      
            "mp_user_id": mp_user_id,
            "mp_sync_enabled": True,          
            "mp_last_updated": firestore.SERVER_TIMESTAMP
        })

        logging.info(f"Credenciais do MercadoPago salvas com sucesso para o salão: {salao_id}")
        
        # 3. Redireciona de volta para a página de configurações no frontend
        frontend_success_url = f"https://horalis.app/painel/{salao_id}/configuracoes?mp_sync=success"
        return RedirectResponse(frontend_success_url)

    except httpx.HTTPStatusError as e:
        logging.error(f"Erro HTTP ao trocar token do MP: {e.response.text}")
        return RedirectResponse(frontend_error_url)
    except Exception as e:
        logging.exception(f"Erro CRÍTICO durante o callback do MercadoPago: {e}")
        return RedirectResponse(frontend_error_url)
# --- <<< FIM DOS ENDPOINTS DO MERCADO PAGO OAUTH >>> ---

@router.get("/user/salao-id", response_model=dict[str, str])
async def get_salao_id_for_user(current_user: dict[str, Any] = Depends(get_current_user)):
    user_uid = current_user.get("uid")
    try:
        clients_ref = db.collection('cabeleireiros')
        query = clients_ref.where(filter=FieldFilter('ownerUID', '==', user_uid)).limit(1) 
        client_doc_list = list(query.stream()) 
        if not client_doc_list:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, 
                                detail="Nenhum salão encontrado para esta conta de usuário.")
        salao_id = client_doc_list[0].id 
        return {"salao_id": salao_id}
    except Exception as e:
        logging.exception(f"Erro ao buscar salão por UID ({user_uid}): {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@router.patch("/clientes/{salao_id}/google-sync", status_code=status.HTTP_200_OK)
async def disconnect_google_sync(
    salao_id: str,
    current_user: dict = Depends(get_current_user)
):
    user_uid = current_user.get("uid") 
    try:
        salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
        salao_doc = salao_doc_ref.get(['ownerUID']) 
        if not salao_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado.")
        salon_owner_uid = salao_doc.get('ownerUID')
        if salon_owner_uid != user_uid:
             raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ação não autorizada.")
        salao_doc_ref.update({
            "google_sync_enabled": False,
            "google_refresh_token": firestore.DELETE_FIELD
        })
        return {"message": "Sincronização com Google Calendar desativada com sucesso."}
    except Exception as e:
        logging.exception(f"Erro ao desativar Google Sync para salão {salao_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")
    
# --- <<< NOVO ENDPOINT: Desconectar Mercado Pago >>> ---
@router.patch("/mercadopago/disconnect/{salao_id}", status_code=status.HTTP_200_OK)
async def disconnect_mercadopago_sync(
    salao_id: str,
    current_user: dict = Depends(get_current_user)
):
    """
    Desconecta a conta do MercadoPago do salão, removendo as credenciais OAuth.
    """
    user_uid = current_user.get("uid")
    logging.info(f"Admin (UID: {user_uid}) solicitou desconexão do MercadoPago para salão: {salao_id}")

    try:
        salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
        salao_doc = salao_doc_ref.get(['ownerUID']) 

        if not salao_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado.")

        salon_owner_uid = salao_doc.get('ownerUID')
        if salon_owner_uid != user_uid:
             raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Ação não autorizada.")

        # Remove todos os campos de credenciais do MP
        salao_doc_ref.update({
            "mp_sync_enabled": False,
            "mp_access_token": firestore.DELETE_FIELD,
            "mp_refresh_token": firestore.DELETE_FIELD,
            "mp_public_key": firestore.DELETE_FIELD,
            "mp_user_id": firestore.DELETE_FIELD,
            "mp_last_updated": firestore.SERVER_TIMESTAMP
        })

        logging.info(f"Credenciais MercadoPago removidas com sucesso para o salão: {salao_id}")
        return {"message": "Conta do MercadoPago desconectada com sucesso."}

    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro ao desconectar MercadoPago para salão {salao_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao desconectar.")
# --- <<< FIM DO NOVO ENDPOINT >>> ---
    
# --- Endpoints CRUD de Clientes ---
@router.get("/clientes", response_model=List[ClientDetail])
async def list_clients(current_user: dict = Depends(get_current_user)):
    clients = get_all_clients_from_db()
    if clients is None: raise HTTPException(status_code=500, detail="Erro ao buscar clientes.")
    return clients

@router.get("/clientes/{client_id}", response_model=ClientDetail)
async def get_client_details(client_id: str, current_user: dict = Depends(get_current_user)):
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
    user_uid = current_user.get("uid")
    client_id = client_data.numero_whatsapp
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if client_ref.get().exists: 
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cliente {client_id} (WhatsApp) já existe.")
        
        data_to_save = client_data.dict() 
        data_to_save['ownerUID'] = user_uid
        data_to_save['subscriptionStatus'] = 'trialing' 
        data_to_save['createdAt'] = firestore.SERVER_TIMESTAMP 
        trial_end_date = datetime.now() + timedelta(days=7)
        data_to_save['trialEndsAt'] = trial_end_date 
        
        client_ref.set(data_to_save)
        logging.info(f"Cliente '{data_to_save['nome_salao']}' (Dono: {user_uid}) criado com ID: {client_id} em modo 'trialing'.")
        
        return ClientDetail(id=client_id, servicos=[], **data_to_save)
    except Exception as e:
        logging.exception(f"Erro ao criar cliente:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao criar cliente.")

@router.put("/clientes/{client_id}", response_model=ClientDetail)
async def update_client(client_id: str, client_update_data: ClientDetail, current_user: dict = Depends(get_current_user)):
    if client_id != client_update_data.id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID URL não corresponde aos dados.")
    
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        
        # 1. Verificação de existência
        if not client_ref.get(retry=None, timeout=None).exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado.")
        
        # 2. CONVERSÃO CRÍTICA: Converter o payload Pydantic COMPLETO para um dicionário puro.
        #    Isso resolve o erro 'Cannot convert to a Firestore Value' para DailySchedule.
        client_info_to_save = client_update_data.model_dump(
            exclude={'servicos', 'id'}, # Exclui campos que não vão para o documento principal
            exclude_unset=True,        # Exclui campos que não foram definidos no payload (evita sobrescrever com None)
            mode='json'                # Garante que submodelos (DailySchedule) sejam serializados como dicts puros
        )
        
        updated_services = client_update_data.servicos
        
        # 3. Lógica de Transação e Salvamento
        @firestore.transactional
        def update_in_transaction(transaction, client_ref, client_info_to_save, services_to_save):
            services_ref = client_ref.collection('servicos')
            old_services_refs = [doc.reference for doc in services_ref.stream(transaction=transaction)]
            
            # Aqui usamos o dicionário client_info_to_save que é serializável pelo Firestore
            transaction.update(client_ref, client_info_to_save)
            
            # Lógica de Atualização de Serviços (mantida)
            for old_ref in old_services_refs: transaction.delete(old_ref)
            for service_data in services_to_save:
                new_service_ref = services_ref.document()
                # O Service é um modelo Pydantic e também precisa ser convertido para set()
                service_dict = service_data.model_dump(exclude={'id'}, exclude_unset=True, exclude_none=True)
                transaction.set(new_service_ref, service_dict)
                
        transaction = db.transaction()
        # Passa o dicionário PURAMENTE serializável
        update_in_transaction(transaction, client_ref, client_info_to_save, updated_services)
        
        logging.info(f"Cliente '{client_update_data.nome_salao}' atualizado.")
        
        # 4. Retorno
        # Assumindo que get_client_details retorna um ClientDetail
        updated_details = await get_client_details(client_id, current_user)
        return updated_details
        
    except Exception as e:
        # Se for um erro de validação Pydantic, ele é capturado antes de chegar aqui,
        # mas mantemos o tratamento de erro genérico do Firestore.
        logging.exception(f"Erro CRÍTICO ao atualizar cliente {client_id}:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao salvar dados.")

# --- Agendamento Manual ---
@router.post("/calendario/agendar", status_code=status.HTTP_201_CREATED)
async def create_manual_appointment(
    manual_data: ManualAppointmentData,
    current_user: dict[str, Any] = Depends(get_current_user)
):
    user_email = current_user.get("email")
    salao_id = manual_data.salao_id 
    customer_email_provided = manual_data.customer_email
    logging.info(f"Admin {user_email} criando agendamento manual para {salao_id}")
    
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "Seu Salão")
        salon_email_destino = salon_data.get('calendar_id')

        start_time_dt = datetime.fromisoformat(manual_data.start_time)
        end_time_dt = start_time_dt + timedelta(minutes=manual_data.duration_minutes)

        if not salon_email_destino:
             logging.warning("E-mail de destino do salão não encontrado. Pulando notificação.")
        
        agendamento_data = {
            "salaoId": salao_id, "salonName": salon_name,
            "serviceName": manual_data.service_name, "durationMinutes": manual_data.duration_minutes,
            "startTime": start_time_dt, "endTime": end_time_dt,
            "customerName": manual_data.customer_name,
            "customerPhone": manual_data.customer_phone or None,
            "customerEmail": customer_email_provided,
            "status": "confirmado", "createdBy": user_email,
            "createdAt": firestore.SERVER_TIMESTAMP,
            "reminderSent": False,
            "serviceId": manual_data.service_id, 
            "servicePrice": manual_data.service_price,
            "clienteId": manual_data.cliente_id or None 
        }

        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento manual criado no Firestore com ID: {agendamento_ref.id}")

        if customer_email_provided and salon_email_destino:
            try:
                email_service.send_confirmation_email_to_salon(
                    salon_email=salon_email_destino, salon_name=salon_name, 
                    customer_name=manual_data.customer_name, client_phone=manual_data.customer_phone, 
                    service_name=manual_data.service_name, start_time_iso=manual_data.start_time
                )
                email_service.send_confirmation_email_to_customer(
                    customer_email=customer_email_provided, customer_name=manual_data.customer_name,
                    service_name=manual_data.service_name, start_time_iso=manual_data.start_time,
                    salon_name=salon_name,
                    salao_id=salao_id
                )
                logging.info(f"E-mails de confirmação disparados com sucesso.")
            except Exception as e:
                logging.error(f"Erro CRÍTICO ao disparar e-mail no agendamento manual: {e}")
        else:
             logging.warning("E-mails de confirmação pulados. Cliente/Salão e-mail ausente.")

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
                else:
                    logging.warning("Falha ao salvar agendamento manual no Google Calendar (função retornou None).")
            except Exception as e:
                logging.exception(f"Erro inesperado ao sync Google (manual): {e}")
        else:
            logging.info("Sincronização Google desativada. Pulando etapa para agendamento manual.")

        return {"message": "Agendamento manual criado com sucesso!", "id": agendamento_ref.id}
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento manual:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

# --- Endpoint de Leitura do Calendário ---
@router.get("/calendario/{salao_id}/eventos", response_model=List[CalendarEvent])
async def get_calendar_events(
    salao_id: str, 
    start: str, 
    end: str,
    current_user: dict = Depends(get_current_user)
):
    admin_email = current_user.get("email")
    logging.info(f"Admin {admin_email} buscando eventos para {salao_id} de {start} a {end}")
    try:
        start_dt_utc = datetime.fromisoformat(start)
        end_dt_utc = datetime.fromisoformat(end)
        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        query = agendamentos_ref.where(filter=FieldFilter("startTime", ">=", start_dt_utc)).where(filter=FieldFilter("startTime", "<=", end_dt_utc))
        
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
                    "customerEmail": data.get('customerEmail'),
                    "serviceName": data.get('serviceName'),
                    "durationMinutes": data.get('durationMinutes'),
                    "googleEventId": data.get("googleEventId") 
                }
            )
            eventos.append(evento_formatado)
        return eventos
    except Exception as e:
        logging.exception(f"Erro ao buscar eventos do calendário para {salao_id}:")
        raise HTTPException(status_code=500, detail="Erro interno ao buscar eventos.")

# --- Endpoint de Cancelar Agendamento ---
@router.delete("/calendario/{salao_id}/agendamentos/{agendamento_id}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_appointment(
    salao_id: str, 
    agendamento_id: str,
    current_user: dict = Depends(get_current_user)
):
    logging.info(f"Admin {current_user.get('email')} cancelando agendamento: {agendamento_id}")
    try:
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
        agendamento_doc = agendamento_ref.get()
        if not agendamento_doc.exists:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
        agendamento_data = agendamento_doc.to_dict()
        google_event_id = agendamento_data.get("googleEventId")
        customer_email = agendamento_data.get("customerEmail")
        customer_name = agendamento_data.get("customerName")
        service_name = agendamento_data.get("serviceName")
        start_time_dt = agendamento_data.get("startTime")
        
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "seu salão")

        if google_event_id:
            refresh_token = salon_data.get("google_refresh_token")
            if refresh_token:
                calendar_service.delete_google_event(refresh_token, google_event_id)
        
        agendamento_ref.delete()
        
        if customer_email and customer_name and service_name and start_time_dt and salon_name:
            try:
                email_service.send_cancellation_email_to_customer(
                    customer_email=customer_email,
                    customer_name=customer_name,
                    service_name=service_name,
                    start_time_iso=start_time_dt.isoformat(),
                    salon_name=salon_name,
                    salao_id=salao_id
                )
            except Exception as e:
                logging.error(f"Falha ao enviar e-mail de CANCELAMENTO (Cliente) para {customer_email}: {e}")
        else:
            logging.warning(f"Pulando e-mail de cancelamento (dados incompletos) para agendamento {agendamento_id}")
        
        return 
    except Exception as e:
        logging.exception(f"Erro ao cancelar agendamento {agendamento_id}:")
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")

# --- Endpoint de Reagendar Agendamento ---
@router.patch("/calendario/{salao_id}/agendamentos/{agendamento_id}")
async def reschedule_appointment(
    salao_id: str, 
    agendamento_id: str,
    body: ReagendamentoBody,
    current_user: dict = Depends(get_current_user)
):
    logging.info(f"Admin {current_user.get('email')} tentando reagendar {agendamento_id} para {body.new_start_time}")
    try:
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
        agendamento_doc = agendamento_ref.get()
        if not agendamento_doc.exists:
            raise HTTPException(status_code=404, detail="Agendamento não encontrado")
        
        agendamento_data = agendamento_doc.to_dict()
        google_event_id = agendamento_data.get("googleEventId")
        duration = agendamento_data.get("durationMinutes")
        customer_email = agendamento_data.get("customerEmail")
        customer_name = agendamento_data.get("customerName")
        service_name = agendamento_data.get("serviceName")
        old_start_time_dt = agendamento_data.get("startTime")
        
        salon_data = get_hairdresser_data_from_db(salao_id)
        salon_name = salon_data.get("nome_salao", "seu salão")

        if not duration or not salon_data or not old_start_time_dt:
             raise HTTPException(status_code=500, detail="Dados do agendamento ou salão estão incompletos.")

        new_start_dt = datetime.fromisoformat(body.new_start_time)
        local_tz = pytz.timezone(calendar_service.LOCAL_TIMEZONE)
        if new_start_dt.tzinfo is None:
             new_start_dt = local_tz.localize(new_start_dt)
        else:
             new_start_dt = new_start_dt.astimezone(local_tz)
        new_end_dt = new_start_dt + timedelta(minutes=duration)
        
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
        
        if google_event_id:
            refresh_token = salon_data.get("google_refresh_token")
            if refresh_token:
                calendar_service.update_google_event(
                    refresh_token, google_event_id, 
                    new_start_dt.isoformat(), new_end_dt.isoformat()
                )

        agendamento_ref.update({
            "startTime": new_start_dt,
            "endTime": new_end_dt
        })
        logging.info(f"Agendamento {agendamento_id} atualizado no Firestore.")

        if customer_email and customer_name and service_name and salon_name:
            try:
                email_service.send_reschedule_email_to_customer(
                    customer_email=customer_email,
                    customer_name=customer_name,
                    service_name=service_name,
                    salon_name=salon_name,
                    old_start_time_iso=old_start_time_dt.isoformat(),
                    new_start_time_iso=new_start_dt.isoformat(),
                    salao_id=salao_id
                )
            except Exception as e:
                logging.error(f"Falha ao enviar e-mail de REAGENDAMENTO (Cliente) para {customer_email}: {e}")
        else:
            logging.warning(f"Pulando e-mail de reagendamento (dados incompletos) para agendamento {agendamento_id}")

        return {"message": "Agendamento reagendado com sucesso."}
    except HTTPException as httpe:
        raise httpe 
    except Exception as e:
        logging.exception(f"Erro ao reagendar agendamento {agendamento_id}:")
        raise HTTPException(status_code=500, detail=f"Erro interno: {e}")

# --- ROTEADOR PÚBLICO PARA O CALLBACK ---
@callback_router.get("/google/auth/callback")
async def google_auth_callback_handler(
    state: str, 
    code: str, 
    scope: str
):
    logging.info(f"Recebido callback do Google para o state (UID): {state}")
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
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
        redirect_uri=GOOGLE_REDIRECT_URI
    )
    try:
        flow.fetch_token(code=code)
        credentials = flow.credentials
        refresh_token = credentials.refresh_token
        if not refresh_token:
            raise HTTPException(status_code=400, detail="Falha ao obter o token de atualização do Google...")
        user_uid = state
        clients_ref = db.collection('cabeleireiros')
        query = clients_ref.where(filter=FieldFilter('ownerUID', '==', user_uid)).limit(1) 
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
    logging.info(f"Polling recebido para verificar Payment ID: {payment_id}")
    try:
        query = db.collection('cabeleireiros').where(
            filter=FieldFilter('mercadopagoLastPaymentId', '==', payment_id)
        ).limit(1)
        client_doc_list = list(query.stream())
        
        if not client_doc_list:
            return {"status": "pending", "message": "Aguardando registro inicial ou pagamento."}
        
        salao_doc = client_doc_list[0]
        current_status = salao_doc.get('subscriptionStatus')

        if current_status == 'active':
            return {"status": "approved", "message": "Pagamento confirmado. Login liberado."}
        elif current_status in ['pending', 'trialing']:
            return {"status": "pending", "message": "Aguardando confirmação do PIX."}
        else:
            return {"status": current_status, "message": "Pagamento não aprovado. Tente novamente."}
    except Exception as e:
        logging.exception(f"Erro no Polling de Pagamento para {payment_id}: {e}")
        return {"status": "pending", "message": "Erro de comunicação. Tente o login em instantes."}

# <<< NOVO ENDPOINT: Polling de Status do Agendamento >>>
@auth_router.get("/check-agendamento-status/{salao_id}/{agendamento_id}", response_model=dict[str, str])
async def check_agendamento_status(salao_id: str, agendamento_id: str):
    """
    Endpoint PÚBLICO de polling para verificar o status de um agendamento específico (PIX/Boleto).
    """
    logging.info(f"Polling recebido para verificar Agendamento ID: {agendamento_id} no Salão {salao_id}")
    
    try:
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document(agendamento_id)
        agendamento_doc = agendamento_ref.get()

        if not agendamento_doc.exists:
            return {"status": "not_found", "message": "Agendamento não encontrado."}
        
        current_status = agendamento_doc.get('status')

        if current_status == 'confirmado':
            return {"status": "approved", "message": "Pagamento confirmado."}
        
        elif current_status == 'pending_payment':
            return {"status": "pending_payment", "message": "Aguardando confirmação do PIX."}
        
        else:
            return {"status": current_status, "message": "Pagamento não aprovado."}

    except Exception as e:
        logging.exception(f"Erro no Polling de Agendamento para {agendamento_id}: {e}")
        return {"status": "error", "message": "Erro de comunicação."}


@router.get("/clientes/{salao_id}/lista-crm", response_model=List[ClienteListItem])
async def list_crm_clients(
    salao_id: str,
    current_user: dict = Depends(get_current_user)
):
    user_email = current_user.get("email")
    logging.info(f"Admin {user_email} solicitou lista CRM para salão: {salao_id}")
    try:
        clientes_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes')
        docs = clientes_ref.stream()
        
        clientes_list = []
        for doc in docs:
            data = doc.to_dict()
            data_cadastro = data.get('data_cadastro')
            ultima_visita = data.get('ultima_visita')
            clientes_list.append(ClienteListItem(
                id=doc.id,
                nome=data.get('nome', 'N/A'),
                email=data.get('email', 'N/A'),
                whatsapp=data.get('whatsapp', 'N/A'),
                data_cadastro=data_cadastro.isoformat() if data_cadastro else None,
                ultima_visita=ultima_visita.isoformat() if ultima_visita else None,
            ))
        
        logging.info(f"Retornando {len(clientes_list)} perfis CRM para o salão {salao_id}.")
        return clientes_list
    except Exception as e:
        logging.exception(f"Erro ao buscar perfis CRM para o salão {salao_id}: {e}")
        if "No document to update" in str(e):
             raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado.")
        
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao buscar clientes.")
    
@router.get("/clientes/{salao_id}/detalhes-crm/{cliente_id}", response_model=ClienteDetailsResponse)
async def get_cliente_details_and_history(
    salao_id: str,
    cliente_id: str,
    current_user: dict = Depends(get_current_user)
):
    user_email = current_user.get("email")
    logging.info(f"Admin {user_email} buscando detalhes e timeline do cliente: {cliente_id}")
    try:
        timeline_items = []
        
        # 1. BUSCA DADOS DO CLIENTE (Mantido)
        cliente_doc_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes').document(cliente_id)
        cliente_doc = cliente_doc_ref.get()

        if not cliente_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perfil do cliente não encontrado.")
        
        cliente_data = cliente_doc.to_dict()

        # ----------------------------------------------------
        # >>> NOVO PASSO: BUSCAR O NOME DO SALÃO <<<
        # ----------------------------------------------------
        salon_doc_ref = db.collection('cabeleireiros').document(salao_id)
        salon_doc = salon_doc_ref.get()
        
        if not salon_doc.exists:
            # Não é um erro crítico, mas precisamos de um nome para o frontend
            salon_name = "Studio Horalis" 
            logging.warning(f"Documento do salão {salao_id} não encontrado para obter o nome.")
        else:
            # Assumindo que o nome do salão está no campo 'nome_salao' do documento raiz
            salon_name = salon_doc.get('nome_salao')
        # ----------------------------------------------------

        # 2. BUSCA HISTÓRICO (Mantido)
        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        # ... (restante da lógica de timeline_items) ...
        
        # 3. CONSTRÓI RESPOSTA
        logging.info(f"Timeline de {len(timeline_items)} itens encontrada para o cliente {cliente_id}.")

        return ClienteDetailsResponse(
            cliente=cliente_data,
            historico_agendamentos=timeline_items,
            # ----------------------------------------------------
            # >>> INCLUSÃO DO NOME DO SALÃO NA RESPOSTA <<<
            salonName=salon_name 
            # ----------------------------------------------------
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
    user_email = current_user.get("email")
    logging.info(f"Admin {user_email} adicionando nota ao cliente {body.cliente_id} no salão {body.salao_id}.")
    try:
        nota_ref = db.collection('cabeleireiros').document(body.salao_id).collection('clientes').document(body.cliente_id).collection('registros').document()
        
        nota_data = {
            "tipo": "NotaManual",
            "data_envio": firestore.SERVER_TIMESTAMP,
            "texto": body.nota_texto,
            "enviado_por": user_email
        }
        
        nota_ref.set(nota_data)
        
        nota_salva = nota_ref.get().to_dict() 
        
        return TimelineItem(
            id=nota_ref.id,
            tipo=nota_salva.get("tipo"),
            data_evento=nota_salva.get("data_envio"),
            dados=nota_salva
        )
    except Exception as e:
        logging.exception(f"Erro ao adicionar nota manual: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao salvar nota.")
    
    
@router.post("/clientes/enviar-promocional", status_code=status.HTTP_200_OK)
async def send_promotional_email_endpoint(
    body: EmailPromocionalBody,
    current_user: dict = Depends(get_current_user)
):
    user_uid = current_user.get("uid")
    logging.info(f"Admin {current_user.get('email')} solicitou envio promocional para o cliente {body.cliente_id}.")
    try:
        cliente_doc_ref = db.collection('cabeleireiros').document(body.salao_id).collection('clientes').document(body.cliente_id)
        cliente_doc = cliente_doc_ref.get()
        if not cliente_doc.exists:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Perfil do cliente não encontrado.")
        cliente_data = cliente_doc.to_dict()
        customer_email = cliente_data.get('email')
        customer_name = cliente_data.get('nome')
        if not customer_email:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="E-mail do cliente ausente no perfil.")
            
        salon_data = get_hairdresser_data_from_db(body.salao_id)
        salon_name = salon_data.get("nome_salao", "Seu Salão")
        
        email_sent = email_service.send_promotional_email_to_customer(
            customer_email=customer_email,
            customer_name=customer_name,
            salon_name=salon_name,
            custom_subject=body.subject,
            custom_message_html=body.message,
            salao_id=body.salao_id
        )
        if not email_sent:
            raise Exception("O serviço de e-mail falhou ao enviar a mensagem.")
        try:
            registro_ref = cliente_doc_ref.collection('registros').document()
            registro_ref.set({
                "tipo": "Promocional",
                "data_envio": firestore.SERVER_TIMESTAMP,
                "assunto": body.subject,
                "enviado_por": current_user.get('email'),
                "message_preview": body.message[:100] + "..."
            })
        except Exception as e:
            logging.error(f"Falha ao registrar envio promocional no CRM: {e}")
        logging.info(f"E-mail promocional enviado e registrado para {customer_email}.")
        return {"message": "E-mail promocional enviado com sucesso!"}
    except HTTPException as httpe: 
        raise httpe
    except Exception as e:
        detail_msg = str(e)
        if "E-mail do cliente ausente" in detail_msg:
             detail_msg = "O perfil do cliente não possui um e-mail cadastrado."
        logging.exception(f"Erro ao enviar e-mail promocional para cliente {body.cliente_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=detail_msg)
    
# (Função _process_chart_data)
def _process_chart_data(snapshot, end_date: datetime, dias: int) -> List[dict[str, Any]]:
    import locale
    try:
        locale.setlocale(locale.LC_TIME, 'pt_BR.UTF-8')
    except locale.Error:
        locale.setlocale(locale.LC_TIME, 'C') 
    
    agendamentos_por_data = {}
    for i in range(dias):
        date = (end_date - timedelta(days=i)).date() 
        date_key = date.isoformat()
        day_name = date.strftime('%a') 
        agendamentos_por_data[date_key] = {
            "name": day_name.capitalize().replace('.', ''),
            "Agendamentos": 0,
            "fullDate": date_key
        }
    for doc in snapshot:
        data = doc.to_dict()
        start_dt: datetime = data['startTime']
        agendamento_date_key = start_dt.date().isoformat()
        if agendamento_date_key in agendamentos_por_data:
            agendamentos_por_data[agendamento_date_key]["Agendamentos"] += 1
    sorted_keys = sorted(agendamentos_por_data.keys())
    return [agendamentos_por_data[key] for key in sorted_keys]

@router.get("/dashboard-data/{salao_id}", response_model=DashboardDataResponse)
async def get_dashboard_data_consolidated(
    salao_id: str,
    agendamentos_foco_periodo: str = Query("hoje"),
    novos_clientes_periodo: str = Query("30dias"),
    agendamentos_grafico_dias: int = Query(7),
    receita_periodo: str = Query("hoje"), 
    current_user: dict = Depends(get_current_user)
):
    logging.info(f"Admin {current_user.get('email')} buscando dados consolidados para {salao_id}.")
    
    try:
        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        clientes_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes')

        now_utc = datetime.now(pytz.utc) 
        hoje_utc = now_utc.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # 1. Novos Clientes (Lógica mantida)
        if novos_clientes_periodo == 'hoje':
             clientes_start = hoje_utc
             clientes_end = hoje_utc + timedelta(days=1)
        elif novos_clientes_periodo == '7dias':
             clientes_start = hoje_utc - timedelta(days=6)
             clientes_end = hoje_utc + timedelta(days=1)
        else: # 30 dias
             clientes_start = hoje_utc - timedelta(days=29)
             clientes_end = hoje_utc + timedelta(days=1)
        
        # 2. Agendamentos em Foco (Lógica mantida)
        if agendamentos_foco_periodo == 'hoje':
            foco_start = hoje_utc
            foco_end = hoje_utc + timedelta(days=1)
        elif agendamentos_foco_periodo == 'prox7dias':
            foco_start = now_utc 
            foco_end = now_utc + timedelta(days=7)
        else: # novos24h
            foco_start = now_utc - timedelta(hours=24)
            foco_end = now_utc
            
        # 3. Receita ESTIMADA
        if receita_periodo == 'mes':
            receita_start = now_utc.replace(day=1).replace(hour=0, minute=0, second=0, microsecond=0)
            # Define o final do mês atual (fim do dia do último dia)
            receita_end = (receita_start + timedelta(days=32)).replace(day=1) 
        elif receita_periodo == 'semana':
             receita_start = hoje_utc
             # Próximos 7 dias (incluindo hoje), terminando ao final do 7º dia
             receita_end = hoje_utc + timedelta(days=7) 
        else: # 'hoje'
             receita_start = hoje_utc
             # O final é o final do dia de hoje (meia-noite do dia seguinte)
             receita_end = hoje_utc + timedelta(days=1) 

        # 4. Gráfico (Lógica mantida)
        chart_start = hoje_utc - timedelta(days=agendamentos_grafico_dias - 1)
        chart_end = hoje_utc + timedelta(days=1) # Apenas para consultas de "até hoje"
        
        
        # --- Queries Firestone (CORRIGIDAS) ---
        novos_clientes_query = clientes_ref.where(filter=FieldFilter('data_cadastro', '>=', clientes_start)).where(filter=FieldFilter('data_cadastro', '<', clientes_end))
        
        foco_query = agendamentos_ref.where(filter=FieldFilter('startTime', '>=', foco_start)).where(filter=FieldFilter('startTime', '<', foco_end)).where(filter=FieldFilter('status', '!=', 'cancelado'))
        
        # CORREÇÃO CRÍTICA: AGORA USA receita_end para limitar a consulta de receita
        receita_query = agendamentos_ref.where(filter=FieldFilter('startTime', '>=', receita_start)).where(filter=FieldFilter('startTime', '<', receita_end)).where(filter=FieldFilter('status', '!=', 'cancelado'))
        
        chart_query = agendamentos_ref.where(filter=FieldFilter('startTime', '>=', chart_start)).where(filter=FieldFilter('startTime', '<', chart_end)).where(filter=FieldFilter('status', '!=', 'cancelado'))

        
        # --- Execução das Consultas (Síncronas) ---
        novos_clientes_snapshot = novos_clientes_query.get()
        foco_snapshot = foco_query.get()
        receita_snapshot = receita_query.get()
        chart_snapshot = chart_query.get()
        
        
        # --- Processamento dos Resultados ---
        count_novos_clientes = len(novos_clientes_snapshot)
        count_agendamentos_foco = len(foco_snapshot)
        
        # O cálculo da receita agora será preciso, pois o snapshot já está filtrado
        total_receita = sum(doc.to_dict().get('servicePrice', 0) for doc in receita_snapshot)
        receita_formatada = f"{total_receita:.2f}".replace('.', ',')
        
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
            chart_data=processed_chart_data
        )

    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro no endpoint consolidado do dashboard: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao carregar dados do dashboard.")

# --- Endpoint de Envio de E-mail em Massa (MODIFICADO COM COTAS) ---
def _process_mass_email_send(salao_id: str, subject: str, message: str, admin_email: str, segmento: str):
    """
    Executa o envio real em uma thread separada.
    AGORA TAMBÉM VERIFICA A COTA.
    """
    logging.info(f"THREAD DE BACKGROUND: Iniciando envio em massa para salão {salao_id} (Segmento: {segmento}).")
    
    try:
        salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
        salon_data = salao_doc_ref.get().to_dict()
        
        if not salon_data:
             logging.error(f"Falha na thread: Salão {salao_id} não encontrado.")
             return

        salon_name = salon_data.get("nome_salao", "Seu Salão")
        
        # --- LÓGICA DE VERIFICAÇÃO DE COTA ---
        now_utc = datetime.now(pytz.utc)
        cota_total = salon_data.get("marketing_cota_total", MARKETING_COTA_INICIAL)
        cota_usada = salon_data.get("marketing_cota_usada", 0)
        cota_reset_em = salon_data.get("marketing_cota_reset_em") 
        
        # 1. Verifica se a cota deve ser resetada
        if cota_reset_em and now_utc > cota_reset_em:
            logging.info(f"Resetando cota de marketing para o salão {salao_id}.")
            cota_usada = 0
            novo_reset = now_utc + timedelta(days=30)
            salao_doc_ref.update({
                "marketing_cota_usada": 0,
                "marketing_cota_reset_em": novo_reset
            })
        
        # 2. Constrói a Query do Segmento
        clientes_ref = salao_doc_ref.collection('clientes')
        query = clientes_ref # Base da query (todos)
        
        if segmento == "inativos":
            inativos_start_date = now_utc - timedelta(days=60)
            query = clientes_ref.where(filter=FieldFilter('ultima_visita', '<=', inativos_start_date))
        
        elif segmento == "recentes":
            recentes_start_date = now_utc - timedelta(days=30)
            query = clientes_ref.where(filter=FieldFilter('ultima_visita', '>=', recentes_start_date))

        # 3. Conta quantos clientes serão enviados
        clientes_snapshot = query.get()
        tamanho_do_envio = len(clientes_snapshot)
        
        if tamanho_do_envio == 0:
             logging.warning(f"Segmento '{segmento}' não encontrou clientes. Nenhum e-mail enviado.")
             return

        # 4. VERIFICA A COTA
        if (cota_usada + tamanho_do_envio) > cota_total:
            logging.error(f"Falha no envio em massa para {salao_id}: Cota excedida. Tentativa: {tamanho_do_envio}, Restante: {cota_total - cota_usada}")
            return
            
        logging.info(f"Cota verificada. Enviando {tamanho_do_envio} e-mails. (Usado: {cota_usada}/{cota_total})")

        # 5. Atualiza a cota USADA
        salao_doc_ref.update({
            "marketing_cota_usada": firestore.Increment(tamanho_do_envio)
        })
        
    except Exception as e:
        logging.exception(f"Erro CRÍTICO na verificação de cota: {e}")
        return 

    # --- 6. Processamento e Envio (Loop) ---
    clientes_enviados = 0
    clientes_falha_email = 0
    EMAIL_DELAY_SECONDS = 0.1 
    import time 
    
    for doc in clientes_snapshot: # Usa o snapshot que já buscamos
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
                    custom_message_html=message,
                    salao_id=salao_id
                )
                if email_sent:
                    clientes_enviados += 1
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
        
        time.sleep(EMAIL_DELAY_SECONDS) 

    logging.info(f"THREAD FINALIZADA. Disparo de marketing em massa para {salao_id}. Enviados: {clientes_enviados}, Falhas: {clientes_falha_email}")


@router.post("/marketing/enviar-massa", status_code=status.HTTP_202_ACCEPTED)
async def send_mass_marketing_email(
    body: MarketingMassaBody,
    background_tasks: BackgroundTasks,
    current_user: dict = Depends(get_current_user)
):
    user_email_admin = current_user.get("email")
    logging.info(f"Admin {user_email_admin} REQUISITOU disparo de marketing em massa.")
    try:
        salon_data = get_hairdresser_data_from_db(body.salao_id)
        if not salon_data:
             raise HTTPException(status_code=404, detail="Salão não encontrado.")
        salon_name = salon_data.get("nome_salao", "Seu Salão")
        
        # <<< VERIFICAÇÃO RÁPIDA DE COTA (ANTES DE INICIAR A TASK) >>>
        now_utc = datetime.now(pytz.utc)
        cota_total = salon_data.get("marketing_cota_total", MARKETING_COTA_INICIAL)
        cota_usada = salon_data.get("marketing_cota_usada", 0)
        cota_reset_em = salon_data.get("marketing_cota_reset_em")

        if cota_reset_em and now_utc > cota_reset_em:
            cota_usada = 0
            
        if cota_usada >= cota_total:
            logging.warning(f"Envio bloqueado para {body.salao_id}. Cota de e-mail (100) já utilizada.")
            raise HTTPException(status_code=403, detail="Limite de cota de e-mail atingido para este mês.")

    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Falha na busca inicial do salão: {e}")
        raise HTTPException(status_code=500, detail="Erro ao verificar dados iniciais do salão.")
        
    try:
        background_tasks.add_task(
            _process_mass_email_send, 
            body.salao_id, 
            body.subject, 
            body.message, 
            user_email_admin,
            body.segmento # <<< Passa o segmento para a task
        )
    except Exception as e:
        logging.error(f"Falha CRÍTICA ao iniciar Background Task: {e}")
        raise HTTPException(status_code=500, detail="O servidor não conseguiu iniciar o processo de envio.")
    
    return {
        "status": "Processamento Aceito",
        "message": f"Disparo de e-mail iniciado em segundo plano para {salon_name}."
    }
    
@router.patch("/configuracoes/pagamento/{salao_id}", status_code=status.HTTP_200_OK)
async def update_payment_settings(
    salao_id: str,
    settings: PagamentoSettingsBody,
    current_user: dict = Depends(get_current_user)
):
    """
    Endpoint seguro para o admin salvar a Chave Pública (fallback) e o Valor do Sinal.
    """
    user_uid = current_user.get("uid")
    logging.info(f"Admin (UID: {user_uid}) atualizando config de pagamento para {salao_id}.")

    try:
        salao_doc_ref = db.collection('cabeleireiros').document(salao_id)
        
        # Opcional: Verificação de segurança se o usuário logado é o dono do salão
        # (Se a verificação for feita em get_hairdresser_data_from_db ou por regras do Firestore, pode ser ignorada aqui)

        update_data = {
            "sinal_valor": settings.sinal_valor
            # Se a chave pública for enviada, ela é atualizada. Se for None, mantemos a OAuth salva.
        }
        
        # Apenas atualiza a chave pública se o admin enviá-la (fluxo manual fallback)
        if settings.mp_public_key:
            update_data["mp_public_key"] = settings.mp_public_key
        
        salao_doc_ref.update(update_data)
        
        logging.info(f"Configurações de pagamento (Sinal: {settings.sinal_valor}) salvas para {salao_id}.")
        return {"message": "Configurações de pagamento salvas com sucesso!"}

    except Exception as e:
        logging.exception(f"Erro ao salvar configurações de pagamento para {salao_id}: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao salvar configurações.")