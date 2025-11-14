# backend/routers/public_routes.py
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
def normalize_phone(phone: str) -> str:
    """Remove tudo que não é dígito para garantir buscas precisas no banco."""
    if not phone: return ""
    return re.sub(r'\D', '', phone)

def is_conflict_with_lunch(
    booking_start_dt: datetime, 
    service_duration_minutes: int, 
    salon_data: Dict[str, Any]
) -> bool:
    """
    Verifica conflito com almoço considerando o FUSO HORÁRIO (America/Sao_Paulo).
    """
    try:
        # 1. Define o Fuso Horário do Salão (Idealmente viria do salon_data, mas fixamos BR por enquanto)
        timezone = pytz.timezone('America/Sao_Paulo')

        # 2. Converte a data do agendamento (que vem em UTC) para o horário local do salão
        if booking_start_dt.tzinfo is None:
            # Se for naive, assume UTC e converte
            booking_local = pytz.utc.localize(booking_start_dt).astimezone(timezone)
        else:
            # Se já tiver fuso, apenas converte
            booking_local = booking_start_dt.astimezone(timezone)

        # 3. Determina o dia da semana baseado no horário LOCAL (Isso corrige bugs de virada de dia)
        day_name = booking_local.strftime('%A').lower() # ex: 'monday', 'tuesday'

        # 4. Busca a configuração do dia
        daily_schedule = salon_data.get('horario_trabalho_detalhado', {}).get(day_name)

        if not daily_schedule:
            return False # Sem agenda configurada, sem conflito de almoço explícito

        # 5. Verifica se tem almoço configurado
        if not daily_schedule.get('hasLunch') or not daily_schedule.get('lunchStart') or not daily_schedule.get('lunchEnd'):
            return False

        # 6. Monta os horários de almoço usando a data LOCAL
        lunch_start_str = daily_schedule['lunchStart']
        lunch_end_str = daily_schedule['lunchEnd']
        
        date_local = booking_local.date()
        
        lunch_start_time = datetime.strptime(lunch_start_str, '%H:%M').time()
        lunch_end_time = datetime.strptime(lunch_end_str, '%H:%M').time()

        # Cria datetimes localizados para o almoço
        lunch_start_dt = timezone.localize(datetime.combine(date_local, lunch_start_time))
        lunch_end_dt = timezone.localize(datetime.combine(date_local, lunch_end_time))

        # 7. Calcula o fim do agendamento
        service_duration = timedelta(minutes=service_duration_minutes)
        booking_end_local = booking_local + service_duration

        # 8. Log para Debug (Isso vai aparecer no seu terminal do backend, ajuda muito!)
        logging.info(f"CHECK ALMOÇO [{day_name}]: Agendamento({booking_local.strftime('%H:%M')} - {booking_end_local.strftime('%H:%M')}) vs Almoço({lunch_start_str} - {lunch_end_str})")

        # 9. Verifica Sobreposição
        # Se (InicioReserva < FimAlmoço) E (FimReserva > InicioAlmoço)
        if (booking_local < lunch_end_dt) and (booking_end_local > lunch_start_dt):
            logging.warning(f"CONFLITO DE ALMOÇO DETECTADO!")
            return True

        return False

    except Exception as e:
        logging.error(f"Erro ao verificar almoço: {e}")
        # Em caso de erro na lógica de verificação, é mais seguro permitir (ou bloquear, dependendo da sua regra)
        # Aqui retornamos False para não travar o agendamento por erro de código, mas logamos o erro.
        return False
# --- Função Utility para o CRM ---
def check_and_update_cliente_profile(salao_id: str, appointment_data) -> str:
    """
    Busca o cliente pelo telefone (chave única mais confiável) ou email.
    Se não existir, CRIA um perfil completo.
    Se existir, ATUALIZA a última visita e estatísticas.
    """
    # Normaliza dados
    phone_raw = appointment_data.customer_phone
    phone_clean = normalize_phone(phone_raw)
    email_clean = appointment_data.customer_email.strip().lower() if appointment_data.customer_email else None
    name_clean = appointment_data.customer_name.strip()

    clientes_ref = db.collection('cabeleireiros').document(salao_id).collection('clientes')
    cliente_doc = None

    # 1. Tenta achar pelo telefone (Prioridade)
    if phone_clean:
        query_phone = clientes_ref.where(filter=FieldFilter("whatsapp", "==", phone_clean)).limit(1).stream()
        cliente_doc = next(query_phone, None)

    # 2. Se não achou e tem email, tenta pelo email
    if not cliente_doc and email_clean:
        query_email = clientes_ref.where(filter=FieldFilter("email", "==", email_clean)).limit(1).stream()
        cliente_doc = next(query_email, None)

    now = firestore.SERVER_TIMESTAMP

    if cliente_doc:
        # --- CLIENTE EXISTENTE: Atualiza ---
        cliente_id = cliente_doc.id
        logging.info(f"CRM: Cliente recorrente identificado ({cliente_id}). Atualizando estatísticas.")
        
        update_data = {"ultima_visita": now}
        # Se o cliente antigo não tinha nome ou email e agora forneceu, atualizamos
        current_data = cliente_doc.to_dict()
        if not current_data.get('email') and email_clean: update_data['email'] = email_clean
        if not current_data.get('nome') and name_clean: update_data['nome'] = name_clean
        
        cliente_doc.reference.update(update_data)
        return cliente_id
    else:
        # --- CLIENTE NOVO: Cria Perfil ---
        logging.info(f"CRM: Novo cliente detectado. Criando perfil para {name_clean}.")
        new_client_data = {
            "nome": name_clean,
            "whatsapp": phone_clean, # Salva sempre limpo
            "email": email_clean,
            "data_cadastro": now,
            "ultima_visita": now,
            "total_gasto": 0.0, # Inicializa métricas (opcional, mas bom para queries rápidas)
            "total_visitas": 0
        }
        new_ref = clientes_ref.document()
        new_ref.set(new_client_data)
        return new_ref.id
# --- FIM DA FUNÇÃO UTILITY ---


# --- Endpoint GET /saloes/{salao_id}/servicos (Sem alterações) ---
# <<< ESTE ENDPOINT ESTÁ CORRETO. A MUDANÇA ESTÁ NO core/models.py >>>
@router.get("/saloes/{salao_id}/servicos", response_model=SalonPublicDetails)
def get_salon_services_and_details(salao_id: str):
    logging.info(f"Buscando detalhes/serviços para: {salao_id}")
    salon_data = get_hairdresser_data_from_db(salao_id) 
    if 'numero_whatsapp' in salon_data:
        salon_data['telefone'] = salon_data.pop('numero_whatsapp')
    if not salon_data:
        raise HTTPException(status_code=404, detail="Salão não encontrado")
    
    status_assinatura = salon_data.get("subscriptionStatus")
    trial_ends_at = salon_data.get("trialEndsAt")
    
    is_active = False
    
    # 1. Verifica se está Ativo (Pago)
    if status_assinatura == "active":
        is_active = True
    
    # 2. Verifica se está em Trial Válido
    elif status_assinatura == "trialing":
        if trial_ends_at:
            # Garante que trial_ends_at seja datetime com timezone
            # O Firestore retorna datetime, mas se vier string ou naive, tratamos:
            if isinstance(trial_ends_at, str):
                trial_ends_at = datetime.fromisoformat(trial_ends_at)
            
            if trial_ends_at.tzinfo is None:
                trial_ends_at = trial_ends_at.replace(tzinfo=pytz.utc)
            
            # Compara com agora (UTC)
            if trial_ends_at > datetime.now(pytz.utc):
                is_active = True

    # 🚫 SE NÃO ESTIVER ATIVO, BLOQUEIA O ACESSO PÚBLICO
    if not is_active:
        logging.warning(f"Acesso público bloqueado para salão {salao_id}. Status: {status_assinatura}")
        raise HTTPException(
            status_code=403, # Forbidden
            detail="Este estabelecimento está temporariamente indisponível."
        )
    
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
    
    # Normalização do telefone para garantir consistência no CRM
    phone_clean = normalize_phone(payload.customer_phone)
    
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

        # Instanciação do Mercado Pago com o token do Salão
        mp_client_do_salao = mercadopago.SDK(salon_access_token)
        mp_client_do_salao_payment = mp_client_do_salao.payment()
        # -----------------------------------------------

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=404, detail="Serviço não selecionado ou inválido.")

        # --- 2. Snapshot dos Dados (CRUCIAL) ---
        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        salon_name = salon_data.get('nome_salao')
        salon_email_destino = salon_data.get('calendar_id') 
        # Garante float para cálculos
        service_price = float(service_info.get('preco', 0.0)) 
        
        # BUSCA VALOR DO SINAL DO DB (SEGURANÇA)
        sinal_valor_backend = float(salon_data.get('sinal_valor', 0.0))
        payload.transaction_amount = sinal_valor_backend # Sobrescreve o valor do payload pelo do banco

        if duration is None or service_name is None:
            raise HTTPException(status_code=500, detail="Dados do serviço incompletos.")
            
        start_time_dt = datetime.fromisoformat(payload.start_time)
        
        # --- 3. VERIFICAÇÃO DE HORÁRIO DISPONÍVEL ---
        is_free = calendar_service.is_slot_available(
            salao_id=salao_id, salon_data=salon_data,
            new_start_dt=start_time_dt, duration_minutes=duration,
            ignore_firestore_id=None, ignore_google_event_id=None
        )
        if not is_free:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este horário não está mais disponível. Por favor, escolha outro.")
            
        # --- 4. VERIFICAÇÃO DE ALMOÇO ---
        if is_conflict_with_lunch(start_time_dt, duration, salon_data):
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="O horário de agendamento conflita com o horário de almoço do salão.")

        # --- 5. Checagem/Criação de Cliente (CRM) ---
        cliente_id = check_and_update_cliente_profile(salao_id, payload)

        # --- 6. SALVAMENTO DO AGENDAMENTO (PENDENTE) ---
        end_time_dt = start_time_dt + timedelta(minutes=duration)
        
        agendamento_data = {
            "salaoId": salao_id,
            "clienteId": cliente_id, # Vínculo CRM
            
            "customerName": payload.customer_name.strip(),
            "customerPhone": phone_clean, # Salva limpo
            "customerEmail": payload.customer_email.strip(),
            
            # Snapshot Financeiro
            "serviceId": service_id,
            "serviceName": service_name,
            "servicePrice": service_price, 
            "sinalValor": sinal_valor_backend,
            "durationMinutes": duration,
            
            "startTime": start_time_dt,
            "endTime": end_time_dt,
            "status": "pending_payment", # Aguardando pagamento
            "createdAt": firestore.SERVER_TIMESTAMP,
            "paymentStatus": "pending",
            "channel": "site"
        }
        
        # O agendamento TEMPORÁRIO é criado aqui
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento 'pending_payment' salvo no Firestore com ID: {agendamento_ref.id}")

        # --- 7. Processar o Pagamento no Mercado Pago ---
        notification_url = f"{RENDER_API_URL}/webhooks/mercado-pago"
        external_reference = f"agendamento__{salao_id}__{agendamento_ref.id}"
        
        payer_identification_data = {
            "type": payload.payer.identification.type, "number": payload.payer.identification.number
        } if payload.payer.identification else None

        # Captura o device ID e configura o header para Antifraude
        device_id_value = getattr(payload, 'device_session_id', None)
        custom_headers = {}
        if device_id_value:
            custom_headers["X-Meli-Session-Id"] = device_id_value
            logging.info(f"Enviando X-Meli-Session-Id: {device_id_value}")
        else:
            logging.warning("Device ID ausente no payload. Risco de fraude aumentado.")

        ro_obj = RequestOptions(custom_headers=custom_headers)

        nome_completo = payload.customer_name.strip().split()
        primeiro_nome = nome_completo[0]
        ultimo_nome = nome_completo[-1] if len(nome_completo) > 1 else primeiro_nome

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
        
        # --- CASO 7.A: PAGAMENTO COM PIX ---
        if payload.payment_method_id == 'pix':
            payment_data = {
                "transaction_amount": payload.transaction_amount, "description": f"Sinal: {service_name}",
                "payment_method_id": "pix",
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, "notification_url": notification_url, 
                "additional_info": additional_info,
                "statement_descriptor": statement_descriptor
            }
            
            payment_response = mp_client_do_salao_payment.create(payment_data, request_options=ro_obj)
            
            if payment_response["status"] not in [200, 201]:
                if agendamento_ref: agendamento_ref.delete()
                raise Exception(f"Erro MP (PIX): {payment_response.get('response', {}).get('message', 'Erro desconhecido')}")

            payment_result = payment_response["response"]
            payment_status = payment_result.get("status")

            if payment_status in ["pending", "in_process"]:
                qr_code_data = payment_result.get("point_of_interaction", {}).get("transaction_data", {})
                agendamento_ref.update({"mercadopagoPaymentId": str(payment_result.get("id"))})
                
                return {
                    "status": "pending_pix",
                    "message": "PIX gerado com sucesso.",
                    "payment_data": {
                        "qr_code": qr_code_data.get("qr_code"),
                        "qr_code_base64": qr_code_data.get("qr_code_base64"),
                        "payment_id": str(payment_result.get("id")),
                        "agendamento_id_ref": agendamento_ref.id 
                    }
                }
            else:
                logging.warning(f"PIX com status inesperado ({payment_status}). Deletando.")
                if agendamento_ref: agendamento_ref.delete()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Falha ao gerar o PIX.")


        # --- CASO 7.B: PAGAMENTO COM CARTÃO ---
        else:
            payment_data = {
                "transaction_amount": payload.transaction_amount, 
                "token": payload.token,
                "description": f"Sinal: {service_name}",
                "installments": payload.installments, 
                "payment_method_id": payload.payment_method_id,
                "issuer_id": payload.issuer_id,
                "payer": { "email": payload.payer.email, "identification": payer_identification_data },
                "external_reference": external_reference, 
                "notification_url": notification_url,
                "additional_info": additional_info,
                "statement_descriptor": statement_descriptor
            }
            
            payment_response = mp_client_do_salao_payment.create(payment_data, request_options=ro_obj)

            if payment_response["status"] not in [200, 201]:
                if agendamento_ref: agendamento_ref.delete()
                error_msg = payment_response.get('response', {}).get('message', 'Erro desconhecido ao processar cartão.')
                raise Exception(f"Erro MP (Cartão): {error_msg}")

            payment_result = payment_response["response"]
            payment_status = payment_result.get("status")
            
            # SE APROVADO NA HORA (Comum em cartão)
            if payment_status == "approved":
                agendamento_ref.update({
                    "status": "confirmado", 
                    "paymentStatus": "paid_signal",
                    "mercadopagoPaymentId": str(payment_result.get("id"))
                })
                
                # Dispara e-mails e Sync Google imediatamente
                try:
                    if salon_email_destino:
                        email_service.send_confirmation_email_to_salon(salon_email_destino, salon_name, payload.customer_name, payload.customer_phone, service_name, payload.start_time)
                    if payload.customer_email:
                        email_service.send_confirmation_email_to_customer(payload.customer_email, payload.customer_name, service_name, payload.start_time, salon_name, salao_id)
                    
                    # Google Calendar
                    if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
                        google_event_data = {
                            "summary": f"{service_name} - {payload.customer_name}",
                            "description": f"Agendamento (Sinal Pago).\nServiço: {service_name}\nSinal: R$ {sinal_valor_backend}",
                            "start_time_iso": start_time_dt.isoformat(),
                            "end_time_iso": end_time_dt.isoformat(),
                        }
                        google_event_id = calendar_service.create_google_event_with_oauth(
                            refresh_token=salon_data.get("google_refresh_token"),
                            event_data=google_event_data
                        )
                        if google_event_id:
                            agendamento_ref.update({"googleEventId": google_event_id})

                except Exception as e:
                    logging.error(f"Sinal pago, mas falha ao processar integrações pós-pagamento: {e}")
                
                return {"status": "approved", "message": "Pagamento aprovado e agendamento confirmado!"}
            
            elif payment_status in ["in_process", "pending", "pending_review_manual"]:
                logging.warning(f"Sinal (Cartão) PENDENTE/EM ANÁLISE ({payment_status}). Deletando agendamento.")
                if agendamento_ref: agendamento_ref.delete()
                error_detail = payment_response["response"].get("status_detail", "Pagamento em análise. Tente outro método.")
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)
            
            else:
                # Rejeitado
                error_detail = payment_response["response"].get("status_detail", "Pagamento rejeitado.")
                if agendamento_ref: agendamento_ref.delete()
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=error_detail)

    except HTTPException as httpe: 
        if agendamento_ref: 
            try: agendamento_ref.delete() 
            except: pass
        raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento com sinal: {e}")
        if agendamento_ref:
            try: agendamento_ref.delete()
            except: pass
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/agendamentos", status_code=status.HTTP_201_CREATED)
async def create_appointment(appointment: Appointment):
    salao_id = appointment.salao_id
    service_id = appointment.service_id
    
    # Normalização do telefone para o CRM
    phone_clean = normalize_phone(appointment.customer_phone)
    
    try:
        # 1. Validações e Busca de Dados
        salon_data = get_hairdresser_data_from_db(salao_id) 
        if not salon_data: raise HTTPException(404, "Salão não encontrado")
        
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(404, "Serviço não selecionado ou inválido.")

        # 2. Snapshot dos Dados (CRUCIAL PARA FINANCEIRO/HISTÓRICO)
        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        # Garante que o preço seja salvo como float para cálculos futuros
        service_price = float(service_info.get('preco', 0.0)) 
        salon_name = salon_data.get('nome_salao')
        salon_email_destino = salon_data.get('calendar_id') 

        if duration is None or service_name is None or not salon_email_destino:
            raise HTTPException(status_code=500, detail="Dados do serviço ou configuração do salão incompletos.")

        # 3. Verificar Disponibilidade e Conflitos
        start_dt = datetime.fromisoformat(appointment.start_time)
        
        # Check de disponibilidade no banco e google (se ativo)
        if not calendar_service.is_slot_available(salao_id, salon_data, start_dt, duration):
            raise HTTPException(status_code=409, detail="Este horário não está mais disponível.")
            
        # Check de horário de almoço
        if is_conflict_with_lunch(start_dt, duration, salon_data):
            raise HTTPException(status_code=409, detail="Conflito com o horário de almoço.")

        # 4. CRM: Vincular ou Criar Cliente
        # Essa função garante que o histórico vá para o lugar certo
        cliente_id = check_and_update_cliente_profile(salao_id, appointment)

        # 5. Salvar Agendamento Completo no Firestore
        end_dt = start_dt + timedelta(minutes=duration)
        
        agendamento_data = {
            "salaoId": salao_id,
            "clienteId": cliente_id, # Vínculo CRM
            "customerName": appointment.customer_name.strip(),
            "customerPhone": phone_clean, # Salva limpo para busca fácil
            "customerEmail": appointment.customer_email.strip(),
            
            # Snapshot Financeiro (Congela o preço no momento da venda)
            "serviceId": service_id,
            "serviceName": service_name,
            "servicePrice": service_price, 
            "durationMinutes": duration,
            
            # Dados de Agenda
            "startTime": start_dt,
            "endTime": end_dt,
            "status": "confirmado",
            "createdAt": firestore.SERVER_TIMESTAMP,
            "paymentStatus": "na_loja", # Pagamento será feito no local
            "channel": "site" # Origem do agendamento
        }
        
        # Cria o documento
        ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        ref.set(agendamento_data)
        logging.info(f"Agendamento criado com ID: {ref.id}")

        # 6. Disparo de E-mails (Notificações)
        try:
            # Notifica o Dono do Salão
            email_service.send_confirmation_email_to_salon(
                salon_email=salon_email_destino, 
                salon_name=salon_name, 
                customer_name=appointment.customer_name, 
                client_phone=appointment.customer_phone, 
                service_name=service_name, 
                start_time_iso=appointment.start_time
            )
            # Notifica o Cliente Final
            if appointment.customer_email:
                email_service.send_confirmation_email_to_customer(
                    customer_email=appointment.customer_email, 
                    customer_name=appointment.customer_name, 
                    service_name=service_name, 
                    start_time_iso=appointment.start_time, 
                    salon_name=salon_name, 
                    salao_id=salao_id
                )
        except Exception as e:
            logging.error(f"Erro ao enviar e-mails de confirmação: {e}")
            # Não paramos o fluxo se o e-mail falhar, pois o agendamento já existe

        # 7. Sincronização com Google Calendar
        if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
            try:
                google_event_data = {
                    "summary": f"{service_name} - {appointment.customer_name}",
                    "description": f"Agendamento via Horalis.\nTel: {appointment.customer_phone}\nServiço: {service_name}\nPreço: R$ {service_price}",
                    "start_time_iso": start_dt.isoformat(),
                    "end_time_iso": end_dt.isoformat(),
                }
                
                google_event_id = calendar_service.create_google_event_with_oauth(
                    refresh_token=salon_data.get("google_refresh_token"),
                    event_data=google_event_data
                )
                
                if google_event_id:
                    ref.update({"googleEventId": google_event_id})
                    logging.info(f"Sincronizado com Google Calendar. ID: {google_event_id}")
            
            except Exception as e:
                logging.error(f"Falha na sincronização com Google Calendar: {e}")

        return {"message": "Agendamento confirmado com sucesso!", "id": ref.id}

    except HTTPException as he:
        raise he
    except Exception as e:
        logging.error(f"Erro crítico em create_appointment: {e}")
        raise HTTPException(status_code=500, detail="Erro interno ao processar agendamento.")