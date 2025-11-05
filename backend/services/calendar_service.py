import logging
import pytz
import os
import re # Necessário se for usado em outros lugares
from datetime import datetime, time, timedelta
from typing import List, Dict, Any, Optional 

from core.db import db # Firestore DB

# --- IMPORTS PARA GOOGLE OAUTH ---
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# --- FIM DOS NOVOS IMPORTS ---

logging.basicConfig(level=logging.INFO)

# --- Configurações Globais ---
LOCAL_TIMEZONE = 'America/Sao_Paulo'
WEEKDAY_MAP_DB = {
    0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday',
    4: 'friday', 5: 'saturday', 6: 'sunday'
}
SLOT_INTERVAL_MINUTES = 15

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
SCOPES = ['https://www.googleapis.com/auth/calendar'] 


# ----------------------------------------------------
# >>> FUNÇÃO DE VALIDAÇÃO CRÍTICA (ALMOÇO) <<<
# ----------------------------------------------------

def is_conflict_with_lunch(
    booking_start_dt: datetime, 
    service_duration_minutes: int, 
    salon_data: Dict[str, Any]
) -> bool:
    """
    Verifica se um agendamento entra em conflito com o horário de almoço do salão.
    """
    
    day_name = WEEKDAY_MAP_DB.get(booking_start_dt.weekday())
    daily_schedule: Optional[Dict[str, Any]] = salon_data.get('horario_trabalho_detalhado', {}).get(day_name)

    # 1. Checa se o almoço é relevante
    if not daily_schedule or not daily_schedule.get('hasLunch') or not daily_schedule.get('lunchStart') or not daily_schedule.get('lunchEnd'):
        return False

    lunch_start_str = daily_schedule['lunchStart'] 
    lunch_end_str = daily_schedule['lunchEnd']     
    service_duration = timedelta(minutes=service_duration_minutes)
    
    try:
        date_of_booking = booking_start_dt.date()
        
        # Converte as strings 'HH:MM' para objetos datetime completos no dia, no timezone correto
        lunch_start_time = datetime.strptime(lunch_start_str, '%H:%M').time()
        lunch_end_time = datetime.strptime(lunch_end_str, '%H:%M').time()
        
        # Usa o tzinfo do booking_start_dt para garantir a mesma referência
        lunch_start_dt = datetime.combine(date_of_booking, lunch_start_time).astimezone(booking_start_dt.tzinfo)
        lunch_end_dt = datetime.combine(date_of_booking, lunch_end_time).astimezone(booking_start_dt.tzinfo)
        
    except ValueError:
        logging.error("Formato de horário de almoço inválido no DB.")
        return False
        
    # Determina o fim do agendamento
    booking_end_dt = booking_start_dt + service_duration

    # Conflito existe se: [Início da Reserva] < [Fim do Almoço] E [Fim da Reserva] > [Início do Almoço]
    is_overlapping = (booking_start_dt < lunch_end_dt) and (booking_end_dt > lunch_start_dt)
    
    return is_overlapping

# ----------------------------------------------------
# --- FUNÇÕES AUXILIARES DE SUPORTE (OAuth e CRUD) ---
# ----------------------------------------------------

def get_google_calendar_service(refresh_token: str):
    # ... (Sua implementação) ...
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logging.error("Credenciais OAuth (Client ID/Secret) não configuradas no ambiente.")
        return None
    
    try:
        creds = Credentials.from_authorized_user_info(
            info={
                "refresh_token": refresh_token,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "token_uri": "https://oauth2.googleapis.com/token" 
            },
            scopes=SCOPES 
        )
        service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        return service
        
    except Exception:
        logging.exception(f"Falha CRÍTICA ao criar serviço Google Calendar com refresh_token:")
        return None

def create_google_event_with_oauth(refresh_token: str, event_data: Dict[str, Any]) -> Optional[str]:
    # ... (Sua implementação de criação de evento) ...
    google_service = get_google_calendar_service(refresh_token)
    if not google_service: return None
    try:
        event_body = {
            'summary': event_data['summary'],
            'description': event_data['description'],
            'start': {'dateTime': event_data['start_time_iso']},
            'end': {'dateTime': event_data['end_time_iso']},
            'attendees': [],
            'reminders': {'useDefault': True},
        }
        event = google_service.events().insert(calendarId='primary', body=event_body).execute()
        return event.get('id')
    except Exception:
        logging.exception("Erro inesperado ao criar evento no Google Calendar (OAuth):")
        return None

def delete_google_event(refresh_token: str, event_id: str) -> bool:
    # ... (Sua implementação de delete) ...
    google_service = get_google_calendar_service(refresh_token)
    if not google_service: return False
    try:
        google_service.events().delete(calendarId='primary', eventId=event_id, sendUpdates='all').execute()
        return True
    except HttpError as e:
        if e.resp.status == 410: return True
        logging.error(f"Erro HttpError ao DELETAR evento {event_id}: {e.content}")
        return False
    except Exception:
        logging.exception(f"Erro inesperado ao DELETAR evento {event_id}:")
        return False

def update_google_event(refresh_token: str, event_id: str, new_start_iso: str, new_end_iso: str) -> bool:
    # ... (Sua implementação de update) ...
    google_service = get_google_calendar_service(refresh_token)
    if not google_service: return False
    try:
        event_patch_body = { 'start': {'dateTime': new_start_iso}, 'end': {'dateTime': new_end_iso} }
        google_service.events().patch(calendarId='primary', eventId=event_id, body=event_patch_body, sendUpdates='all').execute()
        return True
    except Exception:
        logging.exception(f"Erro inesperado ao ATUALIZAR evento {event_id}:")
        return False


# ----------------------------------------------------
# >>> FUNÇÃO PRINCIPAL: ENCONTRAR SLOTS DISPONÍVEIS <<<
# ----------------------------------------------------

def find_available_slots(
    salao_id: str, 
    salon_data: dict, 
    service_duration_minutes: int, 
    date_str: str
) -> List[str]:
    """
    Encontra horários disponíveis, respeitando a AGENDA DETALHADA e o ALMOÇO.
    """
    if db is None: 
        logging.error("Firestore DB não está inicializado.")
        return []

    available_slots_iso = [] 
    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        
        # 1. Validar e Definir Dia Alvo
        target_date_local = datetime.strptime(date_str, '%Y-%m-%d').date()
        day_of_week_name = WEEKDAY_MAP_DB.get(target_date_local.weekday())
        
        # --- BUSCANDO AGENDA DETALHADA DO DIA ALVO ---
        daily_config = salon_data.get('horario_trabalho_detalhado', {}).get(day_of_week_name)

        if not daily_config or not daily_config.get('isOpen'):
            logging.info(f"Dia de folga ou não configurado detectado para {date_str}.")
            return []
        
        # 2. Definir Período de Trabalho (USANDO daily_config)
        start_hour_str = daily_config.get('openTime', '09:00')
        end_hour_str = daily_config.get('closeTime', '18:00')

        start_work_time = datetime.strptime(start_hour_str, '%H:%M').time()
        end_work_time = datetime.strptime(end_hour_str, '%H:%M').time()

        day_start_dt = local_tz.localize(datetime.combine(target_date_local, start_work_time))
        day_end_dt = local_tz.localize(datetime.combine(target_date_local, end_work_time))

        # 3. Definir Ponto de Partida da Busca
        now_local = datetime.now(local_tz)
        minutes_to_next_interval = SLOT_INTERVAL_MINUTES - (now_local.minute % SLOT_INTERVAL_MINUTES)
        start_search_today = now_local.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_next_interval)
        search_from = max(start_search_today, day_start_dt) if target_date_local == now_local.date() else day_start_dt

        if search_from >= day_end_dt:
             logging.info(f"Horário de início da busca ({search_from}) é após o fim do expediente.")
             return []

        # 4. COLETA DE HORÁRIOS OCUPADOS (HÍBRIDO)
        busy_periods = []
        # --- FONTE 1: FIRESTORE (Agendamentos Horalis) ---
        try:
            agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
            day_start_utc = day_start_dt.astimezone(pytz.utc)
            day_end_utc = day_end_dt.astimezone(pytz.utc)
            query = agendamentos_ref.where("startTime", ">=", day_start_utc).where("startTime", "<", day_end_utc)
            for doc in query.stream():
                data = doc.to_dict()
                if data.get('startTime') and data.get('endTime'):
                    busy_periods.append({
                        "start": data['startTime'].astimezone(local_tz),
                        "end": data['endTime'].astimezone(local_tz)
                    })
        except Exception as e: logging.error(f"Erro ao buscar agendamentos do Firestore: {e}")
            
        # --- FONTE 2: GOOGLE CALENDAR (Eventos Pessoais) ---
        refresh_token = salon_data.get("google_refresh_token")
        if salon_data.get("google_sync_enabled") and refresh_token:
            google_service = get_google_calendar_service(refresh_token)
            if google_service:
                try:
                    events_result = google_service.events().list(
                        calendarId='primary', timeMin=day_start_dt.isoformat(), timeMax=day_end_dt.isoformat(),
                        singleEvents=True, orderBy='startTime', timeZone=LOCAL_TIMEZONE
                    ).execute()
                    for event in events_result.get('items', []):
                        start_str = event['start'].get('dateTime')
                        end_str = event['end'].get('dateTime')
                        if start_str and end_str and event.get('transparency') != 'transparent':
                            busy_periods.append({
                                "start": datetime.fromisoformat(start_str).astimezone(local_tz),
                                "end": datetime.fromisoformat(end_str).astimezone(local_tz)
                            })
                except Exception as e: logging.error(f"Erro ao buscar eventos do Google: {e}")
                logging.info(f"Horários ocupados brutos para análise: {busy_periods}") # <<<< ADICIONE ESTA LINHA!

        # 5. Calcular Vãos Disponíveis (COM FILTRO DE ALMOÇO)
        logging.info(f"Calculando vãos com base em {len(busy_periods)} eventos combinados.")
        potential_slot = search_from 
        
        while potential_slot < day_end_dt:
            slot_end = potential_slot + timedelta(minutes=service_duration_minutes)
            if slot_end > day_end_dt: break 
            
            is_free = True
            
            # --- FILTRO 1: VERIFICAÇÃO DE ALMOÇO (CRÍTICA) ---
            if is_conflict_with_lunch(potential_slot, service_duration_minutes, salon_data):
                is_free = False
                
                # Avança o slot para o FIM do almoço
                lunch_end_str = daily_config.get('lunchEnd', end_work_time.strftime('%H:%M'))
                lunch_end_time = datetime.strptime(lunch_end_str, '%H:%M').time()
                lunch_end_dt = local_tz.localize(datetime.combine(target_date_local, lunch_end_time))
                
                potential_slot = lunch_end_dt 
                minute_offset = potential_slot.minute % SLOT_INTERVAL_MINUTES
                if minute_offset != 0:
                     potential_slot += timedelta(minutes=SLOT_INTERVAL_MINUTES - minute_offset)
                
                continue 
            
            # --- FILTRO 2: VERIFICAÇÃO DE CONFLITO COM EVENTOS OCUPADOS ---
            for event in busy_periods:
                if potential_slot < event['end'] and slot_end > event['start']:
                    is_free = False
                    potential_slot = event['end']
                    minute_offset = potential_slot.minute % SLOT_INTERVAL_MINUTES
                    if minute_offset != 0:
                        potential_slot += timedelta(minutes=SLOT_INTERVAL_MINUTES - minute_offset)
                    break 
            
            # Se passou pelos dois filtros, adiciona o slot
            if is_free:
                available_slots_iso.append(potential_slot.isoformat())
                potential_slot += timedelta(minutes=SLOT_INTERVAL_MINUTES)
            
        final_slots = sorted(list(set(available_slots_iso)))
        return final_slots

    except Exception as e:
        logging.exception(f"Erro inesperado no cálculo de slots (Híbrido):")
        return []

# ----------------------------------------------------
# --- FUNÇÕES DE VERIFICAÇÃO SIMPLES (is_slot_available) ---
# ----------------------------------------------------

def is_slot_available(
    salao_id: str,
    salon_data: dict,
    new_start_dt: datetime, 
    duration_minutes: int,
    ignore_firestore_id: str, 
    ignore_google_event_id: Optional[str]
) -> bool:
    """
    Verifica se um slot de horário específico está livre, checando Firestore, Google Calendar e ALMOÇO.
    """
    if db is None: 
        logging.error("Firestore DB não está inicializado (is_slot_available).")
        return False

    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        new_end_dt = new_start_dt + timedelta(minutes=duration_minutes)

        # 0. VERIFICAÇÃO DE CONFLITO COM ALMOÇO
        if is_conflict_with_lunch(new_start_dt, duration_minutes, salon_data):
            logging.warning("[Verificação de Conflito] Falha: Horário solicitado cai no horário de almoço.")
            return False 

        # 1. Verificar contra o horário de funcionamento do salão
        target_date_local = new_start_dt.date()
        day_of_week_name = WEEKDAY_MAP_DB.get(target_date_local.weekday())
        
        # --- BUSCANDO AGENDA DETALHADA DO DIA ALVO ---
        daily_config = salon_data.get('horario_trabalho_detalhado', {}).get(day_of_week_name)

        if not daily_config or not daily_config.get('isOpen'):
             logging.warning(f"[Verificação de Conflito] Falha: {target_date_local} é um dia de folga.")
             return False 

        start_hour_str = daily_config.get('openTime', '09:00')
        end_hour_str = daily_config.get('closeTime', '18:00')
        start_work_time = datetime.strptime(start_hour_str, '%H:%M').time()
        end_work_time = datetime.strptime(end_hour_str, '%H:%M').time()

        day_start_dt = local_tz.localize(datetime.combine(target_date_local, start_work_time))
        day_end_dt = local_tz.localize(datetime.combine(target_date_local, end_work_time))

        if new_start_dt < day_start_dt or new_end_dt > day_end_dt:
            logging.warning(f"[Verificação de Conflito] Falha: Horário fora do expediente.")
            return False 

        # 2. Coletar todos os outros períodos ocupados (Híbrido)
        busy_periods = []
        
        day_start_utc = day_start_dt.astimezone(pytz.utc)
        day_end_utc = day_end_dt.astimezone(pytz.utc)

        # --- FONTE 1: FIRESTORE (Outros Agendamentos Horalis) ---
        try:
            agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
            query = agendamentos_ref.where("startTime", ">=", day_start_utc).where("startTime", "<", day_end_utc)
            for doc in query.stream():
                 if doc.id == ignore_firestore_id: continue
                 data = doc.to_dict()
                 if data.get('startTime') and data.get('endTime'):
                    busy_periods.append({
                        "start": data['startTime'].astimezone(local_tz),
                        "end": data['endTime'].astimezone(local_tz)
                    })
        except Exception as e: logging.error(f"Erro ao buscar agendamentos do Firestore (is_slot_available): {e}"); return False 

        # --- FONTE 2: GOOGLE CALENDAR (Eventos Pessoais) ---
        refresh_token = salon_data.get("google_refresh_token")
        if salon_data.get("google_sync_enabled") and refresh_token:
            google_service = get_google_calendar_service(refresh_token)
            if google_service:
                try:
                    events_result = google_service.events().list(
                        calendarId='primary', timeMin=day_start_dt.isoformat(), timeMax=day_end_dt.isoformat(),
                        singleEvents=True, timeZone=LOCAL_TIMEZONE
                    ).execute()
                    for event in events_result.get('items', []):
                        if ignore_google_event_id and event.get('id') == ignore_google_event_id: continue
                        start_str = event['start'].get('dateTime')
                        end_str = event['end'].get('dateTime')
                        if start_str and end_str and event.get('transparency') != 'transparent':
                            busy_periods.append({
                                "start": datetime.fromisoformat(start_str).astimezone(local_tz),
                                "end": datetime.fromisoformat(end_str).astimezone(local_tz)
                            })
                except Exception as e: logging.error(f"Erro ao buscar eventos do Google (is_slot_available): {e}"); return False 

        # 3. Verificação Final de Conflito
        for event in busy_periods:
            if new_start_dt < event['end'] and new_end_dt > event['start']:
                logging.warning(f"[Verificação de Conflito] Falha: Conflito detectado com evento das {event['start'].time()} às {event['end'].time()}.")
                return False # Conflito!

        # 4. Se passou por tudo, o slot está livre
        logging.info(f"[Verificação de Conflito] Sucesso: Slot {new_start_dt.time()} está livre.")
        return True

    except Exception as e:
        logging.exception(f"Erro inesperado em 'is_slot_available':")
        return False