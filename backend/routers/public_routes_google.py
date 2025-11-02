# backend/routers/public_routes.py
import logging
import re
from fastapi import APIRouter, HTTPException, Query, status, Depends
from typing import List, Dict, Any, Optional

# Importações relativas da nossa nova estrutura
import backend.services.calendar_service as calendar_service
from core.db import get_hairdresser_data_from_db
from core.models import SalonPublicDetails, Service, Appointment

# Cria um novo "roteador". Pense nele como um mini-aplicativo FastAPI.
# Todos os endpoints aqui serão prefixados com /api/v1 (definiremos isso no main.py)
router = APIRouter(
    tags=["Cliente Final"] # Agrupa estes endpoints na documentação /docs
)

# --- Endpoints da API PÚBLICA (para o frontend do cliente final) ---

# A rota raiz "/" será movida para o main.py
# @router.get("/")
# def read_root():
#     return {"status": "API de Agendamento Rodando"}

@router.get("/saloes/{salao_id}/servicos", response_model=SalonPublicDetails)
def get_salon_services_and_details(salao_id: str):
    """Retorna detalhes públicos do salão E a lista de serviços (público)."""
    logging.info(f"Buscando detalhes/serviços para: {salao_id}")
    salon_data = get_hairdresser_data_from_db(salao_id)
    if not salon_data:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado")

    services_list_formatted = []
    if salon_data.get("servicos_data"):
        for service_id, service_info in salon_data["servicos_data"].items():
            # Usa **service_info para passar todos os campos (incluindo preco, descricao)
            services_list_formatted.append(Service(id=service_id, **service_info))
    
    # Usa **salon_data para passar todos os campos de personalização
    response_data = SalonPublicDetails(servicos=services_list_formatted, **salon_data)
    return response_data

@router.get("/saloes/{salao_id}/horarios-disponiveis")
async def get_available_slots_endpoint(
    salao_id: str,
    service_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    """Busca horários disponíveis (público)."""
    logging.info(f"Buscando horários para salão {salao_id} em {date}")
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado")
        
        calendar_id = salon_data.get('calendar_id')
        if not calendar_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="ID Calendário não configurado.")

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serviço não encontrado.")
        
        duration = service_info.get('duracao_minutos')
        if duration is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Duração do serviço não encontrada.")

        available_slots = calendar_service.find_available_slots(
            calendar_id=calendar_id, service_duration_minutes=duration,
            work_days=salon_data.get('dias_trabalho', []), start_hour_str=salon_data.get('horario_inicio', '09:00'),
            end_hour_str=salon_data.get('horario_fim', '18:00'), date_str=date
        )
        return {"horarios_disponiveis": available_slots}
    except Exception as e:
        logging.exception(f"Erro CRÍTICO no cálculo de slots:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao calcular horários.")

@router.post("/agendamentos", status_code=status.HTTP_201_CREATED)
async def create_appointment(appointment: Appointment):
    """Cria um novo agendamento (público, recebe nome/telefone no corpo)."""
    salao_id = appointment.salao_id
    service_id = appointment.service_id
    start_time = appointment.start_time
    user_name = appointment.customer_name.strip()
    user_phone = appointment.customer_phone
    logging.info(f"Cliente '{user_name}' ({user_phone}) criando agendamento para {salao_id}")

    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Salão não encontrado")
        
        calendar_id = salon_data.get('calendar_id')
        if not calendar_id:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="ID Calendário não configurado.")

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Serviço não encontrado.")
        
        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        if duration is None or service_name is None:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Dados do serviço incompletos.")

        cleaned_phone = re.sub(r'\D', '', user_phone)
        if not (10 <= len(cleaned_phone) <= 11):
             raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Formato de telefone inválido após limpeza.")

        success = calendar_service.create_event(
            calendar_id=calendar_id, service_name=service_name, start_time_str=start_time,
            duration_minutes=duration,
            customer_name=user_name,
            customer_phone=user_phone
        )
        if not success:
             raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Falha ao criar evento no calendário. Verifique permissões ou log.")

        return {"message": f"Agendamento para '{service_name}' criado com sucesso!"}

    except HTTPException as httpe:
        raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento:")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno ao criar agendamento.")
