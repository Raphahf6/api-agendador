# backend/email_service.py
import os
import logging
import resend 
from datetime import datetime
from dotenv import load_dotenv

# <<< ADICIONADO: Importa o 'ZoneInfo' para lidar com fusos horários >>>
try:
    from zoneinfo import ZoneInfo
except ImportError:
    # Fallback para Python < 3.9 (requer 'pip install pytz')
    try:
        from pytz import timezone as ZoneInfo
        logging.info("Usando 'pytz' como fallback para ZoneInfo.")
    except ImportError:
        logging.error("Nem 'zoneinfo' nem 'pytz' encontrados. A conversão de fuso horário falhará.")
        ZoneInfo = lambda x: None # Define um 'falso' ZoneInfo

load_dotenv()  # Carrega variáveis de ambiente do .env
logging.basicConfig(level=logging.INFO)

# --- Constante de Fuso Horário ---
# Define o fuso de destino (Onde o salão está)
try:
    TARGET_TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TARGET_TZ = None
# --- Fim da Constante ---


# --- Função HELPER INTERNA para formatar a hora ---
def _format_time_to_brt(start_time_iso: str) -> str:
    """Helper para converter ISO string para 'dd/MM/YYYY às HH:mm' (BRT)."""
    if not TARGET_TZ:
        return start_time_iso # Retorna original se o fuso falhou
    try:
        start_time_dt_aware = datetime.fromisoformat(start_time_iso)
        start_time_dt_local = start_time_dt_aware.astimezone(TARGET_TZ)
        return start_time_dt_local.strftime("%d/%m/%Y às %H:%M")
    except (ValueError, TypeError) as e:
        logging.warning(f"Não foi possível converter fuso para {start_time_iso}: {e}")
        return start_time_iso
# --- Fim da Função HELPER ---


# --- FUNÇÃO 1: E-mail para o SALÃO (Sua função original, agora usa o HELPER) ---
def send_confirmation_email_to_salon(
    salon_email: str, 
    salon_name: str, 
    customer_name: str, 
    client_phone: str,
    service_name: str, 
    start_time_iso: str
) -> bool:
    """
    Envia um e-mail de confirmação para o SALÃO sobre o novo agendamento.
    """
    
    # Formata a data para leitura (ex: 24 de Outubro de 2025 às 14:30)
    formatted_time = _format_time_to_brt(start_time_iso)

    subject = f"✅ NOVO AGENDAMENTO para {salon_name}: {service_name} às {formatted_time}"
    
    # Corpo do e-mail em HTML (moderno e responsivo)
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f4f4; color: #333; margin: 0; padding: 0; }}
            .container {{ max-width: 600px; margin: 20px auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
            h1 {{ color: #7c3aed; font-size: 24px; border-bottom: 2px solid #eee; padding-bottom: 10px; }}
            p {{ line-height: 1.6; margin-bottom: 15px; }}
            .detail {{ background-color: #f9f6ff; padding: 10px; border-radius: 4px; border-left: 5px solid #a78bfa; }}
            .footer {{ text-align: center; margin-top: 20px; font-size: 12px; color: #888; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Confirmação de Agendamento - Horalis</h1>
            <p>Olá, <strong>{salon_name}</strong>!</p>
            <p>Um novo serviço foi agendado em sua agenda:</p>
            
            <div class="detail">
                <strong>Serviço:</strong> {service_name}<br>
                <strong>Cliente:</strong> {customer_name}<br>
                <strong>Telefone:</strong> {client_phone}<br>
                <strong>Data e Hora:</strong> {formatted_time}<br>
            </div>
            
            <p style="margin-top: 20px;">Lembre-se de checar sua agenda Horalis para todos os detalhes.</p>
        </div>
        <div class="footer">
            Este e-mail foi enviado automaticamente pelo sistema Horalis.
        </div>
    </body>
    </html>
    """
    
    try:
        result = resend.Emails.send({
            "from": "Horalis Agendamentos <Agendamentos-Horalis@rebdigitalsolucoes.com.br>", 
            "to": [salon_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de confirmação (para SALÃO) enviado com sucesso para {salon_email}. ID: {result.get('id')}")
        return True
        
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail (para SALÃO) {salon_email}: {e}")
        return False


# --- <<< ADICIONADO: FUNÇÃO 2: E-mail de Confirmação para o CLIENTE >>> ---
def send_confirmation_email_to_customer(
    customer_email: str,
    customer_name: str,
    service_name: str,
    start_time_iso: str,
    salon_name: str
) -> bool:
    """
    Envia um e-mail de confirmação para o CLIENTE sobre o novo agendamento.
    """
    formatted_time = _format_time_to_brt(start_time_iso)
    subject = f"Agendamento Confirmado! ✅ {service_name} em {salon_name}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            {_get_base_css()}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Agendamento Confirmado!</h1>
            <p>Olá, <strong>{customer_name}</strong>!</p>
            <p>Seu agendamento no(a) <strong>{salon_name}</strong> foi confirmado com sucesso.</p>
            
            <div class="detail">
                <strong>Serviço:</strong> {service_name}<br>
                <strong>Data e Hora:</strong> {formatted_time}<br>
            </div>
            
            <p style="margin-top: 20px;">Caso precise cancelar ou reagendar, por favor, entre em contato diretamente com o estabelecimento.</p>
        </div>
        <div class="footer">
            Este e-mail foi enviado automaticamente pelo sistema Horalis.
        </div>
    </body>
    </html>
    """
    
    try:
        result = resend.Emails.send({
            "from": "Horalis Agendamentos <Agendamentos-Horalis@rebdigitalsolucoes.com.br>",
            "to": [customer_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de confirmação (para CLIENTE) enviado com sucesso para {customer_email}. ID: {result.get('id')}")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail (para CLIENTE) {customer_email}: {e}")
        return False

# --- <<< ADICIONADO: FUNÇÃO 3: E-mail de Cancelamento para o CLIENTE >>> ---
def send_cancellation_email_to_customer(
    customer_email: str,
    customer_name: str,
    service_name: str,
    start_time_iso: str, # Hora original
    salon_name: str
) -> bool:
    """
    Envia um e-mail de CANCELAMENTO para o CLIENTE.
    """
    formatted_time = _format_time_to_brt(start_time_iso)
    subject = f"Agendamento Cancelado ❌ {service_name} em {salon_name}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            {_get_base_css()}
            h1 {{ color: #D32F2F; }} /* Vermelho para cancelamento */
            .detail {{ border-left: 5px solid #FFCDD2; background-color: #FFF8F8; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Agendamento Cancelado</h1>
            <p>Olá, <strong>{customer_name}</strong>.</p>
            <p>Infelizmente, seu agendamento no(a) <strong>{salon_name}</strong> precisou ser cancelado.</p>
            
            <div class="detail">
                <strong>Serviço Cancelado:</strong> {service_name}<br>
                <strong>Que seria em:</strong> {formatted_time}<br>
            </div>
            
            <p style="margin-top: 20px;">Por favor, entre em contato com o estabelecimento para mais detalhes ou para tentar um novo horário.</p>
        </div>
        <div class="footer">
            Este e-mail foi enviado automaticamente pelo sistema Horalis.
        </div>
    </body>
    </html>
    """
    
    try:
        result = resend.Emails.send({
            "from": "Horalis Agendamentos <Agendamentos-Horalis@rebdigitalsolucoes.com.br>",
            "to": [customer_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de CANCELAMENTO (para CLIENTE) enviado com sucesso para {customer_email}. ID: {result.get('id')}")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail de CANCELAMENTO (para CLIENTE) {customer_email}: {e}")
        return False

# --- <<< ADICIONADO: FUNÇÃO 4: E-mail de Reagendamento para o CLIENTE >>> ---
def send_reschedule_email_to_customer(
    customer_email: str,
    customer_name: str,
    service_name: str,
    salon_name: str,
    old_start_time_iso: str,
    new_start_time_iso: str
) -> bool:
    """
    Envia um e-mail de REAGENDAMENTO para o CLIENTE.
    """
    old_formatted_time = _format_time_to_brt(old_start_time_iso)
    new_formatted_time = _format_time_to_brt(new_start_time_iso)
    subject = f"Agendamento Reagendado 🗓️ {service_name} em {salon_name}"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            {_get_base_css()}
            h1 {{ color: #303F9F; }} /* Azul escuro para reagendamento */
            .detail-old {{ border-left: 5px solid #FFCDD2; background-color: #FFF8F8; padding: 10px; border-radius: 4px; text-decoration: line-through; color: #777; }}
            .detail-new {{ border-left: 5px solid #C8E6C9; background-color: #F8FFF8; padding: 10px; border-radius: 4px; margin-top: 10px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Seu Agendamento Foi Reagendado!</h1>
            <p>Olá, <strong>{customer_name}</strong>!</p>
            <p>Seu agendamento no(a) <strong>{salon_name}</strong> foi alterado.</p>
            
            <p><strong>De:</strong></p>
            <div class="detail-old">
                {service_name} em {old_formatted_time}
            </div>
            
            <p style="margin-top:15px;"><strong>Para:</strong></p>
            <div class="detail-new">
                <strong>{service_name}</strong><br>
                <strong>{new_formatted_time}</strong>
            </div>
            
            <p style="margin-top: 20px;">Caso esta nova data não seja ideal, por favor, entre em contato diretamente com o estabelecimento.</p>
        </div>
        <div class="footer">
            Este e-mail foi enviado automaticamente pelo sistema Horalis.
        </div>
    </body>
    </html>
    """
    
    try:
        result = resend.Emails.send({
            "from": "Horalis Agendamentos <Agendamentos-Horalis@rebdigitalsolucoes.com.br>",
            "to": [customer_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de REAGENDAMENTO (para CLIENTE) enviado com sucesso para {customer_email}. ID: {result.get('id')}")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail de REAGENDAMENTO (para CLIENTE) {customer_email}: {e}")
        return False

# --- <<< ADICIONADO: Helper CSS Base para os templates >>> ---
def _get_base_css() -> str:
    """Retorna o CSS base para os e-mails dos clientes."""
    return """
        body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f4f4; color: #333; margin: 0; padding: 0; }
        .container { max-width: 600px; margin: 20px auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        h1 { color: #7c3aed; font-size: 24px; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        p { line-height: 1.6; margin-bottom: 15px; }
        .detail { background-color: #f9f6ff; padding: 10px; border-radius: 4px; border-left: 5px solid #a78bfa; }
        .footer { text-align: center; margin-top: 20px; font-size: 12px; color: #888; }
    """