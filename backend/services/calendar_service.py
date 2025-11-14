import logging
import pytz
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional 
from google.cloud.firestore import FieldFilter

from core.db import db # Firestore DB

# --- IMPORTS PARA GOOGLE OAUTH ---
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logging.basicConfig(level=logging.INFO)

# --- Configurações Globais ---
LOCAL_TIMEZONE = 'America/Sao_Paulo'
WEEKDAY_MAP_DB = {
    0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday',
    4: 'friday', 5: 'saturday', 6: 'sunday'
}
SLOT_INTERVAL_MINUTES = 30

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
    try:
        timezone = pytz.timezone(LOCAL_TIMEZONE)

        # 1. Garante que a data do agendamento tenha fuso horário
        if booking_start_dt.tzinfo is None:
            booking_local = pytz.utc.localize(booking_start_dt).astimezone(timezone)
        else:
            booking_local = booking_start_dt.astimezone(timezone)

        # 2. Pega a configuração do dia
        day_name = booking_local.strftime('%A').lower()
        daily_schedule = salon_data.get('horario_trabalho_detalhado', {}).get(day_name)

        if not daily_schedule: return False
        
        # 3. Verifica se almoço existe e está configurado
        if not daily_schedule.get('hasLunch'): return False
        
        lunch_start_str = daily_schedule.get('lunchStart')
        lunch_end_str = daily_schedule.get('lunchEnd')
        
        if not lunch_start_str or not lunch_end_str: return False

        # 4. Monta os objetos de data do almoço com FUSO HORÁRIO
        date_local = booking_local.date()
        
        lunch_start_time = datetime.strptime(lunch_start_str, '%H:%M').time()
        lunch_end_time = datetime.strptime(lunch_end_str, '%H:%M').time()

        lunch_start_dt = timezone.localize(datetime.combine(date_local, lunch_start_time))
        lunch_end_dt = timezone.localize(datetime.combine(date_local, lunch_end_time))
        
        # 5. Calcula fim do agendamento
        booking_end_local = booking_local + timedelta(minutes=service_duration_minutes)

        # 6. Verifica sobreposição
        if (booking_local < lunch_end_dt) and (booking_end_local > lunch_start_dt):
            # logging.info(f"CONFLITO DE ALMOÇO: {booking_local.time()} colide com {lunch_start_str}-{lunch_end_str}")
            return True
            
        return False

    except Exception as e:
        logging.error(f"Erro ao verificar almoço: {e}")
        return False

# ----------------------------------------------------
# --- FUNÇÕES AUXILIARES DE SUPORTE (OAuth e CRUD) ---
# ----------------------------------------------------

def get_google_calendar_service(refresh_token: str):
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
        logging.exception(f"Falha CRÍTICA ao criar serviço Google Calendar com refresh_token.")
        return None

def create_google_event_with_oauth(refresh_token: str, event_data: Dict[str, Any]) -> Optional[str]:
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

# ----------------------------------------------------
# >>> FUNÇÃO PRINCIPAL: ENCONTRAR SLOTS DISPONÍVEIS <<<
# ----------------------------------------------------

def find_available_slots(
    salao_id: str, 
    salon_data: dict, 
    service_duration_minutes: int, 
    date_str: str,
    professional_id: Optional[str] = None # 🌟 NOVO PARÂMETRO
) -> List[str]:
    """
    Encontra horários disponíveis, filtrando por profissional se especificado.
    """
    if db is None: return []

    available_slots_iso = [] 
    
    try:
        # 1. Configuração de Fuso e Datas
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        target_date_local = datetime.strptime(date_str, '%Y-%m-%d').date()
        day_of_week_name = WEEKDAY_MAP_DB.get(target_date_local.weekday())
        
        # 2. Obter Configuração do Dia
        daily_config = salon_data.get('horario_trabalho_detalhado', {}).get(day_of_week_name)

        if not daily_config or not daily_config.get('isOpen'):
            return []
        
        # 3. Definir Início e Fim do Expediente
        start_hour_str = daily_config.get('openTime', '09:00')
        end_hour_str = daily_config.get('closeTime', '18:00')
        
        start_work_time = datetime.strptime(start_hour_str, '%H:%M').time()
        end_work_time = datetime.strptime(end_hour_str, '%H:%M').time()

        day_start_dt = local_tz.localize(datetime.combine(target_date_local, start_work_time))
        day_end_dt = local_tz.localize(datetime.combine(target_date_local, end_work_time))

        # 4. Ponto de Partida
        now_local = datetime.now(local_tz)
        if target_date_local == now_local.date():
            minutes_to_next_interval = SLOT_INTERVAL_MINUTES - (now_local.minute % SLOT_INTERVAL_MINUTES)
            start_search_today = now_local.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_next_interval)
            search_from = max(start_search_today, day_start_dt)
        else:
            search_from = day_start_dt

        if search_from >= day_end_dt: return []

        # 5. COLETA DE HORÁRIOS OCUPADOS
        busy_periods = [] 
        
        day_start_utc = day_start_dt.astimezone(pytz.utc)
        day_end_utc = day_end_dt.astimezone(pytz.utc) + timedelta(hours=3) 

        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        
        # Query Base: Intervalo de Tempo
        query = agendamentos_ref.where(filter=FieldFilter("startTime", ">=", day_start_utc))\
                                .where(filter=FieldFilter("startTime", "<=", day_end_utc))
        
        # 🌟 FILTRO DE PROFISSIONAL
        # Se um profissional foi selecionado, filtramos apenas os agendamentos dele.
        if professional_id:
            query = query.where(filter=FieldFilter("professionalId", "==", professional_id))
        
        docs = query.stream()

        for doc in docs:
            data = doc.to_dict()
            if data.get('status') in ['cancelado', 'rejeitado', 'canceled', 'rejected']: continue
                
            appt_start = data.get('startTime')
            appt_end = data.get('endTime')
            
            if appt_start and appt_end:
                if appt_start.tzinfo is None: appt_start = pytz.utc.localize(appt_start)
                if appt_end.tzinfo is None: appt_end = pytz.utc.localize(appt_end)
                
                busy_periods.append({
                    'start': appt_start.astimezone(local_tz),
                    'end': appt_end.astimezone(local_tz)
                })

        # 6. Almoço
        if daily_config.get('hasLunch'):
            try:
                l_start_time = datetime.strptime(daily_config.get('lunchStart'), '%H:%M').time()
                l_end_time = datetime.strptime(daily_config.get('lunchEnd'), '%H:%M').time()
                l_start_dt = local_tz.localize(datetime.combine(target_date_local, l_start_time))
                l_end_dt = local_tz.localize(datetime.combine(target_date_local, l_end_time))
                busy_periods.append({'start': l_start_dt, 'end': l_end_dt})
            except: pass

        busy_periods.sort(key=lambda x: x['start'])

        # 7. Calcular Vãos
        current_slot = search_from 
        while current_slot < day_end_dt:
            slot_end = current_slot + timedelta(minutes=service_duration_minutes)
            if slot_end > day_end_dt: break 
            
            is_conflict = False
            for event in busy_periods:
                if current_slot < event['end'] and slot_end > event['start']:
                    is_conflict = True
                    # Otimização de pulo
                    next_possible_start = event['end']
                    minute_offset = next_possible_start.minute % SLOT_INTERVAL_MINUTES
                    if minute_offset != 0: next_possible_start += timedelta(minutes=SLOT_INTERVAL_MINUTES - minute_offset)
                    
                    if next_possible_start > current_slot: current_slot = next_possible_start
                    else: current_slot += timedelta(minutes=SLOT_INTERVAL_MINUTES)
                    break 
            
            if not is_conflict:
                available_slots_iso.append(current_slot.isoformat())
                current_slot += timedelta(minutes=SLOT_INTERVAL_MINUTES)
        
        final_slots = sorted(list(set(available_slots_iso)))
        return final_slots

    except Exception as e:
        logging.exception(f"Erro no cálculo de slots: {e}")
        return []

# ----------------------------------------------------
# --- FUNÇÃO DE VERIFICAÇÃO UNITÁRIA (is_slot_available) ---
# ----------------------------------------------------

def is_slot_available(
    salao_id: str,
    salon_data: dict,
    new_start_dt: datetime, 
    duration_minutes: int,
    ignore_firestore_id: Optional[str] = None,
    ignore_google_event_id: Optional[str] = None,
    professional_id: Optional[str] = None # 🌟 NOVO PARÂMETRO
) -> bool:
    """
    Verifica se um slot específico está livre, filtrando por profissional.
    """
    if db is None: return False

    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        # Garante timezone
        if new_start_dt.tzinfo is None:
            new_start_dt = pytz.utc.localize(new_start_dt).astimezone(local_tz)
        else:
            new_start_dt = new_start_dt.astimezone(local_tz)
            
        new_end_dt = new_start_dt + timedelta(minutes=duration_minutes)

        # 1. Coletar períodos ocupados
        busy_periods = []
        
        day_start_dt = new_start_dt.replace(hour=0, minute=0, second=0, microsecond=0)
        day_end_dt = day_start_dt + timedelta(days=1)
        
        day_start_utc = day_start_dt.astimezone(pytz.utc)
        day_end_utc = day_end_dt.astimezone(pytz.utc)

        # --- FIRESTORE ---
        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        query = agendamentos_ref.where(filter=FieldFilter("startTime", ">=", day_start_utc))\
                                .where(filter=FieldFilter("startTime", "<", day_end_utc))
        
        # 🌟 FILTRO PROFISSIONAL
        if professional_id:
            query = query.where(filter=FieldFilter("professionalId", "==", professional_id))

        for doc in query.stream():
             if doc.id == ignore_firestore_id: continue
             data = doc.to_dict()
             if data.get('status') in ['cancelado', 'rejeitado']: continue
             
             if data.get('startTime') and data.get('endTime'):
                busy_periods.append({
                    "start": data['startTime'].astimezone(local_tz),
                    "end": data['endTime'].astimezone(local_tz)
                })

        # --- GOOGLE CALENDAR (Se aplicável) ---
        # Nota: O Google Calendar geralmente é vinculado ao Dono (Geral). 
        # Se cada profissional tiver seu Google Calendar, a lógica precisaria mudar aqui.
        # Por enquanto, mantemos a lógica de que o Google bloqueia tudo (ou apenas se não tiver prof específico).
        refresh_token = salon_data.get("google_refresh_token")
        if salon_data.get("google_sync_enabled") and refresh_token:
             # ... (Lógica Google mantida - assume que Google bloqueia a agenda do salão como um todo ou do dono)
             # Se quiser que o Google não bloqueie outros profissionais, coloque um 'if not professional_id:' aqui.
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
                except Exception: pass

        # 2. Verificação Final
        for event in busy_periods:
            if new_start_dt < event['end'] and new_end_dt > event['start']:
                return False # Conflito!

        return True

    except Exception as e:
        logging.error(f"Erro is_slot_available: {e}")
        return False