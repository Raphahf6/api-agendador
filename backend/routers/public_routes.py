# backend/routers/public_routes.py
import logging
import re
import os 
import pytz 
from fastapi import APIRouter, HTTPException, Query, status, Depends
from datetime import datetime, timedelta 
from firebase_admin import firestore 
from google.cloud.firestore import FieldFilter
from typing import Optional, Dict, List
import mercadopago 
from mercadopago.config import RequestOptions

# Importações dos nossos módulos
# <<< CORREÇÃO: Garante que estamos importando TODOS os modelos necessários de core.models >>>
from core.models import SalonPublicDetails, Service, Appointment, Cliente, AppointmentPaymentPayload
from core.db import get_hairdresser_data_from_db, db 
# <<< MUDANÇA: Mudei a importação dos seus serviços para um diretório 'services' >>>
# (Se seus arquivos calendar_service e email_service estiverem na raiz, mude esta linha)
from services import calendar_service, email_service 

# --- Constantes ---
CLIENTE_COLLECTION = 'clientes' 
RENDER_API_URL = "https://api-agendador.onrender.com/api/v1"

router = APIRouter(
    tags=["Cliente Final"] 
)

# --- Configuração SDK Mercado Pago ---
try:
    MP_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
    if not MP_ACCESS_TOKEN:
        logging.warning("MERCADO_PAGO_ACCESS_TOKEN (public_routes) não está configurado.")
        sdk = None
        mp_payment_client = None
    else:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        mp_payment_client = sdk.payment()
        logging.info("SDK do Mercado Pago (Payment) inicializado em public_routes.")
except Exception as e:
    logging.error(f"Erro ao inicializar SDK Mercado Pago (public_routes): {e}")
    sdk = None
    mp_payment_client = None
# --- FIM DA ADIÇÃO ---


# --- Função Utility para o CRM ---
def check_and_update_cliente_profile(
    salao_id: str, 
    appointment_data: (Appointment | AppointmentPaymentPayload) 
) -> Optional[str]:
    
    cliente_email = appointment_data.customer_email.strip()
    cliente_whatsapp = appointment_data.customer_phone
    
    clientes_subcollection = db.collection('cabeleireiros').document(salao_id).collection('clientes')

    query_email = clientes_subcollection.where(filter=FieldFilter("email", "==", cliente_email)).limit(1).stream()
    cliente_doc = next(query_email, None)

    if not cliente_doc:
        query_whatsapp = clientes_subcollection.where(filter=FieldFilter("whatsapp", "==", cliente_whatsapp)).limit(1).stream()
        cliente_doc = next(query_whatsapp, None)

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
# <<< ESTE ENDPOINT ESTÁ CORRETO. A MUDANÇA ESTÁ NO core/models.py >>>
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
    
    # Esta linha automaticamente inclui 'mp_public_key' e 'sinal_valor'
    # porque 'SalonPublicDetails' (em core/models.py) agora os possui.
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

# --- Endpoint POST /agendamentos/iniciar-pagamento-sinal (MODIFICADO) ---

@router.post("/agendamentos/iniciar-pagamento-sinal", status_code=status.HTTP_201_CREATED)
async def create_appointment_with_payment(payload: AppointmentPaymentPayload):
    
    salao_id = payload.salao_id
    service_id = payload.service_id
    logging.info(f"Cliente '{payload.customer_name}' iniciando pagamento de sinal para salão {salao_id}")

    agendamento_ref = None 

    try:
        # --- 1. Validações e Busca de Dados ---
        salon_data = get_hairdresser_data_from_db(salao_id) 
        if not salon_data: 
            raise HTTPException(status_code=404, detail="Salão não encontrado")
            
        salon_access_token = salon_data.get('mp_access_token')
        
        if not salon_access_token:
            logging.error(f"Salão {salao_id} tentou pagamento, mas mp_access_token não está configurado.")
            raise HTTPException(status_code=403, detail="O pagamento online não está configurado para este salão.")

        # Cria a instância do MercadoPago com o TOKEN DO SALÃO
        mp_client_do_salao = mercadopago.SDK(salon_access_token)
        mp_client_do_salao_payment = mp_client_do_salao.payment()

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=404, detail="Serviço não selecionado ou inválido.")

        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        salon_name = salon_data.get('nome_salao')
        salon_email_destino = salon_data.get('calendar_id') 
        service_price = service_info.get('preco') # Correção do erro anterior
        
        # BUSCA VALOR DO SINAL DO DB (SEGURANÇA)
        sinal_valor_backend = salon_data.get('sinal_valor', 0.0)
        payload.transaction_amount = sinal_valor_backend # Usa o valor do backend

        if duration is None or service_name is None:
            raise HTTPException(status_code=500, detail="Dados do serviço incompletos.")
            
        start_time_dt = datetime.fromisoformat(payload.start_time)
        
        # --- 2. VERIFICAÇÃO DE HORÁRIO DISPONÍVEL ---
        is_free = calendar_service.is_slot_available(
            salao_id=salao_id, salon_data=salon_data,
            new_start_dt=start_time_dt, duration_minutes=duration,
            ignore_firestore_id=None, ignore_google_event_id=None
        )
        if not is_free:
            agendamento_ref = None # Garante que não deletaremos nada em caso de conflito
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este horário não está mais disponível. Por favor, escolha outro.")

        # --- 3. Checagem/Criação de Cliente (CRM) ---
        cliente_id = check_and_update_cliente_profile(salao_id, payload)

        # --- 4. LÓGICA DE SALVAMENTO (PENDENTE) ---
        end_time_dt = start_time_dt + timedelta(minutes=duration)
        agendamento_data = {
            "salaoId": salao_id, "serviceId": service_id,
            "serviceName": service_name, "salonName": salon_name,
            "customerName": payload.customer_name.strip(), "customerEmail": payload.customer_email.strip(), 
            "customerPhone": payload.customer_phone, "startTime": start_time_dt, 
            "endTime": end_time_dt, "durationMinutes": duration, "servicePrice": service_price,
            "status": "pending_payment", "createdAt": firestore.SERVER_TIMESTAMP,
            "reminderSent": False, "clienteId": cliente_id 
        }
        
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento 'pending_payment' salvo no Firestore com ID: {agendamento_ref.id}")

        # --- 5. Processar o Pagamento ---
        notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"
        external_reference = f"agendamento__{salao_id}__{agendamento_ref.id}"
        
        payer_identification_data = {
            "type": payload.payer.identification.type, "number": payload.payer.identification.number
        } if payload.payer.identification else None

        ro_obj = RequestOptions(custom_headers={ "X-Meli-Session-Id": payload.device_id })
        
        nome_completo = payload.customer_name.strip().split()
        primeiro_nome = nome_completo[0]; ultimo_nome = nome_completo[-1] if len(nome_completo) > 1 else primeiro_nome

        additional_info = {
            "payer": {
                "first_name": primeiro_nome, "last_name": ultimo_nome,
                "phone": { "area_code": payload.customer_phone[0:2], "number": payload.customer_phone[2:] },
            },
            "items": [
                {
                    "id": service_id, "title": service_name,
                    "description": "Sinal de agendamento de serviço",
                    "quantity": 1, "unit_price": payload.transaction_amount,
                    "category_id": "services"
                }
            ]
        }
        statement_descriptor = salon_name[:10].upper().replace(" ", "")
        
        # --- CASO 1: PAGAMENTO COM PIX ---
        if payload.payment_method_id == 'pix':
            payment_data = {
                "transaction_amount": payload.transaction_amount, "description": f"Sinal de agendamento: {service_name}",
                "payment_method_id": "pix",
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, "notification_url": notification_url, 
                "additional_info": additional_info,
                "statement_descriptor": statement_descriptor
            }
            # <<< CHAVE: Usa a instância do salão >>>
            payment_response = mp_client_do_salao_payment.create(payment_data, request_options=ro_obj)
            
            if payment_response["status"] not in [200, 201]:
                raise Exception(f"Erro MP (PIX): {payment_response.get('response').get('message', 'Erro desconhecido')}")

            payment_result = payment_response["response"]
            qr_code_data = payment_result.get("point_of_interaction", {}).get("transaction_data", {})
            agendamento_ref.update({"mercadopagoPaymentId": payment_result.get("id")})
            
            return {
                "status": "pending_pix", "message": "PIX gerado. Aguardando pagamento.",
                "payment_data": {
                    "qr_code": qr_code_data.get("qr_code"), "qr_code_base64": qr_code_data.get("qr_code_base64"),
                    "payment_id": payment_result.get("id"), "agendamento_id_ref": agendamento_ref.id 
                }
            }
        
        # --- CASO 2: PAGAMENTO COM CARTÃO ---
        else:
            payment_data = {
                "transaction_amount": payload.transaction_amount, "token": payload.token,
                "description": f"Sinal de agendamento: {service_name}",
                "installments": payload.installments, "payment_method_id": payload.payment_method_id,
                "issuer_id": payload.issuer_id,
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, "notification_url": notification_url,
                "additional_info": additional_info,
                "statement_descriptor": statement_descriptor
            }
            # <<< CHAVE: Usa a instância do salão >>>
            payment_response = mp_client_do_salao_payment.create(payment_data, request_options=ro_obj)

            if payment_response["status"] not in [200, 201]:
                error_msg = payment_response.get('response', {}).get('message', 'Erro desconhecido ao processar o cartão.')
                raise Exception(f"Erro MP (Cartão): {error_msg}")

            payment_status = payment_response["response"].get("status")
            
            if payment_status == "approved":
                agendamento_ref.update({"status": "confirmado", "mercadopagoPaymentId": payment_response["response"].get("id")})
                
                # Dispara e-mails (apenas se for aprovado)
                try:
                    if salon_email_destino:
                        email_service.send_confirmation_email_to_salon(salon_email=salon_email_destino, salon_name=salon_name, customer_name=payload.customer_name, client_phone=payload.customer_phone, service_name=service_name, start_time_iso=payload.start_time)
                    if payload.customer_email:
                        email_service.send_confirmation_email_to_customer(customer_email=payload.customer_email, customer_name=payload.customer_name, service_name=service_name, start_time_iso=payload.start_time, salon_name=salon_name, salao_id=salao_id)
                except Exception as e:
                    logging.error(f"Sinal pago, mas falha ao enviar e-mail: {e}")
                
                return {"status": "approved", "message": "Pagamento aprovado e agendamento confirmado!"}
            
            elif payment_status in ["in_process", "pending", "pending_review_manual"]:
                logging.info(f"Sinal (Cartão) PENDENTE ou EM REVISÃO ({payment_status}). Agendamento {agendamento_ref.id} aguardando webhook.")
                agendamento_ref.update({"status": "pending_payment", "mercadopagoPaymentId": payment_response["response"].get("id")})
                return {"status": "pending_review", "message": "Seu pagamento está em análise pelo MercadoPago. Você receberá a confirmação por e-mail."}
            
            else:
                error_detail = payment_response["response"].get("status_detail", "Pagamento rejeitado.")
                if agendamento_ref: agendamento_ref.delete()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    except HTTPException as httpe: 
        if agendamento_ref: agendamento_ref.delete()
        raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento com sinal: {e}")
        if agendamento_ref:
            try: agendamento_ref.delete()
            except Exception: pass
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/agendamentos", status_code=status.HTTP_201_CREATED)
async def create_appointment(appointment: Appointment):
    """
    1. Checa/Cria o perfil do Cliente CRM.
    2. Cria um novo agendamento, SALVA NO FIRESTORE (com cliente_id), envia e-mails e sincroniza Google Calendar.
    """
        
    salao_id = appointment.salao_id
    service_id = appointment.service_id
    start_time_str = appointment.start_time
    user_name = appointment.customer_name.strip()
    user_phone = appointment.customer_phone
    user_email = appointment.customer_email.strip()
    
    logging.info(f"Cliente '{user_name}' ({user_email}) criando agendamento para {salao_id}")
    
    try:
        # --- 0. Checagem de Cliente (CRM) ---
        cliente_id = check_and_update_cliente_profile(salao_id, appointment)
        logging.info(f"Agendamento associado ao cliente_id: {cliente_id or 'N/A'}")
        
        # 1. Validações e Busca de Dados
        salon_data = get_hairdresser_data_from_db(salao_id) 
        if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=404, detail="Serviço não selecionado ou inválido.")
        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        salon_name = salon_data.get('nome_salao')
        service_price = service_info.get('preco')
        salon_email_destino = salon_data.get('calendar_id') 
        service_price = service_info.get('preco')
        if duration is None or service_name is None or not salon_email_destino:
            raise HTTPException(status_code=500, detail="Dados do serviço ou e-mail de destino incompletos.")

        # 2. Validação do telefone
        cleaned_phone = re.sub(r'\D', '', user_phone)
        if not (10 <= len(cleaned_phone) <= 11):
            raise HTTPException(status_code=400, detail="Formato de telefone inválido.")

        # 3. LÓGICA DE SALVAMENTO NO FIRESTORE
        start_time_dt = datetime.fromisoformat(start_time_str)
        end_time_dt = start_time_dt + timedelta(minutes=duration)
        agendamento_data = {
            "salaoId": salao_id,
            "serviceId": appointment.service_id,
            "serviceName": service_name,
            "salonName": salon_name,
            "customerName": user_name,
            "customerEmail": user_email, 
            "customerPhone": user_phone,
            "startTime": start_time_dt, 
            "endTime": end_time_dt, 
            "durationMinutes": duration, 
            "servicePrice": service_price,
            "status": "confirmado", 
            "createdAt": firestore.SERVER_TIMESTAMP,
            "reminderSent": False,
            "clienteId": cliente_id # Linka ao perfil CRM
        }
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento salvo no Firestore com ID: {agendamento_ref.id}")

        # 4. DISPARO DO E-MAIL (SALÃO e CLIENTE)
        try:
            email_service.send_confirmation_email_to_salon(
                salon_email=salon_email_destino, salon_name=salon_name, 
                customer_name=user_name, client_phone=user_phone, 
                service_name=service_name, start_time_iso=start_time_str
            )
            # <<< CORREÇÃO AQUI: Passando o salao_id >>>
            email_service.send_confirmation_email_to_customer(
                customer_email=user_email, customer_name=user_name,
                service_name=service_name, start_time_iso=start_time_str,
                salon_name=salon_name,
                salao_id=salao_id # Passa o ID para o link "Agendar Novamente"
            )
        except Exception as e:
            logging.error(f"Erro CRÍTICO ao disparar e-mail: {e}")

        # 5. LÓGICA DE ESCRITA HÍBRIDA (Google Calendar)
        google_event_data = {
            "summary": f"{service_name} - {user_name}",
            "description": f"Agendamento via Horalis.\nCliente: {user_name}\nTelefone: {user_phone}\nServiço: {service_name}",
            "start_time_iso": start_time_dt.isoformat(),
            "end_time_iso": end_time_dt.isoformat(),
        }
        if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
            logging.info(f"Sincronização Google Ativa para {salao_id}. Tentando salvar no Google Calendar.")
            try:
                google_event_id = calendar_service.create_google_event_with_oauth(
                    refresh_token=salon_data.get("google_refresh_token"),
                    event_data=google_event_data
                )
                if google_event_id:
                    logging.info(f"Agendamento salvo com sucesso no Google Calendar (ID: {google_event_id}).")
                    agendamento_ref.update({"googleEventId": google_event_id})
                else:
                    logging.warning("Falha ao salvar no Google Calendar (OAuth) (função retornou None).")
            except Exception as e:
                logging.error(f"Erro inesperado ao tentar salvar no Google Calendar: {e}")
        else:
            logging.info(f"Sincronização Google desativada ou refresh_token ausente para {salao_id}. Pulando etapa de escrita no Google.")

        # 6. Retorna a resposta ao cliente final
        return {"message": f"Agendamento para '{service_name}' criado com sucesso!"}

    except HTTPException as httpe: 
        raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento (Híbrido):")
        raise HTTPException(status_code=500, detail="Erro interno ao criar agendamento.")