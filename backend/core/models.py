# backend/core/models.py
from pydantic import BaseModel, Field, EmailStr 
from typing import List, Optional, Any, Dict
from datetime import datetime
from uuid import UUID 

# --- Modelos de Serviço e Salão (Existentes) ---
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
    mp_public_key: Optional[str] = None
    sinal_valor: Optional[float] = 0.0

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
    subscriptionStatus: Optional[str] = None
    trialEndsAt: Optional[datetime] = None
    marketing_cota_total: Optional[int] = 100 # Importe a constante ou defina 100
    marketing_cota_usada: Optional[int] = 0
    marketing_cota_reset_em: Optional[datetime] = None
    mp_public_key: Optional[str] = None
    sinal_valor: Optional[float] = 0.0

class NewClientData(BaseModel): # Admin
    nome_salao: str
    numero_whatsapp: str = Field(..., pattern=r"^\+55\d{10,11}$")
    calendar_id: str
    tagline: str = "Bem-vindo(a) ao seu Horalis!"
    dias_trabalho: List[str] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    horario_inicio: str = '09:00'
    horario_fim: str = '18:00'
    url_logo: Optional[str] = None
    cor_primaria: str = "#6366F1" 
    cor_secundaria: str = "#EC4899"
    cor_gradiente_inicio: str = "#A78BFA"
    cor_gradiente_fim: str = "#F472B6"

# --- Modelo de Agendamento Público (Existente) ---
class Appointment(BaseModel):
    salao_id: str
    service_id: str
    start_time: str # Formato ISO
    customer_name: str = Field(..., min_length=2)
    customer_email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?\d{10,11}$")
    cliente_id: Optional[str] = None

# --- Modelo de Agendamento Manual (Existente) ---
class ManualAppointmentData(BaseModel):
    salao_id: str
    start_time: str 
    duration_minutes: int
    customer_name: str = Field(..., min_length=2)
    customer_phone: Optional[str] = None
    customer_email: Optional[EmailStr] = None
    service_name: str = Field(..., min_length=3)
    service_id: Optional[str] = None
    service_price:Optional[float] = None
    cliente_id: Optional[str] = None


# --- <<< MODELOS NOVOS (Movidos do admin_routes.py) >>> ---

class Cliente(BaseModel):
    id: Optional[str] = None 
    profissional_id: Optional[str] = None 
    nome: str
    email: EmailStr
    whatsapp: str
    data_cadastro: Optional[datetime] = None
    ultima_visita: Optional[datetime] = None

class EmailPromocionalBody(BaseModel):
    cliente_id: str
    salao_id: str
    subject: str = Field(..., min_length=5)
    message: str = Field(..., min_length=10)

class ClienteListItem(BaseModel):
    id: str
    nome: str
    email: str
    whatsapp: str
    data_cadastro: Optional[datetime] = None
    ultima_visita: Optional[datetime] = None

class CalendarEvent(BaseModel):
    id: str
    title: str
    start: datetime
    end: datetime
    backgroundColor: Optional[str] = None
    borderColor: Optional[str] = None
    extendedProps: Optional[dict] = None

class ReagendamentoBody(BaseModel):
    new_start_time: str 

class PayerIdentification(BaseModel):
    type: str
    number: str

class PayerData(BaseModel):
    email: EmailStr
    identification: Optional[PayerIdentification] = None 

class UserPaidSignupPayload(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=6)
    nome_salao: str = Field(..., min_length=2)
    numero_whatsapp: str
    token: Optional[str] = None
    issuer_id: Optional[str] = None
    payment_method_id: str
    transaction_amount: float
    installments: Optional[int] = None
    payer: PayerData
    device_id: Optional[str] = None

class NotaManualBody(BaseModel):
    salao_id: str
    cliente_id: str
    nota_texto: str = Field(..., min_length=1)

class TimelineItem(BaseModel):
    id: str
    tipo: str
    data_evento: datetime
    dados: Dict[str, Any]

class HistoricoAgendamentoItem(BaseModel):
    id: str
    serviceName: str
    startTime: datetime
    durationMinutes: int
    servicePrice: Optional[float] = None
    status: str
    
class ClienteDetailsResponse(BaseModel):
    cliente: Dict[str, Any]
    historico_agendamentos: List[TimelineItem]

class DashboardDataResponse(BaseModel):
    agendamentos_foco_valor: int
    novos_clientes_valor: int
    receita_estimada: str
    chart_data: List[Dict[str, Any]] # Alterado para Dict genérico

class MarketingMassaBody(BaseModel):
    salao_id: str
    subject: str = Field(..., min_length=5)
    message: str = Field(..., min_length=10)
    segmento: str = "todos"
    
    
class AppointmentPaymentPayload(BaseModel):
    # Dados do Agendamento (do formulário)
    salao_id: str
    service_id: str
    start_time: str # Formato ISO
    customer_name: str = Field(..., min_length=2)
    customer_email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?\d{10,11}$")
    
    # Dados do Pagamento (do Brick)
    token: Optional[str] = None
    issuer_id: Optional[str] = None
    payment_method_id: str
    transaction_amount: float # Valor do SINAL
    installments: Optional[int] = None
    payer: PayerData
    device_id: Optional[str] = None 
    
class PagamentoSettingsBody(BaseModel):
    # Nota: Não precisamos mais do mp_public_key aqui, pois o OAuth já o salva.
    # Mas vamos mantê-lo para a UX do admin, se ele quiser digitá-lo manualmente como fallback.
    mp_public_key: Optional[str] = None
    sinal_valor: Optional[float] = 0.0