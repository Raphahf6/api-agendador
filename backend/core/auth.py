# backend/core/auth.py
import logging
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from firebase_admin import auth

# Define o esquema de autenticação.
# "tokenUrl" é um parâmetro necessário para a documentação Swagger/FastAPI,
# mesmo que não o usemos diretamente para obter o token (quem faz isso é o Firebase no frontend).
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") 

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """
    Dependência FastAPI para verificar o token Firebase ID.
    Usado para proteger os endpoints do admin.
    Retorna os dados do usuário decodificados se o token for válido.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token de autenticação não fornecido",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        # Verifica o token usando o Firebase Admin SDK
        decoded_token = auth.verify_id_token(token)
        
        # --- Validação Opcional de Admin ---
        # No futuro, podemos verificar se o e-mail é o seu e-mail de admin:
        # admin_email = "seu-email@gmail.com"
        # if decoded_token.get('email') != admin_email:
        #     logging.warning(f"Tentativa de acesso admin falhou: {decoded_token.get('email')}")
        #     raise HTTPException(
        #         status_code=status.HTTP_403_FORBIDDEN,
        #         detail="Acesso restrito ao administrador da plataforma."
        #     )
        # -------------------------------------
        
        logging.info(f"Token de admin verificado para: {decoded_token.get('email')}")
        return decoded_token

    except auth.ExpiredIdTokenError:
        logging.warning("Token de admin expirado recebido.")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token expirado",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except auth.InvalidIdTokenError as e:
        logging.warning(f"Token de admin inválido recebido: {e}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        # Captura outros erros (ex: problema de rede ao verificar o token)
        logging.error(f"Erro inesperado na verificação do token admin: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro interno ao verificar autenticação",
        )
