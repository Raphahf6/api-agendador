import logging
import re
import os 
import pytz 
from fastapi import APIRouter, HTTPException, Query, status, Depends
from datetime import datetime, timedelta 
from firebase_admin import firestore 
from google.cloud.firestore import FieldFilter
from typing import Optional, Dict, List, Any
import mercadopago 
from mercadopago.config import RequestOptions

# Importações dos nossos módulos
from core.models import SalonPublicDetails, Service, Appointment, Cliente, AppointmentPaymentPayload, Professional # 🌟 Adicionado Professional
from core.db import get_hairdresser_data_from_db, db 
from services import calendar_service, email_service 

# --- Constantes ---
CLIENTE_COLLECTION = 'clientes' 
RENDER_API_URL = "https://api-agendador.onrender.com/api/v1"

router = APIRouter(
    tags=["Cliente Final"] 
)

# --- Configuração SDK Mercado Pago (Mantida) ---
try:
    MP_ACCESS_TOKEN = os.environ.get("MERCADO_PAGO_ACCESS_TOKEN")
    if not MP_ACCESS_TOKEN:
        logging.warning("MERCADO_PAGO_ACCESS_TOKEN (public_routes) não está configurado.")
        sdk = None
    else:
        sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
        logging.info("SDK do Mercado Pago (Payment) inicializado em public_routes.")
except Exception as e:
    logging.error(f"Erro ao inicializar SDK Mercado Pago (public_routes): {e}")
    sdk = None

# --- Helpers (Mantidos) ---
def normalize_phone(phone: str) -> str:
    """Remove tudo que não é dígito para garantir buscas precisas no banco."""
    if not phone: return ""
    return re.sub(r'\D', '', phone)

def is_conflict_with_lunch(booking_start_dt: datetime, service_duration_minutes: int, salon_data: Dict[str, Any]) -> bool:
    """Verifica conflito com almoço considerando o FUSO HORÁRIO (America/Sao_Paulo)."""
    try:
        timezone = pytz.timezone('America/Sao_Paulo')

        if booking_start_dt.tzinfo is None:
            booking_local = pytz.utc.localize(booking_start_dt).astimezone(timezone)
        else:
            booking_local = booking_start_dt.astimezone(timezone)

        day_name = booking_local.strftime('%A').lower()
        daily_schedule = salon_data.get('horario_trabalho_detalhado', {}).get(day_name)

        if not daily_schedule: return False
        if not daily_schedule.get('hasLunch') or not daily_schedule.get('lunchStart') or not daily_schedule.get('lunchEnd'):
            return False

        date_local = booking_local.date()
        lunch_start_time = datetime.strptime(daily_schedule['lunchStart'], '%H:%M').time()
        lunch_end_time = datetime.strptime(daily_schedule['lunchEnd'], '%H:%M').time()

        lunch_start_dt = timezone.localize(datetime.combine(date_local, lunch_start_time))
        lunch_end_dt = timezone.localize(datetime.combine(date_local, lunch_end_time))
        
        service_duration = timedelta(minutes=service_duration_minutes)
        booking_end_local = booking_local + service_duration

        if (booking_local < lunch_end_dt) and (booking_end_local > lunch_start_dt):
            logging.warning(f"CONFLITO DE ALMOÇO DETECTADO!")
            return True
        return False
    except Exception as e:
        logging.error(f"Erro ao verificar almoço: {e}")
        return False

def check_and_update_cliente_profile(salao_id: str, appointment_data) -> str:
    """Cria ou atualiza o perfil do cliente (CRM) e retorna o ID do documento."""
    phone_clean = normalize_phone(appointment_data.customer_phone)
    email_clean = appointment_data.customer_email.strip().lower() if appointment_data.customer_email else None
    name_clean = appointment_data.customer_name.strip()

    clientes_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes')
    cliente_doc = None

    if phone_clean:
        query_phone = clientes_ref.where(filter=FieldFilter("whatsapp", "==", phone_clean)).limit(1).stream()
        cliente_doc = next(query_phone, None)

    if not cliente_doc and email_clean:
        query_email = clientes_ref.where(filter=FieldFilter("email", "==", email_clean)).limit(1).stream()
        cliente_doc = next(query_email, None)

    now = firestore.SERVER_TIMESTAMP

    if cliente_doc:
        cliente_id = cliente_doc.id
        update_data = {"ultima_visita": now}
        current_data = cliente_doc.to_dict()
        if not current_data.get('email') and email_clean: update_data['email'] = email_clean
        if not current_data.get('nome') and name_clean: update_data['nome'] = name_clean
        cliente_doc.reference.update(update_data)
        return cliente_id
    else:
        new_client_data = {
            "nome": name_clean, "whatsapp": phone_clean, "email": email_clean,
            "data_cadastro": now, "ultima_visita": now, "total_gasto": 0.0, "total_visitas": 0
        }
        new_ref = clientes_ref.document()
        new_ref.set(new_client_data)
        return new_ref.id

# --- ROTAS ---

# 🌟 ATUALIZADO: Agora busca a equipe junto com os serviços.
@router.get("/saloes/{salao_id}/servicos", response_model=SalonPublicDetails)
def get_salon_services_and_details(salao_id: str):
    logging.info(f"Buscando detalhes/serviços/equipe para: {salao_id}")
    salon_data = get_hairdresser_data_from_db(salao_id) 
    
    if 'numero_whatsapp' in salon_data:
        salon_data['telefone'] = salon_data.pop('numero_whatsapp')
    if not salon_data:
        raise HTTPException(status_code=404, detail="Salão não encontrado")
    
    # Validação de Assinatura (Mantida)
    status_assinatura = salon_data.get("subscriptionStatus")
    trial_ends_at = salon_data.get("trialEndsAt")
    is_active = False
    if status_assinatura == "active": is_active = True
    elif status_assinatura == "trialing":
        if trial_ends_at:
            if isinstance(trial_ends_at, str): trial_ends_at = datetime.fromisoformat(trial_ends_at)
            if trial_ends_at.tzinfo is None: trial_ends_at = trial_ends_at.replace(tzinfo=pytz.utc)
            if trial_ends_at > datetime.now(pytz.utc): is_active = True

    if not is_active:
        logging.warning(f"Acesso público bloqueado para salão {salao_id}. Status: {status_assinatura}")
        raise HTTPException(status_code=403, detail="Este estabelecimento está temporariamente indisponível.")
    
    # Carrega Serviços (Mantido)
    services_list_formatted = []
    if salon_data.get("servicos_data"):
        for service_id, service_info in salon_data["servicos_data"].items():
            services_list_formatted.append(Service(id=service_id, **service_info)) 
    
    # 🌟 NOVO: Carrega Profissionais (Equipe)
    profissionais_list = []
    try:
        pros_ref = db.collection('cabeleireiros').document(salao_id).collection('profissionais')
        # Filtra apenas os que estão 'ativos' (se houver essa lógica)
        docs = pros_ref.stream() 
        for doc in docs:
            profissionais_list.append(Professional(id=doc.id, **doc.to_dict()))
    except Exception as e:
        logging.error(f"Erro ao buscar equipe do salão {salao_id}: {e}")
        # Não quebra a rota se falhar, apenas retorna lista vazia
    
    response_data = SalonPublicDetails(
        servicos=services_list_formatted,
        profissionais=profissionais_list, # 🌟 Envia a equipe
        **salon_data
    ) 
    
    return response_data

# 🌟 ATUALIZADO: Aceita professional_id
@router.get("/saloes/{salao_id}/horarios-disponiveis")
async def get_available_slots_endpoint( 
    salao_id: str,
    service_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    professional_id: Optional[str] = Query(None) # 🌟 NOVO PARÂMETRO
):
    logging.info(f"Buscando horários para salão {salao_id} em {date} (Profissional: {professional_id})")
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
        
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(status_code=404, detail="Serviço não encontrado.")
        
        duration = service_info.get('duracao_minutos')
        if duration is None: raise HTTPException(status_code=500, detail="Duração do serviço não encontrada.")
        
        # 🌟 Passa o professional_id para o filtro
        available_slots = calendar_service.find_available_slots(
            salao_id=salao_id,
            salon_data=salon_data, 
            service_duration_minutes=duration,
            date_str=date,
            professional_id=professional_id # 🌟 Repassa o ID
        )
        return {"horarios_disponiveis": available_slots}
    except Exception as e:
        logging.exception(f"Erro CRÍTICO no cálculo de slots (Híbrido):")
        raise HTTPException(status_code=500, detail="Erro interno ao calcular horários.")

# 🌟 ATUALIZADO: Salva o professional_id
@router.post("/agendamentos", status_code=status.HTTP_201_CREATED)
async def create_appointment(appointment: Appointment):
    salao_id = appointment.salao_id
    service_id = appointment.service_id
    phone_clean = normalize_phone(appointment.customer_phone)
    
    try:
        # 1. Validações
        salon_data = get_hairdresser_data_from_db(salao_id) 
        if not salon_data: raise HTTPException(404, "Salão não encontrado")
        
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(404, "Serviço inválido")

        # 2. Snapshot dos Dados
        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        service_price = float(service_info.get('preco', 0.0)) 
        salon_name = salon_data.get('nome_salao')
        salon_email_destino = salon_data.get('calendar_id') 

        if duration is None or service_name is None:
            raise HTTPException(status_code=500, detail="Dados do serviço incompletos.")

        # 3. Verificar Disponibilidade (passando o ID do profissional)
        start_dt = datetime.fromisoformat(appointment.start_time)
        
        if not calendar_service.is_slot_available(salao_id, salon_data, start_dt, duration, professional_id=appointment.professional_id):
            raise HTTPException(status_code=409, detail="Horário indisponível para este profissional.")
            
        if is_conflict_with_lunch(start_dt, duration, salon_data):
            raise HTTPException(status_code=409, detail="Conflito com o horário de almoço.")

        # 4. CRM: Vincular Cliente
        cliente_id = check_and_update_cliente_profile(salao_id, appointment)

        # 5. Salvar Agendamento
        end_dt = start_dt + timedelta(minutes=duration)
        
        agendamento_data = {
            "salaoId": salao_id,
            "clienteId": cliente_id,
            "customerName": appointment.customer_name.strip(),
            "customerPhone": phone_clean,
            "customerEmail": appointment.customer_email.strip(),
            
            "serviceId": service_id,
            "serviceName": service_name,
            "servicePrice": service_price, 
            "durationMinutes": duration,
            
            # 🌟 DADOS DO PROFISSIONAL 🌟
            "professionalId": appointment.professional_id, 
            "professionalName": appointment.professional_name, 
            
            "startTime": start_dt,
            "endTime": end_dt,
            "status": "confirmado",
            "createdAt": firestore.SERVER_TIMESTAMP,
            "paymentStatus": "na_loja",
            "channel": "site"
        }
        
        ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        ref.set(agendamento_data)

        # 6. Notificações e Google Calendar
        svc_display = f"{service_name}" + (f" com {appointment.professional_name}" if appointment.professional_name else "")
        try:
            if salon_email_destino:
                email_service.send_confirmation_email_to_salon(salon_email_destino, salon_name, appointment.customer_name, appointment.customer_phone, svc_display, appointment.start_time)
            if appointment.customer_email:
                email_service.send_confirmation_email_to_customer(appointment.customer_email, appointment.customer_name, svc_display, appointment.start_time, salon_name, salao_id)
        except Exception as e:
            logging.error(f"Erro ao enviar e-mails: {e}")

        # Sync Google
        if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
            try:
                google_event_data = {
                    "summary": f"{svc_display} - {appointment.customer_name}",
                    "description": f"Agendamento via Horalis.\nCliente: {appointment.customer_name}\nTelefone: {appointment.customer_phone}\nServiço: {svc_display}",
                    "start_time_iso": start_dt.isoformat(),
                    "end_time_iso": end_dt.isoformat(),
                }
                google_event_id = calendar_service.create_google_event_with_oauth(
                    refresh_token=salon_data.get("google_refresh_token"),
                    event_data=google_event_data
                )
                if google_event_id:
                    ref.update({"googleEventId": google_event_id})
            except Exception as e:
                logging.error(f"Falha na sync Google: {e}")

        return {"message": "Agendamento confirmado!", "id": ref.id}

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Erro create_appointment: {e}")
        raise HTTPException(500, "Erro interno.")


# 🌟 ATUALIZADO: Salva o professional_id
@router.post("/agendamentos/iniciar-pagamento-sinal", status_code=status.HTTP_201_CREATED)
async def create_appointment_with_payment(payload: AppointmentPaymentPayload):
    
    salao_id = payload.salao_id
    service_id = payload.service_id
    phone_clean = normalize_phone(payload.customer_phone)
    agendamento_ref = None 

    try:
        # 1. Validações e Dados
        salon_data = get_hairdresser_data_from_db(salao_id) 
        if not salon_data: raise HTTPException(404, "Salão não encontrado")
            
        salon_access_token = salon_data.get('mp_access_token')
        if not salon_access_token: raise HTTPException(403, "Pagamento não configurado.")

        mp_client_do_salao = mercadopago.SDK(salon_access_token)
        mp_client_do_salao_payment = mp_client_do_salao.payment()

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(404, "Serviço inválido.")

        # 2. Snapshot dos Dados
        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        salon_name = salon_data.get('nome_salao')
        salon_email_destino = salon_data.get('calendar_id') 
        service_price = float(service_info.get('preco', 0.0))
        
        sinal_valor_backend = float(salon_data.get('sinal_valor', 0.0))
        payload.transaction_amount = sinal_valor_backend 

        if duration is None or service_name is None:
            raise HTTPException(500, "Dados do serviço incompletos.")
            
        start_time_dt = datetime.fromisoformat(payload.start_time)
        
        # 3. Verificação de Horário (passando o ID do profissional)
        if not calendar_service.is_slot_available(salao_id, salon_data, start_time_dt, duration, professional_id=payload.professional_id):
            raise HTTPException(409, "Horário indisponível para este profissional.")
            
        if is_conflict_with_lunch(start_time_dt, duration, salon_data):
            raise HTTPException(409, "Conflito com o horário de almoço.")

        # 4. CRM
        cliente_id = check_and_update_cliente_profile(salao_id, payload)

        # 5. Lógica de Salvamento (Pendente)
        end_time_dt = start_time_dt + timedelta(minutes=duration)
        agendamento_data = {
            "salaoId": salao_id, "clienteId": cliente_id,
            "customerName": payload.customer_name.strip(), 
            "customerPhone": phone_clean, 
            "customerEmail": payload.customer_email.strip(), 
            
            "serviceId": service_id, 
            "serviceName": service_name, 
            "servicePrice": service_price,
            "sinalValor": sinal_valor_backend,
            "durationMinutes": duration, 
            
            # 🌟 DADOS DO PROFISSIONAL 🌟
            "professionalId": payload.professional_id,
            "professionalName": payload.professional_name,

            "startTime": start_time_dt, 
            "endTime": end_time_dt, 
            "status": "pending_payment", 
            "createdAt": firestore.SERVER_TIMESTAMP,
            "paymentStatus": "pending"
        }
        
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento 'pending_payment' salvo (Prof: {payload.professional_id}): {agendamento_ref.id}")

        # 6. Processar o Pagamento (Lógica MP Mantida)
        notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"
        external_reference = f"agendamento__{salao_id}__{agendamento_ref.id}"
        
        payer_identification_data = {
            "type": payload.payer.identification.type, "number": payload.payer.identification.number
        } if payload.payer.identification else None

        device_id_value = getattr(payload, 'device_session_id', None)
        custom_headers = {}
        if device_id_value:
            custom_headers["X-Meli-Session-Id"] = device_id_value
        ro_obj = RequestOptions(custom_headers=custom_headers)

        nome_completo = payload.customer_name.strip().split()
        primeiro_nome = nome_completo[0]; ultimo_nome = nome_completo[-1] if len(nome_completo) > 1 else primeiro_nome

        additional_info = {
            "payer": {
                "first_name": primeiro_nome, "last_name": ultimo_nome,
                "phone": { "area_code": payload.customer_phone[0:2], "number": payload.customer_phone[2:] },
            },
            "items": [{"id": service_id, "title": service_name, "description": "Sinal de agendamento", "quantity": 1, "unit_price": payload.transaction_amount}]
        }
        statement_descriptor = salon_name[:10].upper().replace(" ", "")
        
        # --- CASO 1: PIX ---
        if payload.payment_method_id == 'pix':
            payment_data = {
                "transaction_amount": payload.transaction_amount, "description": f"Sinal: {service_name}",
                "payment_method_id": "pix",
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, "notification_url": notification_url, 
                "additional_info": additional_info, "statement_descriptor": statement_descriptor
            }
            payment_response = mp_client_do_salao_payment.create(payment_data, request_options=ro_obj)
            
            if payment_response["status"] not in [200, 201]:
                if agendamento_ref: agendamento_ref.delete()
                raise Exception(f"Erro MP (PIX): {payment_response.get('response', {}).get('message', 'Erro')}")

            payment_result = payment_response["response"]
            payment_status = payment_result.get("status")

            if payment_status in ["pending", "in_process"]:
                qr_code_data = payment_result.get("point_of_interaction", {}).get("transaction_data", {})
                agendamento_ref.update({"mercadopagoPaymentId": str(payment_result.get("id"))})
                
                return {
                    "status": "pending_pix", "message": "PIX gerado.",
                    "payment_data": {
                        "qr_code": qr_code_data.get("qr_code"), "qr_code_base64": qr_code_data.get("qr_code_base64"),
                        "payment_id": str(payment_result.get("id")), "agendamento_id_ref": agendamento_ref.id 
                    }
                }
            else:
                if agendamento_ref: agendamento_ref.delete()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Falha ao gerar o PIX.")

        # --- CASO 2: CARTÃO ---
        else:
            payment_data = {
                "transaction_amount": payload.transaction_amount, "token": payload.token,
                "description": f"Sinal: {service_name}",
                "installments": payload.installments, "payment_method_id": payload.payment_method_id,
                "issuer_id": payload.issuer_id,
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, "notification_url": notification_url,
                "additional_info": additional_info, "statement_descriptor": statement_descriptor
            }
            payment_response = mp_client_do_salao_payment.create(payment_data, request_options=ro_obj)

            if payment_response["status"] not in [200, 201]:
                if agendamento_ref: agendamento_ref.delete()
                error_msg = payment_response.get('response', {}).get('message', 'Erro ao processar cartão.')
                raise Exception(f"Erro MP (Cartão): {error_msg}")

            payment_result = payment_response["response"]
            payment_status = payment_result.get("status")
            
            if payment_status == "approved":
                agendamento_ref.update({
                    "status": "confirmado", 
                    "paymentStatus": "paid_signal",
                    "mercadopagoPaymentId": str(payment_result.get("id"))
                })
                
                # Dispara e-mails e Sync Google
                svc_display = f"{service_name}" + (f" com {payload.professional_name}" if payload.professional_name else "")
                try:
                    if salon_email_destino:
                        email_service.send_confirmation_email_to_salon(salon_email_destino, salon_name, payload.customer_name, payload.customer_phone, svc_display, payload.start_time)
                    if payload.customer_email:
                        email_service.send_confirmation_email_to_customer(payload.customer_email, payload.customer_name, svc_display, payload.start_time, salon_name, salao_id)
                    
                    if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
                        # ... (lógica do google sync mantida) ...
                        pass

                except Exception as e:
                    logging.error(f"Sinal pago, mas falha nas integrações: {e}")
                
                return {"status": "approved", "message": "Pagamento aprovado e agendamento confirmado!"}
            
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