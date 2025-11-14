from pydantic import BaseModel, Field, EmailStr, field_validator
from typing import List, Optional, Any, Dict
from datetime import datetime
from uuid import UUID 

# --- Modelos de Serviço e Salão ---
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

# --- NOVO MODELO: Profissional (Equipe) .
class Professional(BaseModel):
    id: Optional[str] = None
    nome: str
    cargo: str = "Profissional" # ex: Barbeiro, Manicure
    foto_url: Optional[str] = None
    ativo: bool = True
    horario_trabalho: Optional[Dict[str, Any]] = None
    servicos: List[str] = []
    
class SalonPublicDetails(BaseModel):
    # --- Campos Core & Cores ---
    nome_salao: str
    tagline: Optional[str] = None
    url_logo: Optional[str] = None
    cor_primaria: str = "#9daa9d"
    cor_secundaria: str = "#FFFFFF"
    
    # --- Mapeamento de Campos de Conteúdo (Microsite) ---
    telefone: Optional[str] = Field(None, description="Número de contato principal (WhatsApp).") 
    horario_trabalho_detalhado: Dict[str, Dict[str, Any]] = Field({}, description="Agenda detalhada (segunda, terça, etc).")
    
    endereco_completo: Optional[str] = Field(None, description="Endereço completo para exibição.")
    formas_pagamento: Optional[str] = Field(None, description="Formas de pagamento aceitas pelo salão.")
    fotos_carousel: List[Dict[str, str]] = Field([], description="Imagens para o carrossel.")
    comodidades: Dict[str, bool] = Field({}, description="Comodidades disponíveis (wifi, café, etc).")
    redes_sociais: Dict[str, Optional[str]] = Field({}, description="Links para redes sociais.")
    
    # --- Campos do Agendamento/Pagamento ---
    servicos: List[Any] = Field([], description="Lista de serviços disponíveis.")
    
    # 🌟 ATUALIZADO: Lista de Profissionais para o Microsite 🌟
    profissionais: List[Professional] = Field([], description="Lista de profissionais da equipe.")
    
    mp_public_key: Optional[str] = None
    sinal_valor: Optional[float] = 0.0

    class Config:
        populate_by_name = True
    
class OwnerRegisterRequest(BaseModel):
    nome_salao: str
    whatsapp: str
    email: EmailStr
    cpf: str
    uid: str  
    
class ClientDetail(BaseModel): # Admin (Painel)
    id: str
    nome_salao: str
    tagline: Optional[str] = None
    calendar_id: Optional[str] = None
    dias_trabalho: List[str] = []
    horario_inicio: Optional[str] = None
    horario_fim: Optional[str] = None
    servicos: List[Service] = []
    url_logo: Optional[str] = None
    
    # --- Cores e Branding ---
    cor_primaria: Optional[str] = None
    cor_secundaria: Optional[str] = None
    cor_gradiente_inicio: Optional[str] = None
    cor_gradiente_fim: Optional[str] = None
    email_footer_message: Optional[str] = None 
    
    # --- Assinatura e Marketing ---
    subscriptionStatus: Optional[str] = None
    trialEndsAt: Optional[datetime] = None
    marketing_cota_total: Optional[int] = 100 
    marketing_cota_usada: Optional[int] = 0
    marketing_cota_reset_em: Optional[datetime] = None
    
    # --- Pagamento ---
    mp_public_key: Optional[str] = None
    sinal_valor: Optional[float] = 0.0
    
    # --- Horários Detalhados ---
    horario_trabalho_detalhado: Optional[Dict[str, Any]] = Field(
        None,
        description="Estrutura detalhada de horários de funcionamento por dia, incluindo almoço."
    )

    # --- Novos Campos Microsite (Admin) ---
    telefone: Optional[str] = Field(None, description="WhatsApp/Telefone principal.")
    endereco_completo: Optional[str] = Field(None, description="Endereço completo para o mapa.")
    redes_sociais: Optional[Dict[str, Optional[str]]] = Field(default_factory=dict)
    comodidades: Optional[Dict[str, bool]] = Field(default_factory=dict)
    formas_pagamento: Optional[str] = Field(None, description="Texto descrevendo formas de pagamento.")
    fotos_carousel: Optional[List[Dict[str, str]]] = Field(default_factory=list)

    class Config:
        extra = "ignore"

class NewClientData(BaseModel):
    nome_salao: str
    numero_whatsapp: str = Field(..., pattern=r"^\+55\d{10,11}$")
    calendar_id: str
    tagline: str = "Bem-vindo(a) ao seu Horalis!"
    dias_trabalho: List[str] = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday']
    horario_inicio: str = '09:00'
    horario_fim: str = '18:00'
    url_logo: Optional[str] = None
    cor_primaria: str = "#9daa9d" 
    cor_secundaria: str = "#FFFFFF"

    @field_validator('numero_whatsapp', mode='after')
    @classmethod
    def strip_plus_sign_for_storage(cls, value: str) -> str:
        if value.startswith('+'):
            return value[1:] 
        return value
    

# --- 🌟 ATUALIZADO: Modelo de Agendamento Público 🌟 ---
class Appointment(BaseModel):
    salao_id: str
    service_id: str
    start_time: str # Formato ISO
    customer_name: str = Field(..., min_length=2)
    customer_email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?\d{10,11}$")
    cliente_id: Optional[str] = None
    
    # NOVOS CAMPOS (Opcionais para compatibilidade)
    professional_id: Optional[str] = None
    professional_name: Optional[str] = None

# --- Modelo de Agendamento Manual ---
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
    # Opcional: Adicionar professional_id aqui também se quiser agendamento manual por profissional no futuro

# --- OUTROS MODELOS DE SUPORTE ---

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
    salonName: Optional[str] = "Salão"

class DashboardDataResponse(BaseModel):
    agendamentos_foco_valor: int
    novos_clientes_valor: int
    receita_estimada: str
    chart_data: List[Dict[str, Any]] 

class MarketingMassaBody(BaseModel):
    salao_id: str
    subject: str = Field(..., min_length=5)
    message: str = Field(..., min_length=10)
    segmento: str = "todos"
    
# 🌟 ATUALIZADO: Modelo de Pagamento com Agendamento 🌟
class AppointmentPaymentPayload(BaseModel):
    # Dados do Agendamento
    salao_id: str
    service_id: str
    start_time: str 
    customer_name: str = Field(..., min_length=2)
    customer_email: str = Field(..., pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(..., pattern=r"^(?:\+55)?\d{10,11}$")
    device_session_id: Optional[str] = None
    
    # 🌟 NOVOS CAMPOS 🌟
    professional_id: Optional[str] = None
    professional_name: Optional[str] = None
    
    # Dados do Pagamento
    token: Optional[str] = None
    issuer_id: Optional[str] = None
    payment_method_id: str
    transaction_amount: float 
    installments: Optional[int] = None
    payer: PayerData
    device_id: Optional[str] = None 
    
class PagamentoSettingsBody(BaseModel):
    mp_public_key: Optional[str] = None
    sinal_valor: Optional[float] = 0.0