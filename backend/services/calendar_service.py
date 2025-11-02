# backend/calendar_service.py (Versão Híbrida - COMPLETA)
import logging
import pytz
import os
from datetime import datetime, time, timedelta
from typing import List, Dict, Any, Optional # <<< 'Optional' foi adicionado

from core.db import db # Firestore DB

# --- NOVOS IMPORTS PARA GOOGLE OAUTH (Token de Refresh) ---
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
# --- FIM DOS NOVOS IMPORTS ---

logging.basicConfig(level=logging.INFO)

# --- Configurações ---
LOCAL_TIMEZONE = 'America/Sao_Paulo'
WEEKDAY_MAP_DB = {
    0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday',
    4: 'friday', 5: 'saturday', 6: 'sunday'
}
SLOT_INTERVAL_MINUTES = 15

GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
SCOPES = ['https://www.googleapis.com/auth/calendar'] 


# --- FUNÇÃO HELPER (SIMPLIFICADA): Criar Serviço Google com OAuth ---
def get_google_calendar_service(refresh_token: str):
    """
    Cria um serviço (service) do Google Calendar autenticado 
    usando o refresh_token (OAuth2) do dono do salão.
    """
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
        logging.info("Serviço Google Calendar (OAuth) inicializado com sucesso.")
        return service
        
    except Exception as e:
        logging.exception(f"Falha CRÍTICA ao criar serviço Google Calendar com refresh_token: {e}")
        return None
# --- FIM DA FUNÇÃO HELPER ---


# --- Função find_available_slots (Sem alterações) ---
def find_available_slots(
    salao_id: str, 
    salon_data: dict, 
    service_duration_minutes: int, 
    date_str: str
) -> List[str]:
    """
    Encontra horários disponíveis lendo os agendamentos do FIRESTORE
    E (se ativado) os eventos do GOOGLE CALENDAR do dono do salão.
    """
    # ... (Esta função longa permanece exatamente igual à sua versão anterior) ...
    if db is None: 
        logging.error("Firestore DB não está inicializado.")
        return []

    available_slots_iso = [] 
    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        
        # 1. Validar e Definir Dia Alvo
        target_date_local = datetime.strptime(date_str, '%Y-%m-%d').date()

        # 2. Verificar Dia de Folga
        work_days = salon_data.get('dias_trabalho', [])
        day_of_week_name = WEEKDAY_MAP_DB.get(target_date_local.weekday())
        if not day_of_week_name or day_of_week_name not in work_days:
            logging.info(f"Dia de folga detectado para {date_str}.")
            return []

        # 3. Definir Período de Trabalho (com Timezone!)
        start_hour_str = salon_data.get('horario_inicio', '09:00')
        end_hour_str = salon_data.get('horario_fim', '18:00')
        start_work_time = datetime.strptime(start_hour_str, '%H:%M').time()
        end_work_time = datetime.strptime(end_hour_str, '%H:%M').time()

        day_start_dt = local_tz.localize(datetime.combine(target_date_local, start_work_time))
        day_end_dt = local_tz.localize(datetime.combine(target_date_local, end_work_time))

        # 4. Definir Ponto de Partida da Busca
        now_local = datetime.now(local_tz)
        minutes_to_next_interval = SLOT_INTERVAL_MINUTES - (now_local.minute % SLOT_INTERVAL_MINUTES)
        start_search_today = now_local.replace(second=0, microsecond=0) + timedelta(minutes=minutes_to_next_interval)
        search_from = max(start_search_today, day_start_dt) if target_date_local == now_local.date() else day_start_dt

        if search_from >= day_end_dt:
             logging.info(f"Horário de início da busca ({search_from}) é após o fim do expediente.")
             return []

        # --- COLETA DE HORÁRIOS OCUPADOS (HÍBRIDO) ---
        busy_periods = [] # Lista combinada

        # --- FONTE 1: FIRESTORE (Agendamentos Horalis) ---
        try:
            logging.info(f"Buscando agendamentos no Firestore para '{salao_id}' em {date_str}")
            day_start_utc = day_start_dt.astimezone(pytz.utc)
            day_end_utc = day_end_dt.astimezone(pytz.utc)
            agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
            
            query = agendamentos_ref.where("startTime", ">=", day_start_utc).where("startTime", "<", day_end_utc)
            busy_periods_docs = query.stream()
            
            for doc in busy_periods_docs:
                data = doc.to_dict()
                if data.get('startTime') and data.get('endTime'):
                    busy_periods.append({
                        "start": data['startTime'].astimezone(local_tz),
                        "end": data['endTime'].astimezone(local_tz)
                    })
            logging.info(f"Encontrados {len(busy_periods)} agendamentos no Horalis (Firestore).")
        except Exception as e:
            logging.error(f"Erro ao buscar agendamentos do Firestore: {e}")
            
        # --- FONTE 2: GOOGLE CALENDAR (Eventos Pessoais) ---
        refresh_token = salon_data.get("google_refresh_token")
        if salon_data.get("google_sync_enabled") and refresh_token:
            logging.info("Sincronização Google Ativa. Buscando eventos do Google Calendar.")
            
            google_service = get_google_calendar_service(refresh_token)
            
            if google_service:
                try:
                    events_result = google_service.events().list(
                        calendarId='primary', 
                        timeMin=day_start_dt.isoformat(), 
                        timeMax=day_end_dt.isoformat(),
                        singleEvents=True,
                        orderBy='startTime',
                        timeZone=LOCAL_TIMEZONE
                    ).execute()
                    
                    google_events = events_result.get('items', [])
                    
                    for event in google_events:
                        start_str = event['start'].get('dateTime')
                        end_str = event['end'].get('dateTime')
                        transparency = event.get('transparency') 
                        
                        if start_str and end_str and transparency != 'transparent':
                            g_start = datetime.fromisoformat(start_str).astimezone(local_tz)
                            g_end = datetime.fromisoformat(end_str).astimezone(local_tz)
                            busy_periods.append({"start": g_start, "end": g_end})
                            
                    logging.info(f"Adicionados {len(google_events)} eventos do Google Calendar à lista de ocupados.")
                    
                except HttpError as e:
                    logging.error(f"Erro na API Google Calendar (Token pode ter sido revogado): {e}")
                except Exception as e:
                    logging.error(f"Erro inesperado ao processar eventos do Google: {e}")
        # --- FIM DA FONTE 2 ---

        # 6. Calcular Vãos Disponíveis (Lógica inalterada)
        logging.info(f"Calculando vãos com base em {len(busy_periods)} eventos combinados.")
        potential_slot = search_from 
        while potential_slot < day_end_dt:
            slot_end = potential_slot + timedelta(minutes=service_duration_minutes)
            if slot_end > day_end_dt: break 
            
            is_free = True
            for event in busy_periods:
                if potential_slot < event['end'] and slot_end > event['start']:
                    is_free = False
                    potential_slot = event['end']
                    minute_offset = potential_slot.minute % SLOT_INTERVAL_MINUTES
                    if minute_offset != 0:
                        potential_slot += timedelta(minutes=SLOT_INTERVAL_MINUTES - minute_offset)
                    break 
            
            if is_free:
                available_slots_iso.append(potential_slot.isoformat())
                potential_slot += timedelta(minutes=SLOT_INTERVAL_MINUTES)
            
        final_slots = sorted(list(set(available_slots_iso)))
        logging.info(f"Retornando {len(final_slots)} horários (Híbrido) para {date_str}.")
        return final_slots

    except Exception as e:
        logging.exception(f"Erro inesperado no cálculo de slots (Híbrido):")
        return []
    

# --- <<< MODIFICADO: Função de Escrita agora retorna o ID >>> ---
def create_google_event_with_oauth(
    refresh_token: str, 
    event_data: Dict[str, Any]
) -> Optional[str]: # <<< ALTERADO: Retorna string (ID) ou None
    """
    Cria um evento no Google Calendar do dono do salão usando OAuth2.
    Retorna o ID do evento em caso de sucesso, ou None em caso de falha.
    """
    google_service = get_google_calendar_service(refresh_token)
    
    if not google_service:
        logging.error("Não foi possível criar o serviço Google (OAuth) para escrita.")
        return None # <<< ALTERADO

    try:
        # Relembrando: Removemos o 'timeZone' pois a string ISO já contém
        event_body = {
            'summary': event_data['summary'],
            'description': event_data['description'],
            'start': {'dateTime': event_data['start_time_iso']},
            'end': {'dateTime': event_data['end_time_iso']},
            'attendees': [],
            'reminders': {'useDefault': True},
        }

        event = google_service.events().insert(
            calendarId='primary', 
            body=event_body
        ).execute()
        
        event_id = event.get('id')
        logging.info(f"Evento criado com sucesso no Google Calendar (OAuth). ID: {event_id}")
        return event_id # <<< ALTERADO: Retorna o ID

    except HttpError as e:
        logging.error(f"Erro HttpError ao criar evento no Google Calendar (OAuth): {e.resp.status} - {e.content}")
        return None # <<< ALTERADO
    except Exception as e:
        logging.exception(f"Erro inesperado ao criar evento no Google Calendar (OAuth):")
        return None # <<< ALTERADO
# --- <<< FIM DA MODIFICAÇÃO >>> ---


# --- <<< ADICIONADO: Nova função para DELETAR eventos >>> ---
def delete_google_event(refresh_token: str, event_id: str) -> bool:
    """Deleta um evento específico do Google Calendar usando seu ID."""
    google_service = get_google_calendar_service(refresh_token)
    if not google_service:
        logging.error(f"Não foi possível criar serviço Google para DELETAR evento {event_id}")
        return False
        
    try:
        google_service.events().delete(
            calendarId='primary', 
            eventId=event_id,
            sendUpdates='all' # Notifica participantes (se houver)
        ).execute()
        logging.info(f"Evento {event_id} deletado com sucesso do Google Calendar.")
        return True
    except HttpError as e:
        # Erro 410 "Gone" significa que o evento já foi deletado.
        if e.resp.status == 410: 
            logging.warning(f"Evento {event_id} já tinha sido deletado do Google Calendar (Erro 410).")
            return True # Consideramos sucesso
        logging.error(f"Erro HttpError ao DELETAR evento {event_id}: {e.content}")
        return False
    except Exception as e:
        logging.exception(f"Erro inesperado ao DELETAR evento {event_id}:")
        return False
# --- <<< FIM DA ADIÇÃO >>> ---


# --- <<< ADICIONADO: Nova função para ATUALIZAR (Reagendar) eventos >>> ---
def update_google_event(
    refresh_token: str, 
    event_id: str, 
    new_start_iso: str, 
    new_end_iso: str
) -> bool:
    """Atualiza (Reagenda) a hora de início e fim de um evento no Google Calendar."""
    google_service = get_google_calendar_service(refresh_token)
    if not google_service:
        logging.error(f"Não foi possível criar serviço Google para ATUALIZAR evento {event_id}")
        return False
        
    try:
        # Para o PATCH, enviamos apenas os campos que queremos mudar.
        event_patch_body = {
            'start': {'dateTime': new_start_iso},
            'end': {'dateTime': new_end_iso},
        }

        google_service.events().patch(
            calendarId='primary',
            eventId=event_id,
            body=event_patch_body,
            sendUpdates='all' # Notifica participantes
        ).execute()
        
        logging.info(f"Evento {event_id} ATUALIZADO com sucesso no Google Calendar.")
        return True
    except HttpError as e:
        logging.error(f"Erro HttpError ao ATUALIZAR evento {event_id}: {e.content}")
        return False
    except Exception as e:
        logging.exception(f"Erro inesperado ao ATUALIZAR evento {event_id}:")
        return False
# --- <<< FIM DA ADIÇÃO >>> ---


def is_slot_available(
    salao_id: str,
    salon_data: dict,
    new_start_dt: datetime, # O novo horário de início (já como objeto datetime c/ fuso)
    duration_minutes: int,
    ignore_firestore_id: str, # O ID do agendamento que estamos arrastando
    ignore_google_event_id: Optional[str] # O ID do Google Event (se houver)
) -> bool:
    """
    Verifica se um slot de horário específico está livre, checando tanto o
    Firestore quanto o Google Calendar, ignorando o próprio evento que está sendo movido.
    Retorna True se o slot estiver livre, False se houver conflito.
    """
    if db is None: 
        logging.error("Firestore DB não está inicializado (is_slot_available).")
        return False # Falha fechada

    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        new_end_dt = new_start_dt + timedelta(minutes=duration_minutes)

        # 1. Verificar contra o horário de funcionamento do salão
        target_date_local = new_start_dt.date()
        day_of_week_name = WEEKDAY_MAP_DB.get(target_date_local.weekday())
        work_days = salon_data.get('dias_trabalho', [])
        
        if not day_of_week_name or day_of_week_name not in work_days:
            logging.warning(f"[Verificação de Conflito] Falha: {target_date_local} é um dia de folga.")
            return False # Conflito (dia de folga)

        start_hour_str = salon_data.get('horario_inicio', '09:00')
        end_hour_str = salon_data.get('horario_fim', '18:00')
        start_work_time = datetime.strptime(start_hour_str, '%H:%M').time()
        end_work_time = datetime.strptime(end_hour_str, '%H:%M').time()

        day_start_dt = local_tz.localize(datetime.combine(target_date_local, start_work_time))
        day_end_dt = local_tz.localize(datetime.combine(target_date_local, end_work_time))

        if new_start_dt < day_start_dt or new_end_dt > day_end_dt:
            logging.warning(f"[Verificação de Conflito] Falha: Horário ({new_start_dt.time()} - {new_end_dt.time()}) fora do expediente.")
            return False # Conflito (fora do horário de trabalho)

        # 2. Coletar todos os outros períodos ocupados (Híbrido)
        busy_periods = []
        
        # Define a janela de busca (o dia inteiro, para garantir)
        day_start_utc = day_start_dt.astimezone(pytz.utc)
        day_end_utc = day_end_dt.astimezone(pytz.utc)

        # --- FONTE 1: FIRESTORE (Outros Agendamentos Horalis) ---
        try:
            agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
            query = agendamentos_ref.where("startTime", ">=", day_start_utc).where("startTime", "<", day_end_utc)
            busy_periods_docs = query.stream()
            
            for doc in busy_periods_docs:
                # <<< AQUI ESTÁ A LÓGICA DE IGNORAR >>>
                if doc.id == ignore_firestore_id:
                    continue # Pula o próprio agendamento que estamos movendo
                    
                data = doc.to_dict()
                if data.get('startTime') and data.get('endTime'):
                    busy_periods.append({
                        "start": data['startTime'].astimezone(local_tz),
                        "end": data['endTime'].astimezone(local_tz)
                    })
        except Exception as e:
            logging.error(f"Erro ao buscar agendamentos do Firestore (is_slot_available): {e}")
            return False # Falha fechada

        # --- FONTE 2: GOOGLE CALENDAR (Eventos Pessoais) ---
        refresh_token = salon_data.get("google_refresh_token")
        if salon_data.get("google_sync_enabled") and refresh_token:
            google_service = get_google_calendar_service(refresh_token)
            if google_service:
                try:
                    events_result = google_service.events().list(
                        calendarId='primary', 
                        timeMin=day_start_dt.isoformat(), 
                        timeMax=day_end_dt.isoformat(),
                        singleEvents=True,
                        timeZone=LOCAL_TIMEZONE
                    ).execute()
                    
                    for event in events_result.get('items', []):
                        # <<< AQUI ESTÁ A LÓGICA DE IGNORAR (Google) >>>
                        if ignore_google_event_id and event.get('id') == ignore_google_event_id:
                            continue # Pula o próprio evento do Google que estamos movendo

                        start_str = event['start'].get('dateTime')
                        end_str = event['end'].get('dateTime')
                        transparency = event.get('transparency') 
                        
                        if start_str and end_str and transparency != 'transparent':
                            busy_periods.append({
                                "start": datetime.fromisoformat(start_str).astimezone(local_tz),
                                "end": datetime.fromisoformat(end_str).astimezone(local_tz)
                            })
                except Exception as e:
                    logging.error(f"Erro ao buscar eventos do Google (is_slot_available): {e}")
                    return False # Falha fechada

        # 3. Verificação Final de Conflito
        for event in busy_periods:
            # Verifica se o NOVO slot (new_start_dt/new_end_dt) colide com algum 'event'
            if new_start_dt < event['end'] and new_end_dt > event['start']:
                logging.warning(f"[Verificação de Conflito] Falha: Conflito detectado com evento das {event['start'].time()} às {event['end'].time()}.")
                return False # Conflito!

        # 4. Se passou por tudo, o slot está livre
        logging.info(f"[Verificação de Conflito] Sucesso: Slot {new_start_dt.time()} está livre.")
        return True

    except Exception as e:
        logging.exception(f"Erro inesperado em 'is_slot_available':")
        return False # Falha fechada (assume que está ocupado se der erro)
# --- <<< FIM DA ADIÇÃO >>> ---