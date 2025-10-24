# backend/calendar_service.py (Versão FINAL - Lendo do FIRESTORE)
import datetime
import logging
import pytz # Para fusos horários
from firebase_admin import firestore # Importa o firestore
from typing import List, Dict, Any

# Importa a instância 'db' do nosso módulo core.db
from core.db import db 

logging.basicConfig(level=logging.INFO)

# --- Configurações ---
# (Não precisamos mais do SERVICE_ACCOUNT_FILE ou SCOPES do Google Calendar aqui)
LOCAL_TIMEZONE = 'America/Sao_Paulo'
WEEKDAY_MAP_DB = {
    0: 'monday', 1: 'tuesday', 2: 'wednesday', 3: 'thursday',
    4: 'friday', 5: 'saturday', 6: 'sunday'
}
SLOT_INTERVAL_MINUTES = 15

# --- REMOVIDA a autenticação do Google Calendar ---
# creds = ...
# service = ...

# --- Função find_available_slots (REESCRITA para FIRESTORE) ---
def find_available_slots(
    # calendar_id não é mais necessário, mas o salao_id é (para a query)
    # Vamos assumir que o main.py passará o salao_id
    salao_id: str, 
    service_duration_minutes: int, 
    work_days: list, 
    start_hour_str: str, 
    end_hour_str: str, 
    date_str: str
) -> List[str]:
    """
    Encontra horários disponíveis lendo os agendamentos do FIRESTORE
    para um dia específico.
    """
    available_slots_iso = []
    try:
        local_tz = pytz.timezone(LOCAL_TIMEZONE)
        
        # 1. Validar e Definir Dia Alvo
        try:
            target_date_local = datetime.datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            logging.error(f"Formato de data inválido recebido: {date_str}")
            return []

        # 2. Verificar Dia de Folga
        day_of_week_name = WEEKDAY_MAP_DB.get(target_date_local.weekday())
        if not day_of_week_name or day_of_week_name not in work_days:
            logging.info(f"Dia de folga detectado para {date_str}.")
            return []

        # 3. Validar e Definir Período de Trabalho (com Timezone!)
        try:
            start_work_time = datetime.datetime.strptime(start_hour_str, '%H:%M').time()
            end_work_time = datetime.datetime.strptime(end_hour_str, '%H:%M').time()
        except ValueError:
             logging.error(f"Formato de hora inválido: Início '{start_hour_str}', Fim '{end_hour_str}'")
             return []

        day_start_dt = local_tz.localize(datetime.datetime.combine(target_date_local, start_work_time))
        day_end_dt = local_tz.localize(datetime.datetime.combine(target_date_local, end_work_time))

        # 4. Definir Ponto de Partida da Busca (Considerando Dia Atual)
        now_local = datetime.datetime.now(local_tz)
        current_minute = now_local.minute
        minutes_to_next_interval = SLOT_INTERVAL_MINUTES - (current_minute % SLOT_INTERVAL_MINUTES)
        start_search_today = now_local.replace(second=0, microsecond=0) + datetime.timedelta(minutes=minutes_to_next_interval)
        
        search_from = max(start_search_today, day_start_dt) if target_date_local == now_local.date() else day_start_dt

        if search_from >= day_end_dt:
             logging.info(f"Horário de busca ({search_from}) é após o fim do expediente.")
             return []

        # --- NOVA LÓGICA: BUSCAR AGENDAMENTOS NO FIRESTORE ---
        
        # 5. Buscar Agendamentos Existentes no Firestore para aquele dia
        logging.info(f"Buscando agendamentos no Firestore para '{salao_id}' em {date_str}")
        
        # Define o início e o fim do dia em UTC para a query (Firestore armazena em UTC)
        day_start_utc = day_start_dt.astimezone(pytz.utc)
        day_end_utc = day_end_dt.astimezone(pytz.utc)

        agendamentos_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
        
        # Query: busca agendamentos que começam (startTime) no dia alvo
        query = agendamentos_ref.where("startTime", ">=", day_start_utc).where("startTime", "<", day_end_utc)
        busy_periods_docs = query.stream()
        
        # Converte os documentos do Firestore para um formato utilizável (com timezones locais)
        busy_periods = []
        for doc in busy_periods_docs:
            data = doc.to_dict()
            # Converte os Timestamps do Firestore (que são UTC) de volta para o fuso horário local
            busy_periods.append({
                "start": data['startTime'].astimezone(local_tz),
                "end": data['endTime'].astimezone(local_tz)
            })
        
        logging.info(f"Encontrados {len(busy_periods)} agendamentos no Firestore.")
        # --- FIM DA NOVA LÓGICA ---


        # 6. Calcular Vãos Disponíveis (Iterando pelos Slots Possíveis)
        # (Esta lógica permanece a mesma de antes, mas agora usa os 'busy_periods' do Firestore)
        potential_slot = search_from 

        while potential_slot < day_end_dt:
            slot_end = potential_slot + datetime.timedelta(minutes=service_duration_minutes)

            if slot_end > day_end_dt:
                break 

            is_free = True
            for event in busy_periods:
                event_start = event['start']
                event_end = event['end']

                # Verifica sobreposição
                if potential_slot < event_end and slot_end > event_start:
                    is_free = False
                    # Avança o próximo potencial slot para DEPOIS do fim deste evento
                    potential_slot = event_end
                    # Arredonda para o próximo intervalo
                    minute_offset = potential_slot.minute % SLOT_INTERVAL_MINUTES
                    if minute_offset != 0:
                        potential_slot += datetime.timedelta(minutes=SLOT_INTERVAL_MINUTES - minute_offset)
                    break # Sai do loop interno (eventos)

            if is_free:
                available_slots_iso.append(potential_slot.isoformat())
                potential_slot += datetime.timedelta(minutes=SLOT_INTERVAL_MINUTES)
            
        final_slots = sorted(list(set(available_slots_iso)))
        logging.info(f"Retornando {len(final_slots)} horários (Firestore) para {date_str}.")
        return final_slots

    except Exception as e:
        logging.exception(f"Erro inesperado no cálculo de slots (Firestore):") # Loga traceback
        return [] # Retorna vazio em caso de erro


# --- Função create_event (REMOVIDA) ---
# Esta função não é mais necessária aqui, pois o agendamento
# é agora tratado diretamente no public_routes.py
# (A menos que queiramos manter a sincronização com o Google como "Fase 2")

# def create_event(...):
#     ...