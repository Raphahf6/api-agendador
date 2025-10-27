# backend/calendar_service.py (Versão Híbrida - Firestore + Google OAuth2)
import logging
import pytz
import os
from datetime import datetime, time, timedelta
from typing import List, Dict, Any, Optional
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

# --- NOVAS CONFIGURAÇÕES OAUTH ---
# (Lê as variáveis de ambiente que você configurou no Render)
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_OAUTH_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET")
# Pedimos 'readonly' pois só queremos *ler* os eventos pessoais para bloquear horários
SCOPES = ['https://www.googleapis.com/auth/calendar.readonly'] 
# --- FIM DA CONFIGURAÇÃO OAUTH ---


# --- NOVA FUNÇÃO HELPER: Criar Serviço Google com OAuth ---
def get_google_calendar_service(refresh_token: str):
    """
    Cria um serviço (service) do Google Calendar autenticado 
    usando o refresh_token (OAuth2) do dono do salão.
    """
    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        logging.error("Credenciais OAuth (Client ID/Secret) não configuradas no ambiente.")
        return None
    
    try:
        # Cria as credenciais a partir do refresh_token salvo
        creds = Credentials.from_authorized_user_info(
            info={
                "refresh_token": refresh_token,
                "client_id": GOOGLE_CLIENT_ID,
                "client_secret": GOOGLE_CLIENT_SECRET,
                "token_uri": "https://oauth2.googleapis.com/token" # Padrão
            },
            scopes=SCOPES
        )
        
        # O token pode estar expirado; o 'creds.refresh(None)' lidaria com isso,
        # mas o 'build' geralmente força a atualização se necessário.
        
        service = build('calendar', 'v3', credentials=creds, cache_discovery=False)
        logging.info("Serviço Google Calendar (OAuth) inicializado com sucesso.")
        return service
        
    except Exception as e:
        # Se o token for revogado pelo usuário, isto irá falhar
        logging.error(f"Falha ao criar serviço Google Calendar com refresh_token: {e}")
        return None
# --- FIM DA FUNÇÃO HELPER ---


# --- Função find_available_slots (ATUALIZADA PARA HÍBRIDO) ---
def find_available_slots(
    salao_id: str, 
    salon_data: dict, # Recebe o dict completo do salão
    service_duration_minutes: int, 
    date_str: str
) -> List[str]:
    """
    Encontra horários disponíveis lendo os agendamentos do FIRESTORE
    E (se ativado) os eventos do GOOGLE CALENDAR do dono do salão.
    """
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
            # Continua mesmo se o Firestore falhar, mas loga o erro
        
        # --- FONTE 2: GOOGLE CALENDAR (Eventos Pessoais) ---
        refresh_token = salon_data.get("google_refresh_token")
        if salon_data.get("google_sync_enabled") and refresh_token:
            logging.info("Sincronização Google Ativa. Buscando eventos do Google Calendar.")
            
            google_service = get_google_calendar_service(refresh_token)
            
            if google_service:
                try:
                    events_result = google_service.events().list(
                        calendarId='primary', # 'primary' refere-se ao calendário principal do usuário
                        timeMin=day_start_dt.isoformat(), # ISO Format com Timezone
                        timeMax=day_end_dt.isoformat(),
                        singleEvents=True,
                        orderBy='startTime',
                        timeZone=LOCAL_TIMEZONE # Garante que o Google use o nosso fuso
                    ).execute()
                    
                    google_events = events_result.get('items', [])
                    
                    for event in google_events:
                        # Ignora eventos "Dia Inteiro" (que não têm 'dateTime')
                        start_str = event['start'].get('dateTime')
                        end_str = event['end'].get('dateTime')
                        
                        # Queremos apenas eventos "Ocupados" (não "Livres")
                        transparency = event.get('transparency') 
                        
                        if start_str and end_str and transparency != 'transparent':
                            g_start = datetime.fromisoformat(start_str).astimezone(local_tz)
                            g_end = datetime.fromisoformat(end_str).astimezone(local_tz)
                            busy_periods.append({"start": g_start, "end": g_end})
                            
                    logging.info(f"Adicionados {len(google_events)} eventos do Google Calendar à lista de ocupados.")
                    
                except HttpError as e:
                    # Se o token for revogado, a API do Google retornará um erro (ex: 401)
                    logging.error(f"Erro na API Google Calendar (Token pode ter sido revogado): {e}")
                    # TODO: No futuro, poderíamos definir 'google_sync_enabled = False' no Firestore aqui
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
    
def create_google_event_with_oauth(
    refresh_token: str, 
    event_data: Dict[str, Any]
) -> bool:
    """
    Cria um evento no Google Calendar do dono do salão usando OAuth2.
    """
    # 1. Obtém o serviço de calendário com permissão de ESCRITA
    google_service = get_google_calendar_service(refresh_token, readonly=False)
    
    if not google_service:
        logging.error("Não foi possível criar o serviço Google (OAuth) para escrita.")
        return False

    try:
        # 2. Monta o corpo do evento para a API do Google
        event_body = {
            'summary': event_data['summary'],
            'description': event_data['description'],
            'start': {
                'dateTime': event_data['start_time_iso'],
                'timeZone': LOCAL_TIMEZONE,
            },
            'end': {
                'dateTime': event_data['end_time_iso'],
                'timeZone': LOCAL_TIMEZONE,
            },
            'attendees': [], # O cliente não é convidado (para não enviar spam)
            'reminders': {
                'useDefault': True,
            },
        }

        # 3. Insere o evento
        event = google_service.events().insert(
            calendarId='primary', 
            body=event_body
        ).execute()
        
        logging.info(f"Evento criado com sucesso no Google Calendar (OAuth). ID: {event.get('id')}")
        return True

    except HttpError as e:
        logging.error(f"Erro HttpError ao criar evento no Google Calendar (OAuth): {e}")
        return False
    except Exception as e:
        logging.exception(f"Erro inesperado ao criar evento no Google Calendar (OAuth):")
        return False
# --- FIM DA NOVA FUNÇÃO ---