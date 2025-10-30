# backend/core/models.py
from pydantic import BaseModel, Field,EmailStr 
from typing import List, Optional 
from datetime import datetime

# --- Modelos Pydantic (Movidos do main.py) ---

class Service(BaseModel):
    id: Optional[str] = None
    nome_servico: str
    duracao_minutos: int
    preco: Optional[float] = None
    descricao: Optional[str] = None

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
    # <<< ADICIONADO: Campos de Assinatura >>>
    subscriptionStatus: Optional[str] = None
    trialEndsAt: Optional[datetime] = None
    # <<< FIM DA ADIÇÃO >>>

class NewClientData(BaseModel): # Admin
    nome_salao: str
    numero_whatsapp: str = Field(..., pattern=r"^\+55\d{10,11}$")
    calendar_id: str
    
    # Adicionamos valores padrão para os campos que o frontend não envia
    tagline: str = "Bem-vindo(a) ao seu Horalis!"
    dias_trabalho: List[str] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    horario_inicio: str = '09:00'
    horario_fim: str = '18:00'
    url_logo: Optional[str] = None # Logo pode ser nulo
    
    # Cores padrão (para evitar 'null' que quebra o frontend)
    cor_primaria: str = "#6366F1" 
    cor_secundaria: str = "#EC4899"
    cor_gradiente_inicio: str = "#A78BFA"
    cor_gradiente_fim: str = "#F472B6"

# --- <<< MODIFICADO: Modelo Appointment >>> ---
class Appointment(BaseModel): # Cliente Final (Payload para POST /agendamentos)
    salao_id: str
    service_id: str
    start_time: str # Formato ISO
    customer_name: str = Field(..., min_length=2)
    # <<< ADICIONADO: 'customer_email' com validação de regex >>>
    customer_email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?(\d{2})?\d{8,9}$")
# --- <<< FIM DA MODIFICAÇÃO >>> ---

class ManualAppointmentData(BaseModel):
    salao_id: str
    start_time: str # ISO string
    duration_minutes: int
    customer_name: str = Field(..., min_length=2)
    customer_phone: Optional[str] = None
    # <<< ADICIONADO: E-mail opcional, com validação de formato >>>
    customer_email: Optional[EmailStr] = None
    service_name: str = Field(..., min_length=3)