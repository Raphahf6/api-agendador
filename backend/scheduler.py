# backend/scheduler.py
import logging
import os
import firebase_admin
from firebase_admin import credentials, firestore
from datetime import datetime, timedelta
import pytz # Para lidar com fusos horários consistentemente
from dotenv import load_dotenv

# Carrega variáveis de ambiente
load_dotenv()

# --- NOSSOS MÓDULOS ---
import backend.services.email_service as email_service

# --- CONFIGURAÇÃO ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
REMINDER_WINDOW_MINUTES_BEFORE = 60
QUERY_INTERVAL_MINUTES = 10
# (Configuração do fuso, movida para dentro da inicialização do Firebase)

# --- INICIALIZAÇÃO DO FIREBASE (Standalone) ---
try:
    if not firebase_admin._apps:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        if not os.path.exists(cred_path) and os.path.exists("../credentials.json"):
             cred_path = "../credentials.json"
        elif not os.path.exists(cred_path) and os.path.exists("backend/credentials.json"):
             cred_path = "backend/credentials.json"

        if not os.path.exists(cred_path):
            raise FileNotFoundError(f"Credencial Firebase não encontrada: {cred_path}")

        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        logging.info(f"[Scheduler] Firebase Admin SDK inicializado com: {cred_path}")
    
    db = firestore.client()
    TARGET_TZ = pytz.timezone("America/Sao_Paulo")
except Exception as e:
    logging.error(f"[Scheduler] Falha CRÍTICA ao inicializar Firebase: {e}")
    db = None
    TARGET_TZ = None
# --- FIM DA INICIALIZAÇÃO ---


# --- TAREFA 1: Enviar Lembretes de Agendamento ---
def find_and_send_reminders():
    """Busca agendamentos que precisam de lembrete e os envia."""
    if not db or not TARGET_TZ:
        logging.error("[Scheduler/Lembretes] Dependências não inicializadas. Saindo.")
        return

    logging.info("[Scheduler/Lembretes] Iniciando busca por lembretes...")

    try:
        now_utc = datetime.now(pytz.utc)
        reminder_start_utc = now_utc + timedelta(minutes=REMINDER_WINDOW_MINUTES_BEFORE)
        reminder_end_utc = reminder_start_utc + timedelta(minutes=QUERY_INTERVAL_MINUTES)

        logging.info(f"[Scheduler/Lembretes] Buscando agendamentos entre {reminder_start_utc.isoformat()} e {reminder_end_utc.isoformat()}")

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

        for doc in appointments_to_remind:
            try:
                logging.info(f"[Scheduler/Lembretes] Processando agendamento ID: {doc.id}")
                data = doc.to_dict()

                customer_email = data.get("customerEmail")
                customer_name = data.get("customerName")
                service_name = data.get("serviceName")
                start_time_dt = data.get("startTime") # Vem como datetime UTC
                salon_name = data.get("salonName")
                # <<< NOVO: Pega o salao_id para o link >>>
                salao_id = data.get("salaoId") 

                if not all([customer_email, customer_name, service_name, start_time_dt, salon_name, salao_id]):
                    logging.warning(f"[Scheduler/Lembretes] Dados incompletos para agendamento {doc.id}. Pulando.")
                    skipped_count += 1
                    continue

                logging.info(f"[Scheduler/Lembretes] Enviando lembrete para {customer_email}...")
                success = email_service.send_reminder_email_to_customer(
                    customer_email=customer_email,
                    customer_name=customer_name,
                    service_name=service_name,
                    start_time_iso=start_time_dt.isoformat(), # Envia como ISO string UTC
                    salon_name=salon_name,
                    salao_id=salao_id # <<< Passa o salao_id para o link
                )

                if success:
                    logging.info(f"[Scheduler/Lembretes] Lembrete enviado para {doc.id}. Atualizando Firestore...")
                    doc.reference.update({"reminderSent": True})
                    sent_count += 1
                else:
                    logging.error(f"[Scheduler/Lembretes] Falha ao enviar lembrete para {doc.id} (e-mail: {customer_email}).")
                    error_count += 1

            except Exception as e:
                logging.exception(f"[Scheduler/Lembretes] Erro ao processar agendamento individual {doc.id}: {e}")
                error_count += 1

        logging.info(f"[Scheduler/Lembretes] Busca concluída. Enviados: {sent_count}, Pulados: {skipped_count}, Erros: {error_count}")

    except Exception as e:
        logging.exception(f"[Scheduler/Lembretes] Erro CRÍTICO durante a busca/envio de lembretes: {e}")


# --- <<< NOVA TAREFA: Enviar E-mails de Reengajamento (Clientes Inativos) >>> ---
def find_and_send_reengagement_emails():
    """
    Busca clientes que não agendam há 60 dias e envia e-mail de reengajamento.
    Roda uma vez por dia.
    """
    if not db or not TARGET_TZ:
        logging.error("[Scheduler/Inativos] Dependências não inicializadas. Saindo.")
        return

    logging.info("[Scheduler/Inativos] Iniciando busca por clientes inativos...")

    try:
        # 1. Definir a janela de "inatividade" (ex: 60 dias atrás)
        now_utc = datetime.now(pytz.utc)
        # Queremos clientes cuja 'ultima_visita' foi entre 60 e 61 dias atrás
        # Isso garante que o e-mail só seja enviado UMA VEZ
        inactive_start_date = (now_utc - timedelta(days=61)).replace(hour=0, minute=0, second=0)
        inactive_end_date = (now_utc - timedelta(days=60)).replace(hour=23, minute=59, second=59)

        logging.info(f"[Scheduler/Inativos] Buscando clientes com última visita entre {inactive_start_date.isoformat()} e {inactive_end_date.isoformat()}")

        # 2. Buscar TODOS os salões primeiro
        saloes_ref = db.collection('cabeleireiros')
        saloes_docs = saloes_ref.stream()

        sent_count = 0
        error_count = 0

        # Loop 1: Para cada salão
        for salao in saloes_docs:
            salao_id = salao.id
            salao_data = salao.to_dict()
            salon_name = salao_data.get("nome_salao", "Seu Salão")
            
            logging.info(f"[Scheduler/Inativos] Verificando Salão: {salon_name} ({salao_id})")

            # 3. Query: Busca clientes inativos *dentro* desse salão
            clientes_ref = salao.reference.collection('clientes')
            clientes_inativos = clientes_ref.where(
                filter=firestore.FieldFilter('ultima_visita', '>=', inactive_start_date)
            ).where(
                filter=firestore.FieldFilter('ultima_visita', '<=', inactive_end_date)
            ).stream()

            # Loop 2: Para cada cliente inativo encontrado
            for cliente in clientes_inativos:
                try:
                    cliente_data = cliente.to_dict()
                    customer_email = cliente_data.get("email")
                    customer_name = cliente_data.get("nome", "Cliente")

                    if not customer_email or customer_email.lower() == 'n/a':
                        logging.warning(f"[Scheduler/Inativos] Cliente {cliente.id} é inativo, mas não possui e-mail. Pulando.")
                        continue

                    logging.info(f"[Scheduler/Inativos] Enviando e-mail de reengajamento para {customer_email}...")
                    
                    # 4. Preparar e Enviar o E-mail Promocional
                    subject = f"Estamos com saudades, {customer_name}! 🎁"
                    message_html = (
                        f"<p>Faz um tempo que você não aparece no <strong>{salon_name}</strong>!</p>"
                        "<p>Estamos com saudades e gostaríamos de te ver novamente. Que tal reservar um horário?</p>"
                        "<p>Estamos te esperando!</p>"
                        "<p><em>(Opcional: Você pode adicionar um cupom aqui, ex: Use VOLTA10 para 10% OFF)</em></p>"
                    )
                    
                    success = email_service.send_promotional_email_to_customer(
                        customer_email=customer_email,
                        customer_name=customer_name,
                        salon_name=salon_name,
                        custom_subject=subject,
                        custom_message_html=message_html,
                        salao_id=salao_id # Passa o ID para o link "Agendar Novamente"
                    )

                    if success:
                        sent_count += 1
                        # Registrar o envio no histórico do cliente
                        cliente.reference.collection('registros').document().set({
                            "tipo": "Reengajamento",
                            "data_envio": firestore.SERVER_TIMESTAMP,
                            "assunto": subject,
                            "enviado_por": "Scheduler"
                        })
                    else:
                        error_count += 1
                
                except Exception as e:
                    logging.exception(f"[Scheduler/Inativos] Erro ao processar cliente individual {cliente.id}: {e}")
                    error_count += 1
        
        logging.info(f"[Scheduler/Inativos] Busca concluída. E-mails de reengajamento enviados: {sent_count}, Erros: {error_count}")

    except Exception as e:
        logging.exception(f"[Scheduler/Inativos] Erro CRÍTICO durante a busca/envio de reengajamento: {e}")
# --- <<< FIM DA NOVA TAREFA >>> ---


# --- Ponto de Entrada do Script ---
if __name__ == "__main__":
    logging.info("[Scheduler] Script iniciado manualmente ou via Cron.")
    
    # --- Chama as duas tarefas ---
    find_and_send_reminders()
    find_and_send_reengagement_emails() 
    
    logging.info("[Scheduler] Script finalizado.")