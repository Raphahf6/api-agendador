# backend/core/models.py
from pydantic import BaseModel, Field, EmailStr 
from typing import List, Optional 
from datetime import datetime
from uuid import UUID 

# --- Modelos Pydantic ---

class Service(BaseModel):
    id: Optional[str] = None
    nome_servico: str
    duracao_minutos: int
    preco: Optional[float] = None
    descricao: Optional[str] = None

# --- NOVO MODELO: Cliente (para o CRM) ---
class Cliente(BaseModel):
    id: Optional[str] = None 
    profissional_id: Optional[str] = None 
    nome: str
    email: EmailStr
    whatsapp: str
    data_cadastro: Optional[datetime] = None
    ultima_visita: Optional[datetime] = None
# --- FIM DO NOVO MODELO ---


class SalonPublicDetails(BaseModel):
    nome_salao: str
    tagline: Optional[str] = None
    url_logo: Optional[str] = None
    cor_primaria: str = "#6366F1"
    cor_secundaria: str = "#EC4899"
    cor_gradiente_inicio: str = "#A78BFA"
    cor_gradiente_fim: str = "#F472B6"
    servicos: List[Service] = []

class ClientDetail(BaseModel): # Admin
    id: str
    nome_salao: str
    tagline: Optional[str] = None
    calendar_id: Optional[str] = None
    dias_trabalho: List[str] = []
    horario_inicio: Optional[str] = None
    horario_fim: Optional[str] = None
    servicos: List[Service] = []
    url_logo: Optional[str] = None
    cor_primaria: Optional[str] = None
    cor_secundaria: Optional[str] = None
    cor_gradiente_inicio: Optional[str] = None
    cor_gradiente_fim: Optional[str] = None
    # Campos de Assinatura
    subscriptionStatus: Optional[str] = None
    trialEndsAt: Optional[datetime] = None

class NewClientData(BaseModel): # Admin
    nome_salao: str
    numero_whatsapp: str = Field(..., pattern=r"^\+55\d{10,11}$")
    calendar_id: str
    
    # Adicionamos valores padrão para os campos que o frontend não envia
    tagline: str = "Bem-vindo(a) ao seu Horalis!"
    dias_trabalho: List[str] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    horario_inicio: str = '09:00'
    horario_fim: str = '18:00'
    url_logo: Optional[str] = None
    
    # Cores padrão
    cor_primaria: str = "#6366F1" 
    cor_secundaria: str = "#EC4899"
    cor_gradiente_inicio: str = "#A78BFA"
    cor_gradiente_fim: str = "#F472B6"

# --- MODIFICADO: Modelo Appointment (Adicionado cliente_id) ---
class Appointment(BaseModel): # Cliente Final (Payload para POST /agendamentos)
    salao_id: str
    service_id: str
    start_time: str # Formato ISO
    customer_name: str = Field(..., min_length=2)
    customer_email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?\d{10,11}$")
    cliente_id: Optional[str] = None # NOVO CAMPO
# --- FIM DA MODIFICAÇÃO ---

class ManualAppointmentData(BaseModel):
    salao_id: str
    start_time: str # ISO string
    duration_minutes: int
    customer_name: str = Field(..., min_length=2)
    customer_phone: Optional[str] = None
    customer_email: Optional[EmailStr] = None
    service_name: str = Field(..., min_length=3)
    service_id: Optional[str] = None
    service_price:Optional[float] = None