# backend/email_service.py
import os
import logging
import resend # <<< Importa o módulo Resend
from datetime import datetime
from dotenv import load_dotenv
load_dotenv()  # Carrega variáveis de ambiente do .env

logging.basicConfig(level=logging.INFO)

# --- Configuração do Resend no módulo ---
# A biblioteca Resend procura automaticamente por RESEND_API_KEY no ambiente.
# Se a chave for encontrada, o cliente estará pronto para uso.
# Não é mais necessário instanciar a classe Resend().

def send_confirmation_email_to_salon(
    salon_email: str, 
    salon_name: str, 
    customer_name: str, 
    client_phone: str,
    service_name: str, 
    start_time_iso: str
) -> bool:
    """
    Envia um e-mail de confirmação para o salão sobre o novo agendamento.
    Retorna True em caso de sucesso, False em caso de falha.
    """
    # Se a chave RESEND_API_KEY não estiver carregada, a chamada 'resend.Emails.send' falhará,
    # mas não precisamos checar manualmente aqui, vamos direto para o try/except.
        
    # Formata a data para leitura (ex: 24 de Outubro de 2025 às 14:30)
    try:
        start_time_dt = datetime.fromisoformat(start_time_iso)
        # Formata para PT-BR
        formatted_time = start_time_dt.strftime("%d/%m/%Y às %H:%M")
    except ValueError:
        formatted_time = start_time_iso 

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
        # ATENÇÃO: SUBSTITUA 'onboarding@seu-dominio.com' pelo seu domínio verificado
        # Usamos resend.Emails.send diretamente (o módulo)
        result = resend.Emails.send({
            "from": "Horalis Agendamentos <Agendamentos-Horalis@rebdigitalsolucoes.com.br>", 
            "to": [salon_email],
            "subject": subject,
            "html": html_content,
        })
        
        logging.info(f"E-mail de confirmação enviado com sucesso para {salon_email}. Resposta Resend: {result.get('id')}")
        return True
        
    except Exception as e:
        # Se a chave não for encontrada, o erro será capturado aqui (como 'missing_api_key')
        logging.error(f"ERRO RESEND: Falha ao enviar e-mail para {salon_email}: {e}")
        return False