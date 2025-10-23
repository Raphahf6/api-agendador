# backend/main.py (Versão Refatorada)
import logging
import os
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# Importa os nossos novos módulos de rotas
from .routers import public_routes, admin_routes

# Configuração do logging
logging.basicConfig(level=logging.INFO)

# --- INICIALIZAÇÃO DO FIREBASE ---
# Esta inicialização deve acontecer antes de qualquer chamada 'db = firestore.client()'
# nos módulos importados (como core.db ou routers.admin_routes).
try:
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
    cred = credentials.Certificate(cred_path)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
        logging.info(f"Firebase Admin SDK inicializado com: {cred_path}")
    db = firestore.client() # Embora o 'db' seja inicializado nos módulos, tê-lo aqui é seguro.
except Exception as e:
    logging.error(f"Falha CRÍTICA ao inicializar Firebase: {e}")
    # Numa aplicação real, poderíamos querer que a aplicação falhe ao iniciar se o DB não ligar.
# --- FIM DA INICIALIZAÇÃO ---

# Cria a instância principal do FastAPI
app = FastAPI(
    title="API Horalis Agendamento",
    description="Backend para o sistema de agendamento Horalis",
    version="1.0.0"
)

# --- CONFIGURAÇÃO DO CORS ---
origins = [
    "http://localhost:5173", # Admin Frontend
    "http://127.0.0.1:5173",
    "http://localhost:5174", # Cliente Frontend
    "http://127.0.0.1:5174",
    "https://skyborne-periodically-yvonne.ngrok-free.dev", # Ngrok (se ainda estiver a usar)
    "https://agendador-jet.vercel.app", # Frontend Vercel
    "https://horalis.rebdigitalsolucoes.com.br" # Domínio personalizado
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Permite todos os métodos (GET, POST, PUT, etc.)
    allow_headers=["*"], # Permite todos os cabeçalhos
)
# --- FIM DO CORS ---


# --- INCLUSÃO DOS ROTEADORES ---
# Inclui todas as rotas públicas (prefixo /api/v1)
app.include_router(public_routes.router, prefix="/api/v1")
# Inclui todas as rotas de admin (prefixo /api/v1/admin)
app.include_router(admin_routes.router, prefix="/api/v1")
# --- FIM DA INCLUSÃO ---


# --- Rota Raiz Principal ---
@app.get("/", tags=["Root"])
def read_root():
    """Endpoint raiz para verificar o estado da API."""
    return {"status": "API Horalis de Agendamento está online e operacional!"}

# --- Outras lógicas de inicialização podem vir aqui ---

# (Todo o código dos endpoints @app.get(...), @app.post(...),
# funções get_current_user, get_hairdresser_data_from_db, etc.,
# e modelos Pydantic foram REMOVIDOS daqui e movidos para
# os seus respectivos ficheiros em 'core/' e 'routers/')

