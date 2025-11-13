from fastapi import APIRouter, HTTPException, status, Depends, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta
import pytz
from google.cloud.firestore import FieldFilter
from core.db import db
from core.auth import get_current_user 

router = APIRouter(prefix="/admin/financeiro", tags=["Financeiro"])

# --- Modelos Pydantic ---
class ExpenseCreate(BaseModel):
    description: str
    amount: float
    date: str  # YYYY-MM-DD
    category: str # 'fixa' | 'variavel'
    status: str = 'pending' # 'paid' | 'pending'

class ExpenseResponse(ExpenseCreate):
    id: str

class FinancialSummary(BaseModel):
    total_revenue: float
    total_expenses: float
    net_profit: float
    expenses_list: List[ExpenseResponse]
    chart_data: List[Dict[str, Any]] # Para o gráfico

# --- Rotas ---

@router.post("/despesas", status_code=status.HTTP_201_CREATED)
def create_expense(
    expense: ExpenseCreate,
    current_user: dict = Depends(get_current_user)
):
    """Cria uma nova despesa no Firestore"""
    uid = current_user['uid']
    
    # 1. Busca o ID do Salão (garantia de segurança)
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        # Salva na subcoleção 'despesas'
        new_expense = expense.dict()
        new_expense['createdAt'] = datetime.utcnow()
        
        doc_ref = db.collection('cabeleireiros').document(salao_id).collection('despesas').document()
        doc_ref.set(new_expense)
        
        return {"message": "Despesa salva com sucesso", "id": doc_ref.id}
    except Exception as e:
        print(f"Erro ao salvar despesa: {e}")
        raise HTTPException(500, "Erro ao salvar despesa")

@router.delete("/despesas/{despesa_id}")
def delete_expense(
    despesa_id: str,
    current_user: dict = Depends(get_current_user)
):
    uid = current_user['uid']
    # Busca Salão
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        db.collection('cabeleireiros').document(salao_id).collection('despesas').document(despesa_id).delete()
        return {"message": "Despesa removida"}
    except Exception as e:
        raise HTTPException(500, f"Erro ao deletar: {str(e)}")

@router.patch("/despesas/{despesa_id}/toggle")
def toggle_expense_status(
    despesa_id: str,
    current_user: dict = Depends(get_current_user)
):
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    try:
        ref = db.collection('cabeleireiros').document(salao_id).collection('despesas').document(despesa_id)
        doc = ref.get()
        if not doc.exists: raise HTTPException(404, "Despesa não encontrada")
        
        current_status = doc.to_dict().get('status', 'pending')
        new_status = 'paid' if current_status == 'pending' else 'pending'
        
        ref.update({'status': new_status})
        return {"status": new_status}
    except Exception as e:
        raise HTTPException(500, f"Erro ao atualizar: {str(e)}")

@router.get("/resumo", response_model=FinancialSummary)
def get_financial_summary(
    period: str = Query("month", enum=["week", "month"]),
    current_user: dict = Depends(get_current_user)
):
    """
    Calcula Entradas (Agendamentos) vs Saídas (Despesas) e monta o gráfico.
    """
    uid = current_user['uid']
    query = db.collection('cabeleireiros').where(filter=FieldFilter('ownerUID', '==', uid)).limit(1)
    docs = list(query.stream())
    if not docs: raise HTTPException(404, "Salão não encontrado")
    salao_id = docs[0].id

    # 1. Definir Datas (Fuso SP)
    tz = pytz.timezone('America/Sao_Paulo')
    now = datetime.now(tz)
    
    if period == 'month':
        start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        # End date é hoje (para o gráfico ir até hoje) ou fim do mês
        end_date = now
    else: # week
        start_date = now - timedelta(days=6)
        start_date = start_date.replace(hour=0, minute=0, second=0, microsecond=0)
        end_date = now

    # Converter para UTC para buscar no Banco
    start_utc = start_date.astimezone(pytz.utc)
    end_utc = (end_date + timedelta(days=1)).astimezone(pytz.utc) # +1 dia para pegar o dia atual inteiro

    # 2. BUSCAR RECEITA (AGENDAMENTOS)
    # Somamos o preço de todos os serviços confirmados ou pendentes (não cancelados)
    appt_ref = db.collection('cabeleireiros').document(salao_id).collection('agendamentos')
    appt_query = appt_ref.where(filter=FieldFilter('startTime', '>=', start_utc))\
                         .where(filter=FieldFilter('startTime', '<=', end_utc))
    
    appts = list(appt_query.stream())
    
    total_revenue = 0.0
    revenue_by_day = {} # Para o gráfico

    for doc in appts:
        data = doc.to_dict()
        if data.get('status') == 'cancelado': continue
        
        price = float(data.get('servicePrice', 0))
        total_revenue += price
        
        # Agrupar por dia para o gráfico
        # Converte UTC -> Local para saber o dia certo (ex: 09/11)
        dt_local = data['startTime'].astimezone(tz)
        day_key = dt_local.strftime('%d/%m') # "09/11"
        
        if day_key not in revenue_by_day: revenue_by_day[day_key] = 0
        revenue_by_day[day_key] += price

    # 3. BUSCAR DESPESAS
    exp_ref = db.collection('cabeleireiros').document(salao_id).collection('despesas')
    # Filtragem simples por string de data YYYY-MM-DD (como salvamos no create)
    start_str = start_date.strftime('%Y-%m-%d')
    end_str = (end_date + timedelta(days=1)).strftime('%Y-%m-%d')
    
    exp_query = exp_ref.where(filter=FieldFilter('date', '>=', start_str))\
                       .where(filter=FieldFilter('date', '<=', end_str))
    
    exps = list(exp_query.stream())
    
    total_expenses = 0.0
    expenses_list = []
    expenses_by_day = {}

    for doc in exps:
        data = doc.to_dict()
        val = float(data.get('amount', 0))
        total_expenses += val
        
        # Formata para lista de retorno
        expenses_list.append({**data, "id": doc.id})
        
        # Agrupar por dia
        # data['date'] já é "2025-11-09"
        d_obj = datetime.strptime(data['date'], '%Y-%m-%d')
        day_key = d_obj.strftime('%d/%m')
        
        if day_key not in expenses_by_day: expenses_by_day[day_key] = 0
        expenses_by_day[day_key] += val

    # 4. MONTAR DADOS DO GRÁFICO
    # Gera todos os dias do intervalo para o gráfico não ter buracos
    chart_data = []
    current = start_date
    while current <= end_date:
        d_key = current.strftime('%d/%m')
        chart_data.append({
            "day": d_key,
            "entradas": revenue_by_day.get(d_key, 0),
            "saidas": expenses_by_day.get(d_key, 0)
        })
        current += timedelta(days=1)

    return {
        "total_revenue": total_revenue,
        "total_expenses": total_expenses,
        "net_profit": total_revenue - total_expenses,
        "expenses_list": sorted(expenses_list, key=lambda x: x['date'], reverse=True),
        "chart_data": chart_data
    }