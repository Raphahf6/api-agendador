# backend/core/models.py
from pydantic import BaseModel, Field, EmailStr,field_validator
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
    
class DailySchedule(BaseModel):
    # Campos principais de funcionamento
    isOpen: bool = Field(..., description="Indica se o estabelecimento está aberto neste dia.")
    openTime: str = Field(..., description="Horário de abertura (HH:MM).")
    closeTime: str = Field(..., description="Horário de fechamento (HH:MM).")
    
    # Campos de Almoço/Intervalo
    hasLunch: bool = Field(..., description="Indica se há intervalo de almoço.")
    lunchStart: Optional[str] = Field(None, description="Início do almoço (HH:MM).")
    lunchEnd: Optional[str] = Field(None, description="Fim do almoço (HH:MM).")

# --- NOVO MODELO 2: Estrutura para os Dados de Serviço (Apenas para evitar erro no ClientDetail) ---
# Se Service não estiver definido, você precisará defini-lo (usando o seu modelo real)
class Service(BaseModel):
    id: str
    nome: str
    duracao_minutos: int
    valor: float
    # Adicione todos os outros campos que o seu Service real possui.

class SalonPublicDetails(BaseModel):
    nome_salao: str
    tagline: Optional[str] = None
    url_logo: Optional[str] = None
    cor_primaria: str = "#9daa9d"
    cor_secundaria: str = "#FFFFFF"
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
    horario_trabalho_detalhado: Optional[Dict[str, DailySchedule]] = Field(
        None,
        description="Estrutura detalhada de horários de funcionamento por dia, incluindo almoço."
    )

class NewClientData(BaseModel):
    """Modelo para validação e criação de dados de um novo salão/cliente."""

    nome_salao: str
    # O padrão (pattern) continua exigindo o '+' para garantir o formato de entrada correto
    numero_whatsapp: str = Field(..., pattern=r"^\+55\d{10,11}$", description="Número de WhatsApp com DDI +55 e DDD (10 ou 11 dígitos no total).")
    calendar_id: str
    tagline: str = "Bem-vindo(a) ao seu Horalis!"
    dias_trabalho: List[str] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    horario_inicio: str = '09:00'
    horario_fim: str = '18:00'
    url_logo: Optional[str] = None
    cor_primaria: str = "#9daa9d" 
    cor_secundaria: str = "#FFFFFF"

    # NOVO VALIDATOR: Executa após a validação do formato (pattern)
    @field_validator('numero_whatsapp', mode='after')
    @classmethod
    def strip_plus_sign_for_storage(cls, value: str) -> str:
        """Remove o sinal de '+' do número após a validação do formato ser concluída.
        O valor salvo no modelo será '5511...'."""
        
        # Como o pattern garante que o '+' é o primeiro caractere, podemos removê-lo.
        if value.startswith('+'):
            return value[1:] # Retorna a string a partir do segundo caractere
        
        # Caso o pattern fosse mais flexível (o que não é o caso aqui), manteríamos o valor
        return value
    

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
    client_whatsapp_id: str 

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
    device_session_id: Optional[str] = None
    
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
    
    
