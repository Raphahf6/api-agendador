# backend/core/models.py
from pydantic import BaseModel, Field
from typing import List, Optional # Usar 'List' e 'Optional' é uma boa prática
import calendar_service

# --- Modelos Pydantic ---

class Service(BaseModel):
    id: Optional[str] = None # str | None
    nome_servico: str
    duracao_minutos: int
    preco: Optional[float] = None # float | None
    descricao: Optional[str] = None # str | None

class SalonPublicDetails(BaseModel):
    nome_salao: str
    tagline: Optional[str] = None
    url_logo: Optional[str] = None
    cor_primaria: str = "#6366F1"
    cor_secundaria: str = "#EC4899"
    cor_gradiente_inicio: str = "#A78BFA"
    cor_gradiente_fim: str = "#F472B6"
    servicos: List[Service] = [] # list[Service]

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

class NewClientData(BaseModel): # Admin
    nome_salao: str
    numero_whatsapp: str = Field(..., pattern=r"^\+55\d{10,11}$")
    calendar_id: str
    tagline: Optional[str] = None
    dias_trabalho: List[str] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    horario_inicio: str = '09:00'
    horario_fim: str = '18:00'
    url_logo: Optional[str] = None
    cor_primaria: Optional[str] = None
    cor_secundaria: Optional[str] = None
    cor_gradiente_inicio: Optional[str] = None
    cor_gradiente_fim: Optional[str] = None

class Appointment(BaseModel): # Cliente Final
    salao_id: str
    service_id: str
    start_time: str # Formato ISO
    customer_name: str = Field(..., min_length=2)
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?(\d{2})?\d{8,9}$")
