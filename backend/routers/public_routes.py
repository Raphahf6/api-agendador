# backend/routers/public_routes.py
import logging
import re
from fastapi import APIRouter, HTTPException, Query, status, Depends
from datetime import datetime, timedelta 
from firebase_admin import firestore 


# Importações dos nossos módulos
from core.models import SalonPublicDetails, Service, Appointment
from core.db import get_hairdresser_data_from_db, db 
import calendar_service
import email_service # Importa o serviço de e-mail

router = APIRouter(
    tags=["Cliente Final"] 
)

# --- Endpoint GET /saloes/{salao_id}/servicos (Sem alterações) ---
@router.get("/saloes/{salao_id}/servicos", response_model=SalonPublicDetails)
def get_salon_services_and_details(salao_id: str):
    logging.info(f"Buscando detalhes/serviços para: {salao_id}")
    salon_data = get_hairdresser_data_from_db(salao_id) # Usa a função de DB
    if not salon_data:
        raise HTTPException(status_code=404, detail="Salão não encontrado")

    services_list_formatted = []
    if salon_data.get("servicos_data"):
        for service_id, service_info in salon_data["servicos_data"].items():
            services_list_formatted.append(Service(id=service_id, **service_info)) 
    
    response_data = SalonPublicDetails(servicos=services_list_formatted, **salon_data) 
    return response_data

# --- Endpoint GET /saloes/{salao_id}/horarios-disponiveis (HÍBRIDO - Leitura) ---
@router.get("/saloes/{salao_id}/horarios-disponiveis")
async def get_available_slots_endpoint( 
    salao_id: str,
    service_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    """Busca horários disponíveis (lendo do FIRESTORE + GOOGLE)."""
    logging.info(f"Buscando horários (Híbrido) para salão {salao_id} em {date}")
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
        
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(status_code=404, detail="Serviço não encontrado.")
        
        duration = service_info.get('duracao_minutos')
        if duration is None: raise HTTPException(status_code=500, detail="Duração do serviço não encontrada.")

        # --- CHAMADA DA FUNÇÃO HÍBRIDA (Leitura) ---
        available_slots = calendar_service.find_available_slots(
            salao_id=salao_id,
            salon_data=salon_data, # Passa o dict 'salon_data' inteiro
            service_duration_minutes=duration,
            date_str=date
        )
        
        return {"horarios_disponiveis": available_slots}
    except Exception as e:
        logging.exception(f"Erro CRÍTICO no cálculo de slots (Híbrido):")
        raise HTTPException(status_code=500, detail="Erro interno ao calcular horários.")

# --- Endpoint POST /agendamentos (HÍBRIDO - Escrita) ---
@router.post("/agendamentos", status_code=status.HTTP_201_CREATED)
async def create_appointment(appointment: Appointment):
    """Cria um novo agendamento (público), SALVA NO FIRESTORE, envia e-mail,
       E SINCRONIZA COM GOOGLE CALENDAR (se ativo)."""
       
    salao_id = appointment.salao_id
    service_id = appointment.service_id
    start_time_str = appointment.start_time
    user_name = appointment.customer_name.strip()
    user_phone = appointment.customer_phone
    logging.info(f"Cliente '{user_name}' criando agendamento (Híbrido) para {salao_id}")

    try:
        # 1. Validações e Busca de Dados
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
        
        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(status_code=404, detail="Serviço não encontrado.")
        
        duration = service_info.get('duracao_minutos')
        service_name = service_info.get('nome_servico')
        salon_name = salon_data.get('nome_salao')
        # Usamos o 'calendar_id' como o e-mail de destino do Resend
        salon_email_destino = salon_data.get('calendar_id') 

        if duration is None or service_name is None or not salon_email_destino:
            raise HTTPException(status_code=500, detail="Dados do serviço ou e-mail de destino incompletos.")

        # 2. Validação do telefone
        cleaned_phone = re.sub(r'\D', '', user_phone)
        if not (10 <= len(cleaned_phone) <= 11):
             raise HTTPException(status_code=400, detail="Formato de telefone inválido.")

        # 3. LÓGICA DE SALVAMENTO NO FIRESTORE (Fonte da Verdade)
        start_time_dt = datetime.fromisoformat(start_time_str)
        end_time_dt = start_time_dt + timedelta(minutes=duration)
        
        agendamento_data = {
            "salaoId": salao_id, "serviceId": service_id, "serviceName": service_name,
            "durationMinutes": duration, "startTime": start_time_dt, "endTime": end_time_dt,
            "customerName": user_name, "customerPhone": user_phone,
            "status": "confirmado", "createdAt": firestore.SERVER_TIMESTAMP 
        }
        
        agendamento_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos').document()
        agendamento_ref.set(agendamento_data)
        logging.info(f"Agendamento salvo no Firestore com ID: {agendamento_ref.id}")

        # 4. DISPARO DO E-MAIL (Resend)
        # (Executado de forma assíncrona, não bloqueia a resposta)
        try:
            email_success = email_service.send_confirmation_email_to_salon(
                salon_email=salon_email_destino, 
                salon_name=salon_name, 
                customer_name=user_name,
                client_phone=user_phone, # Passando o telefone
                service_name=service_name,
                start_time_iso=start_time_str
            )
            if email_success:
                logging.info("E-mail de confirmação via Resend disparado com sucesso.")
            else:
                logging.warning("Falha ao disparar e-mail de confirmação Resend (função retornou False).")
        except Exception as e:
            logging.error(f"Erro CRÍTICO ao disparar e-mail Resend: {e}")
            # Não quebra o agendamento, apenas loga.

        # 5. --- LÓGICA DE ESCRITA HÍBRIDA (ITEM 5 - IMPLEMENTADO) ---
        # Verifica se a sincronização está ativa E se temos um token
        google_event_data = {
            "summary": f"{service_name} - {user_name}",
            "description": f"Agendamento via Horalis.\nCliente: {user_name}\nTelefone: {user_phone}\nServiço: {service_name}",
            "start_time_iso": start_time_dt.isoformat(), 
            "end_time_iso": end_time_dt.isoformat(),
        }

        # --- NOVO BLOCO DE DEBUG: IMPRIMIR OS VALORES ---
        try:
            sync_enabled_val = salon_data.get("google_sync_enabled")
            token_val = salon_data.get("google_refresh_token")
            
            logging.error("--- VERIFICAÇÃO DE DEBUG ANTES DO IF ---")
            logging.error(f"DEBUG: Valor lido para 'google_sync_enabled': {sync_enabled_val}")
            logging.error(f"DEBUG: Tipo do valor 'google_sync_enabled': {type(sync_enabled_val)}")
            logging.error(f"DEBUG: Token (primeiros 10 chars): {str(token_val)[:10]}...")
            logging.error("-------------------------------------------")

        except Exception as e:
            logging.error(f"DEBUG: Erro ao tentar logar os dados: {e}")
        # --- FIM DO NOVO BLOCO DE DEBUG ---
        

        # Verifica se a sincronização está ativa E se temos um token
        if salon_data.get("google_sync_enabled") and salon_data.get("google_refresh_token"):
            logging.info(f"DEBUG: Sincronização ATIVA. Refresh token encontrado.")
            
            try:
                debug_token = salon_data.get("google_refresh_token")
                # ... (o resto do 'try' continua igual) ...
                google_success = calendar_service.create_google_event_with_oauth(
                    refresh_token=debug_token,
                    event_data=google_event_data
                )
                
                if google_success:
                    logging.info("Agendamento salvo com sucesso no Google Calendar (OAuth).")
                else:
                    logging.error("DEBUG: A função create_google_event_with_oauth retornou 'False'.")
                    raise HTTPException(status_code=500, detail="DEBUG: create_google_event_with_oauth retornou 'False'.")
            
            except Exception as e:
                logging.error(f"DEBUG: Erro inesperado DENTRO do 'try' de sincronização: {e}")
                raise HTTPException(status_code=500, detail=f"DEBUG: Erro na chamada de sync: {str(e)}")
        else:
            logging.warning("DEBUG: Sincronização PULADA. 'google_sync_enabled' ou 'google_refresh_token' está faltando.")
            raise HTTPException(status_code=500, detail="DEBUG: Sincronização PULADA. Checagem 'if' falhou.")
        # --- FIM DA LÓGICA DE ESCRITA HÍBRIDA (MODO DE DEBUG V2) ---

        # 6. Retorna a resposta ao cliente final
        return {"message": f"Agendamento para '{service_name}' criado com sucesso!"}

    except HTTPException as httpe: raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento (Híbrido):")
        raise HTTPException(status_code=500, detail="Erro interno ao criar agendamento.")

