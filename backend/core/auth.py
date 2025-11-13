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
    Dependência FastAPI para verificar o token Firebase ID.
    ATUALIZAÇÃO: Não bloqueia mais por assinatura expirada (403). 
    Apenas verifica a identidade para permitir que o frontend carregue e redirecione para o pagamento.
    """

    # --- Checa se é preflight OPTIONS (Sem alteração) ---
    if request.method == "OPTIONS":
        return None

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    if token is None:
         raise credentials_exception

    try:
        # --- Passo 1: Verificar o Token do Firebase Auth ---
        decoded_token = auth.verify_id_token(token)
        user_uid = decoded_token.get("uid")
        
        # --- Passo 2: Verificar existência básica no Firestore ---
        # Isso ainda é útil para garantir que o cadastro foi finalizado
        query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', user_uid)).limit(1)
        docs = list(query.stream())

        if not docs:
            logging.warning(f"Usuário autenticado (UID: {user_uid}) mas sem documento de salão.")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, 
                detail="Cadastro incompleto. Salão não encontrado."
            )

        # 🌟 LÓGICA RELAXADA:
        # Não verificamos mais 'subscriptionStatus' ou 'trialEndsAt' aqui para bloquear a requisição.
        # O Frontend receberá os dados e fará o bloqueio visual.
        
        return decoded_token

    except auth.ExpiredIdTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expirado")
    except auth.InvalidIdTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token inválido")
    except HTTPException as e:
        raise e
    except Exception as e:
        logging.error(f"Erro na verificação de token: {e}")
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno de autenticação")