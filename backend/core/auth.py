# backend/core/auth.py
import logging
# <<< ADICIONADO: Importa Request >>>
from fastapi import Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer
from firebase_admin import auth
# Removido import não utilizado de calendar_service se não for necessário aqui

# Define o esquema de autenticação.
# <<< ALTERADO: Adicionado auto_error=False >>>
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token", auto_error=False)

# <<< ALTERADO: Função agora recebe 'request' e 'token' pode ser None >>>
async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    logging.info(f"get_current_user called. Method: {request.method}") # Log 1: Método
    logging.info(f"Token received (type: {type(token)}): {str(token)[:10] if token else 'None'}") # Log 2: Token
    """
    Dependência FastAPI para verificar o token Firebase ID.
    Usado para proteger os endpoints do admin.
    Ignora a verificação para requisições OPTIONS (preflight).
    Retorna os dados do usuário decodificados se o token for válido.
    """

    # <<< ADICIONADO: Checa se é preflight OPTIONS >>>
    if request.method == "OPTIONS":
        # Para requisições OPTIONS, bypass token validation.
        # Retorna None ou um valor placeholder seguro.
        # O importante é NÃO levantar erro 401/400.
        logging.debug("OPTIONS request received, bypassing token validation.")
        return None
    # <<< FIM DA ADIÇÃO >>>

    # --- Validação Normal para outros métodos (GET, POST, PUT, DELETE, PATCH) ---

    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials / Token missing or invalid",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # <<< ADICIONADO: Checa se o token veio (necessário por causa do auto_error=False) >>>
    if token is None:
         # Se não é OPTIONS e não tem token, então é Unauthorized
         logging.warning("Authentication token not provided for non-OPTIONS request.")
         raise credentials_exception

    # <<< Bloco try/except original mantido, mas ajustado para o token None já tratado >>>
    try:
        # Verifica o token usando o Firebase Admin SDK
        logging.debug(f"Verifying token (first 10 chars): {token[:10]}...") # Log para debug
        decoded_token = auth.verify_id_token(token)

        # --- Validação Opcional de Admin ---
        # (Seu código de validação de admin comentado pode ficar aqui)
        # -------------------------------------

        logging.info(f"Token verified for user: {decoded_token.get('email')}")
        return decoded_token

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
            detail=f"Token inválido", # Removido {e} da mensagem para não expor detalhes
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        # Captura outros erros
        logging.error(f"Unexpected error during token verification: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno ao verificar autenticação",
        )