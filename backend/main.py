# backend/main.py (Versão FINAL - Agendamento Público com Nome/Telefone)
from fastapi import FastAPI, HTTPException, Query, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import OAuth2PasswordBearer
from pydantic import BaseModel, Field # Adicionado Field para validação
import logging
import datetime
import firebase_admin
from firebase_admin import credentials, firestore, auth
import os
import re # Para validar telefone

import calendar_service

logging.basicConfig(level=logging.INFO)
app = FastAPI()

# --- CORS ---
origins = [
    "http://localhost:5173", "http://127.0.0.1:5173",
    "http://localhost:5174", "http://127.0.0.1:5174","https://skyborne-periodically-yvonne.ngrok-free.dev",
    "https://agendador-jet.vercel.app"
]
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# --- FIREBASE INIT ---
if not firebase_admin._apps:
    try:
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "credentials.json")
        cred = credentials.Certificate(cred_path)
        firebase_admin.initialize_app(cred)
        logging.info(f"Firebase Admin SDK inicializado com: {cred_path}")
    except Exception as e:
        logging.error(f"Falha CRÍTICA ao inicializar Firebase: {e}")
db = firestore.client()

# --- AUTENTICAÇÃO (Mantida SÓ para Endpoints Admin) ---
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token") # Placeholder

async def get_current_user(token: str = Depends(oauth2_scheme)):
    """Verifica o token Firebase ID e retorna dados do usuário (usado SÓ pelo admin)."""
    if not token: raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token não fornecido")
    try:
        decoded_token = auth.verify_id_token(token)
        # Poderia adicionar validação aqui para garantir que SÓ o seu email de admin pode acessar
        # if decoded_token.get('email') != "SEU_EMAIL_ADMIN@gmail.com":
        #     raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Acesso restrito ao administrador.")
        return decoded_token
    except Exception as e:
        logging.warning(f"Erro na verificação do token admin: {e}")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Token inválido ou expirado: {e}")

# --- Modelos Pydantic ---
class Service(BaseModel):
    id: str | None = None
    nome_servico: str
    duracao_minutos: int
    preco: float | None = None
    descricao: str | None = None

class SalonPublicDetails(BaseModel):
    nome_salao: str
    tagline: str | None = None
    url_logo: str | None = None
    cor_primaria: str | None = "#6366F1"
    cor_secundaria: str | None = "#EC4899"
    cor_gradiente_inicio: str | None = "#A78BFA"
    cor_gradiente_fim: str | None = "#F472B6"
    servicos: list[Service] = []

class ClientDetail(BaseModel): # Admin
    id: str; nome_salao: str; tagline: str | None = None; calendar_id: str | None = None
    dias_trabalho: list[str] = []; horario_inicio: str | None = None; horario_fim: str | None = None
    servicos: list[Service] = []; url_logo: str | None = None; cor_primaria: str | None = None
    cor_secundaria: str | None = None; cor_gradiente_inicio: str | None = None; cor_gradiente_fim: str | None = None

class NewClientData(BaseModel): # Admin
    nome_salao: str; numero_whatsapp: str = Field(..., pattern=r"^\+55\d{10,11}$") # Validação básica
    calendar_id: str; tagline: str | None = None
    dias_trabalho: list[str] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    horario_inicio: str = '09:00'; horario_fim: str = '18:00'; url_logo: str | None = None
    cor_primaria: str | None = None; cor_secundaria: str | None = None
    cor_gradiente_inicio: str | None = None; cor_gradiente_fim: str | None = None

class Appointment(BaseModel): # Cliente Final (Payload para POST /agendamentos)
    salao_id: str
    service_id: str
    start_time: str # Formato ISO: "2025-10-25T10:30:00"
    # --- DADOS DO CLIENTE (OBRIGATÓRIOS AGORA) ---
    customer_name: str = Field(..., min_length=2) # Nome com pelo menos 2 caracteres
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?(\d{2})?\d{8,9}$") # Validação telefone BR

# --- Funções DB ---
def get_hairdresser_data_from_db(salao_id: str):
    # ... (código da função igual ao anterior) ...
    try:
        doc_ref = db.collection('cabeleireiros').document(salao_id); hairdresser_doc = doc_ref.get()
        if not hairdresser_doc.exists: return None
        hairdresser_data = hairdresser_doc.to_dict()
        services_ref = doc_ref.collection('servicos'); services_stream = services_ref.stream()
        services_dict_with_ids = {doc.id: doc.to_dict() for doc in services_stream}
        return {
            "nome_salao": hairdresser_data.get('nome_salao'), "tagline": hairdresser_data.get('tagline'),
            "calendar_id": hairdresser_data.get('calendar_id'), "servicos_data": services_dict_with_ids,
            "dias_trabalho": hairdresser_data.get('dias_trabalho', []), "horario_inicio": hairdresser_data.get('horario_inicio', '09:00'),
            "horario_fim": hairdresser_data.get('horario_fim', '18:00'), "url_logo": hairdresser_data.get('url_logo'),
            "cor_primaria": hairdresser_data.get('cor_primaria', "#6366F1"), "cor_secundaria": hairdresser_data.get('cor_secundaria', "#EC4899"),
            "cor_gradiente_inicio": hairdresser_data.get('cor_gradiente_inicio', "#A78BFA"), "cor_gradiente_fim": hairdresser_data.get('cor_gradiente_fim', "#F472B6")
        }
    except Exception as e: logging.error(f"Erro buscar dados Firestore {salao_id}: {e}"); return None

def get_all_clients_from_db():
     # ... (código da função igual ao anterior) ...
    try:
        clients_ref = db.collection('cabeleireiros').stream(); clients_list = []
        for doc in clients_ref:
            client_data = doc.to_dict()
            clients_list.append(ClientDetail(id=doc.id, servicos=[], **client_data))
        return clients_list
    except Exception as e: logging.error(f"Erro buscar todos clientes: {e}"); return None


# --- Endpoints da API ---
@app.get("/")
def read_root(): return {"status": "API de Agendamento Rodando"}

# Endpoint de Serviços (Público)
@app.get("/saloes/{salao_id}/servicos", response_model=SalonPublicDetails)
def get_salon_services_and_details(salao_id: str):
    # ... (código da função igual ao anterior) ...
    logging.info(f"Buscando detalhes/serviços para: {salao_id}"); salon_data = get_hairdresser_data_from_db(salao_id)
    if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
    services_list_formatted = []
    if salon_data.get("servicos_data"):
        for service_id, service_info in salon_data["servicos_data"].items():
            # Usa **service_info para passar todos os campos (incluindo preco, descricao)
            services_list_formatted.append(Service(id=service_id, **service_info)) 
    # Usa **salon_data para passar todos os campos de personalização
    response_data = SalonPublicDetails(servicos=services_list_formatted, **salon_data) 
    return response_data

# Endpoint de Horários (Público)
@app.get("/saloes/{salao_id}/horarios-disponiveis")
async def get_available_slots_endpoint( # <<< SEM Depends
    salao_id: str,
    service_id: str,
    date: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$")
):
    """Busca horários disponíveis (público)."""
    logging.info(f"Buscando horários para salão {salao_id} em {date}")
    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
        calendar_id = salon_data.get('calendar_id');
        if not calendar_id: raise HTTPException(status_code=500, detail="ID Calendário não configurado.")

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(status_code=404, detail="Serviço não encontrado.")
        duration = service_info.get('duracao_minutos')
        if duration is None: raise HTTPException(status_code=500, detail="Duração do serviço não encontrada.") # Validação extra

        available_slots = calendar_service.find_available_slots(
            calendar_id=calendar_id, service_duration_minutes=duration,
            work_days=salon_data.get('dias_trabalho', []), start_hour_str=salon_data.get('horario_inicio', '09:00'),
            end_hour_str=salon_data.get('horario_fim', '18:00'), date_str=date
        )
        return {"horarios_disponiveis": available_slots}
    except Exception as e:
        logging.exception(f"Erro CRÍTICO no cálculo de slots:")
        raise HTTPException(status_code=500, detail="Erro interno ao calcular horários.")

# Endpoint de Agendamentos (PÚBLICO - recebe nome/telefone no corpo)
@app.post("/agendamentos", status_code=201)
async def create_appointment(appointment: Appointment): # <<< SEM Depends
    """Cria um novo agendamento (público, recebe nome/telefone no corpo)."""
    salao_id = appointment.salao_id; service_id = appointment.service_id; start_time = appointment.start_time
    # --- PEGANDO DADOS DO CORPO DA REQUISIÇÃO (DO MODELO Appointment) ---
    user_name = appointment.customer_name.strip() # Remove espaços extras
    user_phone = appointment.customer_phone
    logging.info(f"Cliente '{user_name}' ({user_phone}) criando agendamento para {salao_id}")
    # --- FIM PEGANDO DADOS ---

    try:
        salon_data = get_hairdresser_data_from_db(salao_id)
        if not salon_data: raise HTTPException(status_code=404, detail="Salão não encontrado")
        calendar_id = salon_data.get('calendar_id')
        if not calendar_id: raise HTTPException(status_code=500, detail="ID Calendário não configurado.")

        service_info = salon_data.get("servicos_data", {}).get(service_id)
        if not service_info: raise HTTPException(status_code=404, detail="Serviço não encontrado.")
        duration = service_info.get('duracao_minutos'); service_name = service_info.get('nome_servico')
        if duration is None or service_name is None:
            raise HTTPException(status_code=500, detail="Dados do serviço incompletos.")

        # Validação extra do telefone (embora Pydantic já valide o formato)
        cleaned_phone = re.sub(r'\D', '', user_phone) # Remove não-dígitos
        if not (10 <= len(cleaned_phone) <= 11): # Valida tamanho básico BR
             raise HTTPException(status_code=400, detail="Formato de telefone inválido após limpeza.")

        success = calendar_service.create_event(
            calendar_id=calendar_id, service_name=service_name, start_time_str=start_time,
            duration_minutes=duration,
            customer_name=user_name, # Passa os dados recebidos
            customer_phone=user_phone # Passa os dados recebidos
        )
        if not success:
             # O calendar_service retorna False em caso de erro na API Google
             raise HTTPException(status_code=500, detail="Falha ao criar evento no calendário. Verifique permissões ou log.")

        # Opcional: Salvar agendamento no Firestore
        # appointment_data = appointment.dict()
        # appointment_data['status'] = 'confirmado'
        # db.collection('agendamentos').add(appointment_data)

        return {"message": f"Agendamento para '{service_name}' criado com sucesso!"}

    except HTTPException as httpe: raise httpe
    except Exception as e:
        logging.exception(f"Erro CRÍTICO ao criar agendamento:")
        raise HTTPException(status_code=500, detail="Erro interno ao criar agendamento.")


# --- ENDPOINTS PROTEGIDOS DO ADMIN (Continuam protegidos) ---
@app.get("/admin/clientes", response_model=list[ClientDetail])
async def list_clients(current_user: dict = Depends(get_current_user)):
    # ... (código completo da função list_clients aqui) ...
    logging.info(f"Admin {current_user.get('email')} solicitou lista de clientes.")
    clients = get_all_clients_from_db()
    if clients is None: raise HTTPException(status_code=500, detail="Erro ao buscar clientes.")
    return clients

@app.get("/admin/clientes/{client_id}", response_model=ClientDetail)
async def get_client_details(client_id: str, current_user: dict = Depends(get_current_user)):
    # ... (código completo da função get_client_details aqui) ...
    admin_email = current_user.get("email"); logging.info(f"Admin {admin_email} detalhes cliente: {client_id}")
    try:
        client_ref = db.collection('cabeleireiros').document(client_id); client_doc = client_ref.get()
        if not client_doc.exists: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado.")
        client_data = client_doc.to_dict()
        services_ref = client_ref.collection('servicos').stream()
        services_list = [Service(id=doc.id, **doc.to_dict()) for doc in services_ref]
        # Usa **client_data para passar todos os campos, Pydantic valida e aplica defaults
        client_details = ClientDetail(id=client_doc.id, servicos=services_list, **client_data) 
        return client_details
    except Exception as e: logging.exception(f"Erro buscar detalhes cliente {client_id}:"); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@app.post("/admin/clientes", response_model=ClientDetail, status_code=status.HTTP_201_CREATED)
async def create_client(client_data: NewClientData, current_user: dict = Depends(get_current_user)):
    # ... (código completo da função create_client aqui) ...
    admin_email = current_user.get("email"); logging.info(f"Admin {admin_email} criando: {client_data.nome_salao}")
    # Removida validação startswith('+') pois Pydantic já valida formato com pattern
    client_id = client_data.numero_whatsapp
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if client_ref.get().exists: raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"Cliente {client_id} já existe.")
        data_to_save = client_data.dict(exclude_unset=True)
        client_ref.set(data_to_save)
        logging.info(f"Cliente '{data_to_save['nome_salao']}' criado ID: {client_id}")
        # Retorna ClientDetail completo (serviços vazio)
        return ClientDetail(id=client_id, servicos=[], **data_to_save)
    except HTTPException as httpe: raise httpe
    except Exception as e: logging.exception(f"Erro ao criar cliente:"); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

@app.put("/admin/clientes/{client_id}", response_model=ClientDetail)
async def update_client(client_id: str, client_update_data: ClientDetail, current_user: dict = Depends(get_current_user)):
    # ... (código completo da função update_client com transação aqui) ...
    admin_email = current_user.get("email"); logging.info(f"Admin {admin_email} atualizando: {client_id}")
    if client_id != client_update_data.id: raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID URL não corresponde aos dados.")
    try:
        client_ref = db.collection('cabeleireiros').document(client_id)
        if not client_ref.get(retry=None, timeout=None).exists: raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Cliente não encontrado.")
        
        client_info = client_update_data.dict(exclude={'servicos', 'id'}, exclude_unset=True) # Exclui campos não enviados
        updated_services = client_update_data.servicos

        @firestore.transactional
        def update_in_transaction(transaction, client_ref, client_info_to_save, services_to_save):
            services_ref = client_ref.collection('servicos')
            old_services_refs = [doc.reference for doc in services_ref.stream(transaction=transaction)]
            transaction.update(client_ref, client_info_to_save) # Usa UPDATE para mesclar
            for old_ref in old_services_refs: transaction.delete(old_ref)
            for service_data in services_to_save:
                 new_service_ref = services_ref.document()
                 service_dict = service_data.dict(exclude={'id'}, exclude_unset=True, exclude_none=True)
                 transaction.set(new_service_ref, service_dict)

        transaction = db.transaction()
        update_in_transaction(transaction, client_ref, client_info, updated_services)
        logging.info(f"Cliente '{client_update_data.nome_salao}' atualizado.")
        
        updated_details = await get_client_details(client_id, current_user) # Busca novamente para garantir consistência
        return updated_details
    except HTTPException as httpe: raise httpe
    except Exception as e: logging.exception(f"Erro CRÍTICO ao atualizar cliente {client_id}:"); raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro interno.")

