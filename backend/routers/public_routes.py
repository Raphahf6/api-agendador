# backend/routers/public_routes.py
import logging
import re
from fastapi import APIRouter, HTTPException, Query, status, Depends
from datetime import datetime, timedelta 
from firebase_admin import firestore 
from typing import Optional, Dict

# Importações dos seus módulos
from core.models import SalonPublicDetails, Service, Appointment, Cliente
from core.db import get_hairdresser_data_from_db, db 
import calendar_service
import email_service

# --- Constantes ---
CLIENTE_COLLECTION = 'clientes'

router = APIRouter(
    tags=["Cliente Final"] 
)

# --- FUNÇÃO UTILITY: Checa e Cria/Atualiza o Cliente CRM ---
def check_and_update_cliente_profile(
    salao_id: str, 
    appointment_data: Appointment
) -> Optional[str]:
    """
    Verifica se o cliente já existe pelo e-mail ou WhatsApp. 
    Se não, cria um novo perfil.
    Retorna o ID do cliente (existente ou recém-criado).
    """
    
    cliente_email = appointment_data.customer_email.strip()
    cliente_whatsapp = appointment_data.customer_phone
    
    # Busca na subcoleção 'clientes' dentro do documento do salão
    clientes_subcollection = db.collection('cabeleireiros').document(salao_id).collection('clientes')

    # 1. Busca pelo E-mail (Prioridade)
    query_email = clientes_subcollection.where("email", "==", cliente_email).limit(1).stream()
    cliente_doc = next(query_email, None)

    # 2. Se não achou por email, busca por WhatsApp
    if not cliente_doc:
        query_whatsapp = clientes_subcollection.where("whatsapp", "==", cliente_whatsapp).limit(1).stream()
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

# --- Endpoint POST /agendamentos (MODIFICADO) ---
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
        # --- 0. Checagem de Cliente (NOVA LÓGICA CRM) ---
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
            "clienteId": cliente_id # NOVO CAMPO: Liga ao perfil CRM
        }
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento salvo no Firestore com ID: {agendamento_ref.id}")

        # 4. Disparo do E-MAIL (SALÃO e CLIENTE)
        try:
            email_service.send_confirmation_email_to_salon(
                salon_email=salon_email_destino, salon_name=salon_name, 
                customer_name=user_name, client_phone=user_phone, 
                service_name=service_name, start_time_iso=start_time_str
            )
            email_service.send_confirmation_email_to_customer(
                customer_email=user_email, customer_name=user_name,
                service_name=service_name, start_time_iso=start_time_str,
                salon_name=salon_name
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