# backend/core/auth.py
import logging
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from firebase_admin import auth, firestore

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
    Usado para proteger os endpoints do admin.
    Ignora a verificação para requisições OPTIONS (preflight).
    Retorna os dados do usuário decodificados se o token for válido E a assinatura estiver ativa.
    """

    # --- Checa se é preflight OPTIONS (Sem alteração) ---
    if request.method == "OPTIONS":
        logging.debug("OPTIONS request received, bypassing token validation.")
        return None
    # --- FIM DA CHECAGEM ---

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials / Token missing or invalid",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if token is None:
         logging.warning("Authentication token not provided for non-OPTIONS request.")
         raise credentials_exception

    try:
        # --- Passo 1: Verificar o Token do Firebase Auth (Sua lógica original) ---
        logging.debug(f"Verifying token (first 10 chars): {token[:10]}...")
        decoded_token = auth.verify_id_token(token)
        user_uid = decoded_token.get("uid")
        user_email = decoded_token.get("email")
        
        logging.info(f"Token verificado para user: {user_email} (UID: {user_uid})")

        # --- <<< NOVO: Passo 2: Verificar Status da Assinatura no Firestore >>> ---
        
        # 2a. Encontra o documento do salão baseado no UID do token
        query = db.collection('cabeleireiros').where('ownerUID', '==', user_uid).limit(1)
        docs = list(query.stream())

        if not docs:
            # Usuário existe no Auth, mas não tem salão no Firestore (cadastro incompleto/erro)
            logging.warning(f"Usuário autenticado (UID: {user_uid}) mas sem documento de salão no Firestore.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Nenhum salão encontrado para esta conta. Processo de cadastro pode estar incompleto."
            )

        # 2b. Pega os dados e verifica o status
        salao_data = docs[0].to_dict()
        status_assinatura = salao_data.get("subscriptionStatus")

        # 2c. Caso 1: Assinatura "active" (Paga)
        if status_assinatura == "active":
            # (Opcional) Poderia verificar 'paidUntil' aqui, mas o webhook já deve garantir isso.
            logging.info(f"Acesso concedido para {user_email} (Status: active)")
            return decoded_token # <<< SUCESSO: Deixa o usuário passar

        # 2d. Caso 2: Assinatura "trialing" (Teste Gratuito)
        if status_assinatura == "trialing":
            trial_ends_at = salao_data.get("trialEndsAt") # Isso é um Timestamp do Firestore
            
            # Compara o 'trialEndsAt' com a hora atual em UTC
            if trial_ends_at and trial_ends_at > datetime.now(pytz.utc):
                logging.info(f"Acesso concedido para {user_email} (Status: trialing)")
                return decoded_token # <<< SUCESSO (TESTE): Deixa o usuário passar
            else:
                # Trial expirou!
                logging.warning(f"Acesso BLOQUEADO para {user_email} (Status: trialing expirado)")
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN, 
                    detail="Seu período de teste gratuito expirou. Por favor, assine um plano para continuar."
                )
        
        # 2e. Caso 3: "pending", "cancelled", "rejected", None, ou qualquer outra coisa
        logging.warning(f"Acesso BLOQUEADO para {user_email} (Status: {status_assinatura})")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, 
            detail="Sua assinatura não está ativa. Por favor, complete o pagamento ou renove sua assinatura para acessar o painel."
        )
        # --- <<< FIM DA VERIFICAÇÃO DE ASSINATURA >>> ---

    except auth.ExpiredIdTokenError:
        logging.warning("Expired token received.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.InvalidIdTokenError as e:
        logging.warning(f"Invalid token received: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except HTTPException as e:
        # Repassa os erros 403 (Forbidden) que nós criamos
        raise e
    except Exception as e:
        # Captura outros erros
        logging.error(f"Unexpected error during token verification: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno ao verificar autenticação",
        )