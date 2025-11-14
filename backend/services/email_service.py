import os
import logging
import resend 
from datetime import datetime
from dotenv import load_dotenv

# Importa o 'ZoneInfo'
try:
    from zoneinfo import ZoneInfo
except ImportError:
    try:
        from pytz import timezone as ZoneInfo
        logging.info("Usando 'pytz' como fallback para ZoneInfo.")
    except ImportError:
        logging.error("Nem 'zoneinfo' nem 'pytz' encontrados. A conversão de fuso horário falhará.")
        ZoneInfo = lambda x: None # Fallback seguro

load_dotenv() 
logging.basicConfig(level=logging.INFO)

# 🌟 CORREÇÃO CRÍTICA: Inicialização do Resend com a Chave da API 🌟
# (Certifique-se de que RESEND_API_KEY está no seu arquivo .env)
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
if not RESEND_API_KEY:
    logging.warning("RESEND_API_KEY não está configurada no .env! O envio de e-mails falhará.")
else:
    resend.api_key = RESEND_API_KEY
    logging.info("Serviço de e-mail (Resend) inicializado.")

try:
    TARGET_TZ = ZoneInfo("America/Sao_Paulo")
except Exception:
    TARGET_TZ = None

# E-mail verificado no Resend
SENDER_EMAIL_ADDRESS = "Agendamentos-Horalis@horalis.app"
FRONTEND_BASE_URL = "https://horalis.app" 


# --- Função HELPER INTERNA para formatar a hora ---
def _format_time_to_brt(start_time_iso: str) -> str:
    if not TARGET_TZ:
        return start_time_iso 
    try:
        start_time_dt_aware = datetime.fromisoformat(start_time_iso)
        start_time_dt_local = start_time_dt_aware.astimezone(TARGET_TZ)
        return start_time_dt_local.strftime("%d/%m/%Y às %H:%M")
    except (ValueError, TypeError) as e:
        logging.warning(f"Não foi possível converter fuso para {start_time_iso}: {e}")
        return start_time_iso

# --- Função HELPER INTERNA para o CSS Base ---
def _get_base_css() -> str:
    """Retorna o CSS base para os e-mails dos clientes."""
    return """
        body { font-family: 'Helvetica Neue', Helvetica, Arial, sans-serif; background-color: #f4f4f4; color: #333; margin: 0; padding: 0; }
        .container { max-width: 600px; margin: 20px auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }
        h1 { color: #0E7490; font-size: 24px; border-bottom: 2px solid #eee; padding-bottom: 10px; }
        p { line-height: 1.6; margin-bottom: 15px; }
        .detail { background-color: #f0f8ff; padding: 10px; border-radius: 4px; border-left: 5px solid #0E7490; }
        .footer { text-align: center; margin-top: 20px; font-size: 12px; color: #888; }
        .button {
            display: inline-block;
            background-color: #0E7490; /* Seu Ciano 800 */
            color: #ffffff;
            padding: 12px 24px;
            margin-top: 15px;
            text-decoration: none;
            border-radius: 6px;
            font-weight: bold;
        }
    """

# --- Função HELPER INTERNA para o Rodapé com Link ---
def _get_footer_with_link(salao_id: str) -> str:
    """Gera o HTML do rodapé com o link público de agendamento."""
    public_url = f"{FRONTEND_BASE_URL}/agendar/{salao_id}"
    
    return f"""
        <div style="text-align: center; margin-top: 25px; padding-top: 20px; border-top: 1px solid #eee;">
            <a href="{public_url}" class="button" style="color: #ffffff;">
                Ver Minha Página de Agendamento
            </a>
        </div>
        <div class="footer">
            Você pode acessar seu painel aqui: <a href="{FRONTEND_BASE_URL}/login" style="color: #0E7490;">{FRONTEND_BASE_URL}/login</a>
        </div>
    """

# =========================================================================
# === FUNÇÃO 1: E-mail de Boas-Vindas (Trial) ===
# =========================================================================

def send_welcome_email_to_salon(
    salon_email: str, 
    salon_name: str, 
    salao_id: str,
    login_email: str 
) -> bool:
    """Envia um e-mail de boas-vindas e confirmação de ativação da conta."""
    
    subject = f"✨ Bem-vindo(a) à Horalis Pro, {salon_name}!"
    from_address = f"Equipe Horalis <{SENDER_EMAIL_ADDRESS}>"
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            {_get_base_css()}
            h1 {{ color: #06b6d4; }} /* Ciano mais claro */
        </style>
    </head>
    <body>
        <div class="container">
            <h1 style="color: #06b6d4;">Parabéns, sua conta de Teste está ativa!</h1>
            <p>Olá, <strong>{salon_name}</strong>!</p>
            <p>É um prazer tê-lo(a) a bordo. Seu período de 7 dias de teste gratuito começou.</p>
            
            <p>Aqui estão os dados da sua conta e os próximos passos:</p>
            
            <div class="detail" style="background-color: #e0f7fa; border-left: 5px solid #06b6d4;">
                <strong>Seu ID de Login (WhatsApp):</strong> {salao_id}<br>
                <strong>Seu E-mail de Notificação:</strong> {login_email}<br>
                <strong>Plano:</strong> Horalis Pro (Teste Gratuito)
            </div>
            
            <p style="margin-top: 20px; font-weight: bold;">
                🎉 O primeiro passo é personalizar sua página de agendamento!
            </p>
            
            <div style="text-align: center; margin: 30px 0;">
                <a href="{FRONTEND_BASE_URL}/login" class="button" style="background-color: #06b6d4; color: #ffffff;">
                    Acessar Meu Painel Agora
                </a>
            </div>

            <p style="text-align: center; font-size: 14px; color: #666; margin-top: 20px;">
                Seu link público para clientes é: <a href="{FRONTEND_BASE_URL}/agendar/{salao_id}" style="color: #0E7490;">horalis.app/agendar/{salao_id}</a>
            </p>

            <div class="footer">
                Este e-mail foi enviado automaticamente pelo sistema Horalis.
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address, 
            "to": [salon_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de BOAS-VINDAS enviado com sucesso para {salon_email}.")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail de BOAS-VINDAS para {salon_email}: {e}")
        return False

# =========================================================================
# === FUNÇÃO 2: E-mail para o SALÃO (Novo Agendamento) ===
# =========================================================================
def send_confirmation_email_to_salon(
    salon_email: str, 
    salon_name: str, 
    customer_name: str, 
    client_phone: str,
    service_name: str, 
    start_time_iso: str
) -> bool:
    
    formatted_time = _format_time_to_brt(start_time_iso)
    subject = f"✅ NOVO AGENDAMENTO: {service_name} às {formatted_time}"
    from_address = f"Horalis Agendamentos <{SENDER_EMAIL_ADDRESS}>"
    
    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head><style>{_get_base_css()}</style></head>
    <body>
        <div class="container">
            <h1>Novo Agendamento - Horalis</h1>
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
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address, 
            "to": [salon_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de confirmação (para SALÃO) enviado com sucesso para {salon_email}.")
        return True
        
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail (para SALÃO) {salon_email}: {e}")
        return False

# =========================================================================
# === 🌟 NOVA FUNÇÃO 3: E-mail para o PROFISSIONAL (Novo Agendamento) 🌟 ===
# =========================================================================
def send_new_appointment_email_to_professional(
    pro_email: str,
    pro_name: str,
    customer_name: str,
    customer_phone: str,
    service_name: str,
    start_time_iso: str,
    salon_name: str
) -> bool:
    """
    Envia notificação para o profissional sobre um novo agendamento.
    """
    
    formatted_time = _format_time_to_brt(start_time_iso)
    subject = f"📅 Novo Agendamento: {customer_name} às {formatted_time}"
    # O e-mail é enviado "em nome" do salão
    from_address = f"{salon_name} <{SENDER_EMAIL_ADDRESS}>"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <style>
            {_get_base_css()}
            /* Um leve toque de cor diferente para o profissional */
            h1 {{ color: #0891B2; }} 
            .detail {{ background-color: #f0f9ff; border-left: 5px solid #0891B2; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Novo Agendamento</h1>
            <p>Olá, <strong>{pro_name}</strong>!</p>
            <p>Um novo agendamento foi atribuído a você no(a) <strong>{salon_name}</strong>:</p>
            
            <div class="detail">
                <strong>Cliente:</strong> {customer_name}<br>
                <strong>Telefone:</strong> {customer_phone}<br>
                <strong>Serviço:</strong> {service_name}<br>
                <strong>Data e Hora:</strong> {formatted_time}
            </div>
            
            <p style="margin-top: 20px;">Por favor, verifique sua agenda no painel Horalis.</p>
            
            <div class="footer">
                Enviado automaticamente por Horalis.
            </div>
        </div>
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address,
            "to": [pro_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de NOTIFICAÇÃO (para PROFISSIONAL) enviado com sucesso para {pro_email}.")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail (para PROFISSIONAL) {pro_email}: {e}")
        return False

# =========================================================================
# === FUNÇÃO 4: E-mail de Confirmação para o CLIENTE ===
# =========================================================================
def send_confirmation_email_to_customer(
    customer_email: str,
    customer_name: str,
    service_name: str,
    start_time_iso: str,
    salon_name: str,
    salao_id: str
) -> bool:
    
    formatted_time = _format_time_to_brt(start_time_iso)
    subject = f"Agendamento Confirmado! ✅ {service_name} em {salon_name}"
    from_address = f"{salon_name} <{SENDER_EMAIL_ADDRESS}>"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head><style>{_get_base_css()}</style></head>
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
            
            {_get_footer_with_link(salao_id)}
        </div>
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address,
            "to": [customer_email],
            "subject": subject,
            "html": html_content,
        })
        logging.info(f"E-mail de confirmação (para CLIENTE) enviado com sucesso para {customer_email}.")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail (para CLIENTE) {customer_email}: {e}")
        return False

# --- FUNÇÃO 5: E-mail de Cancelamento para o CLIENTE ---
def send_cancellation_email_to_customer(
    customer_email: str, customer_name: str, service_name: str,
    start_time_iso: str, salon_name: str, salao_id: str
) -> bool:
    
    formatted_time = _format_time_to_brt(start_time_iso)
    subject = f"Agendamento Cancelado ❌ {service_name} em {salon_name}"
    from_address = f"{salon_name} <{SENDER_EMAIL_ADDRESS}>"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <style>
            {_get_base_css()}
            h1 {{ color: #D32F2F; }}
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
            
            {_get_footer_with_link(salao_id)}
        </div>
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address, "to": [customer_email], "subject": subject, "html": html_content,
        })
        logging.info(f"E-mail de CANCELAMENTO (para CLIENTE) enviado com sucesso para {customer_email}.")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail de CANCELAMENTO (para CLIENTE) {customer_email}: {e}")
        return False

# --- FUNÇÃO 6: E-mail de Reagendamento para o CLIENTE ---
def send_reschedule_email_to_customer(
    customer_email: str, customer_name: str, service_name: str,
    salon_name: str, old_start_time_iso: str, new_start_time_iso: str,
    salao_id: str
) -> bool:
    
    old_formatted_time = _format_time_to_brt(old_start_time_iso)
    new_formatted_time = _format_time_to_brt(new_start_time_iso)
    subject = f"Agendamento Reagendado 🗓️ {service_name} em {salon_name}"
    from_address = f"{salon_name} <{SENDER_EMAIL_ADDRESS}>"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <style>
            {_get_base_css()}
            h1 {{ color: #303F9F; }}
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
            
            {_get_footer_with_link(salao_id)}
        </div>
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address, "to": [customer_email], "subject": subject, "html": html_content,
        })
        logging.info(f"E-mail de REAGENDAMENTO (para CLIENTE) enviado com sucesso para {customer_email}.")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail de REAGENDAMENTO (para CLIENTE) {customer_email}: {e}")
        return False

# --- FUNÇÃO 7: E-mail de Lembrete para o CLIENTE ---
def send_reminder_email_to_customer(
    customer_email: str, customer_name: str, service_name: str,
    start_time_iso: str, salon_name: str, salao_id: str
) -> bool:
    
    formatted_time = _format_time_to_brt(start_time_iso)
    subject = f"Lembrete de Agendamento ⏰ {service_name} hoje em {salon_name}"
    from_address = f"{salon_name} <{SENDER_EMAIL_ADDRESS}>"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <style>
            {_get_base_css()}
            h1 {{ color: #FFA000; }}
            .detail {{ border-left: 5px solid #FFECB3; background-color: #FFFDE7; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>Lembrete de Agendamento!</h1>
            <p>Olá, <strong>{customer_name}</strong>!</p>
            <p>Este é um lembrete amigável sobre o seu agendamento hoje no(a) <strong>{salon_name}</strong>.</p>
            
            <div class="detail">
                <strong>Serviço:</strong> {service_name}<br>
                <strong>Horário:</strong> {formatted_time}<br>
            </div>
            
            <p style="margin-top: 20px;">Esperamos por você!</p>
            
            {_get_footer_with_link(salao_id)}
        </div>
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address, "to": [customer_email], "subject": subject, "html": html_content,
        })
        logging.info(f"E-mail de LEMBRETE (para CLIENTE) enviado com sucesso para {customer_email}.")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail de LEMBRETE (para CLIENTE) {customer_email}: {e}")
        return False
        
# --- FUNÇÃO 8: E-mail Promocional/Personalizado ---
def send_promotional_email_to_customer(
    customer_email: str, customer_name: str, salon_name: str,
    custom_subject: str, custom_message_html: str, salao_id: str
) -> bool:
    
    subject = f"{custom_subject} - Exclusivo {salon_name}"
    from_address = f"{salon_name} <{SENDER_EMAIL_ADDRESS}>"

    html_content = f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <style>
            {_get_base_css()}
            h1 {{ color: #E91E63; }}
            .detail {{ background-color: #FCE4EC; border-left: 5px solid #FF80AB; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>{custom_subject}</h1>
            <p>Olá, <strong>{customer_name}</strong>!</p>
            <p>A equipe do <strong>{salon_name}</strong> tem uma novidade especial para você:</p>
            
            <div class="detail" style="margin-top: 20px; margin-bottom: 20px; padding: 15px; border-radius: 4px;">
                {custom_message_html}
            </div>
            
            <p>Esperamos te ver em breve!</p>
            
            {_get_footer_with_link(salao_id)}
        </div>
    </body>
    </html>
    """
    
    try:
        if not RESEND_API_KEY: raise Exception("Chave RESEND_API_KEY não configurada")
        result = resend.Emails.send({
            "from": from_address, "to": [customer_email], "subject": subject, "html": html_content,
        })
        logging.info(f"E-mail PROMOCIONAL (de {salon_name}) enviado com sucesso para {customer_email}.")
        return True
    except Exception as e:
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail PROMOCIONAL para {customer_email}: {e}")
        return False