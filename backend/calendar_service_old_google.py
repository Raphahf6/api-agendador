# backend/calendar_service.py (Versão Completa com Dados do Cliente na Descrição)
import datetime
import logging
import pytz # Para fusos horários
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from tenacity import retry, stop_after_attempt, wait_fixed # Para retentativas

logging.basicConfig(level=logging.INFO)

# --- Configurações ---
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPES = ['https://www.googleapis.com/auth/calendar']
# Defina o fuso horário local corretamente (essencial!)
LOCAL_TIMEZONE = 'America/Sao_Paulo'
# Mapeamento de dias da semana (Python 0=Segunda) para nomes no DB
WEEKDAY_MAP_DB = {
    0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday',
    4: 'friday', 5: 'saturday', 6: 'sunday'
}
# Intervalo em minutos para gerar os slots disponíveis
SLOT_INTERVAL_MINUTES = 15

# --- Autenticação ---
try:
    creds = service_account.Credentials.from_service_account_file(
            SERVICE_ACCOUNT_FILE, scopes=SCOPES)
    service = build('calendar', 'v3', credentials=creds)
    logging.info("Conexão com Google Calendar API estabelecida.")
except Exception as e:
    logging.error(f"Falha CRÍTICA ao conectar com Google Calendar API: {e}")
    service = None

# --- Funções com Proteção de Rede ---

# Função find_available_slots (Mantida como a última versão funcional)
@retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
def find_available_slots(calendar_id: str, service_duration_minutes: int, work_days: list, start_hour_str: str, end_hour_str: str, date_str: str):
    """Encontra horários disponíveis APENAS para o dia especificado (date_str)."""
    if not service: return []
    available_slots = []
    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        target_date_local = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        day_of_week_name = WEEKDAY_MAP_DB.get(target_date_local.weekday())
        if not day_of_week_name or day_of_week_name not in work_days:
            logging.info(f"Dia de folga ou inválido ({date_str}, {day_of_week_name}).")
            return []

        start_work_time = datetime.datetime.strptime(start_hour_str, '%H:%M').time()
        end_work_time = datetime.datetime.strptime(end_hour_str, '%H:%M').time()
        day_start_dt = local_tz.localize(datetime.datetime.combine(target_date_local, start_work_time))
        day_end_dt = local_tz.localize(datetime.datetime.combine(target_date_local, end_work_time))

        now_local = datetime.datetime.now(local_tz)
        current_minute = now_local.minute
        minutes_to_next_interval = SLOT_INTERVAL_MINUTES - (current_minute % SLOT_INTERVAL_MINUTES)
        start_search_today = now_local.replace(second=0, microsecond=0) + datetime.timedelta(minutes=minutes_to_next_interval)
        search_from = max(start_search_today, day_start_dt) if target_date_local == now_local.date() else day_start_dt

        if search_from >= day_end_dt: return []

        logging.info(f"Buscando eventos em '{calendar_id}' de {day_start_dt.isoformat()} a {day_end_dt.isoformat()}")
        events_result = service.events().list(
            calendarId=calendar_id, timeMin=day_start_dt.isoformat(), timeMax=day_end_dt.isoformat(),
            singleEvents=True, orderBy='startTime'
        ).execute()
        busy_periods = events_result.get('items', [])
        logging.info(f"Encontrados {len(busy_periods)} eventos.")

        last_event_end = search_from
        for event in busy_periods:
            if 'dateTime' not in event['start'] or 'dateTime' not in event['end']: continue
            event_start = datetime.datetime.fromisoformat(event['start']['dateTime']).astimezone(local_tz)
            potential_slot = last_event_end
            while potential_slot + datetime.timedelta(minutes=service_duration_minutes) <= event_start:
                if potential_slot >= day_start_dt and potential_slot < day_end_dt:
                    available_slots.append(potential_slot.isoformat())
                potential_slot += datetime.timedelta(minutes=SLOT_INTERVAL_MINUTES)
            last_event_end = max(last_event_end, datetime.datetime.fromisoformat(event['end']['dateTime']).astimezone(local_tz))

        potential_slot = last_event_end
        while potential_slot + datetime.timedelta(minutes=service_duration_minutes) <= day_end_dt:
            if potential_slot >= day_start_dt:
                available_slots.append(potential_slot.isoformat())
            potential_slot += datetime.timedelta(minutes=SLOT_INTERVAL_MINUTES)

        final_slots = sorted(list(set(available_slots)))
        logging.info(f"Retornando {len(final_slots)} horários para {date_str}.")
        return final_slots

    except HttpError as e:
        if e.resp.status == 403: logging.error(f"ERRO DE PERMISSÃO (403) para Calendar ID: '{calendar_id}'. Verifique compartilhamento.")
        else: logging.error(f"Erro de rede (Calendar API): {e}")
        raise
    except ValueError as ve:
        logging.error(f"Erro de formato data/hora: {ve}")
        return []
    except Exception as e:
        logging.exception(f"Erro inesperado no cálculo de slots:")
        return []


# Função create_event ATUALIZADA
@retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
def create_event(
    calendar_id: str,
    service_name: str,
    start_time_str: str,
    duration_minutes: int,
    # --- NOVOS PARÂMETROS ---
    customer_name: str | None = None,
    customer_phone: str | None = None
):
    """Cria um evento na agenda, incluindo dados do cliente e com retentativas."""
    if not service:
        logging.error("Google Calendar API não inicializada. Impossível criar evento.")
        return False
        
    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        
        try:
             start_time = datetime.datetime.fromisoformat(start_time_str).astimezone(local_tz)
        except ValueError:
             logging.error(f"Formato de start_time inválido recebido: {start_time_str}")
             return False
             
        end_time = start_time + datetime.timedelta(minutes=duration_minutes)

        # --- DESCRIÇÃO ATUALIZADA ---
        description = f"Agendado pela plataforma.\n" # Mantém a linha original
        if customer_name and customer_name != "Cliente": # Adiciona nome se existir e não for o fallback
            description += f"Cliente: {customer_name}\n"
        if customer_phone and customer_phone != "Não informado": # Adiciona telefone se existir e não for o fallback
            description += f"Telefone: {customer_phone}"
        # --- FIM DA ATUALIZAÇÃO ---

        event = {
            'summary': service_name,
            'description': description.strip(), # Usa a nova descrição, removendo espaços extras
            'start': {'dateTime': start_time.isoformat(), 'timeZone': LOCAL_TIMEZONE},
            'end': {'dateTime': end_time.isoformat(), 'timeZone': LOCAL_TIMEZONE},
        }
        
        logging.info(f"Criando evento em '{calendar_id}': {service_name} @ {start_time.isoformat()}")
        created_event = service.events().insert(calendarId=calendar_id, body=event).execute()
        logging.info(f"Evento criado com sucesso: {created_event.get('htmlLink')}")
        return True
        
    except HttpError as e:
        logging.error(f"Erro de rede na API do Calendar ao criar evento (tentativa em andamento): {e}")
        raise # Levanta para ser tratado no main.py
    except Exception as e:
        logging.exception(f"Erro inesperado ao criar evento:")
        return False # Retorna falha para o main.py