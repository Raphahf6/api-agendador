# backend/scheduler.py
import logging
import os
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import pytz # Para lidar com fusos horários consistentemente
from dotenv import load_dotenv

# Carrega variáveis de ambiente (necessário para credenciais e Resend API Key)
load_dotenv()

# --- NOSSOS MÓDULOS ---
# Importa APENAS o serviço de e-mail (não precisamos de FastAPI, rotas, etc.)
import email_service

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
REMINDER_WINDOW_MINUTES_BEFORE = 60 # Enviar lembrete X minutos antes
QUERY_INTERVAL_MINUTES = 10   # Buscar agendamentos que ocorrem entre X e X+10 minutos a partir de agora + REMINDER_WINDOW

# --- INICIALIZAÇÃO DO FIREBASE (Standalone) ---
try:
    if not firebase_admin._apps:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        # Tenta caminhos alternativos se estiver rodando de diretórios diferentes
        if not os.path.exists(cred_path) and os.path.exists("../credentials.json"):
             cred_path = "../credentials.json" # Se rodar de dentro de /backend
        elif not os.path.exists(cred_path) and os.path.exists("backend/credentials.json"):
             cred_path = "backend/credentials.json" # Se rodar da raiz do projeto

        if not os.path.exists(cred_path):
             raise FileNotFoundError(f"Credencial Firebase não encontrada: {cred_path}")

        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        logging.info(f"[Scheduler] Firebase Admin SDK inicializado com: {cred_path}")
    
    db = firestore.client()
except Exception as e:
    logging.error(f"[Scheduler] Falha CRÍTICA ao inicializar Firebase: {e}")
    db = None
# --- FIM DA INICIALIZAÇÃO ---

def find_and_send_reminders():
    """Busca agendamentos que precisam de lembrete e os envia."""
    if not db:
        logging.error("[Scheduler] Firestore não inicializado. Saindo.")
        return

    logging.info("[Scheduler] Iniciando busca por lembretes...")

    try:
        # 1. Calcular a janela de tempo em UTC
        now_utc = datetime.now(pytz.utc)
        reminder_start_utc = now_utc + timedelta(minutes=REMINDER_WINDOW_MINUTES_BEFORE)
        reminder_end_utc = reminder_start_utc + timedelta(minutes=QUERY_INTERVAL_MINUTES)

        logging.info(f"[Scheduler] Buscando agendamentos entre {reminder_start_utc.isoformat()} e {reminder_end_utc.isoformat()}")

        # 2. Query usando Collection Group
        # Busca em TODAS as subcoleções 'agendamentos'
        # NOTA: Pode ser necessário criar um índice composto no Firestore!
        #      Índice: agendamentos | reminderSent (Asc) | startTime (Asc)
        appointments_to_remind = db.collection_group('agendamentos').where(
            filter=firestore.FieldFilter('reminderSent', '==', False)
        ).where(
            filter=firestore.FieldFilter('startTime', '>=', reminder_start_utc)
        ).where(
            filter=firestore.FieldFilter('startTime', '<', reminder_end_utc)
        ).stream()

        sent_count = 0
        skipped_count = 0
        error_count = 0

        # 3. Processar cada agendamento encontrado
        for doc in appointments_to_remind:
            try:
                logging.info(f"[Scheduler] Processando agendamento ID: {doc.id}")
                data = doc.to_dict()

                customer_email = data.get("customerEmail")
                customer_name = data.get("customerName")
                service_name = data.get("serviceName")
                start_time_dt = data.get("startTime") # Vem como datetime UTC do Firestore
                salon_name = data.get("salonName")

                # Valida se temos todos os dados necessários
                if not all([customer_email, customer_name, service_name, start_time_dt, salon_name]):
                    logging.warning(f"[Scheduler] Dados incompletos para agendamento {doc.id}. Pulando lembrete.")
                    # Marcar como 'enviado' para não tentar de novo? Ou deixar para corrigir?
                    # Por segurança, vamos pular sem marcar como enviado.
                    skipped_count += 1
                    continue

                # Enviar o e-mail
                logging.info(f"[Scheduler] Enviando lembrete para {customer_email}...")
                success = email_service.send_reminder_email_to_customer(
                    customer_email=customer_email,
                    customer_name=customer_name,
                    service_name=service_name,
                    start_time_iso=start_time_dt.isoformat(), # Envia como ISO string UTC
                    salon_name=salon_name
                )

                # Se o e-mail foi enviado, marcar no Firestore
                if success:
                    logging.info(f"[Scheduler] Lembrete enviado para {doc.id}. Atualizando Firestore...")
                    doc.reference.update({"reminderSent": True})
                    sent_count += 1
                else:
                    logging.error(f"[Scheduler] Falha ao enviar lembrete para {doc.id} (e-mail: {customer_email}).")
                    error_count += 1

            except Exception as e:
                logging.exception(f"[Scheduler] Erro ao processar agendamento individual {doc.id}: {e}")
                error_count += 1
                # Continua para o próximo agendamento

        logging.info(f"[Scheduler] Busca concluída. Lembretes enviados: {sent_count}, Pulados: {skipped_count}, Erros: {error_count}")

    except Exception as e:
        logging.exception(f"[Scheduler] Erro CRÍTICO durante a busca/envio de lembretes: {e}")

# --- Ponto de Entrada do Script ---
if __name__ == "__main__":
    logging.info("[Scheduler] Script iniciado manualmente ou via Cron.")
    find_and_send_reminders()
    logging.info("[Scheduler] Script finalizado.")