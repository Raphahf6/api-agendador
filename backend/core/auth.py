# backend/core/auth.py
import logging
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from firebase_admin import auth, firestore
from google.cloud.firestore import FieldFilter

# --- <<< NOVOS IMPORTS >>> ---
from core.db import db # Importa a instância do DB
import pytz
from datetime import datetime
# --- <<< FIM DOS NOVOS IMPORTS >>> ---


# Define o esquema de autenticação.
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    """
    Dependência FastAPI para verificar o token Firebase ID E O STATUS DA ASSINATURA.
    """

    # --- Checa se é preflight OPTIONS (Sem alteração) ---
    if request.method == "OPTIONS":
        logging.debug("OPTIONS request received, bypassing token validation.")
        return None

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials / Token missing or invalid",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if token is None:
         logging.warning("Authentication token not provided for non-OPTIONS request.")
         raise credentials_exception

    try:
        # --- Passo 1: Verificar o Token do Firebase Auth ---
        # logging.debug(f"Verifying token...") # Opcional
        decoded_token = auth.verify_id_token(token)
        user_uid = decoded_token.get("uid")
        user_email = decoded_token.get("email")
        
        # --- Passo 2: Verificar Status da Assinatura no Firestore ---
        
        # 2a. Encontra o documento do salão baseado no UID do token
        # Importante: Certifique-se que 'db' e 'FieldFilter' estão importados
        query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', user_uid)).limit(1)
        docs = list(query.stream())

        if not docs:
            logging.warning(f"Usuário autenticado (UID: {user_uid}) mas sem documento de salão.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Nenhum salão encontrado para esta conta. Processo de cadastro pode estar incompleto."
            )

        # 2b. Pega os dados e verifica o status
        salao_data = docs[0].to_dict()
        status_assinatura = salao_data.get("subscriptionStatus")

        # 2c. Caso 1: Assinatura "active" (Paga)
        if status_assinatura == "active":
            return decoded_token # <<< SUCESSO

        # 2d. Caso 2: Assinatura "trialing" (Teste Gratuito)
        if status_assinatura == "trialing":
            trial_ends_at = salao_data.get("trialEndsAt")
            
            # 🌟 BLINDAGEM DE DATA (CORREÇÃO DO ERRO) 🌟
            # Se veio do banco como String, converte para Datetime
            if isinstance(trial_ends_at, str):
                try:
                    trial_ends_at = datetime.fromisoformat(trial_ends_at)
                except ValueError:
                    logging.error(f"Erro ao converter data de trial: {trial_ends_at}")
                    trial_ends_at = None # Falha segura
            
            # Garante que a data tenha fuso horário (se não tiver, assume UTC)
            if trial_ends_at and trial_ends_at.tzinfo is None:
                trial_ends_at = trial_ends_at.replace(tzinfo=pytz.utc)

            # Agora a comparação é segura: Data vs Data
            if trial_ends_at and trial_ends_at > datetime.now(pytz.utc):
                return decoded_token # <<< SUCESSO (TESTE VÁLIDO)
            else:
                logging.warning(f"Acesso BLOQUEADO para {user_email} (Status: trialing expirado)")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, 
                    detail="Seu período de teste gratuito expirou. Por favor, assine um plano para continuar."
                )
        
        # 2e. Caso 3: Outros status
        logging.warning(f"Acesso BLOQUEADO para {user_email} (Status: {status_assinatura})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Sua assinatura não está ativa."
        )

    except auth.ExpiredIdTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except auth.InvalidIdTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Token inválido")
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Unexpected error during token verification: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno de autenticação")