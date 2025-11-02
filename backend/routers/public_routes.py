# backend/routers/public_routes.py
import logging
import re
import os # <<< ADICIONADO
import pytz # <<< ADICIONADO
from fastapi import APIRouter, HTTPException, Query, status, Depends
from datetime import datetime, timedelta 
from firebase_admin import firestore 
from google.cloud.firestore import FieldFilter
from typing import Optional, Dict, List
import mercadopago # <<< ADICIONADO

# Importações dos nossos módulos
# <<< ADICIONADO AppointmentPaymentPayload >>>
from core.models import SalonPublicDetails, Service, Appointment, Cliente, AppointmentPaymentPayload
from core.db import get_hairdresser_data_from_db, db 
from services import calendar_service, email_service
# --- Constantes ---
CLIENTE_COLLECTION = 'clientes' 
RENDER_API_URL = "https://api-agendador.onrender.com/api/v1" # (Necessário para o Webhook)

router = APIRouter(
    tags=["Cliente Final"] 
)

# --- <<< NOVO: Configuração SDK Mercado Pago (Duplicado do admin_routes) >>> ---
try:
    MP_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
    if not MP_ACCESS_TOKEN:
        logging.warning("MERCADO_PAGO_ACCESS_TOKEN (public_routes) não está configurado.")
        sdk = None
        mp_payment_client = None
    else:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        mp_payment_client = sdk.payment() # Só precisamos do cliente de pagamento aqui
        logging.info("SDK do Mercado Pago (Payment) inicializado em public_routes.")
except Exception as e:
    logging.error(f"Erro ao inicializar SDK Mercado Pago (public_routes): {e}")
    sdk = None
    mp_payment_client = None
# --- <<< FIM DA ADIÇÃO >>> ---


# --- Função Utility para o CRM ---
def check_and_update_cliente_profile(
    salao_id: str, 
    # Usamos o payload que contém os dados do cliente
    appointment_data: (Appointment | AppointmentPaymentPayload) 
) -> Optional[str]:
    """
    Verifica se o cliente já existe pelo e-mail ou WhatsApp. 
    Se não, cria um novo perfil.
    Retorna o ID do cliente (existente ou recém-criado).
    """
    
    cliente_email = appointment_data.customer_email.strip()
    cliente_whatsapp = appointment_data.customer_phone
    
    clientes_subcollection = db.collection('cabeleireiros').document(salao_id).collection('clientes')

    # 1. Busca pelo E-mail (Prioridade)
    query_email = clientes_subcollection.where(filter=FieldFilter("email", "==", cliente_email)).limit(1).stream()
    cliente_doc = next(query_email, None)

    # 2. Se não achou por email, busca por WhatsApp
    if not cliente_doc:
        query_whatsapp = clientes_subcollection.where(filter=FieldFilter("whatsapp", "==", cliente_whatsapp)).limit(1).stream()
        cliente_doc = next(query_whatsapp, None)

    
    # --- Cliente Encontrado: Atualiza a última visita ---
    if cliente_doc:
        cliente_id = cliente_doc.id
        logging.info(f"Cliente existente encontrado (ID: {cliente_id}). Atualizando visita.")
        try:
            cliente_doc.reference.update({
                "ultima_visita": firestore.SERVER_TIMESTAMP
            })
            return cliente_id
        except Exception as e:
            logging.error(f"Falha ao atualizar última visita do cliente {cliente_id}: {e}")
            return cliente_id

    
    # --- Cliente NÃO Encontrado: Cria um novo perfil ---
    else:
        try:
            logging.info(f"Cliente novo. Criando perfil CRM para {cliente_email}.")
            novo_cliente_data = {
                "profissional_id": salao_id,
                "nome": appointment_data.customer_name.strip(),
                "email": cliente_email,
                "whatsapp": cliente_whatsapp,
                "data_cadastro": firestore.SERVER_TIMESTAMP,
                "ultima_visita": firestore.SERVER_TIMESTAMP,
            }
            
            novo_cliente_ref = clientes_subcollection.document()
            novo_cliente_ref.set(novo_cliente_data)
            
            logging.info(f"Novo perfil de cliente CRM criado: {novo_cliente_ref.id}")
            return novo_cliente_ref.id

        except Exception as e:
            logging.error(f"Falha CRÍTICA ao criar novo perfil de cliente: {e}")
            return None
# --- FIM DA FUNÇÃO UTILITY ---


# --- Endpoint GET /saloes/{salao_id}/servicos (Sem alterações) ---
@router.get("/saloes/{salao_id}/servicos", response_model=SalonPublicDetails)
def get_salon_services_and_details(salao_id: str):
    logging.info(f"Buscando detalhes/serviços para: {salao_id}")
    salon_data = get_hairdresser_data_from_db(salao_id) 
    if not salon_data:
        raise HTTPException(status_code=404, detail="Salão não encontrado")
    services_list_formatted = []
    if salon_data.get("servicos_data"):
        for service_id, service_info in salon_data["servicos_data"].items():
            services_list_formatted.append(Service(id=service_id, **service_info)) 
    response_data = SalonPublicDetails(servicos=services_list_formatted, **salon_data) 
    return response_data

# --- Endpoint GET /saloes/{salao_id}/horarios-disponiveis (Sem alterações) ---
@router.get("/saloes/{salao_id}/horarios-disponiveis")
async def get_available_slots_endpoint( 
    salao_id: str,
    service_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    logging.info(f"Buscando horários (Híbrido) para salão {salao_id} em {date}")
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(status_code=404, detail="Serviço não encontrado.")
        duration = service_info.get('duracao_minutos')
        if duration is None: raise HTTPException(status_code=500, detail="Duração do serviço não encontrada.")
        available_slots = calendar_service.find_available_slots(
            salao_id=salao_id,
            salon_data=salon_data, 
            service_duration_minutes=duration,
            date_str=date
        )
        return {"horarios_disponiveis": available_slots}
    except Exception as e:
        logging.exception(f"Erro CRÍTICO no cálculo de slots (Híbrido):")
        raise HTTPException(status_code=500, detail="Erro interno ao calcular horários.")

# --- Endpoint POST /agendamentos (SUBSTITUÍDO) ---
@router.post("/agendamentos/iniciar-pagamento-sinal", status_code=status.HTTP_201_CREATED)
async def create_appointment_with_payment(payload: AppointmentPaymentPayload):
    """
    1. Valida o horário.
    2. Cria o Cliente (CRM).
    3. Cria o Agendamento como "pending_payment".
    4. Processa o pagamento (Cartão) ou retorna dados (PIX).
    """
    
    salao_id = payload.salao_id
    service_id = payload.service_id
    logging.info(f"Cliente '{payload.customer_name}' iniciando pagamento de sinal para salão {salao_id}")

    if not mp_payment_client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Serviço de pagamento indisponível.")

    agendamento_ref = None # Inicializa para o bloco finally

    try:
        # --- 1. Validações e Busca de Dados ---
        salon_data = get_hairdresser_data_from_db(salao_id) 
        if not salon_data: 
            raise HTTPException(status_code=404, detail="Salão não encontrado")
            
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=404, detail="Serviço não selecionado ou inválido.")

        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        salon_name = salon_data.get('nome_salao')
        service_price = service_info.get('preco')
        salon_email_destino = salon_data.get('calendar_id') 

        if duration is None or service_name is None:
            raise HTTPException(status_code=500, detail="Dados do serviço incompletos.")
            
        start_time_dt = datetime.fromisoformat(payload.start_time)
        
        # --- 2. VERIFICAÇÃO DE HORÁRIO DISPONÍVEL (CRÍTICO) ---
        is_free = calendar_service.is_slot_available(
            salao_id=salao_id, 
            salon_data=salon_data,
            new_start_dt=start_time_dt, 
            duration_minutes=duration,
            ignore_firestore_id=None,
            ignore_google_event_id=None
        )
        if not is_free:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Este horário não está mais disponível. Por favor, escolha outro."
            )

        # --- 3. Checagem/Criação de Cliente (CRM) ---
        cliente_id = check_and_update_cliente_profile(salao_id, payload)
        logging.info(f"Agendamento associado ao cliente_id: {cliente_id or 'N/A'}")

        # --- 4. LÓGICA DE SALVAMENTO (PENDENTE) ---
        end_time_dt = start_time_dt + timedelta(minutes=duration)
        agendamento_data = {
            "salaoId": salao_id,
            "serviceId": service_id,
            "serviceName": service_name,
            "salonName": salon_name,
            "customerName": payload.customer_name.strip(),
            "customerEmail": payload.customer_email.strip(), 
            "customerPhone": payload.customer_phone,
            "startTime": start_time_dt, 
            "endTime": end_time_dt, 
            "durationMinutes": duration, 
            "servicePrice": service_price,
            "status": "pending_payment", # <<< STATUS PENDENTE
            "createdAt": firestore.SERVER_TIMESTAMP,
            "reminderSent": False,
            "clienteId": cliente_id 
        }
        
        # Cria o documento de agendamento PENDENTE
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento 'pending_payment' salvo no Firestore com ID: {agendamento_ref.id}")

        # --- 5. Processar o Pagamento ---
        notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"
        
        # <<< CHAVE: Referência composta para o webhook saber o que atualizar >>>
        external_reference = f"agendamento__{salao_id}__{agendamento_ref.id}"
        
        payer_identification_data = {
            "type": payload.payer.identification.type,
            "number": payload.payer.identification.number
        } if payload.payer.identification else None

        # --- CASO 1: PAGAMENTO COM PIX (ou Boleto) ---
        if payload.payment_method_id == 'pix':
            payment_data = {
                "transaction_amount": payload.transaction_amount,
                "description": f"Sinal de agendamento: {service_name}",
                "payment_method_id": "pix",
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, 
                "notification_url": notification_url, 
            }
            payment_response = mp_payment_client.create(payment_data)
            
            if payment_response["status"] not in [200, 201]:
                raise Exception(f"Erro MP (PIX): {payment_response.get('response').get('message', 'Erro desconhecido')}")

            payment_result = payment_response["response"]
            qr_code_data = payment_result.get("point_of_interaction", {}).get("transaction_data", {})
            
            agendamento_ref.update({"mercadopagoPaymentId": payment_result.get("id")})
            
            return {
                "status": "pending_pix",
                "message": "PIX gerado. Aguardando pagamento.",
                "payment_data": {
                    "qr_code": qr_code_data.get("qr_code"),
                    "qr_code_base64": qr_code_data.get("qr_code_base64"),
                    "payment_id": payment_result.get("id")
                }
            }
        
        # --- CASO 2: PAGAMENTO COM CARTÃO (Aprovação imediata) ---
        else:
            payment_data = {
                "transaction_amount": payload.transaction_amount,
                "token": payload.token,
                "description": f"Sinal de agendamento: {service_name}",
                "installments": payload.installments,
                "payment_method_id": payload.payment_method_id,
                "issuer_id": payload.issuer_id,
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, 
                "notification_url": notification_url, 
            }
            payment_response = mp_payment_client.create(payment_data)

            if payment_response["status"] not in [200, 201]:
                error_msg = payment_response.get('response', {}).get('message', 'Erro desconhecido ao processar o cartão.')
                raise Exception(f"Erro MP (Cartão): {error_msg}")

            payment_status = payment_response["response"].get("status")
            
            if payment_status == "approved":
                logging.info(f"Sinal (Cartão) APROVADO instantaneamente para agendamento {agendamento_ref.id}.")
                
                agendamento_ref.update({
                    "status": "confirmado",
                    "mercadopagoPaymentId": payment_response["response"].get("id")
                })
                
                # Dispara e-mails (pois o webhook não será chamado)
                try:
                    if salon_email_destino:
                        email_service.send_confirmation_email_to_salon(
                            salon_email=salon_email_destino, salon_name=salon_name, 
                            customer_name=payload.customer_name, client_phone=payload.customer_phone, 
                            service_name=service_name, start_time_iso=payload.start_time
                        )
                    if payload.customer_email:
                        email_service.send_confirmation_email_to_customer(
                            customer_email=payload.customer_email, customer_name=payload.customer_name,
                            service_name=service_name, start_time_iso=payload.start_time,
                            salon_name=salon_name, salao_id=salao_id
                        )
                except Exception as e:
                    logging.error(f"Sinal pago, mas falha ao enviar e-mail: {e}")
                
                return {"status": "approved", "message": "Pagamento aprovado e agendamento confirmado!"}
            
            else:
                error_detail = payment_response["response"].get("status_detail", "Pagamento rejeitado.")
                if agendamento_ref: agendamento_ref.delete()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    except HTTPException as httpe: 
        if agendamento_ref: agendamento_ref.delete() # Rollback em caso de 409 (conflito)
        raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento com sinal: {e}")
        if agendamento_ref:
            try: agendamento_ref.delete()
            except Exception: pass
        raise HTTPException(status_code=500, detail="Erro interno ao processar o agendamento.")