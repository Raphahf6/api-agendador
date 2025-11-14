# backend/main.py (Versão Refatorada)
import logging
import os
import firebase_admin
from firebase_admin import credentials, firestore
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv # Para carregar o .env localmente

# Carrega variáveis de ambiente (como RESEND_API_KEY) do ficheiro .env
load_dotenv() 

# Importa os nossos novos módulos de rotas
# Agora importamos os dois routers do admin_routes
from routers import public_routes, admin_routes, financial_routes, stock_routes, team_routes

# (Serviços que são importados pelos routers)
from services import calendar_service as calendar_service
from services import email_service as email_service

# Configuração do logging
logging.basicConfig(level=logging.INFO)

# --- INICIALIZAÇÃO DO FIREBASE ---
# (Esta lógica permanece a mesma, garantindo que o db seja inicializado)
try:
    cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
    if not os.path.exists(cred_path) and os.path.exists("backend/credentials.json"):
         cred_path = "backend/credentials.json"
         
    cred = credentials.Certificate(cred_path)
    if not firebase_admin._apps:
        firebase_admin.initialize_app(cred)
        logging.info(f"Firebase Admin SDK inicializado com: {cred_path}")
    db = firestore.client()
    
    # Injeta a instância 'db' nos módulos que a utilizam
    from core import db as core_db_module
    core_db_module.db = db
    
except Exception as e:
    logging.error(f"Falha CRÍTICA ao inicializar Firebase: {e}")
# --- FIM DA INICIALIZAÇÃO ---

# Cria a instância principal do FastAPI
app = FastAPI(
    title="API Horalis Agendamento",
    description="Backend para o sistema de agendamento Horalis",
    version="1.1.0" # Versionamento
)

# --- CONFIGURAÇÃO DO CORS ---
origins = [
    "http://localhost:5173", # Admin Frontend (local)
    "http://localhost:5174", # Cliente Frontend (local)
    "https://horalis.rebdigitalsolucoes.com.br", # Domínio personalizado
    "https://api-agendador.onrender.com", # A própria API
    "https://horalis.app",
    "https://www.horalis.app",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"], 
)
# --- FIM DO CORS ---


# --- INCLUSÃO DOS ROTEADORES ---
# 1. Rotas Públicas (Agendamento do Cliente Final)
app.include_router(public_routes.router, prefix="/api/v1")

# 2. Rotas Protegidas do Admin (Painel do Salão)
# (Inclui /user/salao-id, /clientes, /servicos, /calendario/eventos, /google/auth/start)
app.include_router(admin_routes.router, prefix="/api/v1")

# 3. Rota de Callback do Google (NÃO PROTEGIDA)
# (Inclui /google/auth/callback)
app.include_router(admin_routes.callback_router, prefix="/api/v1")
# --- FIM DA INCLUSÃO ---

app.include_router(admin_routes.webhook_router, prefix="/api/v1")

app.include_router(admin_routes.auth_router, prefix="/api/v1")

app.include_router(financial_routes.router, prefix="/api/v1") # <--- Adicione
app.include_router(stock_routes.router, prefix="/api/v1") # <--- Adicione
app.include_router(team_routes.router, prefix="/api/v1")

# --- Rota Raiz Principal ---
@app.get("/", tags=["Root"])
def read_root():
    """Endpoint raiz para verificar o estado da API."""
    return {"status": "API Horalis de Agendamento está online e operacional!"}