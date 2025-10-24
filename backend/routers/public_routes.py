# backend/routers/public_routes.py
import logging
import re
from fastapi import APIRouter, HTTPException, Query, status
from datetime import datetime, timedelta
from firebase_admin import firestore

# Importações dos nossos módulos
from core.models import SalonPublicDetails, Service, Appointment
from core.db import get_hairdresser_data_from_db, db
import calendar_service
# --- ADIÇÃO CRÍTICA PARA E-MAIL ---
from email_service import send_confirmation_email_to_salon  # <<< NOVO IMPORT
# --- FIM DA ADIÇÃO CRÍTICA ---

router = APIRouter(
    tags=["Cliente Final"]
)


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


# --- Endpoint GET /saloes/{salao_id}/horarios-disponiveis (MODIFICADO) ---
@router.get("/saloes/{salao_id}/horarios-disponiveis")
async def get_available_slots_endpoint(
    salao_id: str,
    service_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
):
    """Busca horários disponíveis (Lendo do FIRESTORE - Agenda Própria)."""
    logging.info(f"Buscando horários (Firestore) para salão {salao_id} em {date}")
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data:
            raise HTTPException(status_code=404, detail="Salão não encontrado")

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=404, detail="Serviço não encontrado.")

        duration = service_info.get("duracao_minutos")
        if duration is None:
            raise HTTPException(status_code=500, detail="Duração do serviço não encontrada.")

        available_slots = calendar_service.find_available_slots(
            salao_id=salao_id,
            service_duration_minutes=duration,
            work_days=salon_data.get("dias_trabalho", []),
            start_hour_str=salon_data.get("horario_inicio", "09:00"),
            end_hour_str=salon_data.get("horario_fim", "18:00"),
            date_str=date,
        )

        return {"horarios_disponiveis": available_slots}
    except HTTPException:
        # re-raise known HTTP exceptions
        raise
    except Exception:
        logging.exception("Erro CRÍTICO no cálculo de slots (Firestore):")
        raise HTTPException(status_code=500, detail="Erro interno ao calcular horários.")


# --- Endpoint POST /agendamentos (MODIFICADO para salvar no FIRESTORE E ENVIAR E-MAIL) ---
@router.post("/agendamentos", status_code=status.HTTP_201_CREATED)
async def create_appointment(appointment: Appointment):
    """Cria um novo agendamento (público) e SALVA NO FIRESTORE e envia e-mail."""
    salao_id = appointment.salao_id
    service_id = appointment.service_id
    start_time_str = appointment.start_time
    user_name = appointment.customer_name.strip()
    user_phone = appointment.customer_phone
    logging.info(f"Cliente '{user_name}' criando agendamento (Firestore) para {salao_id}")

    try:
        # 1. Validações e Busca de Dados
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data:
            raise HTTPException(status_code=404, detail="Salão não encontrado")

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=404, detail="Serviço não encontrado.")

        duration = service_info.get("duracao_minutos")
        service_name = service_info.get("nome_servico")
        salon_name = salon_data.get("nome_salao")

        # ATENÇÃO: Precisamos do email do salão. Vamos assumir que o email do salão está no campo 'calendar_id' (como placeholder)
        salon_email_destino = salon_data.get("calendar_id")

        if duration is None or service_name is None or not salon_email_destino:
            raise HTTPException(status_code=500, detail="Dados do serviço ou email de destino incompletos.")

        # 2. Validação do telefone
        cleaned_phone = re.sub(r"\D", "", user_phone)
        if not (10 <= len(cleaned_phone) <= 11):
            raise HTTPException(status_code=400, detail="Formato de telefone inválido.")

        # 3. LÓGICA DE SALVAMENTO NO FIRESTORE (Executada primeiro)
        start_time_dt = datetime.fromisoformat(start_time_str)
        end_time_dt = start_time_dt + timedelta(minutes=duration)

        agendamento_data = {
            "salaoId": salao_id,
            "serviceId": service_id,
            "serviceName": service_name,
            "durationMinutes": duration,
            "startTime": start_time_dt,
            "endTime": end_time_dt,
            "customerName": user_name,
            "customerPhone": user_phone,
            "status": "confirmado",
            "createdAt": firestore.SERVER_TIMESTAMP,
        }

        agendamento_ref = db.collection("cabeleireiros").document(salao_id).collection("agendamentos").document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento salvo no Firestore com ID: {agendamento_ref.id}")

        # 4. DISPARO DO E-MAIL (Executado de forma síncrona aqui; adaptar para async se necessário)
        try:
            email_success = send_confirmation_email_to_salon(
                salon_email=salon_email_destino,
                salon_name=salon_name,
                customer_name=user_name,
                service_name=service_name,
                client_phone=user_phone,
                start_time_iso=start_time_str,
            )
            if email_success:
                logging.info("E-mail de confirmação via Resend disparado com sucesso.")
            else:
                logging.warning("Falha ao disparar e-mail de confirmação Resend.")
        except Exception:
            logging.exception("Falha ao enviar e-mail de confirmação; agendamento já salvo.")

        return {"message": f"Agendamento para '{service_name}' criado com sucesso!"}

    except HTTPException:
        raise
    except Exception:
        logging.exception("Erro CRÍTICO ao criar agendamento no Firestore e enviar e-mail:")
        raise HTTPException(status_code=500, detail="Erro interno ao criar agendamento.")